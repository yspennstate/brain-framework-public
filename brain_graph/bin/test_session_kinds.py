"""Controls for session_kinds.py (27 Sep). Temporary folders only; nothing live is read or written.

    python test_session_kinds.py
"""
import sys as _sys
if __name__ == "__main__" and any(_a in ("-h", "--help") for _a in _sys.argv[1:]):
    # Asking for help must not run the script's real work.
    print((__doc__ or "").strip() or "usage: python " + _sys.argv[0])
    raise SystemExit(0)
import json
import os
import shutil
import sqlite3
import sys
import tempfile

BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BIN)
import session_kinds as sk  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def transcript(path, entry, first):
    with open(path, "w", encoding="utf-8") as fh:
        if entry:
            fh.write(json.dumps({"type": "system", "entrypoint": entry}) + "\n")
        if first is not None:
            fh.write(json.dumps({"type": "user", "entrypoint": entry, "message": {"role": "user", "content": [
                {"type": "text", "text": first}]}}) + "\n")


def main():
    tmp = tempfile.mkdtemp(prefix="sk_test_")
    live = (sk.PROJECTS, sk.CODEX, sk.CACHE)
    try:
        proj = os.path.join(tmp, "projects", "C--work")
        os.makedirs(proj)
        ids = {k: "%s-aaaa-4aaa-8aaa-000000000000" % (c * 8) for k, c in
               (("his", "a"), ("test", "b"), ("script", "c"), ("fresh", "d"), ("rewriter", "e"))}
        transcript(os.path.join(proj, ids["his"] + ".jsonl"), "cli", "prove the lemma")
        transcript(os.path.join(proj, ids["test"] + ".jsonl"), "sdk-cli", "[headless-worker] QUESTION 12")
        transcript(os.path.join(proj, ids["script"] + ".jsonl"), "sdk-cli", "render the next film part")
        transcript(os.path.join(proj, ids["fresh"] + ".jsonl"), None, None)
        transcript(os.path.join(proj, ids["rewriter"] + ".jsonl"), "sdk-cli", "  [headless-worker] Rewrite this search")
        # a grader: two short title records, then a first prompt far longer than the 64 KB head (cut mid-record)
        grader = "f" * 8 + "-aaaa-4aaa-8aaa-000000000000"
        with open(os.path.join(proj, grader + ".jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "custom-title", "customTitle": "grade", "sessionId": grader}) + "\n")
            fh.write(json.dumps({"type": "agent-name", "agentName": "grader", "sessionId": grader}) + "\n")
            fh.write(json.dumps({"type": "user", "entrypoint": "sdk-cli", "message": {"role": "user", "content":
                     "[headless-worker] You are grading packet 17. " + "x " * 40000}}) + "\n")
        codex = os.path.join(tmp, "codex")
        os.makedirs(codex)
        same_min = ("019f0001-bbbb-7bbb-8bbb-000000000001", "019f0001-bbbb-7bbb-8bbb-000000000002")
        other = "019f0002-bbbb-7bbb-8bbb-000000000003"
        db = sqlite3.connect(os.path.join(codex, "state_5.sqlite"))
        db.execute("create table threads (id text primary key, source text)")
        db.executemany("insert into threads values (?, ?)", [(same_min[0], "cli"), (same_min[1], "exec"), (other, "exec")])
        db.commit()
        db.close()
        sk.PROJECTS, sk.CODEX, sk.CACHE = os.path.join(tmp, "projects"), codex, os.path.join(tmp, "kinds.json")
        sk.reset()
        check("1 interactive Claude session is his", sk.kind(ids["his"]) == "his")
        check("2 a [headless-worker] session is test", sk.kind(ids["test"]) == "test")
        check("3 the prefix after leading spaces still marks a worker", sk.kind(ids["rewriter"]) == "test")
        check("4 another headless session is script", sk.kind(ids["script"]) == "script")
        check("5 a transcript with no entrypoint yet is unknown", sk.kind(ids["fresh"]) == "unknown")
        check("6 an evaluation's own id is unknown", sk.kind("vib-0123456789abcdef") == "unknown")
        check("7 an 8-character key resolves when one session has it", sk.kind(ids["his"][:8]) == "his")
        check("8 an upper-case id resolves", sk.kind(ids["test"].upper()) == "test")
        check("9 Codex cli thread is his, exec thread is script",
              sk.kind(same_min[0]) == "his" and sk.kind(other) == "script")
        check("10 an 8-character key two threads of different kinds share is unknown", sk.kind("019f0001") == "unknown")
        check("11 use means his or script", sk.is_work(ids["his"]) and sk.is_work(ids["script"])
              and not sk.is_work(ids["test"]) and not sk.is_work("vib-x"))
        sk.save()
        cache = json.load(open(sk.CACHE, encoding="utf-8"))
        check("12 settled kinds are cached, unknown is not",
              cache.get(ids["his"]) == "his" and cache.get(ids["test"]) == "test" and ids["fresh"] not in cache, str(cache))
        transcript(os.path.join(proj, ids["fresh"] + ".jsonl"), "cli", "now it has started")
        sk.reset()
        check("13 a transcript read as unknown is read again later", sk.kind(ids["fresh"]) == "his")
        check("14 a first prompt longer than the head is still read (a grader is test)", sk.kind(grader) == "test")
    finally:
        sk.PROJECTS, sk.CODEX, sk.CACHE = live
        sk.reset()
        shutil.rmtree(tmp, ignore_errors=True)
    print("%d of 14 controls pass" % (14 - len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
