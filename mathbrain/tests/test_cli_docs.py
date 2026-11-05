"""Every `math-brain <command>` the docs tell a reader to run must exist in the CLI.

A sync of cli.py from another line once dropped calibrate-v2 and verify-calibration-v2 while the module, its
tests and docs/public-calibration-v2.md stayed; nothing failed until the end-to-end CLI test happened to run.
"""
import argparse
import re
from pathlib import Path

from math_brain.cli import _parser

ROOT = Path(__file__).resolve().parents[1]
COMMAND = re.compile(r"(?:\bmath-brain|python -m math_brain\.cli)\s+([a-z][a-z0-9]*(?:-[a-z0-9]+)*)")


def _documented(texts):
    found = {}
    for name, text in texts:
        for match in COMMAND.finditer(text):
            found.setdefault(match.group(1), name)
    return found


def _cli_commands(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


def _doc_texts():
    paths = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
    return [(str(p.relative_to(ROOT)), p.read_text(encoding="utf-8")) for p in paths if p.exists()]


def test_every_documented_command_exists() -> None:
    documented = _documented(_doc_texts())
    missing = {cmd: where for cmd, where in documented.items() if cmd not in _cli_commands(_parser())}
    assert not missing, "documented but not in the CLI: %s" % missing


def test_the_check_fails_when_a_documented_command_is_missing() -> None:
    parser = argparse.ArgumentParser(prog="math-brain")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("policy")
    documented = _documented([("doc.md", "run `math-brain policy check x` then `math-brain calibrate-v2 IN.json`")])
    assert set(documented) == {"policy", "calibrate-v2"}
    assert set(documented) - _cli_commands(parser) == {"calibrate-v2"}
