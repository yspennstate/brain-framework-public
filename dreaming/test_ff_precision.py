"""test_ff_precision.py - a forward-feed fire is selected by its own judged route record, and every fire's hint names
the route it came through (27 Sep, claude-825b1305; scheduled audit account01/B04 auto11, F158 and F159). The acceptance
B04 wrote: synthetic low-precision ff evidence changes ff selection and leaves regex selection for the same id alone.

    python test_ff_precision.py [PATH_TO_instinct_prime.py]

Controls (synthetic precision; fake gfcore and forward_feed modules; the fire and suppression logs sent to temp files):
 1. ff judged 1 of 10: the ff fire is not added, and a 'precision_ff' suppression is recorded
 2. ff judged 7 of 10: the ff fire is added, marked _via ff
 3. no ff record: added (unjudged routes are untouched)
 4. a never-damped scar with a low ff record: added
 5. the same reflex fired by its regex trigger stays in the result with ff judged 1 of 10
 6. the hint of an ff fire quotes the ff record ('1 of 10 ... forward feed'); a regex fire quotes the pooled record
    ('6 of 20'); an ff fire with no ff record falls back to the pooled one
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import importlib.util
import os
import sys
import tempfile
import types

H = os.path.expanduser("~")
IP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(H, ".claude", "dreaming", "instinct_prime.py")
sys.path.insert(0, os.path.join(H, ".claude", "dreaming"))
spec = importlib.util.spec_from_file_location("ip_under_test", IP)
ip = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ip)
tmp = tempfile.mkdtemp(prefix="ff_prec_test_")
for name in ("FIRELOG", "SUPPRESSLOG", "SUPPRESSED_LOG"):
    if hasattr(ip, name):
        setattr(ip, name, os.path.join(tmp, name.lower() + ".tsv"))
suppressed = []
ip._suppress = lambda rid, reason: suppressed.append((rid, reason))

gf = types.ModuleType("gfcore")
gf.graph_fire = lambda text, session: []
gf.shadow_log = lambda *a, **k: None
gf.note_fires = lambda *a, **k: None
ff = types.ModuleType("forward_feed")
FF_OUT = []
ff.forward_feed = lambda text, tn, session: list(FF_OUT)
sys.modules["gfcore"], sys.modules["forward_feed"] = gf, ff
ip.GF_ENABLED = os.path.join(tmp, "ENABLED")
open(ip.GF_ENABLED, "w").close()

RID = "windows_backslash_path"
SAFE = sorted(ip._NEVER_DAMP)[0]


def prec(ff_on=None, ff_n=10):
    rec = {"judged": True, "n": 20, "on_domain": 6, "p": 0.3,
           "paths": {"regex": {"judged": True, "n": 10, "on_domain": 5, "p": 0.5}}}
    if ff_on is not None:
        rec["paths"]["ff"] = {"judged": True, "n": ff_n, "on_domain": ff_on, "p": ff_on / float(ff_n)}
    return {RID: rec, SAFE: {"judged": True, "n": 10, "on_domain": 1, "p": 0.1,
                             "paths": {"ff": {"judged": True, "n": 10, "on_domain": 1, "p": 0.1}}}}


def run(p, fired, ff_ids):
    ip._load_precision = lambda: p
    FF_OUT[:] = [{"id": i, "gut": "a command like this one just failed", "why": "scar"} for i in ff_ids]
    data = {"session_id": "0123abcd-0000-4000-8000-000000000000", "tool_name": "Bash"}
    return ip._augment_graph("cp C:\\x y", data, [], list(fired))


fails = []
# 1
suppressed.clear()
out = run(prec(1), [], [RID])
if any(i["id"] == RID for i in out) or (RID, "precision_ff") not in suppressed:
    fails.append("1 low ff record: out=%r suppressed=%r" % ([i["id"] for i in out], suppressed))
# 2
out = run(prec(7), [], [RID])
if not any(i["id"] == RID and i.get("_via") == "ff" for i in out):
    fails.append("2 good ff record: %r" % out)
# 3
out = run(prec(None), [], [RID])
if not any(i["id"] == RID for i in out):
    fails.append("3 no ff record: %r" % out)
# 4
out = run(prec(1), [], [SAFE])
if not any(i["id"] == SAFE for i in out):
    fails.append("4 never-damped: %r" % out)
# 5
regex_fire = {"id": RID, "mode": "fire", "gut": "backslash path", "sting": "", "source": "MISTAKES", "charge": 0.9}
out = run(prec(1), [regex_fire], [RID])
if not (any(i is regex_fire or (i["id"] == RID and i.get("_via") in (None, "regex")) for i in out)):
    fails.append("5 regex fire lost: %r" % out)
# 6
p = prec(1)
h_ff = ip._hint(RID, p, "ff")
h_rx = ip._hint(RID, p, None)
h_ff_none = ip._hint(RID, prec(None), "ff")
if not ("1 of 10" in h_ff and "forward feed" in h_ff and "6 of 20" in h_rx and "6 of 20" in h_ff_none):
    fails.append("6 hints: ff=%r rx=%r ff_no_record=%r" % (h_ff, h_rx, h_ff_none))

print("FAIL" if fails else "PASS", "- 6 controls;", "; ".join(fails) if fails else
      "low ff record silences the ff fire and only it, good or absent records pass, safety scars exempt, "
      "the regex fire of the same id kept, hints name their route")
sys.exit(1 if fails else 0)
