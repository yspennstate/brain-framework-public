# -*- coding: utf-8 -*-
"""Controls both ways for danger_mode._python_dash (claude-5693aca3, 20 Oct 2026).
    python -s test_python_dash_guard.py"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import danger_mode as dm  # noqa: E402

FIRE = [
    "python - <<'EOF'\nprint(1)\nEOF",
    "python -X utf8 - <<EOF\nEOF",
    "cd x && python -I - <<'EOF' 2>/dev/null || true",
    "python - < /dev/null",
    "python - 2>/dev/null; sed -i s/a/b/ f",
    "C:/Python314/python.exe -X utf8 -",
    "FOO=1 python3 - <<EOF\nEOF",
    "timeout 60 python -s - <<EOF\nEOF",
    "py - < x",
    "PY=C:/x/python.exe; $PY - <<'E' 2>/dev/null || true\nE",
    "PY=python; \"$PY\" -X utf8 -",
    "PYTHON=C:/x/python.exe\n${PYTHON} - < x",
]
SILENT = [
    "python -X utf8 tools/x.py",
    "python -c \"print('-')\"",
    "python -m pip install -",
    "python tools/a.py - b",
    "echo python - is bad",
    "grep -n 'python -' file.md",
    "python -X utf8 -c 'import sys' -",
    "cat x | python -s script.py -",
    "ls -la -",
    "PY=C:/x/python.exe; $PY script.py -",
    "PY=C:/x/python.exe; $PY -c 'print(1)'",
    "X=1; $X - y",
    "$PY - x",
]


def main():
    bad = 0
    for c in FIRE:
        if not dm._python_dash(c):
            bad += 1
            print("MISS  ", repr(c))
    for c in SILENT:
        if dm._python_dash(c):
            bad += 1
            print("FALSE ", repr(c))
    # end to end through the hook's stdin
    ev = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": FIRE[0]}}
    out = subprocess.run([sys.executable, "-s", dm.__file__], input=json.dumps(ev), capture_output=True, text=True).stdout
    if '"deny"' not in out:
        bad += 1
        print("E2E no deny:", out[:200])
    ev["tool_input"]["command"] = SILENT[0]
    out = subprocess.run([sys.executable, "-s", dm.__file__], input=json.dumps(ev), capture_output=True, text=True).stdout
    if '"deny"' in out:
        bad += 1
        print("E2E false deny:", out[:200])
    print("%d fire, %d silent, %d failures" % (len(FIRE), len(SILENT), bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
