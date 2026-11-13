from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


BRAIN = Path(os.environ.get("BRAIN_PY") or Path(__file__).resolve().parents[2] / "dreaming" / "brain.py")


class BrainRememberConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.mem = self.root / "memory"
        self.mem.mkdir()
        (self.mem / "MEMORY.md").write_text("# Memory index\n\n", encoding="utf-8")
        self.env = os.environ.copy()
        self.env["BRAIN_MEM_DIR"] = str(self.mem)
        self.env["BRAIN_WRITE_LOCK"] = str(self.root / "memory.lock")
        self.env["BRAIN_BM25_INDEX"] = str(self.root / "bm25_index.json.gz")
        self.env["BRAIN_RECALL_REFRESH"] = "0"
        (self.root / "home").mkdir()
        self.env["HOME"] = self.env["USERPROFILE"] = str(self.root / "home")

    def tearDown(self):
        self.temp.cleanup()

    def start(self, slug: str, body: str):
        return subprocess.Popen(
            [sys.executable, str(BRAIN), "remember", slug, "--type", "project",
             "--desc", f"concurrency test memory {slug}", "--body", body],
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_unique_concurrent_writers_all_survive_index(self):
        procs = [self.start(f"unique_{n:02d}", f"body {n}") for n in range(12)]
        results = [proc.communicate(timeout=60) + (proc.returncode,) for proc in procs]
        self.assertTrue(all(result[2] == 0 for result in results), results)
        index = (self.mem / "MEMORY.md").read_text(encoding="utf-8")
        for n in range(12):
            slug = f"unique_{n:02d}"
            self.assertTrue((self.mem / f"{slug}.md").exists())
            self.assertEqual(index.count(f"[{slug}.md]"), 1)

    def test_same_slug_has_exactly_one_winner_and_no_overwrite(self):
        procs = [self.start("same_slug", f"writer {n}") for n in range(12)]
        results = [proc.communicate(timeout=60) + (proc.returncode,) for proc in procs]
        codes = [result[2] for result in results]
        self.assertEqual(codes.count(0), 1, results)
        # 2 is remember's only write-failure code (brain.py: `print("memory write
        # FAILED: ...")` then `return 2`); no path returns 1. The invariant this test
        # exists for - one winner, no overwrite, one index line - is asserted below.
        self.assertEqual(codes.count(2), 11, results)
        index = (self.mem / "MEMORY.md").read_text(encoding="utf-8")
        self.assertEqual(index.count("[same_slug.md]"), 1)
        body = (self.mem / "same_slug.md").read_text(encoding="utf-8")
        self.assertEqual(sum(f"writer {n}" in body for n in range(12)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
