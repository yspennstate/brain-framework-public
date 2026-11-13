# -*- coding: utf-8 -*-
"""THE USAGE WATCH: tell a session when model usage is burning faster than it can last.

What it watches, from local files only (no network, no tokens spent):
  1. each account's weekly meter, from a probes file (USAGE_WATCH_PROBES) of the form
     {"probes": {"<account>": {"u7": <fraction of the week used>, "at": <epoch of the reading>, "r7": <epoch of the reset>}}}:
     points burned in the last hour against the even pace that lasts to the account's reset (CAP = 97 percent used);
  2. the rounds of a headless worker fleet that ended in the last hour, from a JSONL ledger (USAGE_WATCH_LEDGER) with rows
     {"worker": ..., "ended": <epoch>, "num_turns": n, "usage": {<the API usage block>}}: tokens read against tokens
     written, and the worker re-reading the most a turn;
  3. the session itself (the tail of its transcript): tokens re-read a turn over its last ten turns, and the share of
     its recent text that hooks attached.
Either file may be absent; that watch is then silent. primary_hook.py calls lines() on every prompt and prints what
comes back at most once per session per 30 minutes. It comes back empty unless something is off, so a healthy session
never sees it.

Why: a long session re-reads its whole history on every turn, and hook text can become half of what it reads. Without a
meter in front of the agent, a day of usage goes before anyone looks.

    python usage_watch.py --report [transcript.jsonl]   the measurement now, whether or not anything is off
    python usage_watch.py --selftest                     controls both ways on synthetic files; live state untouched
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)
import io
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROBES = os.environ.get("USAGE_WATCH_PROBES", "")    # the account meters (format in the docstring); unset = no meter watch
LEDGER = os.environ.get("USAGE_WATCH_LEDGER", "")    # the worker fleet's round ledger; unset = no fleet watch
HIST = os.path.join(HERE, "usage_watch_history.json")
CAP = 0.97                # an account may be used to 97 percent, at an even pace through each window
BURN_X = 1.5              # flag an account burning faster than 1.5x its even pace ...
BURN_MIN = 0.5            # ... and faster than half a point of its week an hour
FLEET_RATIO = 150         # tokens read per token written by the rounds that ended in the last hour
FLEET_MIN_READ = 20e6
WORKER_TURN_MAX = 250e3   # a worker re-reading more than this a turn carries too much context
SESSION_TURN_MAX = 250e3  # a session past this re-reads its whole history on every turn (the workers averaged 178k)
TAIL = 2 * 1024 * 1024
TAG = "[usage-watch]"


def _json(path, default):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _tail_lines(path, size=TAIL):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            end = f.tell()
            f.seek(max(0, end - size))
            data = f.read()
    except Exception:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[1:] if end > size else lines


def _read(u):
    return (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)


def accounts(now, probes_path=PROBES, hist_path=HIST, save=True):
    """Per account: fraction of the week used, points burned an hour (oldest reading 40-120 min back), even pace."""
    probes = (_json(probes_path, {}) or {}).get("probes") or {}
    hist = _json(hist_path, {}) or {}
    out = {}
    for acct, p in sorted(probes.items()):
        if not isinstance(p, dict) or p.get("u7") is None or not p.get("at"):
            continue
        at, u7, r7 = float(p["at"]), float(p["u7"]), float(p.get("r7") or 0)
        h = [x for x in hist.get(acct, []) if now - x[0] < 6 * 3600]
        if not h or h[-1][0] != at:
            h.append([at, u7])
        hist[acct] = h
        rec = {"used": u7, "age_min": (now - at) / 60.0, "days_left": (r7 - at) / 86400.0 if r7 > at else None}
        past = [x for x in h if 40 * 60 <= at - x[0] <= 2 * 3600]
        if past and past[0][1] <= u7:
            rec["burn"] = (u7 - past[0][1]) * 100.0 / ((at - past[0][0]) / 3600.0)
        if r7 > at:
            rec["even"] = max(0.0, (CAP - u7) * 100.0 / ((r7 - at) / 3600.0))
        out[acct] = rec
    if save:
        tmp = hist_path + ".%d.tmp" % os.getpid()
        try:
            with io.open(tmp, "w", encoding="utf-8") as f:
                json.dump(hist, f)
            os.replace(tmp, hist_path)
        except Exception:
            try:
                os.remove(tmp)
            except Exception:
                pass
    return out


def fleet(now, ledger_path=LEDGER, window=3600):
    """The worker rounds that ended in the last hour: tokens read, written, the worst reader a turn."""
    read = wrote = 0
    per = {}
    for line in (_tail_lines(ledger_path, 1024 * 1024) if ledger_path else []):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if (r.get("ended") or 0) < now - window:
            continue
        u = r.get("usage") or {}
        read += _read(u)
        wrote += u.get("output_tokens") or 0
        w = per.setdefault(r.get("worker") or "?", [0, 0])
        w[0] += _read(u)
        w[1] += r.get("num_turns") or 0
    worst = max(((k, v[0] / v[1]) for k, v in per.items() if v[1] >= 20), key=lambda kv: kv[1], default=None)
    return {"read": read, "wrote": wrote, "workers": len(per), "worst": worst}


def session(transcript_path):
    """The session's last ten turns: tokens re-read a turn, and the hook share of its recent text."""
    if not transcript_path or not os.path.exists(transcript_path):
        return None
    seen, reads, hook, total = set(), [], 0, 0
    for line in _tail_lines(transcript_path):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        t = r.get("type")
        if t == "attachment":
            a = r.get("attachment") or {}
            b = a.get("content") or a.get("stdout") or a.get("additionalContext") or ""
            n = len(b) if isinstance(b, str) else len(json.dumps(b))
            hook += n
            total += n
        elif t in ("user", "assistant"):
            m = r.get("message") or {}
            c = m.get("content")
            total += len(c) if isinstance(c, str) else len(json.dumps(c or ""))
            u = m.get("usage") if t == "assistant" else None
            if u and m.get("id") not in seen and _read(u) > 0:
                seen.add(m.get("id"))
                reads.append(_read(u))
    reads = reads[-10:]
    if len(reads) < 5:
        return None
    return {"per_turn": sum(reads) / float(len(reads)), "hook_share": hook / float(max(1, total))}


