"""A pulse renews live claims, except a claim on a resource listed in idle_lapse.json whose holder has not taken
or asserted it for that many minutes: the pulse ends that one. With no file nothing changes.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("agent_mesh_idle_lapse_test", ROOT / "agent_mesh.py")
agent_mesh = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = agent_mesh
SPEC.loader.exec_module(agent_mesh)


class IdleLapseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.mesh = agent_mesh.Mesh(self.home / "mesh.sqlite3")
        self.mesh.hello("alice", "claude", "test", self.temp.name, 3600, "active")

    def tearDown(self):
        self.mesh.close()
        self.temp.cleanup()

    def age_claim(self, resource, minutes):
        """Pretend the claim (and every claim/assert event naming it) happened `minutes` ago."""
        past = agent_mesh.iso(agent_mesh.utc_now() - dt.timedelta(minutes=minutes))
        self.mesh.conn.execute("UPDATE claims SET acquired_at=? WHERE resource=?", (past, resource))
        self.mesh.conn.execute("UPDATE events SET happened_at=? WHERE event_type IN ('claim','claim_assert')", (past,))

    def live(self, resource):
        now = agent_mesh.iso(agent_mesh.utc_now())
        return self.mesh.conn.execute(
            "SELECT COUNT(*) FROM claims WHERE resource=? AND released_at IS NULL AND expires_at>?", (resource, now)
        ).fetchone()[0]

    def test_no_file_renews_everything(self):
        self.mesh.claim("alice", ["topic:index"], "exclusive", "write", 3600)
        self.age_claim("topic:index", 120)
        out = self.mesh.pulse("alice")
        self.assertNotIn("lapsed_idle_claims", out)
        self.assertEqual(self.live("topic:index"), 1)

    def test_idle_listed_claim_lapses(self):
        (self.home / agent_mesh.IDLE_LAPSE_FILE).write_text(json.dumps({"topic:index": 15}), encoding="utf-8")
        self.mesh.claim("alice", ["topic:index"], "exclusive", "write", 3600)
        self.mesh.claim("alice", ["topic:other"], "exclusive", "write", 3600)
        self.age_claim("topic:index", 30)
        out = self.mesh.pulse("alice")
        self.assertEqual(out.get("lapsed_idle_claims"), ["topic:index"])
        self.assertEqual(self.live("topic:index"), 0)
        self.assertEqual(self.live("topic:other"), 1)

    def test_recent_assert_keeps_it(self):
        (self.home / agent_mesh.IDLE_LAPSE_FILE).write_text(json.dumps({"topic:index": 15}), encoding="utf-8")
        self.mesh.claim("alice", ["topic:index"], "exclusive", "write", 3600)
        self.age_claim("topic:index", 30)
        self.mesh.assert_claim("alice", ["topic:index"])
        out = self.mesh.pulse("alice")
        self.assertNotIn("lapsed_idle_claims", out)
        self.assertEqual(self.live("topic:index"), 1)

    def test_bad_file_is_ignored(self):
        (self.home / agent_mesh.IDLE_LAPSE_FILE).write_text("not json", encoding="utf-8")
        self.assertEqual(agent_mesh.load_idle_lapse(self.home), {})
        (self.home / agent_mesh.IDLE_LAPSE_FILE).write_text(json.dumps({"topic:x": -1, "topic:y": "z"}),
                                                            encoding="utf-8")
        self.assertEqual(agent_mesh.load_idle_lapse(self.home), {})


if __name__ == "__main__":
    unittest.main()
