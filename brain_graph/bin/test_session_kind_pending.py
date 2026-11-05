"""test_session_kind_pending.py - a headless session is not settled before its first prompt, and the learner's session
kinds are the shared resolver's (29 Sep 2026, claude-825b1305; interactive audit S01, scheduled audits account02/B18 r77
B18-R9-SESSION-KIND-PREMATURE-SCRIPT-CACHE-001 and account03/B01 auto73 B01-R9-GRAPH-LEARN-PREFIX-KIND-ORDER-DEPENDENT).

    python test_session_kind_pending.py [--old-kinds OLD_SESSION_KINDS.py] [--old-learn OLD_GRAPH_LEARN.py]

Throwaway project folders, Codex folder and cache; nothing live is read or written.
 K1 an sdk-cli transcript with no prompt yet, just written: unknown, and not cached
 K2 its first prompt arrives, '[headless-worker] ...': test, with no cache to delete
 K3 an sdk-cli transcript with an ordinary first prompt: script
 K4 an sdk-cli transcript that never got a prompt, quiet 11 minutes: script
 K5 a first prompt behind 70 KB of attachment records, '[headless-worker] ...': test
 K6 a cli transcript with no prompt: his
 K7 two sessions sharing their first 8 characters, one his and one test, in either listing order: the shared resolver
    and the learner both say unknown for the 8-character key
 K8 the whole ids keep their own kinds, through the learner too
With --old-kinds, the classifier given must settle K1 as script (N1) or read K5 as script (N2); with --old-learn, the
learner given must answer K7's key by listing order (N3). Each check can fail.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import session_kinds as sk  # noqa: E402

ARGS = sys.argv[1:]
OLD_K = os.path.abspath(ARGS[ARGS.index("--old-kinds") + 1]) if "--old-kinds" in ARGS else None
OLD_L = os.path.abspath(ARGS[ARGS.index("--old-learn") + 1]) if "--old-learn" in ARGS else None
fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def sid(n, prefix="0000aaaa"):
    return "%s-0000-4000-8000-%012d" % (prefix, n)


def write(folder, s, entry, prompt=None, pad=0, age=0.0):
    os.makedirs(folder, exist_ok=True)
    p = os.path.join(folder, s + ".jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "system", "entrypoint": entry, "sessionId": s}) + "\n")
        if pad:
            fh.write(json.dumps({"type": "attachment", "entrypoint": entry, "blob": "x" * pad}) + "\n")
        if prompt is not None:
            fh.write(json.dumps({"type": "user", "entrypoint": entry, "message": {"role": "user", "content": prompt}}) + "\n")
    if age:
        t = time.time() - age
        os.utime(p, (t, t))
    return p


def point(m, td):
    m.PROJECTS = os.path.join(td, "projects")
    m.CODEX = os.path.join(td, "codex")
    m.CACHE = os.path.join(td, "cache", "session_kinds.json")
    os.makedirs(m.CODEX, exist_ok=True)
    os.makedirs(os.path.dirname(m.CACHE), exist_ok=True)
    m.reset()


def classifier_cases(m):
    td = tempfile.mkdtemp(prefix="sk_pending_")
    r = {}
    try:
        proj = os.path.join(td, "projects", "C--proj")
        p1 = write(proj, sid(1), "sdk-cli")
        write(proj, sid(3), "sdk-cli", "summarise the file")
        write(proj, sid(4), "sdk-cli", age=660)
        write(proj, sid(5), "sdk-cli", "[headless-worker] grade this answer", pad=70000)
        write(proj, sid(6), "cli")
        point(m, td)
        r["K1"] = m.kind(sid(1))
        r["K1_cached"] = sid(1) in m._cache()
        with open(p1, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "user", "entrypoint": "sdk-cli",
                                 "message": {"role": "user", "content": "[headless-worker] rewrite this query"}}) + "\n")
        r["K2"] = m.kind(sid(1))
        r["K3"] = m.kind(sid(3))
        r["K4"] = m.kind(sid(4))
        r["K5"] = m.kind(sid(5))
        r["K6"] = m.kind(sid(6))
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return r


def learner_cases(learn_path, tag):
    out = {}
    for order in ("his_first", "test_first"):
        td = tempfile.mkdtemp(prefix="sk_learn_")
        try:
            a, b = os.path.join(td, "projects", "A-proj"), os.path.join(td, "projects", "B-proj")
            his_dir, test_dir = (a, b) if order == "his_first" else (b, a)
            write(his_dir, sid(1, "deadbeef"), "cli", "look at the file")
            write(test_dir, sid(2, "deadbeef"), "sdk-cli", "[headless-worker] grade this")
            point(sk, td)
            gl = load(learn_path, "gl_%s_%s" % (tag, order))
            gl.PROJECTS = os.path.join(td, "projects")
            idx = gl.transcript_index(max_age_days=8) if hasattr(gl, "transcript_index") else {}
            out[order] = {"shared": sk.kind("deadbeef"), "learner": gl.session_kind("deadbeef", idx, {}),
                          "his_full": gl.session_kind(sid(1, "deadbeef"), idx, {}),
                          "test_full": sk.kind(sid(2, "deadbeef"))}
        finally:
            shutil.rmtree(td, ignore_errors=True)
    return out


def main():
    r = classifier_cases(sk)
    check(r["K1"] == "unknown" and not r["K1_cached"], "K1 no prompt yet: %s, cached %s" % (r["K1"], r["K1_cached"]))
    check(r["K2"] == "test", "K2 its '[headless-worker]' prompt arrives: %s" % r["K2"])
    check(r["K3"] == "script", "K3 an ordinary first prompt: %s" % r["K3"])
    check(r["K4"] == "script", "K4 no prompt, quiet 11 minutes: %s" % r["K4"])
    check(r["K5"] == "test", "K5 the prompt behind 70 KB of attachments: %s" % r["K5"])
    check(r["K6"] == "his", "K6 cli, no prompt: %s" % r["K6"])
    lr = learner_cases(os.path.join(HERE, "graph_learn.py"), "new")
    check(all(v["shared"] == "unknown" and v["learner"] == "unknown" for v in lr.values()),
          "K7 a shared 8-character key, either order: shared %s, learner %s" % (
              [v["shared"] for v in lr.values()], [v["learner"] for v in lr.values()]))
    check(all(v["his_full"] == "his" and v["test_full"] == "test" for v in lr.values()),
          "K8 whole ids keep their kinds: %s" % ([(v["his_full"], v["test_full"]) for v in lr.values()]))
    if OLD_K:
        o = classifier_cases(load(OLD_K, "sk_old"))
        check(o["K1"] == "script" or o["K5"] == "script",
              "N1/N2 the classifier given settles K1 as %s (cached %s) and reads K5 as %s (the check can fail)" % (
                  o["K1"], o["K1_cached"], o["K5"]))
    if OLD_L:
        ol = learner_cases(OLD_L, "old")
        answers = [ol["his_first"]["learner"], ol["test_first"]["learner"]]
        check(answers[0] != answers[1], "N3 the learner given answers K7's key by listing order: %s (the check can fail)"
              % answers)
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
