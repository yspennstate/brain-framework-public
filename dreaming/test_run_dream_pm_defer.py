"""run_dream defers its project-memory step only when the hourly job's last run is clean and recent, or running now.

Builds a throwaway runner_health table (the columns hourly_runner.py writes, utc_now()'s isoformat) and asks
_pm_recent() about each case. Clean under an hour, or running under 45 min: defer. Clean two hours ago, an
incomplete or failed run, a deferred run, a stale 'running' row, no row, no database: run the step.
Run: python3 test_run_dream_pm_defer.py (exit 0 = pass)
"""
import importlib
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def iso(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


CASES = [  # (status, started min ago, completed min ago, want deferral)
    ("ok", 20, 10, True),
    ("ok", 130, 120, False),
    ("incomplete", 20, 10, False),
    ("error", 20, 10, False),
    ("deferred", 10, 10, False),
    ("running", 5, None, True),
    ("running", 90, None, False),
]


def main():
    try:
        import fcntl  # noqa: F401  (run_dream.py is the POSIX dream runner and imports it at the top)
    except ImportError:
        print("SKIP run_dream.py needs fcntl, which this platform lacks")
        return 0
    tmp = tempfile.mkdtemp(prefix="pmdefer_")
    fails = []
    try:
        db = os.path.join(tmp, "project_memory.sqlite3")
        os.environ["PROJECT_MEMORY_DB"] = db
        rd = importlib.import_module("run_dream")
        if not hasattr(rd, "_pm_recent"):
            print("FAIL run_dream has no _pm_recent")
            return 1
        if rd._pm_recent() != "":
            fails.append("no database should mean run the step")
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE runner_health(runner_id TEXT PRIMARY KEY, attempt_started_at TEXT, "
                  "completed_at TEXT, status TEXT, error_code TEXT)")
        c.commit()
        if rd._pm_recent() != "":
            fails.append("no row should mean run the step")
        for status, started, done, want in CASES:
            c.execute("DELETE FROM runner_health")
            c.execute("INSERT INTO runner_health VALUES('hourly',?,?,?,NULL)",
                      (iso(started), iso(done) if done is not None else None, status))
            c.commit()
            got = bool(rd._pm_recent())
            if got != want:
                fails.append("%s started %s done %s: deferred=%s, want %s" % (status, started, done, got, want))
        c.close()
        src = open(os.path.join(HERE, "run_dream.py"), encoding="utf-8").read()
        if 'elif name == "project-memory hourly" and _pm_recent():' not in src:
            fails.append("the tail loop does not consult _pm_recent")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for f in fails:
        print("FAIL", f)
    print("PASS" if not fails else "FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
