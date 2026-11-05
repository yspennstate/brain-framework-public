"""Controls for the membrane's six-hour memory quota and who spends it (27 Sep, claude-825b1305; audit account03/B15, R9).

A memory reaches at most two other sessions in six hours. Before this change a bench test worker counted as one of the
two, so two workers that drew a memory kept it from his next session. These controls check that test workers no longer
spend the quota, that his sessions, scripts and unknown receivers still do, and that the receiver's kind is read from
its own transcript.

    python test_live_membrane_quota.py [path/to/live_membrane.py]     (default: the copy beside this file)
Exit 0 when every control passes.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import importlib.util
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "live_membrane.py")
spec = importlib.util.spec_from_file_location("live_membrane_under_test", PATH)
m = importlib.util.module_from_spec(spec)
sys.path.insert(0, os.path.dirname(os.path.abspath(PATH)))
spec.loader.exec_module(m)

TMP = tempfile.mkdtemp(prefix="membrane_quota_")
m.SERVED = os.path.join(TMP, "served.jsonl")
m.FIRES = os.path.join(TMP, "fires.jsonl")
m.POLICY = os.path.join(TMP, "no_policy.json")
m.PRECISION = os.path.join(TMP, "no_precision.json")
if hasattr(m, "KINDS_CACHE"):
    m.KINDS_CACHE = os.path.join(TMP, "session_kinds.json")

NOW = time.time()
M = "some_memory_2026_09_27"
HIS = "aaaaaaaa-0000-0000-0000-000000000001"          # the session asking; the quota counts OTHER sessions
SID = {n: "%08x-1111-2222-3333-444444444444" % n for n in range(1, 9)}


def reset(rows=(), fires=(), cache=None):
    for p, rs in ((m.SERVED, rows), (m.FIRES, fires)):
        with open(p, "w", encoding="utf-8") as fh:
            for r in rs:
                fh.write(json.dumps(r) + "\n")
    if hasattr(m, "KINDS_CACHE"):
        json.dump(cache or {}, open(m.KINDS_CACHE, "w", encoding="utf-8"))
    if hasattr(m, "_KINDS"):
        m._KINDS.clear()


def row(n, kind=None, age_h=1.0, slug=M, ch="mem"):
    r = {"t": NOW - age_h * 3600, "sid": SID[n], "ep": "", "served": [[ch, slug, []]]}
    if kind is not None:
        r["kind"] = kind
    return r


def blocked(slug=M):
    others = m.served_elsewhere(NOW)
    return len(others.get(slug, set()) - {HIS[:8]}) >= 2


RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print("%s  %s" % ("PASS" if cond else "FAIL", name))


def run(name, fn):
    try:
        check(name, fn())
    except Exception as e:                    # a control that cannot run on this version fails, it does not crash the rest
        check("%s  (%s: %s)" % (name, type(e).__name__, str(e)[:80]), False)


# 1. two bench workers drew M: his session may still get it
reset([row(1, "test"), row(2, "test")])
run("1 two test-worker receivers do not block his session", lambda: not blocked())

# 2. two of his other sessions drew M: a third is held back (the rule itself stands)
reset([row(1, "his"), row(2, "his")])
run("2 two real receivers still block a third", lambda: blocked())

# 3. rows written before the change carry no kind: the settled cache decides, and an unknown session still counts
reset([row(1), row(2)], cache={SID[1]: "test", SID[2]: "test"})
run("3a old rows whose sessions the cache knows as test do not block", lambda: not blocked())
reset([row(3), row(4)], cache={SID[4]: "his"})
run("3b an unknown and a his session in old rows do block", lambda: blocked())

# 4. scripts and unknown receivers count
reset([row(1, "script"), row(2, "unknown")])
run("4 a script and an unknown receiver block", lambda: blocked())

# 5. the fire log's tail is read by the same rule
reset(fires=[row(1, "test"), row(2, "test")])
run("5a test rows in the fire log do not block", lambda: not blocked())
reset(fires=[row(1, "his"), row(2, "script")])
run("5b real rows in the fire log do block", lambda: blocked())

# 6. rows older than six hours are ignored, as before
reset([row(1, "his", age_h=7), row(2, "his", age_h=7)])
run("6 receivers older than six hours are ignored", lambda: not blocked())

# 7. a test worker's own row still counts nowhere else: mixed rows, only the real ones count
reset([row(1, "test"), row(2, "his"), row(3, "test")])
run("7 one real receiver among test workers does not block", lambda: not blocked())


# 8. the receiver's kind comes from the head of its own transcript and is kept in its state
def transcript(entry, first):
    p = os.path.join(TMP, "t_%s_%d.jsonl" % (entry, len(first)))
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "entrypoint": entry, "message": {"role": "user", "content": first}}) + "\n")
    return p


def kinds_ok():
    st = {}
    a = m.receiver_kind(st, transcript("cli", "ok now keep working")) == "his" and st["kind"] == "his"
    st2 = {}
    b = m.receiver_kind(st2, transcript("sdk-cli", "[headless-worker] grade this")) == "test"
    st3 = {}
    c = m.receiver_kind(st3, os.path.join(TMP, "missing.jsonl")) == "unknown"
    d = m.receiver_kind(st3, transcript("sdk-cli", "a dream pass")) == "script"      # unknown is read again
    e = m.receiver_kind({"kind": "his"}, transcript("sdk-cli", "[headless-worker] x")) == "his"   # settled is kept
    return a and b and c and d and e


run("8 receiver kind read from its transcript, unknown re-read, settled kept", kinds_ok)


# 9. through decide(): with two bench workers holding M, his session is served M
def decide_serves():
    reset([row(1, "test"), row(2, "test")])
    pol = m.policy()
    # hold_mem off: the diffuse-line trial holds a line by a hash of the session, a separate rule from the quota
    pol.update({"hold_mem": False, "serve_attr": False, "serve_err": False, "serve_place": False, "serve_word": False, "serve_mem": True,
                "tired": []})
    ev = {"mem_all": [{"slug": M, "size": pol["mem_size_min"] + 1.0, "hebb": 0, "reach": 0,
                       "why": ["file:~/work/some_script.py"], "descr": "a memory"}]}
    state = {"sid": HIS, "seen": [], "served": {}, "fire_times": []}
    lines, served = m.decide(ev, NOW, state, pol)
    ok = any(s[0] == "mem" and s[1] == M for s in served)
    # and the same with two real receivers is held back, and the quota's cost is logged
    reset([row(1, "his"), row(2, "his")])
    ev2 = {"mem_all": [dict(ev["mem_all"][0])]}
    lines2, served2 = m.decide(ev2, NOW, {"sid": HIS, "seen": [], "served": {}, "fire_times": []}, pol)
    held = not any(s[0] == "mem" for s in served2) and ev2.get("qb") == [[M, 2]]
    return ok and held


run("9 decide(): served past test workers, held back (and logged) past real ones", decide_serves)

bad = [n for n, ok in RESULTS if not ok]
print("\n%d of %d controls pass (%s)" % (len(RESULTS) - len(bad), len(RESULTS), PATH))
sys.exit(1 if bad else 0)
