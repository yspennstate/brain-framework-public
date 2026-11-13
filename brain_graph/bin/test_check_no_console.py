"""Controls for check_no_console.ps1 on two throwaway scheduled tasks of the current user.

    python test_check_no_console.py

Windows only; elsewhere it says so and exits 0. Registers two tasks (no trigger, removed again at the end), each running
a hidden launcher from a temporary folder, and runs the checker on each:
 P  a launcher whose only child is pythonw.exe (no console subsystem): no console host, exit 0
 N  a launcher that starts cmd.exe with window style 0: a hidden console still has a host, so exit 1 (the check
    can fail) - nothing is ever shown on screen
 M  a -Match that names no launcher: exit 1, "never seen"
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NOWIN = 0x08000000 if os.name == "nt" else 0
PS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + detail) if detail and not ok else ""), flush=True)
    if not ok:
        FAILS.append(name)


def ps(cmd, timeout=300):
    return subprocess.run([PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                          capture_output=True, text=True, creationflags=NOWIN, timeout=timeout)


def q(s):
    return "'" + s.replace("'", "''") + "'"


def main():
    if os.name != "nt":
        print("SKIP - scheduled tasks and console hosts are Windows")
        return 0
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pyw):
        print("SKIP - no pythonw.exe beside " + sys.executable)
        return 0
    tmp = tempfile.mkdtemp(prefix="noconsole_")
    tag = "BrainNoConsoleTest_%d" % os.getpid()
    tasks = []
    try:
        nap = os.path.join(tmp, "nap.py")
        with open(nap, "w", encoding="utf-8") as fh:
            fh.write("import time\ntime.sleep(15)\n")
        launchers = {
            "pos": 'CreateObject("WScript.Shell").Run """%s"" ""%s""", 0, True' % (pyw, nap),
            "neg": 'CreateObject("WScript.Shell").Run "cmd /c ping -n 16 127.0.0.1 >nul", 0, True',
        }
        for k, body in launchers.items():
            vbs = os.path.join(tmp, "launch_%s_%s.vbs" % (k, tag))
            with open(vbs, "w", encoding="ascii") as fh:
                fh.write(body + "\r\n")
            name = "%s_%s" % (tag, k)
            r = ps("Register-ScheduledTask -TaskName %s -Action (New-ScheduledTaskAction -Execute 'wscript.exe' "
                   "-Argument %s) -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
                   "-DontStopIfGoingOnBatteries) -Force | Out-Null" % (q(name), q('//B "%s"' % vbs)))
            if r.returncode:
                print("SKIP - could not register a task: " + (r.stderr or r.stdout)[-200:])
                return 0
            tasks.append(name)
        checker = os.path.join(HERE, "check_no_console.ps1")

        def run(k, match, secs="180"):
            return subprocess.run([PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", checker,
                                   "-TaskName", "%s_%s" % (tag, k), "-Match", match, "-Seconds", secs],
                                  capture_output=True, text=True, creationflags=NOWIN, timeout=400)

        r = run("pos", "launch_pos_" + tag)
        check("P a pythonw-only run has no console host", r.returncode == 0 and "console hosts attached: 0" in r.stdout
              and "pythonw.exe" in r.stdout, (r.stdout + r.stderr)[-300:])
        r = run("neg", "launch_neg_" + tag)
        check("N a hidden cmd run is caught by its console host", r.returncode == 1 and "conhost.exe" in r.stdout.lower()
              or (r.returncode == 1 and "openconsole.exe" in r.stdout.lower()), (r.stdout + r.stderr)[-300:])
        r = run("pos", "no_such_launcher_" + tag, "60")
        check("M a -Match that names no launcher fails", r.returncode == 1 and "never seen" in r.stdout,
              (r.stdout + r.stderr)[-300:])
    finally:
        for name in tasks:
            ps("Unregister-ScheduledTask -TaskName %s -Confirm:$false" % q(name), timeout=120)
        shutil.rmtree(tmp, ignore_errors=True)
    print("%d of 3 controls pass" % (3 - len(FAILS)), flush=True)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
