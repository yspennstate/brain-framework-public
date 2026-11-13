"""The task checklist on a fresh install, on both runtimes.

An empty HOME, one ask sent through the hook as Claude Code and as Codex would send it, then
working, done and list from the command line. The ask must be registered on its own (no agent
call), and the result line must reach CHECKLIST.md.

Run: python -I dreaming/test_task_checklist_fresh_install.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "task_checklist_hook.py")


def run(env, cwd, args, stdin=None):
    p = subprocess.run([sys.executable, "-I", HOOK] + args, input=stdin, capture_output=True,
                       text=True, encoding="utf-8", env=env, cwd=cwd, timeout=120)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def one_runtime(runtime):
    tmp = tempfile.mkdtemp(prefix="tc_fresh_")
    try:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("TASK_CHECKLIST", "CODEX_", "CLAUDE_", "AGENT_MESH"))}
        env.update(HOME=tmp, USERPROFILE=tmp, PYTHONIOENCODING="utf-8")
        payload = json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "session_id": "11111111-2222-3333-4444-55555555555" + str(len(runtime)),
            "prompt": "please write a short summary of the quarterly sales figures and email it to the team",
            "cwd": tmp, "transcript_path": os.path.join(tmp, "t.jsonl")})
        rc, out = run(env, tmp, ["--hook", "--runtime", runtime], payload)
        if rc != 0:
            return "hook rc %d: %s" % (rc, out[:300])
        store = os.path.join(tmp, ".claude", "task_checklist")
        state_p = os.path.join(store, "state.json")
        if not os.path.exists(state_p):
            return "no state.json after the ask"
        st = json.load(open(state_p, encoding="utf-8"))
        entries = st.get("entries") or st.get("tasks") or {}
        ids = list(entries.keys()) if isinstance(entries, dict) else [e.get("id") for e in entries]
        if not ids:
            return "the ask was not registered"
        tid = ids[-1]
        for args in (["working", tid, "writing the summary"], ["done", tid, "summary sent"], ["list"]):
            rc, out = run(env, tmp, args)
            if rc != 0:
                return "%s rc %d: %s" % (args[0], rc, out[:300])
        view_p = os.path.join(store, "CHECKLIST.md")
        view = open(view_p, encoding="utf-8").read() if os.path.exists(view_p) else ""
        if "summary sent" not in view:
            return "the result line is missing from CHECKLIST.md"
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    failed = 0
    for runtime in ("claude", "codex"):
        err = one_runtime(runtime)
        print("%-6s %s" % (runtime, "PASS" if err is None else "FAIL: " + err))
        failed += err is not None
    return 1 if failed else 0


if __name__ == "__main__":
    # Discovery is not execution: a help request exits before any work.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__ or os.path.basename(__file__))
        raise SystemExit(0)
    sys.exit(main())
