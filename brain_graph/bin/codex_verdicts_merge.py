"""One record of his Codex corrections, repeats and praise from the two derivations (28 Sep 2026, claude-825b1305; the
scheduled audit jobs R25 and R30).

Two files derive his verdicts in Codex sessions:
  - the ledger's (codex_verdicts.py, events_verdicts_codex.jsonl): prompts of the prompt ledger with origin "owner",
    today's patterns, keyed to the session's full id only where the prompt's own text proves it;
  - the capture's (capture/extract_codex_events.py, the "verdict" rows of events_codex.jsonl): UserMessage items of the
    sessions he drives (codex-tui or the desktop app), with the patterns as they were when each rollout's bytes were
    first read, and never recomputed.
This settles every row on which they disagree by reading the prompt itself. A capture row is re-read from its rollout
(the UserMessage at that second) and is
  - automation: the ledger holds the text and no owner origin for it -> not his, dropped;
  - stale pattern: today's patterns (codex_verdicts.verdicts_of) do not give that verdict -> dropped;
  - ledger gap: his text by the capture's own test, today's pattern gives the verdict, the ledger lacks the prompt ->
    kept and labelled;
  - keyed apart: the ledger has the prompt as his and gives the verdict, but under no key or another second -> kept once;
and a ledger row the capture lacks is explained (no rollout, a session the capture does not count as driven, a prompt
over 4,000 characters, the capture's text test refusing it, a pattern the capture's older rows lacked). The merged
record is the rows both hold, the ledger rows and the capture rows kept above, each with its basis. No prompt text is
written: a row carries a 12-character hash of its prompt.

    python codex_verdicts_merge.py OUT_MERGED.jsonl OUT_REPORT.json
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import collections
import hashlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import codex_verdicts as CV  # noqa: E402
import extract_touch_events as X  # noqa: E402

BG = os.path.join(CV.HOME, ".claude", "brain_graph")
EVENTS = os.path.join(BG, "events_codex.jsonl")
STATE = os.path.join(BG, "codex_state.json")
TOL = 5.0


def sha12(text):
    return hashlib.sha1(" ".join((text or "").split()).encode("utf-8")).hexdigest()[:12]


def ledger_verdicts():
    """codex_verdicts' rows, rebuilt the same way, with each prompt's text kept in memory only"""
    res, out = CV.Resolver(), []
    for r in CV.his_codex_rows():
        vs = CV.verdicts_of(r.get("text"))
        if not vs:
            continue
        full, how = res.full((r.get("session") or "")[:12], r.get("text"))
        for v in vs:
            out.append({"ts": CV.epoch_local(r["ts"]), "session": CV.capture_key(full), "id": v,
                        "text": r.get("text") or "", "resolved": how})
    return out


def ledger_origins():
    """text key -> the origins the ledger gives Codex prompts with that text"""
    idx = collections.defaultdict(set)
    with io.open(CV.LEDGER, encoding="utf-8") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            if "codex" in (o.get("source") or "").lower():
                idx[CV._text_key(o.get("text"))].add(o.get("origin") or "")
    return idx


def capture_verdicts():
    """the capture's verdict rows and the first and last time of any capture event"""
    rows, lo, hi = [], None, None
    with io.open(EVENTS, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            ts = o.get("ts")
            if isinstance(ts, (int, float)):
                lo = ts if lo is None else min(lo, ts)
                hi = ts if hi is None else max(hi, ts)
            if o.get("kind") == "verdict":
                rows.append({"ts": ts, "session": o.get("session"), "id": o.get("id")})
    return rows, lo, hi


def rollouts_by_session():
    """capture key -> [(rollout path, driven)] from the capture's own state"""
    out = collections.defaultdict(list)
    try:
        st = json.load(io.open(STATE, encoding="utf-8"))
    except Exception:
        return out
    for path, s in st.items():
        if isinstance(s, dict) and s.get("session"):
            out[s["session"]].append((path, bool(s.get("driven"))))
    return out


_UM = {}


def user_messages(path):
    """(epoch, raw text) of every UserMessage item in a rollout, read once"""
    if path not in _UM:
        msgs = []
        try:
            with open(path, "rb") as fh:
                for raw in fh:
                    if b"UserMessage" not in raw:
                        continue
                    try:
                        o = json.loads(raw)
                    except Exception:
                        continue
                    p = o.get("payload") or {}
                    if o.get("type") != "event_msg" or p.get("type") != "item_completed":
                        continue
                    it = p.get("item") or {}
                    if it.get("type") == "UserMessage":
                        txt = " ".join(str(c.get("text") or "") for c in (it.get("content") or []) if isinstance(c, dict))
                        msgs.append((X.epoch(o.get("timestamp") or ""), txt))
        except OSError:
            pass
        _UM[path] = msgs
    return _UM[path]


def near(session, ts, rb, tol):
    """the UserMessage texts of a session's rollouts within tol seconds of ts, and whether any rollout was found"""
    hits, found = [], False
    for path, _driven in rb.get(session, []):
        if not os.path.exists(path):
            continue
        found = True
        hits += [t for e, t in user_messages(path) if e and abs(e - ts) <= tol]
    return hits, found


def main():
    out_merged, out_report = sys.argv[1], sys.argv[2]
    led = ledger_verdicts()
    origins = ledger_origins()
    cap, lo, hi = capture_verdicts()
    rb = rollouts_by_session()
    led_w = [r for r in led if lo is not None and lo <= r["ts"] <= hi]
    matched_l, matched_c = set(), set()
    for i, c in enumerate(cap):
        for j, r in enumerate(led_w):
            if j in matched_l or r["session"] != c["session"] or r["id"] != c["id"]:
                continue
            if abs(r["ts"] - c["ts"]) <= TOL:
                matched_l.add(j)
                matched_c.add(i)
                break
    rows, classes, detail = [], collections.Counter(), []
    for j in sorted(matched_l):
        r = led_w[j]
        rows.append({"ts": round(r["ts"], 3), "session": r["session"], "id": r["id"], "basis": "both",
                     "prompt_sha12": sha12(r["text"])})
    for i, c in enumerate(cap):
        if i in matched_c:
            continue
        texts, found = near(c["session"], c["ts"], rb, 1.0)
        mine = [t for t in texts if X.his_text({}, t.strip())]
        if not found:
            cls = "capture: rollout gone"
        elif not mine:
            cls = "capture: no prompt of his at that second"
        else:
            t = mine[0]
            org = origins.get(CV._text_key(t), set())
            if org and "owner" not in org:
                cls = "capture: automation (ledger origin %s)" % "/".join(sorted(org))
            elif c["id"] not in CV.verdicts_of(t):
                cls = "capture: stale pattern (today %s)" % (",".join(CV.verdicts_of(t)) or "none")
            elif not org:
                cls = "capture kept: ledger gap"
            else:
                cls = "capture kept: keyed apart"
            if cls.startswith("capture kept"):
                rows.append({"ts": round(c["ts"], 3), "session": c["session"], "id": c["id"],
                             "basis": cls.split(": ", 1)[1], "prompt_sha12": sha12(t)})
        classes[cls] += 1
        detail.append({"side": "capture", "ts": round(c["ts"], 3), "session": c["session"], "id": c["id"], "class": cls})
    for j, r in enumerate(led_w):
        if j in matched_l:
            continue
        kept = True
        if not r["session"]:
            cls = "ledger: unkeyed (%s)" % r["resolved"]
        else:
            rolls = rb.get(r["session"], [])
            if not rolls:
                cls = "ledger: the capture has no rollout of the session"
            elif not any(d for _p, d in rolls):
                cls = "ledger: the capture does not count the session as driven"
            else:
                texts, _found = near(r["session"], r["ts"], rb, TOL)
                if not texts:
                    cls = "ledger: no UserMessage within 5 s"
                elif len(r["text"].strip()) >= 4000:
                    cls = "ledger: over 4,000 characters"
                elif not any(X.his_text({}, t.strip()) for t in texts):
                    cls = "ledger: the capture's text test refuses it"
                else:
                    cls = "ledger: the capture's older rows lack the verdict"
        rows.append({"ts": round(r["ts"], 3), "session": r["session"], "id": r["id"], "basis": "ledger",
                     "prompt_sha12": sha12(r["text"])})
        classes[cls] += 1
        detail.append({"side": "ledger", "ts": round(r["ts"], 3), "session": r["session"], "id": r["id"], "class": cls,
                       "kept": kept})
    rows.sort(key=lambda d: d["ts"])
    with io.open(out_merged, "w", encoding="utf-8", newline="\n") as fh:
        for d in rows:
            fh.write(json.dumps(dict(d, kind="verdict", runtime="codex")) + "\n")
    rep = {"window": [lo, hi], "ledger_rows_all": len(led), "ledger_rows_in_window": len(led_w),
           "capture_rows": len(cap), "both": len(matched_l), "classes": dict(classes),
           "merged_rows": len(rows), "merged_by_basis": dict(collections.Counter(d["basis"] for d in rows)),
           "merged_by_verdict": dict(collections.Counter(d["id"] for d in rows)), "detail": detail}
    json.dump(rep, io.open(out_report, "w", encoding="utf-8"), indent=1)
    print(json.dumps({k: v for k, v in rep.items() if k != "detail"}, indent=1))


if __name__ == "__main__":
    main()
