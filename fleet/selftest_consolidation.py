#!/usr/bin/env python3
"""selftest_consolidation.py - prove the load-bearing invariants of the 2026-07-12 fleet
consolidation tools still hold. Static source checks (catch a regression that reintroduces a
forbidden pattern - the way the launcher selftest.ps1 does) plus dynamic checks against throwaway
fixtures. Opens no windows, spams no agents, pushes nothing, touches no real repo or the live mesh
DB. Run after editing console.py / push_all.py / roster_A.py / fleet_learn.py / mesh_mail scripts.

  python selftest_consolidation.py
"""
import os, sys, subprocess, sqlite3, tempfile, shutil

FLEET = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SCAN = os.path.join(HOME, ".claude", "dreaming", "scan_secrets.py")
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def src(*parts):
    return open(os.path.join(FLEET, *parts), encoding="utf-8").read()


def static_checks():
    pa = src("push_all.py")
    check("push_all: never force-pushes (no \"--force\" git arg)", '"--force"' not in pa)
    check("push_all: worktree-safe repo check", "--is-inside-work-tree" in pa)
    check("push_all: secret-scan gate present", "SECRET-HOLD" in pa and "scan_files" in pa)

    co = src("console.py")
    check("console: own identity owner-console (no hub impersonation)", 'ME = "owner-console"' in co)
    check("console: no invalid --kind note", '"--kind", "note"' not in co)

    fl = src("fleet_learn.py")
    check("fleet_learn: ledger guarded on send success (no silenced alarm)", 'if "message_id" in resp' in fl)
    check("fleet_learn: age cutoff so an unseeded ledger cannot spam ancient lessons", "7 * 86400" in fl)
    check("fleet_learn: no invalid --kind note", '"--kind", "note"' not in fl)

    ro = src("roster_A.py")
    check("roster: archive guards DEAD + disabled + >2h quiet", '!= "DEAD"' in ro and 'e.get("disabled")' in ro and "7200" in ro)
    check("roster: plain-started sessions held out (unclassified)", "unclassified" in ro and "ALIVE*" in ro)

    tr = src("mesh_mail", "triage.py")
    check("triage: opens the mesh DB read-only (mode=ro)", "mode=ro" in tr)

    ak = src("mesh_mail", "ack_sweep.py")
    check("ack_sweep: handoffs separated and never auto-acked", '== "handoff"' in ak and "for m in plain" in ak)


def dynamic_checks():
    tmp = tempfile.mkdtemp(prefix="fleet_selftest_")
    try:
        real = os.path.join(tmp, "real.txt")
        with open(real, "w") as f:
            # built from parts so THIS source file carries no scannable AKIA pattern (it is fabricated)
            f.write("aws_access_key_id = " + "AKIA" + "3K7QW9ZL2MNP4RTX\n")
        r = subprocess.run(["python", SCAN, real], capture_output=True, text=True, creationflags=NO_WIN)
        check("scan_secrets FIRES on a real-shaped AKIA key", r.returncode != 0, "exit=%d" % r.returncode)

        fake = os.path.join(tmp, "fake.txt")
        with open(fake, "w") as f:
            f.write("doc_example = " + "AKIA" + "IOSFODNN7EXAMPLE\n")
        r2 = subprocess.run(["python", SCAN, fake], capture_output=True, text=True, creationflags=NO_WIN)
        check("scan_secrets SKIPS the AWS doc placeholder", r2.returncode == 0, "exit=%d" % r2.returncode)

        dbp = os.path.join(tmp, "t.sqlite3")
        cx = sqlite3.connect(dbp)
        cx.execute("CREATE TABLE t(a)")
        cx.commit()
        cx.close()
        roc = sqlite3.connect("file:" + dbp.replace("\\", "/") + "?mode=ro", uri=True)
        try:
            roc.execute("INSERT INTO t VALUES(1)")
            roc.commit()
            check("triage-style mode=ro connection refuses writes", False, "write SUCCEEDED (bad)")
        except sqlite3.OperationalError:
            check("triage-style mode=ro connection refuses writes", True)
        finally:
            roc.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    tools = ["console.py", "push_all.py", "roster_A.py", "fleet_learn.py",
             os.path.join("mesh_mail", "triage.py"), os.path.join("mesh_mail", "ack_sweep.py")]
    rc = subprocess.run([sys.executable, "-m", "py_compile"] + [os.path.join(FLEET, t) for t in tools],
                        capture_output=True, text=True, creationflags=NO_WIN)
    check("all six consolidation tools compile", rc.returncode == 0, (rc.stderr or "")[:100])

    # the recovery-critical .ps1 must parse (a broken bringup = no crash recovery). Audit finding: the
    # suite wasn't parse-checking either .ps1.
    ps1 = os.path.join(FLEET, "bringup_A.ps1")
    pc = subprocess.run(["powershell", "-NoProfile", "-Command",
                         "$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('" + ps1 +
                         "',[ref]$null,[ref]$e);if($e -and $e.Count){exit 1}else{exit 0}"],
                        capture_output=True, text=True, creationflags=NO_WIN)
    check("bringup_A.ps1 parses (PowerShell)", pc.returncode == 0, (pc.stderr or "")[:80])


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    static_checks()
    dynamic_checks()
    npass = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, (" - " + detail) if (detail and not ok) else ""))
    print("\n%d/%d checks passed" % (npass, len(results)))
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
