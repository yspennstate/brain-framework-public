"""The diffuse-line trial's persistent first-exposure cohort (dot D7CB6565's source review, 6 Oct 2026, P1-3).
Run: python test_trial_cohort.py"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("graph_learn_cohort_test", os.path.join(HERE, "graph_learn.py"))
gl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gl)
gl.LIVE = tempfile.mkdtemp()
T0 = gl.TRIAL_START
fails = 0


def check(cond, what):
    global fails
    print(("PASS  " if cond else "FAIL  ") + what)
    fails += 0 if cond else 1


# run 1: the first place exposure is held with an unknown outcome; a later one in the shown arm has an outcome
run1 = [{"t": T0 + 10, "sid": "s1", "lines": [["held_place", "place:dir:~/x", None, []]]},
        {"t": T0 + 99, "sid": "s1", "lines": [["place", "place:dir:~/x", True, []]]},
        {"t": T0 - 50, "sid": "s2", "lines": [["mem", "slug_a", True, []]]}]
c = gl._trial_cohort(run1)
u = "s1\tplace\tdir:~/x"
check(c[u]["kind"] == "held_place" and c[u]["ok"] is None, "the first exposure stays, unknown, in its own arm")
check(not any(v["sid"] == "s2" for v in c.values()), "an exposure before TRIAL_START is not in the cohort")
# run 2: the first exposure matured to False; the judged window no longer holds the later shown one
run2 = [{"t": T0 + 10, "sid": "s1", "lines": [["held_place", "place:dir:~/x", False, []]]}]
c = gl._trial_cohort(run2)
check(c[u]["ok"] is False and c[u]["kind"] == "held_place", "the same exposure's outcome is filled in when it matures")
# run 3: the judged history has dropped s1 entirely (older than its window); a new session arrives
run3 = [{"t": T0 + 500, "sid": "s3", "lines": [["mem", "slug_b", True, []], ["held_mem", "mem:slug_c", None, []]]}]
c = gl._trial_cohort(run3)
check(u in c and c[u]["ok"] is False, "a first exposure outside the judged window is kept")
check(c["s3\tmem\tslug_b"]["ok"] is True and c["s3\tmem\tslug_c"]["ok"] is None, "memory lines keyed without the mem: prefix")
# a later exposure in the other arm never replaces the first, even with a known outcome
run4 = [{"t": T0 + 900, "sid": "s1", "lines": [["place", "place:dir:~/x", True, []]]}]
c = gl._trial_cohort(run4)
check(c[u]["kind"] == "held_place" and c[u]["t"] == T0 + 10, "a later opposite-arm exposure does not replace the first")
# round 6 (dot D7CB6565's review of version 3)
gl.LIVE = tempfile.mkdtemp()
p = os.path.join(gl.LIVE, "trial_cohort.json")
gl._trial_cohort([{"t": T0 + 1, "eid": "e1", "sid": "d", "lines": [["mem", "slug_d", True, []]]}], write=False)
check(not os.path.exists(p), "A: write=False (graph_learn --dry) persists nothing")
r1 = [{"t": T0 + 5, "eid": "e1", "sid": "s9", "lines": [["held_mem", "mem:slug_e", None, []]]},
      {"t": T0 + 5, "eid": "e2", "sid": "s9", "lines": [["held_mem", "mem:slug_e", True, []]]}]
c = gl._trial_cohort(r1)
check(c["s9\tmem\tslug_e"]["ok"] is None and c["s9\tmem\tslug_e"]["eid"] == "e1",
      "B: another evaluation sharing a timestamp does not fill the first one's outcome")
c = gl._trial_cohort([{"t": T0 + 5, "eid": "e1", "sid": "s9", "lines": [["held_mem", "mem:slug_e", False, []]]}])
check(c["s9\tmem\tslug_e"]["ok"] is False, "B: the same evaluation fills its own outcome")
c = gl._trial_cohort([{"t": T0 + 2, "eid": "e0", "sid": "s9", "lines": [["mem", "mem:slug_e", True, []]]}])
check(c["s9\tmem\tslug_e"]["eid"] == "e0" and c["s9\tmem\tslug_e"]["kind"] == "mem",
      "B: an earlier exposure arriving late becomes the first")
open(p, "w", encoding="utf-8").write("{not json")
c = gl._trial_cohort([{"t": T0 + 9, "eid": "e9", "sid": "z", "lines": [["mem", "slug_z", True, []]]}])
check(c == {} and gl.TRIAL_COHORT_STATUS[0] == "unreadable" and open(p, encoding="utf-8").read() == "{not json",
      "B: an unreadable cohort file is left untouched and the counts are withheld")
# round 7 (dot D7CB6565's round-6 disposition: legacy identity-free records)
gl.LIVE = tempfile.mkdtemp()
p = os.path.join(gl.LIVE, "trial_cohort.json")
import json
json.dump({"since": T0, "first": {"L\tmem\tslug_l": {"t": T0 + 7, "eid": None, "kind": "mem", "sid": "L", "ok": None},
                                  "M\tmem\tslug_m": {"t": T0 + 7, "eid": None, "kind": "mem", "sid": "M", "ok": None},
                                  "N\tmem\tslug_n": {"t": T0 + 7, "eid": None, "kind": "mem", "sid": "N", "ok": None}}},
          open(p, "w", encoding="utf-8"))
c = gl._trial_cohort([{"t": T0 + 7, "eid": "eL", "sid": "L", "lines": [["mem", "slug_l", True, []]]},
                      {"t": T0 + 7, "sid": "M", "lines": [["mem", "slug_m", False, []]]},
                      {"t": T0 + 8, "eid": "eN", "sid": "N", "lines": [["mem", "slug_n", True, []]]},
                      {"t": T0 + 9, "eid": "eP", "sid": "P", "lines": [["mem", "slug_p", True, []]]}])
L, M, N, P = (c[k + "\tmem\tslug_" + k.lower()] for k in "LMNP")
check(L["eid"] == "eL" and L["identity"] == "migrated_by_timestamp" and L["ok"] is True,
      "C: a legacy record met by an eid-bearing judgment at the same second adopts the id, marked migrated")
check(M["identity"] == "timestamp" and M["ok"] is False and M["eid"] is None,
      "C: two identity-free records matched by timestamp alone are filled but marked timestamp, never verified")
check(N["ok"] is None and N.get("identity") is None and N["eid"] is None,
      "C: a judgment one second off neither fills nor migrates a legacy record")
check(P["identity"] == "eid", "C: a new record with an evaluation id is identity-verified")
saved = json.load(open(p, encoding="utf-8"))["first"]
check(saved["L\tmem\tslug_l"]["identity"] == "migrated_by_timestamp", "C: the identity marks are persisted")
print(("PASS" if not fails else "FAIL") + " - trial cohort controls (rounds 6 and 7), %d failed" % fails)
sys.exit(1 if fails else 0)
