import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import sys


MODULE = Path(__file__).resolve().parents[1] / "backup_brain.py"
SPEC = importlib.util.spec_from_file_location("backup_brain", MODULE)
backup = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
# register before exec: @dataclass resolves its own
# module via sys.modules and dies on None
sys.modules[SPEC.name] = backup
SPEC.loader.exec_module(backup)


def git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


class BackupBrainTests(unittest.TestCase):
    def make_repo(self, root: Path) -> tuple[Path, Path]:
        repo = root / "repo"
        remote = root / "remote.git"
        repo.mkdir()
        subprocess.check_call(["git", "init", "-b", "main"], cwd=repo, stdout=subprocess.DEVNULL)
        subprocess.check_call(["git", "config", "user.email", "test@example.invalid"], cwd=repo)
        subprocess.check_call(["git", "config", "user.name", "backup test"], cwd=repo)
        (repo / "RESTORE.md").write_text("restore\n", encoding="utf-8")
        (repo / "brain.txt").write_text("one\n", encoding="utf-8")
        subprocess.check_call(["git", "add", "."], cwd=repo)
        subprocess.check_call(["git", "commit", "-m", "one"], cwd=repo, stdout=subprocess.DEVNULL)
        subprocess.check_call(["git", "init", "--bare", remote], stdout=subprocess.DEVNULL)
        subprocess.check_call(["git", "remote", "add", "resilience", str(remote)], cwd=repo)
        return repo, remote

    def test_source_guard_and_status_hash_notice_dirty_work(self):
        with tempfile.TemporaryDirectory() as td:
            repo, _ = self.make_repo(Path(td))
            clean = backup.source_snapshot(repo, min_tracked=1)
            self.assertEqual(clean["status_entries"], 0)
            (repo / "brain.txt").write_text("two\n", encoding="utf-8")
            dirty = backup.source_snapshot(repo, min_tracked=1)
            self.assertGreater(dirty["status_entries"], 0)
            self.assertNotEqual(clean["status_sha256"], dirty["status_sha256"])

    def test_content_fingerprint_notices_dirty_file_bytes_when_status_does_not(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, _ = self.make_repo(root)
            (repo / "brain.txt").write_text("two\n", encoding="utf-8")
            first_status = backup.source_snapshot(repo, min_tracked=1)
            first = backup.write_recovery_artifacts(repo, root / "first", first_status)

            (repo / "brain.txt").write_text("three\n", encoding="utf-8")
            second_status = backup.source_snapshot(repo, min_tracked=1)
            second = backup.write_recovery_artifacts(repo, root / "second", second_status)

            self.assertEqual(first_status["status_sha256"], second_status["status_sha256"])
            self.assertNotEqual(first["content_sha256"], second["content_sha256"])

    def test_github_updates_are_fast_forward_and_divergence_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            repo, remote = self.make_repo(Path(td))
            first = git(repo, "rev-parse", "HEAD")
            backup.push_ref(repo, "resilience", first)
            (repo / "brain.txt").write_text("two\n", encoding="utf-8")
            subprocess.check_call(["git", "commit", "-am", "two"], cwd=repo, stdout=subprocess.DEVNULL)
            second = git(repo, "rev-parse", "HEAD")
            self.assertEqual(backup.push_github(repo, "resilience", second), second)

            other = Path(td) / "other"
            subprocess.check_call(["git", "clone", "-b", "main", str(remote), str(other)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "config", "user.email", "test@example.invalid"], cwd=other)
            subprocess.check_call(["git", "config", "user.name", "other"], cwd=other)
            (other / "remote-only.txt").write_text("diverge\n", encoding="utf-8")
            subprocess.check_call(["git", "add", "."], cwd=other)
            subprocess.check_call(["git", "commit", "-m", "remote only"], cwd=other, stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "push", "origin", "main"], cwd=other, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with self.assertRaises(backup.BackupError):
                backup.push_github(repo, "resilience", second)

    def test_process_lock_rejects_overlap(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "run.lock"
            with backup.ProcessLock(path):
                with self.assertRaises(backup.BackupError):
                    with backup.ProcessLock(path):
                        pass

    def test_full_then_incremental_bundle_chain(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, _ = self.make_repo(root)
            state = root / "state"
            state.mkdir()
            first = git(repo, "rev-parse", "HEAD")
            first_refs = backup.selected_refs(repo)
            full_stage = root / "full"
            full_stage.mkdir()
            full = backup.create_bundle(repo, state, full_stage, first, first_refs)
            self.assertEqual(full["type"], "full")
            self.assertTrue((full_stage / full["file"]).is_file())

            previous = {
                "source_head_sha": first, "source_refs": first_refs, "run_id": "base",
            }
            (repo / "brain.txt").write_text("two\n", encoding="utf-8")
            subprocess.check_call(["git", "commit", "-am", "two"], cwd=repo, stdout=subprocess.DEVNULL)
            second = git(repo, "rev-parse", "HEAD")
            second_refs = backup.selected_refs(repo)
            incremental_stage = root / "incremental"
            incremental_stage.mkdir()
            incremental = backup.create_bundle(repo, state, incremental_stage, second, second_refs, previous)
            self.assertEqual(incremental["type"], "incremental")
            self.assertEqual(incremental["base_source_head"], first)
            self.assertTrue((incremental_stage / incremental["file"]).is_file())

    def test_new_committed_agent_branch_is_not_treated_as_reference(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, _ = self.make_repo(root)
            state = root / "state"
            state.mkdir()
            main = git(repo, "rev-parse", "HEAD")
            original_refs = backup.selected_refs(repo)
            previous = {
                "source_head_sha": main, "source_refs": original_refs, "run_id": "base",
            }
            subprocess.check_call(["git", "checkout", "-b", "agent-work"], cwd=repo, stdout=subprocess.DEVNULL)
            (repo / "agent.txt").write_text("committed\n", encoding="utf-8")
            subprocess.check_call(["git", "add", "agent.txt"], cwd=repo)
            subprocess.check_call(["git", "commit", "-m", "agent branch"], cwd=repo, stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "checkout", "main"], cwd=repo, stdout=subprocess.DEVNULL)
            stage = root / "agent-bundle"
            stage.mkdir()
            result = backup.create_bundle(repo, state, stage, main, backup.selected_refs(repo), previous)
            self.assertEqual(result["type"], "incremental")
            heads = git(repo, "bundle", "list-heads", str(stage / result["file"]))
            self.assertIn("refs/heads/agent-work", heads)

    def test_selected_refs_include_stash_and_custom_namespaces(self):
        with tempfile.TemporaryDirectory() as td:
            repo, _ = self.make_repo(Path(td))
            head = git(repo, "rev-parse", "HEAD")
            subprocess.check_call(["git", "update-ref", "refs/safety/checkpoint", head], cwd=repo)
            (repo / "brain.txt").write_text("dirty\n", encoding="utf-8")
            subprocess.check_call(["git", "stash", "push", "-m", "fixture"], cwd=repo, stdout=subprocess.DEVNULL)
            refs = backup.selected_refs(repo)
            self.assertEqual(refs["refs/safety/checkpoint"], head)
            self.assertIn("refs/stash", refs)

    def test_receipt_version_listing_paginates_past_delete_markers(self):
        pages = [
            json.dumps({
                "IsTruncated": True,
                "Versions": [{"Key": "receipts/one.json", "VersionId": "v1"}],
                "DeleteMarkers": [{"Key": "receipts/one.json", "VersionId": "deleted"}],
                "NextKeyMarker": "receipts/one.json", "NextVersionIdMarker": "v1",
            }),
            json.dumps({
                "IsTruncated": False,
                "Versions": [{"Key": "receipts/two.json", "VersionId": "v2"}],
            }),
        ]
        with mock.patch.object(backup, "aws", side_effect=pages) as aws_call:
            versions = backup.list_receipt_versions(Path.cwd(), "bucket", "region", "profile")
        self.assertEqual([row["VersionId"] for row in versions], ["v1", "v2"])
        second_args = aws_call.call_args_list[1].args
        self.assertIn("--key-marker", second_args)
        self.assertIn("--version-id-marker", second_args)

    def test_dependency_verifier_checks_full_incrementals_and_latest_recovery(self):
        full = {"bundle": {"type": "full", "file": "full.bundle"}}
        incremental = {"bundle": {"type": "incremental", "file": "inc.bundle"}}
        target = {"bundle": {"type": "reference"}}
        history = [({}, full), ({}, incremental), ({}, target)]
        with mock.patch.object(backup, "verify_manifest_artifacts") as verify:
            backup.verify_restore_dependencies(Path.cwd(), history)
        self.assertEqual(verify.call_args_list[0].args[2], ["full.bundle"])
        self.assertEqual(verify.call_args_list[1].args[2], ["inc.bundle"])
        self.assertEqual(verify.call_args_list[2].args, (Path.cwd(), target))

    def test_incremental_bundle_captures_detached_unreferenced_head(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, _ = self.make_repo(root)
            state = root / "state"
            state.mkdir()
            base_head = git(repo, "rev-parse", "HEAD")
            base_refs = backup.selected_refs(repo)
            previous = {"source_head_sha": base_head, "source_refs": base_refs, "run_id": "base"}

            subprocess.check_call(["git", "checkout", "--detach"], cwd=repo, stdout=subprocess.DEVNULL)
            (repo / "detached.txt").write_text("recover me\n", encoding="utf-8")
            subprocess.check_call(["git", "add", "detached.txt"], cwd=repo)
            subprocess.check_call(["git", "commit", "-m", "detached"], cwd=repo, stdout=subprocess.DEVNULL)
            detached_head = git(repo, "rev-parse", "HEAD")
            self.assertNotIn(detached_head, backup.selected_refs(repo).values())

            stage = root / "incremental"
            stage.mkdir()
            result = backup.create_bundle(
                repo, state, stage, detached_head, backup.selected_refs(repo), previous,
            )
            self.assertEqual(result["type"], "incremental")
            heads = git(repo, "bundle", "list-heads", str(stage / result["file"]))
            self.assertIn(detached_head, heads)

    def test_compute_gate_allows_owner_away_and_defers_owner_present(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "compute_state.json"
            state.write_text(json.dumps({
                "status": "ok", "mode": "owner_away_open", "owner_active": False,
            }), encoding="utf-8")
            backup.require_compute_window(state)
            state.write_text(json.dumps({
                "status": "ok", "mode": "owner_present_polite", "owner_active": False,
            }), encoding="utf-8")
            with self.assertRaisesRegex(backup.BackupError, "owner-away/open"):
                backup.require_compute_window(state)

    def test_scheduled_launcher_static_no_window_and_compute_contract(self):
        root = MODULE.parent
        launcher = (root / "launch_backup.vbs").read_text(encoding="utf-8")
        runner = (root / "run_backup.ps1").read_text(encoding="utf-8")
        installer = (root / "install_task.ps1").read_text(encoding="utf-8")
        self.assertIn(", 0, True", launcher)
        self.assertIn("C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe", launcher)
        self.assertIn("PriorityClass = 'BelowNormal'", runner)
        self.assertIn("owner_away_open", runner)
        self.assertIn("wscript.exe", installer)
        self.assertIn('"//B', installer)


if __name__ == "__main__":
    unittest.main()
