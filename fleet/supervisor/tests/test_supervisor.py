import datetime as dt
import importlib.util
import json
import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock
import sys


HERE = Path(__file__).resolve().parents[1]
DEPLOYED_HERE = Path.home() / ".claude" / "fleet" / "codex_continuity"
SPEC = importlib.util.spec_from_file_location("fleet_continuity_supervisor", HERE / "supervisor.py")
mod = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
# register before exec: @dataclass resolves its own
# module via sys.modules and dies on None
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class SupervisorTests(unittest.TestCase):
    def worker(self, root: Path, **overrides):
        base = {
            "thread_id": str(uuid.uuid4()),
            "runtime": "codex",
            "mesh_agent_id": "test-worker",
            "task": "continue the existing bounded test",
            "cwd": str(root),
            "enabled": True,
            "service_until": "2030-01-01T00:00:00Z",
            "active_seconds_required": 120,
            "enrollment_id": str(uuid.uuid4()),
        }
        base.update(overrides)
        return base

    def manifest(self, root: Path, workers):
        return {"schema": 1, "controller": {"max_concurrent": 2,
                "sample_interval_seconds": 60, "accounting_poll_seconds": 5},
                "workers": workers}

    def checkpoint(self, generation, ids, body="Bounded checkpoint."):
        return (body + "\n\nFLEET_CHECKPOINT v1\n"
                f"generation: {generation}\n"
                f"warning_ids: {','.join(ids)}\n"
                "hardest_unverified: live integration remains the hardest open point\n"
                "verified_now: inspected the current source and direct test output\n"
                "not_verified: production behavior was not exercised\n"
                "next_front: run the next bounded integration check\n"
                "END_FLEET_CHECKPOINT\n")

    def rollout(self, path, final, turn_id=None, *, divergent=None, complete=True):
        turn_id = turn_id or str(uuid.uuid4())
        rows = [
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn_id}},
            {"type": "event_msg", "payload": {"type": "agent_message", "phase": "final_answer",
                                                "message": divergent if divergent is not None else final}},
        ]
        if complete:
            rows.append({"type": "event_msg", "payload": {"type": "task_complete",
                         "turn_id": turn_id, "last_agent_message": final}})
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return turn_id

    def test_manifest_requires_explicit_valid_enrollment(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            good = mod.validate_manifest(self.manifest(root, [self.worker(root)]))
            self.assertEqual(len(good["workers"]), 1)
            bad = self.manifest(root, [self.worker(root, thread_id="recent")])
            with self.assertRaises(ValueError):
                mod.validate_manifest(bad)
            truthy = self.manifest(root, [self.worker(root, enabled="false")])
            with self.assertRaisesRegex(ValueError, "JSON boolean"):
                mod.validate_manifest(truthy)

    def test_command_fences_effective_config(self):
        with mock.patch.object(mod, "native_codex_exe", return_value="codex.exe"):
            command = mod.codex_command(str(uuid.uuid4()))
        joined = " ".join(command)
        self.assertIn("exec", command)
        self.assertIn("resume", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("read-only", command)
        self.assertIn('approval_policy="never"', command)
        self.assertIn('shell_environment_policy.inherit="core"', command)
        self.assertNotIn("workspace-write", command)
        self.assertIn('windows.sandbox="unelevated"', command)
        self.assertNotIn("dangerously", joined)
        self.assertNotIn("bypass", joined)
        self.assertEqual(command[0], "codex.exe")

    def test_safe_environment_drops_secret_shaped_variables(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {
                "OPENAI_API_KEY": "synthetic-secret", "PATH": td, "USERPROFILE": td}, clear=True):
            worker = self.worker(Path(td), mesh_agent_id="safe-agent")
            env = mod.safe_child_env(worker)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertEqual(env["AGENT_MESH_ID"], "safe-agent")
        self.assertIn("PATH", env)

    @unittest.skipUnless(
        os.name == "nt" and HERE.resolve() == DEPLOYED_HERE.resolve(),
        "deployed Windows ACL contract",
    )
    def test_control_plane_acl_excludes_codex_sandbox_group(self):
        result = mod.subprocess.run(["icacls.exe", str(HERE)], capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", check=True)
        self.assertNotIn("CodexSandboxUsers", result.stdout)

    def test_prompt_uses_absolute_deadline_not_relative_restart(self):
        with tempfile.TemporaryDirectory() as td:
            worker = mod.validate_manifest(self.manifest(Path(td), [self.worker(Path(td))]))["workers"][0]
            prompt = mod.continuation_prompt(worker, 77)
        self.assertIn("2030-01-01T00:00:00Z", prompt)
        self.assertIn("77 seconds", prompt)
        self.assertIn("Containment mode: READ-ONLY", prompt)
        self.assertIn("Owner-correction warning digest", prompt)
        self.assertIn("[restate_the_ask]", prompt)
        self.assertIn("[done_means_verified]", prompt)
        self.assertIn("[the_last_hard_mile]", prompt)
        self.assertIn("[keep_working]", prompt)
        self.assertIn("FLEET_CHECKPOINT v1", prompt)
        self.assertIn("warning_ids: restate_the_ask,done_means_verified,the_last_hard_mile,keep_working", prompt)
        self.assertIn("hardest_unverified:", prompt)
        self.assertNotIn("for the next", prompt.lower())

    def test_generation_rollout_accepts_exact_warning_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rollout.jsonl"
            generation = str(uuid.uuid4())
            ids = ("restate_the_ask", "done_means_verified")
            final = self.checkpoint(generation, ids)
            turn_id = self.rollout(path, final)
            result = mod.validate_rollout_generation(path, 0, generation, ids, 0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["turn_id"], turn_id)
        self.assertRegex(result["final_sha256"], r"^[0-9a-f]{64}$")

    def test_generation_rollout_rejects_chatter_old_or_divergent_final(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            generation = str(uuid.uuid4())
            ids = ("keep_working",)
            final = self.checkpoint(generation, ids)
            missing = root / "missing.jsonl"
            self.rollout(missing, final, complete=False)
            self.assertEqual(mod.validate_rollout_generation(missing, 0, generation, ids)["code"],
                             "task-complete-missing")
            old = root / "old.jsonl"
            self.rollout(old, final)
            offset = old.stat().st_size
            with old.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"type": "event_msg", "payload": {"type": "token_count"}}) + "\n")
            self.assertEqual(mod.validate_rollout_generation(old, offset, generation, ids)["code"],
                             "task-complete-missing")
            divergent = root / "divergent.jsonl"
            self.rollout(divergent, final, divergent="different final")
            self.assertEqual(mod.validate_rollout_generation(divergent, 0, generation, ids)["code"],
                             "final-message-divergence")

    def test_checkpoint_rejects_wrong_receipt_disclosure_and_owner_oracles(self):
        generation = str(uuid.uuid4())
        ids = ("restate_the_ask", "keep_working")
        wrong_generation = self.checkpoint(str(uuid.uuid4()), ids)
        self.assertEqual(mod._checkpoint_result(wrong_generation, generation, ids)["code"],
                         "checkpoint-generation-mismatch")
        wrong_ids = self.checkpoint(generation, ("keep_working",))
        self.assertEqual(mod._checkpoint_result(wrong_ids, generation, ids)["code"],
                         "checkpoint-warning-receipt-mismatch")
        none_hardest = self.checkpoint(generation, ids).replace(
            "hardest_unverified: live integration remains the hardest open point",
            "hardest_unverified: NONE")
        self.assertEqual(mod._checkpoint_result(none_hardest, generation, ids)["code"],
                         "checkpoint-disclosure-incomplete")
        sign = self.checkpoint(generation, ids, "His current strategy loses money every year.")
        self.assertEqual(mod._checkpoint_result(sign, generation, ids)["code"],
                         "owner_report:profitable_lived_sign")
        stale = self.checkpoint(generation, ids, "The old result was $111k.")
        self.assertEqual(mod._checkpoint_result(stale, generation, ids)["code"],
                         "retired-financial-oracle")

    def test_expired_worker_without_accepted_generation_cannot_complete(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            worker = self.worker(root, active_seconds_required=0,
                                 service_until="2020-01-01T00:00:00Z")
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(self.manifest(root, [worker])), encoding="utf-8")
            supervisor = mod.Supervisor(manifest_path, root, dry_run=True)
            normalized = mod.load_manifest(manifest_path)["workers"][0]
            state = supervisor.worker_state(normalized)
            state["status"] = "queued"
            with mock.patch.object(mod, "process_groups_for_thread", return_value=[]), \
                    mock.patch.object(mod.Supervisor, "compute_launch_policy", return_value=(True, "test", 1)):
                sample = supervisor.tick(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
                self.assertFalse(supervisor.all_satisfied(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)))
            self.assertEqual(sample["workers"][0]["status"], "blocked-output")

    def test_warning_digest_is_bounded_pure_and_task_matched(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.tsv"
            path.write_text(
                "keyword_guard\tfire\thigh\tcritical[- ]keyword\tmatched task warning\ts\tm\t0.91\n"
                "broken_regex\tfire\thigh\t([\tshould fail open\ts\tm\t1.0\n",
                encoding="utf-8",
            )
            before = path.read_bytes()
            digest = mod.owner_warning_digest("audit this critical keyword", path)
            after = path.read_bytes()
        self.assertEqual(before, after)
        self.assertIn("[restate_the_ask]", digest)
        self.assertIn("[done_means_verified]", digest)
        self.assertIn("[the_last_hard_mile]", digest)
        self.assertIn("[keep_working]", digest)
        self.assertIn("[keyword_guard] matched task warning", digest)
        self.assertLessEqual(len(digest), 3000)

    def test_exact_owner_clause_warning_is_metadata_only_and_generation_receipted(self):
        task = "Use 7 subagents, not 3."
        line, warning_id = mod.exact_clause_warning(task)
        self.assertRegex(warning_id, r"^owner_clause:[0-9a-f]{12}$")
        self.assertIn("categories=", line)
        self.assertNotIn("7 subagents", line)
        self.assertNotIn("not 3", line)
        with tempfile.TemporaryDirectory() as td:
            worker = mod.validate_manifest(self.manifest(Path(td), [
                self.worker(Path(td), task=task)
            ]))["workers"][0]
            prompt, prompt_ids = mod.continuation_prompt(worker, 10, return_warning_ids=True)
        self.assertIn(line, prompt)
        self.assertIn("," + warning_id, prompt)
        self.assertIn("warning_ids:", prompt)
        self.assertIn(warning_id, prompt_ids)
        self.assertEqual(mod.warning_ids(prompt), prompt_ids)
        self.assertIn("warning_ids: " + ",".join(prompt_ids), prompt)

    def test_launch_state_receipts_the_same_dynamic_warning_as_the_prompt(self):
        class Sink:
            def __init__(self):
                self.text = ""

            def write(self, value):
                self.text += value

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            worker = mod.validate_manifest(self.manifest(root, [
                self.worker(root, task="Use 7 subagents, not 3.")
            ]))["workers"][0]
            supervisor = object.__new__(mod.Supervisor)
            supervisor.dry_run = False
            supervisor.locks = root
            supervisor.children = {}
            supervisor.rollout_snapshot = mock.Mock(return_value={"path": "rollout", "size": 0})
            state = {"remaining_active_seconds": 10}
            sink = Sink()
            proc = mock.Mock(pid=424242, stdin=sink)
            psproc = mock.Mock()
            psproc.create_time.return_value = 123.0
            with mock.patch.object(mod, "process_groups_for_thread", return_value=[]), \
                    mock.patch.object(mod, "reclaim_stale_launch_lock", return_value=False), \
                    mock.patch.object(mod, "try_create_launch_lock", return_value=True), \
                    mock.patch.object(mod, "release_launch_lock"), \
                    mock.patch.object(mod, "codex_command", return_value=["codex"]), \
                    mock.patch.object(mod, "safe_child_env", return_value={}), \
                    mock.patch.object(mod.subprocess, "Popen", return_value=proc), \
                    mock.patch.object(mod.psutil, "Process", return_value=psproc), \
                    mock.patch.object(mod.time, "sleep"):
                result = supervisor._launch(worker, state, dt.datetime.now(dt.timezone.utc))
        self.assertEqual(result["kind"], "launched")
        launched_ids = tuple(state["launch_warning_ids"])
        self.assertTrue(any(item.startswith("owner_clause:") for item in launched_ids))
        self.assertEqual(mod.warning_ids(sink.text), launched_ids)
        self.assertIn("warning_ids: " + ",".join(launched_ids), sink.text)

    def test_exact_owner_clause_overflow_only_warning_survives(self):
        task = "x" * 400 + "; Only deploy website."
        line, warning_id = mod.exact_clause_warning(task)
        self.assertRegex(warning_id, r"^owner_clause:[0-9a-f]{12}$")
        self.assertIn("parsed_count=0 overflow=true", line)
        self.assertIn("Bounded exact-clause parser exceeded", line)
        self.assertIn("manually restate every exact owner constraint", line)
        self.assertIn("do not emit a partial checklist", line)
        self.assertNotIn("deploy website", line)

    def test_exact_owner_clause_large_manifest_stays_bounded_and_visible(self):
        task = "\n".join("Do not delete file%d." % index for index in range(1000))
        line, warning_id = mod.exact_clause_warning(task)
        self.assertRegex(warning_id, r"^owner_clause:[0-9a-f]{12}$")
        self.assertIn("parsed_count=1000 overflow=true", line)
        self.assertIn("manually restate every exact owner constraint", line)
        self.assertLess(len(line), 500)

    def test_missing_exact_clause_extractor_fails_open(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(mod, "EXACT_CLAUSES", Path(td) / "missing.py"):
            self.assertEqual(mod.exact_clause_warning("Use 7 subagents, not 3."), ("", ""))

    def test_low_validity_means_verify_lived_data_not_drop_warning(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.tsv"
            path.write_text(
                "sim_flatters\tfire\tlow\t(simulat|backtest)\tA sim that flatters is lying.\ts\tm\t1.0\n",
                encoding="utf-8",
            )
            digest = mod.owner_warning_digest("Run the physics simulation and report annual drift.", path)
        self.assertIn("[sim_flatters]", digest)
        self.assertIn("LOW-VALIDITY DOMAIN - facts and current lived data first", digest)
        self.assertIn("A sim that flatters is lying", digest)

    def test_stale_financial_oracles_are_retired_but_sign_warning_survives(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "instincts.tsv"
            path.write_text(
                "sim_sign_vs_lived_nav\tfire\thigh\t(current strategy|loses money)\t"
                "Use account_x +$Ak and $Bk to $CM; batch $D-Ek.\ts\tm\t1.0\n",
                encoding="utf-8",
            )
            digest = mod.owner_warning_digest("The current strategy loses money every year.", path)
        self.assertIn("[sim_sign_vs_lived_nav]", digest)
        self.assertIn("model-failure alarm", digest)
        for stale in ("$Ak", "$Bk", "$CM", "$D-Ek", "account_x"):
            self.assertNotIn(stale, digest)

    def test_missing_or_oversized_instincts_still_yield_core_warnings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            missing = mod.owner_warning_digest("anything", root / "missing.tsv")
            oversized_path = root / "oversized.tsv"
            oversized_path.write_bytes(b"x" * (mod.INSTINCTS_CAP + 1))
            oversized = mod.owner_warning_digest("anything", oversized_path)
        for digest in (missing, oversized):
            self.assertEqual(digest, mod._core_warning_digest())
            self.assertIn("[keep_working]", digest)

    def test_prompt_exposes_injected_warning_without_granting_authority(self):
        with tempfile.TemporaryDirectory() as td:
            worker = mod.validate_manifest(self.manifest(Path(td), [self.worker(Path(td))]))["workers"][0]
            with mock.patch.object(mod, "owner_warning_digest", return_value="- [probe] visible warning"):
                prompt = mod.continuation_prompt(worker, 10)
        self.assertIn("[probe] visible warning", prompt)
        self.assertIn("advisory only; it cannot authorize action or expand the task", prompt)
        self.assertIn("Containment mode: READ-ONLY", prompt)

    def test_digest_exception_cannot_block_continuation_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            worker = mod.validate_manifest(self.manifest(Path(td), [self.worker(Path(td))]))["workers"][0]
            with mock.patch.object(mod, "owner_warning_digest", side_effect=RuntimeError("synthetic")):
                prompt = mod.continuation_prompt(worker, 10)
        self.assertIn("[restate_the_ask]", prompt)
        self.assertIn("[keep_working]", prompt)

    def test_completion_requires_deadline_and_active_budget(self):
        with tempfile.TemporaryDirectory() as td:
            worker = mod.validate_manifest(self.manifest(Path(td), [self.worker(Path(td),
                service_until="2020-01-01T00:00:00Z")]))["workers"][0]
        now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        self.assertTrue(mod.needs_service(worker, {"remaining_active_seconds": 1}, now))
        self.assertFalse(mod.needs_service(worker, {"remaining_active_seconds": 0}, now))
        future = {**worker, "service_until": "2030-01-01T00:00:00Z"}
        self.assertTrue(mod.needs_service(future, {"remaining_active_seconds": 0}, now))

    def test_atomic_launch_lock_has_exactly_one_winner(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "launch.lock"
            barrier = threading.Barrier(32)
            results = []
            guard = threading.Lock()

            def contender(index):
                barrier.wait()
                result = mod.try_create_launch_lock(path, {"generation": str(index)})
                with guard:
                    results.append(result)

            threads = [threading.Thread(target=contender, args=(i,)) for i in range(32)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(results.count(True), 1)
            self.assertEqual(results.count(False), 31)

    def test_stale_lock_recovery_never_displaces_live_owner(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "launch.lock"
            live = {"generation": "live", "pid": os.getpid(), "boot_time": mod.psutil.boot_time()}
            self.assertTrue(mod.try_create_launch_lock(path, live))
            self.assertFalse(mod.reclaim_stale_launch_lock(path))
            path.write_text(json.dumps({"generation": "dead", "pid": 2147483000,
                                        "boot_time": mod.psutil.boot_time()}), encoding="utf-8")
            self.assertTrue(mod.reclaim_stale_launch_lock(path))
            self.assertFalse(path.exists())

    def test_scheduler_respects_concurrency_and_fairness(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workers = mod.validate_manifest(self.manifest(root, [self.worker(root) for _ in range(4)]))["workers"]
        states = {worker["thread_id"]: {"remaining_active_seconds": 100,
                  "next_retry_at": 0, "consecutive_failures": 0,
                  "last_launch_at": str(i)} for i, worker in enumerate(workers)}
        chosen = mod.plan_launches(workers, states, {workers[0]["thread_id"]},
                                   dt.datetime.now(dt.timezone.utc), 2)
        self.assertEqual(len(chosen), 1)
        self.assertNotIn(workers[0]["thread_id"], chosen)

    def test_ambiguous_external_rollout_progress_blocks_launch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            worker = self.worker(root)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(self.manifest(root, [worker])), encoding="utf-8")
            supervisor = mod.Supervisor(manifest_path, root, dry_run=True)
            normalized = mod.load_manifest(manifest_path)["workers"][0]
            state = supervisor.worker_state(normalized)
            state.update({"status": "queued", "last_rollout_size": 1,
                          "last_rollout_mtime": 1, "next_retry_at": 0})
            with mock.patch.object(mod, "process_groups_for_thread", return_value=[]), \
                    mock.patch.object(supervisor, "rollout_snapshot",
                        return_value={"path": "x", "size": 2, "mtime": 2}), \
                    mock.patch.object(mod.Supervisor, "compute_launch_policy",
                        return_value=(True, "test", 2)):
                sample = supervisor.tick(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
            self.assertEqual(sample["workers"][0]["status"], "ambiguous")
            self.assertIn("external-progress-ambiguous",
                          [event["kind"] for event in sample["events"]])
            self.assertNotIn("dry-run-launch", [event["kind"] for event in sample["events"]])

    def test_new_enrollment_baselines_existing_rollout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            worker = self.worker(root)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(self.manifest(root, [worker])), encoding="utf-8")
            supervisor = mod.Supervisor(manifest_path, root, dry_run=True)
            with mock.patch.object(supervisor, "rollout_snapshot",
                    return_value={"path": "x", "size": 17, "mtime": 19.0}):
                state = supervisor.worker_state(mod.load_manifest(manifest_path)["workers"][0])
            self.assertEqual(state["last_rollout_size"], 17)
            self.assertEqual(state["last_rollout_mtime"], 19.0)
            self.assertEqual(state["status"], "queued")

    def test_hidden_launcher_propagates_exit_and_installer_preflights(self):
        vbs = (HERE / "launch_supervisor.vbs").read_text(encoding="utf-8-sig")
        installer = (HERE / "install_task.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("WScript.Quit rc", vbs)
        self.assertIn("--validate", installer)
        self.assertIn("MultipleInstances", installer)

    def test_dry_run_writes_no_state_or_logs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(self.manifest(root, [self.worker(root, enabled=False,
                active_seconds_required=0, service_until="2020-01-01T00:00:00Z")])), encoding="utf-8")
            supervisor = mod.Supervisor(manifest_path, root, dry_run=True)
            with mock.patch.object(mod, "process_for_thread", return_value=None):
                supervisor.tick(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["manifest.json"])

    def test_eligible_dry_run_still_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(self.manifest(root, [self.worker(root)])), encoding="utf-8")
            supervisor = mod.Supervisor(manifest_path, root, dry_run=True)
            with mock.patch.object(mod, "process_for_thread", return_value=None), \
                    mock.patch.object(mod, "latest_rollout", return_value={"path": None, "size": 0, "mtime": 0.0}), \
                    mock.patch.object(mod.Supervisor, "compute_launch_policy", return_value=(True, "ok/test", 2)):
                sample = supervisor.tick(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
            self.assertIn("dry-run-launch", [event["kind"] for event in sample["events"]])
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["manifest.json"])

    def test_compute_policy_is_fail_closed_and_owner_capped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(self.manifest(root, [])), encoding="utf-8")
            supervisor = mod.Supervisor(manifest_path, root, dry_run=True)
            with mock.patch.object(mod, "read_json", return_value={}):
                self.assertEqual(supervisor.compute_launch_policy(2)[2], 0)
            with mock.patch.object(mod, "read_json", return_value={"status": "ok",
                    "mode": "owner_present_polite", "owner_active": True}):
                self.assertEqual(supervisor.compute_launch_policy(2)[2], 1)
            with mock.patch.object(mod, "read_json", return_value={"status": "ok",
                    "mode": "throttle", "owner_active": False}):
                self.assertEqual(supervisor.compute_launch_policy(2)[2], 0)

    def test_reboot_gap_can_credit_at_most_one_poll_slice(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            worker = self.worker(root, active_seconds_required=100)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(self.manifest(root, [worker])), encoding="utf-8")
            supervisor = mod.Supervisor(manifest_path, root, dry_run=True)
            state = supervisor.worker_state(mod.load_manifest(manifest_path)["workers"][0])
            state.update({"remaining_active_seconds": 100, "last_accounted_at": 1,
                          "last_rollout_size": 1, "last_rollout_mtime": 1})
            proc = mock.Mock(pid=1234)
            proc.create_time.return_value = 2.0
            with mock.patch.object(mod, "process_groups_for_thread", return_value=[proc]), \
                    mock.patch.object(supervisor, "rollout_snapshot",
                        return_value={"path": "x", "size": 2, "mtime": 2}), \
                    mock.patch.object(mod.Supervisor, "compute_launch_policy", return_value=(False, "test", 0)):
                supervisor.tick(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
            self.assertGreaterEqual(state["remaining_active_seconds"], 95)

    def test_duplicate_process_groups_are_quarantined_not_launched(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            worker = self.worker(root)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(self.manifest(root, [worker])), encoding="utf-8")
            supervisor = mod.Supervisor(manifest_path, root, dry_run=True)
            a, b = mock.Mock(pid=11), mock.Mock(pid=22)
            with mock.patch.object(mod, "process_groups_for_thread", return_value=[a, b]), \
                    mock.patch.object(supervisor, "rollout_snapshot",
                        return_value={"path": None, "size": 0, "mtime": 0}), \
                    mock.patch.object(mod.Supervisor, "compute_launch_policy", return_value=(True, "test", 2)):
                sample = supervisor.tick(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
            self.assertEqual(sample["workers"][0]["status"], "duplicate")
            self.assertNotIn("dry-run-launch", [event["kind"] for event in sample["events"]])

    def test_jsonl_tail_repair_and_coverage_gap_are_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "events.jsonl"
            path.write_bytes(b'{"ok":1}\n{"partial"')
            self.assertGreater(mod.repair_jsonl_tail(path), 0)
            self.assertEqual(path.read_bytes(), b'{"ok":1}\n')


if __name__ == "__main__":
    unittest.main()
