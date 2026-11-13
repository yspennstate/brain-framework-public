"""The CLI marks a message delivered only after its text was printed: a print that fails leaves the message queued for
the next read. A console that cannot encode a character gets an escaped line instead of a traceback.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("agent_mesh_cli_delivery_test", ROOT / "agent_mesh.py")
agent_mesh = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = agent_mesh
SPEC.loader.exec_module(agent_mesh)


class Narrow(io.StringIO):
    """A stdout whose code page is ASCII."""
    encoding = "ascii"

    def write(self, s):
        s.encode("ascii")
        return super().write(s)


class Broken(io.StringIO):
    def write(self, s):
        raise OSError("pipe closed")


class CliDeliveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "mesh.sqlite3")
        mesh = agent_mesh.Mesh(Path(self.db))
        for name in ("alice", "bob"):
            mesh.hello(name, "claude", "test", self.temp.name, 600, "active")
        self.mid = mesh.send("alice", "bob", "t", "café report")["message_id"]
        mesh.close()

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *argv, out=None):
        out = out if out is not None else io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                rc = agent_mesh.main(["--db", self.db, *argv])
            except Exception as exc:  # the broken pipe surfaces here
                rc = exc
        return rc, out

    def delivered(self):
        mesh = agent_mesh.Mesh(Path(self.db))
        try:
            return mesh.conn.execute("SELECT delivered_at FROM messages WHERE message_id=?", (self.mid,)).fetchone()[0]
        finally:
            mesh.close()

    def test_inbox_stamps_after_print(self):
        rc, out = self.cli("inbox", "--agent", "bob", "--mark-delivered")
        self.assertEqual(rc, 0)
        self.assertIn(self.mid, out.getvalue())
        self.assertIsNotNone(self.delivered())

    def test_failed_print_leaves_message_queued(self):
        self.cli("inbox", "--agent", "bob", "--mark-delivered", out=Broken())
        self.assertIsNone(self.delivered())
        rc, out = self.cli("inbox", "--agent", "bob", "--pending")
        self.assertIn(self.mid, out.getvalue())

    def test_narrow_console_gets_escapes(self):
        rc, out = self.cli("inbox", "--agent", "bob", "--mark-delivered", out=Narrow())
        self.assertEqual(rc, 0)
        rows = json.loads(out.getvalue())
        self.assertEqual(rows[0]["body"], "café report")
        self.assertIsNotNone(self.delivered())


if __name__ == "__main__":
    unittest.main()
