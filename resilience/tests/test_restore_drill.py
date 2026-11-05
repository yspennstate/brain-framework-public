import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils
import sys


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # register before exec: @dataclass resolves its own
    # module via sys.modules and dies on None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


backup = load_module("backup_brain_restore_test", ROOT / "backup_brain.py")
restore = load_module("restore_drill_under_test", ROOT / "restore_drill.py")
envelope = load_module("envelope_restore_test", ROOT / "envelope.py")


def git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


class RestoreDrillTests(unittest.TestCase):
    def make_repo(self, root: Path) -> Path:
        repo = root / "source"
        repo.mkdir()
        subprocess.check_call(["git", "init", "-b", "main"], cwd=repo, stdout=subprocess.DEVNULL)
        subprocess.check_call(["git", "config", "user.email", "restore@example.invalid"], cwd=repo)
        subprocess.check_call(["git", "config", "user.name", "restore test"], cwd=repo)
        (repo / "RESTORE.md").write_text("restore\n", encoding="utf-8")
        (repo / "brain.txt").write_text("one\n", encoding="utf-8")
        subprocess.check_call(["git", "add", "."], cwd=repo)
        subprocess.check_call(["git", "commit", "-m", "one"], cwd=repo, stdout=subprocess.DEVNULL)
        return repo

    def test_full_incremental_ref_update_and_reference_restore_all_refs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self.make_repo(root)
            state = root / "state"
            state.mkdir()

            first_head = git(repo, "rev-parse", "HEAD")
            first_refs = backup.selected_refs(repo)
            first_stage = root / "first"
            first_stage.mkdir()
            full = backup.create_bundle(repo, state, first_stage, first_head, first_refs)
            first = {
                "run_id": "r1", "source_head_sha": first_head, "source_refs": first_refs,
                "bundle": full, "_files": {full["file"]: first_stage / full["file"]},
            }

            (repo / "brain.txt").write_text("two\n", encoding="utf-8")
            subprocess.check_call(["git", "commit", "-am", "two"], cwd=repo, stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "checkout", "-b", "agent-side"], cwd=repo, stdout=subprocess.DEVNULL)
            (repo / "agent.txt").write_text("branch\n", encoding="utf-8")
            subprocess.check_call(["git", "add", "agent.txt"], cwd=repo)
            subprocess.check_call(["git", "commit", "-m", "agent"], cwd=repo, stdout=subprocess.DEVNULL)
            subprocess.check_call(["git", "checkout", "main"], cwd=repo, stdout=subprocess.DEVNULL)
            second_head = git(repo, "rev-parse", "HEAD")
            second_refs = backup.selected_refs(repo)
            second_stage = root / "second"
            second_stage.mkdir()
            incremental = backup.create_bundle(repo, state, second_stage, second_head, second_refs, first)
            self.assertEqual(incremental["type"], "incremental")
            second = {
                "run_id": "r2", "source_head_sha": second_head, "source_refs": second_refs,
                "bundle": incremental, "_files": {incremental["file"]: second_stage / incremental["file"]},
            }

            subprocess.check_call(["git", "branch", "-D", "agent-side"], cwd=repo, stdout=subprocess.DEVNULL)
            third_refs = backup.selected_refs(repo)
            ref_update = backup.create_bundle(repo, state, root / "third", second_head, third_refs, second)
            self.assertEqual(ref_update["type"], "ref-update")
            third = {
                "run_id": "r3", "source_head_sha": second_head,
                "source_refs": third_refs, "bundle": ref_update, "_files": {},
            }
            reference = backup.create_bundle(repo, state, root / "fourth", second_head, third_refs, third)
            self.assertEqual(reference["type"], "reference")
            fourth = {
                "run_id": "r4", "source_head_sha": second_head,
                "source_refs": third_refs, "bundle": reference, "_files": {},
            }

            def local_artifact(manifest, name, *, bucket, destination, profiles_by_account,
                               profiles_by_bucket=None):
                source = manifest["_files"][name]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                return {"name": name, "plaintext_sha256": restore.sha256_file(destination)}

            chain = [({}, first), ({}, second), ({}, third), ({}, fourth)]
            with mock.patch.object(restore, "materialize_artifact", side_effect=local_artifact):
                worktree, receipt = restore.restore_git(
                    chain, bucket="fixture", root=root / "restored", profiles_by_account={},
                )
            bare = root / "restored" / "repository.git"
            self.assertEqual(restore.selected_ref_map(bare), third_refs)
            self.assertEqual(receipt["source_head_sha"], second_head)
            self.assertEqual((worktree / "brain.txt").read_text(encoding="utf-8"), "two\n")
            subprocess.check_call(["git", "fsck", "--full"], cwd=bare, stdout=subprocess.DEVNULL)

    def test_working_index_and_untracked_state_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = self.make_repo(root)
            (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
            subprocess.check_call(["git", "add", "staged.txt"], cwd=repo)
            (repo / "brain.txt").write_text("dirty\n", encoding="utf-8")
            (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
            snapshot = backup.source_snapshot(repo, min_tracked=1)
            stage = root / "capture"
            backup.write_recovery_artifacts(repo, stage, snapshot)
            restored = root / "worktree"
            subprocess.check_call(["git", "clone", "--no-checkout", str(repo), str(restored)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git", "checkout", snapshot["head_sha"]], cwd=restored, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            manifest = {
                "status_sha256": snapshot["status_sha256"],
                "_files": {name: stage / name for name in (
                    "working-tree.patch", "index.patch", "untracked.tar.gz", "untracked-manifest.json",
                )},
            }

            def local_artifact(value, name, *, bucket, destination, profiles_by_account,
                               profiles_by_bucket=None):
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(value["_files"][name], destination)
                return {"name": name, "plaintext_sha256": restore.sha256_file(destination)}

            with mock.patch.object(restore, "materialize_artifact", side_effect=local_artifact):
                receipt = restore.restore_working_state(
                    manifest, bucket="fixture", root=root / "recovery-root",
                    worktree=restored, profiles_by_account={},
                )
            self.assertEqual(receipt["status_sha256"], snapshot["status_sha256"])
            self.assertEqual((restored / "brain.txt").read_text(encoding="utf-8"), "dirty\n")
            self.assertEqual((restored / "untracked.txt").read_text(encoding="utf-8"), "untracked\n")
            self.assertIn("staged.txt", git(restored, "diff", "--cached", "--name-only"))

    def test_chunk_reassembly_and_authenticated_decryption(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plaintext = root / "plain"
            ciphertext = root / "cipher"
            restored = root / "restored"
            plaintext.write_bytes((b"brain-restore" * 100_000) + b"tail")
            key = b"k" * 32
            aad = envelope.canonical_json_bytes({"artifact": "fixture"})
            metadata = envelope.encrypt_stream(plaintext, ciphertext, key, aad, nonce=b"n" * 12)
            raw = ciphertext.read_bytes()
            chunk_files = []
            chunks = []
            offset = 0
            for index, data in enumerate((raw[:500_000], raw[500_000:900_000], raw[900_000:])):
                path = root / f"chunk-{index}"
                path.write_bytes(data)
                chunk_files.append(path)
                chunks.append({
                    "index": index, "offset": offset, "bytes": len(data),
                    "replicas": [{"bucket": "fixture", "region": "us-east-1", "_path": path}],
                })
                offset += len(data)
            manifest = {"uploaded_artifacts": {"artifact.bin": {
                "storage": {"chunks": chunks, "ciphertext_bytes": len(raw), "ciphertext_sha256": hashlib.sha256(raw).hexdigest()},
                "encryption": metadata,
            }}}

            def local_download(descriptor, destination, profiles_by_bucket=None):
                shutil.copy2(descriptor["_path"], destination)
                return {"sha256": restore.sha256_file(destination)}

            def local_decrypt(source, destination, *, cwd, region, metadata, profiles_by_account):
                return envelope.decrypt_stream(source, destination, key, aad, metadata)

            with mock.patch.object(restore, "download_descriptor", side_effect=local_download), mock.patch.object(
                restore.envelope, "decrypt_artifact", side_effect=local_decrypt,
            ):
                receipt = restore.materialize_artifact(
                    manifest, "artifact.bin", bucket="fixture", destination=restored,
                    profiles_by_account={},
                )
            self.assertEqual(restored.read_bytes(), plaintext.read_bytes())
            self.assertEqual(receipt["plaintext_sha256"], hashlib.sha256(plaintext.read_bytes()).hexdigest())

    def test_untracked_archive_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "bad.tar.gz"
            payload = root / "payload"
            payload.write_text("escape", encoding="utf-8")
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(payload, arcname="../escape.txt")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"files": [{
                "path": "../escape.txt", "bytes": 6,
                "sha256": hashlib.sha256(b"escape").hexdigest(),
            }]}), encoding="utf-8")
            with self.assertRaises(restore.RestoreError):
                restore.extract_untracked(archive, manifest, root / "worktree")

    def test_profile_override_replaces_historical_hint(self):
        descriptor = {"bucket": "vault", "profile_hint": "old-machine-name"}
        self.assertEqual(restore.profile_for(descriptor), "old-machine-name")
        self.assertEqual(restore.profile_for(descriptor, {"vault": "recovery"}), "recovery")
        self.assertIsNone(restore.profile_for(descriptor, {"vault": ""}))

    def test_auto_rejects_divergent_valid_vault_tips(self):
        args = SimpleNamespace(
            vault="auto", primary_profile="", secondary_profile="recovery",
            expected_receipt_sha256=None,
        )
        first = [({"_verified_sha256": "a" * 64}, {"run_id": "one"})]
        second = [({"_verified_sha256": "b" * 64}, {"run_id": "two"})]
        with mock.patch.object(restore, "load_receipt_chain", side_effect=[first, second]):
            with self.assertRaisesRegex(restore.RestoreError, "disagree"):
                restore.collect_receipt_chains(args, {})

    def test_auto_retries_secondary_after_primary_artifact_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            destination = root / "restore"
            args = SimpleNamespace(
                vault="auto", primary_profile="", secondary_profile="recovery",
                expected_receipt_sha256=None, destination=destination,
            )
            receipt = {"_verified_sha256": "c" * 64}
            manifest = {"run_id": "run", "github_sanitized_sha": "d" * 40}
            chain = [(receipt, manifest)]
            valid = [
                ((backup.DEFAULT_BUCKET, backup.DEFAULT_REGION, None), chain),
                ((backup.DEFAULT_SECONDARY_BUCKET, backup.DEFAULT_SECONDARY_REGION, "recovery"), chain),
            ]

            def restore_git_side_effect(value, *, bucket, root, profiles_by_account, profiles_by_bucket):
                if bucket == backup.DEFAULT_BUCKET:
                    raise restore.RestoreError("primary artifact missing")
                worktree = root / "worktree"
                worktree.mkdir(parents=True)
                return worktree, {"source_head_sha": "e" * 40}

            with mock.patch.object(restore, "collect_receipt_chains", return_value=(valid, {})), mock.patch.object(
                restore, "restore_git", side_effect=restore_git_side_effect,
            ), mock.patch.object(restore, "restore_working_state", return_value={"status_sha256": "f" * 64}), mock.patch.object(
                restore, "write_signed_evidence", return_value=[{"run_id": "run"}],
            ), mock.patch.object(restore, "verify_existing_restore", return_value={"run_id": "run"}):
                result = restore.perform_restore(args)

            self.assertEqual(result["vault"]["bucket"], backup.DEFAULT_SECONDARY_BUCKET)
            self.assertIn(backup.DEFAULT_BUCKET, result["vault_restore_errors"])

    def test_existing_restore_rechecks_dual_signatures_refs_fsck_and_status(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = self.make_repo(root)
            restored = root / "restored"
            bare = restored / "repository.git"
            worktree = restored / "worktree"
            subprocess.check_call(["git", "clone", "--bare", str(source), str(bare)], stdout=subprocess.DEVNULL)
            head = git(source, "rev-parse", "HEAD")
            subprocess.check_call([
                "git", f"--git-dir={bare}", "worktree", "add", "--detach", str(worktree), head,
            ], stdout=subprocess.DEVNULL)
            snapshot = backup.source_snapshot(source, min_tracked=1)
            manifest = {
                "run_id": "signed-run", "source_head_sha": head,
                "source_refs": backup.selected_refs(source),
                "status_sha256": snapshot["status_sha256"],
            }
            manifest_raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
            payload = {
                "run_id": "signed-run", "source_head_sha": head,
                "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
                "previous_completion_anchor": None,
            }
            message_digest = hashlib.sha256(envelope.canonical_json_bytes(payload)).digest()
            signers = {}
            signatures = []
            for index in range(2):
                private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
                public_path = root / f"signer-{index}.pub"
                public_path.write_bytes(private.public_key().public_bytes(
                    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
                ))
                key_id = f"fixture-signer-{index}"
                signers[key_id] = public_path
                signature = private.sign(
                    message_digest,
                    padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
                    utils.Prehashed(hashes.SHA256()),
                )
                signatures.append({
                    "algorithm": "RSASSA_PSS_SHA_256", "kms_key_id": key_id,
                    "signature_b64": base64.b64encode(signature).decode("ascii"),
                })
            receipt = {
                "payload": payload,
                "authentication": {"required_signers": sorted(signers), "signatures": signatures},
            }
            receipt_raw = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
            evidence = restored / "evidence" / "0000-signed-run"
            evidence.mkdir(parents=True)
            (evidence / "completion-receipt.json").write_bytes(receipt_raw)
            (evidence / "manifest.json").write_bytes(manifest_raw)
            report = {
                "receipt_sha256": hashlib.sha256(receipt_raw).hexdigest(),
                "signed_evidence": [{
                    "receipt_path": "evidence/0000-signed-run/completion-receipt.json",
                    "receipt_sha256": hashlib.sha256(receipt_raw).hexdigest(),
                    "manifest_path": "evidence/0000-signed-run/manifest.json",
                    "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
                }],
            }
            backup.atomic_json(restored / "RESTORE_RECEIPT.json", report)
            fingerprints = {key: envelope.public_key_fingerprint(path) for key, path in signers.items()}
            with mock.patch.object(restore.backup_brain, "PINNED_SIGNERS", signers), mock.patch.object(
                restore.backup_brain, "PINNED_SIGNER_SHA256", fingerprints,
            ):
                verified = restore.verify_existing_restore(restored)
                self.assertEqual(verified["source_head_sha"], head)
                (worktree / "brain.txt").write_text("tampered\n", encoding="utf-8")
                with self.assertRaisesRegex(restore.RestoreError, "working state"):
                    restore.verify_existing_restore(restored)


if __name__ == "__main__":
    unittest.main()
