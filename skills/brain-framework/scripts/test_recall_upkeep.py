"""Discriminating controls for admission's original-mechanism recall repair."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import connector as c
import recall_upkeep as upkeep


class UpkeepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="brain-upkeep-control-")
        self.root = Path(self.tmp.name)
        self.d = {"home": str(self.root / "generation/home"), "profile": str(self.root / ".codex"),
                  "memory": str(self.root / "generation/home/memory"), "enabled": ["semantic"],
                  "runtime": "codex", "source_revision": "isolated-source-control", "id": "fixture"}
        self.before = {"complete": False, "readable": True, "corpus_readable": True, "hashes_available": True,
                       "schema_version": 3, "problems": [], "duplicates": [], "activation_inconsistent": [],
                       "missing": ["new_compaction"], "stale": [], "extra": [], "sidecar_sha256": "before"}
        self.after = {**self.before, "complete": True, "missing": [], "sidecar_sha256": "after", "eligible": 2}
        self.coverages = [self.before, self.after]
        self.calls = []
        self.reject = None
        self.body_status = "ok"

    def tearDown(self):
        self.tmp.cleanup()

    def native(self, deployment, tool, args, **kwargs):
        self.calls.append((tool, list(args)))
        if tool == "mesh" and args[0] == self.reject:
            raise c.ConnectorError("deliberate lease refusal")
        if tool == "script" and args[0] == "dreaming/embedding_coverage.py":
            row = self.coverages.pop(0)
            return subprocess.CompletedProcess(args, 0 if row["complete"] else 1, json.dumps(row), "")
        if tool == "script" and args[0] == "governor/compute_governor_posix.py":
            c.atomic(Path(self.d["home"]) / ".claude/compute/compute_state.json",
                     {"status": self.body_status, "mode": "owner_present_polite"})
        return subprocess.CompletedProcess(args, 0, "", "")

    def exercise(self):
        return upkeep.ensure_current(self.d, "actual-session-control")

    def test_current_coverage_has_no_maintenance_or_mesh_mutation(self):
        self.coverages = [self.after]
        with patch.object(c, "native", side_effect=self.native), patch.object(c, "maintain_recall") as maintain:
            self.assertEqual(self.exercise()["state"], "current")
            maintain.assert_not_called()
        self.assertFalse(any(tool == "mesh" for tool, _ in self.calls))

    def test_clean_drift_uses_original_maintenance_and_independent_complete_readback(self):
        with patch.object(c, "native", side_effect=self.native), patch.object(c, "maintain_recall", return_value={"coverage": "complete"}) as maintain:
            result = self.exercise()
            maintain.assert_called_once_with(self.d)
        self.assertEqual(result["state"], "repaired")
        self.assertEqual(result["before_missing"], ["new_compaction"])
        self.assertEqual(result["after_sidecar_sha256"], "after")
        actions = [args[0] for tool, args in self.calls if tool == "mesh"]
        self.assertEqual(actions, ["hello", "digest", "claim", "assert", "assert", "release", "bye"])
        self.assertTrue((Path(self.d["home"]).parent / "recall-admission-upkeep.json").is_file())

    def test_failed_claim_or_assert_never_reaches_maintenance(self):
        for rejected in ("claim", "assert"):
            with self.subTest(rejected=rejected):
                self.calls = []
                self.coverages = [copy.deepcopy(self.before)]
                self.reject = rejected
                with patch.object(c, "native", side_effect=self.native), patch.object(c, "maintain_recall") as maintain:
                    with self.assertRaisesRegex(c.ConnectorError, "lease refusal"):
                        self.exercise()
                    maintain.assert_not_called()
                self.assertFalse(any(tool == "script" and args[0].startswith("governor/") for tool, args in self.calls))

    def test_corrupt_or_unreadable_authority_is_not_repaired_as_drift(self):
        for change in ({"problems": ["model mismatch"]}, {"readable": False}, {"hashes_available": False}, {"activation_inconsistent": ["old"]}):
            with self.subTest(change=change):
                self.calls = []
                self.coverages = [{**self.before, **change}]
                with patch.object(c, "native", side_effect=self.native), patch.object(c, "maintain_recall") as maintain:
                    with self.assertRaisesRegex(c.ConnectorError, "not clean corpus drift"):
                        self.exercise()
                    maintain.assert_not_called()
                self.assertFalse(any(tool == "mesh" for tool, _ in self.calls))

    def test_body_deferral_prevents_model_work_and_maintenance(self):
        self.body_status = "distress"
        with patch.object(c, "native", side_effect=self.native), patch.object(c, "maintain_recall") as maintain:
            with self.assertRaisesRegex(c.ConnectorError, "compute/body"):
                self.exercise()
            maintain.assert_not_called()
        self.assertFalse(any(tool == "script" and args[0] == "dreaming/query_embed.py" for tool, args in self.calls))

    def test_exit_success_without_complete_effect_cannot_pass(self):
        self.coverages = [self.before, self.before]
        with patch.object(c, "native", side_effect=self.native), patch.object(c, "maintain_recall", return_value={"claimed": "success"}):
            with self.assertRaisesRegex(c.ConnectorError, "did not establish complete"):
                self.exercise()
        self.assertFalse((Path(self.d["home"]).parent / "recall-admission-upkeep.json").exists())
        self.assertTrue(any(tool == "mesh" and args[:1] == ["release"] for tool, args in self.calls))

    def test_explicit_maintenance_lock_conflict_prevents_model_work(self):
        with c.lock(c.profile_path(self.d["profile"])):
            with patch.object(c, "native", side_effect=self.native), patch.object(c, "maintain_recall") as maintain:
                with self.assertRaises(c.ConnectorError):
                    self.exercise()
                maintain.assert_not_called()
        self.assertFalse(self.calls, "Lock refusal must not touch another invocation's mesh identity")

    def test_same_native_session_reuses_canonical_identity(self):
        for _ in range(2):
            self.coverages = [copy.deepcopy(self.before), copy.deepcopy(self.after)]
            with patch.object(c, "native", side_effect=self.native), patch.object(c, "maintain_recall", return_value={}):
                self.exercise()
        owners = [args[args.index("--agent") + 1] for tool, args in self.calls if tool == "mesh" and args[0] == "hello"]
        self.assertEqual(len(owners), 2)
        self.assertEqual(len(set(owners)), 1)

    def test_failed_builder_releases_only_its_job_and_propagates_failure(self):
        with patch.object(c, "native", side_effect=self.native), patch.object(c, "maintain_recall", side_effect=c.ConnectorError("builder failed")):
            with self.assertRaisesRegex(c.ConnectorError, "builder failed"):
                self.exercise()
        actions = [args[0] for tool, args in self.calls if tool == "mesh"]
        self.assertEqual(actions[-2:], ["release", "bye"])
        self.assertFalse((Path(self.d["home"]).parent / "recall-admission-upkeep.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
