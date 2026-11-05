"""test_judge_agenda.py - judge_agenda.py turns an awake agent's verdicts into judged_nightly.jsonl rows of the judge's
own shape, writes nothing on a bad verdict, and the stale judged_nightly line points at it (5 Nov 2026).

    python test_judge_agenda.py [--module JUDGE_AGENDA.py] [--liveness HOOK_LIVENESS.py] [--old OLD_HOOK_LIVENESS.py]

Every run uses a throwaway agenda with one case per stratum, the row templates outside the fences (as dream.py
writes them) and a json block that is not a case, beside a log whose last row has no trailing newline.
 A1 show and template see the three cases and nothing else; template asks useful only of J2 and J3 cases
 A2 write appends three rows whose keys are exactly the agenda templates' for each stratum (plus judge), values
    copied from the case; the unterminated last row gets its newline and every line parses
 A3 one malformed verdict (a bad followed, a missing useful, an empty why) writes nothing, rc 1
 A4 a case id the agenda does not hold writes nothing, rc 1
 A5 writing the same verdicts again appends nothing (already judged), and show then lists 0 unjudged
 A6 --dry-run prints the rows and writes nothing
 A7 hook_liveness's stale hint for judged_nightly off Windows names judge_agenda.py
With --old, the hook_liveness before this change must fail A7 (the check can fail).
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]


def _arg(name, default):
    return os.path.abspath(ARGS[ARGS.index(name) + 1]) if name in ARGS else default


MOD = _arg("--module", os.path.join(HERE, "judge_agenda.py"))
LIV = _arg("--liveness", os.path.join(HERE, "hook_liveness.py"))
OLD = _arg("--old", None)
fails, ran = [], []

TEMPLATES = {
    "target": ["case_id", "instinct", "stratum", "path", "fired_at", "domain_at_play", "followed", "why",
               "judged_by"],
    "firehose": ["case_id", "instinct", "stratum", "proxy", "path", "fired_at", "domain_at_play", "followed", "useful",
                 "why", "judged_by"],
    "suppressed": ["case_id", "instinct", "stratum", "reason", "proxy", "fired_at", "domain_at_play", "followed",
                   "useful", "why", "judged_by"],
}
CASES = [
    {"case_id": "t_000", "fired_at": "2026-11-05 03:54:26", "stratum": "target", "proxy": "act", "path": "regex",
     "instinct": "alpha", "gut": "a gut line", "at_fire": ["x"], "after": ["y"]},
    {"case_id": "f_000", "fired_at": "2026-11-05 04:00:00", "stratum": "firehose", "proxy": "ack", "path": "embed",
     "instinct": "beta", "gut": "another", "at_fire": ["x"], "after": []},
    {"case_id": "s_000", "fired_at": "2026-11-05 05:00:00", "stratum": "suppressed", "proxy": "act",
     "reason": "no_local_signal", "instinct": "gamma", "gut": "third", "at_fire": ["x"], "after": ["y"]},
]
GOOD = {
    "t_000": {"domain_at_play": "yes", "followed": "yes", "why": "it applied and was heeded"},
    "f_000": {"domain_at_play": "no", "followed": "no_opportunity", "useful": "no", "why": "off domain"},
    "s_000": {"domain_at_play": "unclear", "followed": "no", "useful": "yes", "why": "would have helped"},
}
OLD_ROW = '{"case_id": "old_1", "instinct": "alpha", "domain_at_play": "no"}'


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def fixture(td):
    agenda = os.path.join(td, "DREAM_AGENDA.md")
    parts = ["# Dream agenda", "", "## J", "", "Append rows like:",
             '{"case_id": "...", "instinct": "...", "stratum": "target", "judged_by": "dream"}', "",
             "```json", json.dumps({"note": "not a case", "stratum": "target"}), "```", ""]
    for c in CASES:
        parts += ["```json", json.dumps(c), "```", ""]
    with open(agenda, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    log = os.path.join(td, "judged_nightly.jsonl")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(OLD_ROW)  # no trailing newline, as a hand append can leave it
    return agenda, log


def run(*a):
    p = subprocess.run([sys.executable, "-s", MOD] + list(a), capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout, p.stderr


def read(p):
    with open(p, "rb") as fh:
        return fh.read()


def verdicts(td, name, obj):
    p = os.path.join(td, name)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    return p


def hint_of(path):
    loader = importlib.machinery.SourceFileLoader("ja_liveness_%d" % len(ran), path)
    spec = importlib.util.spec_from_file_location(loader.name, path, loader=loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    h = getattr(m, "_stale_hint", None)
    return h("judged_nightly.jsonl", "darwin") if h else ""


def main():
    td = tempfile.mkdtemp(prefix="judge_agenda_test_")
    try:
        agenda, log = fixture(td)
        io_args = ["--agenda", agenda, "--log", log]
        rc, out, err = run("show", *io_args)
        rc2, tout, _ = run("template", *io_args)
        try:
            t = json.loads(tout)
        except ValueError:
            t = {}
        check(rc == 0 and out.startswith("3 case(s) in the agenda, 3 unjudged") and rc2 == 0
              and sorted(t) == ["f_000", "s_000", "t_000"] and "useful" not in t.get("t_000", {"useful": 1})
              and "useful" in t.get("f_000", {}) and "useful" in t.get("s_000", {}),
              "A1 show/template see 3 cases, useful asked of J2/J3 only: %s" % (out.splitlines() or [err])[0][:60])

        before = read(log)
        bad = dict(GOOD, t_000={"domain_at_play": "yes", "followed": "maybe", "why": "x"},
                   f_000={"domain_at_play": "no", "followed": "no", "why": "x"},
                   s_000={"domain_at_play": "no", "followed": "no", "useful": "no", "why": " "})
        rc, out, err = run("write", verdicts(td, "bad.json", bad), "--judge", "tester", *io_args)
        check(rc == 1 and read(log) == before and err.count("ERROR") == 3,
              "A3 three malformed verdicts: rc %s, %d error(s), log unchanged %s" % (rc, err.count("ERROR"),
                                                                                    read(log) == before))
        unk = dict(GOOD, z_999={"domain_at_play": "yes", "followed": "yes", "why": "x"})
        rc, out, err = run("write", verdicts(td, "unk.json", unk), "--judge", "tester", *io_args)
        check(rc == 1 and read(log) == before and "z_999" in err,
              "A4 an unknown case id: rc %s, log unchanged %s" % (rc, read(log) == before))

        good = verdicts(td, "good.json", GOOD)
        rc, out, err = run("write", good, "--judge", "tester", "--dry-run", *io_args)
        check(rc == 0 and read(log) == before and out.count('"case_id"') == 3,
              "A6 dry run prints 3 rows and writes nothing: rc %s" % rc)

        rc, out, err = run("write", good, "--judge", "tester", *io_args)
        text = read(log).decode("utf-8")
        lines = text.splitlines()
        rows, parsed = [], True
        for line in lines:
            try:
                rows.append(json.loads(line))
            except ValueError:
                parsed = False
        new = {r.get("case_id"): r for r in rows[1:]}
        shape = all(c["case_id"] in new and list(new[c["case_id"]]) == TEMPLATES[c["stratum"]] + ["judge"]
                    for c in CASES)
        copied = all(c["case_id"] in new and all(new[c["case_id"]][k] == c[k] for k in TEMPLATES[c["stratum"]]
                                                 if k in c)
                     for c in CASES)
        values = all(c["case_id"] in new and new[c["case_id"]]["judged_by"] == "dream"
                     and new[c["case_id"]]["judge"] == "tester"
                     and new[c["case_id"]]["followed"] == GOOD[c["case_id"]]["followed"] for c in CASES)
        check(rc == 0 and parsed and len(lines) == 4 and lines[0] == OLD_ROW and text.endswith("\n")
              and shape and copied and values,
              "A2 3 rows appended in template shape (parsed %s, lines %d, shape %s, copied %s, values %s)"
              % (parsed, len(lines), shape, copied, values))

        after = read(log)
        rc, out, err = run("write", good, "--judge", "tester", *io_args)
        rc2, sout, _ = run("show", *io_args)
        check(rc == 0 and read(log) == after and "appended 0 row(s)" in out and "3 already judged" in out
              and sout.startswith("3 case(s) in the agenda, 0 unjudged"),
              "A5 a second write appends nothing: %s" % out.strip()[:90])
    finally:
        shutil.rmtree(td, ignore_errors=True)

    h = hint_of(LIV)
    check("judge_agenda.py" in h, "A7 the stale judged_nightly hint names the tool: %r" % h[-70:])
    if OLD:
        o = hint_of(OLD)
        check("judge_agenda.py" not in o,
              "N1 the hook_liveness before this change does not name it (the check can fail): %r" % o[-60:])
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
