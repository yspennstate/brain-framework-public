"""Controls for the hidden launchers: each one runs under a fake profile folder and reaches its own target there.

    python test_launchers_resolve_home.py [OLD_SIDECAR_LAUNCHER]

Windows only (cscript); elsewhere it says so and exits 0. A temporary folder stands in for %USERPROFILE%, holding fake
targets that record their arguments and working folder; nothing outside it is read or written, no model is called.
 D1 doc_expand_launcher.vbs runs doc_expand.py --new from the brain_graph bin folder (BRAIN_PYTHON names the python)
 D2 it writes to the store bm25.py reads: --out ~/.claude/brain_graph/doc_expansion/expansions.jsonl
 D3 the store folder is created when missing and the job's output lands in expand.log there
 S1 sidecar_refresh_launcher.vbs runs sidecar_refresh.ps1 from ~/.claude/dreaming
 W  both launchers start their job hidden (window style 0) and wait for it
 N  (with OLD_SIDECAR_LAUNCHER) a launcher that sets CurrentDirectory to a literal %USERPROFILE% path never reaches
    its target (the check can fail)
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NOWIN = 0x08000000 if os.name == "nt" else 0
FAILS = []
SIDECAR = None


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + detail) if detail and not ok else ""), flush=True)
    if not ok:
        FAILS.append(name)


def run_vbs(path, home):
    env = dict(os.environ, USERPROFILE=home, BRAIN_PYTHON=sys.executable)
    return subprocess.run(["cscript", "//B", "//Nologo", path], env=env, capture_output=True, text=True,
                          creationflags=NOWIN, timeout=120)


def norm(p):
    return os.path.normcase(os.path.normpath(p))


def find(name, *up):
    """beside this file, else at its place in the tree (brain_graph/bin/../../<up>), as in the repo and an install"""
    p = os.path.join(HERE, name)
    return p if os.path.exists(p) else os.path.normpath(os.path.join(HERE, "..", "..", *up, name))


def main():
    if os.name != "nt":
        print("SKIP - the launchers are Windows Script Host files")
        return 0
    global SIDECAR
    SIDECAR = find("sidecar_refresh_launcher.vbs", "dreaming", "sidecar_refresh")
    old = sys.argv[1] if len(sys.argv) > 1 else None
    tmp = tempfile.mkdtemp(prefix="launch_home_")
    try:
        home = os.path.join(tmp, "home")
        bin_dir = os.path.join(home, ".claude", "brain_graph", "bin")
        dream = os.path.join(home, ".claude", "dreaming")
        os.makedirs(bin_dir)
        os.makedirs(os.path.join(dream, "sidecar_refresh"))
        mark_d = os.path.join(tmp, "doc_expand.json")
        mark_s = os.path.join(tmp, "sidecar.txt")
        with open(os.path.join(bin_dir, "doc_expand.py"), "w", encoding="utf-8") as fh:
            fh.write("import json, os, sys\nprint('fake expansion ran')\n"
                     "json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd()}, open(%r, 'w'))\n" % mark_d)
        with open(os.path.join(dream, "sidecar_refresh", "sidecar_refresh.ps1"), "w", encoding="ascii") as fh:
            fh.write("(Get-Location).Path | Out-File -Encoding ascii '%s'\n" % mark_s.replace("'", "''"))

        r = run_vbs(find("doc_expand_launcher.vbs"), home)
        d = json.load(open(mark_d, encoding="utf-8")) if os.path.exists(mark_d) else {}
        argv = d.get("argv", [])
        check("D1 the expansion job runs --new from the bin folder", "--new" in argv and norm(d.get("cwd", "")) ==
              norm(bin_dir), "rc %s %s %s" % (r.returncode, r.stderr[-200:], json.dumps(d)))
        want = os.path.join(home, ".claude", "brain_graph", "doc_expansion", "expansions.jsonl")
        out = argv[argv.index("--out") + 1] if "--out" in argv[:-1] else ""
        check("D2 it writes to the store the word index reads", norm(out) == norm(want), out)
        log = os.path.join(home, ".claude", "brain_graph", "doc_expansion", "expand.log")
        check("D3 the store folder was made and the job's output is in expand.log",
              os.path.exists(log) and "fake expansion ran" in open(log, encoding="utf-8", errors="replace").read())

        r = run_vbs(SIDECAR, home)
        got = open(mark_s, encoding="ascii").read().strip() if os.path.exists(mark_s) else ""
        check("S1 the sidecar refresh runs from the dreaming folder", norm(got) == norm(dream),
              "rc %s %s got=%r" % (r.returncode, r.stderr[-200:], got))

        hidden = all(re.search(r"\.Run\(.*,\s*0,\s*True\)", open(f, encoding="utf-8").read(), re.S)
                     for f in (find("doc_expand_launcher.vbs"), SIDECAR))
        check("W both start their job hidden and wait for it", hidden)

        if old:
            os.remove(mark_s)
            r = run_vbs(old, home)
            check("N a literal %USERPROFILE% CurrentDirectory never reaches its target", not os.path.exists(mark_s),
                  "rc %s" % r.returncode)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    n = 5 + (1 if old else 0)
    print("%d of %d controls pass" % (n - len(FAILS), n), flush=True)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
