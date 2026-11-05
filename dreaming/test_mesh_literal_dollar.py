"""danger_mode refuses a mesh claim, assert or release whose resource would reach the mesh as a literal "$name".

    python test_mesh_literal_dollar.py            the live danger_mode.py
    DANGER_MODE_UNDER_TEST=<path> python ...      another copy (a candidate, or the copy before the guard)

The Bash tool turns each double backslash into one before bash runs, and bash reads backslash-dollar inside double
quotes (or bare) as an escaped dollar; inside single quotes nothing expands. So a claim written as
"C:\\...\\memory\\$f" leases the literal text '...memory$f' and covers no file, with exit code 0. That happened on 14
Sep, on 27 Sep and on 30 Sep. The commands below are the text the hook receives, before the Bash tool halves anything.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get("DANGER_MODE_UNDER_TEST") or os.path.join(HERE, "danger_mode.py")
MESH = "python ~/ai-memories-and-functionality/12_cognitive_architecture/agent_mesh/agent_mesh.py"

REFUSE = {
    # the 30 Sep command, as typed: two backslashes before the variable
    "P1 the 30 Sep claim": MESH + r' claim --agent a --resource "D:\\brain\\memory\\$f" --mode write --purpose p',
    "P2 one backslash": MESH + r' claim --agent a --resource "D:\brain\memory\$f" --purpose p',
    "P3 single quotes never expand": MESH + r" claim --agent a --resource 'D:\brain\memory\$f' --purpose p",
    "P4 braces, equals form": MESH + r' claim --agent a --resource="D:\\brain\\${name}.py" --purpose p',
    "P5 assert": MESH + r' assert --agent a --resource "D:\\brain\\dreaming\\$n.py"',
    "P6 release": MESH + r' release --agent a --resource "D:\\brain\\dreaming\\$n.py"',
    "P7 --json first, quoted script path": r'python "C:/x/agent_mesh.py" --json claim --agent a --resource "C:\\x\\$f"',
    "P8 bare, in a loop": "for f in a b; do " + MESH + r" claim --agent a --resource C:\\x\\$f --purpose p; done",
    "P9 second resource of two": MESH + r' claim --agent a --resource "C:/ok/file.py" --resource "C:\\x\\$f"',
    # 2 Oct: the path went into a variable first, then the variable was the resource
    "P10 assigned in the command, 2 Oct": (r'for S in a b; do W="D:\\brain\\memory\\$S.md"; ' + MESH +
                                            r' claim --agent a --resource "$W" --mode write --purpose p; done'),
    "P11 braces both sides, assert": r'R="C:\\x\\${f}.py"; ' + MESH + r' assert --agent a --resource "${R}"',
    "P12 single-quoted assignment": r"W='C:\x\$f.md'; " + MESH + r' release --agent a --resource "$W"',
}
PASS = {
    "N1 forward slashes": MESH + ' claim --agent a --resource "D:/brain/memory/$f" --purpose p',
    "N2 the whole path in the variable": MESH + ' claim --agent a --resource "$RES" --purpose p',
    "N3 a logical resource": MESH + ' claim --agent a --resource "memory:$slug" --purpose p',
    "N4 four backslashes leave one and expand": MESH + r' claim --agent a --resource "C:\\\\x\\\\$f" --purpose p',
    "N5 not a mesh command": r'echo "costs \$5 and \$HOME"',
    "N6 a mesh command that takes no resource": MESH + r' pulse --agent a --task "path C:\\x\\$f"',
    "N7 no dollar": MESH + r" claim --agent a --resource 'D:\brain\file.py' --purpose p",
    "N8 a dollar that names no variable": MESH + r" claim --agent a --resource 'C:\share\C$\x' --purpose p",
    "N9 command substitution": MESH + r' claim --agent a --resource "C:/x/$(date +%F).log" --purpose p',
    "N10 assigned with forward slashes": r'W="D:/brain/memory/$S.md"; ' + MESH + r' claim --agent a --resource "$W"',
    "N11 assigned with no variable inside": (r'W="D:\\brain\\memory\\file.md"; ' + MESH +
                                             r' claim --agent a --resource "$W" --purpose p'),
    "N12 the bad assignment is another variable": (r'X="C:\\x\\$f"; W="C:/ok/$f"; ' + MESH +
                                                   r' claim --agent a --resource "$W" --purpose p'),
    "N13 --resource=value is not an assignment": MESH + r' claim --agent a --resource="C:/ok/$f.py" --purpose p',
}


def load():
    spec = importlib.util.spec_from_file_location("danger_mode_under_test", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def hook(cmd):
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": ""}
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("MESH_DOLLAR_GUARD", None)
    p = subprocess.run([sys.executable, PATH], input=json.dumps(payload), capture_output=True, text=True,
                       encoding="utf-8", env=env, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = (p.stdout or "").strip()
    try:
        hso = json.loads(out).get("hookSpecificOutput", {}) if out else {}
    except ValueError:
        hso = {}
    return hso.get("permissionDecision"), hso.get("permissionDecisionReason") or ""


def main():
    mod = load()
    has = hasattr(mod, "_mesh_literal_dollar")
    fails = 0
    for name, cmd in REFUSE.items():
        direct = bool(has and mod._mesh_literal_dollar(cmd))
        decision, reason = hook(cmd)
        ok = direct and decision == "deny" and "literal" in reason
        fails += not ok
        print("%s  %-40s direct=%s hook=%s" % ("PASS" if ok else "FAIL", name, direct, decision))
    for name, cmd in PASS.items():
        direct = bool(has and mod._mesh_literal_dollar(cmd))
        decision, _ = hook(cmd)
        ok = not direct and decision != "deny"
        fails += not ok
        print("%s  %-40s direct=%s hook=%s" % ("PASS" if ok else "FAIL", name, direct, decision))
    print("%d of %d checks failed" % (fails, len(REFUSE) + len(PASS)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
