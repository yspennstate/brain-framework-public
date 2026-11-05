import os, subprocess, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import running_code_git as R  # noqa: E402


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=cwd, check=True,
                   capture_output=True)


def _repo(tmp_path):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(remote), str(work))
    for name in ("clean.py", "edited.py"):
        (work / name).write_text("print(1)\n")
    _git(work, "add", "clean.py", "edited.py")
    _git(work, "commit", "-q", "-m", "one")
    _git(work, "push", "-q", "origin", "HEAD")
    return work


def test_script_of():
    assert R.script_of(["/usr/bin/python3", "-s", "/a/b.py", "--x"]) == "/a/b.py"
    assert R.script_of(["/Library/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python",
                        "-s", "/a/gov.py"]) == "/a/gov.py"
    assert R.script_of(["python3", "-X", "utf8", "rel.py"]) == "rel.py"
    assert R.script_of(["python3", "-c", "print(1)"]) is None
    assert R.script_of(["python3", "-m", "http.server"]) is None
    assert R.script_of(["/bin/zsh", "x.py"]) is None


def test_states(tmp_path):
    work = _repo(tmp_path)
    (work / "edited.py").write_text("print(2)\n")
    (work / "new.py").write_text("print(3)\n")
    assert R.git_state(str(work / "clean.py"))[1] == "clean"
    assert R.git_state(str(work / "edited.py"))[1] == "modified"
    assert R.git_state(str(work / "new.py"))[1] == "untracked"
    outside = tmp_path / "loose.py"
    outside.write_text("x = 1\n")
    assert R.git_state(str(outside)) is None


def test_unpushed(tmp_path):
    work = _repo(tmp_path)
    (work / "clean.py").write_text("print(5)\n")
    _git(work, "commit", "-q", "-am", "local only")
    assert R.git_state(str(work / "clean.py"))[1] == "unpushed"


def test_survey_resolves_relative_scripts(tmp_path):
    work = _repo(tmp_path)
    (work / "edited.py").write_text("print(2)\n")
    procs = [(11, ["python3", "edited.py"]), (12, ["python3", str(work / "clean.py")]), (13, ["python3", "-c", "1"])]
    rows = R.survey(procs, cwd_of=lambda pid: str(work))
    got = {(r["pid"], r["state"]) for r in rows}
    assert got == {(11, "modified"), (12, "clean")}


def test_modified_but_on_remote(tmp_path):
    work = _repo(tmp_path)
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(tmp_path / "remote.git"), str(other))
    (other / "edited.py").write_text("print(9)\n")
    _git(other, "commit", "-q", "-am", "upstream edit")
    _git(other, "push", "-q", "origin", "HEAD")
    (work / "edited.py").write_text("print(9)\n")
    assert R.git_state(str(work / "edited.py"))[1] == "modified"          # this clone has not fetched it yet
    _git(work, "fetch", "-q")
    assert R.git_state(str(work / "edited.py"))[1] == "modified, on remote"
