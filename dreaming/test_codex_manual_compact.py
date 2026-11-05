import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import compact_nodes as nodes
import codex_manual_compact as manual


SID = "11111111-2222-4333-8444-555555555555"
OTHER = "aaaaaaaa-2222-4333-8444-555555555555"
NOW = dt.datetime(2026, 10, 9, 20, 30, tzinfo=dt.timezone.utc)


class ManualCompactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="manual-compact-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / "compactions"
        self.mem = self.home / "memory"
        self.mem.mkdir()
        (self.mem / (nodes.HUB + ".md")).write_text("Synthetic hub\n", encoding="utf-8")
        self.checklist = self.home / "checklist.json"
        self.checklist.write_text(json.dumps({"entries": {
            "open": {"id": "T-20000101-110", "agent": "codex-11111111", "status": "working"},
            "closed": {"id": "T-20000101-099", "agent": "codex-11111111", "status": "done"},
            "other": {"id": "T-20000101-108", "agent": "claude-11111111", "status": "working"},
        }}), encoding="utf-8")
        for key, value in {"ROOT": self.root, "MEM": self.mem, "INDEX": self.root / "INDEX.jsonl",
                           "CHECKLIST": self.checklist, "HOME": self.home}.items():
            p = patch.object(nodes, key, str(value))
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(nodes, "_related_memories", return_value=[])
        p.start()
        self.addCleanup(p.stop)
        self.rollout = self.home / ".codex-fixture" / "sessions" / "2026" / "10" / "09" / "rollout.jsonl"
        self.rollout.parent.mkdir(parents=True)
        self.native(SID)
        self.summary = self.home / "summary.md"
        self.raw = "Owner ask and evidence.\r\nNEXT: verify the deployed change.\r\nUnicode: μ ש\n".encode("utf-8")
        self.summary.write_bytes(self.raw)
        self.calls = []

    def native(self, sid):
        self.rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": sid}}) + "\n", encoding="utf-8")

    def mesh(self, op, agent, resources):
        self.calls.append((op, agent, tuple(resources)))

    def plan(self, sid=SID):
        return manual.prepare(self.summary, sid, self.rollout, "codex-" + sid[:8], NOW)

    def saved(self):
        return manual.save(self.plan(), mesh=self.mesh)

    def test_plan_does_not_write(self):
        p = self.plan()
        self.assertFalse(self.root.exists())
        self.assertEqual(p["row"]["tasks"], ["T-20000101-110"])
        self.assertFalse(p["row"]["native_context_reduced"])

    def test_exact_bytes_saved_hash_verified_and_claimed(self):
        r = self.saved()
        self.assertEqual(Path(r["node"]).read_bytes(), self.raw)
        self.assertEqual(r["sha256"], manual.sha(self.raw))
        self.assertEqual([c[0] for c in self.calls], ["claim", "assert", "release"])
        self.assertEqual(len(self.calls[0][2]), 3)
        self.assertEqual(manual.manual_rows(), [{k: v for k, v in r.items() if k != "status"}])
        self.assertIn(r["name"], [x["name"] for x in nodes._index_rows()])

    def test_retry_does_not_append_or_rewrite(self):
        r = self.saved()
        before = Path(r["node"]).stat().st_mtime_ns
        second = manual.save(self.plan(), mesh=self.mesh)
        self.assertEqual(second["status"], "ALREADY_SAVED")
        self.assertEqual(Path(r["node"]).stat().st_mtime_ns, before)
        self.assertEqual(len(manual.manual_rows()), 1)
        self.assertEqual(len(self.calls), 3)

    def test_two_sessions_identical_summary_are_distinct(self):
        first = self.saved()
        self.native(OTHER)
        second = manual.save(self.plan(OTHER), mesh=self.mesh)
        self.assertNotEqual(first["node"], second["node"])
        self.assertEqual(len(manual.manual_rows()), 2)

    def test_two_summaries_same_second_are_distinct(self):
        first = self.saved()
        self.summary.write_bytes(self.raw + b"A second completed step.\n")
        second = self.saved()
        self.assertNotEqual(first["node"], second["node"])
        self.assertIn("[[" + first["name"] + "]]", Path(second["anchor"]).read_text(encoding="utf-8"))

    def test_no_summary_text_in_anchor_even_without_screen_keywords(self):
        canary = "SYNTHETIC_UNLABELLED_HELDOUT_PHRASE_47"
        self.summary.write_text(canary + "\nNEXT: " + canary, encoding="utf-8")
        r = self.saved()
        self.assertNotIn(canary, Path(r["anchor"]).read_text(encoding="utf-8"))
        self.assertIn(canary, Path(r["node"]).read_text(encoding="utf-8"))

    def test_claim_denial_writes_nothing(self):
        def deny(*args):
            raise RuntimeError("live claim held by another actor")
        with self.assertRaises(RuntimeError):
            manual.save(self.plan(), mesh=deny)
        self.assertFalse(self.root.exists())
        self.assertEqual(len(list(self.mem.iterdir())), 1)

    def test_assert_denial_releases_and_writes_nothing(self):
        def deny(op, *args):
            self.calls.append(op)
            if op == "assert":
                raise RuntimeError("lease expired")
        with self.assertRaises(RuntimeError):
            manual.save(self.plan(), mesh=deny)
        self.assertEqual(self.calls, ["claim", "assert", "release"])
        self.assertFalse(self.root.exists())

    def test_wrong_native_identity_rejected(self):
        self.native(OTHER)
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.plan()
        self.assertFalse(self.root.exists())

    def test_wrong_agent_rejected(self):
        with self.assertRaisesRegex(ValueError, "mesh identity"):
            manual.prepare(self.summary, SID, self.rollout, "codex-aaaaaaaa", NOW)

    def test_bad_uuid_rejected(self):
        with self.assertRaises(ValueError):
            self.plan("../invalid")

    def test_non_native_path_rejected(self):
        arbitrary = self.home / "ordinary.jsonl"
        arbitrary.write_bytes(self.rollout.read_bytes())
        with self.assertRaisesRegex(ValueError, "native profile"):
            manual.prepare(self.summary, SID, arbitrary, "codex-11111111", NOW)

    def test_partial_header_rejected(self):
        self.rollout.write_bytes(self.rollout.read_bytes().rstrip(b"\n"))
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.plan()

    def test_empty_or_no_next_rejected(self):
        for text in ("", "A summary with no actionable next line.", "NEXT:   \n"):
            with self.subTest(text=text):
                self.summary.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.plan()

    def test_oversized_and_invalid_utf8_rejected(self):
        for raw in (b"a" * (manual.MAX_SUMMARY_BYTES + 1), b"\xff\nNEXT: go"):
            with self.subTest(n=len(raw)):
                self.summary.write_bytes(raw)
                with self.assertRaises(ValueError):
                    self.plan()

    def test_tampered_summary_and_anchor_rejected(self):
        r = self.saved()
        for field in ("node", "anchor"):
            p = Path(r[field])
            original = p.read_bytes()
            p.write_bytes(original + b"bad")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                self.plan()
            p.write_bytes(original)

    def test_existing_conflicting_bytes_not_overwritten(self):
        p = self.plan()
        node = Path(p["row"]["node"])
        node.parent.mkdir(parents=True)
        node.write_bytes(b"previous unrelated bytes")
        with self.assertRaisesRegex(ValueError, "preserved without overwrite"):
            manual.save(p, mesh=self.mesh)
        self.assertEqual(node.read_bytes(), b"previous unrelated bytes")
        self.assertFalse(Path(p["row"]["anchor"]).exists())

    def test_crash_after_summary_resumes_exact_bytes(self):
        p = self.plan()
        manual.put_once(p["row"]["node"], p["raw"])
        self.assertEqual(manual.save(p, mesh=self.mesh)["status"], "SAVED")

    def test_crash_after_anchor_recovers_on_later_retry(self):
        first = self.plan()
        manual.put_once(first["row"]["node"], first["raw"])
        manual.put_once(first["row"]["anchor"], first["anchor_bytes"])
        later = manual.prepare(self.summary, SID, self.rollout, "codex-11111111", NOW + dt.timedelta(days=1))
        result = manual.save(later, mesh=self.mesh)
        self.assertEqual(result["node"], first["row"]["node"])
        self.assertEqual(result["anchor_sha256"], first["row"]["anchor_sha256"])

    def test_crash_after_raw_only_recovers_next_day_without_duplicate(self):
        first = self.plan()
        manual.put_once(first["row"]["node"], first["raw"])
        later = manual.prepare(self.summary, SID, self.rollout, "codex-11111111", NOW + dt.timedelta(days=1))
        result = manual.save(later, mesh=self.mesh)
        self.assertEqual(result["node"], first["row"]["node"])
        self.assertEqual(len(list((self.root / "manual").rglob("*.md"))), 1)

    def test_changed_incomplete_anchor_is_preserved_and_rejected(self):
        first = self.plan()
        manual.put_once(first["row"]["node"], first["raw"])
        original = first["anchor_bytes"]
        bad = original.replace(b"This is the agent's provisional recovery checkpoint.", b"Injected unlabelled private prose.")
        manual.put_once(first["row"]["anchor"], bad)
        with self.assertRaisesRegex(ValueError, "metadata-only format"):
            manual.prepare(self.summary, SID, self.rollout, "codex-11111111", NOW + dt.timedelta(days=1))
        self.assertEqual(Path(first["row"]["anchor"]).read_bytes(), bad)
        self.assertFalse((self.root / "MANUAL_INDEX.jsonl").exists())

    def test_malformed_ledger_refuses_success(self):
        self.root.mkdir()
        (self.root / "MANUAL_INDEX.jsonl").write_bytes(b'{"incomplete":')
        with self.assertRaises(ValueError):
            self.plan()

    def test_missing_hub_refuses_write(self):
        with patch.object(nodes, "HUB", "missing-hub"):
            with self.assertRaisesRegex(ValueError, "hub is missing"):
                self.plan()
        self.assertFalse(self.root.exists())

    def test_manual_does_not_suppress_native_compaction(self):
        r = self.saved()
        native = nodes.save_node(self.raw.decode("utf-8"), "codex", SID, "auto", when=NOW, window_s=600)
        self.assertIsNotNone(native)
        auto = [x for x in nodes._index_rows() if x.get("capture_kind") != "manual_checkpoint"]
        self.assertEqual(len(auto), 1)
        self.assertEqual(auto[0]["tasks"], ["T-20000101-110"])
        self.assertIn("[[" + r["name"] + "]]", Path(auto[0]["anchor"]).read_text(encoding="utf-8"))

    def test_original_claude_task_selection_preserved(self):
        self.assertEqual(nodes._open_tasks(SID), ["T-20000101-108"])

    def private_auto(self, text="Violet falcon narrative.\nNEXT: carry the local result forward.\n", runtime="codex"):
        self.summary.write_text(text, encoding="utf-8", newline="")
        manual_row = self.saved()
        with patch.object(nodes, "_related_memories", side_effect=AssertionError("private prose reached recall")):
            nodes.save_node(text, runtime, SID, "auto", when=NOW)
        automatic = [r for r in nodes._index_rows() if r.get("capture_kind") != "manual_checkpoint"][0]
        return manual_row, automatic

    def test_matching_native_automatic_anchor_never_copies_unlabelled_prose(self):
        _, automatic = self.private_auto()
        anchor = Path(automatic["anchor"]).read_text(encoding="utf-8")
        self.assertNotIn("Violet falcon", anchor)
        self.assertNotIn("carry the local result", anchor)
        self.assertIn("Local recovery checkpoint; content withheld", anchor)
        self.assertEqual(automatic["anchor_privacy"], "metadata_only_manual_digest")

    def test_matching_claude_automatic_anchor_is_also_private(self):
        _, automatic = self.private_auto(runtime="claude")
        self.assertNotIn("Violet falcon", Path(automatic["anchor"]).read_text(encoding="utf-8"))

    def test_transport_whitespace_does_not_remove_privacy(self):
        self.summary.write_bytes(b"  Violet falcon\r\nnarrative.\r\nNEXT: retain locally.\r\n")
        self.saved()
        with patch.object(nodes, "_related_memories", side_effect=AssertionError("private recall")):
            node = nodes.save_node("Violet falcon narrative.\nNEXT: retain locally.", "codex", SID, "auto", when=NOW)
        auto = next(r for r in nodes._index_rows() if r.get("node") == node.replace("\\", "/"))
        self.assertNotIn("Violet falcon", Path(auto["anchor"]).read_text(encoding="utf-8"))

    def test_matching_automatic_anchor_repair_stays_private(self):
        _, automatic = self.private_auto()
        anchor = Path(automatic["anchor"])
        anchor.rename(anchor.with_suffix(".fixture-backup"))
        self.assertEqual(nodes.ensure_anchors(), 1)
        self.assertNotIn("Violet falcon", anchor.read_text(encoding="utf-8"))

    def test_relink_never_sends_manual_or_matching_automatic_prose_to_recall(self):
        manual_row, _ = self.private_auto()
        before = Path(manual_row["anchor"]).read_bytes()
        with patch.object(nodes, "_related_memories", side_effect=AssertionError("private recall")):
            self.assertEqual(nodes.relink(), 0)
        self.assertEqual(Path(manual_row["anchor"]).read_bytes(), before)
        self.assertIn("existing", self.plan())

    def test_unrelated_claude_automatic_gist_and_excerpt_are_preserved(self):
        self.saved()
        text = "An unrelated ordinary project update.\nNEXT: inspect the public example."
        node = nodes.save_node(text, "claude", OTHER, "auto", when=NOW)
        auto = next(r for r in nodes._index_rows() if r.get("node") == node.replace("\\", "/"))
        anchor = Path(auto["anchor"]).read_text(encoding="utf-8")
        self.assertIn("An unrelated ordinary project update.", anchor)
        self.assertIn("inspect the public example", anchor)

    def test_legacy_manual_record_uses_verified_local_bytes_for_privacy(self):
        row = self.saved()
        row.pop("anchor_privacy_sha256")
        (self.root / "MANUAL_INDEX.jsonl").write_text(json.dumps(row)+"\n", encoding="utf-8")
        self.assertTrue(nodes.matches_manual_checkpoint(self.raw.decode("utf-8")))
        Path(row["node"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "unverified"):
            nodes.matches_manual_checkpoint(self.raw.decode("utf-8"))

    def test_automatic_repair_cannot_expose_manual_summary(self):
        self.summary.write_bytes(b"UNLABELLED_TEST_CONTENT\nNEXT: preserve locally.\n")
        row = self.saved()
        anchor = Path(row["anchor"])
        # Move only this synthetic fixture to simulate an interrupted store.
        anchor.rename(anchor.with_suffix(".fixture-backup"))
        self.assertEqual(nodes.ensure_anchors(), 0)
        self.assertFalse(anchor.exists())
        with self.assertRaises(OSError):
            self.plan()


    def configure_pool(self, actor="w1", thread=SID):
        pool = self.home / "brain_workers"
        pool.mkdir(exist_ok=True)
        (pool / "state.json").write_text(json.dumps({actor: {"thread": thread}}), encoding="utf-8")
        self.rollout.write_text(json.dumps({"type": "session_meta", "payload": {
            "id": SID, "cwd": str(pool / actor)}}) + "\n", encoding="utf-8")
        return pool

    def pool_plan(self, actor="w1"):
        return manual.prepare(self.summary, SID, self.rollout, actor, NOW)

    def test_current_pool_actor_saves_without_second_mesh_identity(self):
        self.configure_pool()
        row = manual.save(self.pool_plan(), mesh=self.mesh)
        self.assertEqual(row["status"], "SAVED")
        self.assertEqual(row["agent"], "w1")
        self.assertIn("  agent: w1\n", Path(row["anchor"]).read_text(encoding="utf-8"))
        self.assertTrue(all(call[1] == "w1" for call in self.calls))
        self.assertEqual(Path(row["node"]).read_bytes(), self.raw)

    def test_pool_actor_must_match_native_cwd(self):
        self.configure_pool("w2")
        with self.assertRaisesRegex(ValueError, "native cwd"):
            self.pool_plan("w1")
        self.assertFalse(self.root.exists())

    def test_pool_actor_must_match_current_thread(self):
        self.configure_pool(thread=OTHER)
        with self.assertRaisesRegex(ValueError, "current native thread"):
            self.pool_plan()

    def test_pool_actor_missing_state_cannot_infer_binding(self):
        pool = self.configure_pool()
        (pool / "state.json").rename(pool / "synthetic_missing_state.json")
        with self.assertRaises(OSError):
            self.pool_plan()

    def test_pool_actor_name_cannot_escape_worker_scope(self):
        self.configure_pool()
        with self.assertRaisesRegex(ValueError, "mesh identity"):
            self.pool_plan("w1/../w2")

    def test_pool_task_links_include_native_and_stable_actor_only(self):
        self.configure_pool()
        data = json.loads(self.checklist.read_text(encoding="utf-8"))
        data["entries"].update({
            "pool": {"id": "T-20000101-112", "agent": "w1", "status": "working"},
            "peer": {"id": "T-20000101-113", "agent": "w2", "status": "working"},
            "duplicate": {"id": "T-20000101-110", "agent": "w1", "status": "working"},
        })
        self.checklist.write_text(json.dumps(data), encoding="utf-8")
        row = self.pool_plan()["row"]
        self.assertEqual(row["tasks"], ["T-20000101-110", "T-20000101-112"])

    def test_pool_reassignment_after_plan_refuses_all_writes(self):
        pool = self.configure_pool()
        plan = self.pool_plan()
        (pool / "state.json").write_text(json.dumps({"w1": {"thread": OTHER}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "current native thread"):
            manual.save(plan, mesh=self.mesh)
        self.assertFalse(self.calls)
        self.assertFalse(self.root.exists())

    def test_pool_header_change_after_plan_refuses_all_writes(self):
        self.configure_pool()
        plan = self.pool_plan()
        self.native(OTHER)
        with self.assertRaisesRegex(ValueError, "header changed"):
            manual.save(plan, mesh=self.mesh)
        self.assertFalse(self.calls)
        self.assertFalse(self.root.exists())

    def test_pool_reassignment_during_claim_refuses_all_writes(self):
        pool = self.configure_pool()
        plan = self.pool_plan()
        def claiming(op, agent, resources):
            self.mesh(op, agent, resources)
            if op == "claim":
                (pool / "state.json").write_text(json.dumps({"w1": {"thread": OTHER}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "current native thread"):
            manual.save(plan, mesh=claiming)
        self.assertEqual([c[0] for c in self.calls], ["claim", "assert", "release"])
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
