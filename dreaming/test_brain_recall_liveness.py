#!/usr/bin/env python3
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import brain


OLD = "fixture_recall_old_2026_07_20"
NEW = "fixture_recall_current_2026_07_20"
OTHER = "fixture_recall_other_2026_07_20"


class BrainRecallLivenessTests(unittest.TestCase):
    def test_recall_reuses_one_corpus_snapshot_for_every_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = Path(tmp)
            for slug in (OLD, NEW, OTHER):
                (memory / (slug + ".md")).write_text("fixture\n", encoding="utf-8")

            corpus_calls = []

            def corpus_slugs():
                corpus_calls.append(True)
                return [OLD, NEW, OTHER]

            index = {
                OLD: "old terminal recovery",
                NEW: "current terminal recovery",
                OTHER: "other terminal recovery",
            }
            with (
                mock.patch.object(brain, "MEM", str(memory)),
                mock.patch.object(brain, "_archived_index_lines", return_value={}),
                mock.patch.object(brain, "_corpus_slugs", side_effect=corpus_slugs),
                mock.patch.object(brain, "_load_superseded", return_value={OLD: NEW}),
                mock.patch.object(brain, "_semantic_scores", return_value={}),
                mock.patch.object(brain, "_tiers", return_value={}),
                mock.patch.object(brain.sr, "_index_lines", return_value=index),
                mock.patch.object(brain.sr, "_words", return_value=["terminal", "recovery"]),
                mock.patch.object(
                    brain.sr, "_seeds_from_prompt",
                    return_value=({OLD: 1.0, OTHER: 0.5}, {}),
                ),
                mock.patch.object(brain.sr, "_load_graph", return_value=({}, {})),
                mock.patch.object(brain.sr, "_load_salience", return_value={}),
                mock.patch.object(brain.sr, "_desc", side_effect=lambda slug, _idx: slug),
                mock.patch.dict(os.environ, {"BRAIN_RECALL_BLEND": "1"}),
            ):
                output = io.StringIO()
                with redirect_stdout(output):
                    brain.recall("terminal recovery", n=3)

            self.assertEqual(len(corpus_calls), 1)
            self.assertIn(NEW, output.getvalue())

    def test_default_fallback_reuses_its_first_corpus_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = Path(tmp)
            for slug in (OLD, NEW, OTHER):
                (memory / (slug + ".md")).write_text("fixture\n", encoding="utf-8")

            corpus_calls = []

            def corpus_slugs():
                corpus_calls.append(True)
                return [OLD, NEW, OTHER]

            class FakeBM25:
                def score(self, _query, _limit):
                    return [(OLD, 2.0), (OTHER, 1.0)]

            index = {
                OLD: "old terminal recovery",
                NEW: "current terminal recovery",
                OTHER: "other terminal recovery",
            }
            with (
                mock.patch.object(brain, "MEM", str(memory)),
                mock.patch.object(brain, "_archived_index_lines", return_value={}),
                mock.patch.object(brain, "_corpus_slugs", side_effect=corpus_slugs),
                mock.patch.object(brain, "_load_superseded", return_value={OLD: NEW}),
                mock.patch.object(brain, "_semantic_scores", return_value={}),
                mock.patch.object(brain, "_tiers", return_value={}),
                mock.patch.object(brain.sr, "_index_lines", return_value=index),
                mock.patch.object(brain.sr, "_words", return_value=["terminal", "recovery"]),
                mock.patch.object(brain.sr, "_seeds_from_prompt", return_value=({}, {})),
                mock.patch.object(brain.sr, "_load_graph", return_value=({}, {})),
                mock.patch.object(brain.sr, "_load_salience", return_value={}),
                mock.patch.object(brain.sr, "_desc", side_effect=lambda slug, _idx: slug),
                mock.patch("bm25.BM25", FakeBM25),
                mock.patch.dict(os.environ, {"BRAIN_RECALL_BLEND": "",
                                             # FakeBM25 has no .df; the 07-24 spellfix
                                             # (QueryNormalizer(_bm.df)) would raise and the
                                             # bm25 block swallows it into lexical blindness.
                                             "BRAIN_RECALL_SPELLFIX": "0"}),
            ):
                output = io.StringIO()
                with redirect_stdout(output):
                    brain.recall("terminal recovery", n=3)

            self.assertEqual(len(corpus_calls), 1)
            self.assertIn(NEW, output.getvalue())

    def test_shared_snapshot_keeps_broken_and_cyclic_routes_fail_open(self):
        known = {OLD, NEW}
        self.assertEqual(brain._current_slug(OLD, {OLD: NEW}, known), NEW)
        self.assertEqual(brain._current_slug(OLD, {OLD: "missing"}, known), OLD)
        self.assertEqual(brain._current_slug(OLD, {OLD: NEW, NEW: OLD}, known), OLD)


if __name__ == "__main__":
    unittest.main()
