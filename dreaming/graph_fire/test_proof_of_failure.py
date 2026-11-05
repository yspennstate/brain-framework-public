#!/usr/bin/env python3
"""Adversarial soundness controls for the proof-of-failure deny predicate."""
from __future__ import annotations

import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

import proof_of_failure as proof

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import proof_deny_hook
import shadow_deny_status


class ProofOfFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.existing = str(Path(__file__).resolve())
        if "\\" not in cls.existing:
            raise unittest.SkipTest("This suite calibrates the Windows/Bash path seam")

    def command(self, prefix: str = "python", suffix: str = "") -> str:
        return f"{prefix} {self.existing}{suffix}"

    def assert_proven(self, command: str, expected_class: str) -> None:
        proven, corrected, why = proof.prove_doomed(command)
        self.assertTrue(proven, command)
        self.assertTrue(why.startswith(expected_class + ":"), why)
        if expected_class == "A":
            self.assertIn("/", corrected)

    def assert_unproven(self, command: str) -> None:
        self.assertEqual(proof.prove_doomed(command), (False, None, None), command)

    def test_single_simple_backslash_path_is_proven(self) -> None:
        self.assert_proven(self.command(), "A")
        self.assert_proven(self.command("timeout 10 python", " --flag"), "A")
        self.assert_proven(self.command("nice -n 12 python", ' --name "a;b"'), "A")

    def test_adjacent_backslashes_are_parse_depth_ambiguous(self) -> None:
        doubled = self.existing.replace("\\", "\\\\")
        quadrupled = self.existing.replace("\\", "\\\\\\\\")
        self.assert_unproven("python " + doubled)
        self.assert_unproven("python " + quadrupled)

    def test_recovery_and_multiple_command_forms_are_not_whole_command_proofs(self) -> None:
        for command in (
            self.command(suffix=" || true"),
            self.command(suffix="; echo recovered"),
            self.command(suffix=" | Write-Output recovered"),
            self.command(suffix=" && echo later"),
            self.command(suffix=" &"),
            self.command(suffix=" >out.txt"),
            "if false; then " + self.command() + "; fi",
        ):
            with self.subTest(command=command):
                self.assert_unproven(command)

    def test_paths_in_quotes_comments_and_heredoc_data_are_not_proofs(self) -> None:
        for command in (
            f'python "{self.existing}"',
            "echo python " + self.existing,
            "# python " + self.existing,
            "cat <<'EOF'\npython " + self.existing + "\nEOF",
            "python 'unterminated " + self.existing,
        ):
            with self.subTest(command=command):
                self.assert_unproven(command)

    def test_standalone_invalid_python_heredoc_is_proven(self) -> None:
        command = "python - <<'PY'\ndef f(:\n    pass\nPY"
        self.assert_proven(command, "B")

    def test_valid_or_recovered_python_heredoc_is_not_proven(self) -> None:
        valid = "python - <<'PY'\nprint('ok')\nPY"
        invalid_then_success = "python - <<'PY'\ndef f(:\n pass\nPY\necho recovered"
        invalid_in_conditional = "true || python - <<'PY'\ndef f(:\n pass\nPY"
        expands_before_python = "python - <<PY\nprint($?)\nPY"
        three_heredocs = (
            "python - <<'EOF'\nprint('first')\nEOF\n"
            "python - <<'EOF'\nprint('second')\nEOF\n"
            "python - <<'EOF'\nprint('third')\nEOF"
        )
        form_feed_is_not_a_shell_line = "python - <<'PY'\nPY=1\nx =\fPY"
        split_header_is_two_shell_commands = "python -\n<<'PY'\ndef f(:\n pass\nPY"
        for command in (valid, invalid_then_success, invalid_in_conditional,
                        expands_before_python, three_heredocs, form_feed_is_not_a_shell_line,
                        split_header_is_two_shell_commands):
            with self.subTest(command=command):
                self.assert_unproven(command)

    def test_remote_and_nonexistent_local_paths_fail_open(self) -> None:
        self.assert_unproven("ssh box " + self.command())
        missing = self.existing.replace("test_proof_of_failure.py", "absent_xyz.py")
        self.assert_unproven("python " + missing)
        self.assert_unproven("cd C:\\Windows")
        self.assert_unproven("pushd C:\\Windows")

    def test_heredoc_header_shell_operators_fail_open(self) -> None:
        self.assert_unproven("python -Ebad||true - <<'PY'\ndef f(:\n pass\nPY")

    def test_junk_never_raises_or_proves(self) -> None:
        for value in (None, 123, {}, "", "x" * 30000):
            with self.subTest(value=type(value).__name__):
                self.assertEqual(proof.prove_doomed(value), (False, None, None))

    def test_shadow_evidence_is_bound_to_exact_predicate_bytes(self) -> None:
        hook_id = proof_deny_hook._predicate_id(proof)
        status_id = shadow_deny_status._predicate_id()
        self.assertEqual(hook_id, status_id)
        self.assertRegex(hook_id, re.compile(r"^[0-9a-f]{16}$"))
        rows = [
            ["1", "s", "shadow", "A:x", "old command"],
            ["2", "s", "shadow", "A:x", "wrong", "0" * 16],
            ["3", "s", "shadow", "A:x", "current", hook_id],
        ]
        self.assertEqual(
            shadow_deny_status._current_proof_rows(rows, hook_id),
            [rows[-1]],
        )
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "proof.tsv"
            with mock.patch.object(proof_deny_hook, "LOG", str(log)):
                proof_deny_hook._log("session", "A:test", "shadow", "python bad", hook_id)
            fields = log.read_text(encoding="utf-8").rstrip("\n").split("\t")
            self.assertEqual(len(fields), 6)
            self.assertEqual(fields[-1], hook_id)

    def test_live_budget_fails_open_on_corruption_or_lock_contention(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            lock = Path(str(state) + ".lock")
            with mock.patch.object(proof_deny_hook, "STATE", str(state)), \
                    mock.patch.object(proof_deny_hook, "STATE_LOCK", str(lock)):
                state.write_text("{broken", encoding="utf-8")
                self.assertFalse(proof_deny_hook._claim("session", True))
                self.assertEqual(state.read_text(encoding="utf-8"), "{broken")
                self.assertFalse(lock.exists())

                state.unlink()
                lock.write_text("another hook owns this", encoding="utf-8")
                self.assertFalse(proof_deny_hook._claim("session", True))
                self.assertFalse(state.exists())

                lock.unlink()
                self.assertTrue(proof_deny_hook._claim("session", True))
                persisted = __import__("json").loads(state.read_text(encoding="utf-8"))
                self.assertEqual(len(persisted["sessions"]["session"]), 1)
                self.assertFalse(lock.exists())

    def test_live_deny_requires_predicate_identity_and_durable_log(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": self.command()},
            "session_id": "test-session",
        }
        with tempfile.TemporaryDirectory() as td:
            live = Path(td) / "LIVE"
            live.write_text("armed for isolated unit test", encoding="utf-8")

            def invoke(predicate_id: str, logged: bool) -> str:
                source = io.StringIO(json.dumps(payload))
                sink = io.StringIO()
                with mock.patch.object(proof_deny_hook, "LIVE", str(live)), \
                        mock.patch.object(proof_deny_hook, "_predicate_id", return_value=predicate_id), \
                        mock.patch.object(proof_deny_hook, "_claim", return_value=True), \
                        mock.patch.object(proof_deny_hook, "_log", return_value=logged), \
                        mock.patch.object(sys, "stdin", source), \
                        mock.patch.object(sys, "stdout", sink):
                    proof_deny_hook.main()
                return sink.getvalue()

            self.assertEqual(invoke("unknown", True), "")
            self.assertEqual(invoke(proof_deny_hook._predicate_id(proof), False), "")
            decision = json.loads(invoke(proof_deny_hook._predicate_id(proof), True))
            self.assertEqual(
                decision["hookSpecificOutput"]["permissionDecision"],
                "deny",
            )


if __name__ == "__main__":
    unittest.main()
