"""Discriminating controls for identity, project isolation and fail-closed admission.

Synthetic guard fixtures are isolated; the real native writer/receiver controls
remain in test_connector.py and brainctl admit. No fixture certifies a live brain.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import brain_binding as b
import connector as c
from receiver_probe import worker_dispatch_observation


class BindingControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brain-binding-controls-")
        self.root = Path(self.temp.name)
        self.profile = self.root / "main profile λ"
        self.project_profile = self.root / "project profile"
        self.workspace = self.root / "project workspace"
        self.workspace.mkdir()
        self.other = self.root / "other workspace"
        self.other.mkdir()
        self.deployments = {}
        for profile, name in ((self.profile, "main"), (self.project_profile, "project")):
            home = profile / "brain-framework/generations" / name / "home"
            dreaming = home / ".claude/dreaming"
            dreaming.mkdir(parents=True)
            (dreaming / "BRAIN_SLUG").write_text(name)
            memory = home / ".claude/projects" / name / "memory"
            memory.mkdir(parents=True)
            (memory / "MEMORY.md").write_text("# Synthetic fixture index\n")
            self.deployments[str(profile)] = {"id": name, "profile": str(profile), "home": str(home), "memory": str(memory),
                "source": str(self.root), "source_revision": c.source_lock()["revision"], "slug": name,
                "runtime": "codex", "python": sys.executable, "enabled": []}
        self.active = patch.object(c, "active", side_effect=lambda p, **kw: self.deployments[str(p)])
        self.active.start()
        b.bind_main(self.profile, "Synthetic owner's explicit main designation", apply=True)

    def tearDown(self):
        self.active.stop()
        self.temp.cleanup()

    def test_designation_plan_has_no_registry_write(self):
        before = b.registry_path(self.profile).read_bytes()
        proposed = b.bind_main(self.profile, "Different synthetic evidence", apply=False)
        self.assertFalse(proposed["applied"])
        self.assertEqual(before, b.registry_path(self.profile).read_bytes())

    def test_unbound_cli_never_creates_a_substitute_profile(self):
        missing = self.root / "no brain here"
        result = subprocess.run([sys.executable, str(c.SKILL / "scripts/brainctl.py"), "--profile", str(missing), "recall", "identity"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No explicit main", result.stderr)
        self.assertFalse(missing.exists())

    def test_project_never_replaces_main_and_wrong_workspace_is_rejected(self):
        b.bind_project(self.profile, "alpha", self.project_profile, [self.workspace], "Synthetic project authorization", apply=True)
        self.assertEqual(b.resolve(self.profile)["id"], "main")
        self.assertEqual(b.resolve(self.profile, "alpha", self.workspace)["id"], "project")
        with self.assertRaisesRegex(c.ConnectorError, "outside"):
            b.resolve(self.profile, "alpha", self.other)
        with self.assertRaisesRegex(c.ConnectorError, "select one explicitly"):
            b.selection(self.profile, workspace=self.workspace)
        data = c.read_json(b.registry_path(self.profile))
        data["main"]["id"] = "lost-main"
        c.atomic(b.registry_path(self.profile), data)
        with self.assertRaisesRegex(c.ConnectorError, "changed at id"):
            b.resolve(self.profile, "alpha", self.workspace)

    def test_same_corpus_cannot_be_relabeled_a_project(self):
        with self.assertRaisesRegex(c.ConnectorError, "separate corpus"):
            b.bind_project(self.profile, "alias", self.profile, [self.workspace], "Synthetic authorization", apply=True)

    def test_project_profile_can_share_the_explicit_existing_main(self):
        b.bind_main(self.project_profile, "Synthetic authorization to share main", True, self.profile)
        self.assertEqual(b.resolve(self.project_profile)["profile"], str(self.profile))
        b.bind_project(self.project_profile, "alpha", self.project_profile, [self.workspace], "Synthetic project authorization", True)
        self.assertEqual(b.resolve(self.project_profile, "alpha", self.workspace)["id"], "project")

    def test_redirected_path_and_changed_slug_block_but_restore_allows(self):
        marker = Path(self.deployments[str(self.profile)]["home"]) / ".claude/dreaming/BRAIN_SLUG"
        marker.write_text("another-corpus")
        with self.assertRaisesRegex(c.ConnectorError, "marker changed"):
            b.resolve(self.profile)
        marker.write_text("main")
        self.assertEqual(b.resolve(self.profile)["id"], "main")
        data = c.read_json(b.registry_path(self.profile))
        data["main"]["resolved_memory"] = str(self.other)
        c.atomic(b.registry_path(self.profile), data)
        with self.assertRaisesRegex(c.ConnectorError, "different location"):
            b.resolve(self.profile)

    def require_for(self, session, workspace, brain="main"):
        # Existing isolated tests name synthetic contexts; make that host input explicit.
        with patch.dict(os.environ, {"CODEX_THREAD_ID": session}):
            return b.require_admission(self.profile, session, workspace, brain)

    def test_fingerprint_kind_is_explicit_and_wrong_kind_cannot_authorize_a_writer(self):
        report, _, _ = self.exercise_admit("typed-fingerprint")
        self.assertEqual(report["receiver"]["fingerprint_kind"], "admission")
        self.assertTrue(all(row["fingerprint_kind"] == "admission" for row in report["brains"]))
        report["receiver"]["fingerprint_kind"] = "deployment"
        c.atomic(b.activation_path(self.profile, "typed-fingerprint", self.other), report)
        with patch("receiver_probe.deployment_fingerprint", side_effect=lambda d: "fixture-fingerprint-" + d["id"]), patch.object(c, "verify_runtime_code"):
            with self.assertRaisesRegex(c.ConnectorError, "Invoking receiver changed"):
                self.require_for("typed-fingerprint", self.other)
            report["receiver"]["fingerprint_kind"] = "admission"
            report["brains"][0]["fingerprint_kind"] = "deployment"
            c.atomic(b.activation_path(self.profile, "typed-fingerprint", self.other), report)
            with self.assertRaisesRegex(c.ConnectorError, "Brain code/configuration/binding changed"):
                self.require_for("typed-fingerprint", self.other)

    def exercise_admit(self, session="fixture-a", ready=True, complete=True, brain="main", workspace=None, host_session="same"):
        signal = session if host_session == "same" else host_session
        inventory = {"items": [{"id": "S01", "capability": "core", "checks": [
            {"requirement": "Current locked source", "status": "checked-current-observation" if complete else "unchecked"}
        ]}], "selected_capabilities": {"core": ["deployment.enabled"]}}
        with patch.dict(os.environ, {"CODEX_THREAD_ID": signal or ""}), \
             patch.object(c, "verify", return_value={"ready": ready}) as verify, \
             patch.object(c, "verify_source"), patch.object(c, "verify_runtime_code"), \
             patch("setup_checklist.inspect", return_value=inventory) as checklist, \
             patch.object(c, "native", side_effect=lambda d, t, a, **kw: subprocess.CompletedProcess([], 0, "BRAIN NODE: " + a[1], "")), \
             patch("receiver_probe.deployment_fingerprint", side_effect=lambda d: "fixture-fingerprint-" + d["id"]):
            report = b.admit(self.profile, session, workspace or self.other, brain)
        return report, verify, checklist

    def test_every_admission_recomputes_and_one_unchecked_obligation_blocks(self):
        passed, verify, checklist = self.exercise_admit()
        self.assertTrue(passed["passed"])
        verify.assert_called_once_with(self.deployments[str(self.profile)], integrated=True, hooks=True, smoke=True)
        checklist.assert_called_once_with(self.profile, observation={"ready": True}, persist=False)
        failed, verify, _ = self.exercise_admit(complete=False)
        self.assertFalse(failed["passed"])
        verify.assert_called_once()
        with self.assertRaisesRegex(c.ConnectorError, "failed, stale"):
            self.require_for( "fixture-a", self.other)

    def test_future_transfer_is_visible_and_never_satisfies_current_admission(self):
        live = {"requirement": "Working main", "status": "checked-current-observation"}
        transfer = {"requirement": "New installation", "status": "unchecked", "scope": "qualification"}
        report = {"items": [{"id": "S03", "capability": "core", "checks": [live, transfer]}],
                  "selected_capabilities": {"core": ["deployment.enabled"]}}
        result = b.applicable_setup(report, False)
        self.assertTrue(result["complete"])
        self.assertEqual(result["qualification_only"][0]["status"], "unchecked")
        live["status"] = "unchecked"
        self.assertFalse(b.applicable_setup(report, False)["complete"])
        self.assertFalse(b.applicable_setup({"complete": True}, False)["complete"])

    def test_failed_runtime_and_missing_wake_node_each_block_independently(self):
        failed, _, _ = self.exercise_admit(ready=False)
        self.assertFalse(failed["passed"])
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "missing-node"}), \
             patch.object(c, "verify", return_value={"ready": True}), \
             patch.object(c, "verify_source"), patch.object(c, "verify_runtime_code"), \
             patch("setup_checklist.inspect", return_value={"complete": True}), \
             patch.object(c, "native", return_value=subprocess.CompletedProcess([], 0, "claimed success but no node", "")), \
             patch("receiver_probe.deployment_fingerprint", return_value="fixture"):
            failed = b.admit(self.profile, "missing-node", self.other)
        self.assertFalse(failed["passed"])

    def test_project_admission_checks_both_brains(self):
        b.bind_project(self.profile, "alpha", self.project_profile, [self.workspace], "Synthetic project authorization", True)
        report, verify, _ = self.exercise_admit(brain="alpha", workspace=self.workspace)
        self.assertTrue(report["passed"])
        self.assertEqual([r["brain"] for r in report["brains"]], ["main", "alpha"])
        self.assertEqual(verify.call_count, 2)

    def test_session_receipts_do_not_overwrite_one_another_and_stale_mutations_fail(self):
        self.exercise_admit("session-a")
        self.exercise_admit("session-b")
        with patch("receiver_probe.deployment_fingerprint", side_effect=lambda d: "fixture-fingerprint-" + d["id"]), \
             patch.object(c, "verify_runtime_code"):
            self.assertTrue(self.require_for( "session-a", self.other)["passed"])
            self.assertTrue(self.require_for( "session-b", self.other)["passed"])
            with self.assertRaisesRegex(c.ConnectorError, "requires current"):
                self.require_for( "no-such-session", self.other)
            path = b.activation_path(self.profile, "session-a", self.other)
            data = c.read_json(path)
            data["at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
            c.atomic(path, data)
            with self.assertRaisesRegex(c.ConnectorError, "failed, stale"):
                self.require_for( "session-a", self.other)

    def test_changed_configuration_invalidates_mutation_admission(self):
        self.exercise_admit()
        with patch("receiver_probe.deployment_fingerprint", return_value="changed-config"):
            with self.assertRaisesRegex(c.ConnectorError, "receiver changed|changed since admission"):
                self.require_for( "fixture-a", self.other)

    def test_parent_receipt_cannot_authorize_child_context(self):
        report, _, _ = self.exercise_admit("parent-context")
        self.assertTrue(report["passed"])
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "child-context"}), \
             patch("receiver_probe.deployment_fingerprint", side_effect=lambda d: "fixture-fingerprint-" + d["id"]), \
             patch.object(c, "verify_runtime_code"), \
             patch.object(c, "native") as native:
            with self.assertRaisesRegex(c.ConnectorError, "Current native session differs"):
                b.require_admission(self.profile, "parent-context", self.other)
        native.assert_not_called()
        self.assertEqual(report["native_context"]["session"], "parent-context")
        self.assertTrue(self.exercise_admit("child-context")[0]["passed"])

    def test_mismatched_admission_is_rejected_before_source_or_host_effects(self):
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "actual-context"}), \
             patch.object(c, "verify_source") as source, \
             patch.object(c, "verify_runtime_code"), \
             patch.object(c, "verify", return_value={"ready": True}) as verify, \
             patch("setup_checklist.inspect", return_value={"complete": True}), \
             patch("receiver_probe.deployment_fingerprint", return_value="fixture"), \
             patch.object(c, "native", side_effect=lambda d, t, a, **kw: subprocess.CompletedProcess([], 0, "BRAIN NODE: " + a[1], "")) as native:
            with self.assertRaisesRegex(c.ConnectorError, "Current native session differs"):
                b.admit(self.profile, "caller-named-context", self.other)
        source.assert_not_called()
        verify.assert_not_called()
        native.assert_not_called()
        self.assertFalse(b.activation_path(self.profile, "caller-named-context", self.other).exists())

    def test_missing_native_signal_keeps_full_diagnostic_blocked(self):
        with patch("recall_upkeep.ensure_current") as upkeep:
            report, verify, checklist = self.exercise_admit("missing-signal", host_session=None)
        upkeep.assert_not_called()
        self.assertFalse(report["passed"])
        self.assertFalse(report["native_context"]["verified"])
        self.assertIn("unavailable", report["native_context"]["error"])
        self.assertTrue(report["brains"][0]["verification"]["ready"])
        self.assertEqual(len(report["brains"][0]["wake_nodes"]), len(b.WAKE_NODES))
        verify.assert_called_once()
        checklist.assert_called_once()
        with self.assertRaisesRegex(c.ConnectorError, "failed, stale"):
            self.require_for("missing-signal", self.other)
        self.assertTrue(self.exercise_admit("missing-signal")[0]["passed"])

    def test_pre_context_receipt_and_lost_native_identity_cannot_authorize_writes(self):
        self.exercise_admit("old-receipt")
        path = b.activation_path(self.profile, "old-receipt", self.other)
        saved = c.read_json(path)
        del saved["native_context"]
        c.atomic(path, saved)
        with self.assertRaisesRegex(c.ConnectorError, "current trusted native context"):
            self.require_for("old-receipt", self.other)
        self.exercise_admit("lost-signal")
        with patch.dict(os.environ, {"CODEX_THREAD_ID": ""}):
            with self.assertRaisesRegex(c.ConnectorError, "current trusted native context"):
                b.require_admission(self.profile, "lost-signal", self.other)

    def test_runtime_signals_are_separate_and_unknown_runtime_is_blocked(self):
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "codex-session", "CLAUDE_CODE_SESSION_ID": "claude-session"}):
            self.assertTrue(b.native_session_context({"runtime": "codex"}, "codex-session")["verified"])
            self.assertTrue(b.native_session_context({"runtime": "claude"}, "claude-session")["verified"])
            self.assertFalse(b.native_session_context({"runtime": "unqualified"}, "codex-session")["verified"])
            with self.assertRaisesRegex(c.ConnectorError, "Current native session differs"):
                b.native_session_context({"runtime": "codex"}, "claude-session")

    def test_native_hook_event_does_not_inherit_parent_writer_identity(self):
        self.exercise_admit("native-child")
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "native-parent"}), \
             patch("receiver_probe.deployment_fingerprint", side_effect=lambda d: "fixture-fingerprint-" + d["id"]), \
             patch.object(c, "verify_runtime_code"):
            report = b.require_hook_admission(self.profile, {"session_id": "native-child", "cwd": str(self.other)})
            self.assertTrue(report["passed"])
            with self.assertRaisesRegex(c.ConnectorError, "Current native session differs"):
                b.require_admission(self.profile, "native-child", self.other)

    def test_native_hook_cannot_borrow_a_parent_receipt_or_guess_absent_event(self):
        self.exercise_admit("native-parent")
        with self.assertRaisesRegex(c.ConnectorError, "requires current"):
            b.require_hook_admission(self.profile, {"session_id": "native-child", "cwd": str(self.other)})
        for payload in ({}, {"cwd": str(self.other)}, {"session_id": "native-parent"}, None):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(c.ConnectorError, "host event's actual"):
                    b.require_hook_admission(self.profile, payload)

    def test_native_hook_still_requires_a_verified_current_admission_context(self):
        self.exercise_admit("native-child")
        path = b.activation_path(self.profile, "native-child", self.other)
        original = c.read_json(path)
        for value in ({}, {"verified": False}, {**original["native_context"], "session": "native-parent"}):
            with self.subTest(value=value):
                c.atomic(path, {**original, "native_context": value})
                with self.assertRaisesRegex(c.ConnectorError, "current trusted native context"):
                    b.require_hook_admission(self.profile, {"session_id": "native-child", "cwd": str(self.other)})

    def test_tampered_runtime_blocks_before_any_native_host_runs(self):
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "tampered-fixture"}), \
             patch.object(c, "verify_source"), \
             patch.object(c, "verify_runtime_code", side_effect=c.ConnectorError("runtime differs")), \
             patch.object(c, "verify") as verify, patch.object(c, "native") as native:
            with self.assertRaisesRegex(c.ConnectorError, "runtime differs"):
                b.admit(self.profile, "tampered-fixture", self.other)
        verify.assert_not_called()
        native.assert_not_called()

    def test_healthy_external_brain_cannot_admit_an_unqualified_receiver(self):
        # A third controller can name main, but its unrelated runtime/corpus
        # cannot borrow main's receiver qualification.
        controller = self.root / "unqualified receiver"
        self.deployments[str(controller)] = dict(self.deployments[str(self.project_profile)], profile=str(controller), id="unqualified")
        b.bind_main(controller, "Synthetic designation of the real main", True, self.profile)
        with patch.object(c, "verify") as verify, patch.object(c, "native") as native:
            with self.assertRaisesRegex(c.ConnectorError, "Invoking receiver"):
                b.admit(controller, "unqualified-session", self.other)
        verify.assert_not_called()
        native.assert_not_called()

    def test_main_only_work_does_not_require_optional_project_store_creation(self):
        checklist = {"complete": False, "items": [{"id": "S23", "checks": [{"requirement": "Initialize selected specialist store", "status": "unchecked"}]}]}
        main = b.applicable_setup(checklist, project_selected=False)
        self.assertTrue(main["complete"])
        self.assertEqual(checklist["items"][0]["checks"][0]["status"], "unchecked")
        self.assertFalse(b.applicable_setup(checklist, project_selected=True)["complete"])
        checklist["items"].append({"id": "S11", "checks": [{"requirement": "Native main memory writer", "status": "unchecked"}]})
        self.assertFalse(b.applicable_setup(checklist, project_selected=False)["complete"])

    def test_actual_selected_faculties_are_required_and_unselected_remain_visible(self):
        inventory = {"selected_capabilities": {"core": ["deployment.enabled"], "reflex": ["codex_reflex_hook.py"]},
                     "items": [{"id": "S13", "capability": "reflex", "checks": [{"requirement": "Reflex control", "status": "unchecked"}]},
                               {"id": "S36", "capability": "literature", "checks": [{"requirement": "Literature store", "status": "unchecked"}]}]}
        scoped = b.applicable_setup(inventory, False)
        self.assertFalse(scoped["complete"])
        self.assertEqual([r["id"] for r in scoped["open_required_checks"]], ["S13"])
        inventory["items"][0]["checks"][0]["status"] = "checked-evidence"
        self.assertTrue(b.applicable_setup(inventory, False)["complete"])
        self.assertEqual(inventory["items"][1]["checks"][0]["status"], "unchecked")
        inventory["items"][0]["checks"][0]["status"] = "unknown"
        self.assertFalse(b.applicable_setup(inventory, False)["complete"])

    def test_native_worker_start_events_are_evidence_and_zero_or_multiple_are_rejected(self):
        def event(identity):
            return {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "SubAgentActivity", "kind": "started", "agent_thread_id": identity}}}
        self.assertEqual(worker_dispatch_observation([event("worker-one")])["started_ids"], ["worker-one"])
        for rows in [[], [event("one"), event("two")]]:
            with self.assertRaises(c.ConnectorError):
                worker_dispatch_observation(rows)
        raw = {"payload": {"type": "function_call", "name": "spawn_agent"}}
        self.assertEqual(worker_dispatch_observation([raw])["raw_dispatches"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
