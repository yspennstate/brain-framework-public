from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = str(Path(__file__).resolve().parents[3])     # the brain root: holds dreaming/ (and tools/ in a clone)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dreaming.project_memory.clauses import MAX_PROMPT_CHARS, partition_spans, project_prompt
from dreaming.project_memory.classify import (CLASSIFIER_VERSION, classification_digest,
                                               classify_clause, default_config, load_config)
from dreaming.project_memory.drift import operation_for, record_action
from dreaming.project_memory.hook import handle as hook_handle
from dreaming.project_memory.hot_cache import (build_contract_cache, cache_path,
                                                evaluate_hot, load_contract_cache)
from dreaming.project_memory.hourly_runner import (_below_normal, _compute_deferral,
                                                    _publish_and_refresh_cache,
                                                    _record_deferral_in_store)
from dreaming.project_memory.review import (SourceBytesUnavailable, acknowledge, mark_shard_served, offer_context,
                                             publish_all, publish_project_window, read_offer)
from dreaming.project_memory.reclassify import (apply_reclassification,
                                                 assignment_table_digest,
                                                 prepare_reclassification)
from dreaming.project_memory.sources import commit_manifests, configured_files, scan_sources
from dreaming.project_memory.store import ProjectMemoryStore
import dreaming.project_memory_hot_hook as standalone_hot
try:
    from tools.install_project_memory import install
except ImportError:                                  # an installed brain ships without the repo's tools/ folder
    install = None


def config(*projects: tuple[str, list[str]]) -> dict:
    value = default_config()
    value["projects"] = [
        {"id": project_id, "active": True, "pinned": False, "roots": [], "aliases": [], "anchors": anchors}
        for project_id, anchors in projects
    ]
    return value


class ProjectMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="project-memory-test-")
        self.root = Path(self.temp.name)
        self.store = ProjectMemoryStore(root=self.root, path=self.root / "state.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def ingest(self, text: str, seq: int = 1, session: str = "session-one",
               cfg: dict | None = None, source: str = "synthetic-source") -> tuple[str, bool]:
        return self.store.ingest_prompt(
            text, source_id=source, source_seq=seq, session=session,
            occurred_at="2026-01-01T00:00:00Z", provenance="owner", runtime="synthetic-test",
            cwd="", source_project="", config=cfg or config(("project-alpha", ["alpha parser"])),
        )

    def write_config(self, cfg: dict, name: str) -> Path:
        path = self.root / name
        path.write_text(json.dumps(cfg, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
        return path

    def configured_claude_fixture(self) -> tuple[dict, Path]:
        transcript = self.root / "claude-source" / "session.jsonl"
        transcript.parent.mkdir()
        rows = [
            {"type": "user", "sessionId": "source-session",
             "timestamp": "2026-01-01T00:00:00Z", "cwd": "",
             "message": {"role": "user", "content": "Implement alpha parser."}},
            {"type": "user", "sessionId": "source-session",
             "timestamp": "2026-01-01T00:01:00Z", "cwd": "",
             "message": {"role": "user", "content": "Implement beta parser."}},
        ]
        transcript.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8", newline="\n",
        )
        cfg = config(("project-alpha", ["alpha parser"]))
        cfg["sources"] = [
            {"type": "claude", "root": str(transcript.parent),
             "glob": "*.jsonl"}
        ]
        occurrences, _source_rows, pending = scan_sources(self.store, cfg)
        for occurrence in occurrences:
            self.store.ingest_prompt(
                occurrence.text, source_id=occurrence.source_id,
                source_seq=occurrence.source_seq, session=occurrence.session,
                occurred_at=occurrence.occurred_at,
                provenance=occurrence.provenance, runtime=occurrence.runtime,
                cwd=occurrence.cwd, source_project=occurrence.source_project,
                config=cfg,
            )
        commit_manifests(self.store, pending)
        with self.store.immediate():
            self.store.db.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) "
                "VALUES('classification_config_digest',?)",
                (classification_digest(cfg),),
            )
            self.store.db.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) "
                "VALUES('classification_classifier_version',?)",
                (CLASSIFIER_VERSION,),
            )
        return cfg, transcript

    def test_partition_covers_every_character_and_overflow_tail(self) -> None:
        text = "alpha\r\nβeta; " + ("x" * MAX_PROMPT_CHARS) + "TAIL-A"
        spans, overflow = partition_spans(text)
        self.assertTrue(overflow)
        self.assertEqual(0, spans[0]["start"])
        self.assertEqual(len(text), spans[-1]["end"])
        self.assertEqual(text, "".join(span["raw"] for span in spans))
        keyed = lambda domain, value: hashlib.sha256((domain + value).encode()).hexdigest()
        safe_a, _clauses_a, _ = project_prompt(text, keyed)
        safe_b, _clauses_b, _ = project_prompt(text[:-1] + "B", keyed)
        self.assertNotEqual(safe_a[-1]["span_hmac"], safe_b[-1]["span_hmac"])

    def test_repeated_occurrences_are_distinct_but_replay_is_idempotent(self) -> None:
        first, created_a = self.ingest("Implement alpha parser.", seq=1)
        second, created_b = self.ingest("Implement alpha parser.", seq=2)
        replay, created_replay = self.ingest("Implement alpha parser.", seq=2)
        self.assertTrue(created_a and created_b)
        self.assertNotEqual(first, second)
        self.assertEqual(second, replay)
        self.assertFalse(created_replay)

    def test_source_locator_mutation_fails_loudly(self) -> None:
        self.ingest("Implement alpha parser.", seq=7)
        with self.assertRaisesRegex(RuntimeError, "source_ref_content_changed"):
            self.ingest("Implement a different parser.", seq=7)

    def test_multi_label_does_not_silently_drop_fifteen_projects(self) -> None:
        projects = [("project-%02d" % index, ["token-%02d" % index]) for index in range(15)]
        cfg = config(*projects)
        raw = "Implement " + " ".join("token-%02d" % index for index in range(15))
        labels = classify_clause(raw, config=cfg)
        self.assertEqual(15, len(labels))

    def test_structured_read_only_shell_is_not_progress(self) -> None:
        self.assertEqual(("read", False), operation_for("PowerShell", {"command": "git status"}))
        self.assertEqual(("read", False), operation_for("shell_command", {"command": "rg alpha"}))

    def test_one_tool_call_advances_drift_once(self) -> None:
        cfg = config(("project-alpha", ["alpha parser"]))
        self.ingest("Implement alpha parser.", cfg=cfg)
        record_action(self.store, session="new-session", tool_name="PowerShell",
                      action_text={"command": "python verify_alpha.py"}, outcome="pending",
                      provenance_group="tool", action_key="call-1", project_ids=["project-alpha"])
        record_action(self.store, session="new-session", tool_name="PowerShell",
                      action_text={"command": "python verify_alpha.py"}, outcome="success",
                      provenance_group="test", action_key="call-1", project_ids=["project-alpha"])
        count = self.store.db.execute("SELECT COUNT(*) FROM action_events").fetchone()[0]
        progress = self.store.db.execute("SELECT progress_actions FROM drift_state").fetchone()[0]
        self.assertEqual(1, count)
        self.assertEqual(1, progress)

    def test_ack_requires_observed_exact_bytes_and_tamper_fails(self) -> None:
        cfg = config(("project-alpha", ["alpha parser"]))
        event_id, _ = self.ingest("Implement alpha parser.", cfg=cfg)
        source_rows = {("synthetic-source", 1): {"text": "Implement alpha parser."}}
        publish_project_window(self.store, "project-alpha", source_rows=source_rows,
                               bucket="2026-01-01T01:00:00Z")
        offer = offer_context(self.store, session="reader", project_ids=["project-alpha"])[0]
        with self.assertRaisesRegex(ValueError, "served"):
            acknowledge(self.store, offer["ack_token"], occurrence_count=offer["occurrence_count"],
                        packet_sha256=offer["packet_sha256"])
        listing = read_offer(self.store, offer["ack_token"])
        self.assertIn("--shard 0", listing)
        served = read_offer(self.store, offer["ack_token"], shard_index=0, record_delivery=False)
        self.assertIn("alpha parser", served)
        with self.assertRaisesRegex(ValueError, "served"):
            acknowledge(self.store, offer["ack_token"], occurrence_count=offer["occurrence_count"],
                        packet_sha256=offer["packet_sha256"])
        mark_shard_served(self.store, offer["ack_token"], 0)
        ack = acknowledge(self.store, offer["ack_token"], occurrence_count=offer["occurrence_count"],
                          packet_sha256=offer["packet_sha256"])
        self.assertTrue(ack.startswith("ack:"))

    def test_project_packet_excludes_other_project_clause(self) -> None:
        cfg = config(("project-alpha", ["alpha parser"]), ("project-beta", ["beta renderer"]))
        text = "Implement alpha parser. Implement beta renderer."
        self.ingest(text, cfg=cfg)
        source_rows = {("synthetic-source", 1): {"text": text}}
        row = publish_project_window(self.store, "project-alpha", source_rows=source_rows,
                                     bucket="2026-01-01T02:00:00Z")
        shard_path = self.store.db.execute(
            "SELECT shard_path FROM review_shards WHERE window_id=? ORDER BY shard_index LIMIT 1",
            (row["window_id"],),
        ).fetchone()[0]
        packet = Path(shard_path).read_text(encoding="utf-8")
        self.assertIn("alpha parser", packet)
        self.assertNotIn("beta renderer", packet)

    def test_every_span_has_clause_or_explicit_disposition_and_is_served(self) -> None:
        cfg = config(("project-alpha", ["alpha parser"]))
        text = "Implement alpha parser.\n\n"
        self.ingest(text, cfg=cfg)
        total = self.store.db.execute("SELECT COUNT(*) FROM coverage_spans").fetchone()[0]
        clause_spans = self.store.db.execute("SELECT COUNT(DISTINCT span_id) FROM clauses").fetchone()[0]
        dispositions = self.store.db.execute("SELECT COUNT(*) FROM coverage_dispositions").fetchone()[0]
        overlap = self.store.db.execute(
            "SELECT COUNT(*) FROM coverage_dispositions d JOIN clauses c ON c.span_id=d.span_id"
        ).fetchone()[0]
        self.assertEqual(total, clause_spans + dispositions)
        self.assertEqual(0, overlap)
        row = publish_project_window(
            self.store, "project-alpha", source_rows={("synthetic-source", 1): {"text": text}},
            bucket="2026-01-01T03:00:00Z",
        )
        shard = Path(self.store.db.execute(
            "SELECT shard_path FROM review_shards WHERE window_id=? ORDER BY shard_index LIMIT 1",
            (row["window_id"],),
        ).fetchone()[0])
        payload = shard.read_text(encoding="utf-8")
        self.assertIn("representation=`non_actionable_separator`", payload)
        offer = offer_context(self.store, session="span-reader", project_ids=["project-alpha"])[0]
        shard.write_text(payload.replace("representation=`non_actionable_separator`", "representation=`deleted`", 1),
                         encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(ValueError, "no longer match"):
            read_offer(self.store, offer["ack_token"], shard_index=0)

    def test_v2_migration_backfills_missing_span_dispositions(self) -> None:
        cfg = config(("project-alpha", ["alpha parser"]))
        self.ingest("Implement alpha parser.\n", cfg=cfg)
        with self.store.db:
            self.store.db.execute("DELETE FROM coverage_dispositions")
            self.store.db.execute("UPDATE schema_meta SET value='2' WHERE key='schema_version'")
        self.store.close()
        self.store = ProjectMemoryStore(root=self.root, path=self.root / "state.sqlite3")
        missing = self.store.db.execute(
            "SELECT COUNT(*) FROM coverage_spans s WHERE NOT EXISTS "
            "(SELECT 1 FROM clauses c WHERE c.span_id=s.span_id) AND NOT EXISTS "
            "(SELECT 1 FROM coverage_dispositions d WHERE d.span_id=s.span_id)"
        ).fetchone()[0]
        self.assertEqual(0, missing)

    def test_v3_migration_backfills_only_authoritative_source_bindings(self) -> None:
        cfg = config(("project-alpha", ["alpha parser"]))
        direct_id, _ = self.ingest("Implement alpha parser.", cfg=cfg, seq=1)
        hook_id, _ = self.ingest(
            "Never skip alpha parser checks.", cfg=cfg, source="hook",
            session="pending-hook", seq=2)
        with self.store.db:
            self.store.db.execute("UPDATE prompt_events SET source_content_hmac=NULL")
            self.store.db.execute("UPDATE schema_meta SET value='3' WHERE key='schema_version'")
        self.store.close()
        self.store = ProjectMemoryStore(root=self.root, path=self.root / "state.sqlite3")
        bindings = {
            str(row[0]): row[1] for row in self.store.db.execute(
                "SELECT event_id,source_content_hmac FROM prompt_events"
            )
        }
        self.assertIsNotNone(bindings[direct_id])
        self.assertIsNone(bindings[hook_id])
        self.assertEqual(
            "5", self.store.db.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0])

    def test_v4_migration_makes_raw_wrapper_source_rows_inert(self) -> None:
        cfg = config(("project-alpha", ["alpha parser"]))
        event_id, _ = self.ingest(
            "Implement alpha parser.", cfg=cfg,
            source="claude-transcript:legacy-session", seq=3)
        with self.store.db:
            self.store.db.execute("UPDATE schema_meta SET value='4' WHERE key='schema_version'")
        self.store.close()
        self.store = ProjectMemoryStore(root=self.root, path=self.root / "state.sqlite3")
        row = self.store.db.execute(
            "SELECT provenance FROM prompt_events WHERE event_id=?", (event_id,)
        ).fetchone()
        self.assertEqual("automation", row[0])

    def test_sources_share_hook_normalization_and_filter_non_owner_sessions(self) -> None:
        root = self.root / "sources"
        claude = root / "claude" / "owner.jsonl"
        subagent = root / "claude" / "owner" / "subagents" / "agent-a.jsonl"
        codex_owner = root / "codex" / "rollout-owner.jsonl"
        codex_exec = root / "codex" / "rollout-exec.jsonl"
        codex_subagent = root / "codex" / "rollout-subagent.jsonl"
        for path in (claude, subagent, codex_owner, codex_exec, codex_subagent):
            path.parent.mkdir(parents=True, exist_ok=True)
        wrapper = "<system-reminder>inert harness</system-reminder>\nImplement alpha parser."
        claude.write_text(json.dumps({
            "type": "user", "uuid": "one", "sessionId": "owner", "timestamp": "2026-01-01T00:00:00Z",
            "cwd": "", "message": {"role": "user", "content": wrapper}
        }) + "\n", encoding="utf-8", newline="\n")
        subagent.write_text(claude.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        def codex_meta(originator: str, source: str, session: str) -> str:
            rows = [
                {"type": "session_meta", "payload": {"id": session, "originator": originator,
                                                       "source": source, "cwd": ""}},
                {"timestamp": "2026-01-01T00:00:00Z", "type": "event_msg",
                 "payload": {"type": "user_message", "message": "Implement alpha parser."}},
            ]
            return "".join(json.dumps(row) + "\n" for row in rows)
        codex_owner.write_text(codex_meta("codex-tui", "cli", "owner-codex"),
                               encoding="utf-8", newline="\n")
        codex_exec.write_text(codex_meta("codex_exec", "exec", "exec-codex"),
                              encoding="utf-8", newline="\n")
        subagent_rows = [
            {"type": "session_meta", "payload": {
                "id": "subagent-codex", "originator": "codex-tui",
                "source": {"subagent": {"thread_spawn": {"depth": 1}}},
                "thread_source": "subagent", "cwd": ""}},
            {"timestamp": "2026-01-01T00:00:00Z", "type": "event_msg",
             "payload": {"type": "user_message", "message": "Agent task."}},
        ]
        codex_subagent.write_text("".join(json.dumps(row) + "\n" for row in subagent_rows),
                                  encoding="utf-8", newline="\n")
        cfg = config(("project-alpha", ["alpha parser"]))
        cfg["sources"] = [
            {"type": "claude", "root": str(root / "claude"), "glob": "**/*.jsonl"},
            {"type": "codex", "root": str(root / "codex"), "glob": "*.jsonl"},
        ]
        files = configured_files(cfg)
        self.assertEqual({claude.resolve(), codex_owner.resolve()}, {path for _kind, path in files})
        rows, _source_rows, _pending = scan_sources(self.store, cfg)
        self.assertEqual(4, len(rows))
        self.assertEqual(
            {"Implement alpha parser."},
            {row.text for row in rows if row.provenance == "owner"},
        )
        self.assertEqual(2, sum("-v2:" in row.source_id for row in rows))
        self.assertEqual(1, sum(row.source_id.startswith("claude-wrapper-v1:") for row in rows))
        self.assertEqual(1, sum(row.source_id.startswith("claude-human-v1:") for row in rows))
        self.assertEqual(2, sum(row.provenance == "automation" for row in rows))

    def test_rotated_source_replays_from_manifest_bound_archive(self) -> None:
        root = self.root / "rotated-source"
        live = root / "live" / "owner-session.jsonl"
        archive = root / "archive" / "project" / live.name
        live.parent.mkdir(parents=True)
        text = "Implement alpha parser from the archived record."
        live.write_text(json.dumps({
            "type": "user", "uuid": "one", "sessionId": "owner",
            "timestamp": "2026-01-01T00:00:00Z", "cwd": "",
            "message": {"role": "user", "content": text},
        }) + "\n", encoding="utf-8", newline="\n")
        cfg = config(("project-alpha", ["alpha parser"]))
        cfg["sources"] = [{"type": "claude", "root": str(live.parent), "glob": "*.jsonl"}]
        cfg["replay_sources"] = [
            {"type": "claude", "root": str(root / "archive"), "glob": "**/*.jsonl"}
        ]
        rows, _source_rows, pending = scan_sources(self.store, cfg)
        self.assertEqual(1, len(rows))
        commit_manifests(self.store, pending)
        occurrence = rows[0]
        self.store.ingest_prompt(
            occurrence.text, source_id=occurrence.source_id, source_seq=occurrence.source_seq,
            session=occurrence.session, occurred_at=occurrence.occurred_at,
            provenance=occurrence.provenance, runtime=occurrence.runtime, cwd=occurrence.cwd,
            source_project=occurrence.source_project, config=cfg,
        )

        archive.parent.mkdir(parents=True)
        archive.write_bytes(live.read_bytes())
        unrelated = archive.parent / "unrelated.jsonl"
        unrelated.write_text("not a replay target\n", encoding="utf-8", newline="\n")
        live.unlink()
        original_is_file = Path.is_file

        def reject_unrelated_stat(path: Path) -> bool:
            if path == unrelated:
                raise AssertionError("unrelated replay archive member was inspected")
            return original_is_file(path)
        with patch.object(Path, "is_file", reject_unrelated_stat):
            replay_rows, source_rows, replay_pending = scan_sources(self.store, cfg)
        self.assertEqual([], replay_rows)
        self.assertEqual([], replay_pending)
        self.assertEqual(0, self.store.last_source_inventory_total)
        self.assertEqual(
            text, source_rows[(occurrence.source_id, occurrence.source_seq)]["text"]
        )
        window = publish_project_window(
            self.store, "project-alpha", source_rows=source_rows,
            bucket="2026-01-01T01:00:00Z",
        )
        self.assertEqual(1, int(window["occurrence_count"]))

    def test_rotated_source_replay_rejects_prefix_drift_and_ambiguity(self) -> None:
        root = self.root / "rotated-source-controls"
        live = root / "live" / "owner-session.jsonl"
        live.parent.mkdir(parents=True)
        original = json.dumps({
            "type": "user", "uuid": "one", "sessionId": "owner",
            "timestamp": "2026-01-01T00:00:00Z", "cwd": "",
            "message": {"role": "user", "content": "Implement alpha parser."},
        }) + "\n"
        live.write_text(original, encoding="utf-8", newline="\n")
        cfg = config(("project-alpha", ["alpha parser"]))
        cfg["sources"] = [{"type": "claude", "root": str(live.parent), "glob": "*.jsonl"}]
        cfg["replay_sources"] = [
            {"type": "claude", "root": str(root / "archive"), "glob": "**/*.jsonl"}
        ]
        rows, _source_rows, pending = scan_sources(self.store, cfg)
        commit_manifests(self.store, pending)
        occurrence = rows[0]
        self.store.ingest_prompt(
            occurrence.text, source_id=occurrence.source_id, source_seq=occurrence.source_seq,
            session=occurrence.session, occurred_at=occurrence.occurred_at,
            provenance=occurrence.provenance, runtime=occurrence.runtime, cwd=occurrence.cwd,
            source_project=occurrence.source_project, config=cfg,
        )
        live.unlink()

        first = root / "archive" / "a" / live.name
        first.parent.mkdir(parents=True)
        first.write_text(original.replace("alpha", "omega"), encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(RuntimeError, "source_replay_prefix_changed"):
            scan_sources(self.store, cfg)

        first.write_text(original, encoding="utf-8", newline="\n")
        second = root / "archive" / "b" / live.name
        second.parent.mkdir(parents=True)
        second.write_text(original, encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(RuntimeError, "source_replay_collision"):
            scan_sources(self.store, cfg)

    def test_codex_exact_locator_survives_later_event_representation(self) -> None:
        root = self.root / "codex-replay"
        rollout = root / "rollout-owner.jsonl"
        root.mkdir()
        meta = {
            "type": "session_meta",
            "payload": {"id": "owner-codex", "originator": "codex-tui", "source": "cli", "cwd": ""},
        }
        response = {
            "timestamp": "2026-01-01T00:00:00Z", "type": "response_item",
            "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Implement first alpha parser."}],
            },
        }
        rollout.write_text(
            json.dumps(meta) + "\n" + json.dumps(response) + "\n",
            encoding="utf-8", newline="\n",
        )
        cfg = config(("project-alpha", ["alpha parser"]))
        cfg["sources"] = [{"type": "codex", "root": str(root), "glob": "*.jsonl"}]
        rows, _source_rows, pending = scan_sources(self.store, cfg)
        self.assertEqual([2], [row.source_seq for row in rows])
        commit_manifests(self.store, pending)
        occurrence = rows[0]
        self.store.ingest_prompt(
            occurrence.text, source_id=occurrence.source_id, source_seq=occurrence.source_seq,
            session=occurrence.session, occurred_at=occurrence.occurred_at,
            provenance=occurrence.provenance, runtime=occurrence.runtime, cwd=occurrence.cwd,
            source_project=occurrence.source_project, config=cfg,
        )

        later = {
            "timestamp": "2026-01-01T00:01:00Z", "type": "event_msg",
            "payload": {"type": "user_message", "message": "Implement later alpha parser."},
        }
        with rollout.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(later) + "\n")
        later_rows, source_rows, _pending = scan_sources(self.store, cfg)
        self.assertEqual([3], [row.source_seq for row in later_rows])
        self.assertEqual(
            occurrence.text,
            source_rows[(occurrence.source_id, occurrence.source_seq)]["text"],
        )
        window = publish_project_window(
            self.store, "project-alpha", source_rows=source_rows,
            bucket="2026-01-01T02:00:00Z",
        )
        self.assertEqual(1, int(window["occurrence_count"]))

    def test_source_salvages_only_zero_prefixed_complete_json(self) -> None:
        root = self.root / "zero-prefix"
        root.mkdir()
        rollout = root / "rollout-owner.jsonl"
        meta = {"type": "session_meta", "payload": {
            "id": "owner-codex", "originator": "codex-tui", "source": "cli", "cwd": ""}}
        prompt = {"timestamp": "2026-01-01T00:00:00Z", "type": "event_msg",
                  "payload": {"type": "user_message", "message": "Implement alpha parser."}}
        prefix = json.dumps(meta).encode("utf-8") + b"\n"
        prompt_bytes = json.dumps(prompt).encode("utf-8")
        rollout.write_bytes(prefix + (b"\x00" * 31) + prompt_bytes + b"\n")
        cfg = config(("project-alpha", ["alpha parser"]))
        cfg["sources"] = [{"type": "codex", "root": str(root), "glob": "*.jsonl"}]
        rows, _source_rows, pending = scan_sources(self.store, cfg)
        self.assertEqual(["Implement alpha parser."], [row.text for row in rows])
        digest = hashlib.sha256(rollout.read_bytes()).hexdigest()
        self.assertEqual(self.store.keyed("source-prefix", digest), pending[0]["prefix_hmac"])

        rollout.write_bytes(prefix + b"garbage" + prompt_bytes + b"\n")
        with self.assertRaisesRegex(RuntimeError, "source_malformed_complete_json"):
            scan_sources(self.store, cfg)

    def test_installer_copies_shared_prompt_normalizer(self) -> None:
        if install is None:
            self.skipTest("tools/install_project_memory.py is not part of an installed brain; run from the clone")
        source_root = Path(__file__).resolve().parents[3]
        runtime_root = self.root / "installed" / "runtime"
        state_root = self.root / "installed" / "state"
        stale = runtime_root / "project_memory" / "project_memory" / "stale.py"
        stale.parent.mkdir(parents=True)
        stale.write_text("stale", encoding="ascii")
        launcher = install(source_root, runtime_root, state_root,
                           self.root / "compute-state.json", Path(sys.executable))
        self.assertEqual(runtime_root.resolve().parent / "scheduler" / "project_memory" / "hourly.vbs",
                         launcher)
        expected = source_root / "dreaming" / "prompt_archive.py"
        installed = runtime_root / "prompt_archive.py"
        self.assertEqual(hashlib.sha256(expected.read_bytes()).digest(),
                         hashlib.sha256(installed.read_bytes()).digest())
        self.assertFalse(stale.parent.exists())
        self.assertIn('sh.Run(', launcher.read_text(encoding="ascii"))
        self.assertIn(', 0, True)', launcher.read_text(encoding="ascii"))

    def test_hourly_compute_deferral_yields_to_owner_and_distress(self) -> None:
        self.assertEqual("", _compute_deferral("ok", "open", False))
        self.assertEqual("", _compute_deferral("ok", "owner_away_open", False))
        self.assertEqual("", _compute_deferral("warn", "owner_present_polite", False))
        self.assertEqual("compute_owner_active",
                         _compute_deferral("warn", "owner_present_polite", True))
        self.assertEqual("compute_throttle", _compute_deferral("warn", "throttle", False))
        self.assertEqual("compute_distress", _compute_deferral("distress", "throttle", False))
        _record_deferral_in_store(self.store, "compute_owner_active")
        health = self.store.db.execute(
            "SELECT status,error_code FROM runner_health WHERE runner_id='hourly'"
        ).fetchone()
        self.assertEqual(("deferred", "compute_owner_active"), tuple(health))

    @unittest.skipUnless(os.name == "nt", "Windows priority-class contract")
    def test_below_normal_uses_a_64_bit_safe_windows_handle(self) -> None:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = ()
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.GetPriorityClass.argtypes = (ctypes.c_void_p,)
        kernel32.GetPriorityClass.restype = ctypes.c_uint32
        kernel32.SetPriorityClass.argtypes = (
            ctypes.c_void_p, ctypes.c_uint32
        )
        kernel32.SetPriorityClass.restype = ctypes.c_int
        handle = kernel32.GetCurrentProcess()
        original = kernel32.GetPriorityClass(handle)
        try:
            _below_normal()
            self.assertEqual(
                0x00004000, kernel32.GetPriorityClass(handle)
            )
        finally:
            self.assertTrue(
                kernel32.SetPriorityClass(handle, original)
            )

    def test_successful_review_publication_refreshes_the_signed_cache(self) -> None:
        order = []
        with patch("dreaming.project_memory.hourly_runner.publish_all",
                   side_effect=lambda *_args, **_kwargs: order.append("publish") or ["window"]), \
             patch("dreaming.project_memory.hourly_runner.build_contract_cache",
                   side_effect=lambda *_args, **_kwargs: order.append("cache")):
            windows = _publish_and_refresh_cache(
                self.store, source_rows={}, config=config(("project-alpha", ["alpha"])))
        self.assertEqual(["window"], windows)
        self.assertEqual(["publish", "cache"], order)

    def test_hot_cache_blocks_scoped_prohibition_without_sqlite_hook_write(self) -> None:
        cfg = config(("project-alpha", ["private fixture data"]))
        self.ingest("Never push private fixture data.", cfg=cfg, session="hot-session")
        build_contract_cache(self.store, cfg)
        before = self.store.db.execute("SELECT COUNT(*) FROM action_events").fetchone()[0]
        cache = load_contract_cache(self.root, self.store.key)
        blocked = evaluate_hot(cache, self.store.key, session="hot-session", tool_name="PowerShell",
                               tool_input={"command": "git push private_fixture_data.json"}, cwd="", config=cfg)
        safe = evaluate_hot(cache, self.store.key, session="hot-session", tool_name="PowerShell",
                            tool_input={"command": "git push parser_code.py"}, cwd="", config=cfg)
        after = self.store.db.execute("SELECT COUNT(*) FROM action_events").fetchone()[0]
        self.assertTrue(blocked["violations"])
        self.assertFalse(safe["violations"])
        self.assertEqual(before, after)

    def test_signed_cache_carries_normalized_roots_and_drift_hysteresis(self) -> None:
        cfg = config(("project-alpha", ["alpha parser"]))
        cfg["projects"][0]["roots"] = [str(self.root / "alpha")]
        cfg["drift_min_progress_actions"] = 5
        cfg["drift_min_low_streak"] = 3
        self.ingest("Implement alpha parser.", cfg=cfg, session="hot-session")
        build_contract_cache(self.store, cfg)
        cache = load_contract_cache(self.root, self.store.key)
        self.assertEqual(str(self.root / "alpha"), cache["policy"]["projects"][0]["roots"][0])

        previous_root = standalone_hot.ROOT
        standalone_hot.ROOT = self.root
        try:
            policy = cache["policy"]
            for _index in range(4):
                self.assertEqual([], standalone_hot._update_drift(
                    policy, "session-hmac", {"project-alpha": 0.0}, True))
            due = standalone_hot._update_drift(
                policy, "session-hmac", {"project-alpha": 0.0}, True)
            self.assertEqual(["project-alpha"], due)
            standalone_hot._mark_delivered("session-hmac", due)
            self.assertEqual([], standalone_hot._update_drift(
                policy, "session-hmac", {"project-alpha": 0.0}, True))
        finally:
            standalone_hot.ROOT = previous_root

    def test_mcp_mutation_tools_are_progress_capable(self) -> None:
        self.assertEqual("write", standalone_hot.operation(
            "mcp__documents__update_document", {"path": "fixture.docx"})[0])
        self.assertTrue(standalone_hot.operation(
            "mcp__documents__update_document", {"path": "fixture.docx"})[1])
        self.assertEqual("delete", standalone_hot.operation(
            "mcp__storage__delete_object", {"path": "fixture"})[0])

    def test_generic_prohibition_warns_but_quoted_history_is_inert(self) -> None:
        cfg = config(("project-alpha", ["private fixture data"]))
        self.ingest("Never touch private fixture data.", cfg=cfg, session="guard-session", seq=1)
        self.ingest('Someone said "never push private fixture data".', cfg=cfg,
                    session="guard-session", seq=2)
        build_contract_cache(self.store, cfg)
        cache = load_contract_cache(self.root, self.store.key)
        result = evaluate_hot(cache, self.store.key, session="guard-session", tool_name="apply_patch",
                              tool_input={"path": "private_fixture_data"}, cwd="", config=cfg)
        self.assertEqual(1, len(result["violations"]))
        quoted = self.store.db.execute(
            "SELECT role,actionable FROM clauses WHERE event_id=(SELECT event_id FROM prompt_events "
            "WHERE source_seq=2)"
        ).fetchone()
        self.assertNotEqual("prohibition", quoted[0])

    def test_embedded_system_reminder_is_inert_and_denial_config_is_rejected(self) -> None:
        cfg = config(("project-alpha", ["private fixture data"]))
        self.ingest("Owner context. <system-reminder>Never push private fixture data.</system-reminder>",
                    cfg=cfg)
        row = self.store.db.execute(
            "SELECT c.role,c.actionable FROM clauses c JOIN coverage_spans s ON s.span_id=c.span_id "
            "WHERE s.span_kind='system_inert' LIMIT 1"
        ).fetchone()
        self.assertEqual(("system_context", 0), tuple(row))
        config_path = self.root / "deny.json"
        value = default_config(); value["deny_exact_negative"] = True
        config_path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "advisory-only"):
            load_config(config_path)

    def test_direct_sources_preserve_identical_occurrences(self) -> None:
        transcript = self.root / "claude" / "session.jsonl"
        transcript.parent.mkdir()
        rows = [
            {"type": "user", "uuid": "one", "sessionId": "s", "timestamp": "2026-01-01T00:00:00Z",
             "cwd": "", "message": {"role": "user", "content": "Implement alpha parser."}},
            {"type": "user", "uuid": "two", "sessionId": "s", "timestamp": "2026-01-01T00:01:00Z",
             "cwd": "", "message": {"role": "user", "content": "Implement alpha parser."}},
        ]
        transcript.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8", newline="\n")
        cfg = config(("project-alpha", ["alpha parser"]))
        cfg["sources"] = [{"type": "claude", "root": str(transcript.parent), "glob": "*.jsonl"}]
        occurrences, _source_rows, _pending = scan_sources(self.store, cfg)
        self.assertEqual(2, len(occurrences))
        self.assertNotEqual(occurrences[0].source_seq, occurrences[1].source_seq)

    def test_unreconciled_hook_prompt_is_unknown_and_inert(self) -> None:
        cfg = config(("project-alpha", ["private alpha data"]))
        event_id, _ = self.ingest(
            "Never push private alpha data.", cfg=cfg, source="hook",
            session="hook-session")
        build_contract_cache(self.store, cfg)
        cache = load_contract_cache(self.root, self.store.key)
        self.assertEqual([], cache["contracts"]["project-alpha"])
        row = publish_project_window(
            self.store, "project-alpha", source_rows={},
            bucket="2026-01-01T04:00:00Z")
        self.assertEqual(0, row["occurrence_count"])
        self.assertEqual(
            ("hook", "unknown"),
            tuple(self.store.db.execute(
                "SELECT source_id,provenance FROM prompt_events WHERE event_id=?",
                (event_id,),
            ).fetchone()))
        self.assertEqual(
            0, self.store.db.execute(
                "SELECT COUNT(*) FROM clause_assignments ca JOIN clauses c "
                "ON c.clause_id=ca.clause_id WHERE c.event_id=?", (event_id,)
            ).fetchone()[0])

    def test_publish_all_excludes_unverified_hook_without_false_pending_state(self) -> None:
        cfg = config(("project-alpha", ["alpha parser"]))
        self.ingest(
            "Implement alpha parser from pending hook bytes.", cfg=cfg,
            source="hook", session="pending-session", seq=7)
        windows = publish_all(
            self.store, source_rows={}, config=cfg,
            bucket="2026-01-01T04:30:00Z")
        project_window = next(
            row for row in windows if str(row["project_id"]) == "project-alpha")
        self.assertEqual(0, project_window["occurrence_count"])
        build_contract_cache(self.store, cfg)
        cached = load_contract_cache(self.root, self.store.key)
        review = cached["reviews"]["project-alpha"]
        self.assertFalse(review["attempt_unresolved"])
        self.assertIsNotNone(review["window_id"])
        state = self.store.db.execute(
            "SELECT attempt_generation,published_generation,last_error_code "
            "FROM project_review_state WHERE project_id='project-alpha'"
        ).fetchone()
        self.assertEqual((1, 1), tuple(state[:2]))
        self.assertIsNone(state[2])

    def test_authoritative_source_rebinds_hook_and_restores_review_bytes(self) -> None:
        cfg = config(("project-alpha", ["alpha parser"]))
        text = "Implement alpha parser."
        event_id, _ = self.ingest(
            text, cfg=cfg, source="hook", session="same-session", seq=1)
        rebound, created = self.ingest(
            text, cfg=cfg, source="claude-transcript:session", session="same-session", seq=9)
        self.assertEqual(event_id, rebound)
        self.assertFalse(created)
        binding = self.store.db.execute(
            "SELECT content_hmac,source_content_hmac FROM prompt_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        self.assertEqual(tuple(binding)[0], tuple(binding)[1])
        row = publish_project_window(
            self.store, "project-alpha",
            source_rows={("claude-transcript:session", 9): {"text": text}},
            bucket="2026-01-01T05:00:00Z")
        self.assertEqual(1, row["occurrence_count"])

    def test_live_hook_handler_never_claims_owner_authority(self) -> None:
        cfg = config(("project-alpha", ["alpha parser"]))
        cfg_path = self.write_config(cfg, "hook-config.json")
        environment = {
            "PROJECT_MEMORY_STATE": str(self.root),
            "PROJECT_MEMORY_DB": str(self.root / "state.sqlite3"),
            "PROJECT_MEMORY_CONFIG": str(cfg_path),
        }
        with patch.dict(os.environ, environment, clear=False):
            hook_handle({
                "hook_event_name": "UserPromptSubmit",
                "session_id": "headless-or-owner-unknown",
                "prompt": "Implement alpha parser.",
                "cwd": "",
            })
        row = self.store.db.execute(
            "SELECT source_id,provenance,source_content_hmac FROM prompt_events "
            "WHERE runtime='claude-hook' ORDER BY ingest_seq DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(("hook", "unknown", None), tuple(row))
        self.assertEqual(
            0, self.store.db.execute(
                "SELECT COUNT(*) FROM clause_assignments ca JOIN clauses c "
                "ON c.clause_id=ca.clause_id JOIN prompt_events p "
                "ON p.event_id=c.event_id WHERE p.runtime='claude-hook'"
            ).fetchone()[0])

    def test_signed_reclassification_is_nonmutating_then_atomic_and_quarantines_hooks(self) -> None:
        prior, _transcript = self.configured_claude_fixture()
        prior_path = self.write_config(prior, "prior.json")
        target = config(
            ("project-alpha", ["alpha parser"]),
            ("project-beta", ["beta parser"]),
        )
        target["sources"] = prior["sources"]
        target_path = self.write_config(target, "target.json")
        hook_id, _ = self.ingest(
            "Never publish headless fleet output.", cfg=prior, source="hook",
            session="headless-fleet", seq=999)
        with self.store.immediate():
            # Reproduce the deployed v1 defect: an unbound hook row was marked
            # owner-authoritative. The v2 apply must quarantine it.
            self.store.db.execute(
                "UPDATE prompt_events SET provenance='owner' WHERE event_id=?",
                (hook_id,),
            )
        before_assignments = assignment_table_digest(self.store)
        before_digest = self.store.db.execute(
            "SELECT value FROM schema_meta "
            "WHERE key='classification_config_digest'"
        ).fetchone()[0]
        progress = []
        artifact = prepare_reclassification(
            self.store, target_config_path=target_path,
            prior_config_path=prior_path,
            progress=progress.append,
        )
        self.assertTrue(any(
            message.startswith("candidate progress sessions=")
            for message in progress
        ))
        self.assertTrue(progress[-1].startswith("signed candidate ready rebuild="))
        self.assertEqual(before_assignments, assignment_table_digest(self.store))
        self.assertEqual(
            before_digest, self.store.db.execute(
                "SELECT value FROM schema_meta "
                "WHERE key='classification_config_digest'"
            ).fetchone()[0])
        envelope = json.loads(artifact.read_text(encoding="utf-8"))
        candidate = artifact.parent / envelope["body"]["candidate_filename"]
        candidate_bytes = candidate.read_bytes()
        self.assertNotIn(b"Implement alpha parser", candidate_bytes)
        self.assertNotIn(b"Implement beta parser", candidate_bytes)
        receipt = apply_reclassification(
            self.store, artifact_path=artifact,
            target_config_path=target_path,
        )
        self.assertEqual(CLASSIFIER_VERSION, receipt["classifier_version"])
        self.assertIsNotNone(receipt["cache_refreshed_at"])
        self.assertEqual(
            classification_digest(target), self.store.db.execute(
                "SELECT value FROM schema_meta "
                "WHERE key='classification_config_digest'"
            ).fetchone()[0])
        self.assertEqual(
            {"project-alpha", "project-beta"},
            {str(row[0]) for row in self.store.db.execute(
                "SELECT DISTINCT ca.project_id FROM clause_assignments ca "
                "JOIN clauses c ON c.clause_id=ca.clause_id "
                "JOIN prompt_events p ON p.event_id=c.event_id "
                "WHERE p.provenance='owner' AND ca.project_id<>'unknown'"
            )})
        self.assertEqual(
            "unknown", self.store.db.execute(
                "SELECT provenance FROM prompt_events WHERE event_id=?",
                (hook_id,),
            ).fetchone()[0])
        self.assertEqual(
            0, self.store.db.execute(
                "SELECT COUNT(*) FROM clause_assignments ca JOIN clauses c "
                "ON c.clause_id=ca.clause_id WHERE c.event_id=?", (hook_id,)
            ).fetchone()[0])
        cache_before = cache_path(self.store.root).read_bytes()
        with patch(
                "dreaming.project_memory.reclassify.build_contract_cache"
        ) as rebuild_cache:
            second = apply_reclassification(
                self.store, artifact_path=artifact,
                target_config_path=target_path,
            )
        rebuild_cache.assert_not_called()
        self.assertEqual(cache_before, cache_path(self.store.root).read_bytes())
        self.assertEqual(receipt["rebuild_id"], second["rebuild_id"])
        self.assertEqual(receipt["cache_sha256"], second["cache_sha256"])
        self.assertEqual(1, self.store.db.execute(
            "SELECT COUNT(*) FROM classification_rebuilds"
        ).fetchone()[0])
        stale_envelope = json.loads(
            cache_path(self.store.root).read_text(encoding="utf-8")
        )
        stale_envelope["body"]["built_at"] = "1970-01-01T00:00:00+00:00"
        stale_envelope["signature"] = hmac.new(
            self.store.key,
            json.dumps(
                stale_envelope["body"], sort_keys=True, separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        stale_bytes = (
            json.dumps(
                stale_envelope, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8") + b"\n"
        )
        cache_path(self.store.root).write_bytes(stale_bytes)
        with self.store.immediate():
            self.store.db.execute(
                "UPDATE classification_rebuilds SET cache_sha256=? "
                "WHERE rebuild_id=?",
                (hashlib.sha256(stale_bytes).hexdigest(),
                 receipt["rebuild_id"]),
            )
        refreshed = apply_reclassification(
            self.store, artifact_path=artifact,
            target_config_path=target_path,
        )
        self.assertNotEqual(
            "1970-01-01T00:00:00+00:00",
            load_contract_cache(self.store.root, self.store.key)["built_at"],
        )
        self.assertEqual(
            hashlib.sha256(cache_path(self.store.root).read_bytes()).hexdigest(),
            refreshed["cache_sha256"],
        )
        with self.store.immediate():
            self.store.db.execute(
                "UPDATE clause_assignments SET score=score+0.01 "
                "WHERE revision_id=(SELECT revision_id FROM clause_assignments LIMIT 1)"
            )
        with self.assertRaisesRegex(
                RuntimeError, "classification_applied_assignment_drift"):
            apply_reclassification(
                self.store, artifact_path=artifact,
                target_config_path=target_path,
            )

    def test_candidate_tamper_and_live_input_drift_both_fail_closed(self) -> None:
        prior, _transcript = self.configured_claude_fixture()
        prior_path = self.write_config(prior, "prior-tamper.json")
        target = config(
            ("project-alpha", ["alpha parser"]),
            ("project-beta", ["beta parser"]),
        )
        target["sources"] = prior["sources"]
        target_path = self.write_config(target, "target-tamper.json")
        artifact = prepare_reclassification(
            self.store, target_config_path=target_path,
            prior_config_path=prior_path,
        )
        envelope = json.loads(artifact.read_text(encoding="utf-8"))
        candidate = artifact.parent / envelope["body"]["candidate_filename"]
        original = candidate.read_bytes()
        candidate.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        before = assignment_table_digest(self.store)
        with self.assertRaisesRegex(
                RuntimeError, "classification_candidate_sha256_mismatch"):
            apply_reclassification(
                self.store, artifact_path=artifact,
                target_config_path=target_path,
            )
        self.assertEqual(before, assignment_table_digest(self.store))
        candidate.write_bytes(original)
        original_target = target_path.read_text(encoding="utf-8")
        changed_target = dict(target)
        changed_target["projects"] = [dict(row) for row in target["projects"]]
        changed_target["projects"][0]["anchors"] = ["changed anchor"]
        target_path.write_text(
            json.dumps(changed_target, indent=2) + "\n",
            encoding="utf-8", newline="\n",
        )
        with self.assertRaisesRegex(
                RuntimeError, "classification_target_config_drift"):
            apply_reclassification(
                self.store, artifact_path=artifact,
                target_config_path=target_path,
            )
        target_path.write_text(original_target, encoding="utf-8", newline="\n")
        self.ingest(
            "A later authoritative owner event.", cfg=prior,
            source="later-source", session="later-session", seq=77)
        with self.assertRaisesRegex(
                RuntimeError, "classification_live_input_drift"):
            apply_reclassification(
                self.store, artifact_path=artifact,
                target_config_path=target_path,
            )
        self.assertEqual(0, self.store.db.execute(
            "SELECT COUNT(*) FROM classification_rebuilds"
        ).fetchone()[0])

    def test_historical_authoritative_insert_requires_explicit_reclassification(self) -> None:
        cfg = config(("project-alpha", ["alpha parser"]))
        self.store.ingest_prompt(
            "Later alpha event.", source_id="source", source_seq=2,
            session="ordered-session", occurred_at="2026-01-01T02:00:00Z",
            provenance="owner", runtime="synthetic", cwd="",
            source_project="", config=cfg,
        )
        before = self.store.db.execute(
            "SELECT COUNT(*) FROM prompt_events"
        ).fetchone()[0]
        with self.assertRaisesRegex(
                RuntimeError,
                "historical_authoritative_event_requires_reclassification"):
            self.store.ingest_prompt(
                "Earlier alpha event.", source_id="source", source_seq=1,
                session="ordered-session",
                occurred_at="2026-01-01T01:00:00Z",
                provenance="owner", runtime="synthetic", cwd="",
                source_project="", config=cfg,
            )
        self.assertEqual(before, self.store.db.execute(
            "SELECT COUNT(*) FROM prompt_events"
        ).fetchone()[0])

    def test_legacy_source_replay_requires_and_survives_explicit_reclassification(
            self) -> None:
        cfg, _transcript = self.configured_claude_fixture()
        legacy = self.store.db.execute(
            "SELECT event_id,source_id,source_seq,occurred_at,runtime "
            "FROM prompt_events ORDER BY source_seq LIMIT 1"
        ).fetchone()
        legacy_event_id = str(legacy["event_id"])
        original_event_count = int(self.store.db.execute(
            "SELECT COUNT(*) FROM prompt_events"
        ).fetchone()[0])
        with self.store.immediate():
            self.store.db.execute(
                "UPDATE prompt_events SET provenance='unknown',"
                "projection_version=4,source_content_hmac=NULL WHERE event_id=?",
                (legacy_event_id,),
            )
        assignments_before = assignment_table_digest(self.store)
        legacy_assignments = int(self.store.db.execute(
            "SELECT COUNT(*) FROM clause_assignments ca JOIN clauses c "
            "ON c.clause_id=ca.clause_id WHERE c.event_id=?",
            (legacy_event_id,),
        ).fetchone()[0])
        self.assertGreater(legacy_assignments, 0)
        with self.assertRaisesRegex(
                RuntimeError,
                "authority_reconciliation_requires_deferred_classification"):
            self.store.ingest_prompt(
                "Implement alpha parser.",
                source_id=str(legacy["source_id"]),
                source_seq=int(legacy["source_seq"]),
                session="source-session",
                occurred_at=str(legacy["occurred_at"]),
                provenance="owner", runtime=str(legacy["runtime"]),
                cwd="", source_project="", config=cfg,
                reconcile_existing_authority=True,
            )
        with self.assertRaisesRegex(
                RuntimeError,
                "historical_authoritative_event_requires_reclassification"):
            self.store.ingest_prompt(
                "Implement alpha parser.",
                source_id=str(legacy["source_id"]),
                source_seq=int(legacy["source_seq"]),
                session="source-session",
                occurred_at=str(legacy["occurred_at"]),
                provenance="owner", runtime=str(legacy["runtime"]),
                cwd="", source_project="", config=cfg,
            )
        self.assertEqual(assignments_before, assignment_table_digest(self.store))

        prior_path = self.write_config(cfg, "legacy-prior.json")
        target_path = self.write_config(cfg, "legacy-target.json")
        artifact = prepare_reclassification(
            self.store, target_config_path=target_path,
            prior_config_path=prior_path,
        )
        self.assertEqual(assignments_before, assignment_table_digest(self.store))
        self.assertEqual(
            original_event_count + 1,
            self.store.db.execute(
                "SELECT COUNT(*) FROM prompt_events"
            ).fetchone()[0],
        )
        quarantine = self.store.db.execute(
            "SELECT source_id,provenance,projection_version FROM prompt_events "
            "WHERE event_id=?",
            (legacy_event_id,),
        ).fetchone()
        self.assertTrue(str(quarantine["source_id"]).startswith(
            "authority-quarantine:"))
        self.assertEqual(("owner", 0), tuple(quarantine)[1:])
        reconciled = self.store.db.execute(
            "SELECT event_id,provenance,projection_version FROM prompt_events "
            "WHERE source_id=? AND source_seq=?",
            (str(legacy["source_id"]), int(legacy["source_seq"])),
        ).fetchone()
        reconciled_event_id = str(reconciled["event_id"])
        self.assertNotEqual(legacy_event_id, reconciled_event_id)
        self.assertEqual(("owner", 5), tuple(reconciled)[1:])
        envelope = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertEqual(1, envelope["body"]["quarantined_event_count"])
        self.assertEqual(
            {
                "existing": 0,
                "incremental_rows": 0,
                "inserted": 1,
                "legacy_candidates": 1,
                "legacy_existing": 0,
                "legacy_inserted": 1,
                "legacy_non_owner": 0,
                "legacy_owner_verified": 1,
                "legacy_resolved": 1,
                "legacy_unavailable": 0,
            },
            envelope["body"]["source_reconciliation"],
        )

        receipt = apply_reclassification(
            self.store, artifact_path=artifact,
            target_config_path=target_path,
        )
        self.assertEqual(1, receipt["quarantined_event_count"])
        self.assertEqual(
            "unknown", self.store.db.execute(
                "SELECT provenance FROM prompt_events WHERE event_id=?",
                (legacy_event_id,),
            ).fetchone()[0],
        )
        self.assertEqual(
            0, self.store.db.execute(
                "SELECT COUNT(*) FROM clause_assignments ca JOIN clauses c "
                "ON c.clause_id=ca.clause_id WHERE c.event_id=?",
                (legacy_event_id,),
            ).fetchone()[0],
        )
        self.assertGreater(
            self.store.db.execute(
                "SELECT COUNT(*) FROM clause_assignments ca JOIN clauses c "
                "ON c.clause_id=ca.clause_id WHERE c.event_id=?",
                (reconciled_event_id,),
            ).fetchone()[0],
            0,
        )
        ineligible = self.store.db.execute(
            "SELECT COUNT(*) FROM clause_assignments ca JOIN clauses c "
            "ON c.clause_id=ca.clause_id JOIN prompt_events p "
            "ON p.event_id=c.event_id WHERE NOT (p.provenance='owner' "
            "AND p.source_id<>'hook' AND p.source_content_hmac IS NOT NULL "
            "AND p.source_content_hmac=p.content_hmac "
            "AND p.projection_version>=5)"
        ).fetchone()[0]
        self.assertEqual(0, ineligible)
        replayed, created = self.store.ingest_prompt(
            "Implement alpha parser.",
            source_id=str(legacy["source_id"]),
            source_seq=int(legacy["source_seq"]),
            session="source-session",
            occurred_at=str(legacy["occurred_at"]),
            provenance="owner", runtime=str(legacy["runtime"]),
            cwd="", source_project="", config=cfg,
        )
        self.assertEqual(reconciled_event_id, replayed)
        self.assertFalse(created)

    def test_hot_cache_refuses_semantically_stale_classification(self) -> None:
        prior = config(("project-alpha", ["alpha parser"]))
        self.ingest("Implement alpha parser.", cfg=prior)
        with self.store.immediate():
            self.store.db.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) "
                "VALUES('classification_config_digest',?)",
                (classification_digest(prior),),
            )
            self.store.db.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) "
                "VALUES('classification_classifier_version',?)",
                (CLASSIFIER_VERSION,),
            )
        changed = config(
            ("project-alpha", ["alpha parser"]),
            ("project-beta", ["beta parser"]),
        )
        with self.assertRaisesRegex(
                RuntimeError,
                "classification_config_changed_requires_explicit_rebuild"):
            build_contract_cache(self.store, changed)

    def test_rebuild_session_prior_uses_only_four_prior_events_and_one_prior_per_event(self) -> None:
        transcript = self.root / "prior-source" / "session.jsonl"
        transcript.parent.mkdir()
        prompts = (
            "[project:project-alpha] Seed alpha.",
            "[project:project-beta] Beta checkpoint one.",
            "[project:project-beta] Beta checkpoint two.",
            "[project:project-beta] Beta checkpoint three.",
            "[project:project-beta] Beta checkpoint four.",
            "Continue the work. Finish the remaining work.",
        )
        transcript.write_text(
            "".join(json.dumps({
                "type": "user", "sessionId": "prior-session",
                "timestamp": "2026-01-01T00:%02d:00Z" % index,
                "cwd": "", "message": {"role": "user", "content": prompt},
            }) + "\n" for index, prompt in enumerate(prompts)),
            encoding="utf-8", newline="\n",
        )
        prior = config(
            ("project-alpha", ["unique-alpha-anchor"]),
            ("project-beta", ["unique-beta-anchor"]),
        )
        prior["sources"] = [{
            "type": "claude", "root": str(transcript.parent),
            "glob": "*.jsonl",
        }]
        rows, _source_rows, pending = scan_sources(self.store, prior)
        for row in rows:
            self.store.ingest_prompt(
                row.text, source_id=row.source_id, source_seq=row.source_seq,
                session=row.session, occurred_at=row.occurred_at,
                provenance=row.provenance, runtime=row.runtime, cwd=row.cwd,
                source_project=row.source_project, config=prior,
            )
        commit_manifests(self.store, pending)
        with self.store.immediate():
            self.store.db.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) "
                "VALUES('classification_config_digest',?)",
                (classification_digest(prior),),
            )
            self.store.db.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) "
                "VALUES('classification_classifier_version',?)",
                (CLASSIFIER_VERSION,),
            )
        target = json.loads(json.dumps(prior))
        target["classification_threshold"] = 1.0
        prior_path = self.write_config(prior, "prior-window.json")
        target_path = self.write_config(target, "target-window.json")
        artifact = prepare_reclassification(
            self.store, target_config_path=target_path,
            prior_config_path=prior_path,
        )
        apply_reclassification(
            self.store, artifact_path=artifact,
            target_config_path=target_path,
        )
        final_event = self.store.db.execute(
            "SELECT event_id FROM prompt_events WHERE source_seq=6"
        ).fetchone()[0]
        per_clause = {
            str(row["clause_id"]): str(row["project_id"])
            for row in self.store.db.execute(
                "SELECT c.clause_id,ca.project_id FROM clauses c "
                "JOIN clause_assignments ca ON ca.clause_id=c.clause_id "
                "WHERE c.event_id=?", (final_event,)
            )
        }
        self.assertGreaterEqual(len(per_clause), 2)
        self.assertEqual({"project-beta"}, set(per_clause.values()))

    def test_projection_drift_and_missing_source_block_prepare(self) -> None:
        prior, transcript = self.configured_claude_fixture()
        prior_path = self.write_config(prior, "prior-block.json")
        target_path = self.write_config(prior, "target-block.json")
        clause_id = self.store.db.execute(
            "SELECT clause_id FROM clauses ORDER BY clause_id LIMIT 1"
        ).fetchone()[0]
        with self.store.immediate():
            self.store.db.execute(
                "UPDATE clauses SET features_json='[]' WHERE clause_id=?",
                (clause_id,),
            )
        with self.assertRaisesRegex(RuntimeError, "projection_clause_drift"):
            prepare_reclassification(
                self.store, target_config_path=target_path,
                prior_config_path=prior_path,
            )
        # Restore a clean store, then prove file presence is required rather
        # than treating the DB HMAC as reconstructable raw text.
        self.store.close()
        clean_root = self.root / "missing-source-store"
        self.store = ProjectMemoryStore(
            root=clean_root, path=clean_root / "state.sqlite3")
        missing_cfg = config(("project-alpha", ["alpha parser"]))
        missing_cfg["sources"] = prior["sources"]
        rows, _source_rows, pending = scan_sources(self.store, missing_cfg)
        for row in rows:
            self.store.ingest_prompt(
                row.text, source_id=row.source_id, source_seq=row.source_seq,
                session=row.session, occurred_at=row.occurred_at,
                provenance=row.provenance, runtime=row.runtime, cwd=row.cwd,
                source_project=row.source_project, config=missing_cfg,
            )
        commit_manifests(self.store, pending)
        with self.store.immediate():
            self.store.db.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) "
                "VALUES('classification_config_digest',?)",
                (classification_digest(missing_cfg),),
            )
            self.store.db.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) "
                "VALUES('classification_classifier_version',?)",
                (CLASSIFIER_VERSION,),
            )
        missing_prior = self.write_config(missing_cfg, "missing-prior.json")
        missing_target = self.write_config(missing_cfg, "missing-target.json")
        transcript.rename(transcript.with_suffix(".gone"))
        with self.assertRaisesRegex(
                RuntimeError, "authoritative_source_unavailable"):
            prepare_reclassification(
                self.store, target_config_path=missing_target,
                prior_config_path=missing_prior,
            )

    def test_prepare_reconstructs_prior_only_when_removed_project_digest_matches(self) -> None:
        prior, _transcript = self.configured_claude_fixture()
        target = config(
            ("project-alpha", ["alpha parser"]),
            ("project-beta", ["beta parser"]),
        )
        target["sources"] = prior["sources"]
        target_path = self.write_config(target, "derived-prior-target.json")
        artifact = prepare_reclassification(
            self.store, target_config_path=target_path,
            prior_without_projects=("project-beta",),
        )
        body = json.loads(artifact.read_text(encoding="utf-8"))["body"]
        self.assertEqual(
            {"removed_projects": ["project-beta"]},
            body["prior_config_derivation"],
        )
        with self.assertRaisesRegex(
                RuntimeError, "prior_config_does_not_match_live_classification"):
            prepare_reclassification(
                self.store, target_config_path=target_path,
                prior_without_projects=("project-alpha",),
            )


if __name__ == "__main__":
    unittest.main()
