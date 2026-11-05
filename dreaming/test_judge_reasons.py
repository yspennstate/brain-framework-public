"""The judge's suppressed-case builder must return on ANY reason the hook logs (2026-09-02).

A new suppression reason (precision_graph) that the builder's fixed reason list did not know left its
rows unplaced, and the loop that waits for every row to be placed never ended; the nightly dream builds
that section inline. This test writes a suppression log whose rows carry reasons the builder has never
seen and requires the call to return within a few seconds. Run: python test_judge_reasons.py
"""
import io
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fire_judge_nightly as fj  # noqa: E402


class SuppressedBuilderReturns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._here = fj.HERE
        fj.HERE = self.tmp
        now = time.time()
        rows = []
        for i in range(12):
            reason = ["brand_new_reason", "another_one", "precision_graph", "cap"][i % 4]
            rows.append("%.0f\tPreToolUse\tact\treflex_%d\t%s\t%08x-1111-2222-3333-444444444444" % (now - 60 * i, i, reason, i))
        with io.open(os.path.join(self.tmp, "instinct_suppressed.tsv"), "w", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
        with io.open(os.path.join(self.tmp, "instincts.tsv"), "w", encoding="utf-8") as f:
            f.write("id\tmode\tvalidity\ttrigger\tgut\n")

    def tearDown(self):
        fj.HERE = self._here
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_with_deadline(self, fn, seconds):
        box = {}
        th = threading.Thread(target=lambda: box.setdefault("out", fn()), daemon=True)
        th.start()
        th.join(seconds)
        self.assertFalse(th.is_alive(), "the builder did not return within %d s - the placement loop hangs on a reason it does not know" % seconds)
        return box.get("out")

    def test_unknown_reasons_do_not_hang(self):
        out = self._run_with_deadline(lambda: fj.suppressed_cases(4, days=1), 15)
        self.assertIsInstance(out, list)   # no transcripts for these sessions, so no cases - but it RETURNS

    def test_known_reasons_still_return(self):
        out = self._run_with_deadline(lambda: fj.suppressed_cases(2, days=1), 15)
        self.assertIsInstance(out, list)


if __name__ == "__main__":
    unittest.main(verbosity=1)
