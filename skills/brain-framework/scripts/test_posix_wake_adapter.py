"""Discriminating controls for the stable-inode POSIX dream-lock observer."""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import errno
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import connector as c
import posix_wake_adapter as adapter


@unittest.skipUnless(os.name == "posix", "POSIX flock requires an actual POSIX host")
class LockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix="brain-posix-lock-controls-")
        cls.source = c.acquire_source(Path(cls.work.name) / "profile") / "hooks/wake_brain.py"

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.work.name)
        self.home = Path(self.temp.name)
        self.dreaming = self.home / ".claude/dreaming"
        self.dreaming.mkdir(parents=True)
        self.lock = self.dreaming / "dream.lock"

    def tearDown(self):
        self.temp.cleanup()

    def old_marker(self):
        self.lock.write_bytes(b"persistent inode\n")
        os.utime(self.lock, (1, 1))
        return self.lock.stat()

    @contextlib.contextmanager
    def holder(self):
        code = "import fcntl,sys; f=open(sys.argv[1],'rb'); fcntl.flock(f,fcntl.LOCK_EX); print('held',flush=True); sys.stdin.readline()"
        process = subprocess.Popen([sys.executable, "-c", code, str(self.lock)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "held")
            yield process
        finally:
            process.communicate("release\n", timeout=5)
            self.assertEqual(process.returncode, 0)

    def wake(self, adapted):
        if adapted:
            namespace, errors = adapter.original_namespace(self.source)
        else:
            namespace = {"__name__": "original_control", "__file__": str(self.source)}
            exec(compile(self.source.read_bytes(), str(self.source), "exec"), namespace)
            errors = []
        namespace.update(HOME=str(self.home), CLAUDE=str(self.home / ".claude"), DR=str(self.dreaming),
                         MEM=str(self.home / "memory"), VIEWS=str(self.home / "views"))
        namespace["part1"]("")
        return "\n".join(namespace["OUT"]), errors

    def cli(self):
        return subprocess.run([sys.executable, str(Path(adapter.__file__)), "--source", str(self.source), "--part", "1"],
                              input="{}", capture_output=True, text=True, timeout=15,
                              env={**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home),
                                   "PYTHONDONTWRITEBYTECODE": "1"})

    def test_absence_does_not_create_a_marker(self):
        self.assertEqual(adapter.observe_lock(self.lock), {"state": "absent"})
        self.assertFalse(self.lock.exists())

    def test_old_available_marker_preserves_inode_content_and_mtime(self):
        before = self.old_marker()
        self.assertEqual(adapter.observe_lock(self.lock)["state"], "available")
        after = self.lock.stat()
        self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))
        self.assertEqual(self.lock.read_bytes(), b"persistent inode\n")

    def test_live_cross_process_flock_is_detected_even_on_old_marker_then_restores(self):
        before = self.old_marker()
        with self.holder():
            self.assertEqual(adapter.observe_lock(self.lock)["state"], "held")
        self.assertEqual(adapter.observe_lock(self.lock)["state"], "available")
        self.assertEqual(self.lock.stat().st_ino, before.st_ino)

    def test_original_false_crash_alarm_is_reproduced_and_other_warnings_survive(self):
        self.old_marker()
        original, _ = self.wake(False)
        self.assertIn("stale dream.lock", original)
        adapted, errors = self.wake(True)
        self.assertNotIn("stale dream.lock", adapted)
        self.assertNotIn("a dream lock is held", adapted)
        self.assertIn("MODEL_OF_PARTNER MISSING", adapted)
        self.assertIn("no dream ran recently", adapted)
        self.assertFalse(errors)

    def test_original_misses_old_held_lock_while_adapter_reports_real_ownership(self):
        self.old_marker()
        with self.holder():
            original, _ = self.wake(False)
            adapted, errors = self.wake(True)
        self.assertIn("stale dream.lock", original)
        self.assertIn("a dream lock is held right now", adapted)
        self.assertNotIn("stale dream.lock", adapted)
        self.assertFalse(errors)

    def test_symlink_is_unverified_and_target_is_untouched(self):
        target = self.home / "target"
        target.write_bytes(b"unrelated")
        self.lock.symlink_to(target)
        self.assertEqual(adapter.observe_lock(self.lock)["state"], "unverified")
        self.assertTrue(self.lock.is_symlink())
        self.assertEqual(target.read_bytes(), b"unrelated")

    def test_directory_is_unverified(self):
        self.lock.mkdir()
        self.assertEqual(adapter.observe_lock(self.lock)["state"], "unverified")

    def test_io_failure_is_unverified_not_an_available_lock(self):
        self.old_marker()
        with patch("fcntl.flock", side_effect=OSError(errno.EIO, "injected I/O failure")):
            result = adapter.observe_lock(self.lock)
        self.assertEqual(result["state"], "unverified")
        self.assertIn("injected I/O failure", result["error"])

    def test_flock_permission_failure_is_unverified_not_inferred_ownership(self):
        self.old_marker()
        with patch("fcntl.flock", side_effect=OSError(errno.EACCES, "injected syscall permission failure")):
            result = adapter.observe_lock(self.lock)
        self.assertEqual(result["state"], "unverified")
        self.assertIn("permission failure", result["error"])

    def test_inode_replacement_is_unverified(self):
        before = self.old_marker()
        changed = list(before)
        changed[1] += 1
        with patch.object(adapter.os, "lstat", side_effect=[before, os.stat_result(changed)]):
            result = adapter.observe_lock(self.lock)
        self.assertEqual(result["state"], "unverified")
        self.assertIn("inode changed", result["error"])

    def test_source_change_cannot_be_blessed_by_the_adapter(self):
        wrong = self.home / "wake.py"
        wrong.write_bytes(self.source.read_bytes() + b"\nraise RuntimeError('changed original')\n")
        with self.assertRaisesRegex(ValueError, "reviewed original"):
            adapter.original_namespace(wrong)

    def test_actual_cli_observer_fault_warns_and_restored_control_is_plain_original_wake(self):
        self.lock.mkdir()
        fault = self.cli()
        self.assertEqual(fault.returncode, 0, fault.stderr)
        value = json.loads(fault.stdout)
        self.assertIn("BRAIN ALARM [DREAM_LOCK_OBSERVER_UNVERIFIED]", value["systemMessage"])
        self.assertIn("MODEL_OF_PARTNER MISSING", value["hookSpecificOutput"]["additionalContext"])
        self.lock.rmdir()
        restored = self.cli()
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertNotIn("DREAM_LOCK_OBSERVER_UNVERIFIED", restored.stdout)
        self.assertIn("MODEL_OF_PARTNER MISSING", restored.stdout)

    def test_connector_runs_the_adapter_through_its_normal_child_entry(self):
        self.old_marker()
        deployment = {"source": str(self.source.parent.parent), "home": str(self.home)}
        env = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home), "PYTHONDONTWRITEBYTECODE": "1"}
        with patch.object(c, "runtime_env", return_value=env):
            result = c.run(deployment, [sys.executable, "-s", self.source, "--part", "1"], input_text="{}")
        self.assertNotIn("stale dream.lock", result.stdout)
        self.assertIn("MODEL_OF_PARTNER MISSING", result.stdout)

    def fresh_wake_inputs(self):
        mem = self.home / "memory"
        mem.mkdir()
        for name in ("MODEL_OF_PARTNER", "STATE_OF_MIND", "AUTOBIOGRAPHY", "values_what_we_stand_for",
                     "identity_who_you_are", "THE_OTHER_MINDS", "FASCINATIONS"):
            (mem / (name + ".md")).write_text("# Test memory\n", encoding="utf-8")
        logs = self.dreaming / "logs"
        logs.mkdir()
        (self.dreaming / ".embedding_status.json").write_text(json.dumps({
            "checked_at": datetime.now(timezone.utc).isoformat(), "complete": True,
        }), encoding="utf-8")
        return logs / "dream_20261117_190000.log"

    def test_host_pressure_with_fresh_identity_uses_a_general_wake_warning(self):
        log = self.fresh_wake_inputs()
        log.write_text("generating dream agenda\ndream cycle complete\n", encoding="utf-8")
        ns, _ = adapter.original_namespace(self.source, liveness_observer=lambda _: [
            "Compute governor reported WARN: disk_low",
        ])
        ns.update(HOME=str(self.home), CLAUDE=str(self.home / ".claude"), DR=str(self.dreaming),
                  MEM=str(self.home / "memory"), VIEWS=str(self.home / "views"))
        ns["part1"]("")
        body = "\n".join(ns["OUT"])
        self.assertIn("Compute governor reported WARN: disk_low", body)
        self.assertNotIn("identity layer may not have fully travelled", body)

    def test_visible_wake_alarm_reports_lock_live_and_dream_findings_together(self):
        log = self.fresh_wake_inputs()
        log.write_text("generating dream agenda\nSTEP FAILED: regression gate exit 1\ndream cycle complete\n",
                       encoding="utf-8")
        self.lock.mkdir()
        original = adapter.original_namespace

        def reader(*args, **kwargs):
            ns, errors = original(self.source, liveness_observer=lambda _: [
                "Compute governor reported WARN: disk_low", "Another observed health finding",
            ])
            ns.update(HOME=str(self.home), CLAUDE=str(self.home / ".claude"), DR=str(self.dreaming),
                      MEM=str(self.home / "memory"), VIEWS=str(self.home / "views"))
            return ns, errors

        output = io.StringIO()
        args = ["posix_wake_adapter", "--source", str(self.source), "--part", "1"]
        with (patch.object(adapter, "original_namespace", side_effect=reader), patch.object(sys, "argv", args),
              patch.object(sys, "stdin", io.StringIO("{}")), contextlib.redirect_stdout(output)):
            self.assertEqual(adapter.main(), 0)
        response = json.loads(output.getvalue())
        message = response["systemMessage"]
        self.assertIn("DREAM_LOCK_OBSERVER_UNVERIFIED", message)
        self.assertIn("disk_low", message)
        self.assertIn("regression gate exit 1", message)


if __name__ == "__main__":
    unittest.main()
