from __future__ import annotations

import concurrent.futures
import datetime as dt
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "agent_mesh.py"
SPEC = importlib.util.spec_from_file_location("agent_mesh", SCRIPT)
agent_mesh = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
# register before exec: @dataclass resolves its own
# module via sys.modules and dies on None
sys.modules[SPEC.name] = agent_mesh
SPEC.loader.exec_module(agent_mesh)


class MeshTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "mesh.sqlite3"
        self.mesh = agent_mesh.Mesh(self.db)

    def tearDown(self):
        self.mesh.close()
        self.temp.cleanup()

    def hello(self, name: str, task: str = "test"):
        return self.mesh.hello(name, "test", task, r"C:\work", 1800, "working")

    def test_case_insensitive_hierarchical_path_conflict(self):
        self.hello("a", "owns repo")
        self.hello("b", "owns file")
        self.mesh.claim("a", [r"C:\Work\Repo"], "write", "repo edit", 1800)
        with self.assertRaises(agent_mesh.ConflictError) as caught:
            self.mesh.claim("b", [r"c:\work\repo\src\x.py"], "write", "file edit", 1800)
        payload = json.loads(str(caught.exception))
        self.assertEqual(payload["conflicts"][0]["agent"], "a")
        logged = self.mesh.conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='claim_conflict' AND agent_id='b'"
        ).fetchone()[0]
        self.assertEqual(logged, 1)

    def test_relative_path_claims_are_rejected_as_cwd_traps(self):
        # A relative path resolves against the caller's cwd, so the same string from two shells names two
        # resources and the claim protects nothing. It is refused; absolute forms still canonicalize to one
        # resource across spellings.
        self.hello("a")
        self.hello("b")
        relative = str(Path("src") / "file.py")
        absolute = str(Path(relative).resolve())
        with self.assertRaises(agent_mesh.MeshError):
            self.mesh.claim("a", [relative], "write", "relative", 1800)
        with self.assertRaises(agent_mesh.MeshError):
            self.mesh.claim("a", ["file:" + relative], "write", "relative-prefixed", 1800)
        self.mesh.claim("a", [absolute], "write", "absolute", 1800)
        with self.assertRaises(agent_mesh.ConflictError):
            self.mesh.claim("b", ["file:" + absolute.replace("\\", "/")], "write",
                            "same file, other spelling", 1800)

    def test_windows_extended_prefix_and_bare_paths_normalize(self):
        ordinary = agent_mesh.canonical_resource(r"C:\Work\Repo")[0]
        extended = agent_mesh.canonical_resource(r"\\?\C:\Work\Repo")[0]
        self.assertEqual(ordinary, extended)
        self.assertEqual(ordinary, agent_mesh.canonical_resource(r"file:C:\Work\Repo")[0])
        self.assertEqual(ordinary, agent_mesh.canonical_resource(r"path:C:\Work\Repo")[0])
        self.assertEqual(ordinary, agent_mesh.canonical_resource("file:///C:/Work/Repo")[0])
        # bare filenames and drive-relative forms resolve against the caller's cwd: refused
        with self.assertRaises(agent_mesh.MeshError):
            agent_mesh.canonical_resource("file.txt")
        with self.assertRaises(agent_mesh.MeshError):
            agent_mesh.canonical_resource(r"C:file.txt")

    def test_glob_claim_syntax_is_rejected_without_partial_acquisition(self):
        self.hello("a")
        patterns = (
            r"C:\repo\**",
            r"C:\repo\file?.py",
            "git:repo/**",
            "topic:proof/[ab]",
            "topic:proof/{a,b}",
        )
        for resource in patterns:
            with self.subTest(resource=resource):
                with self.assertRaisesRegex(agent_mesh.MeshError, "glob/wildcard"):
                    self.mesh.claim("a", ["topic:otherwise-free", resource], "write", "bad pattern", 1800)
                active = self.mesh.conn.execute(
                    "SELECT COUNT(*) FROM claims WHERE agent_id='a' AND released_at IS NULL"
                ).fetchone()[0]
                self.assertEqual(active, 0)

        # These question marks are syntax, not glob patterns: the Windows
        # extended-path prefix and a normal URL query must remain claimable.
        self.mesh.claim("a", [r"\\?\C:\Work\Repo"], "write", "extended path", 1800)
        self.mesh.claim(
            "a", ["url:https://example.test/path?version=1"], "write", "URL query", 1800
        )
        with self.assertRaisesRegex(agent_mesh.MeshError, "glob/wildcard"):
            self.mesh.assert_claim("a", [r"C:\Work\Repo\*.py"])

    def test_read_and_write_coexist_but_exclusive_conflicts(self):
        for name in ("reader", "writer", "exclusive"):
            self.hello(name)
        self.mesh.claim("reader", ["topic:proof/a"], "read", "audit", 1800)
        self.mesh.claim("writer", ["topic:proof/a"], "write", "implementation", 1800)
        with self.assertRaises(agent_mesh.ConflictError):
            self.mesh.claim("exclusive", ["topic:proof"], "exclusive", "migration", 1800)

    def test_multi_resource_claim_is_atomic(self):
        self.hello("a")
        self.hello("b")
        self.mesh.claim("a", ["topic:held"], "write", "held", 1800)
        with self.assertRaises(agent_mesh.ConflictError):
            self.mesh.claim("b", ["topic:free", "topic:held"], "write", "two resources", 1800)
        count = self.mesh.conn.execute(
            "SELECT COUNT(*) FROM claims WHERE agent_id='b' AND released_at IS NULL"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_expired_claim_is_pruned_and_reacquired(self):
        self.hello("a")
        self.hello("b")
        self.mesh.claim("a", ["topic:ephemeral"], "write", "short", 1800)
        old = agent_mesh.iso(agent_mesh.utc_now() - dt.timedelta(seconds=1))
        self.mesh.conn.execute("UPDATE claims SET expires_at=? WHERE agent_id='a'", (old,))
        result = self.mesh.claim("b", ["topic:ephemeral"], "write", "recovery", 1800)
        self.assertEqual(result["agent"], "b")
        released = self.mesh.conn.execute(
            "SELECT released_at FROM claims WHERE agent_id='a'"
        ).fetchone()[0]
        self.assertIsNotNone(released)

    def test_rapid_release_reclaim_release_has_no_timestamp_collision(self):
        self.hello("a")
        self.mesh.claim("a", ["topic:rapid"], "write", "first", 1800)
        self.mesh.release("a", ["topic:rapid"])
        self.mesh.claim("a", ["topic:rapid"], "write", "second", 1800)
        released = self.mesh.release("a", ["topic:rapid"])
        self.assertEqual(released["released"], 1)
        rows = self.mesh.conn.execute(
            "SELECT released_at FROM claims WHERE agent_id='a' ORDER BY rowid"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["released_at"] for row in rows))

    def test_pulse_renews_agent_and_claim(self):
        self.hello("a")
        self.mesh.claim("a", ["topic:pulse"], "write", "work", 60)
        almost_expired = agent_mesh.iso(agent_mesh.utc_now() + dt.timedelta(seconds=1))
        self.mesh.conn.execute("UPDATE claims SET expires_at=? WHERE agent_id='a'", (almost_expired,))
        self.mesh.conn.execute(
            "UPDATE agents SET ttl_seconds=1800 WHERE agent_id='a'"
        )
        self.mesh.pulse("a", "still working", "working")
        renewed = self.mesh.conn.execute(
            "SELECT expires_at FROM claims WHERE agent_id='a' AND released_at IS NULL"
        ).fetchone()[0]
        remaining = (agent_mesh.parse_iso(renewed) - agent_mesh.utc_now()).total_seconds()
        self.assertGreaterEqual(remaining, 58)
        self.assertLessEqual(remaining, 61)  # claim TTL, not the agent's 30-minute TTL

    def test_claim_cannot_outlive_agent_and_prune_releases_owner_claim(self):
        self.mesh.hello("a", "test", "work", r"C:\work", 2, "working")
        self.mesh.claim("a", ["topic:owner-bound"], "write", "work", 300)
        claim_until = self.mesh.conn.execute(
            "SELECT expires_at FROM claims WHERE agent_id='a' AND released_at IS NULL"
        ).fetchone()[0]
        agent_until = self.mesh.conn.execute(
            "SELECT lease_until FROM agents WHERE agent_id='a'"
        ).fetchone()[0]
        self.assertLessEqual(claim_until, agent_until)
        old = agent_mesh.iso(agent_mesh.utc_now() - dt.timedelta(seconds=1))
        future = agent_mesh.iso(agent_mesh.utc_now() + dt.timedelta(minutes=5))
        self.mesh.conn.execute("UPDATE agents SET lease_until=? WHERE agent_id='a'", (old,))
        self.mesh.conn.execute("UPDATE claims SET expires_at=? WHERE agent_id='a'", (future,))
        result = self.mesh.prune()
        self.assertEqual(result["offline_agents"], 1)
        self.assertEqual(result["expired_claims"], 1)
        self.assertEqual(len(self.mesh.board()["claims"]), 0)

    def test_pulse_rejects_final_status_so_claims_cannot_turn_invisible(self):
        self.hello("a")
        self.mesh.claim("a", ["topic:visible"], "write", "work", 1800)
        with self.assertRaises(agent_mesh.MeshError):
            self.mesh.pulse("a", status="done")
        self.assertEqual(len(self.mesh.board()["claims"]), 1)

    def test_assert_fences_expired_or_missing_write_claim(self):
        self.hello("a")
        self.mesh.claim("a", [r"C:\repo"], "write", "work", 1800)
        ok = self.mesh.assert_claim("a", [r"C:\repo\x.py"])
        self.assertEqual(ok["claims"][0]["claim_resource"], r"C:\repo")
        old = agent_mesh.iso(agent_mesh.utc_now() - dt.timedelta(seconds=1))
        self.mesh.conn.execute("UPDATE claims SET expires_at=? WHERE agent_id='a'", (old,))
        with self.assertRaises(agent_mesh.ConflictError):
            self.mesh.assert_claim("a", [r"C:\repo\x.py"])

    def test_mailbox_delivery_and_ack(self):
        self.hello("a")
        self.hello("b")
        sent = self.mesh.send("a", "b", "progress", "artifact ready", "message")
        messages = self.mesh.inbox("b", mark_delivered=True)
        self.assertEqual([m["message_id"] for m in messages], [sent["message_id"]])
        self.mesh.ack("b", [sent["message_id"]])
        self.assertEqual(self.mesh.inbox("b"), [])

    def test_mailbox_preserves_database_delivery_order_with_same_second_timestamps(self):
        self.hello("a")
        self.hello("b")
        for number in range(30):
            self.mesh.send("a", "b", "ordered", f"{number:03d}", "message")
        self.assertEqual(
            [row["body"] for row in self.mesh.inbox("b")],
            [f"{number:03d}" for number in range(30)],
        )

    def test_broadcast_is_visible_to_every_agent(self):
        for name in ("a", "b", "c"):
            self.hello(name)
        sent = self.mesh.send("a", "all", "warning", "integration freeze", "warning")
        self.assertEqual(sent["fanout"], 2)
        b_message = self.mesh.inbox("b")[0]
        self.mesh.ack("b", [b_message["message_id"]])
        self.assertEqual(self.mesh.inbox("b"), [])
        self.assertEqual(len(self.mesh.inbox("c")), 1)  # b's ack cannot consume c's copy

    def test_cli_hello_defaults_session_from_environment(self):
        with mock.patch.dict(agent_mesh.os.environ, {"AGENT_MESH_SESSION": "thread-from-env"}):
            args = agent_mesh.build_parser().parse_args(["hello", "--runtime", "codex"])
        self.assertEqual(args.session, "thread-from-env")

    def test_live_runtime_session_rejects_alias_but_allows_reuse_after_final_bye(self):
        self.mesh.hello("canonical", "codex", "work", r"C:\work", 1800, "working",
                        session="thread-1")
        with self.assertRaisesRegex(agent_mesh.ConflictError, "canonical id"):
            self.mesh.hello("alias", "codex", "same work", r"C:\work", 1800, "working",
                            session="thread-1")
        self.mesh.bye("canonical", "done", "finished", release=True)
        revived = self.mesh.hello("alias", "codex", "new owner", r"C:\work", 1800,
                                  "working", session="thread-1")
        self.assertEqual(revived["agent"], "alias")

    def test_concurrent_alias_registration_has_exactly_one_canonical_winner(self):
        barrier = threading.Barrier(12)

        def contender(index):
            local = agent_mesh.Mesh(self.db)
            try:
                barrier.wait()
                try:
                    local.hello(f"alias-{index}", "codex", "work", r"C:\work", 1800,
                                "working", session="one-runtime-thread")
                    return "won"
                except agent_mesh.ConflictError:
                    return "conflict"
            finally:
                local.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(contender, range(12)))
        self.assertEqual(results.count("won"), 1)
        self.assertEqual(results.count("conflict"), 11)
        count = self.mesh.conn.execute(
            "SELECT COUNT(*) FROM agents WHERE runtime='codex' AND session_key='one-runtime-thread' "
            "AND status NOT IN ('done','offline')"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_message_schema_migrates_legacy_ack_semantics(self):
        legacy = Path(self.temp.name) / "legacy.sqlite3"
        con = sqlite3.connect(legacy)
        con.execute(
            "CREATE TABLE messages(message_id TEXT PRIMARY KEY,sender_agent TEXT NOT NULL,"
            "recipient TEXT NOT NULL,topic TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,"
            "created_at TEXT NOT NULL,delivered_at TEXT,acked_at TEXT)"
        )
        for message_id, kind in (("legacy-message", "message"),
                                 ("legacy-request", "request"),
                                 ("legacy-warning", "warning"),
                                 ("legacy-handoff", "handoff")):
            con.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,NULL,NULL)",
                        (message_id, "a", "b", "topic", kind, "body", agent_mesh.iso()))
        con.commit()
        con.close()
        migrated = agent_mesh.Mesh(legacy)
        try:
            columns = {row["name"] for row in migrated.conn.execute("PRAGMA table_info(messages)")}
            self.assertIn("requires_ack", columns)
            self.assertIn("idempotency_key", columns)
            values = dict(migrated.conn.execute(
                "SELECT message_id,requires_ack FROM messages ORDER BY message_id"
            ).fetchall())
            self.assertEqual(values["legacy-message"], 0)
            self.assertEqual(values["legacy-request"], 1)
            self.assertEqual(values["legacy-warning"], 1)
            self.assertEqual(values["legacy-handoff"], 1)
        finally:
            migrated.close()

    def test_idempotency_key_reuses_message_and_rejects_payload_drift(self):
        self.hello("a")
        self.hello("b")
        first = self.mesh.send("a", "b", "progress", "same payload", "request",
                               idempotency_key="attempt-1")
        replay = self.mesh.send("a", "b", "progress", "same payload", "request",
                                idempotency_key="attempt-1")
        self.assertEqual(first["message_id"], replay["message_id"])
        self.assertEqual((first["inserted"], first["reused"]), (1, 0))
        self.assertEqual((replay["inserted"], replay["reused"]), (0, 1))
        self.assertTrue(replay["idempotent_replay"])
        with self.assertRaises(agent_mesh.ConflictError):
            self.mesh.send("a", "b", "progress", "changed payload", "request",
                           idempotency_key="attempt-1")
        count = self.mesh.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE sender_agent='a' AND recipient='b' "
            "AND idempotency_key='attempt-1'"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_concurrent_idempotent_send_inserts_exactly_one_row(self):
        self.hello("a")
        self.hello("b")
        barrier = threading.Barrier(16)

        def contender(_index):
            local = agent_mesh.Mesh(self.db)
            try:
                barrier.wait()
                return local.send("a", "b", "wake", "continue bounded task", "request",
                                  idempotency_key="wake-generation-7")
            finally:
                local.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(contender, range(16)))
        self.assertEqual(len({result["message_id"] for result in results}), 1)
        self.assertEqual(sum(result["inserted"] for result in results), 1)
        self.assertEqual(sum(result["reused"] for result in results), 15)
        count = self.mesh.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE idempotency_key='wake-generation-7'"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_broadcast_idempotency_reuses_each_recipient_copy(self):
        for name in ("a", "b", "c"):
            self.hello(name)
        first = self.mesh.send("a", "all", "notice", "one fanout", "message",
                               idempotency_key="broadcast-1")
        replay = self.mesh.send("a", "all", "notice", "one fanout", "message",
                                idempotency_key="broadcast-1")
        self.assertEqual(first["inserted"], 2)
        self.assertEqual(replay["reused"], 2)
        self.assertEqual(set(first["message_ids"]), set(replay["message_ids"]))
        self.assertEqual(self.mesh.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE idempotency_key='broadcast-1'"
        ).fetchone()[0], 2)

    def test_fire_once_digest_ack_required_receipts_and_backlog(self):
        self.hello("a")
        self.hello("b")
        notice = self.mesh.send("a", "b", "notice", "informational", "message")
        request = self.mesh.send("a", "b", "request", "please confirm", "request")
        self.assertFalse(notice["requires_ack"])
        self.assertTrue(request["requires_ack"])
        queued = self.mesh.receipts("a", [notice["message_id"], request["message_id"]])
        self.assertEqual([row["state"] for row in queued["receipts"]], ["queued", "queued"])

        first_digest = self.mesh.digest("b", mark_delivered=True)
        self.assertIn(notice["message_id"], first_digest)
        self.assertIn(request["message_id"], first_digest)
        second_digest = self.mesh.digest("b", mark_delivered=True)
        self.assertNotIn(notice["message_id"], second_digest)
        self.assertIn(request["message_id"], second_digest)

        after_delivery = self.mesh.receipts(
            "a", [notice["message_id"], request["message_id"]]
        )["receipts"]
        self.assertEqual([row["state"] for row in after_delivery],
                         ["delivered", "awaiting_ack"])
        self.assertNotIn("body", after_delivery[0])
        board = self.mesh.board()
        self.assertEqual(board["message_summary"]["pending"], 1)
        self.assertEqual(board["message_summary"]["queued"], 0)
        self.assertEqual(board["message_summary"]["awaiting_ack"], 1)
        self.assertEqual(board["message_backlog"][0]["recipient"], "b")
        self.assertTrue(board["message_backlog"][0]["recipient_active"])

        self.mesh.ack("b", [request["message_id"]])
        self.assertEqual(self.mesh.board()["message_summary"]["pending"], 0)
        # Explicit history remains inspectable until retention purge even though
        # automatic digests correctly deliver the fire-once notice only once.
        self.assertEqual([row["message_id"] for row in self.mesh.inbox("b")],
                         [notice["message_id"]])

    def test_receipts_are_sender_authorized_and_show_stale_recipient(self):
        for name in ("a", "b", "c"):
            self.hello(name)
        sent = self.mesh.send("a", "b", "check", "ack me", "request")
        with self.assertRaises(agent_mesh.MissingError):
            self.mesh.receipts("c", [sent["message_id"]])
        self.mesh.bye("b", "offline", "went away", release=True)
        receipt = self.mesh.receipts("a", [sent["message_id"]])["receipts"][0]
        self.assertEqual(receipt["state"], "queued")
        self.assertFalse(receipt["recipient_active"])
        board_row = self.mesh.board()["message_backlog"][0]
        self.assertFalse(board_row["recipient_active"])
        self.assertIn("inactive/stale", agent_mesh.markdown_board(self.mesh.board()))

    def test_purge_removes_completed_fire_once_delivery(self):
        self.hello("a")
        self.hello("b")
        sent = self.mesh.send("a", "b", "notice", "done after delivery", "message")
        self.mesh.inbox("b", mark_delivered=True, pending_only=True)
        old = agent_mesh.iso(agent_mesh.utc_now() - dt.timedelta(days=2))
        self.mesh.conn.execute("UPDATE messages SET delivered_at=? WHERE message_id=?",
                               (old, sent["message_id"]))
        purged = self.mesh.purge(86400, 90 * 86400, 90 * 86400)
        self.assertEqual(purged["completed_messages_deleted"], 1)
        self.assertIsNone(self.mesh.conn.execute(
            "SELECT 1 FROM messages WHERE message_id=?", (sent["message_id"],)
        ).fetchone())

    def test_obvious_secrets_are_rejected(self):
        self.hello("a")
        self.hello("b")
        with self.assertRaises(agent_mesh.MeshError):
            self.mesh.send("a", "b", "credential", "api_key=abcdefghijklmnopqrstuv", "message")
        with self.assertRaises(agent_mesh.MeshError):
            self.mesh.send("a", "b", "credential", 'api_key="abcdefghijklmnopqrstuv"', "message")

    def test_legacy_peer_text_is_redacted_at_render_time(self):
        self.hello("sender")
        self.hello("receiver")
        secret_shapes = [
            "api_key=abcdefghijklmnopqrstuv",
            "OPENAI_API_KEY=" + "Z" * 24,
            "xoxb-" + "A" * 32,
            "AIza" + "B" * 35,
            "eyJ" + "C" * 12 + "." + "D" * 12 + "." + "E" * 12,
            "-----BEGIN " + "PRIVATE KEY-----\n" + "Q" * 64 + "\n-----END PRIVATE KEY-----",
        ]
        legacy_body = "safe prefix | " + " | ".join(secret_shapes) + " | safe suffix"
        self.mesh.conn.execute(
            "INSERT INTO messages(message_id,sender_agent,recipient,topic,kind,body,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("msg-legacy-secret", "sender", "receiver", "legacy", "message", legacy_body,
             agent_mesh.iso()),
        )

        digest = self.mesh.digest("receiver", mark_delivered=False)
        self.assertIn("safe prefix", digest)
        self.assertIn("safe suffix", digest)
        self.assertGreaterEqual(digest.count("[redacted]"), len(secret_shapes))
        for secret in secret_shapes:
            self.assertNotIn(secret, digest)

        # The expanded hard shapes are rejected for new rows too.
        for secret in secret_shapes:
            with self.subTest(secret_shape=secret[:12]):
                with self.assertRaises(agent_mesh.MeshError):
                    self.mesh.send("sender", "receiver", "credential-shape", secret, "message")

    def test_render_redaction_preserves_announced_hash_evidence(self):
        sha = "a" * 40
        announced = agent_mesh.peer_text("commit sha256=" + sha)
        opaque = agent_mesh.peer_text("opaque material " + sha)
        self.assertIn(sha, announced)
        self.assertNotIn(sha, opaque)
        self.assertIn("[redacted]", opaque)

    def test_duplicate_resources_in_one_request_create_one_claim(self):
        self.hello("a")
        self.mesh.claim("a", [r"C:\Work\Repo", r"c:\work\repo\."], "write", "one", 1800)
        count = self.mesh.conn.execute(
            "SELECT COUNT(*) FROM claims WHERE agent_id='a' AND released_at IS NULL"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_task_take_is_atomic_and_owner_protected(self):
        self.hello("a")
        self.hello("b")
        task = self.mesh.task_add("audit", "file x", "", "", 50, "a")
        self.mesh.task_take(task["task_id"], "a")
        with self.assertRaises(agent_mesh.ConflictError):
            self.mesh.task_take(task["task_id"], "b")
        with self.assertRaises(agent_mesh.ConflictError):
            self.mesh.task_set(task["task_id"], "b", "done", "no")
        done = self.mesh.task_set(task["task_id"], "a", "done", "verified")
        self.assertEqual(done["status"], "done")

    def test_open_status_and_crashed_owner_both_release_task_ownership(self):
        self.hello("a")
        self.hello("b")
        task = self.mesh.task_add("audit", "x", "a", "", 50, "a")
        reopened = self.mesh.task_set(task["task_id"], "a", "open", "returning to board")
        self.assertIsNone(reopened["owner"])
        self.mesh.task_take(task["task_id"], "b")
        old = agent_mesh.iso(agent_mesh.utc_now() - dt.timedelta(seconds=1))
        self.mesh.conn.execute("UPDATE agents SET lease_until=? WHERE agent_id='b'", (old,))
        pruned = self.mesh.prune()
        self.assertEqual(pruned["orphaned_tasks"], 1)
        row = self.mesh.conn.execute("SELECT owner_agent,status FROM tasks WHERE task_id=?", (task["task_id"],)).fetchone()
        self.assertIsNone(row["owner_agent"])
        self.assertEqual(row["status"], "open")

    def test_campaign_membership_and_deadline_gate_task_take(self):
        self.hello("owner-a")
        self.hello("owner-b")
        self.hello("worker-b")
        self.hello("member-a")
        ca = self.mesh.campaign_start("campaign-a", "a", "owner-a", 1)
        cb = self.mesh.campaign_start("campaign-b", "b", "owner-b", 1)
        self.mesh.campaign_join(ca["campaign_id"], "member-a")
        self.mesh.campaign_join(cb["campaign_id"], "worker-b")
        with self.assertRaises(agent_mesh.ConflictError):
            self.mesh.task_add("member decomposition", "", "", ca["campaign_id"], 50, "member-a")
        task = self.mesh.task_add("only a", "", "", ca["campaign_id"], 50, "owner-a")
        with self.assertRaises(agent_mesh.ConflictError):
            self.mesh.task_take(task["task_id"], "worker-b")
        self.mesh.conn.execute(
            "UPDATE campaigns SET ends_at=? WHERE campaign_id=?",
            (agent_mesh.iso(agent_mesh.utc_now() - dt.timedelta(seconds=1)), ca["campaign_id"]),
        )
        with self.assertRaises(agent_mesh.MissingError):
            self.mesh.campaign_join(ca["campaign_id"], "worker-b")

    def test_live_identity_cannot_be_rebound_to_other_session_or_runtime(self):
        self.mesh.hello("stable", "claude", "work", r"C:\work", 1800, "working", session="session-a")
        with self.assertRaises(agent_mesh.ConflictError):
            self.mesh.hello("stable", "claude", "hijack", r"C:\work", 1800, "working", session="session-b")
        with self.assertRaises(agent_mesh.ConflictError):
            self.mesh.hello("stable", "codex", "hijack", r"C:\work", 1800, "working", session="session-a")

    def test_final_agent_and_reserved_all_cannot_work(self):
        with self.assertRaises(agent_mesh.MeshError):
            self.mesh.hello("all", "test", "reserved", r"C:\work", 1800, "working")
        self.hello("a")
        self.mesh.bye("a", "done", "finished", release=True)
        with self.assertRaises(agent_mesh.ConflictError):
            self.mesh.claim("a", ["topic:invisible"], "write", "bad", 1800)

    def test_unowned_task_requires_take_before_set(self):
        self.hello("a")
        task = self.mesh.task_add("audit", "x", "", "", 50, "a")
        with self.assertRaises(agent_mesh.ConflictError):
            self.mesh.task_set(task["task_id"], "a", "working", "")
        self.mesh.task_take(task["task_id"], "a")
        self.assertEqual(self.mesh.task_set(task["task_id"], "a", "blocked", "waiting")["status"],
                         "blocked")

    def test_structured_handoff_requires_explicit_acceptance(self):
        self.hello("a")
        self.hello("b")
        sent = self.mesh.handoff("a", "b", "parser", "fixed", ["commit:abc"], "tests pass", "none known", "review")
        handoff = self.mesh.inbox("b")[0]
        payload = json.loads(handoff["body"])
        self.assertEqual(payload["verification"], "tests pass")
        with self.assertRaises(agent_mesh.ConflictError):
            self.mesh.ack("b", [sent["message_id"]])
        accepted = self.mesh.handoff_response("b", sent["message_id"], True, "commit inspected")
        self.assertTrue(accepted["accepted"])
        self.assertTrue(any("handoff-accepted" in m["topic"] for m in self.mesh.inbox("a")))

    def test_pending_handoff_blocks_release_and_bye_until_cancelled(self):
        self.hello("a")
        self.hello("b")
        self.mesh.claim("a", ["topic:handoff-owned"], "write", "work", 1800)
        sent = self.mesh.handoff(
            "a", "b", "parser", "fixed", ["commit:abc"], "tests pass", "none known", "review"
        )
        with self.assertRaises(agent_mesh.ConflictError):
            self.mesh.release("a", all_claims=True)
        with self.assertRaises(agent_mesh.ConflictError):
            self.mesh.bye("a", "done", "finished", release=True)
        cancelled = self.mesh.handoff_cancel("a", sent["message_id"], "recipient unavailable")
        self.assertTrue(cancelled["cancelled"])
        self.assertEqual(self.mesh.release("a", all_claims=True)["released"], 1)

    def test_max_length_handoff_topics_finish_atomically(self):
        self.hello("a")
        self.hello("b")
        topic = "x" * agent_mesh.MAX_SHORT

        accepted = self.mesh.handoff("a", "b", topic, "one", ["artifact:one"], "ok", "none", "inspect")
        accept_result = self.mesh.handoff_response("b", accepted["message_id"], True, "accepted")
        rejected = self.mesh.handoff("a", "b", topic, "two", ["artifact:two"], "ok", "none", "inspect")
        reject_result = self.mesh.handoff_response("b", rejected["message_id"], False, "rejected")
        cancelled = self.mesh.handoff("a", "b", topic, "three", ["artifact:three"], "ok", "none", "inspect")
        cancel_result = self.mesh.handoff_cancel("a", cancelled["message_id"], "cancelled")

        for original_id, terminal_id in (
            (accepted["message_id"], accept_result["response_message_id"]),
            (rejected["message_id"], reject_result["response_message_id"]),
            (cancelled["message_id"], cancel_result["notice_message_id"]),
        ):
            original = self.mesh.conn.execute(
                "SELECT acked_at FROM messages WHERE message_id=?", (original_id,)
            ).fetchone()
            terminal = self.mesh.conn.execute(
                "SELECT topic FROM messages WHERE message_id=?", (terminal_id,)
            ).fetchone()
            self.assertIsNotNone(original["acked_at"])
            self.assertIsNotNone(terminal)
            self.assertLessEqual(len(terminal["topic"]), agent_mesh.MAX_SHORT)

    def test_ack_and_release_missing_targets_are_nonzero_semantics(self):
        self.hello("a")
        with self.assertRaises(agent_mesh.MissingError):
            self.mesh.ack("a", ["msg-does-not-exist"])
        with self.assertRaises(agent_mesh.MissingError):
            self.mesh.release("a", ["topic:not-held"])

    def test_campaign_digest_and_markdown_board(self):
        self.hello("coordinator")
        camp = self.mesh.campaign_start("ten-hour-test", "coordinate everyone", "coordinator", 10)
        self.mesh.task_add("stress test", "mesh", "coordinator", camp["campaign_id"], 10, "coordinator")
        digest = self.mesh.digest("coordinator")
        self.assertIn("ten-hour-test", digest)
        rendered = agent_mesh.markdown_board(self.mesh.board())
        self.assertIn("# Agent mesh board", rendered)
        self.assertIn("stress test", rendered)

    def test_bye_releases_claims(self):
        self.hello("a")
        self.mesh.claim("a", ["topic:done"], "write", "work", 1800)
        result = self.mesh.bye("a", "done", "finished", release=True)
        self.assertEqual(result["released"], 1)
        self.assertEqual(len(self.mesh.board()["claims"]), 0)

    def test_doctor_uses_rollback_journal_and_integrity_passes(self):
        result = self.mesh.doctor()
        self.assertEqual(result["integrity"], "ok")
        self.assertEqual(result["journal_mode"], "delete")

    def test_concurrent_claimers_have_exactly_one_winner(self):
        names = [f"agent-{n}" for n in range(8)]
        for name in names:
            self.hello(name)
        barrier = threading.Barrier(len(names))

        def compete(name: str) -> str:
            local = agent_mesh.Mesh(self.db)
            try:
                barrier.wait(timeout=10)
                local.claim(name, ["topic:one-winner"], "write", "race", 1800)
                return "won"
            except agent_mesh.ConflictError:
                return "conflict"
            finally:
                local.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(names)) as pool:
            outcomes = list(pool.map(compete, names))
        self.assertEqual(outcomes.count("won"), 1, outcomes)
        self.assertEqual(outcomes.count("conflict"), len(names) - 1, outcomes)

    def test_cli_conflict_exit_code_is_three(self):
        def run(*args):
            return subprocess.run(
                [sys.executable, str(SCRIPT), "--db", str(self.db), *args],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        run("hello", "--agent", "cli-a", "--runtime", "test", "--task", "a")
        run("hello", "--agent", "cli-b", "--runtime", "test", "--task", "b")
        self.assertEqual(
            run("claim", "--agent", "cli-a", "--resource", "topic:cli", "--purpose", "a").returncode,
            0,
        )
        conflict = run("claim", "--agent", "cli-b", "--resource", "topic:cli", "--purpose", "b")
        self.assertEqual(conflict.returncode, 3, conflict.stderr)
        self.assertIn("claim_conflict", conflict.stderr)

    def test_cli_idempotent_send_and_sender_receipts_roundtrip(self):
        self.hello("cli-sender")
        self.hello("cli-recipient")

        def run(*args):
            return subprocess.run(
                [sys.executable, str(SCRIPT), "--db", str(self.db), "--json", *args],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )

        command = (
            "send", "--agent", "cli-sender", "--to", "cli-recipient",
            "--kind", "message", "--require-ack", "--topic", "cli-check",
            "--body", "bounded payload", "--idempotency-key", "cli-generation-1",
        )
        first = run(*command)
        replay = run(*command)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(replay.returncode, 0, replay.stderr)
        first_payload = json.loads(first.stdout)
        replay_payload = json.loads(replay.stdout)
        self.assertEqual(first_payload["message_id"], replay_payload["message_id"])
        self.assertEqual((first_payload["inserted"], replay_payload["reused"]), (1, 1))

        status = run("receipts", "--agent", "cli-sender", first_payload["message_id"])
        self.assertEqual(status.returncode, 0, status.stderr)
        receipt = json.loads(status.stdout)["receipts"][0]
        self.assertEqual(receipt["state"], "queued")
        self.assertTrue(receipt["requires_ack"])
        self.assertNotIn("body", receipt)

    def test_cli_rejects_out_of_range_integer_without_traceback(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--db", str(self.db), "task-take", "--agent", "a", "9" * 100],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)

    def test_json_digest_is_valid_json_and_status_write_requires_claim(self):
        self.hello("cli-a")
        digest = subprocess.run(
            [sys.executable, str(SCRIPT), "--db", str(self.db), "--json",
             "digest", "--agent", "cli-a"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(digest.returncode, 0, digest.stderr)
        self.assertIsInstance(json.loads(digest.stdout), str)

        board = Path(self.temp.name) / "BOARD.md"
        denied = subprocess.run(
            [sys.executable, str(SCRIPT), "--db", str(self.db),
             "status", "--write", str(board)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(denied.returncode, 2)
        self.assertFalse(board.exists())
        self.mesh.claim("cli-a", [str(board)], "write", "publish board", 1800)
        allowed = subprocess.run(
            [sys.executable, str(SCRIPT), "--db", str(self.db),
             "status", "--agent", "cli-a", "--write", str(board)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn("# Agent mesh board", board.read_text(encoding="utf-8"))
        self.assertIn(
            "task-add --agent coordinator",
            (ROOT / "PROTOCOL.md").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
