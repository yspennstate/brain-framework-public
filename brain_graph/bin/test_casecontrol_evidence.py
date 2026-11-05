"""test_casecontrol_evidence.py - the correction case-control study binds the evidence it ran on, and its verifier refuses
a missing or altered rows file (28 Sep 2026, claude-825b1305; scheduled audit account01/B01 r66,
B01-R13-CORRECTION-CASECONTROL-EVIDENCE-GENERATION-UNBOUND-20260928).

    python test_casecontrol_evidence.py [--study PATH] [--old OLD_STUDY.py]

 E1 one pre-cut transcript event changed, the cut unchanged: the events digest and the evidence id change
 E2 one verdict row changed: the events digest and the evidence id change
 E3 nothing changed: the same digest and id twice
 V1 each 27 Sep result, with its rows file and the binding: MATCH
 V2 the rows file missing: refused (2)
 V3 one score in the rows file changed: refused (3)
 V4 the rows intact, the result's AUC changed: MISMATCH (1)
 V5 a result that carries evidence_generation, checked against its own rows digest: MATCH; the same with one row
    changed: refused (3)
With --old the study before this change must have no evidence_generation (N1: the check can fail).
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BIN)
REPO = os.path.join(os.path.expanduser("~"), "ai-memories-and-functionality", "12_cognitive_architecture", "brain_graph")
BINDING = os.path.join(REPO, "results", "CORRECTIONS_CASECONTROL_BINDING_2026-09-28.json")
RESULTS = ("CORRECTIONS_CASECONTROL_2026-09-27.json", "CORRECTIONS_CASECONTROL_PLACEBO_2026-09-27.json")
VER = os.path.join(BIN, "verify_casecontrol.py")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
ARGS = sys.argv[1:]
STUDY = ARGS[ARGS.index("--study") + 1] if "--study" in ARGS else os.path.join(BIN, "study_corrections_casecontrol.py")
OLD = ARGS[ARGS.index("--old") + 1] if "--old" in ARGS else None
fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def load(path, name):
    import importlib.machinery                      # any file name, a backup's too
    spec = importlib.util.spec_from_file_location(name, path, loader=importlib.machinery.SourceFileLoader(name, path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def verify(res, *extra):
    p = subprocess.run([sys.executable, "-X", "utf8", VER, res] + list(extra), capture_output=True, text=True,
                       creationflags=NO_WINDOW)
    return p.returncode, (p.stdout + p.stderr).strip()


def main():
    st = load(STUDY, "scc_under_test")
    by = {"aaaa1111": [(100.0, "tool", "Bash"), (160.0, "file", "x.py"), (220.0, "verdict", "correction")],
          "bbbb2222": [(90.0, "kw", "brain"), (300.0, "tool", "Edit")]}
    tmp = tempfile.mkdtemp(prefix="cc_evidence_")
    try:
        rows_p = os.path.join(tmp, "rows.jsonl")
        open(rows_p, "w").write('{"sid": "aaaa1111", "t": 160.0, "score": 80.0, "case": true}\n')

        def gen(b):
            return st.evidence_generation(b, sorted(b), 150.0, "W_wide", {"verdict_hebb_min": 76.0},
                                          os.path.join(st.SLM.STUDY, "sidecar_wfW_wide.sqlite"),
                                          os.path.join(st.SLM.STUDY, "no_learned.sqlite"), rows_p, 1)
        g0, g0b = gen(by), gen(copy.deepcopy(by))
        b1 = copy.deepcopy(by)
        b1["bbbb2222"][0] = (90.0, "kw", "brains")                  # a pre-cut transcript event, the cut unchanged
        b2 = copy.deepcopy(by)
        b2["aaaa1111"][2] = (220.0, "verdict", "repeat")            # a verdict row
        g1, g2 = gen(b1), gen(b2)
        check(g1["events"]["sha256"] != g0["events"]["sha256"] and g1["id"] != g0["id"] and g1["cut"] == g0["cut"],
              "E1 one pre-cut transcript event changed, the cut unchanged: events %s -> %s, id %s -> %s" % (
                  g0["events"]["sha256"][:12], g1["events"]["sha256"][:12], g0["id"][:12], g1["id"][:12]))
        check(g2["events"]["sha256"] != g0["events"]["sha256"] and g2["id"] != g0["id"],
              "E2 one verdict row changed: events %s -> %s" % (g0["events"]["sha256"][:12], g2["events"]["sha256"][:12]))
        check(g0 == g0b and g0["events"]["events"] == 5 and g0["rows"]["rows"] == 1,
              "E3 nothing changed: the same digest and id twice (%s), 5 events, 1 row" % g0["id"][:12])

        for r in RESULTS:
            rc, out = verify(os.path.join(REPO, "results", r), "--binding", BINDING)
            check(rc == 0 and out.startswith("MATCH"), "V1 %s with its rows and the binding: %s" % (r, out[:110]))
        res_p = os.path.join(REPO, "results", RESULTS[1])
        rc, out = verify(res_p, "--binding", BINDING, "--rows", os.path.join(tmp, "nowhere.jsonl"))
        check(rc == 2, "V2 the rows file missing: exit %d, %s" % (rc, out[:80]))
        b = json.load(open(BINDING, encoding="utf-8"))
        src = os.path.join(st.SLM.STUDY, b["results"][RESULTS[1]]["rows_file"])
        bad = os.path.join(tmp, "rows_altered.jsonl")
        lines = open(src, encoding="utf-8").read().splitlines(True)
        r0 = json.loads(lines[1])
        r0["score"] = r0["score"] + 1.0
        lines[1] = json.dumps(r0) + "\n"
        open(bad, "w", encoding="utf-8").write("".join(lines))
        rc, out = verify(res_p, "--binding", BINDING, "--rows", bad)
        check(rc == 3, "V3 one score in the rows file changed: exit %d, %s" % (rc, out[:80]))
        res_bad = json.load(open(res_p, encoding="utf-8"))
        res_bad["matched_auc"] = 0.7
        res_bad_p = os.path.join(tmp, RESULTS[1])
        json.dump(res_bad, open(res_bad_p, "w", encoding="utf-8"))
        b2p = os.path.join(tmp, "binding.json")
        json.dump(b, open(b2p, "w", encoding="utf-8"))
        rc, out = verify(res_bad_p, "--binding", b2p, "--rows", src)
        check(rc == 1 and "matched_auc" in out, "V4 the rows intact, the result's AUC changed: exit %d, %s" % (rc, out[-60:]))
        own = json.load(open(res_p, encoding="utf-8"))
        own["evidence_generation"] = {"rows": {"file": os.path.basename(src), "sha256": st._sha_file(src)}}
        own_p = os.path.join(tmp, "own_evidence.json")
        json.dump(own, open(own_p, "w", encoding="utf-8"))
        rc_a, out_a = verify(own_p, "--rows", src)
        rc_b, out_b = verify(own_p, "--rows", bad)
        check(rc_a == 0 and rc_b == 3, "V5 a result carrying evidence_generation: its own rows %d, one row changed %d" % (
            rc_a, rc_b))
        if OLD:
            old = load(OLD, "scc_old")
            check(not hasattr(old, "evidence_generation"),
                  "N1 the study before this change binds no evidence (has evidence_generation: %s)" % hasattr(
                      old, "evidence_generation"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
