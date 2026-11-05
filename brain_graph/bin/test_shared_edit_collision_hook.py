"""test_shared_edit_collision_hook.py - the in-process equivalence the dispatcher's allowlist asks for (28 Sep,
claude-825b1305): shared_edit_collision_hook.py run through hook_dispatch.run_in_process and run as its own process give
the same stdout and exit code on silent and loud payloads, each from the same starting ledger.

    python test_shared_edit_collision_hook.py
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.expanduser("~/.claude/dreaming"))
import hook_dispatch  # noqa: E402

HOOK = os.path.expanduser("~/.claude/dreaming/shared_edit_collision_hook.py")
MEM = os.path.join(os.path.expanduser("~"), ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")), "memory", "x_equiv_2026_09_28.md")
NOW = 1_800_000_000.0


def payload(tool, sid, path):
    return json.dumps({"tool_name": tool, "session_id": sid + "-0000", "tool_input": {"file_path": path}})


CASES = [  # (name, planted ledger lines, payload, expect loud)
    ("silent: first shared edit", [], payload("Edit", "aaaaaaaa", MEM), False),
    ("silent: outside the roots", [], payload("Write", "aaaaaaaa", os.path.expanduser("~/Downloads/x.md")), False),
    ("silent: a Read", [], payload("Read", "aaaaaaaa", MEM), False),
    ("loud: another session 4 min ago",
     [{"t": NOW - 240, "s": "bbbbbbbb", "p": os.path.normcase(MEM), "tool": "Edit"}], payload("Edit", "aaaaaaaa", MEM), True),
    ("loud: another session 29 min ago, Write",
     [{"t": NOW - 1740, "s": "cccccccc", "p": os.path.normcase(MEM), "tool": "Edit"}], payload("Write", "aaaaaaaa", MEM), True),
]


def main():
    d = tempfile.mkdtemp()
    ok = True
    for name, planted, pl, loud in CASES:
        led0 = os.path.join(d, "start.jsonl")
        with open(led0, "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in planted))
        outs = []
        for mode in ("process", "in-process"):
            led = os.path.join(d, "ledger_%s.jsonl" % mode)
            shutil.copyfile(led0, led)
            env = dict(os.environ, SHARED_EDIT_LEDGER=led, SHARED_EDIT_NOW=str(NOW))
            if mode == "process":
                r = subprocess.run([sys.executable, HOOK], input=pl, capture_output=True, text=True, env=env,
                                   creationflags=0x08000000)
                outs.append((r.returncode, r.stdout.strip(), open(led, encoding="utf-8").read()))
            else:
                saved = dict(os.environ)
                os.environ.update(SHARED_EDIT_LEDGER=led, SHARED_EDIT_NOW=str(NOW))
                try:
                    rc, so, se = hook_dispatch.run_in_process(HOOK, pl)
                finally:
                    os.environ.clear()
                    os.environ.update(saved)
                outs.append((rc, so.strip(), open(led, encoding="utf-8").read()))
        same = outs[0] == outs[1]
        is_loud = "[collision]" in outs[0][1]
        good = same and is_loud == loud and outs[0][0] == 0
        ok = ok and good
        print(("PASS " if good else "FAIL ") + name + " | same=%s loud=%s rc=%s" % (same, is_loud, outs[0][0]))
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
