"""brain._sup_base reads supersession's per-folder frontmatter cache, and still answers when a few files are newer
than that cache.

Two ways it used to return None on every call, so the superseded fast-cache never stored and each recall ran the full
frontmatter reader: it opened the old shared cache file after supersession moved to one file per memory folder, and it
demanded that the cache list every file, while supersession leaves up to 64 new ones out on purpose.
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import brain  # noqa: E402
import supersession  # noqa: E402


def _write(d, name, target=""):
    fm = "---\nname: %s\ndescription: x\n%s---\n" % (name, "superseded_by: %s\n" % target if target else "")
    with open(os.path.join(d, name + ".md"), "w", encoding="utf-8") as fh:
        fh.write(fm + "body of %s, long enough to be a memory\n" % name)


class SupBaseFolderCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mem = os.path.join(self.tmp, "memory")
        self.here = os.path.join(self.tmp, "dreaming")
        os.makedirs(self.mem)
        os.makedirs(self.here)
        self.saved = (brain.MEM, brain.SUPERSEDED_INDEX, supersession.HERE)
        brain.MEM = self.mem
        brain.SUPERSEDED_INDEX = os.path.join(self.here, "superseded.tsv")
        open(brain.SUPERSEDED_INDEX, "w").close()
        supersession.HERE = self.here
        brain._SUPROWC.clear()

    def tearDown(self):
        brain.MEM, brain.SUPERSEDED_INDEX, supersession.HERE = self.saved
        brain._SUPROWC.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reads_the_folder_cache_and_the_files_it_left_out(self):
        _write(self.mem, "old_rule_2026_01_01", "new_rule_2026_01_02")
        _write(self.mem, "new_rule_2026_01_02")
        _write(self.mem, "plain_note_2026_01_03")
        supersession._frontmatter_targets(self.mem)
        self.assertTrue(os.path.exists(supersession._fm_cache_path(self.mem)))
        # filed after the cache was written: supersession would not rewrite its cache for it
        _write(self.mem, "later_rule_2026_01_04", "plain_note_2026_01_03")
        rows, _now = brain._sup_rows(supersession)
        got = brain._sup_base(supersession, rows)
        self.assertIsNotNone(got)
        ptr, inv = got
        self.assertEqual(ptr.get("old_rule_2026_01_01.md"), ("new_rule_2026_01_02", ""))
        self.assertEqual(ptr.get("later_rule_2026_01_04.md"), ("plain_note_2026_01_03", ""))
        self.assertNotIn("plain_note_2026_01_03.md", ptr)
        self.assertIn(os.path.normcase("plain_note_2026_01_03"), inv)

    def test_no_cache_for_this_folder_answers_none(self):
        _write(self.mem, "old_rule_2026_01_01", "new_rule_2026_01_02")
        rows, _now = brain._sup_rows(supersession)
        self.assertIsNone(brain._sup_base(supersession, rows))


if __name__ == "__main__":
    unittest.main()
