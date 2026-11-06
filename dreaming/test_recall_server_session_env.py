"""brain_recall_server.Brain.call runs each request under the caller's identity only.

A caller with no CLAUDE_CODE_SESSION_ID must not inherit the server's own (the spawner's), and the server's
environ comes back unchanged after the call. BRS_POP_SESSION_ENV=0 keeps the old inheritance.
Run: python3 test_recall_server_session_env.py
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brain_recall_server as brs  # noqa: E402

FAILS = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


def fake_brain():
    b = brs.Brain.__new__(brs.Brain)
    b.miss = {}
    b.calls = 0
    b.refresh_state = lambda: []

    def main():
        print("sess=%s entry=%s" % (os.environ.get("CLAUDE_CODE_SESSION_ID", "-"),
                                    os.environ.get("CLAUDE_CODE_ENTRYPOINT", "-")))
    b.g = {"main": main}
    return b


def run(env):
    sys.modules.pop("brain_call_sampler", None)
    sys.modules["brain_call_sampler"] = types.SimpleNamespace(begin=lambda: None)
    return fake_brain().call(["recall", "x"], env, HERE)["out"].strip()


def main():
    os.environ["CLAUDE_CODE_SESSION_ID"] = "spawner1"
    os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"
    before = dict(os.environ)

    os.environ.pop("BRS_POP_SESSION_ENV", None)
    check("caller with no session id runs with none", run({}) == "sess=- entry=-")
    check("environ restored after a bare call", dict(os.environ) == before)
    check("caller's own id is used", run({"CLAUDE_CODE_SESSION_ID": "caller22", "CLAUDE_CODE_ENTRYPOINT": "sdk-py"})
          == "sess=caller22 entry=sdk-py")
    check("environ restored after an id call", dict(os.environ) == before)
    check("codex caller drops the claude id", run({"CODEX_THREAD_ID": "t1"}) == "sess=- entry=-")

    os.environ["BRS_POP_SESSION_ENV"] = "0"
    check("BRS_POP_SESSION_ENV=0 keeps the old inheritance", run({}) == "sess=spawner1 entry=cli")
    os.environ.pop("BRS_POP_SESSION_ENV")
    check("environ restored after the off switch", dict(os.environ) == before)

    print("%d failed" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