def lines(payload=None, now=None, probes_path=PROBES, hist_path=HIST, ledger_path=LEDGER, save=True):
    """At most three lines, and only when something is off. Never raises."""
    now = now or time.time()
    out = []
    try:
        bad = []
        for a, r in accounts(now, probes_path, hist_path, save).items():
            if r["age_min"] > 45 or "burn" not in r or "even" not in r:
                continue
            if r["burn"] > max(BURN_MIN, BURN_X * r["even"]):
                bad.append("%s burned %.1f points of its week in the last hour; %.1f an hour lasts to its reset "
                           "(%d%% used, %.1f days left)" % (a, r["burn"], r["even"], round(100 * r["used"]), r["days_left"] or 0))
        if bad:
            out.append("%s Usage week: %s." % (TAG, "; ".join(bad[:2])))
    except Exception:
        pass
    try:
        f = fleet(now, ledger_path)
        msg = []
        if f["read"] >= FLEET_MIN_READ and f["read"] > FLEET_RATIO * max(1, f["wrote"]):
            msg.append("rounds that ended in the last hour read %.0fM tokens for %.1fM written (%d to 1)"
                       % (f["read"] / 1e6, f["wrote"] / 1e6, f["read"] // max(1, f["wrote"])))
        if f["worst"] and f["worst"][1] > WORKER_TURN_MAX:
            msg.append("%s re-read %.0fk tokens a turn" % (f["worst"][0], f["worst"][1] / 1e3))
        if msg:
            out.append("%s Worker rounds: %s. A round should end in results that land and are kept, not in "
                       "re-reading." % (TAG, "; ".join(msg)))
    except Exception:
        pass
    try:
        s = session(payload.get("transcript_path") if isinstance(payload, dict) else None)
        if s and s["per_turn"] > SESSION_TURN_MAX:
            out.append("%s This session re-reads about %.0fk tokens a turn (%.0f%% of its recent text is hook text). "
                       "/compact, or save a NEXT line and start a fresh session." % (TAG, s["per_turn"] / 1e3, 100 * s["hook_share"]))
    except Exception:
        pass
    return out[:3]


def report(transcript_path=None):
    now = time.time()
    for a, r in accounts(now, save=False).items():
        print("%-8s used %3.0f%%  burn %s pts/h  even %s pts/h  reading %.0f min old  %s days left" % (
            a, 100 * r["used"], "%.2f" % r["burn"] if "burn" in r else "-", "%.2f" % r["even"] if "even" in r else "-",
            r["age_min"], "%.1f" % r["days_left"] if r["days_left"] is not None else "-"))
    f = fleet(now)
    print("worker rounds ended in the last hour: %d workers, read %.1fM, wrote %.2fM, worst %s" % (
        f["workers"], f["read"] / 1e6, f["wrote"] / 1e6,
        "%s %.0fk/turn" % (f["worst"][0], f["worst"][1] / 1e3) if f["worst"] else "-"))
    if transcript_path:
        s = session(transcript_path)
        print("session: %s" % ("%.0fk a turn, hook share %.0f%%" % (s["per_turn"] / 1e3, 100 * s["hook_share"]) if s else "too few turns"))
    print("would fire now: %s" % (lines({"transcript_path": transcript_path}, now, save=False) or "nothing"))


def selftest():
    tmp = tempfile.mkdtemp(prefix="uw_")
    ok = True
    try:
        now = 2000000000.0
        P, H, L, T = (os.path.join(tmp, n) for n in ("p.json", "h.json", "l.jsonl", "t.jsonl"))

        def probe(u7_now, u7_then, at_age=60):
            with open(P, "w") as f:
                json.dump({"probes": {"meter_c": {"u7": u7_now, "at": now - at_age, "r7": now + 5.8 * 86400}}}, f)
            with open(H, "w") as f:
                json.dump({"meter_c": [[now - at_age - 3600, u7_then]]}, f)

        def ledger(read, wrote, turns):
            with open(L, "w") as f:
                f.write(json.dumps({"worker": "w13", "ended": now - 600, "num_turns": turns,
                                    "usage": {"cache_read_input_tokens": read, "output_tokens": wrote}}) + "\n")

        def transcript(per_turn, n=12):
            with open(T, "w") as f:
                for i in range(n):
                    f.write(json.dumps({"type": "attachment", "attachment": {"content": "x" * 3000}}) + "\n")
                    f.write(json.dumps({"type": "assistant", "message": {
                        "id": "m%d" % i, "content": [{"type": "text", "text": "y" * 500}],
                        "usage": {"cache_read_input_tokens": per_turn, "output_tokens": 300}}}) + "\n")

        def run():
            return lines({"transcript_path": T}, now, P, H, L, save=False)

        cases = []
        probe(0.85, 0.83); ledger(1e6, 1e5, 30); transcript(80e3)
        cases.append(("an account burning 2 points an hour against an even 0.09 fires", any("meter_c burned" in x for x in run())))
        probe(0.85, 0.849)
        cases.append(("an account at an even pace stays silent", run() == []))
        probe(0.05, 0.95)
        cases.append(("a meter that reset inside the window stays silent", run() == []))
        probe(0.85, 0.83, at_age=3 * 3600)
        cases.append(("a stale reading stays silent", run() == []))
        probe(0.85, 0.849); ledger(300e6, 1e6, 800)
        r = run()
        cases.append(("a fleet hour at 300 to 1 with 375k a turn fires both", any("300 to 1" in x and "375k" in x for x in r)))
        ledger(10e6, 5e5, 100)
        cases.append(("a small, lean fleet hour stays silent", run() == []))
        transcript(560e3)
        r = run()
        cases.append(("a session at 560k a turn fires with its hook share", any("560k" in x and "hook text" in x for x in r)))
        transcript(80e3)
        cases.append(("a session at 80k a turn stays silent", run() == []))
        transcript(560e3, n=3)
        cases.append(("three turns are too few to judge", run() == []))
        cases.append(("no transcript path stays silent", lines({}, now, P, H, L, save=False) == []))
        transcript(560e3); ledger(300e6, 1e6, 800); probe(0.85, 0.83)
        r = run()
        cases.append(("all three at once: three ASCII lines",
                      len(r) == 3 and all(all(ord(c) < 128 for c in x) for x in r)))
        for name, good in cases:
            ok = ok and good
            print("%s  %s" % ("PASS" if good else "FAIL", name))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("PASS - usage watch controls" if ok else "FAIL - usage watch controls")
    return 0 if ok else 1


if __name__ == "__main__":
    if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
        print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
        raise SystemExit(0)
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--report" in sys.argv:
        i = sys.argv.index("--report")
        report(sys.argv[i + 1] if len(sys.argv) > i + 1 else None)
        sys.exit(0)
    print("\n".join(lines({}) or ["nothing off"]))
