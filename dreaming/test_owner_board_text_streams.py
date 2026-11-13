"""test_owner_board_text_streams.py - owner_board_hook gives the same text in the dispatcher's process as in its own
(5 Nov 2026).

    python test_owner_board_text_streams.py [--module OWNER_BOARD_HOOK.py] [--old OLD_OWNER_BOARD_HOOK.py]

The dispatcher runs allowlisted hooks inside its own process with text streams (io.StringIO), which have no .buffer.
The hook read sys.stdin.buffer and wrote sys.stdout.buffer, so in-process it raised, caught it and printed nothing:
on the Mac, 0 of 30 prompts gave the same output (5 Nov).
 T1 its own process, with a board present, prints the board (the check is not passed by two empty outputs)
 T2 in-process with text streams it prints the same text
 T3 a non-ASCII board line survives the text path whole
With --old, the hook before this change must print nothing in-process (N1). Every run copies the hook and its sibling
once_per_context.py into a throwaway folder under a throwaway HOME, so no live board or state file is read or written.
"""
import io
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
MOD = os.path.abspath(ARGS[ARGS.index("--module") + 1]) if "--module" in ARGS else os.path.join(HERE, "owner_board_hook.py")
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
BOARD = ("# board\n\ncontrol: run every task; Raül's note — keep going\n\n"
         "## OWNER MESSAGE\nFinish the parity port before the films.\n")
fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def stage(hook, td):
    home = os.path.join(td, "home")
    b = os.path.join(home, "brain-repo", "12_cognitive_architecture", "scheduled_tasks")
    os.makedirs(b)
    with open(os.path.join(b, "BOARD.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(BOARD)
    code = os.path.join(td, "code")
    os.makedirs(code)
    shutil.copy2(hook, os.path.join(code, "owner_board_hook.py"))
    sib = os.path.join(os.path.dirname(hook), "once_per_context.py")
    if os.path.exists(sib):
        shutil.copy2(sib, code)
    return home, os.path.join(code, "owner_board_hook.py")


def own_process(hook):
    td = tempfile.mkdtemp(prefix="owner_board_own_")
    try:
        home, h = stage(hook, td)
        env = dict(os.environ, HOME=home, USERPROFILE=home, PYTHONIOENCODING="utf-8")
        env.pop("CODEX_THREAD_ID", None)
        p = subprocess.run([sys.executable, "-s", h, "--hook"], input=b'{"session_id": "own-1", "hook_event_name": '
                           b'"UserPromptSubmit"}', env=env, capture_output=True, timeout=60)
        return p.stdout.decode("utf-8", "replace")
    finally:
        shutil.rmtree(td, ignore_errors=True)


IN_PROC = r'''
import io, os, sys
h = sys.argv[1]
sys.argv = [h, "--hook"]
sys.path.insert(0, os.path.dirname(h))
sys.stdin = io.StringIO('{"session_id": "inp-1", "hook_event_name": "UserPromptSubmit"}')
real, sys.stdout = sys.stdout, io.StringIO()
import runpy
try:
    g = runpy.run_path(h, run_name="owner_board_hook")
    g["main"]()
finally:
    out, sys.stdout = sys.stdout.getvalue(), real
sys.stdout.buffer.write(out.encode("utf-8"))
'''


def in_process(hook):
    """The dispatcher's shape: the module loaded, then main() called with StringIO stdin and stdout. It runs in a
    child so the throwaway HOME is the one expanduser reads when the module sets BOARD."""
    td = tempfile.mkdtemp(prefix="owner_board_inp_")
    try:
        home, h = stage(hook, td)
        env = dict(os.environ, HOME=home, USERPROFILE=home)
        env.pop("CODEX_THREAD_ID", None)
        p = subprocess.run([sys.executable, "-s", "-c", IN_PROC, h], env=env, capture_output=True, timeout=60)
        return p.stdout.decode("utf-8", "replace")
    finally:
        shutil.rmtree(td, ignore_errors=True)


def main():
    a = own_process(MOD)
    check("Finish the parity port" in a, "T1 its own process prints the board (%d chars)" % len(a))
    b = in_process(MOD)
    check(b == a, "T2 in-process with text streams prints the same text (%d vs %d chars)" % (len(b), len(a)))
    check("Raül's note — keep going" in b, "T3 the non-ASCII control line survives the text path")
    if OLD:
        o = in_process(OLD)
        check(o == "", "N1 the hook before this change prints nothing in-process (%d chars)" % len(o))
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
