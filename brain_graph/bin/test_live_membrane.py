"""test_live_membrane.py - controls for the live membrane before it reaches a hook, and after.

  negative   no sidecar -> nothing; STOP -> nothing; a malformed or empty payload -> nothing and no exception; a session
             whose words the graph has never seen -> nothing served
  positive   replay of real sessions: at each moment an agent opened a memory file, the transcript is cut just before
             it and the membrane evaluated at that second; the memory must rank in the membrane's list at some rate,
             and lines must fire at some moments (a hook that never fires cannot be studied)
  timing     milliseconds per evaluation on real transcripts (the hook's budget is well under a second)
In-sample: the store has seen these sessions, so the positive rate here is a working check, not the lane's effect; the
effect is measured walk-forward (graph_learn.py on live fires, and study_live_membrane.py on a store cut at a date).

    python test_live_membrane.py [--sessions 12] [--moments 40]
"""
import argparse
import glob
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import live_membrane as LM  # noqa: E402

PROJ = os.path.join(os.path.expanduser("~"), ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")))
MEMRX = re.compile(r"memory[\\/]+([a-z0-9_]{8,})\.md", re.I)
RESULTS = []


def check(ok, what):
    RESULTS.append((bool(ok), what))
    print(("PASS  " if ok else "FAIL  ") + what)


def epoch(ts):
    import datetime
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def memory_reads(path, limit=400):
    """(line number, epoch, slug) of each Read of a memory file in a transcript."""
    out = []
    with open(path, "rb") as fh:
        for n, raw in enumerate(fh):
            if b'"tool_use"' not in raw or b"memory" not in raw:
                continue
            try:
                o = json.loads(raw)
            except Exception:
                continue
            for c in (o.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Read":
                    m = MEMRX.search(str((c.get("input") or {}).get("file_path") or ""))
                    t = epoch(o.get("timestamp") or "")
                    if m and t:
                        out.append((n, t, m.group(1).lower()))
            if len(out) >= limit:
                break
    return out


def cut(path, upto_line, dst):
    with open(path, "rb") as fh, open(dst, "wb") as out:
        for n, raw in enumerate(fh):
            if n >= upto_line:
                break
            out.write(raw)


def negatives(tmp):
    # a malformed payload, an empty one, one with no transcript
    for p in ({}, {"session_id": "x"}, {"session_id": "x", "transcript_path": os.path.join(tmp, "missing.jsonl")}):
        try:
            r = LM.run(p)
            check(r == "", "payload %s -> nothing" % (list(p) or "empty"))
        except Exception as e:
            check(False, "payload raised %s" % e)
    # a transcript of words the graph has never seen
    fake = os.path.join(tmp, "fake.jsonl")
    now = time.time()
    import datetime
    ts = datetime.datetime.fromtimestamp(now - 30, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    rec = {"type": "assistant", "sessionId": "neg-control-session", "timestamp": ts, "cwd": os.path.expanduser("~"),
           "message": {"content": [{"type": "text", "text": "zzqxv plorbnik wuxtrel quandomar flibbertigrix"}]}}
    with open(fake, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    LM_STATE = LM._state_path("neg-control-session")
    if os.path.exists(LM_STATE):
        os.remove(LM_STATE)
    r = LM.run({"session_id": "neg-control-session", "transcript_path": fake, "hook_event_name": "UserPromptSubmit",
                "prompt": "zzqxv plorbnik"}, now=now, force=True)
    check(r == "", "a context the graph has never seen serves nothing")
    if os.path.exists(LM_STATE):
        os.remove(LM_STATE)
    # STOP silences it
    os.makedirs(os.path.dirname(LM.STOP), exist_ok=True)   # a fresh install has no live directory yet
    open(LM.STOP, "w").close()
    try:
        r = LM.run({"session_id": "neg-control-session", "transcript_path": fake, "hook_event_name": "UserPromptSubmit"},
                   now=now, force=True)
        check(r == "", "STOP file -> nothing")
    finally:
        os.remove(LM.STOP)


def positives(n_sessions, n_moments, seed=7):
    files = [p for p in glob.glob(os.path.join(PROJ, "*.jsonl")) if os.path.getsize(p) > 2_000_000]
    files.sort(key=os.path.getmtime, reverse=True)
    rnd = random.Random(seed)
    moments = []
    for p in files[:200]:
        reads = memory_reads(p)
        if reads:
            moments += [(p,) + r for r in rnd.sample(reads, min(4, len(reads)))]
        if len({m[0] for m in moments}) >= n_sessions:
            break
    rnd.shuffle(moments)
    moments = moments[:n_moments]
    tmp = tempfile.mkdtemp(prefix="lm_test_")
    lv = LM.Live()
    pol = LM.policy()
    import extract_touch_events as X
    ranks, fired, served_right, ms = [], 0, 0, []
    try:
        for i, (p, line, t, slug) in enumerate(moments):
            dst = os.path.join(tmp, "cut_%d.jsonl" % i)
            cut(p, line, dst)
            t_eval = t - 1.0
            t0 = time.time()
            items = X.seed_from_transcript(dst, now=t_eval, window=pol["window_s"], k=pol["k_items"],
                                           tail_bytes=pol["tail_bytes"])
            seen = {nm.split(":", 1)[1] for nm, _a in items if nm.split(":", 1)[0] in LM.MEM_KINDS}
            ev = lv.evaluate(items, t_eval, seen_slugs=seen - {slug}, pol=pol)
            ms.append((time.time() - t0) * 1000)
            order = [c["slug"] for c in ev.get("mem", [])]
            ranks.append(order.index(slug) + 1 if slug in order else None)
            st = {"served": {}, "fire_times": [], "seen": sorted(seen - {slug})}
            ev["_names"] = {}
            if ev.get("err"):
                ev["_fix"] = lv.fix_memory(ev["err"][0][0], seen)
            lines, served = LM.decide(ev, t_eval, st, pol)
            if lines:
                fired += 1
                if any(s[0] == "mem" and s[1] == slug for s in served):
                    served_right += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    n = len(ranks)
    h1 = sum(1 for r in ranks if r == 1)
    h10 = sum(1 for r in ranks if r is not None)
    print("replay: %d memory reads in %d sessions; the read memory ranked 1st %d, in the top 10 %d; lines fired at %d "
          "moments, the read memory itself served at %d; median %.0f ms, max %.0f ms per evaluation" % (
              n, len({m[0] for m in moments}), h1, h10, fired, served_right, sorted(ms)[len(ms) // 2] if ms else 0,
              max(ms) if ms else 0))
    check(n >= 10, "enough replay moments (%d)" % n)
    check(h10 > 0, "the membrane ranks the memory an agent read next in its top 10 at some moments (%d of %d)" % (h10, n))
    check(fired > 0, "lines fire at some moments (%d of %d)" % (fired, n))
    check(fired < n, "lines do not fire at every moment (%d of %d)" % (fired, n))
    check((sorted(ms)[len(ms) // 2] if ms else 1e9) < 700, "median evaluation under 700 ms")
    return {"moments": n, "hit1": h1, "hit10": h10, "fired": fired, "served_right": served_right,
            "median_ms": sorted(ms)[len(ms) // 2] if ms else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=12)
    ap.add_argument("--moments", type=int, default=40)
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="lm_neg_")
    try:
        negatives(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if os.path.exists(LM.SIDECAR):
        res = positives(a.sessions, a.moments)
    else:                                   # a fresh install: no graph built, no sessions to replay yet
        print("SKIP positives - no graph sidecar yet (a fresh install builds it on the first graph build)")
        res = {}
    bad = [w for ok, w in RESULTS if not ok]
    print("%d checks, %d failed" % (len(RESULTS), len(bad)))
    print(json.dumps(res))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
