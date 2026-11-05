from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "mesh_hook.py"
SCRIPT = ROOT / "agent_mesh.py"
SPEC = importlib.util.spec_from_file_location("agent_mesh_hook_test", SCRIPT)
agent_mesh = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
# register before exec: @dataclass resolves its own
# module via sys.modules and dies on None
sys.modules[SPEC.name] = agent_mesh
SPEC.loader.exec_module(agent_mesh)


class MeshHookTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "hook.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def hook(self, event: dict, runtime: str = "claude", extra_env: dict | None = None):
        env = os.environ.copy()
        env.update(extra_env or {})
        return subprocess.run(
            [sys.executable, "-s", str(HOOK), "--runtime", runtime, "--db", str(self.db)],
            input=json.dumps(event),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_session_prompt_and_stop_lifecycle(self):
        common = {"session_id": "1234567890abcdef", "cwd": r"C:\repo"}
        started = self.hook({**common, "hook_event_name": "SessionStart", "source": "startup"})
        self.assertEqual(started.returncode, 0, started.stderr)
        output = json.loads(started.stdout)
        self.assertIn("claude:1234567890abcdef", output["hookSpecificOutput"]["additionalContext"])

        prompted = self.hook({**common, "hook_event_name": "UserPromptSubmit", "prompt": "audit the parser"})
        self.assertEqual(prompted.returncode, 0, prompted.stderr)
        mesh = agent_mesh.Mesh(self.db)
        try:
            row = mesh.conn.execute(
                "SELECT task,status FROM agents WHERE agent_id='claude:1234567890abcdef'"
            ).fetchone()
            self.assertEqual(row["task"], "interactive session active; task summary not auto-copied")
            self.assertNotIn("audit the parser", row["task"])
            self.assertEqual(row["status"], "working")
        finally:
            mesh.close()

        stopped = self.hook({**common, "hook_event_name": "Stop"})
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        mesh = agent_mesh.Mesh(self.db)
        try:
            status = mesh.conn.execute(
                "SELECT status FROM agents WHERE agent_id='claude:1234567890abcdef'"
            ).fetchone()[0]
            self.assertEqual(status, "waiting")
        finally:
            mesh.close()

    def test_codex_uses_same_common_hook_schema(self):
        result = self.hook({
            "hook_event_name": "SessionStart",
            "session_id": "codex-session-xyz",
            "cwd": r"C:\repo",
            "model": "test",
            "source": "startup",
        }, runtime="codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("codex:codex-session-", result.stdout)

    def test_runner_environment_unifies_hook_identity(self):
        result = self.hook({
            "hook_event_name": "SessionStart",
            "session_id": "runtime-native-session",
            "cwd": r"C:\repo",
            "source": "resume",
        }, extra_env={"AGENT_MESH_ID": "agentA-1-sample", "AGENT_MESH_SESSION": "stable-resume-id"})
        self.assertEqual(result.returncode, 0, result.stderr)
        mesh = agent_mesh.Mesh(self.db)
        try:
            agents = mesh.conn.execute("SELECT agent_id,session_key FROM agents").fetchall()
            self.assertEqual([(row["agent_id"], row["session_key"]) for row in agents],
                             [("agentA-1-sample", "stable-resume-id")])
        finally:
            mesh.close()

    def test_unavailable_database_injects_visible_degraded_mode(self):
        blocker = self.root / "not-a-directory"
        blocker.write_text("x", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-s", str(HOOK), "--runtime", "claude", "--db", str(blocker / "mesh.sqlite3")],
            input=json.dumps({"hook_event_name": "SessionStart", "session_id": "x", "cwd": r"C:\repo"}),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("DEGRADED", result.stdout)
        self.assertIn("do not mutate shared", result.stdout)

    def test_locked_database_degrades_quickly_instead_of_freezing_prompt(self):
        bootstrap = agent_mesh.Mesh(self.db)
        bootstrap.close()

        # What is being claimed is that the LOCK costs almost nothing, so the lock's own cost is
        # what gets measured: an identical unlocked call, then the locked one, and the difference
        # is what the assertion reads. Timing the locked call alone also measures this machine's
        # process startup, which is seconds on a busy box and has nothing to do with the busy
        # timeout under test. The warm-up call ahead of both keeps the cold/warm gap between the
        # two measured spawns from hiding a real regression.
        self.hook({
            "hook_event_name": "SessionStart",
            "session_id": "warmup-session",
            "cwd": r"C:\repo",
        })
        started = time.perf_counter()
        self.hook({
            "hook_event_name": "SessionStart",
            "session_id": "baseline-session",
            "cwd": r"C:\repo",
        })
        unlocked = time.perf_counter() - started

        locker = sqlite3.connect(str(self.db), isolation_level=None, timeout=1)
        locker.execute("BEGIN EXCLUSIVE")
        try:
            started = time.perf_counter()
            result = self.hook({
                "hook_event_name": "SessionStart",
                "session_id": "locked-session",
                "cwd": r"C:\repo",
            })
            elapsed = time.perf_counter() - started
        finally:
            locker.execute("ROLLBACK")
            locker.close()
        self.assertEqual(result.returncode, 0)
        self.assertIn("DEGRADED", result.stdout)
        self.assertLess(
            elapsed - unlocked, 2.5,
            "a locked coordination DB must not feel like a frozen terminal: the lock added "
            "%.1fs over an identical unlocked call that took %.1fs" % (elapsed - unlocked, unlocked),
        )

    def test_malformed_input_fails_open_silently(self):
        result = subprocess.run(
            [sys.executable, "-s", str(HOOK), "--runtime", "claude", "--db", str(self.db)],
            input="not-json",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_non_object_json_fails_open_silently(self):
        result = subprocess.run(
            [sys.executable, "-s", str(HOOK), "--runtime", "claude", "--db", str(self.db)],
            input="[]",
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_unicode_peer_text_survives_cp1252_hook_pipe(self):
        mesh = agent_mesh.Mesh(self.db)
        try:
            mesh.hello("sender", "test", "safe summary", r"C:\repo", 1800, "working")
            mesh.hello("claude:unicode-session", "claude", "safe summary", r"C:\repo", 1800,
                       "working", session="unicode-session")
            mesh.send("sender", "claude:unicode-session", "unicode", "שלום 🚀", "message")
        finally:
            mesh.close()
        result = self.hook(
            {"hook_event_name": "SessionStart", "session_id": "unicode-session", "cwd": r"C:\repo"},
            extra_env={"PYTHONIOENCODING": "cp1252"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("שלום 🚀", payload["hookSpecificOutput"]["additionalContext"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
