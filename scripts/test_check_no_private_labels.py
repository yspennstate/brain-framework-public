"""The private-label guard must fire on every spelling a leak actually takes.

This guard reported "clean" on 2026-08-10 while eight tracked files held a real home
directory, because its rule matched `[\\/]` - exactly ONE path separator - and a path
written inside a source literal carries two. It also never opened .vbs files, which are
the ones most likely to hardcode a path. Both are fixed; these are the controls, and they
live here rather than in a throwaway script so they travel with the guard.

The negative cases matter as much as the positive ones. The guard's own docstring says a
check that fires on correct code teaches people to skim its output, so the placeholders
that documentation legitimately uses must stay silent.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "check_no_private_labels.py")
BS = chr(92)


def _load():
    spec = importlib.util.spec_from_file_location("private_label_guard", GUARD)
    module = importlib.util.module_from_spec(spec)
    sys.modules["private_label_guard"] = module
    spec.loader.exec_module(module)
    return module


class PrivateLabelGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guard = _load()
        cls.home_rules = [p for p, what in cls.guard.PRIVATE
                          if "home directory" in what]

    def _home_hit(self, text):
        return any(re.search(p, text, re.I) for p in self.home_rules)

    def test_every_spelling_of_a_home_directory_is_caught(self):
        # the escaped form is the one that slipped through: a source literal carries two
        for label, text in (
            ("single separator", "C:" + BS + "Users" + BS + "someone" + BS + "x.md"),
            ("escaped separator", "C:" + BS * 2 + "Users" + BS * 2 + "someone"),
            # assembled at runtime, never written as a literal: this file is inside the
            # tree the guard scans, so a literal fixture here is a real finding for it
            ("forward slash", "C:/" + "Users/" + "someone/x.md"),
            ("posix home", "/" + "home/" + "someone/x.md"),
            ("macOS home", "/" + "Users/" + "someone/x.md"),
        ):
            with self.subTest(label):
                self.assertTrue(self._home_hit(text),
                                "guard is blind to the %s spelling" % label)

    def test_documentation_placeholders_stay_silent(self):
        for label, text in (
            ("example", "C:" + BS * 2 + "Users" + BS * 2 + "example" + BS * 2 + "x.md"),
            ("ellipsis", "C:" + BS * 2 + "Users" + BS * 2 + "..."),
            ("named fixture", "C:" + BS * 2 + "Users" + BS * 2 + "private-sentinel"),
            ("the literal word user", "C:/Users/user/x.md"),
            ("macOS placeholder", "/" + "Users/" + "user/x.md"),
        ):
            with self.subTest(label):
                self.assertFalse(self._home_hit(text),
                                 "guard fires on a legitimate placeholder: %s" % label)

    def test_every_text_file_is_read_whatever_its_name(self):
        # .vbs was once outside the suffix list; on 2026-09-29 so were .jsonl, .log, .lock and
        # files with no suffix (account04/B17 r13, B17-F1). The guard now reads every file that
        # is not binary, and says how many it read.
        import contextlib
        import io
        import shutil
        import tempfile
        name = "zq-" + "synthetic-host-9"
        tmp = tempfile.mkdtemp(prefix="label_guard_cov_")
        try:
            rules_file = os.path.join(tmp, "rules.local")
            with open(rules_file, "w", encoding="utf-8") as fh:
                fh.write("zq-synthetic-host-\\d|synthetic host\n")
            rules = self.guard.PUBLIC_RULES + self.guard._local_private_rules([rules_file])
            os.remove(rules_file)
            names = ["launcher.vbs", "log.jsonl", "refresh.log", "Caddyfile", "deps.lock",
                     "digest.sha256", "unit.service"]
            for fn in names:
                with open(os.path.join(tmp, fn), "w", encoding="utf-8") as fh:
                    fh.write("host " + name + "\n")
            with open(os.path.join(tmp, "picture.png"), "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n\0\0\0" + name.encode() + b"\0")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = self.guard.main(root=tmp, rules=rules)
            text = out.getvalue()
            self.assertEqual(rc, 1, text)
            for fn in names:
                with self.subTest(fn):
                    self.assertIn(fn, text)
            self.assertNotIn("picture.png", text)
            self.assertIn("8 files: 7 read as text, 1 binary skipped", text)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_tree_itself_is_clean(self):
        # the guard is also its own acceptance test: run it over the repository
        import subprocess
        proc = subprocess.run([sys.executable, GUARD], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=600)
        self.assertEqual(proc.returncode, 0,
                         (proc.stdout or "")[-800:] + (proc.stderr or "")[-400:])

    # The published rule table holds shapes only. Until 2026-09-28 it also held eleven literal
    # names and skipped its own file, so "clean" meant clean everywhere except the list of names.
    SHAPE_LABELS = {"hardcoded project slug", "absolute home directory",
                    "private profile document", "routable IP address"}

    def test_published_rules_are_shapes_only(self):
        extra = sorted({what for _, what in self.guard.PUBLIC_RULES} - self.SHAPE_LABELS)
        self.assertEqual(extra, [], "a published rule names something other than a shape; put "
                         "a literal name in the local rule file instead: %s" % extra)

    def test_the_local_file_catches_a_name_and_the_guard_scans_itself(self):
        import contextlib
        import io
        import shutil
        import tempfile
        # a synthetic stand-in: the real names never appear in a file this repository carries
        name = "zq-" + "synthetic-host-7"
        tmp = tempfile.mkdtemp(prefix="label_guard_")
        try:
            os.makedirs(os.path.join(tmp, "scripts"))
            shutil.copy(GUARD, os.path.join(tmp, "scripts", "check_no_private_labels.py"))
            rules_file = os.path.join(tmp, "rules.local")
            with open(rules_file, "w", encoding="utf-8") as fh:
                fh.write("# synthetic\nzq-synthetic-host-\\d|synthetic host\n")
            rules = self.guard.PUBLIC_RULES + self.guard._local_private_rules([rules_file])

            def run():
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = self.guard.main(root=tmp, rules=rules)
                return rc, out.getvalue()

            self.assertEqual(run()[0], 0, "a tree with no names must be clean")
            with open(os.path.join(tmp, "notes.md"), "w", encoding="utf-8") as fh:
                fh.write("deploy to " + name + " tonight\n")
            rc, out = run()
            self.assertEqual(rc, 1, out)
            self.assertIn("notes.md", out)
            os.remove(os.path.join(tmp, "notes.md"))
            # a literal put back into the rule table is a finding, not an exemption
            with open(os.path.join(tmp, "scripts", "check_no_private_labels.py"), "a",
                      encoding="utf-8") as fh:
                fh.write("\n# (r\"" + name + "\", \"host\")\n")
            rc, out = run()
            self.assertEqual(rc, 1, out)
            self.assertIn("check_no_private_labels.py", out)
            with open(GUARD, encoding="utf-8") as fh:
                self.assertNotIn(name, fh.read())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_every_clone_on_the_machine_reads_the_user_rule_file(self):
        files = [os.path.normcase(os.path.abspath(p)) for p in self.guard.local_rule_files()]
        user = os.path.join(os.path.expanduser("~"), ".brain-framework", "private_labels.local")
        self.assertIn(os.path.normcase(os.path.abspath(user)), files)


if __name__ == "__main__":
    unittest.main()
