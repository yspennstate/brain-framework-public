"""A new script must not do real work when asked for help.

These scripts mostly have no argument parser, so an unrecognised flag falls straight
through to the entry point and runs a build that writes shared artifacts. It has bitten
this codebase five times; the most recent was `instincts.py --help` compiling live
artifacts while holding no claim. The four graph builders were fixed by hand.

This began as a ratchet over 86 recorded scripts. The baseline is now EMPTY - every one of
them takes an early -h/--help return - so in practice this is a clean-tree assertion, and
help_hazard_baseline.txt exists to absorb any script that legitimately cannot be guarded
yet. Adding a line to silence a failure is the wrong move; the early return is the fix.

An empty baseline and a broken detector look identical from the outside, so the detector is
controlled by planting a hazardous script and confirming the first test fails.

Verifying by execution is not an option here: running these to see what they do IS the
hazard, and several write into a live brain directory. The check is therefore static, and
deliberately requires all three conditions before calling something hazardous.
"""
import ast
import io
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASELINE = os.path.join(HERE, "help_hazard_baseline.txt")

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}
WRITE_HINTS = ("os.replace", "shutil.copy", "shutil.move", "makedirs", "mkdir",
               "subprocess.run", "subprocess.Popen", "to_csv", "savez", "dump(")


def _works_at_module_scope(tree):
    """True when the file executes on import rather than only defining things.

    Loops, with-blocks and bare calls at top level are work. print() is excluded so a
    banner does not count, and imports/defs/assignments are definition, not execution.
    """
    for node in tree.body:
        if isinstance(node, (ast.For, ast.While, ast.With, ast.AsyncWith)):
            return True
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            name = getattr(func, "id", "") or getattr(func, "attr", "")
            if name in ("print", "insert", "append"):
                # sys.path.insert / sys.path.append is import plumbing, not work. A
                # library that only adjusts the path so it can reach its siblings has
                # no entry point to guard, and flagging it fires on correct code -
                # which this file's own doctrine says teaches people to skim the output.
                # job_common.py was the first false positive here (2026-08-10).
                continue
            return True
    return False


def _hazardous(path):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError:
        return False
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    # A missing __main__ guard is the WORSE shape, not a safer one: the work runs on
    # import, so every invocation does it and there is no entry point to guard. This
    # filter used to be `if "__main__" not in src: return False`, which exempted
    # exactly those files - dream.py among them, 15 module-scope statements and six
    # loops, invisible to a ratchet whose baseline read zero.
    if "__main__" not in src and not _works_at_module_scope(tree):
        return False
    if "--help" in src or "argparse" in src or "ArgumentParser" in src:
        return False
    # unittest.main() parses argv with argparse of its own: --help prints its usage
    # and exits without running a test. Verified by running one. Counting these as
    # hazards inflated the list by nine, and a list padded with things that are
    # already safe is one people stop reading.
    if "unittest.main(" in src:
        return False
    if any(hint in src for hint in WRITE_HINTS):
        return True
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # `open(...)` is a Name; `io.open(...)`, `codecs.open(...)` and
        # `Path(...).open(...)` are Attributes. Matching only the Name form missed 172
        # write-mode call sites in this tree against 59 it saw - including dream.py's
        # own `with io.open(OUT, "w") as f`, which writes the agenda.
        if "open" not in (getattr(node.func, "id", ""), getattr(node.func, "attr", "")):
            continue
        for arg in node.args[1:]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                    and any(m in arg.value for m in ("w", "a", "x")):
                return True
        for kw in node.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant) \
                    and isinstance(kw.value.value, str) \
                    and any(m in kw.value.value for m in ("w", "a", "x")):
                return True
    return False


def _scan():
    found = set()
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            if _hazardous(full):
                found.add(os.path.relpath(full, ROOT).replace(os.sep, "/"))
    return found


def _baseline():
    with io.open(BASELINE, encoding="utf-8") as fh:
        return {line.strip() for line in fh
                if line.strip() and not line.startswith("#")}


class HelpFlagIsInertTests(unittest.TestCase):
    def test_no_new_script_does_work_on_help(self):
        added = sorted(_scan() - _baseline())
        self.assertEqual(
            added, [],
            "these scripts run real work on --help; add an early "
            "'-h/--help' return before the first side effect:\n  "
            + "\n  ".join(added))

    def test_the_detector_catches_both_shapes_of_hazard(self):
        """The control, as a test rather than a note in the docstring.

        It was a manual procedure - "plant a hazardous script and confirm the first
        test fails" - and a manual procedure gets run for the shape its author had in
        mind. This one plants BOTH: work behind a __main__ guard, and work at module
        scope with no guard at all. The second was exempt by construction for weeks
        while the baseline read zero and looked like proof.
        """
        guarded = ('if __name__ == "__main__":\n'
                   '    subprocess.run(["x"])\n')
        unguarded = 'for name in ["a"]:\n    subprocess.run([name])\n'
        # io.open, not open: the attribute form is the majority idiom in this tree and
        # was invisible while the baseline read zero.
        attr_open = ('import io\n\n'
                     'with io.open("out.txt", "w", encoding="utf-8") as fh:\n'
                     '    fh.write("x")\n')
        kw_mode = ('import io\n\n'
                   'with io.open("out.txt", mode="a") as fh:\n'
                   '    fh.write("x")\n')
        safe = 'import argparse\n\n\ndef main():\n    argparse.ArgumentParser()\n'
        with tempfile.TemporaryDirectory() as tmp:
            for label, body, want in (("guarded by __main__", guarded, True),
                                      ("module scope, no guard", unguarded, True),
                                      ("io.open in write mode", attr_open, True),
                                      ("open with mode= keyword", kw_mode, True),
                                      ("has an argument parser", safe, False)):
                probe = os.path.join(tmp, "probe.py")
                with io.open(probe, "w", encoding="utf-8") as fh:
                    fh.write(body)
                self.assertEqual(
                    _hazardous(probe), want,
                    "detector is blind to a hazard shape: %s" % label)

    def test_baseline_has_no_stale_entries(self):
        # A fixed script must be removed from the baseline, or the ratchet stops
        # ratcheting: the list would keep licensing a hazard that no longer exists.
        stale = sorted(_baseline() - _scan())
        self.assertEqual(
            stale, [],
            "these are no longer hazardous - delete them from "
            "help_hazard_baseline.txt:\n  " + "\n  ".join(stale))


if __name__ == "__main__":
    unittest.main()
