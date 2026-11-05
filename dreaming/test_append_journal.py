#!/usr/bin/env python3
import multiprocessing as mp
import os
import tempfile
import unittest

from append_journal import append_text


def _writer(path, worker, count, result_queue):
    ok = True
    for index in range(count):
        row = "%d\t%d\t%s\n" % (worker, index, "payload-" + ("x" * 24))
        ok = append_text(path, row, timeout=10.0) and ok
    result_queue.put(ok)


class AppendJournalTests(unittest.TestCase):
    def test_exact_utf8_payload(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "journal.tsv")
            self.assertTrue(append_text(path, "1\tbrain\tcontinuity\n"))
            self.assertTrue(append_text(path, "2\tmath\tpi=3.14\n"))
            with open(path, encoding="utf-8", newline="") as handle:
                self.assertEqual(handle.read(), "1\tbrain\tcontinuity\n2\tmath\tpi=3.14\n")

    def test_spawned_writers_never_tear_or_lose_rows(self):
        workers, per_worker = 8, 200
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "journal.tsv")
            ctx = mp.get_context("spawn")
            results = ctx.Queue()
            procs = [ctx.Process(target=_writer, args=(path, worker, per_worker, results))
                     for worker in range(workers)]
            for proc in procs:
                proc.start()
            for proc in procs:
                proc.join(30)
                self.assertEqual(proc.exitcode, 0)
            self.assertTrue(all(results.get(timeout=2) for _ in procs))
            with open(path, encoding="utf-8", newline="") as handle:
                rows = handle.read().splitlines()
            self.assertEqual(len(rows), workers * per_worker)
            parsed = [row.split("\t") for row in rows]
            self.assertTrue(all(len(parts) == 3 for parts in parsed))
            keys = {(int(parts[0]), int(parts[1])) for parts in parsed}
            self.assertEqual(len(keys), workers * per_worker)

    def test_oversize_and_nontext_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "journal.tsv")
            self.assertFalse(append_text(path, "abcdef", max_bytes=5))
            self.assertFalse(append_text(path, b"bytes"))
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
