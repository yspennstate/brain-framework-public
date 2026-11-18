"""Controls for reading actually deployed health inputs."""
import datetime as dt
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from health_inputs import governor_findings, lexical_findings, owner_window, recurrence_findings


class HealthInputsTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.home = Path(workspace.name)

    def test_old_index_with_current_content_passes_but_same_slug_edits_fail(self):
        index = self.home / "index.json.gz"
        with gzip.open(index, "wt") as stream:
            json.dump({"n": 1, "dl": {"fact": 2}, "df": {"a": 1},
                       "tf": {"fact": {"a": 2}}, "avgdl": 2}, stream)
        os.utime(index, (1, 1))
        self.assertEqual(lexical_findings(self.home, index, lambda _: {"fact": ["a", "a"]}), [])
        self.assertTrue(lexical_findings(self.home, index, lambda _: {"fact": ["a", "b"]}))
        self.assertTrue(lexical_findings(self.home, index, lambda _: {"fact": ["a", "a"], "new": ["x"]}))

    def test_small_prompt_history_passes_but_missing_expected_history_fails(self):
        now = time.time()
        row = {"ts": dt.datetime.fromtimestamp(now - 4 * 3600).isoformat(timespec="seconds"),
               "session": "owner", "text": "actual recorded prompt"}
        ledger = self.home / "PROMPTS.jsonl"
        ledger.write_text(json.dumps(row) + "\n")
        builder = types.SimpleNamespace(_is_owner=lambda _: True, _slim=lambda value: value,
                                        _fold_cache=lambda rows: rows, owner_prompts=lambda rows: rows,
                                        CACHE_MAX_ROWS=7500, N_KEEP=3000, JSON_TEXT_CAP=1000)
        report = {"built_epoch": int(now), "kept": 1, "prompts": [row], "ledger_rows": 1}
        self.assertEqual(owner_window(report, ledger, builder, now)[0], [])
        dropped = dict(report, kept=0, prompts=[])
        self.assertIn("missing 1", owner_window(dropped, ledger, builder, now)[0][0])
        ledger.write_text("")
        self.assertIn("last build recorded 1", owner_window(report, ledger, builder, now)[0][0])

    def test_unattempted_recurrence_does_not_flag_a_fresh_install(self):
        (self.home / "strong_nodes").mkdir()
        (self.home / ".mistake_graph_snapshot.json.append.lock").touch()
        self.assertEqual(recurrence_findings(self.home, self.home / "memory"), [])

    def test_recurring_faults_and_foreign_or_unpinned_success_are_visible(self):
        root = self.home / "strong_nodes"
        root.mkdir()
        (root / "WATCH_HISTORY.jsonl").touch()
        (self.home / ".mistake_graph_snapshot.json.generation.json").touch()
        mem = self.home / "memory"
        brain = types.SimpleNamespace(
            _strong_node_live_root=lambda: root,
            _strong_node_watch_latest=lambda: ({"status": "DEGRADED", "reason": "reviewed package absent"}, "fresh"),
            _strong_node_runtime_pin=lambda: (root / "runtime" / "pinned", "pinned"),
        )
        mistakes = types.SimpleNamespace(summarize_path=lambda path, generation_path: types.SimpleNamespace(
            title="Mistake recurrence watch: FEED UNVERIFIED", detail="attempt had no saved verdict"))
        with patch.dict(sys.modules, brain=brain, mistake_graph_status=mistakes):
            findings = recurrence_findings(self.home, mem)
            self.assertTrue(any("DEGRADED" in row and "reviewed package absent" in row for row in findings))
            self.assertTrue(any("FEED UNVERIFIED" in row for row in findings))
            (self.home / ".mistake_graph_snapshot.json.generation.json").unlink()
            observed = {"status": "OBSERVED", "runtime_generation_id": "pinned", "inputs": {"memory_dir": str(mem)}}
            brain._strong_node_watch_latest = lambda: (observed, "fresh")
            self.assertEqual(recurrence_findings(self.home, mem), [])
            observed["inputs"]["memory_dir"] = str(self.home / "other")
            self.assertIn("different corpus", recurrence_findings(self.home, mem)[0])
            observed["runtime_generation_id"] = "different-generation"
            self.assertIn("differs from the current reviewed pin", recurrence_findings(self.home, mem)[0])

    def test_heartbeat_content_and_process_must_both_match(self):
        compute = self.home / ".claude/compute"
        compute.mkdir(parents=True)
        state = compute / "compute_state.json"
        (compute / "governor.pid").write_text("1234")
        now = 10000
        program = self.home / "brain-framework/governor/compute_governor_posix.py"
        process = subprocess.CompletedProcess([], 0, f"/native/python -B {program} --every 60\n", "")
        with patch("health_inputs.subprocess.run", return_value=process):
            state.write_text(json.dumps({"t_epoch": now - 5, "status": "ok", "reasons": []}))
            self.assertEqual(governor_findings(self.home, now), [])
            state.write_text(json.dumps({"t_epoch": now - 900, "status": "distress", "reasons": ["disk_low"]}))
            findings = governor_findings(self.home, now)
            self.assertTrue(any("DISTRESS" in message for message in findings))
            self.assertTrue(any("minutes old" in message for message in findings))
        state.write_text(json.dumps({"t_epoch": now - 5, "status": "ok", "reasons": []}))
        with patch("health_inputs.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, "unrelated-process\n", "")):
            self.assertIn("no matching live POSIX runner", governor_findings(self.home, now)[0])
        with patch("health_inputs.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "")):
            self.assertIn("no matching live POSIX runner", governor_findings(self.home, now)[0])
        with patch("health_inputs.subprocess.run", return_value=subprocess.CompletedProcess(
                [], 1, "", "ps: process observation denied by the sandbox\n")):
            findings = governor_findings(self.home, now)
            self.assertEqual(len(findings), 1)
            self.assertIn("process UNVERIFIED", findings[0])
            self.assertIn("observation denied", findings[0])
        state.write_text("{}")
        self.assertIn("UNVERIFIED", governor_findings(self.home, now)[0])


if __name__ == "__main__":
    unittest.main()
