"""Synthetic host/corpus controls; none certifies a private Brain or native UI."""
from __future__ import annotations

import ast
import contextlib
import copy
import datetime as dt
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

import connector as c
import codex_health_observer as health
import posix_wake_adapter as wake
import receiver_probe


class ObserverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix="brain-host-observer-controls-")
        cls.source = c.acquire_source(Path(cls.work.name) / "public-source-fixture")

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.work.name)
        self.root = Path(self.temp.name)
        self.profile = self.root / "external codex profile"
        self.home = self.root / "private virtual HOME"
        self.dreaming = self.home / ".claude/dreaming"
        self.memory = self.home / "memory"
        self.memory.mkdir(parents=True)
        self.dreaming.mkdir(parents=True)
        (self.profile / "skills/brain-framework/scripts").mkdir(parents=True)
        (self.profile / "skills/brain-framework/SKILL.md").write_text("synthetic fingerprint fixture only\n")
        self.bridge = self.profile / "skills/brain-framework/scripts/hook_bridge.py"
        self.bridge.write_text("# synthetic compile-only bridge\n")
        target = self.dreaming / "original_handler.py"
        target.write_text("# synthetic compile-only handler\n")
        group = {"matcher": "*", "hooks": [{"type": "command", "command": '%s --profile "%s" --handler SessionStart-probe' % (self.bridge, self.profile), "timeout": 30}]}
        # Quote the bridge path exactly as a normal registered command does.
        import shlex
        group["hooks"][0]["command"] = shlex.join([sys.executable, str(self.bridge), "--profile", str(self.profile), "--handler", "SessionStart-probe"])
        self.hooks = {"SessionStart": [group]}
        self.d = {"id": "synthetic-host", "profile": str(self.profile), "home": str(self.home), "memory": str(self.memory),
                  "source": str(self.source), "source_revision": c.source_lock()["revision"], "runtime": "codex", "runtime_code": {},
                  "enabled": [], "handlers": {"SessionStart-probe": {"event": "SessionStart", "argv": [sys.executable, str(target)]}},
                  "managed_hook_groups": copy.deepcopy(self.hooks), "hook_config": str(self.profile / "hooks.json")}
        self.save_hooks()
        self.config = self.profile / "config.toml"
        key = str(self.profile / "hooks.json") + ":session_start:0:0"
        self.config.write_text('[hooks.state.' + json.dumps(key) + ']\ntrusted_hash = "synthetic-shape-only-not-trust"\n')
        self.receiver = patch.object(receiver_probe, "validate_receivers", return_value={"passed": False, "reason": "synthetic fixture has no qualified receiver"})
        self.receiver.start()
        self.guardian = patch.object(c, "verify_guardian", return_value={"synthetic": True})
        self.guardian.start()

    def tearDown(self):
        self.receiver.stop()
        self.guardian.stop()
        self.temp.cleanup()

    def save_hooks(self):
        (self.profile / "hooks.json").write_text(json.dumps({"hooks": self.hooks}))

    def record(self, rc, seconds=0):
        when = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)
        row = {"at": when.isoformat(), "revision": self.d["source_revision"], "handler": "SessionStart-probe", "rc": rc, "session_hash": "0" * 64}
        with (self.home.parent / "hook-delivery.jsonl").open("a") as stream:
            stream.write(json.dumps(row) + "\n")

    def execute(self, mode, adapted=True, blocked=1, retired_blocked=False, attested_host=False):
        calls = []
        def check(text):
            calls.append(text)
            retired = "grid backtest" in text or "Account7" in text
            return {"should_block": retired_blocked if retired else len([x for x in calls if "grid backtest" not in x and "Account7" not in x]) <= blocked}
        fake = types.SimpleNamespace(check=check)
        # Scope every original fallback path to this synthetic fixture.
        expand = lambda path: str(self.home) + str(path)[1:] if str(path).startswith("~") else str(path)
        modules = {"pre_report": fake,
                   "codex_warning_acceptance": types.SimpleNamespace(_selftest=lambda: 0, validate=lambda _: []),
                   "codex_reflex_hook": types.SimpleNamespace(_selftest=lambda: 0),
                   "codex_stop_guard": types.SimpleNamespace(_selftest=lambda: 0)}
        with patch.object(os.path, "expanduser", side_effect=expand), patch.dict(sys.modules, modules):
            if adapted:
                ns = health.original_namespace(self.d, mode)
            else:
                relative = health.SOURCE_HASHES[mode][0]
                ns = {"__name__": "raw_source_control", "__file__": str(self.dreaming / Path(relative).name)}
                exec(compile((self.source / relative).read_bytes(), str(self.source / relative), "exec"), ns)
                ns.update(HOME=str(self.home), MEM=str(self.memory), MEMDIR=str(self.memory))
            ns["_brain_wrapper_controls"] = lambda: []
            if attested_host:
                ns["_brain_observe_host"] = lambda: {"failures": [], "unqualified": [], "profile": str(self.profile)}
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                ns["main"]()
        return output.getvalue(), calls

    def test_external_profile_is_observed_while_virtual_codex_is_absent(self):
        result = health.observe_host(self.d)
        self.assertTrue(result["configuration_valid"])
        self.assertEqual(result["registered_commands"], 1)
        self.assertFalse((self.home / ".codex/hooks.json").exists())
        self.assertFalse(result["failures"])
        self.assertTrue(any("UNQUALIFIED" in value for value in result["unqualified"]))

    def test_missing_actual_owned_registration_fails_and_restoration_passes(self):
        self.hooks["SessionStart"] = []
        self.save_hooks()
        fault = health.observe_host(self.d)
        self.assertFalse(fault["configuration_valid"])
        self.assertIn("occurs 0 times", fault["failures"][0])
        self.hooks = copy.deepcopy(self.d["managed_hook_groups"])
        self.save_hooks()
        self.assertTrue(health.observe_host(self.d)["configuration_valid"])

    def test_duplicate_owned_registration_is_not_a_green_registry(self):
        self.hooks["SessionStart"].append(copy.deepcopy(self.hooks["SessionStart"][0]))
        self.save_hooks()
        self.assertIn("occurs 2 times", health.observe_host(self.d)["failures"][0])

    def test_virtual_profile_cannot_rescue_a_missing_external_profile(self):
        virtual = self.home / ".codex/hooks.json"
        virtual.parent.mkdir()
        virtual.write_text((self.profile / "hooks.json").read_text())
        (self.profile / "hooks.json").unlink()
        result = health.observe_host(self.d)
        self.assertFalse(result["configuration_valid"])
        self.assertIn(str(self.profile), result["failures"][0])

    def test_wrong_hook_config_binding_is_unverified(self):
        self.d["hook_config"] = str(self.home / ".codex/hooks.json")
        self.assertFalse(health.observe_host(self.d)["configuration_valid"])

    def test_profile_symlink_input_is_unverified(self):
        path = self.profile / "hooks.json"
        target = self.root / "other.json"
        path.rename(target)
        path.symlink_to(target)
        self.assertFalse(health.observe_host(self.d)["configuration_valid"])

    def test_malformed_hook_registry_is_not_quiet(self):
        (self.profile / "hooks.json").write_text('{"hooks":{"SessionStart":"not groups"}}')
        self.assertFalse(health.observe_host(self.d)["configuration_valid"])

    def test_review_record_presence_does_not_certify_native_behavior(self):
        result = health.observe_host(self.d)
        self.assertEqual(result["stored_review_records"], 1)
        self.assertFalse(result["receiver"]["passed"])
        self.assertTrue(result["unqualified"])

    def test_outside_guardian_failure_is_not_hidden_by_valid_registrations(self):
        with patch.object(c, "verify_guardian", side_effect=c.ConnectorError("outside guardian changed")):
            result = health.observe_host(self.d)
        self.assertFalse(result["configuration_valid"])
        self.assertTrue(any("outside guardian changed" in item for item in result["failures"]))

    def test_missing_review_record_stays_unqualified(self):
        self.config.write_text("")
        self.assertTrue(any("review records missing" in x for x in health.observe_host(self.d)["unqualified"]))

    def test_owned_bridged_handler_selects_real_reader(self):
        target = self.dreaming / "owner_prompts_hook.py"
        target.write_text("# synthetic reader\n")
        self.d["handlers"]["SessionStart-probe"]["argv"] = [sys.executable, str(target)]
        namespace = health.original_namespace(self.d, "liveness")
        self.assertTrue(namespace["_wired_anywhere"]("owner_prompts_hook.py"))

    def test_inactive_copied_list_does_not_select_reader(self):
        (self.dreaming / "hook_dispatch_list.json").write_text('{"command":"owner_prompts_hook.py"}')
        self.config.write_text(self.config.read_text() + "enabled=false\n")
        namespace = health.original_namespace(self.d, "liveness")
        self.assertFalse(namespace["_wired_anywhere"]("owner_prompts_hook.py"))

    def test_native_disabled_owned_definition_is_not_eligible_and_restoration_passes(self):
        original = self.config.read_text()
        self.config.write_text(original + "enabled = false\n")
        disabled = health.observe_host(self.d)
        self.assertFalse(disabled["configuration_valid"])
        self.assertEqual(disabled["configured_eligible_commands"], 0)
        self.assertFalse(health.wired_originals(self.d, self.hooks))
        self.config.write_text(original)
        restored = health.observe_host(self.d)
        self.assertTrue(restored["configuration_valid"])
        self.assertEqual(restored["configured_eligible_commands"], 1)

    def test_native_feature_flags_follow_observed_canonical_alias_precedence(self):
        original = self.config.read_text()
        for flags, expected in (("hooks = false", False), ("codex_hooks = false", False),
                                ("hooks = true\ncodex_hooks = false", True),
                                ("hooks = false\ncodex_hooks = true", False)):
            with self.subTest(flags=flags):
                self.config.write_text(original + "[features]\n" + flags + "\n")
                result = health.observe_host(self.d)
                self.assertEqual(result["configuration_valid"], expected)
                self.assertEqual(result["configured_eligible_commands"], int(expected))

    def test_unrelated_external_dispatcher_basename_does_not_select_this_deployment(self):
        external = self.root / "unrelated hooks/hook_dispatch.py"
        external.parent.mkdir()
        external.write_text("# unrelated external hook with the same basename\n")
        import shlex
        self.hooks["UserPromptSubmit"] = [{"hooks": [{"type": "command", "command": shlex.join([sys.executable, str(external)])}]}]
        self.save_hooks()
        key = str(self.profile / "hooks.json") + ":user_prompt_submit:0:0"
        self.config.write_text(self.config.read_text() + '[hooks.state.' + json.dumps(key) + ']\ntrusted_hash="synthetic-record"\n')
        self.assertFalse(health.dispatcher_registered(self.d, self.hooks))

    def test_disabled_deployment_dispatcher_is_not_selected(self):
        target = self.dreaming / "hook_dispatch.py"
        target.write_text("# synthetic actual deployment dispatcher\n")
        self.d["handlers"]["SessionStart-probe"]["argv"] = [sys.executable, str(target)]
        self.config.write_text(self.config.read_text() + "enabled=false\n")
        self.assertFalse(health.dispatcher_registered(self.d, self.hooks))

    def test_dispatcher_path_passed_as_data_is_not_executed_registration(self):
        import shlex
        target = self.dreaming / "hook_dispatch.py"
        key = str(self.profile / "hooks.json") + ":user_prompt_submit:0:0"
        self.config.write_text(self.config.read_text() + '[hooks.state.' + json.dumps(key) + ']\ntrusted_hash="synthetic-record"\n')
        for tokens in (["printf", "%s", str(target)], [sys.executable, "-c", "print('fixture')", str(target)]):
            with self.subTest(tokens=tokens):
                self.hooks["UserPromptSubmit"] = [{"hooks": [{"command": shlex.join(tokens)}]}]
                self.save_hooks()
                self.assertFalse(health.dispatcher_registered(self.d, self.hooks))

    def test_foreign_profile_bridge_reference_cannot_select_this_deployments_originals(self):
        import shlex
        tokens = shlex.split(self.hooks["SessionStart"][0]["hooks"][0]["command"])
        tokens[tokens.index("--profile") + 1] = str(self.root / "another profile")
        self.hooks["SessionStart"][0]["hooks"][0]["command"] = shlex.join(tokens)
        self.save_hooks()
        self.assertFalse(health.wired_originals(self.d, self.hooks))

    def test_latest_protocol_failure_and_actual_recovery_are_distinguished(self):
        self.record(2)
        self.assertTrue(health.journal_observation(self.d)["unrecovered"])
        self.record(0, 1)
        result = health.journal_observation(self.d)
        self.assertEqual(result["rows"], 2)
        self.assertFalse(result["unrecovered"])
        self.assertFalse(health.observe_host(self.d)["receiver"]["passed"])

    def test_protocol_tail_malformed_rows_remain_unverified(self):
        (self.home.parent / "hook-delivery.jsonl").write_text("not JSON\n")
        self.assertTrue(health.journal_observation(self.d)["unqualified"])

    def test_source_dispatcher_marker_is_not_actual_registration(self):
        (self.dreaming / "HOOK_DISPATCH_ENABLED").touch()
        (self.dreaming / "hook_dispatch.py").write_text("# copied source marker fixture\n")
        self.assertFalse(health.dispatcher_registered(self.d, self.hooks))
        output, _ = self.execute("liveness")
        self.assertIn("dispatcher allowlist   not selected", output)
        state = json.loads((self.dreaming / ".liveness_status.json").read_text())
        self.assertFalse(any("dispatcher allowlist missing" in x for x in state["artifacts"]))
        self.hooks["UserPromptSubmit"] = [{"hooks": [{"command": 'python "' + str(self.dreaming / "hook_dispatch.py") + '"'}]}]
        self.save_hooks()
        key = str(self.profile / "hooks.json") + ":user_prompt_submit:0:0"
        self.config.write_text(self.config.read_text() + '[hooks.state.' + json.dumps(key) + ']\ntrusted_hash="synthetic-record"\n')
        self.assertTrue(health.dispatcher_registered(self.d, self.hooks))
        output, _ = self.execute("liveness")
        self.assertIn("dispatcher allowlist   STALE", output)

    def test_source_owner_counts_and_predicate_inputs_are_preserved(self):
        raw, before = self.execute("liveness", adapted=False)
        output, after = self.execute("liveness")
        self.assertIn("Codex hooks not installed", raw)
        self.assertIn(str(self.home / ".codex/hooks.json"), raw)
        self.assertIn("pre_report durable-invariant blockers REGRESSED (1/3", raw)
        self.assertIn("source-owner examples UNQUALIFIED (1/3", output)
        self.assertEqual(before, after)
        self.assertNotIn("Codex hooks not installed", output)
        state = json.loads((self.dreaming / ".liveness_status.json").read_text())
        self.assertEqual(state["observer_context"]["profile"], str(self.profile))
        self.assertIn("UNQUALIFIED (1/3", state["source_owner_notes"][0])
        self.assertFalse(any("source-owner examples" in x for x in state["safety"]))

    def test_all_source_examples_passing_still_does_not_invent_local_baseline(self):
        output, _ = self.execute("liveness", blocked=3)
        self.assertIn("UNQUALIFIED (3/3", output)
        self.assertIn("no locally qualified prior baseline", output)

    def test_local_safety_pass_does_not_claim_source_examples_all_blocked(self):
        output, _ = self.execute("liveness", blocked=1, attested_host=True)
        self.assertIn("UNQUALIFIED (1/3", output)
        self.assertIn("Local wrapper checks and retired numeric control passed", output)
        self.assertNotIn("blocks all 3 durable contradictions", output)

    def test_liveness_publishes_active_recurrence_failures(self):
        notice = "Strong-node recurrence watch DEGRADED: reviewed package absent"
        with patch.object(health, "recurrence_findings", return_value=[notice]):
            output, _ = self.execute("liveness", attested_host=True)
        state = json.loads((self.dreaming / ".liveness_status.json").read_text())
        self.assertIn(notice, state["artifacts"])
        self.assertNotIn("VERDICT: all wired hooks compile; all streams fresh", output)

    def test_brain_status_counts_and_inputs_survive_without_false_regression_claim(self):
        raw, before = self.execute("status", adapted=False)
        output, after = self.execute("status")
        self.assertIn("REGRESSED: durable-discuss blocked 1/3", raw)
        self.assertIn("source-owner report examples UNQUALIFIED: durable-discuss blocked 1/3", output)
        self.assertEqual(before, after)

    def test_current_retired_oracle_failure_still_fails_without_claiming_a_prior_baseline(self):
        output, _ = self.execute("status", retired_blocked=True)
        self.assertIn("retired-rate blocks 3/3", output)
        self.assertIn("CHECK FAILED", output)
        output, _ = self.execute("liveness", retired_blocked=True)
        self.assertIn("stale numeric oracle CHECK FAILED", output)

    def test_namespace_paths_bind_selected_home_even_from_another_callers_home(self):
        ns = health.original_namespace(self.d, "status")
        self.assertEqual(ns["DR"], str(self.dreaming))
        self.assertEqual(ns["MEM"], str(self.memory))

    def test_changed_original_is_refused_before_execution(self):
        self.d["source"] = str(self.root / "changed source")
        target = Path(self.d["source"]) / "dreaming/hook_liveness.py"
        target.parent.mkdir(parents=True)
        target.write_bytes((self.source / "dreaming/hook_liveness.py").read_bytes() + b"\nraise RuntimeError('changed')\n")
        with self.assertRaisesRegex(ValueError, "exact reviewed original"):
            health.original_namespace(self.d, "liveness")

    def test_unbound_snapshot_keeps_findings_explicit_without_claiming_foreign_failure(self):
        path = self.dreaming / ".liveness_status.json"
        path.write_text(json.dumps({"dead": [], "stale": ["old stream"], "safety": ["Codex hooks not installed: virtual HOME/.codex"]}))
        findings = health.wake_findings(self.d, path)
        self.assertTrue(any("0 dead/1 stale/1 safety findings retained" in x for x in findings))
        self.assertFalse(any(x.startswith("Codex hooks not installed") for x in findings))

    def test_bound_snapshot_surfaces_safety_failure_and_retains_source_notes(self):
        path = self.dreaming / ".liveness_status.json"
        snapshot = {"ts": time.time(), "dead": [], "stale": [], "artifacts": [],
                    "safety": ["local guard failed"], "source_owner_notes": ["source-owner examples UNQUALIFIED (1/3)"],
                    "observer_context": health.observer_context(self.d)}
        path.write_text(json.dumps(snapshot))
        findings = health.wake_findings(self.d, path)
        self.assertTrue(any("local guard failed" in x for x in findings))
        self.assertFalse(any("source-owner examples" in x for x in findings))
        snapshot["ts"] -= health.MAX_LIVENESS_AGE + 1
        path.write_text(json.dumps(snapshot))
        self.assertTrue(any("stale or invalid" in x for x in health.wake_findings(self.d, path)))

    def test_malformed_snapshot_is_unverified(self):
        path = self.dreaming / ".liveness_status.json"
        path.write_text('{"dead":[],"stale":[],"safety":"not an array"}')
        self.assertTrue(any("snapshot UNVERIFIED" in x for x in health.wake_findings(self.d, path)))

    def test_selected_main_mismatch_cannot_use_a_different_host(self):
        import brain_binding
        with patch.object(c, "active", return_value=self.d), patch.object(brain_binding, "resolve", return_value={"profile": str(self.profile), "id": "different-main"}):
            with self.assertRaisesRegex(c.ConnectorError, "explicitly designated main"):
                health.selected_main(self.profile)

    def test_connector_observer_scope_preserves_project_path_and_blocks_missing_main(self):
        import brain_binding
        with patch.object(brain_binding, "resolve", return_value=self.d):
            self.assertTrue(c.main_host_observer_selected(self.d))
        other = {**self.d, "id": "a-separate-explicit-main"}
        with patch.object(brain_binding, "resolve", return_value=other):
            self.assertFalse(c.main_host_observer_selected(self.d))
        with patch.object(brain_binding, "resolve", side_effect=c.ConnectorError("main unavailable")):
            with self.assertRaisesRegex(c.ConnectorError, "main unavailable"):
                c.main_host_observer_selected(self.d)

    def test_original_wrapper_selftest_failure_is_retained(self):
        modules = [types.SimpleNamespace(_selftest=lambda: value) for value in (0, 0)]
        modules.append(types.SimpleNamespace(_selftest=lambda: 1))
        with patch.object(health.importlib, "import_module", side_effect=modules):
            self.assertEqual(health.wrapper_controls(), ["codex_stop_guard.py original selftest failed"])

    def test_live_wake_consumer_reports_scoped_findings_and_observer_errors(self):
        source = self.source / "hooks/wake_brain.py"
        ns, errors = wake.original_namespace(source, liveness_observer=lambda _: ["source-owner examples UNQUALIFIED 1/3; original counts retained"])
        ns.update(HOME=str(self.home), CLAUDE=str(self.home / ".claude"), DR=str(self.dreaming), MEM=str(self.memory), VIEWS=str(self.home / "views"))
        ns["part1"]("")
        self.assertIn("source-owner examples UNQUALIFIED 1/3", "\n".join(ns["OUT"]))
        self.assertFalse(errors)
        self.assertTrue(ns["_brain_liveness_notices"])

    def publish_main(self, producer):
        ns = {"__file__": str(self.dreaming / "hook_liveness.py"), "main": producer,
              "_brain_observer_context": health.observer_context(self.d)}
        with patch.object(health, "selected_main", return_value=self.d), patch.object(c, "verify_source"), patch.object(c, "verify_runtime_code"), \
             patch.object(health, "original_namespace", return_value=ns), patch.object(sys, "argv", ["observer", "--profile", str(self.profile), "--mode", "liveness"]):
            return health.main()

    def test_silent_writer_cannot_reuse_an_old_bound_status_but_real_publication_restores(self):
        path = self.dreaming / ".liveness_status.json"
        context = health.observer_context(self.d)
        path.write_text(json.dumps({"ts": time.time() - 60, "observer_context": context}))
        with self.assertRaisesRegex(ValueError, "fresh bound publication"):
            self.publish_main(lambda: None)
        def writer(failures):
            path.write_text(json.dumps({"ts": time.time(), "observer_context": context,
                                        "dead": [], "artifacts": [], "safety": failures,
                                        "stale": [], "source_owner_notes": []}))
        self.assertIsNone(self.publish_main(lambda: writer([])))
        self.assertEqual(self.publish_main(lambda: writer(["local guard failed"])), 2)

    def test_touching_an_old_status_is_not_a_fresh_publication(self):
        path = self.dreaming / ".liveness_status.json"
        path.write_text(json.dumps({"ts": time.time() - 60, "observer_context": health.observer_context(self.d)}))
        with self.assertRaisesRegex(ValueError, "fresh bound publication"):
            self.publish_main(lambda: os.utime(path, None))


if __name__ == "__main__":
    unittest.main()
