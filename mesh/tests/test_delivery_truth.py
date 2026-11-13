"""A message counts as delivered when its text was printed, not when it was fetched.

digest_with_ids fetches without stamping; a crash before mark_delivered leaves the message pending for the next
tick. mark_delivered stamps only the recipient's own messages and keeps the first stamp.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("agent_mesh_delivery_test", ROOT / "agent_mesh.py")
agent_mesh = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = agent_mesh
SPEC.loader.exec_module(agent_mesh)


class DeliveryTruthTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.mesh = agent_mesh.Mesh(Path(self.temp.name) / "mesh.sqlite3")
        for agent in ("alice", "bob", "carol"):
            self.mesh.hello(agent, "claude", "test", self.temp.name, 600, "active")
        self.mid = self.mesh.send("alice", "bob", "t", "report one")["message_id"]

    def tearDown(self):
        self.mesh.close()
        self.temp.cleanup()

    def pending(self, agent):
        return [m["message_id"] for m in self.mesh.inbox(agent, mark_delivered=False, pending_only=True)]

    def test_fetch_alone_does_not_deliver(self):
        text, ids = self.mesh.digest_with_ids("bob")
        self.assertIn("report one", text)
        self.assertEqual(ids, [self.mid])
        # the caller died before printing: the message must still be there next tick
        self.assertEqual(self.pending("bob"), [self.mid])
        text2, ids2 = self.mesh.digest_with_ids("bob")
        self.assertEqual(ids2, [self.mid])

    def test_mark_delivered_after_emit(self):
        _, ids = self.mesh.digest_with_ids("bob")
        self.assertEqual(self.mesh.mark_delivered("bob", ids), 1)
        self.assertEqual(self.pending("bob"), [])
        first = self.mesh.conn.execute("SELECT delivered_at FROM messages WHERE message_id=?",
                                       (self.mid,)).fetchone()[0]
        self.mesh.mark_delivered("bob", ids)
        again = self.mesh.conn.execute("SELECT delivered_at FROM messages WHERE message_id=?",
                                       (self.mid,)).fetchone()[0]
        self.assertEqual(first, again)

    def test_cannot_stamp_another_agents_message(self):
        self.assertEqual(self.mesh.mark_delivered("carol", [self.mid]), 0)
        self.assertEqual(self.pending("bob"), [self.mid])
        self.assertEqual(self.mesh.mark_delivered("bob", []), 0)


if __name__ == "__main__":
    unittest.main()
