#!/usr/bin/env python3
"""Regression controls for Codex's pre-action help-discovery guard."""
from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import codex_reflex_hook as guard


class HelpDiscoveryGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.unsafe = self.root / "side_effectful.py"
        self.unsafe.write_text("print('normal work ran')\n", encoding="utf-8")
        self.argparse_looking = self.root / "argparse_looking.py"
        self.argparse_looking.write_text(
            "import argparse\n"
            "print('work before parser')\n"
            "parser = argparse.ArgumentParser()\n"
            "parser.parse_args()\n",
            encoding="utf-8",
        )
        self.ignores_argv = self.root / "ignores_argv.py"
        self.ignores_argv.write_text(
            "import argparse\n"
            "argparse.ArgumentParser().parse_args([])\n"
            "print('normal work ran')\n",
            encoding="utf-8",
        )
        self.dead_parser = self.root / "dead_parser.py"
        self.dead_parser.write_text(
            "import argparse\n"
            "if False:\n"
            "    argparse.ArgumentParser().parse_args()\n"
            "print('normal work ran')\n",
            encoding="utf-8",
        )
        self.argv_no_exit = self.root / "argv_no_exit.py"
        self.argv_no_exit.write_text(
            "import sys\n"
            "if '--help' in sys.argv:\n"
            "    print('seen')\n"
            "print('normal work ran')\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def payload(self, command: str, event: str = "PreToolUse") -> dict:
        return {
            "hook_event_name": event,
            "tool_name": "Bash",
            "cwd": str(self.root),
            "tool_input": {"command": command},
        }

    def assert_denied(self, command: str) -> None:
        result = guard._help_guard(self.payload(command))
        self.assertIsNotNone(result)
        self.assertEqual(
            result["hookSpecificOutput"].get("permissionDecision"),
            "deny",
        )

    def approved_hash(self, path: Path | None = None) -> str:
        digest = guard._sha256(path or self.unsafe)
        self.assertIsNotNone(digest)
        return digest

    def test_unapproved_source_is_denied_regardless_of_parser_tokens(self) -> None:
        self.assert_denied(f'python "{self.unsafe}" --help')
        self.assert_denied(f'py -3 "{self.unsafe}" -h')
        for path in (self.argparse_looking, self.ignores_argv, self.dead_parser, self.argv_no_exit):
            self.assert_denied(f'python "{path}" --help')

    def test_hash_bound_approval_and_mutation_invalidation(self) -> None:
        digest = self.approved_hash()
        ps = f"$env:HELP_INSPECTED='{digest}'; python \"{self.unsafe}\" --help"
        bash = f'HELP_INSPECTED={digest} python "{self.unsafe}" --help'
        self.assertIsNone(guard._help_guard(self.payload(ps)))
        self.assertIsNone(guard._help_guard(self.payload(bash)))
        self.assert_denied(
            f"$env:HELP_INSPECTED='{'0' * 64}'; python \"{self.unsafe}\" --help"
        )
        self.unsafe.write_text("print('changed bytes')\n", encoding="utf-8")
        self.assert_denied(ps)
        new_digest = self.approved_hash()
        self.assertIsNone(guard._help_guard(self.payload(
            f"$env:HELP_INSPECTED='{new_digest}'; python \"{self.unsafe}\" --help"
        )))

    def test_dynamic_powershell_forms_and_interpreters_are_denied(self) -> None:
        commands = (
            f'python "{self.unsafe}" --hel"p"',
            f"$h='--help'; python \"{self.unsafe}\" $h",
            f"$p='python'; & $p \"{self.unsafe}\" --help",
            f"$env:H='--help'; python \"{self.unsafe}\" $env:H",
            f"$env:H='--help'; python \"{self.unsafe}\" ${{env:H}}",
            f"python \"{self.unsafe}\" ('--he'+'lp')",
            f"python \"{self.unsafe}\" ('--he' + 'lp')",
            f"cmd /c python \"{self.unsafe}\" --help",
            f"cmd /c \"python -B '{self.unsafe}' --help\"",
            f"Invoke-Expression \"python '{self.unsafe}' --help\"",
            f"Invoke-Expression (\"python -B '{self.unsafe}' --help\")",
            f"& {{ python -B \"{self.unsafe}\" --help }}",
            f"python --check-hash-based-pycs always -B \"{self.unsafe}\" --help",
            f'pypy3 "{self.unsafe}" --help',
            f'uv run "{self.unsafe}" --help',
            f'uv run python "{self.unsafe}" --help',
            f'uv run --python 3.14 python "{self.unsafe}" --help',
            f'sudo -u root python "{self.unsafe}" --help',
            f'C:/Python314/python.exe -B "{self.unsafe}" --help',
        )
        for command in commands:
            with self.subTest(command=command):
                self.assert_denied(command)

    def test_nested_exec_source_is_not_regex_authority(self) -> None:
        # The host presents the subsequent nested shell_command as a second,
        # structured PreToolUse event.  Outer JS variables/templates stay inert.
        for nested in (
            f"tools.shell_command({{command: 'python {self.unsafe} --help'}})",
            f"const c = `python {self.unsafe} --help`; tools.shell_command({{command:c}})",
        ):
            payload = {
                "hook_event_name": "PreToolUse",
                "tool_name": "exec",
                "cwd": str(self.root),
                "tool_input": nested,
            }
            self.assertIsNone(guard._help_guard(payload))
        self.assert_denied(f'python "{self.unsafe}" --help')

    def test_nearby_safe_commands_stay_allowed(self) -> None:
        self.assertIsNone(guard._help_guard(self.payload(f'Get-Content "{self.unsafe}"')))
        self.assertIsNone(guard._help_guard(self.payload("python -m pytest --help")))
        self.assertIsNone(guard._help_guard(self.payload("python --help")))
        self.assertIsNone(guard._help_guard(self.payload(f'python "{self.unsafe}" --selftest')))
        digest = self.approved_hash()
        self.assertIsNone(guard._help_guard(self.payload(
            f'Write-Output python "{self.unsafe}" --help'
        )))
        self.assertIsNone(guard._help_guard(self.payload(
            f'Write-Output ok # python "{self.unsafe}" --help'
        )))
        self.assertIsNone(guard._help_guard(self.payload(
            f'<#\npython "{self.unsafe}" --help\n#>\nWrite-Output ok'
        )))
        self.assertIsNone(guard._help_guard(self.payload(
            "$text=@'\nit's ordinary prose\n"
            f'python "{self.unsafe}" --help\n'
            "'@; Write-Output $text"
        )))
        self.assert_denied(f'python "{self.unsafe}" --help # HELP_INSPECTED={digest}')
        self.assert_denied(
            f'Write-Output "HELP_INSPECTED={digest}"; python "{self.unsafe}" --help'
        )
        self.assert_denied(
            f"'HELP_INSPECTED={digest}'; python \"{self.unsafe}\" --help"
        )
        self.assert_denied(
            f"$env:HELP_INSPECTED='{digest}'; $x='intervening'; python \"{self.unsafe}\" --help"
        )
        self.assertIsNone(guard._help_guard(self.payload(
            f'python "{self.unsafe}" --help', event="UserPromptSubmit"
        )))

    def test_guard_remains_live_when_advisory_brain_is_offline(self) -> None:
        with mock.patch("brain_switch.is_offline", return_value=True):
            result = guard.process(self.payload(f'python "{self.unsafe}" --help'))
        self.assertEqual(
            result["hookSpecificOutput"].get("permissionDecision"),
            "deny",
        )

    def test_denial_is_recorded_in_supported_block_metrics_bucket(self) -> None:
        raw = json.dumps(self.payload(f'python "{self.unsafe}" --help'))
        with mock.patch.object(guard.sys, "stdin", io.StringIO(raw)), \
                mock.patch.object(guard.sys, "stdout", io.StringIO()), \
                mock.patch("codex_hook_telemetry.record") as record:
            self.assertEqual(guard.main(), 0)
        record.assert_called_once()
        self.assertEqual(record.call_args.args[1:3], ("block", "ok"))

    def test_generic_child_decisions_remain_advisory_only(self) -> None:
        child = {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "must not escape",
            "additionalContext": "visible warning",
            "updatedInput": {"command": "must not escape"},
        }}
        self.assertEqual(guard._advisory(child, "PreToolUse"), {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": "visible warning",
            }
        })


if __name__ == "__main__":
    unittest.main()
