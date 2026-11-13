"""Opening a mesh whose schema is current takes no write lock, so a CLI call does not queue behind a busy writer.
A message's identity and body are fixed once sent; its lifecycle columns may still change.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("agent_mesh_open_test", ROOT / "agent_mesh.py")
agent_mesh = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = agent_mesh
SPEC.loader.exec_module(agent_mesh)


class OpenNoLockTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "mesh.sqlite3"
        self.mesh = agent_mesh.Mesh(self.db)

    def tearDown(self):
        self.mesh.close()
        self.temp.cleanup()

    def test_open_while_another_writer_holds_the_lock(self):
        other = sqlite3.connect(str(self.db), timeout=0.1, isolation_level=None)
        again = None
        try:
            other.execute("BEGIN IMMEDIATE")
            again = agent_mesh.Mesh(self.db, busy_timeout_ms=100)  # raises "database is locked" if it writes
        finally:
            other.execute("ROLLBACK")
            other.close()
            if again is not None:
                again.close()

    def test_message_body_is_fixed_and_lifecycle_is_not(self):
        self.mesh.hello("alice", "claude", "test", self.temp.name, 600, "active")
        self.mesh.hello("bob", "claude", "test", self.temp.name, 600, "active")
        mid = self.mesh.send("alice", "bob", "t", "report one")["message_id"]
        with self.assertRaises(sqlite3.DatabaseError):
            self.mesh.conn.execute("UPDATE messages SET body='changed' WHERE message_id=?", (mid,))
        self.mesh.conn.execute("UPDATE messages SET delivered_at='2026-01-01T00:00:00Z' WHERE message_id=?", (mid,))
        self.assertEqual(
            self.mesh.conn.execute("SELECT body FROM messages WHERE message_id=?", (mid,)).fetchone()[0], "report one"
        )

    def test_missing_message_index_is_rebuilt(self):
        self.mesh.conn.execute("DROP INDEX idx_messages_open_handoffs")
        self.mesh.close()
        self.mesh = agent_mesh.Mesh(self.db)
        names = {r[0] for r in self.mesh.conn.execute("SELECT name FROM sqlite_master WHERE tbl_name='messages'")}
        self.assertIn("idx_messages_open_handoffs", names)


if __name__ == "__main__":
    unittest.main()
