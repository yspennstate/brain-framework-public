#!/usr/bin/env python3
"""Health check for the High method - one command that confirms the whole delegation pipeline
still works: detector, ledger, router, dream hook, /high command, and the hourly task. Run it
after any change, or from the dream, to catch drift. Exit 0 = all pass."""
from __future__ import annotations
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _check(name, fn):
    try:
        fn()
        print(f"  PASS  {name}")
        return True
    except Exception as e:
        print(f"  FAIL  {name}: {e}")
        return False


def _detector():
    import verify_model
    assert verify_model.selftest(), "verify_model.selftest failed"


def _router():
    import route
    route.rebuild()
    pol = json.load(open(os.path.join(HERE, "routing_policy.json"), encoding="utf-8"))
    assert pol["policy"], "empty policy"


def _ledger():
    import ledger
    ledger.read_records()  # must parse without error


def _dream_hook():
    txt = open(r"{{USERPROFILE}}\.claude\dreaming\dream.py", encoding="utf-8").read()
    assert "dream_delegation_review" in txt, "delegation review not wired into dream.py"


def _command():
    assert os.path.exists(r"{{USERPROFILE}}\.claude\commands\high.md"), "/high command missing"


def _hourly_task():
    out = subprocess.run(["schtasks", "/Query", "/TN", "HighMethodBroadcast"],
                         capture_output=True, text=True).stdout
    assert "HighMethodBroadcast" in out, "hourly broadcast task not registered"


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    print("High method health check:")
    results = [
        _check("detector (verify_model selftest)", _detector),
        _check("router rebuild + policy valid", _router),
        _check("ledger readable", _ledger),
        _check("dream hook wired", _dream_hook),
        _check("/high command present", _command),
        _check("hourly broadcast task registered", _hourly_task),
    ]
    ok = all(results)
    print("HIGH SELFTEST:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
