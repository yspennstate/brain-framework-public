"""The native encrypted-summary schema must not leak retained test prompts."""
import datetime as dt
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import compact_nodes as nodes
import test_codex_sweep_identity as fixtures

SID, NOW = fixtures.SID, fixtures.NOW


class OpaqueBoundaryTests(unittest.TestCase):
    setUp = fixtures.NativeSweepTests.setUp
    native = fixtures.NativeSweepTests.native
    mesh = fixtures.NativeSweepTests.mesh
    plan = fixtures.NativeSweepTests.plan
    saved = fixtures.NativeSweepTests.saved
    header = fixtures.NativeSweepTests.header
    pool = fixtures.NativeSweepTests.pool
    sweep = fixtures.NativeSweepTests.sweep
    automatic = fixtures.NativeSweepTests.automatic
    event = fixtures.NativeSweepTests.event

    def opaque(self, window=4):
        data = {"type": "compacted", "timestamp": (NOW + dt.timedelta(seconds=30)).isoformat(), "payload": {
            "message": "", "window_number": window,
            "replacement_history": [
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "SECRET_SYNTHETIC_QUESTION"}]},
                {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "SECRET_SYNTHETIC_DEVELOPER"}]},
                {"type": "compaction", "encrypted_content": "OPAQUE_SYNTHETIC_CIPHERTEXT"}],
            "retained_context": {"user_messages": ["SECRET_SYNTHETIC_RETAINED"], "incomplete": False, "next_order": 7},
            "latest_token_usage_record": {"usage": {"input_tokens": 123, "output_tokens": 7,
                                                     "unapproved_text": "SECRET_SYNTHETIC_USAGE"}}}}
        with self.rollout.open("ab") as stream:
            stream.write((json.dumps(data) + "\n").encode("utf-8"))

    def assert_private(self):
        for directory in (self.root, self.mem):
            for path in directory.rglob("*"):
                if path.is_file():
                    raw = path.read_bytes()
                    self.assertNotIn(b"SECRET_SYNTHETIC", raw, str(path))
                    self.assertNotIn(b"OPAQUE_SYNTHETIC_CIPHERTEXT", raw, str(path))

    def test_native_opaque_record_creates_honest_metadata_node_and_task_links(self):
        self.pool()
        checkpoint = self.saved()
        self.opaque()
        with patch.object(nodes, "_related_memories", side_effect=AssertionError("must not recall retained context")):
            self.assertEqual(self.sweep(), 1)
        row, = self.automatic()
        self.assertEqual(row["capture_kind"], "codex_boundary_metadata")
        self.assertFalse(row["summary_available"])
        self.assertTrue(row["encrypted_summary_present"])
        self.assertFalse(row["retained_messages_copied"])
        self.assertEqual(row["native_event"]["window_number"], 4)
        self.assertEqual(row["native_event"]["usage"], {"input_tokens": 123, "output_tokens": 7})
        self.assertEqual(set(row["tasks"]), {"T-20000101-110", "T-20000101-112"})
        self.assertEqual(row["previous"], checkpoint["name"])
        self.assertIn("No plaintext recovery summary", Path(row["anchor"]).read_text(encoding="utf-8"))
        self.assertEqual(self.sweep(), 0)
        self.assert_private()

    def test_repair_and_relink_preserve_metadata_boundary(self):
        self.opaque()
        self.assertEqual(self.sweep(), 1)
        row, = self.automatic()
        Path(row["anchor"]).unlink()  # synthetic temporary fixture only
        self.assertEqual(nodes.ensure_anchors(), 1)
        with patch.object(nodes, "_related_memories", side_effect=AssertionError("must not recall boundary")):
            self.assertEqual(nodes.relink(), 0)
        self.assertIn("No plaintext recovery summary", Path(row["anchor"]).read_text(encoding="utf-8"))
        self.assert_private()

    def test_two_boundaries_same_second_are_distinct_and_linked(self):
        self.opaque(window=4)
        self.opaque(window=5)
        self.assertEqual(self.sweep(), 2)
        first, second = self.automatic()
        self.assertNotEqual(first["node"], second["node"])
        self.assertEqual(second["previous"], first["name"])
        self.assertTrue(Path(first["node"]).is_file())
        self.assertEqual(self.sweep(), 0)
        self.assert_private()

    def test_test_harness_scope_denied_even_with_matching_native_task(self):
        self.header(cwd=self.home / "pair_study" / "w1")
        self.opaque()
        self.assertEqual(self.sweep(), 0)
        self.assertFalse(Path(nodes.INDEX).exists())
        self.assertIn("scope_not_allowed", (self.root / "EVENTS.jsonl").read_text(encoding="utf-8"))
        self.assert_private()

    def test_plaintext_in_test_cwd_is_also_excluded(self):
        self.header(cwd=self.home / "test_v1" / "items")
        self.event("SECRET_SYNTHETIC_PLAINTEXT_QUESTION")
        self.assertEqual(self.sweep(), 0)
        self.assertFalse(Path(nodes.INDEX).exists())
        self.assert_private()

    def test_unregistered_nonpool_session_is_not_captured(self):
        self.checklist.write_text('{"entries":{}}', encoding="utf-8")
        self.opaque()
        self.assertEqual(self.sweep(), 0)
        self.assertFalse(Path(nodes.INDEX).exists())

    def test_exact_pool_cwd_can_capture_without_a_current_checklist_task(self):
        self.pool()
        self.checklist.write_text('{"entries":{}}', encoding="utf-8")
        self.opaque()
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.automatic()[0]["agent"], "w1")
        self.assert_private()

    def test_verified_manual_checkpoint_allows_later_nonpool_capture(self):
        checkpoint = self.saved()
        self.checklist.write_text('{"entries":{}}', encoding="utf-8")
        self.opaque()
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.automatic()[0]["previous"], checkpoint["name"])
        self.assert_private()


if __name__ == "__main__":
    unittest.main()
