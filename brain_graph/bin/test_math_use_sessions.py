"""Controls for math_use.py's session filter and math_note.py's fetch session column (27 Sep, claude-825b1305).

Every path the build reads or writes is pointed into a temporary directory; nothing live is touched.

    python test_math_use_sessions.py
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BIN)
import math_use  # noqa: E402
import session_kinds as sk  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def shard_name(sid):
    return hashlib.sha1(sid.encode("utf-8", "replace")).hexdigest()[:24]


def transcript(path, entry, first):
    rows = []
    if entry:
        rows.append({"type": "system", "entrypoint": entry})
    if first is not None:
        rows.append({"type": "user", "entrypoint": entry, "message": {"role": "user", "content": first}})
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def main():
    tmp = tempfile.mkdtemp(prefix="mu_test_")
    live = {k: getattr(math_use, k) for k in ("STORE", "SHARDS", "ARCHIVE", "SNAPSHOT", "SERVES", "FETCHES")}
    live_sk = (sk.PROJECTS, sk.CODEX, sk.CACHE)
    try:
        proj = os.path.join(tmp, "projects", "C--work")
        os.makedirs(proj)
        his, test, script, fresh = ("%s-aaaa-4aaa-8aaa-000000000000" % (c * 8) for c in "abcd")
        transcript(os.path.join(proj, his + ".jsonl"), "cli", "prove the lemma about the kernel")
        transcript(os.path.join(proj, test + ".jsonl"), "sdk-cli", "[headless-worker] bank question 3")
        transcript(os.path.join(proj, script + ".jsonl"), "sdk-cli", "render the next film part")
        transcript(os.path.join(proj, fresh + ".jsonl"), None, None)          # too new to show its entrypoint
        codex = os.path.join(tmp, "codex")
        os.makedirs(codex)
        cx_cli, cx_exec = "019f0001-bbbb-7bbb-8bbb-000000000001", "019f0001-bbbb-7bbb-8bbb-000000000002"  # one minute
        db = sqlite3.connect(os.path.join(codex, "state_5.sqlite"))
        db.execute("create table threads (id text primary key, source text)")
        db.executemany("insert into threads values (?, ?)", [(cx_cli, "cli"), (cx_exec, "exec")])
        db.commit()
        db.close()
        shards = os.path.join(tmp, "shards")
        os.makedirs(shards)
        now = time.time()

        def put(sid, ids, ts):
            json.dump({"ids": ids, "ts": ts}, open(os.path.join(shards, shard_name(sid) + ".json"), "w"))
        put(his, ["claim_A", "claim_B"], now - 3600)
        put(script, ["claim_A", "claim_B"], now - 1800)
        put(cx_cli, ["claim_C"], now - 900)
        put(cx_exec, ["claim_D"], now - 600)
        put(test, ["claim_T1", "claim_T2"], now - 500)
        put(fresh, ["claim_U"], now - 400)
        for i in range(30):                                                # an evaluation harness: a fresh id per question
            put("vib-%032x" % i, ["claim_T1", "claim_T2"], now - 300 + i)
        archive = os.path.join(tmp, "archive.jsonl")
        with open(archive, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"shard": shard_name("vib-archived"), "archived": now - 7200,
                                 "rec": {"ids": ["claim_T1", "claim_T2"], "ts": now - 7200}}) + "\n")
            fh.write(json.dumps({"shard": shard_name(his), "archived": now - 86400,
                                 "rec": {"ids": ["claim_E"], "ts": now - 86400}}) + "\n")
        serves = os.path.join(tmp, "serves.jsonl")
        with open(serves, "w", encoding="utf-8") as fh:
            for sid, ids in ((his, ["claim_A"]), (script[:8], ["claim_B"]), ("", ["claim_T1"]),
                             ("vib-0000", ["claim_T2"]), (test, ["claim_T1"]), (cx_cli[:8], ["claim_T2"])):
                fh.write(json.dumps({"t": now - 100, "sid": sid, "ids": ids}) + "\n")
        fetches = os.path.join(tmp, "fetches.tsv")
        with open(fetches, "w", encoding="utf-8") as fh:
            fh.write("%d\t%s\tclaim_F\n" % (now - 50, his))
            fh.write("%d\t\tclaim_G\n" % (now - 40))
            fh.write("%d\t%s\tclaim_H\n" % (now - 30, test))
            fh.write("%d\t%s\tclaim_K\tfailed:2\n" % (now - 20, his))      # a refused read (B19-R79)
            fh.write("%d\t%s\tclaim_L\tok\n" % (now - 10, his))
        math_use.STORE = os.path.join(tmp, "store.json")
        sk.CACHE = os.path.join(tmp, "kinds.json")
        math_use.SHARDS, math_use.ARCHIVE = shards, archive
        math_use.SNAPSHOT = os.path.join(tmp, "no_snapshot.jsonl")
        math_use.SERVES, math_use.FETCHES = serves, fetches
        sk.PROJECTS, sk.CODEX = os.path.join(tmp, "projects"), codex
        sk.reset()
        r = math_use.build()
        st = json.load(open(math_use.STORE, encoding="utf-8"))
        kept, skipped, uses, co = r["kept"], r["skipped"], st["uses"], st["co"]
        check("1 his shard kept (live and archived)", kept.get("shard his") == 3, str(kept))
        check("2 script shard kept (Claude sdk-cli and Codex exec)", kept.get("shard script") == 2, str(kept))
        check("3 test-worker shard skipped", skipped.get("shard test") == 1, str(skipped))
        check("4 evaluation-harness shards skipped", skipped.get("shard no session") == 31, str(skipped))
        check("5 a transcript too new to classify is skipped and not cached",
              skipped.get("shard unknown") == 1 and fresh not in json.load(open(sk.CACHE)), str(skipped))
        check("6 serve log: his (whole id) and script (old 8-character id) kept; empty, harness, test skipped",
              kept.get("serve his") == 1 and kept.get("serve script") == 1 and skipped.get("serve no session") == 1
              and skipped.get("serve test") == 1, str((kept, skipped)))
        check("6b a harness id and an old 8-character id two Codex threads of different kinds share are unknown",
              skipped.get("serve unknown") == 2, str(skipped))
        check("7 fetch: his kept at weight 2, empty and test skipped",
              [w for _t, w in uses.get("claim_F", [])] == [2.0] and "claim_G" not in uses and "claim_H" not in uses
              and skipped.get("fetch no session") == 1 and skipped.get("fetch test") == 1, str(uses.get("claim_F")))
        check("7b fetch: a refused read is not a use, a read logged ok is",
              "claim_K" not in uses and [w for _t, w in uses.get("claim_L", [])] == [2.0], str(uses.get("claim_L")))
        check("8 claims used only by tests and harnesses have no size", not any(c in uses for c in
                                                                                   ("claim_T1", "claim_T2", "claim_U")))
        check("9 real co-use makes an edge (A-B in his and a script session)", co.get("claim_A", {}).get("claim_B", 0) > 0,
              str(co))
        check("10 harness co-use makes no edge (T1-T2 served together 32 times)", "claim_T1" not in co)
        check("11 claims from real shards have sizes", all(c in uses for c in ("claim_A", "claim_B", "claim_C",
                                                                                "claim_D", "claim_E")))
        kinds = json.load(open(sk.CACHE, encoding="utf-8"))
        check("12 kind cache holds the settled Claude kinds",
              kinds.get(his) == "his" and kinds.get(test) == "test" and kinds.get(script) == "script", str(kinds))
        # the fetch logger, from a copy so its log lands in the temporary directory
        nd = os.path.join(tmp, "note")
        os.makedirs(nd)
        shutil.copy(os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "math_note.py"), nd)
        spec = importlib.util.spec_from_file_location("math_note_copy", os.path.join(nd, "math_note.py"))
        mn = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mn)
        saved = {k: os.environ.pop(k, None) for k in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CODEX_THREAD_ID")}
        try:
            os.environ["CLAUDE_CODE_SESSION_ID"] = his
            mn._log_fetch("claim_X")
            del os.environ["CLAUDE_CODE_SESSION_ID"]
            os.environ["CODEX_THREAD_ID"] = cx_cli
            mn._log_fetch("claim_Y")
            del os.environ["CODEX_THREAD_ID"]
            mn._log_fetch("claim_Z")
            os.environ["CLAUDE_CODE_SESSION_ID"] = his                  # an evaluation run inside his session's shell
            os.environ["CLAUDE_SESSION_ID"] = "vib-label"
            mn._log_fetch("claim_V")
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
        rows = [l.rstrip("\n").split("\t") for l in open(os.path.join(nd, "mathbrain_fetches.tsv"), encoding="utf-8")]
        check("13 fetch log records the Claude session", rows[0][1:] == [his, "claim_X"], str(rows))
        check("14 fetch log records a Codex thread", rows[1][1:] == [cx_cli, "claim_Y"], str(rows))
        check("15 fetch log stays empty with no session", rows[2][1:] == ["", "claim_Z"], str(rows))
        check("16 an evaluation's own label wins over the shell's session", rows[3][1:] == ["vib-label", "claim_V"],
              str(rows))
        # the read's outcome (account04/B19, 30 Sep): the row is written after the read, with ok or failed:<code>
        import types
        saved_mods = {k: sys.modules.get(k) for k in ("math_brain", "math_brain.current_note")}
        pkg, cn = types.ModuleType("math_brain"), types.ModuleType("math_brain.current_note")
        pkg.__path__ = []
        sys.modules["math_brain"], sys.modules["math_brain.current_note"] = pkg, cn
        raised = False
        try:
            cn.main = lambda argv: 2
            rc_bad = mn.main(["INVALID ID WITH SPACE"])
            cn.main = lambda argv: 0
            rc_ok = mn.main(["claim_OK"])

            def exits(argv):
                raise SystemExit(2)
            cn.main = exits
            try:
                mn.main(["claim_EXIT"])
            except SystemExit:
                raised = True
        finally:
            for k, v in saved_mods.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        rows = [l.rstrip("\n").split("\t") for l in open(os.path.join(nd, "mathbrain_fetches.tsv"), encoding="utf-8")]
        check("16b a refused read is logged after the read, with its outcome",
              rc_bad == 2 and rows[4][2:] == ["INVALID ID WITH SPACE", "failed:2"], str(rows[4:]))
        check("16c a full read is logged ok", rc_ok == 0 and rows[5][2:] == ["claim_OK", "ok"], str(rows[4:]))
        check("16d a reader that exits is logged with its code and still exits",
              raised and rows[6][2:] == ["claim_EXIT", "failed:2"], str(rows[4:]))
        # brain_switch's command line, from a copy whose flags land in the temporary directory
        shutil.copy(os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "brain_switch.py"), nd)
        spec = importlib.util.spec_from_file_location("brain_switch_copy", os.path.join(nd, "brain_switch.py"))
        bs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bs)
        bs.OFFLINE_DIR = os.path.join(tmp, "offline")
        bs.GLOBAL_FLAG = os.path.join(bs.OFFLINE_DIR, "ALL.flag")
        saved = {k: os.environ.pop(k, None) for k in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")}
        import contextlib
        import io
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                bs._cli(["off"])
                none_written = sorted(os.listdir(bs.OFFLINE_DIR)) if os.path.isdir(bs.OFFLINE_DIR) else []
                os.environ["CLAUDE_CODE_SESSION_ID"] = his
                bs._cli(["off"])
                off = sorted(os.listdir(bs.OFFLINE_DIR))
                bs._cli(["on"])
                on = sorted(os.listdir(bs.OFFLINE_DIR))
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
        check("17 brain_switch off from a session's shell switches that session off",
              none_written == [] and off == [his + ".flag"] and not os.path.exists(bs.GLOBAL_FLAG), str((none_written, off)))
        check("18 brain_switch on switches it back", on == [], str(on))
    finally:
        for k, v in live.items():
            setattr(math_use, k, v)
        sk.PROJECTS, sk.CODEX, sk.CACHE = live_sk
        sk.reset()
        shutil.rmtree(tmp, ignore_errors=True)
    print("%d of 19 controls pass" % (19 - len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
