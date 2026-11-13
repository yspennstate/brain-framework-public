"""The recall server's code watch must see an edit to a module first imported after the server started.

Runs code_changed on a Brain built without __init__ (no index load), with the server's folder pointed at a temp dir.
Control: with BRS_LATE_IMPORT_WATCH=0 the same edit goes unseen, so the test can fail."""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import importlib, os, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brain_recall_server as brs


def bump(path):
    t = time.time() + 5
    os.utime(path, (t, t))


def run(switch):
    os.environ["BRS_LATE_IMPORT_WATCH"] = switch
    d = tempfile.mkdtemp()
    old_d = brs.D
    brs.D = d
    sys.path.insert(0, d)
    try:
        start = os.path.join(d, "mod_at_start_%s.py" % switch)
        late = os.path.join(d, "mod_late_%s.py" % switch)
        open(start, "w").write("X = 1\n")
        open(late, "w").write("Y = 1\n")
        importlib.import_module("mod_at_start_%s" % switch)
        b = brs.Brain.__new__(brs.Brain)
        b.code = {start: os.stat(start).st_mtime_ns}
        first = b.code_changed()                       # nothing edited yet
        importlib.import_module("mod_late_%s" % switch)  # imported inside a call
        second = b.code_changed()                      # first sight of the late module
        bump(late)
        third = b.code_changed()                       # the edit
        bump(start)
        fourth = b.code_changed()
        return first, second, third, fourth, late, start
    finally:
        brs.D = old_d
        sys.path.remove(d)


def test_late_import_edit_is_seen():
    first, second, third, fourth, late, start = run("1")
    assert first is None and second is None
    assert third == late
    assert fourth in (late, start)


def test_control_switch_off_misses_it():
    first, second, third, fourth, late, start = run("0")
    assert first is None and second is None
    assert third is None
    assert fourth == start


if __name__ == "__main__":
    # Discovery is not execution: a help request exits before any work.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__ or os.path.basename(__file__))
        raise SystemExit(0)
    test_late_import_edit_is_seen()
    test_control_switch_off_misses_it()
    print("2/2 ok")
