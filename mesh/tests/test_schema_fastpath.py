"""Opening a mesh whose claims schema is already current takes no write lock: the check reads sqlite_master and
returns, so a CLI call does not queue behind a busy writer. A missing index or a legacy broadcast row still sends
the open through the full repair.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("agent_mesh_fastpath_test", ROOT / "agent_mesh.py")
agent_mesh = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = agent_mesh
SPEC.loader.exec_module(agent_mesh)


class SchemaFastPathTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "mesh.sqlite3"
        self.mesh = agent_mesh.Mesh(self.db)

    def tearDown(self):
        self.mesh.close()
        self.temp.cleanup()

    def test_fresh_schema_is_current(self):
        self.assertTrue(self.mesh._claims_schema_current())

    def test_current_schema_takes_no_write_lock(self):
        again = agent_mesh.Mesh(self.db, busy_timeout_ms=100)
        other = sqlite3.connect(str(self.db), timeout=0.1, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")
            again._repair_claims_schema()  # would raise "database is locked" if it tried to write
        finally:
            other.execute("ROLLBACK")
            other.close()
            again.close()

    def test_missing_index_is_repaired(self):
        self.mesh.conn.execute("DROP INDEX idx_claims_active")
        self.assertFalse(self.mesh._claims_schema_current())
        self.mesh._repair_claims_schema()
        self.assertTrue(self.mesh._claims_schema_current())

    def test_legacy_broadcast_row_is_repaired(self):
        self.mesh.conn.execute(
            "INSERT INTO messages (message_id, sender_agent, recipient, topic, kind, body, created_at) "
            "VALUES ('m-legacy', 'alice', 'all', 't', 'note', 'old broadcast', '2026-01-01T00:00:00Z')"
        )
        self.assertFalse(self.mesh._claims_schema_current())
        self.mesh._repair_claims_schema()
        self.assertTrue(self.mesh._claims_schema_current())
        self.assertIsNone(self.mesh.conn.execute("SELECT 1 FROM messages WHERE recipient='all'").fetchone())


if __name__ == "__main__":
    unittest.main()
