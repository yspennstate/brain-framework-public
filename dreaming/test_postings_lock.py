#!/usr/bin/env python3
"""The postings build lock: one per index, taken over only from a dead holder, released only by its owner, and the
holder catches up to generations published while it built. The build itself is stubbed; nothing here reads the
live index."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bm25


class PostingsLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.idx = os.path.join(self.tmp.name, "bm25_index.json.gz")
        Path(self.idx).write_bytes(b"x")
        self.lock = self.idx + ".postings.build.lock"
        self.gen = ["g1"]
        self.built = []

        def build(index_path, base=None):
            self.built.append(self.gen[0])
            if self.on_build:
                self.on_build()
            return 1, 1
        self.on_build = None
        self.patches = [
            mock.patch.object(bm25, "_index_signature", lambda p: self.gen[0]),
            mock.patch.object(bm25, "_postings_conn", lambda p: None),
            mock.patch.object(bm25, "_sweep_postings_temps", lambda p, i: None),
            mock.patch.object(bm25, "_postings_base", lambda i, p: None),
            mock.patch.object(bm25, "build_postings", build),
            mock.patch.object(bm25, "postings_path", lambda i, signature=None: i + ".postings." + self.gen[0]),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def age(self, seconds):
        t = time.time() - seconds
        os.utime(self.lock, (t, t))

    def test_lock_does_not_depend_on_the_generation(self):
        Path(self.lock).write_text(str(os.getppid()))
        self.gen[0] = "g2"
        self.assertEqual(bm25.maybe_build_postings(self.idx, force=True), "another-build")
        self.assertEqual(self.built, [])

    def test_old_lock_of_a_live_holder_is_left_alone(self):
        Path(self.lock).write_text(str(os.getppid()))
        self.age(bm25.POSTINGS_LOCK_STALE_S + 5)
        self.assertEqual(bm25.maybe_build_postings(self.idx, force=True), "another-build")
        self.assertEqual(Path(self.lock).read_text(), str(os.getppid()))

    def test_old_lock_of_a_dead_holder_is_taken_over_and_released(self):
        Path(self.lock).write_text("99998")
        self.age(bm25.POSTINGS_LOCK_STALE_S + 5)
        self.assertEqual(bm25.maybe_build_postings(self.idx, force=True), "built")
        self.assertFalse(os.path.exists(self.lock))

    def test_a_lock_past_dead_s_counts_as_gone_even_with_a_live_pid(self):
        Path(self.lock).write_text(str(os.getpid()))
        self.age(bm25.POSTINGS_LOCK_DEAD_S + 5)
        self.assertFalse(bm25._lock_holder_alive(self.lock))

    def test_a_builder_never_removes_a_lock_that_is_no_longer_its_own(self):
        def stolen():
            Path(self.lock).write_text(str(os.getppid()))
        self.on_build = stolen
        bm25._build_postings_once(self.idx, force=True)
        self.assertEqual(Path(self.lock).read_text(), str(os.getppid()))

    def test_the_holder_builds_again_for_a_generation_published_during_its_build(self):
        def publish():
            if len(self.built) == 1:
                self.gen[0] = "g2"
        self.on_build = publish

        def current(p):
            return (mock.Mock(),) if self.built.count(self.gen[0]) else None
        with mock.patch.object(bm25, "_postings_conn", current):
            self.assertEqual(bm25.maybe_build_postings(self.idx, force=True), "built")
        self.assertEqual(self.built, ["g1", "g2"])


if __name__ == "__main__":
    unittest.main()
