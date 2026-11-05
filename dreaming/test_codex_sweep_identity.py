"""Native rollout integration controls, using only private temporary fixtures."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import compact_nodes as nodes
import test_codex_manual_compact as fixtures

SID, OTHER, NOW = fixtures.SID, fixtures.OTHER, fixtures.NOW


class NativeSweepTests(unittest.TestCase):
    native = fixtures.ManualCompactTests.native
    mesh = fixtures.ManualCompactTests.mesh
    plan = fixtures.ManualCompactTests.plan
    saved = fixtures.ManualCompactTests.saved

    def setUp(self):
        fixtures.ManualCompactTests.setUp(self)
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("COMPACT_MATH_STAGE_FILE", None)
        os.environ.pop("COMPACT_MATH_DRAFT_DIR", None)
        self.rollout = self.rollout.with_name("rollout-2026-10-09T20-00-00-" + SID + ".jsonl")
        self.header()
        self.cursor = {}

    def header(self, sid=SID, cwd=None):
        payload = {"id": sid, "cwd": str(cwd or self.home)}
        self.rollout.write_text(json.dumps({"type": "session_meta", "payload": payload}) + "\n", encoding="utf-8")

    def pool(self, thread=SID, cwd=None):
        directory = self.home / "brain_workers"
        directory.mkdir(exist_ok=True)
        (directory / "state.json").write_text(json.dumps({"w1": {"thread": thread}}), encoding="utf-8")
        self.header(cwd=cwd or directory / "w1")
        data = json.loads(self.checklist.read_text(encoding="utf-8"))
        data["entries"]["stable"] = {"id": "T-20000101-112", "agent": "w1", "status": "working"}
        data["entries"]["unrelated"] = {"id": "T-20000101-113", "agent": "w2", "status": "working"}
        self.checklist.write_text(json.dumps(data), encoding="utf-8")

    def event(self, text="Synthetic automatic recovery checkpoint.", path=None):
        record = {"type": "compacted", "timestamp": (NOW + dt.timedelta(seconds=30)).isoformat(),
                  "payload": {"message": text}}
        raw = (json.dumps(record) + "\n").encode("utf-8")
        with (path or self.rollout).open("ab") as stream:
            stream.write(raw)
        return raw

    def sweep(self, paths=None):
        return nodes.codex_sweep(paths=[str(p) for p in (paths or [self.rollout])], state=self.cursor)

    def automatic(self):
        return [r for r in nodes._index_rows() if r.get("capture_kind") != "manual_checkpoint"]

    def test_actual_sweep_full_uuid_manual_link_equal_digest_and_private_anchor(self):
        self.pool()
        text = "PRIVATE_SYNTHETIC_NATIVE_CANARY\nNEXT: verify the bound session."
        self.summary.write_bytes(text.encode("utf-8"))
        import codex_manual_compact as manual
        checkpoint = manual.save(manual.prepare(self.summary, SID, self.rollout, "w1", NOW), mesh=self.mesh)
        self.event(text)
        self.assertEqual(self.sweep(), 1)
        row, = self.automatic()
        self.assertEqual(row["session_id"], SID)
        self.assertEqual(row["sha256"], checkpoint["sha256"])
        self.assertEqual(row["agent"], "w1")
        self.assertEqual(set(row["tasks"]), {"T-20000101-110", "T-20000101-112"})
        self.assertEqual(row["previous"], checkpoint["name"])
        anchor = Path(row["anchor"]).read_text(encoding="utf-8")
        self.assertIn("[[" + checkpoint["name"] + "]]", anchor)
        self.assertNotIn("PRIVATE_SYNTHETIC_NATIVE_CANARY", anchor)
        self.assertEqual(row["anchor_privacy"], "metadata_only_manual_digest")
        with self.rollout.open("rb") as stream:
            digest = hashlib.sha256(stream.readline()).hexdigest()
        self.assertEqual(row["native_header_sha256"], digest)
        self.assertEqual(self.sweep(), 0)
        self.assertEqual(len(self.automatic()), 1)

    def test_interactive_native_task_does_not_inherit_claude_prefix(self):
        self.event()
        self.assertEqual(self.sweep(), 1)
        row, = self.automatic()
        self.assertEqual(row["agent"], "codex-11111111")
        self.assertEqual(row["tasks"], ["T-20000101-110"])

    def test_stale_worker_retains_native_task_only(self):
        self.pool(thread=OTHER)
        self.event()
        self.assertEqual(self.sweep(), 1)
        row, = self.automatic()
        self.assertEqual(row["tasks"], ["T-20000101-110"])
        self.assertEqual(row["agent"], "codex-11111111")

    def test_worker_named_directory_outside_native_pool_is_not_binding(self):
        self.pool(cwd=self.home / "unrelated" / "brain_workers" / "w1")
        self.event()
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.automatic()[0]["tasks"], ["T-20000101-110"])

    def test_malformed_pool_state_does_not_create_worker_links(self):
        self.pool()
        (self.home / "brain_workers/state.json").write_bytes(b"{incomplete")
        self.event()
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.automatic()[0]["tasks"], ["T-20000101-110"])

    def test_header_uuid_mismatch_is_held_for_retry_without_node(self):
        self.header(sid=OTHER)
        offset = self.rollout.stat().st_size
        self.event("PRIVATE_REJECTED_SYNTHETIC_TEXT")
        self.assertEqual(self.sweep(), 0)
        self.assertEqual(list(self.cursor.values()), [offset])
        self.assertFalse(Path(nodes.INDEX).exists())
        error = (self.root / "errors.log").read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE_REJECTED_SYNTHETIC_TEXT", error)

    def test_nonobject_header_is_rejected_without_node(self):
        self.rollout.write_bytes(b"[]\n")
        self.event()
        self.assertEqual(self.sweep(), 0)
        self.assertFalse(Path(nodes.INDEX).exists())

    def test_non_session_header_is_rejected_without_node(self):
        self.rollout.write_text(json.dumps({"type": "response_item", "payload": {"id": SID}}) + "\n", encoding="utf-8")
        self.event()
        self.assertEqual(self.sweep(), 0)
        self.assertFalse(Path(nodes.INDEX).exists())

    def test_missing_filename_uuid_is_rejected_without_guessing(self):
        self.rollout = self.rollout.with_name("rollout.jsonl")
        self.header()
        self.event()
        self.assertEqual(self.sweep(), 0)
        self.assertFalse(Path(nodes.INDEX).exists())

    def test_header_changed_during_recall_prevents_write(self):
        self.event()
        def change(*_):
            self.header(cwd=self.home / "changed")
            self.event()
            return []
        with patch.object(nodes, "_related_memories", side_effect=change):
            self.assertEqual(self.sweep(), 0)
        self.assertFalse(Path(nodes.INDEX).exists())

    def test_pool_recycled_during_recall_does_not_inherit_new_owners_tasks(self):
        self.pool()
        self.event()
        def recycle(*_):
            (self.home / "brain_workers/state.json").write_text(json.dumps({"w1": {"thread": OTHER}}), encoding="utf-8")
            return []
        with patch.object(nodes, "_related_memories", side_effect=recycle):
            self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.automatic()[0]["tasks"], ["T-20000101-110"])

    def test_partial_record_is_retried_after_writer_finishes(self):
        offset = self.rollout.stat().st_size
        raw = (json.dumps({"type": "compacted", "payload": {"summary": "Partial synthetic record"}}) + "\n").encode()
        with self.rollout.open("ab") as stream:
            stream.write(raw[:25])
        self.assertEqual(self.sweep(), 0)
        self.assertEqual(list(self.cursor.values()), [offset])
        with self.rollout.open("ab") as stream:
            stream.write(raw[25:])
        self.assertEqual(self.sweep(), 1)
        self.assertEqual(self.sweep(), 0)

    def test_rejected_file_does_not_block_another_rollout(self):
        bad = self.rollout.with_name("rollout-" + OTHER + ".jsonl")
        bad.write_bytes(self.rollout.read_bytes())
        self.event("Rejected native identity", path=bad)
        self.event("Accepted native identity")
        self.assertEqual(self.sweep(paths=[bad, self.rollout]), 1)
        self.assertEqual(self.automatic()[0]["session_id"], SID)

    def test_math_packet_stays_transient_and_retry_still_stages(self):
        calls = []
        def stage(packet, name, directory, expected_session):
            calls.append((packet, name, directory, expected_session))
            return {"draft": "synthetic-validated-reference"}
        packet = {"unselected_private_canary": "PRIVATE_MATH_SYNTHETIC_FIELD"}
        with patch.dict(sys.modules, {"compact_math": SimpleNamespace(stage_math_node=stage)}):
            first = nodes.save_node("Math fixture summary", "codex", SID, "auto", str(self.rollout), NOW,
                                    extra={"math_solver": packet})
            second = nodes.save_node("Math fixture summary", "codex", SID, "auto", str(self.rollout), NOW,
                                     extra={"math_solver": packet})
        self.assertTrue(first)
        self.assertIsNone(second)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(c[-1] == SID for c in calls))
        row, = self.automatic()
        self.assertEqual(row["math_draft"], {"draft": "synthetic-validated-reference"})
        self.assertNotIn("math_solver", row)
        self.assertNotIn("PRIVATE_MATH_SYNTHETIC_FIELD", Path(nodes.INDEX).read_text(encoding="utf-8"))

    @unittest.skip("since 14 Oct a backfill row matches the hook's row only when its text is the same compaction (_same_compaction), so a prefaced summary is saved as its own node")
    def test_math_backfill_window_retry_still_stages(self):
        with patch.object(nodes, "_stage_math_draft", return_value=None) as stage:
            nodes.save_node("First Claude summary", "claude", SID, "auto", when=NOW)
            result = nodes.save_node("Preface then first Claude summary", "claude", SID, "auto",
                                     when=NOW + dt.timedelta(seconds=2), window_s=600, extra={"math_solver": {}})
        self.assertIsNone(result)
        self.assertEqual(stage.call_count, 2)

    def test_math_export_exception_logs_type_without_private_input(self):
        def reject(*_):
            raise ValueError("PRIVATE_MATH_ERROR_CANARY")
        with patch.dict(sys.modules, {"compact_math": SimpleNamespace(stage_math_node=reject)}):
            nodes.save_node("Synthetic safe summary", "codex", SID, "auto", when=NOW, extra={"math_solver": {}})
        error = (self.root / "math_errors.jsonl").read_text(encoding="utf-8")
        self.assertIn("ValueError", error)
        self.assertNotIn("PRIVATE_MATH_ERROR_CANARY", error)


if __name__ == "__main__":
    unittest.main()
