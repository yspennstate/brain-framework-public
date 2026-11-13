"""test_flow_sentinel.py - the scheduled-flow audit replayed from a recorded task list in a temporary folder: a task
whose script's log ends on a repeated failure is flagged with a short category and none of the log's own text, a
success line clears earlier failures, a run older than three cadences is stale, a nonzero result on a running task is
running status not failure, and a folder named by FLOW_SENTINEL_FORBIDDEN is never opened."""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def write(p, t):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(t)


def task(name, ps1, last_run, result=0, state="Ready", interval="PT15M"):
    return {"name": name, "path": "\\", "state": state, "last_run": last_run, "next_run": None,
            "last_result": result,
            "actions": [{"Execute": "powershell.exe", "Arguments": '-File "%s"' % ps1, "WorkingDirectory": None}],
            "triggers": [{"interval": interval, "days": None, "weeks": None, "start": "2026-01-01T00:00:00",
                          "class": "MSFT_TaskTimeTrigger", "enabled": True}]}


def test_replay():
    d = tempfile.mkdtemp(prefix="flow_sentinel_test_")
    try:
        a, b, c = (os.path.join(d, x) for x in ("a", "b", "secretdir"))
        write(os.path.join(a, "run.ps1"), '& python "%s"\n' % os.path.join(a, "job.py"))
        write(os.path.join(a, "job.py"), 'LOG = "%s"\n' % os.path.join(a, "job.log").replace("\\", "/"))
        write(os.path.join(a, "job.log"), "2026-01-01 00:00:00 published\n" +
              "".join("2026-01-01 00:%02d:00 ERROR: private words about a customer\n" % i for i in range(1, 5)))
        write(os.path.join(b, "run.ps1"), '& python "%s"\n' % os.path.join(b, "job.py"))
        write(os.path.join(b, "job.py"), 'LOG = "%s"\n' % os.path.join(b, "job.log").replace("\\", "/"))
        write(os.path.join(b, "job.log"), "2026-01-01 00:00:00 failed\n2026-01-01 00:01:00 published\n")
        write(os.path.join(c, "run.ps1"), '& python "%s"\n' % os.path.join(c, "job.py"))
        write(os.path.join(c, "job.py"), 'LOG = "%s"\n' % os.path.join(c, "job.log").replace("\\", "/"))
        write(os.path.join(c, "job.log"), "ERROR: never read\n")
        tasks = [task("BrainA", os.path.join(a, "run.ps1"), "2026-01-01T00:50:00+00:00"),
                 task("BrainB", os.path.join(b, "run.ps1"), "2026-01-01T00:10:00+00:00"),
                 task("BrainC", os.path.join(c, "run.ps1"), "2026-01-01T00:55:00+00:00", result=267009, state="Running")]
        tf = os.path.join(d, "tasks.json")
        with open(tf, "w", encoding="utf-8") as fh:
            json.dump(tasks, fh)
        out = os.path.join(d, "out")
        env = dict(os.environ, FLOW_SENTINEL_FORBIDDEN=r"secretdir")
        r = subprocess.run([sys.executable, "-s", os.path.join(HERE, "flow_sentinel.py"), "--output-dir", out,
                            "--tasks-file", tf, "--now", "2026-01-01T01:00:00+00:00"],
                           capture_output=True, encoding="utf-8", env=env)
        assert r.returncode == 0, r.stderr
        with open(os.path.join(out, "sentinel.json"), encoding="utf-8") as fh:
            rep = json.load(fh)
        kinds = {(f["name"], f["kind"]): f for f in rep["findings"]}
        fa = kinds[("BrainA", "log_failure")]
        assert fa["repeating_failure"] == "error" and fa["repeats"] == 4, fa
        assert "customer" not in json.dumps(rep)                  # log text never copied
        assert ("BrainB", "log_failure") not in kinds              # a later success clears it
        assert kinds[("BrainB", "stale_run")]["classification"] == "failure"
        assert kinds[("BrainC", "nonzero_result")]["classification"] == "running_status"
        cov = {x["name"]: x for x in rep["coverage"]}
        assert cov["BrainC"]["logs"] == [], cov["BrainC"]          # forbidden folder never opened
        assert "flows: BrainA, BrainB" in r.stdout, r.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    test_replay()
    print("ok test_replay")
