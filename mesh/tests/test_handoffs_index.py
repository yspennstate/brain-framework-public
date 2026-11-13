"""Open handoffs are found through an index, never a scan of every message, and a transaction that ends in any
exception (KeyboardInterrupt included) is rolled back, so the connection is never left holding the write lock.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("agent_mesh_handoff_test", ROOT / "agent_mesh.py")
agent_mesh = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = agent_mesh
SPEC.loader.exec_module(agent_mesh)


class HandoffIndexTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.mesh = agent_mesh.Mesh(Path(self.temp.name) / "mesh.sqlite3")
        for name in ("alice", "bob", "carol"):
            self.mesh.hello(name, "claude", "test", self.temp.name, 600, "active")

    def tearDown(self):
        self.mesh.close()
        self.temp.cleanup()

    def test_plan_uses_no_full_scan(self):
        sql = ("SELECT * FROM messages WHERE rowid IN ("
               "SELECT rowid FROM messages WHERE kind='handoff' AND acked_at IS NULL AND sender_agent=? UNION "
               "SELECT rowid FROM messages WHERE kind='handoff' AND acked_at IS NULL AND recipient=?) ORDER BY rowid")
        plan = " | ".join(r[-1] for r in self.mesh.conn.execute("EXPLAIN QUERY PLAN " + sql, ("alice", "alice")))
        self.assertNotRegex(plan, r"SCAN messages(?! USING)", plan)

    def test_both_directions_in_order(self):
        for _ in range(3):
            self.mesh.send("bob", "carol", "t", "unrelated note")
        out = self.mesh.send("alice", "bob", "t", "take this", kind="handoff")["message_id"]
        inc = self.mesh.send("carol", "alice", "t", "take that", kind="handoff")["message_id"]
        both = self.mesh._pending_handoffs("alice", outgoing=True, incoming=True)
        self.assertEqual([r["message_id"] for r in both], [out, inc])
        self.assertEqual([r["message_id"] for r in self.mesh._pending_handoffs("alice", outgoing=True)], [out])
        self.assertEqual([r["message_id"] for r in self.mesh._pending_handoffs("alice", incoming=True)], [inc])
        self.assertEqual(self.mesh._pending_handoffs("alice"), [])

    def test_interrupt_rolls_back(self):
        with self.assertRaises(KeyboardInterrupt):
            with self.mesh.write_tx():
                self.mesh.conn.execute("UPDATE agents SET task='changed' WHERE agent_id='alice'")
                raise KeyboardInterrupt
        self.assertFalse(self.mesh.conn.in_transaction)
        task = self.mesh.conn.execute("SELECT task FROM agents WHERE agent_id='alice'").fetchone()[0]
        self.assertEqual(task, "test")


if __name__ == "__main__":
    unittest.main()
