"""typed_paths (history, source, links, path) ships its own self-test on a toy store; run it with the suite."""
from math_brain import typed_paths


def test_typed_paths_selftest_passes(capsys) -> None:
    rc = typed_paths.selftest()
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "FAIL" not in out and "selftest passed" in out
