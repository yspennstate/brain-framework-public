#!/usr/bin/env python3
"""Clean-room verifier and exact restore path for the dual-vault brain backup.

The restore trusts only the two public keys pinned beside this file and exact
versioned object descriptors carried by their signed receipt chain.  It never
reads or mutates the source brain worktree.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Mapping, Sequence

try:
    import backup_brain
    import envelope
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import backup_brain
    import envelope


DEFAULT_DESTINATION_ROOT = Path(r"{{USERPROFILE}}\.brain-resilience\restore-drills")


class RestoreError(RuntimeError):
    pass


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def stamp() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def run(argv: Sequence[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(argv), cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "AWS_PAGER": "", "AWS_CLI_AUTO_PROMPT": "off"},
    )
    if check and result.returncode != 0:
        raise RestoreError(f"command failed ({result.returncode}): {' '.join(argv)}\n{result.stderr[-3000:]}")
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def profile_for(descriptor: Mapping[str, object],
                profiles_by_bucket: Mapping[str, str] | None = None) -> str | None:
    bucket = str(descriptor.get("bucket", ""))
    if profiles_by_bucket is not None and bucket in profiles_by_bucket:
        return profiles_by_bucket[bucket] or None
    hint = str(descriptor.get("profile_hint", "default"))
    return None if hint == "default" else hint


def choose_replica(replicas: object, bucket: str) -> dict[str, object]:
    if not isinstance(replicas, list):
        raise RestoreError("artifact has no replica list")
    matches = [item for item in replicas if isinstance(item, dict) and item.get("bucket") == bucket]
    if len(matches) != 1:
        raise RestoreError(f"artifact does not have exactly one replica in {bucket}")
    return matches[0]


def download_descriptor(descriptor: Mapping[str, object], destination: Path,
                        profiles_by_bucket: Mapping[str, str] | None = None) -> dict[str, object]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "aws", "s3api", "get-object", "--bucket", str(descriptor["bucket"]),
        "--key", str(descriptor["key"]), "--version-id", str(descriptor["version_id"]),
        "--checksum-mode", "ENABLED", "--region", str(descriptor["region"]),
    ]
    profile = profile_for(descriptor, profiles_by_bucket)
    if profile:
        command.extend(["--profile", profile])
    command.append(str(destination))
    run(command)
    actual_hash = sha256_file(destination)
    expected_hash = descriptor.get("sha256")
    expected_bytes = descriptor.get("bytes")
    if ((expected_hash is not None and actual_hash != expected_hash)
            or (expected_bytes is not None and destination.stat().st_size != expected_bytes)):
        destination.unlink(missing_ok=True)
        raise RestoreError(f"downloaded object does not match signed descriptor: {descriptor.get('key')}")
    return {"key": descriptor["key"], "version_id": descriptor["version_id"], "sha256": actual_hash}


def materialize_artifact(manifest: Mapping[str, object], name: str, *, bucket: str,
                         destination: Path, profiles_by_account: Mapping[str, str],
                         profiles_by_bucket: Mapping[str, str] | None = None) -> dict[str, object]:
    uploaded = manifest.get("uploaded_artifacts")
    if not isinstance(uploaded, dict) or not isinstance(uploaded.get(name), dict):
        raise RestoreError(f"signed manifest does not contain artifact: {name}")
    artifact = uploaded[name]
    assert isinstance(artifact, dict)
    storage = artifact.get("storage")
    encryption = artifact.get("encryption")
    if not isinstance(storage, dict) or not isinstance(encryption, dict):
        raise RestoreError(f"artifact metadata is malformed: {name}")
    chunks = storage.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise RestoreError(f"artifact has no ciphertext chunks: {name}")

    encrypted = destination.with_suffix(destination.suffix + ".enc")
    encrypted.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    chunk_receipts: list[dict[str, object]] = []
    try:
        with encrypted.open("wb") as outgoing:
            for expected_index, chunk in enumerate(chunks):
                if (not isinstance(chunk, dict) or chunk.get("index") != expected_index
                        or chunk.get("offset") != total):
                    raise RestoreError(f"artifact chunk order is invalid: {name}")
                replica = choose_replica(chunk.get("replicas"), bucket)
                with tempfile.TemporaryDirectory(prefix="brain-restore-chunk-") as temp:
                    part = Path(temp) / "chunk"
                    chunk_receipts.append(download_descriptor(replica, part, profiles_by_bucket))
                    with part.open("rb") as incoming:
                        for block in iter(lambda: incoming.read(4 * 1024 * 1024), b""):
                            outgoing.write(block)
                            digest.update(block)
                            total += len(block)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if total != storage.get("ciphertext_bytes") or digest.hexdigest() != storage.get("ciphertext_sha256"):
            raise RestoreError(f"reassembled ciphertext does not match manifest: {name}")
        selected_region = str(choose_replica(chunks[0].get("replicas"), bucket)["region"])
        try:
            plaintext = envelope.decrypt_artifact(
                encrypted, destination, cwd=Path.cwd(), region=selected_region,
                metadata=encryption, profiles_by_account=profiles_by_account,
            )
        except envelope.EnvelopeError as exc:
            raise RestoreError(str(exc)) from exc
    finally:
        encrypted.unlink(missing_ok=True)
    return {
        "name": name,
        "plaintext_sha256": plaintext["sha256"],
        "plaintext_bytes": plaintext["bytes"],
        "chunks": chunk_receipts,
    }


def descriptor_for_bucket(anchor: Mapping[str, object], bucket: str) -> dict[str, object]:
    return choose_replica(anchor.get("replicas"), bucket)


def load_receipt_chain(
    *, bucket: str, region: str, profile: str | None,
    profiles_by_bucket: Mapping[str, str] | None = None,
) -> list[tuple[dict[str, object], dict[str, object]]]:
    try:
        tip_receipt, tip_manifest = backup_brain.discover_receipt_tip(
            Path.cwd(), bucket, region, profile, profiles_by_bucket,
        )
    except backup_brain.BackupError as exc:
        raise RestoreError(str(exc)) from exc
    chain: list[tuple[dict[str, object], dict[str, object]]] = [(tip_receipt, tip_manifest)]
    seen = {str(tip_receipt.get("_verified_sha256"))}
    while True:
        payload = chain[-1][0].get("payload")
        if not isinstance(payload, dict):
            raise RestoreError("verified receipt lost its payload")
        previous = payload.get("previous_completion_anchor")
        if previous is None:
            break
        if not isinstance(previous, dict):
            raise RestoreError("receipt predecessor anchor is malformed")
        descriptor = descriptor_for_bucket(previous, bucket)
        try:
            receipt, manifest = backup_brain.verify_completion_receipt(
                Path.cwd(), str(descriptor["bucket"]), str(descriptor["region"]), descriptor,
                profiles_by_bucket,
            )
        except backup_brain.BackupError as exc:
            raise RestoreError(str(exc)) from exc
        receipt_hash = str(receipt.get("_verified_sha256"))
        if (receipt_hash != previous.get("sha256") or manifest.get("run_id") != previous.get("run_id")
                or receipt_hash in seen):
            raise RestoreError("receipt chain predecessor is missing, mismatched, or cyclic")
        seen.add(receipt_hash)
        receipt["_verified_descriptor"] = descriptor
        chain.append((receipt, manifest))
    chain.reverse()
    for index in range(1, len(chain)):
        anchor = chain[index][0].get("payload", {}).get("previous_completion_anchor")
        if not isinstance(anchor, dict) or anchor.get("sha256") != chain[index - 1][0].get("_verified_sha256"):
            raise RestoreError("receipt chain is not contiguous")
    return chain


def selected_ref_map(repo: Path) -> dict[str, str]:
    result = run([
        "git", "for-each-ref", "--format=%(refname) %(objectname)", "refs",
    ], cwd=repo)
    refs: dict[str, str] = {}
    for row in result.stdout.splitlines():
        parts = row.split()
        if len(parts) == 2:
            refs[parts[0]] = parts[1]
    return dict(sorted(refs.items()))


def restore_git(chain: list[tuple[dict[str, object], dict[str, object]]], *, bucket: str,
                root: Path, profiles_by_account: Mapping[str, str],
                profiles_by_bucket: Mapping[str, str] | None = None) -> tuple[Path, dict[str, object]]:
    manifests = [manifest for _, manifest in chain]
    full_indexes = [index for index, manifest in enumerate(manifests)
                    if isinstance(manifest.get("bundle"), dict) and manifest["bundle"].get("type") == "full"]
    if not full_indexes:
        raise RestoreError("signed receipt chain contains no full Git bundle")
    start = full_indexes[-1]
    bare = root / "repository.git"
    run(["git", "init", "--bare", str(bare)])
    bundle_receipts: list[dict[str, object]] = []
    for manifest in manifests[start:]:
        bundle = manifest.get("bundle")
        if not isinstance(bundle, dict):
            raise RestoreError("manifest has no bundle descriptor")
        bundle_type = bundle.get("type")
        if bundle_type not in {"full", "incremental", "reference", "ref-update"}:
            raise RestoreError(f"unknown bundle type: {bundle_type}")
        if bundle_type in {"reference", "ref-update"}:
            continue
        filename = str(bundle.get("file", ""))
        if not filename:
            raise RestoreError("bundle filename is missing")
        bundle_path = root / "bundles" / str(manifest["run_id"]) / filename
        bundle_receipts.append(materialize_artifact(
            manifest, filename, bucket=bucket, destination=bundle_path,
            profiles_by_account=profiles_by_account, profiles_by_bucket=profiles_by_bucket,
        ))
        run(["git", "bundle", "verify", str(bundle_path)], cwd=bare)
        run(["git", "fetch", "--force", str(bundle_path), "+refs/*:refs/*"], cwd=bare)

    target = manifests[-1]
    target_refs = target.get("source_refs")
    if not isinstance(target_refs, dict) or not target_refs:
        raise RestoreError("target manifest has no selected Git ref map")
    current_refs = selected_ref_map(bare)
    for ref in current_refs:
        if ref not in target_refs:
            run(["git", "update-ref", "-d", ref], cwd=bare)
    for ref, sha in sorted(target_refs.items()):
        run(["git", "cat-file", "-e", f"{sha}^{{object}}"], cwd=bare)
        run(["git", "update-ref", ref, str(sha)], cwd=bare)
    if selected_ref_map(bare) != dict(sorted((str(k), str(v)) for k, v in target_refs.items())):
        raise RestoreError("restored Git refs do not match the signed target map")
    source_head = str(target.get("source_head_sha", ""))
    run(["git", "cat-file", "-e", f"{source_head}^{{commit}}"], cwd=bare)
    if "refs/heads/main" in target_refs:
        run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=bare)
    fsck = run(["git", "fsck", "--full"], cwd=bare)
    worktree = root / "worktree"
    run(["git", f"--git-dir={bare}", "worktree", "add", "--detach", str(worktree), source_head])
    return worktree, {
        "source_head_sha": source_head,
        "source_refs_sha256": hashlib.sha256(envelope.canonical_json_bytes(target_refs)).hexdigest(),
        "source_ref_count": len(target_refs),
        "bundle_artifacts": bundle_receipts,
        "git_fsck_tail": (fsck.stdout + fsck.stderr)[-2000:],
    }


def _safe_member_path(root: Path, name: str) -> Path:
    posix = PurePosixPath(name)
    if posix.is_absolute() or not posix.parts or any(part in {"", ".", ".."} for part in posix.parts):
        raise RestoreError(f"unsafe untracked archive path: {name}")
    target = (root / Path(*posix.parts)).resolve()
    if not target.is_relative_to(root.resolve()):
        raise RestoreError(f"untracked archive escapes restore root: {name}")
    return target


def extract_untracked(archive: Path, manifest_path: Path, worktree: Path) -> dict[str, object]:
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = value.get("files") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise RestoreError("untracked manifest is malformed")
    expected = {str(row["path"]): row for row in rows if isinstance(row, dict)}
    extracted: set[str] = set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile() or member.name not in expected or member.issym() or member.islnk():
                raise RestoreError(f"unexpected untracked archive member: {member.name}")
            target = _safe_member_path(worktree, member.name)
            if target.exists():
                raise RestoreError(f"untracked archive would overwrite an existing path: {member.name}")
            source = tar.extractfile(member)
            if source is None:
                raise RestoreError(f"could not read untracked archive member: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            size = 0
            with source, target.open("wb") as output:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    output.write(block)
                    digest.update(block)
                    size += len(block)
            row = expected[member.name]
            if size != row.get("bytes") or digest.hexdigest() != row.get("sha256"):
                target.unlink(missing_ok=True)
                raise RestoreError(f"untracked file hash mismatch: {member.name}")
            extracted.add(member.name)
    if extracted != set(expected):
        raise RestoreError("untracked archive and manifest have different path sets")
    return {"files": len(extracted), "manifest_sha256": sha256_file(manifest_path)}


def restore_working_state(manifest: Mapping[str, object], *, bucket: str, root: Path,
                          worktree: Path, profiles_by_account: Mapping[str, str],
                          profiles_by_bucket: Mapping[str, str] | None = None) -> dict[str, object]:
    names = ("working-tree.patch", "index.patch", "untracked.tar.gz", "untracked-manifest.json")
    receipts: dict[str, object] = {}
    paths: dict[str, Path] = {}
    for name in names:
        path = root / "recovery" / name
        receipts[name] = materialize_artifact(
            manifest, name, bucket=bucket, destination=path,
            profiles_by_account=profiles_by_account, profiles_by_bucket=profiles_by_bucket,
        )
        paths[name] = path
    if paths["working-tree.patch"].stat().st_size:
        run(["git", "apply", "--check", str(paths["working-tree.patch"])], cwd=worktree)
        run(["git", "apply", str(paths["working-tree.patch"])], cwd=worktree)
    if paths["index.patch"].stat().st_size:
        run(["git", "apply", "--cached", "--check", str(paths["index.patch"])], cwd=worktree)
        run(["git", "apply", "--cached", str(paths["index.patch"])], cwd=worktree)
    untracked = extract_untracked(paths["untracked.tar.gz"], paths["untracked-manifest.json"], worktree)
    status = subprocess.run(
        ["git", "status", "--porcelain=v2", "-z", "--untracked-files=all"], cwd=worktree,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if status.returncode != 0:
        raise RestoreError(status.stderr.decode("utf-8", "replace")[-2000:])
    status_sha256 = hashlib.sha256(status.stdout).hexdigest()
    if status_sha256 != manifest.get("status_sha256"):
        raise RestoreError("restored working/index/untracked state does not match the signed status hash")
    return {"artifacts": receipts, "untracked": untracked, "status_sha256": status_sha256}


def vault_candidates(args: argparse.Namespace) -> list[tuple[str, str, str | None]]:
    vaults: list[tuple[str, str, str | None]] = []
    if args.vault in {"auto", "primary"}:
        vaults.append((backup_brain.DEFAULT_BUCKET, backup_brain.DEFAULT_REGION, args.primary_profile or None))
    if args.vault in {"auto", "secondary"}:
        vaults.append((
            backup_brain.DEFAULT_SECONDARY_BUCKET, backup_brain.DEFAULT_SECONDARY_REGION,
            args.secondary_profile or None,
        ))
    return vaults


def collect_receipt_chains(
    args: argparse.Namespace, profiles_by_bucket: Mapping[str, str],
) -> tuple[list[tuple[tuple[str, str, str | None], list[tuple[dict[str, object], dict[str, object]]]]], dict[str, str]]:
    valid = []
    errors: dict[str, str] = {}
    for bucket, region, profile in vault_candidates(args):
        try:
            chain = load_receipt_chain(
                bucket=bucket, region=region, profile=profile,
                profiles_by_bucket=profiles_by_bucket,
            )
            valid.append(((bucket, region, profile), chain))
        except RestoreError as exc:
            errors[bucket] = str(exc)
    if not valid:
        detail = "; ".join(f"{bucket}: {message}" for bucket, message in errors.items())
        raise RestoreError("no vault produced a valid receipt chain: " + detail)
    tip_hashes = {str(chain[-1][0].get("_verified_sha256")) for _, chain in valid}
    if len(tip_hashes) != 1:
        raise RestoreError("valid vaults disagree on the signed receipt tip; select a vault explicitly after investigation")
    tip_hash = next(iter(tip_hashes))
    expected = (args.expected_receipt_sha256 or "").lower()
    if expected and tip_hash.lower() != expected:
        raise RestoreError("signed receipt tip does not match --expected-receipt-sha256")
    return valid, errors


def write_signed_evidence(
    chain: list[tuple[dict[str, object], dict[str, object]]], *, bucket: str, root: Path,
    profiles_by_bucket: Mapping[str, str],
) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for index, (receipt, manifest) in enumerate(chain):
        run_id = str(manifest.get("run_id", "unknown"))
        directory = root / "evidence" / f"{index:04d}-{run_id}"
        receipt_path = directory / "completion-receipt.json"
        manifest_path = directory / "manifest.json"
        receipt_descriptor = receipt.get("_verified_descriptor")
        payload = receipt.get("payload")
        if not isinstance(receipt_descriptor, dict) or not isinstance(payload, dict):
            raise RestoreError("verified receipt lacks an exact version descriptor")
        receipt_descriptor = dict(receipt_descriptor)
        receipt_descriptor["sha256"] = receipt.get("_verified_sha256")
        receipt_download = download_descriptor(receipt_descriptor, receipt_path, profiles_by_bucket)
        manifest_descriptor = choose_replica(payload.get("manifest_replicas"), bucket)
        manifest_download = download_descriptor(manifest_descriptor, manifest_path, profiles_by_bucket)
        try:
            downloaded_receipt = json.loads(receipt_path.read_bytes())
            downloaded_manifest = json.loads(manifest_path.read_bytes())
        except json.JSONDecodeError as exc:
            raise RestoreError("downloaded signed evidence is not valid JSON") from exc
        receipt_value = {key: value for key, value in receipt.items() if not key.startswith("_verified_")}
        if downloaded_receipt != receipt_value or downloaded_manifest != manifest:
            raise RestoreError("signed evidence changed after receipt-chain verification")
        evidence.append({
            "run_id": run_id,
            "receipt_path": receipt_path.relative_to(root).as_posix(),
            "receipt_sha256": receipt_download["sha256"],
            "manifest_path": manifest_path.relative_to(root).as_posix(),
            "manifest_sha256": manifest_download["sha256"],
        })
    return evidence


def verify_evidence_receipt(receipt: Mapping[str, object], manifest_raw: bytes) -> dict[str, object]:
    payload = receipt.get("payload")
    authentication = receipt.get("authentication")
    if not isinstance(payload, dict) or not isinstance(authentication, dict):
        raise RestoreError("embedded completion receipt is malformed")
    signatures = authentication.get("signatures")
    if not isinstance(signatures, list):
        raise RestoreError("embedded completion receipt has no signatures")
    message = envelope.canonical_json_bytes(payload)
    seen: set[str] = set()
    for signature in signatures:
        if not isinstance(signature, dict):
            raise RestoreError("embedded completion signature is malformed")
        key_id = str(signature.get("kms_key_id", ""))
        public_key = backup_brain.PINNED_SIGNERS.get(key_id)
        if public_key is None or signature.get("algorithm") != "RSASSA_PSS_SHA_256":
            raise RestoreError(f"embedded completion receipt used an unpinned signer: {key_id}")
        if envelope.public_key_fingerprint(public_key) != backup_brain.PINNED_SIGNER_SHA256[key_id]:
            raise RestoreError(f"pinned restore public key was replaced: {key_id}")
        try:
            valid = envelope.verify_signature(public_key, message, str(signature["signature_b64"]))
        except (KeyError, ValueError, envelope.EnvelopeError) as exc:
            raise RestoreError(f"embedded completion signature failed: {exc}") from exc
        if not valid:
            raise RestoreError(f"embedded completion signature is invalid: {key_id}")
        seen.add(key_id)
    if seen != set(backup_brain.PINNED_SIGNERS):
        raise RestoreError("embedded receipt does not contain both pinned account signatures")
    manifest_hash = hashlib.sha256(manifest_raw).hexdigest()
    if manifest_hash != payload.get("manifest_sha256"):
        raise RestoreError("embedded manifest hash does not match the signed receipt")
    try:
        manifest = json.loads(manifest_raw)
    except json.JSONDecodeError as exc:
        raise RestoreError("embedded manifest is invalid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("run_id") != payload.get("run_id"):
        raise RestoreError("embedded receipt and manifest run IDs disagree")
    if manifest.get("source_head_sha") != payload.get("source_head_sha"):
        raise RestoreError("embedded receipt and manifest source SHAs disagree")
    return manifest


def verify_existing_restore(root: Path) -> dict[str, object]:
    report_path = root / "RESTORE_RECEIPT.json"
    try:
        report = json.loads(report_path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise RestoreError(f"restore receipt cannot be read: {report_path}") from exc
    rows = report.get("signed_evidence") if isinstance(report, dict) else None
    if not isinstance(rows, list) or not rows:
        raise RestoreError("restore receipt has no signed evidence chain")
    chain: list[tuple[str, dict[str, object]]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise RestoreError("restore evidence index is malformed")
        receipt_path = _safe_member_path(root, str(row.get("receipt_path", "")))
        manifest_path = _safe_member_path(root, str(row.get("manifest_path", "")))
        receipt_raw = receipt_path.read_bytes()
        manifest_raw = manifest_path.read_bytes()
        if hashlib.sha256(receipt_raw).hexdigest() != row.get("receipt_sha256"):
            raise RestoreError("embedded receipt file hash does not match restore evidence index")
        if hashlib.sha256(manifest_raw).hexdigest() != row.get("manifest_sha256"):
            raise RestoreError("embedded manifest file hash does not match restore evidence index")
        try:
            receipt = json.loads(receipt_raw)
        except json.JSONDecodeError as exc:
            raise RestoreError("embedded receipt is invalid JSON") from exc
        if not isinstance(receipt, dict):
            raise RestoreError("embedded receipt has an unexpected shape")
        manifest = verify_evidence_receipt(receipt, manifest_raw)
        receipt_hash = hashlib.sha256(receipt_raw).hexdigest()
        previous = receipt.get("payload", {}).get("previous_completion_anchor")
        if not chain:
            if previous is not None:
                raise RestoreError("embedded evidence chain does not start at its signed root")
        elif (not isinstance(previous, dict) or previous.get("sha256") != chain[-1][0]
              or previous.get("run_id") != chain[-1][1].get("run_id")):
            raise RestoreError("embedded evidence receipt chain is not contiguous")
        chain.append((receipt_hash, manifest))

    target = chain[-1][1]
    bare = root / "repository.git"
    worktree = root / "worktree"
    target_refs = target.get("source_refs")
    if not isinstance(target_refs, dict) or selected_ref_map(bare) != dict(sorted(target_refs.items())):
        raise RestoreError("existing restore Git refs do not match signed evidence")
    source_head = str(target.get("source_head_sha", ""))
    run(["git", "cat-file", "-e", f"{source_head}^{{commit}}"], cwd=bare)
    run(["git", "fsck", "--full"], cwd=bare)
    status = subprocess.run(
        ["git", "status", "--porcelain=v2", "-z", "--untracked-files=all"], cwd=worktree,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if status.returncode != 0 or hashlib.sha256(status.stdout).hexdigest() != target.get("status_sha256"):
        raise RestoreError("existing restore working state does not match signed evidence")
    if chain[-1][0] != report.get("receipt_sha256"):
        raise RestoreError("restore receipt points at a different signed tip")
    return {
        "verified_at_utc": utc_now().isoformat(), "run_id": target.get("run_id"),
        "receipt_sha256": chain[-1][0], "receipt_chain_length": len(chain),
        "source_head_sha": source_head, "status_sha256": target.get("status_sha256"),
    }


def perform_restore(args: argparse.Namespace) -> dict[str, object]:
    destination = args.destination.resolve()
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise RestoreError(f"restore destination is not a normal directory: {destination}")
    if destination.exists() and any(destination.iterdir()):
        raise RestoreError(f"restore destination is not empty: {destination}")
    profiles_by_bucket = {
        backup_brain.DEFAULT_BUCKET: args.primary_profile,
        backup_brain.DEFAULT_SECONDARY_BUCKET: args.secondary_profile,
    }
    profiles_by_account = {
        "<aws-account-primary>": args.primary_profile,
        "<aws-account-secondary>": args.secondary_profile,
    }
    valid, chain_errors = collect_receipt_chains(args, profiles_by_bucket)
    attempt_errors: dict[str, str] = {}
    for (bucket, region, profile), chain in valid:
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        try:
            worktree, git_receipt = restore_git(
                chain, bucket=bucket, root=destination,
                profiles_by_account=profiles_by_account, profiles_by_bucket=profiles_by_bucket,
            )
            target_receipt, target_manifest = chain[-1]
            working_receipt = restore_working_state(
                target_manifest, bucket=bucket, root=destination, worktree=worktree,
                profiles_by_account=profiles_by_account, profiles_by_bucket=profiles_by_bucket,
            )
            evidence = write_signed_evidence(
                chain, bucket=bucket, root=destination, profiles_by_bucket=profiles_by_bucket,
            )
            result = {
                "schema_version": 2,
                "verified_at_utc": utc_now().isoformat(),
                "vault": {"bucket": bucket, "region": region, "profile": profile or "default"},
                "vault_chain_errors": chain_errors,
                "vault_restore_errors": attempt_errors,
                "run_id": target_manifest.get("run_id"),
                "receipt_sha256": target_receipt.get("_verified_sha256"),
                "receipt_chain_length": len(chain),
                "github_sanitized_sha": target_manifest.get("github_sanitized_sha"),
                "git": git_receipt,
                "working_state": working_receipt,
                "signed_evidence": evidence,
                "worktree": str(worktree),
            }
            backup_brain.atomic_json(destination / "RESTORE_RECEIPT.json", result)
            verify_existing_restore(destination)
            return result
        except (RestoreError, backup_brain.BackupError) as exc:
            attempt_errors[bucket] = str(exc)
            if args.vault != "auto":
                raise
    if destination.exists():
        shutil.rmtree(destination)
    detail = "; ".join(f"{bucket}: {message}" for bucket, message in attempt_errors.items())
    raise RestoreError("every valid vault failed during artifact restore: " + detail)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--vault", choices=("auto", "primary", "secondary"), default="auto")
    value.add_argument("--primary-profile", default="")
    value.add_argument("--secondary-profile", default=backup_brain.DEFAULT_SECONDARY_PROFILE)
    value.add_argument("--expected-receipt-sha256")
    value.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION_ROOT / stamp())
    value.add_argument("--verify-existing", type=Path)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = verify_existing_restore(args.verify_existing.resolve()) if args.verify_existing else perform_restore(args)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (RestoreError, backup_brain.BackupError) as exc:
        print(f"restore ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
