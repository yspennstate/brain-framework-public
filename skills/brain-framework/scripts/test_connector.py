"""Controls for deployment boundaries and runtime contracts. Run with Python's unittest."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import connector as c
import hook_bridge as bridge
import brainctl
import native_adapter
import profile_scope
import brain_binding as binding


def measured_fixture(status):
    return {"items": [{"id": "S01", "capability": "core", "checks": [
        {"requirement": "Current source", "status": status},
    ]}], "selected_capabilities": {"core": ["fixture"]}}


class BoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="brain-connector-controls-")
        cls.root = Path(cls.workspace.name)
        cls.profile = cls.root / "source profile λ"
        cls.source = c.acquire_source(cls.profile)

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.root)
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_offline_source_matches_full_github_lock(self):
        result = c.verify_source(self.source, strict=True)
        self.assertEqual(result["verified_files"], len(c.source_lock()["files"]))
        self.assertEqual(result["revision"], c.source_lock()["revision"])

    def test_modified_upstream_file_is_rejected(self):
        target = self.source / "docs/METHOD.md"
        original = target.read_bytes()
        try:
            target.write_bytes(original + b"\nfalse positive control\n")
            with self.assertRaises(c.ConnectorError):
                c.acquire_source(self.profile)
        finally:
            target.write_bytes(original)
        c.verify_source(self.source, strict=True)

    def test_missing_source_file_is_not_a_smaller_valid_installation(self):
        target = self.source / "hooks/brain_startup.template.md"
        original = target.read_bytes()
        try:
            target.unlink()
            with self.assertRaises(c.ConnectorError):
                c.verify_source(self.source)
        finally:
            target.write_bytes(original)

    def test_untracked_python_cannot_enter_the_source_cache(self):
        target = self.source / "json.py"
        try:
            target.write_text("raise RuntimeError('wrong source')\n")
            with self.assertRaises(c.ConnectorError):
                c.acquire_source(self.profile)
        finally:
            target.unlink()

    def archive(self, members):
        path = self.directory / "fixture.tar.gz"
        with tarfile.open(path, "w:gz") as stream:
            for name, kind, data in members:
                item = tarfile.TarInfo(name)
                item.type = kind
                item.size = len(data) if kind == tarfile.REGTYPE else 0
                item.linkname = "../../outside"
                stream.addfile(item, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
        return path

    def test_archive_traversal_is_rejected(self):
        path = self.archive([("../../outside", tarfile.REGTYPE, b"bad")])
        with self.assertRaises(c.ConnectorError):
            c.unpack_source(path, self.directory / "unpacked", {"files": [{"path": "expected.py", "sha": ""}]})
        self.assertFalse((self.directory.parent / "outside").exists())

    def test_archive_links_are_rejected(self):
        path = self.archive([("expected.py", tarfile.SYMTYPE, b"")])
        with self.assertRaises(c.ConnectorError):
            c.unpack_source(path, self.directory / "unpacked", {"files": [{"path": "expected.py", "sha": ""}]})

    def test_duplicate_archive_member_is_rejected(self):
        path = self.archive([("expected.py", tarfile.REGTYPE, b"x"), ("expected.py", tarfile.REGTYPE, b"x")])
        with self.assertRaises(c.ConnectorError):
            c.unpack_source(path, self.directory / "unpacked", {"files": [{"path": "expected.py", "sha": ""}]})

    def test_dry_install_does_not_create_profile(self):
        target = self.directory / "absent profile"
        result = subprocess.run([sys.executable, str(c.SKILL / "scripts/brainctl.py"), "--profile", str(target), "install"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(target.exists())

    def test_resolving_venv_symlink_cannot_silently_install_into_base_python(self):
        with self.assertRaises(c.ConnectorError):
            c.verify_venv(sys.executable)
        env = self.directory / "venv with spaces"
        subprocess.run([sys.executable, "-m", "venv", str(env)], check=True, capture_output=True)
        c.verify_venv(c.venv_python(env))

    def test_hook_argv_keeps_paths_with_spaces_in_single_arguments(self):
        deployment = {"source": str(self.source), "home": str(self.directory / "home with spaces λ"),
                      "python": str(self.directory / "venv with spaces/bin/python"), "profile": str(self.directory / ".codex"),
                      "slug": "a_slug", "runtime": "codex", "enabled": ["core", "common-sense"]}
        handlers, groups, disabled = c.commands_from_template(deployment)
        self.assertTrue(handlers)
        self.assertIn("SubagentStart", groups)
        for row in handlers.values():
            if row.get("builtin"):
                continue
            self.assertEqual(row["argv"][0], deployment["python"])
            self.assertNotIn("{{", " ".join(row["argv"]))
            for arg in row["argv"]:
                if arg.endswith(".py") and " -c " not in arg:
                    self.assertTrue(arg.startswith(str(self.directory)) or arg.startswith(str(self.source)), arg)
        self.assertTrue(any("Private signed" in row["reason"] for row in disabled))
        self.assertTrue(any("publication policy" in row["reason"] for row in disabled))

    def test_runtime_environment_overrides_inherited_brain_seams(self):
        home = self.directory / "bound home"
        deployment = {"home": str(home), "memory": str(home / "memory"), "source": str(self.source), "python": str(home / "venv/bin/python")}
        with patch.dict(os.environ, {"BRAIN_MEM_DIR": "/wrong", "TASK_CHECKLIST_HOME": "/wrong", "COMPACT_NODES_ROOT": "/wrong", "AGENT_MESH_HOME": "/wrong", "PYTHONPATH": "/untrusted", "MATHBRAIN_DB": "/wrong"}):
            env = c.runtime_env(deployment)
        self.assertEqual(env["BRAIN_MEM_DIR"], str(home / "memory"))
        for key in ("TASK_CHECKLIST_HOME", "COMPACT_NODES_ROOT", "AGENT_MESH_HOME"):
            self.assertTrue(env[key].startswith(str(home)))
        self.assertNotIn("MATHBRAIN_DB", env)
        self.assertNotIn("/untrusted", env["PYTHONPATH"])
        self.assertEqual(len(env["PYTHONPATH"].split(os.pathsep)), 2)

    def test_selected_python_is_preflighted_instead_of_the_callers_version(self):
        rejected = subprocess.CompletedProcess([], 0, "[3,11,9]\n", "")
        with patch.object(c.subprocess, "run", return_value=rejected) as child:
            with self.assertRaisesRegex(c.ConnectorError, "3.13"):
                c.preflight_python("/selected/python")
        self.assertEqual(child.call_args.args[0][0], "/selected/python")

    def test_no_deployment_checklist_keeps_every_setup_obligation_open(self):
        from setup_checklist import inspect
        report = inspect(self.directory / "ignorant")
        self.assertFalse(report["complete"])
        self.assertFalse(report["automatic_delivery_verified"])
        self.assertEqual(report["checked"], 0)
        self.assertEqual(len(report["items"]), 37)
        self.assertGreater(report["total"], 100)

    def test_scoped_permission_preserves_comments_modes_and_other_settings(self):
        profile = self.directory / "permission-profile"
        profile.mkdir()
        path = profile / "config.toml"
        original = b'# Keep this comment\nmodel = "existing"\n[sandbox_workspace_write]\nnetwork_access = false\nwritable_roots = [\n  "/existing/#literal", # preserve this array comment semantically\n]\n[unrelated]\nvalue = "unchanged"\n'
        path.write_bytes(original)
        path.chmod(0o640)
        result = profile_scope.configure(profile)
        self.assertTrue(result["added"])
        data = __import__("tomllib").loads(path.read_text())
        self.assertEqual(data["model"], "existing")
        self.assertEqual(data["unrelated"], {"value": "unchanged"})
        self.assertFalse(data["sandbox_workspace_write"]["network_access"])
        self.assertEqual(data["sandbox_workspace_write"]["writable_roots"], ["/existing/#literal", str(c.state_dir(profile))])
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        self.assertTrue(path.read_text().startswith('# Keep this comment\n'))
        before = path.read_bytes()
        self.assertFalse(profile_scope.configure(profile)["changed"])
        self.assertEqual(path.read_bytes(), before)
        profile_scope.configure(profile, allow=False)
        self.assertEqual(__import__("tomllib").loads(path.read_text())["sandbox_workspace_write"]["writable_roots"], ["/existing/#literal"])

    def test_ambiguous_toml_scope_edit_is_refused_without_changing_bytes(self):
        raw = b'sandbox_workspace_write = { writable_roots = ["/existing"] }\n'
        with self.assertRaises(c.ConnectorError):
            profile_scope.edit_roots(raw, ["/existing", "/brain"])

    def test_array_parser_does_not_mistake_quotes_or_comments_for_array_end(self):
        raw = b'''[sandbox_workspace_write]\nwritable_roots = [\n  '/path/with ] and # chars',\n  "/a/quoted\\\"bracket]", # ] does not end it\n]\n[keep]\nnumber=9\n'''
        updated = profile_scope.edit_roots(raw, ["/new"])
        data = __import__("tomllib").loads(updated.decode())
        self.assertEqual(data["keep"]["number"], 9)
        self.assertEqual(data["sandbox_workspace_write"]["writable_roots"], ["/new"])

    @unittest.skipIf(os.name == "nt", "Requires real POSIX qualification")
    def test_timeout_cleans_its_own_descendant_not_only_the_parent(self):
        home = self.directory / "timeout home"
        (home / ".claude/dreaming").mkdir(parents=True)
        deployment = {"home": str(home), "memory": str(home / "memory"), "source": str(self.source), "python": sys.executable}
        pidfile = home / "child.pid"
        child = "import os,time,pathlib;pathlib.Path(" + repr(str(pidfile)) + ").write_text(str(os.getpid()));time.sleep(30)"
        parent = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c'," + repr(child) + "]);time.sleep(30)"
        with self.assertRaisesRegex(c.ConnectorError, "owned process tree"):
            c.run(deployment, [sys.executable, "-c", parent], timeout=0.4)
        pid = int(pidfile.read_text())
        state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
        self.assertTrue(not state or state.startswith("Z"), state)

    def test_profile_disconnect_prevents_even_loading_a_handler(self):
        profile = self.directory / "disconnected"
        c.atomic(c.state_dir(profile) / "DISCONNECTED", b"off")
        with patch.object(c, "active", side_effect=AssertionError("must not read deployment")):
            self.assertEqual(bridge.deliver(profile, "missing", {}), (0, "", ""))

    def test_permission_deny_survives_output_adapter(self):
        deny = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "control"}}
        self.assertEqual(json.loads(bridge.output_for(json.dumps(deny), "PreToolUse", "codex")), deny)

    def test_plain_prompt_hook_becomes_receiver_visible_json(self):
        output = bridge.output_for("A relevant hint λ", "UserPromptSubmit", "codex")
        self.assertEqual(json.loads(output)["hookSpecificOutput"]["additionalContext"], "A relevant hint λ")

    def test_stop_plain_text_never_becomes_a_continuation_loop(self):
        self.assertIn("INVALID_STOP_OUTPUT", json.loads(bridge.output_for("ordinary stop status", "Stop", "codex"))["systemMessage"])

    def test_stop_guard_wake_command_uses_bound_native_entry_point(self):
        command = native_adapter.wake_command("/venv with spaces/python", "/skill with spaces/brainctl.py", "/profile with spaces/.codex")
        if os.name != "nt":
            import shlex
            self.assertEqual(shlex.split(command), ["/venv with spaces/python", "/skill with spaces/brainctl.py", "--profile", "/profile with spaces/.codex", "--maintenance", "repair", "native", "script", "dreaming/codex_wake.py"])
        self.assertNotIn("Python314", command)

    def test_claude_original_hook_output_is_preserved(self):
        raw = "original output\n"
        self.assertEqual(bridge.output_for(raw, "UserPromptSubmit", "claude"), raw)

    def test_non_object_hook_input_is_rejected_before_dispatch(self):
        result = subprocess.run([sys.executable, str(c.SKILL / "scripts/hook_bridge.py"), "--handler", "none"], input="[]", text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)  # protocol success delivers the native UI warning
        self.assertIn("INVALID_HOOK_INPUT", json.loads(result.stdout)["systemMessage"])
        self.assertIn("JSON object", json.loads(result.stdout)["systemMessage"])
        self.assertNotIn("Traceback", result.stderr)

    def test_atomic_write_will_not_follow_configuration_symlink(self):
        original = self.directory / "original"
        original.write_bytes(b"preserve")
        alias = self.directory / "alias"
        alias.symlink_to(original)
        with self.assertRaises(c.ConnectorError):
            c.atomic(alias, b"replace")
        self.assertEqual(original.read_bytes(), b"preserve")

    def test_configuration_snapshot_restores_exact_bytes_and_absence(self):
        original = self.directory / "config"
        original.write_bytes(b"\xef\xbb\xbfexisting\r\n")
        absent = self.directory / "absent"
        records = c.snapshot_files(self.directory, [original, absent], self.directory / "backup")
        original.write_text("changed")
        absent.write_text("created")
        c.restore_files(records)
        self.assertEqual(original.read_bytes(), b"\xef\xbb\xbfexisting\r\n")
        self.assertFalse(absent.exists())

    def test_active_pointer_cannot_escape_its_profile(self):
        profile = self.directory / "profile"
        c.atomic(c.state_dir(profile) / "active.json", {"deployment": str(self.directory / "outside.json")})
        with self.assertRaises(c.ConnectorError):
            c.active(profile)

    def test_runtime_modification_is_rejected_independently_of_receipt(self):
        home = self.directory / "home"
        home.mkdir()
        module = home / "brain.py"
        module.write_text("print('original')\n")
        deployment = {"home": str(home), "runtime_code": {"brain.py": c.sha(module)}}
        c.verify_runtime_code(deployment)
        module.write_text("print('false success')\n")
        with self.assertRaises(c.ConnectorError):
            c.verify_runtime_code(deployment)

    def test_extra_runtime_module_is_rejected(self):
        home = self.directory / "home"
        home.mkdir()
        (home / "injected.py").write_text("print('bad')\n")
        with self.assertRaises(c.ConnectorError):
            c.verify_runtime_code({"home": str(home), "runtime_code": {}})

    def test_bundle_contains_public_source_but_no_runtime_state(self):
        package = self.directory / "brain.zip"
        receipt = brainctl.bundle(package)
        with zipfile.ZipFile(package) as archive:
            names = archive.namelist()
            if (c.SKILL / "assets/upstream-source.tar.gz").is_file():
                self.assertIn("brain-framework/assets/upstream-source.tar.gz", names)
            self.assertIn("brain-framework/references/upstream-lock.json", names)
            self.assertFalse(any("generations/" in name or "active.json" in name or "__pycache__" in name for name in names))
        self.assertEqual(receipt["sha256"], c.sha(package))

    def migration_fixture(self):
        old = self.directory / "old-home"
        new = self.directory / "new-home"
        (old / "memory").mkdir(parents=True)
        (new / "memory").mkdir(parents=True)
        previous = {"id": "old", "home": str(old), "memory": str(old / "memory"), "runtime_code": {}, "source": "/old-source", "python": "/old-python"}
        deployment = {"id": "new", "home": str(new), "memory": str(new / "memory"), "source": "/new-source", "python": "/new-python"}
        return old, new, previous, deployment

    def test_model_cache_links_are_excluded_before_private_state_validation(self):
        old, new, previous, deployment = self.migration_fixture()
        outside = self.directory / "model-blob"
        outside.write_bytes(b"public model blob")
        cache = old / ".cache/huggingface"
        cache.mkdir(parents=True)
        (cache / "model").symlink_to(outside)
        (old / "memory/fact.md").write_bytes(b"preserve this real fact\n")
        result = c.migrate_state(previous, deployment)
        self.assertEqual((new / "memory/fact.md").read_bytes(), b"preserve this real fact\n")
        self.assertFalse((new / ".cache").exists())
        self.assertTrue(result["copied"])

    def test_private_memory_symlink_still_requires_review(self):
        old, new, previous, deployment = self.migration_fixture()
        target = self.directory / "private-other-boundary"
        target.write_bytes(b"must not copy across boundary")
        (old / "memory/alias.md").symlink_to(target)
        with self.assertRaises(c.ConnectorError):
            c.migrate_state(previous, deployment)
        self.assertFalse((new / "memory/alias.md").exists())

    def test_old_embedding_authority_is_not_reused_in_new_generation(self):
        old, new, previous, deployment = self.migration_fixture()
        derived = old / ".claude/dreaming"
        derived.mkdir(parents=True)
        (derived / ".embedding_current.json").write_text('{"sidecar":"/old/generation.npz"}\n')
        (derived / ".embedding_status.json").write_text('{"complete":true}\n')
        (derived / "memory_embeddings.abc.npz").write_bytes(b"old artifact")
        c.migrate_state(previous, deployment)
        self.assertFalse((new / ".claude/dreaming/.embedding_current.json").exists())
        self.assertFalse((new / ".claude/dreaming/.embedding_status.json").exists())
        self.assertFalse((new / ".claude/dreaming/memory_embeddings.abc.npz").exists())


    @unittest.skipIf(os.name == "nt", "Requires real POSIX qualification")
    def test_native_child_priority_is_below_normal_without_changing_the_caller(self):
        home = self.directory / "priority home"
        (home / ".claude/dreaming").mkdir(parents=True)
        d = {"home": str(home), "memory": str(home / "memory"), "source": str(self.source), "python": sys.executable}
        before = os.getpriority(os.PRIO_PROCESS, 0)
        result = c.run(d, [sys.executable, "-c", "import os;print(os.getpriority(os.PRIO_PROCESS,0))"])
        self.assertGreaterEqual(int(result.stdout), 10)
        self.assertEqual(os.getpriority(os.PRIO_PROCESS, 0), before)



class InstallationTests(unittest.TestCase):
    """Actual cold bootstrap, original compiler and configuration transaction; no model/network."""

    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="brain-fresh-install-")
        cls.profile = Path(cls.workspace.name) / "machine with spaces λ" / ".codex"
        cls.profile.mkdir(parents=True)
        cls.original_instructions = b"# Existing owner instructions\r\nKeep this exact text.\r\n"
        (cls.profile / "AGENTS.md").write_bytes(cls.original_instructions)
        cls.original_hooks = {"description": "unrelated owner configuration", "hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "printf owner_control", "timeout": 3}]}]}}
        (cls.profile / "hooks.json").write_text(json.dumps(cls.original_hooks) + "\n")
        cls.original_hook_bytes = (cls.profile / "hooks.json").read_bytes()
        old_skill = cls.profile / "skills/brain-framework"
        old_skill.mkdir(parents=True)
        (old_skill / "SKILL.md").write_bytes(b"Original skill to restore exactly.\r\n")
        cls.deployment = c.create_deployment(cls.profile, dependencies=False)
        cls.deployment = c.activate(cls.profile, cls.deployment)
        binding.bind_main(cls.profile, "Isolated test fixture owner designation; not a live user brain", apply=True)

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def test_a_fresh_original_compiler_has_actual_three_file_receipt(self):
        dreaming = Path(self.deployment["home"]) / ".claude/dreaming"
        candidates = [p for p in dreaming.glob("*.json") if p.is_file() and '"brain-instincts-compile-receipt-v1"' in p.read_text(errors="replace")]
        self.assertEqual(len(candidates), 1)
        receipt = c.read_json(candidates[0])
        for name, digest in receipt["files"].items():
            self.assertEqual(c.sha(dreaming / name), digest)
        overlay = c.read_json(dreaming / "instincts_learned.json")
        self.assertEqual(overlay[0]["status"], "provisional")
        self.assertEqual(overlay[0]["mode"], "guide")
        self.assertIn("not personal history", overlay[0]["origin"])
        self.assertEqual(c.native(self.deployment, "script", ["dreaming/instincts.py", "--parse-only"]).returncode, 0)

    def test_b_empty_overlay_is_rejected_without_certifying_shrunken_gut(self):
        dreaming = Path(self.deployment["home"]) / ".claude/dreaming"
        authored = dreaming / "instincts_learned.json"
        original = authored.read_bytes()
        before = {name: c.sha(dreaming / name) for name in ("instincts.tsv", "INSTINCTS.md")}
        try:
            authored.write_text("[]\n")
            result = c.native(self.deployment, "script", ["dreaming/instincts.py"], check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(before, {name: c.sha(dreaming / name) for name in before})
        finally:
            authored.write_bytes(original)

    def test_c_existing_instruction_and_hook_survive_successful_activation(self):
        self.assertIn(self.original_instructions, (self.profile / "AGENTS.md").read_bytes())
        config = c.read_json(self.profile / "hooks.json")
        self.assertEqual(config["description"], self.original_hooks["description"])
        self.assertEqual(config["hooks"]["SessionEnd"], self.original_hooks["hooks"]["SessionEnd"])
        self.assertIn("SessionStart", config["hooks"])

    def test_d_native_verified_memory_requires_evidence_and_leaves_nothing_on_rejection(self):
        slug = "unsubstantiated_verified_control"
        result = c.native(self.deployment, "brain", ["remember", slug, "--type", "project", "--status", "verified", "--desc", "Unsubstantiated control"], check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((Path(self.deployment["memory"]) / (slug + ".md")).exists())

    def test_e_native_writer_reader_and_recall_use_this_corpus(self):
        result = c.native(self.deployment, "brain", ["recall", "Installation provenance " + self.deployment["id"]])
        self.assertIn(self.deployment["canary"], result.stdout)
        node = c.native(self.deployment, "brain", ["node", self.deployment["canary"]])
        self.assertIn(self.deployment["source_revision"], node.stdout)

    def test_f_script_entrypoint_rejects_traversal_and_non_upstream_code(self):
        for name in ("../../outside.py", "/tmp/outside.py", "dreaming/unknown.py"):
            with self.assertRaises(c.ConnectorError):
                c.native(self.deployment, "script", [name])

    def test_g_stale_or_self_reported_receiver_receipt_cannot_make_ready(self):
        from receiver_probe import validate_receivers
        path = Path(self.deployment["home"]).parent / "receiver-verification.json"
        c.atomic(path, {"passed": True, "root_verified": True, "worker_verified": True})
        self.assertFalse(validate_receivers(self.deployment)["passed"])
        path.unlink()

    def test_h_rollback_preserves_a_concurrent_owner_edit_instead_of_clobbering_it(self):
        path = self.profile / "AGENTS.md"
        before = path.read_bytes()
        altered = before + b"\nA new owner instruction.\n"
        try:
            path.write_bytes(altered)
            with self.assertRaises(c.ConnectorError):
                c.rollback(self.profile)
            self.assertEqual(path.read_bytes(), altered)
            self.assertEqual(c.active(self.profile)["id"], self.deployment["id"])
        finally:
            path.write_bytes(before)

    def test_i_skill_edit_refuses_rollback_without_losing_owner_change(self):
        path = self.profile / "skills/brain-framework/SKILL.md"
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"\nOwner's later edit\n")
            with self.assertRaisesRegex(c.ConnectorError, "skill changed"):
                c.rollback(self.profile)
            self.assertTrue(path.read_bytes().endswith(b"Owner's later edit\n"))
        finally:
            path.write_bytes(original)

    def test_j_bound_checklist_advice_and_nonzero_failure_survive_adapter(self):
        result = c.native(self.deployment, "checklist", ["working", "T-19000101-999", "missing entry"], check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("brainctl.py", result.stderr)

    def test_k_empty_native_ledgers_are_real_empty_evidence(self):
        home = Path(self.deployment["home"])
        self.assertEqual((home / ".claude/dreaming/instinct_fires.tsv").read_bytes(), b"")
        self.assertIn("No archived entries", (Path(self.deployment["memory"]) / "INDEX_ARCHIVE.md").read_text())

    def test_l_canonical_binding_allows_native_indexing_and_refuses_a_changed_marker(self):
        env = c.runtime_env(self.deployment)
        self.assertNotIn("BRAIN_MEM_DIR", env)
        marker = Path(self.deployment["home"]) / ".claude/dreaming/BRAIN_SLUG"
        before = marker.read_bytes()
        try:
            marker.write_text("different-private-corpus")
            with self.assertRaisesRegex(c.ConnectorError, "marker changed"):
                c.runtime_env(self.deployment)
        finally:
            marker.write_bytes(before)
        result = c.native(self.deployment, "brain", ["remember", "native_incremental_index_control", "--type", "project",
                          "--desc", "Ordinary native writes must enter the canonical lexical index", "--body", "A connector integration control from the native memory writer."])
        self.assertIn("token content verified", result.stdout)
        self.assertNotIn("custom corpus memory saved", result.stdout)

    def test_m_repair_missing_owned_hook_preserves_unrelated_settings(self):
        path = Path(self.deployment["hook_config"])
        before = path.read_bytes()
        current = c.read_json(path)
        removed = current["hooks"]["SessionStart"].pop()
        c.atomic(path, current)
        try:
            plan = c.repair_wiring(self.profile)
            self.assertEqual(plan["missing_groups"], 1)
            self.assertEqual(c.read_json(path), current)
            fixed = c.repair_wiring(self.profile, apply=True)
            self.assertTrue(fixed["changes_applied"])
            after = c.read_json(path)
            self.assertIn(removed, after["hooks"]["SessionStart"])
            self.assertEqual(after["description"], "unrelated owner configuration")
        finally:
            path.write_bytes(before)

    def test_n_disconnect_reconnect_restores_owned_scope_and_native_switch(self):
        profile = self.profile
        c.disable(profile)
        root = str(c.state_dir(profile))
        self.assertNotIn(root, __import__("tomllib").loads((profile / "config.toml").read_text())["sandbox_workspace_write"]["writable_roots"])
        self.assertTrue((c.state_dir(profile) / "DISCONNECTED").exists())
        with patch.object(sys, "argv", ["brainctl", "--profile", str(profile), "enable"]), contextlib.redirect_stdout(io.StringIO()):
            brainctl.main()
        self.assertFalse((c.state_dir(profile) / "DISCONNECTED").exists())
        self.assertFalse((Path(self.deployment["home"]) / ".claude/brain_offline/ALL.flag").exists())
        self.assertIn(root, __import__("tomllib").loads((profile / "config.toml").read_text())["sandbox_workspace_write"]["writable_roots"])

    def checklist_state(self):
        # the hook keeps its store in the brain tree when ~/brain-repo/12_cognitive_architecture exists, else in ~/.claude
        home = Path(self.deployment["home"])
        tree = home / "brain-repo/12_cognitive_architecture"
        return (tree / "task_checklist" if tree.is_dir() else home / ".claude/task_checklist") / "state.json"

    def test_o_app_prediction_cannot_become_an_owner_ask_but_genuine_request_can(self):
        key = next(k for k,s in self.deployment["handlers"].items() if s["event"] == "UserPromptSubmit" and "task_checklist_hook.py" in " ".join(s.get("argv", [])))
        state = self.checklist_state()
        before = state.read_bytes() if state.exists() else None
        text = "This is an app-generated request for a suggested next user message. The user did not write this message. Predict a suggestion."
        self.assertEqual(bridge.deliver(self.profile,key,{"hook_event_name":"UserPromptSubmit","session_id":"connector-fixture","prompt":text}), (0,"",""))
        self.assertEqual(state.read_bytes() if state.exists() else None,before)
        asked = "Please verify this genuine owner request in the isolated source-control fixture."
        code,out,err = bridge.deliver(self.profile,key,{"hook_event_name":"UserPromptSubmit","session_id":"11111111-2222-4333-8444-555555555555","prompt":asked})
        self.assertEqual(code,0,err)
        self.assertTrue(any(e["asked_words"]==asked for e in c.read_json(state)["entries"].values()))
        self.assertFalse(bridge.app_generated_owner_prompt({"prompt":"Review this quotation: "+text}))

    def test_p_quiet_host_execution_probe_never_invents_owner_requests(self):
        state = self.checklist_state()
        before = set(c.read_json(state)["entries"])
        report = c.verify(self.deployment, integrated=False, hooks=True, smoke=False)
        self.assertTrue(report["passed"], report["checks"])
        self.assertEqual(set(c.read_json(state)["entries"]),before)

    def test_q_main_binding_blocks_unknown_redirected_and_disconnected_corpora(self):
        registry = binding.registry_path(self.profile)
        original = registry.read_bytes()
        try:
            registry.unlink()
            with self.assertRaisesRegex(c.ConnectorError, "No explicit main"):
                binding.resolve(self.profile)
            registry.write_bytes(original)
            self.assertEqual(binding.resolve(self.profile)["id"], self.deployment["id"])
            data = c.read_json(registry)
            data["main"]["memory"] += "-substitute"
            c.atomic(registry, data)
            with self.assertRaisesRegex(c.ConnectorError, "changed at memory"):
                binding.resolve(self.profile)
            registry.write_bytes(original)
            offline = c.state_dir(self.profile) / "DISCONNECTED"
            offline.write_text("fixture")
            with self.assertRaisesRegex(c.ConnectorError, "disconnected"):
                binding.resolve(self.profile)
            offline.unlink()
        finally:
            registry.write_bytes(original)
            (c.state_dir(self.profile) / "DISCONNECTED").unlink(missing_ok=True)

    def test_r_cli_failed_admission_cannot_write_but_authorized_maintenance_uses_native_writer(self):
        target = Path(self.deployment["memory"]) / "blocked_write_control.md"
        command = [sys.executable, str(c.SKILL / "scripts/brainctl.py"), "--profile", str(self.profile)]
        refused = subprocess.run(command + ["native", "brain", "remember", "blocked_write_control", "--type", "project", "--desc", "Must never be written"], capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("requires current", refused.stderr)
        self.assertFalse(target.exists())
        routed = subprocess.run(command + ["native", "script", "dreaming/brain.py", "remember", "blocked_write_control", "--type", "project", "--desc", "Must never be written"], capture_output=True, text=True)
        self.assertNotEqual(routed.returncode, 0)
        self.assertIn("requires current", routed.stderr)
        self.assertFalse(target.exists())
        slug = "explicit_maintenance_native_control"
        allowed = subprocess.run(command + ["--maintenance", "qualification", "native", "brain", "remember", slug, "--type", "project", "--desc", "Isolated native qualification write", "--body", "Not live history"], capture_output=True, text=True)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertTrue((Path(self.deployment["memory"]) / (slug + ".md")).is_file())
        self.assertIn(slug, c.native(self.deployment, "brain", ["node", slug]).stdout)

    @patch.dict(os.environ, {"CODEX_THREAD_ID": "receiver-guard-control"})
    def test_s_external_main_cannot_qualify_an_empty_receiver_and_receiver_edits_reopen_gate(self):
        # Inject only health/setup producer verdicts to reach the admission guard.
        # Real descriptor, source/runtime integrity, wake reads and fingerprints run.
        empty = Path(self.workspace.name) / "empty receiver"
        binding.bind_main(empty, "Synthetic fixture shares the explicit existing main", True, self.profile)
        with patch.object(c, "verify", return_value={"ready": True}) as health, \
             patch("setup_checklist.inspect", return_value=measured_fixture("checked-current-observation")):
            with self.assertRaisesRegex(c.ConnectorError, "No deployment"):
                binding.admit(empty, "fixture-session", self.profile)
            health.assert_not_called()
            report = binding.admit(self.profile, "receiver-guard-control", self.profile)
        receipt = binding.activation_path(self.profile, "receiver-guard-control", self.profile)
        startup = self.profile / "AGENTS.md"
        original = startup.read_bytes()
        try:
            self.assertTrue(report["passed"])
            self.assertTrue(binding.require_admission(self.profile, "receiver-guard-control", self.profile)["passed"])
            startup.write_bytes(original + b"\nReceiver guard negative control\n")
            with self.assertRaisesRegex(c.ConnectorError, "receiver changed"):
                binding.require_admission(self.profile, "receiver-guard-control", self.profile)
        finally:
            startup.write_bytes(original)
            receipt.unlink(missing_ok=True)

    def test_t_core_delivery_does_not_admit_unmeasured_selected_faculties(self):
        from receiver_probe import deployment_fingerprint
        from setup_checklist import selected_capabilities
        evidence_path = c.state_dir(self.profile) / "setup-evidence.json"
        artifact = c.state_dir(self.profile) / "isolated-admission-control.txt"
        artifact.write_text("Synthetic admission evidence for the isolated gate control only.\n")
        catalog = c.read_json(c.SKILL / "references/mechanisms.json")
        entries = {
            row["id"] + ":" + str(index): {
                "deployment": self.deployment["id"], "fingerprint": deployment_fingerprint(self.deployment),
                "verdict": "pass", "scope": "isolated admission-binding control; no real faculty qualification",
                "evidence": {str(artifact): c.sha(artifact)},
            }
            for row in catalog["mechanisms"] if row["capability"] in selected_capabilities(self.deployment)
            for index, _ in enumerate(row["setup_checks"])
        }
        try:
            # Simulate only the receiver axis; all local source/config/readback checks still execute.
            with patch("receiver_probe.validate_receivers", return_value={"passed": True}):
                missing = c.verify(self.deployment, integrated=True, hooks=False, smoke=False)
                self.assertTrue(missing["connection_verified"], missing["checks"])
                self.assertFalse(missing["ready"])
                self.assertEqual(missing["faculties"]["graph"]["state"], "configured-awaiting-admission")
                for capability in ("reflex", "checklist", "continuity", "governor"):
                    self.assertTrue(missing["faculties"][capability]["selected"])
                    self.assertEqual(missing["faculties"][capability]["state"], "configured-awaiting-admission")
                self.assertFalse(missing["faculties"]["specialists"]["selected"])
                c.atomic(evidence_path, entries)
                admitted = c.verify(self.deployment, integrated=True, hooks=False, smoke=False)
                self.assertFalse(admitted["ready"])
                self.assertTrue(admitted["unrun_live_checks"])
                self.assertFalse(admitted["readiness_gaps"])
                artifact.write_text("Changed evidence must reopen admission.\n")
                changed = c.verify(self.deployment, integrated=True, hooks=False, smoke=False)
                self.assertTrue(changed["connection_verified"])
                self.assertFalse(changed["ready"])
        finally:
            evidence_path.unlink(missing_ok=True)
            artifact.unlink(missing_ok=True)


    def test_u_receiver_fingerprint_binds_the_discovered_setup_contract(self):
        from receiver_probe import deployment_fingerprint
        path = self.profile / "skills/brain-framework/references/mechanisms.json"
        before = path.read_bytes()
        fingerprint = deployment_fingerprint(self.deployment)
        try:
            path.write_bytes(before + b"\n")
            self.assertNotEqual(deployment_fingerprint(self.deployment), fingerprint)
        finally:
            path.write_bytes(before)
        self.assertEqual(deployment_fingerprint(self.deployment), fingerprint)

    def test_receiver_must_use_the_fingerprint_recorded_before_dispatch(self):
        import receiver_probe
        with tempfile.TemporaryDirectory(dir=self.workspace.name) as directory:
            folder = Path(directory)
            for name in ("prompt.txt", "request.json", "records.json"):
                (folder / name).write_text("qualification input")
            origin = {"profile": str(self.profile), "deployment": self.deployment["id"],
                      "revision": self.deployment["source_revision"], "reviewed_automation": False,
                      "deployment_fingerprint": "earlier-configuration",
                      "fixture_hashes": {name: c.sha(folder / name)
                                         for name in ("prompt.txt", "request.json", "records.json")}}
            c.atomic(folder / "invocation.json", origin)
            with patch.object(receiver_probe, "inspect_root") as behavior:
                with self.assertRaisesRegex(c.ConnectorError, "exact current deployment"):
                    receiver_probe.accept_root(self.deployment, folder)
                behavior.assert_not_called()
                origin["deployment_fingerprint"] = receiver_probe.deployment_fingerprint(self.deployment)
                c.atomic(folder / "invocation.json", origin)
                (folder / "records.json").write_text("changed after dispatch")
                with self.assertRaisesRegex(c.ConnectorError, "fixture changed"):
                    receiver_probe.accept_root(self.deployment, folder)
                behavior.assert_not_called()


    def test_z_rollback_restores_original_configuration_and_retains_memories(self):
        c.rollback(self.profile)
        self.assertEqual((self.profile / "skills/brain-framework/SKILL.md").read_bytes(), b"Original skill to restore exactly.\r\n")
        self.assertEqual((self.profile / "AGENTS.md").read_bytes(), self.original_instructions)
        self.assertEqual((self.profile / "hooks.json").read_bytes(), self.original_hook_bytes)
        self.assertIsNone(c.active(self.profile, required=False))
        self.assertTrue((Path(self.deployment["memory"]) / (self.deployment["canary"] + ".md")).is_file())


    @patch.dict(os.environ, {"CODEX_THREAD_ID": "isolated-upkeep-verdict-control"})
    def test_v_failed_upkeep_replaces_an_earlier_session_admission(self):
        session = "isolated-upkeep-verdict-control"
        # Only verdict producers are simulated; real identity/integrity/wake/fingerprint
        # paths run, and these receipts exist only in the temporary profile.
        with patch.object(c, "verify", return_value={"ready": True}), \
             patch("setup_checklist.inspect", return_value=measured_fixture("checked-current-observation")), \
             patch("recall_upkeep.ensure_current", return_value={"state": "current"}):
            self.assertTrue(binding.admit(self.profile, session, self.profile)["passed"])
        with patch.object(c, "verify", return_value={"ready": True}), \
             patch("setup_checklist.inspect", return_value=measured_fixture("checked-current-observation")), \
             patch("recall_upkeep.ensure_current", side_effect=c.ConnectorError("maintenance refused")):
            report = binding.admit(self.profile, session, self.profile)
        self.assertFalse(report["passed"])
        self.assertEqual(report["brains"][0]["recall_upkeep"]["state"], "unverified")
        receipt = binding.activation_path(self.profile, session, self.profile)
        self.assertFalse(c.read_json(receipt)["passed"])
        with self.assertRaisesRegex(c.ConnectorError, "failed"):
            binding.require_admission(self.profile, session, self.profile)
        receipt.unlink()



if __name__ == "__main__":
    unittest.main(verbosity=2)
