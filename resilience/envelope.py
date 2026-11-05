#!/usr/bin/env python3
"""Streaming client-side encryption for immutable brain-backup artifacts.

AWS KMS protects a one-time data key. S3 receives only AES-256-GCM
ciphertext; the plaintext data key exists only in this process while an
artifact is being encrypted or restored.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Mapping, Sequence

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, utils
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


CHUNK_BYTES = 4 * 1024 * 1024


class EnvelopeError(RuntimeError):
    pass


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def encrypt_stream(source: Path, destination: Path, key: bytes, aad: bytes, *, nonce: bytes | None = None) -> dict[str, object]:
    """Encrypt one file without loading it into memory."""

    if len(key) != 32:
        raise EnvelopeError("AES-256-GCM requires a 32-byte data key")
    nonce = nonce or os.urandom(12)
    if len(nonce) != 12:
        raise EnvelopeError("AES-GCM nonce must be 12 bytes")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + f".{os.getpid()}.tmp")
    plain_hash = hashlib.sha256()
    cipher_hash = hashlib.sha256()
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(aad)
    try:
        with source.open("rb") as incoming, temporary.open("wb") as outgoing:
            for chunk in iter(lambda: incoming.read(CHUNK_BYTES), b""):
                plain_hash.update(chunk)
                encrypted = encryptor.update(chunk)
                outgoing.write(encrypted)
                cipher_hash.update(encrypted)
            tail = encryptor.finalize()
            outgoing.write(tail)
            cipher_hash.update(tail)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "algorithm": "AES-256-GCM",
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "tag_b64": base64.b64encode(encryptor.tag).decode("ascii"),
        "aad_sha256": hashlib.sha256(aad).hexdigest(),
        "plaintext_bytes": source.stat().st_size,
        "plaintext_sha256": plain_hash.hexdigest(),
        "ciphertext_bytes": destination.stat().st_size,
        "ciphertext_sha256": cipher_hash.hexdigest(),
    }


def decrypt_stream(source: Path, destination: Path, key: bytes, aad: bytes, metadata: Mapping[str, object]) -> dict[str, object]:
    """Authenticate and decrypt one artifact, publishing it atomically."""

    if len(key) != 32:
        raise EnvelopeError("AES-256-GCM requires a 32-byte data key")
    nonce = base64.b64decode(str(metadata["nonce_b64"]), validate=True)
    tag = base64.b64decode(str(metadata["tag_b64"]), validate=True)
    if hashlib.sha256(aad).hexdigest() != metadata.get("aad_sha256"):
        raise EnvelopeError("artifact additional-data hash does not match the manifest")
    if _sha256_file(source) != metadata.get("ciphertext_sha256"):
        raise EnvelopeError("artifact ciphertext hash does not match the manifest")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + f".{os.getpid()}.tmp")
    plain_hash = hashlib.sha256()
    decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
    decryptor.authenticate_additional_data(aad)
    try:
        with source.open("rb") as incoming, temporary.open("wb") as outgoing:
            for chunk in iter(lambda: incoming.read(CHUNK_BYTES), b""):
                decrypted = decryptor.update(chunk)
                outgoing.write(decrypted)
                plain_hash.update(decrypted)
            tail = decryptor.finalize()
            outgoing.write(tail)
            plain_hash.update(tail)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if plain_hash.hexdigest() != metadata.get("plaintext_sha256"):
            raise EnvelopeError("artifact plaintext hash does not match the manifest")
        if temporary.stat().st_size != metadata.get("plaintext_bytes"):
            raise EnvelopeError("artifact plaintext size does not match the manifest")
        os.replace(temporary, destination)
    except (InvalidTag, Exception) as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, EnvelopeError):
            raise
        if isinstance(exc, InvalidTag):
            raise EnvelopeError("artifact authentication tag is invalid") from exc
        raise
    return {"bytes": destination.stat().st_size, "sha256": plain_hash.hexdigest()}


def _aws(argv: Sequence[str], *, cwd: Path, profile: str | None = None) -> dict[str, object]:
    command = ["aws", *argv]
    if profile:
        command.extend(["--profile", profile])
    result = subprocess.run(
        command, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "AWS_PAGER": "", "AWS_CLI_AUTO_PROMPT": "off"},
    )
    if result.returncode != 0:
        raise EnvelopeError(f"AWS KMS command failed ({result.returncode}): {result.stderr[-2000:]}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise EnvelopeError("AWS KMS returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise EnvelopeError("AWS KMS returned an unexpected response")
    return value


def resolve_key_arn(cwd: Path, key_id: str, region: str, profile: str | None = None) -> str:
    response = _aws([
        "kms", "describe-key", "--key-id", key_id, "--region", region, "--output", "json",
    ], cwd=cwd, profile=profile)
    metadata = response.get("KeyMetadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("Arn"), str):
        raise EnvelopeError("AWS KMS describe-key response is incomplete")
    return str(metadata["Arn"])


def generate_data_key(cwd: Path, key_id: str, region: str, context: Mapping[str, str],
                      profile: str | None = None) -> tuple[bytearray, str, str]:
    request = {"KeyId": key_id, "KeySpec": "AES_256", "EncryptionContext": dict(context)}
    response = _aws([
        "kms", "generate-data-key", "--cli-input-json", json.dumps(request, separators=(",", ":")),
        "--region", region, "--output", "json",
    ], cwd=cwd, profile=profile)
    try:
        plaintext = bytearray(base64.b64decode(str(response["Plaintext"]), validate=True))
        encrypted = str(response["CiphertextBlob"])
        immutable_key_id = str(response["KeyId"])
    except (KeyError, ValueError) as exc:
        raise EnvelopeError("AWS KMS data-key response is incomplete") from exc
    if len(plaintext) != 32:
        raise EnvelopeError("AWS KMS returned a non-256-bit data key")
    return plaintext, encrypted, immutable_key_id


def decrypt_data_key(cwd: Path, encrypted_key_b64: str, key_id: str, region: str,
                     context: Mapping[str, str], profile: str | None = None) -> bytearray:
    encrypted = base64.b64decode(encrypted_key_b64, validate=True)
    with tempfile.TemporaryDirectory(prefix="brain-kms-") as temp:
        blob = Path(temp) / "data-key.bin"
        blob.write_bytes(encrypted)
        response = _aws([
            "kms", "decrypt", "--key-id", key_id, "--ciphertext-blob", f"fileb://{blob}",
            "--encryption-context", json.dumps(dict(context), separators=(",", ":")),
            "--region", region, "--output", "json",
        ], cwd=cwd, profile=profile)
    try:
        plaintext = bytearray(base64.b64decode(str(response["Plaintext"]), validate=True))
    except (KeyError, ValueError) as exc:
        raise EnvelopeError("AWS KMS decrypt response is incomplete") from exc
    if len(plaintext) != 32:
        raise EnvelopeError("AWS KMS decrypted a non-256-bit data key")
    return plaintext


def _account_from_key_arn(key_arn: str) -> str:
    parts = key_arn.split(":")
    if len(parts) < 6 or parts[0] != "arn" or parts[2] != "kms":
        raise EnvelopeError(f"invalid KMS key ARN: {key_arn}")
    return parts[4]


def wrap_data_key(cwd: Path, plaintext_key: bytes, key_id: str, region: str,
                  context: Mapping[str, str], profile: str | None = None) -> dict[str, str]:
    immutable_key_id = resolve_key_arn(cwd, key_id, region, profile)
    with tempfile.TemporaryDirectory(prefix="brain-wrap-") as temp:
        plaintext = Path(temp) / "data-key.bin"
        plaintext.write_bytes(plaintext_key)
        response = _aws([
            "kms", "encrypt", "--key-id", immutable_key_id, "--plaintext", f"fileb://{plaintext}",
            "--encryption-context", json.dumps(dict(context), separators=(",", ":")),
            "--region", region, "--output", "json",
        ], cwd=cwd, profile=profile)
    encrypted = response.get("CiphertextBlob")
    generated_by = response.get("KeyId")
    if not isinstance(encrypted, str) or generated_by != immutable_key_id:
        raise EnvelopeError("AWS KMS wrapped the data key under an unexpected key")
    return {
        "kms_key_id": immutable_key_id,
        "aws_account_id": _account_from_key_arn(immutable_key_id),
        "region": region,
        "profile_hint": profile or "default",
        "encrypted_data_key_b64": encrypted,
    }


def wipe(value: bytearray) -> None:
    for index in range(len(value)):
        value[index] = 0


def generate_mac(cwd: Path, key_id: str, region: str, message: bytes) -> tuple[str, str]:
    """Authenticate a completion receipt with a non-exportable KMS HMAC key."""

    with tempfile.TemporaryDirectory(prefix="brain-mac-") as temp:
        payload = Path(temp) / "receipt.bin"
        payload.write_bytes(message)
        response = _aws([
            "kms", "generate-mac", "--key-id", key_id, "--message", f"fileb://{payload}",
            "--mac-algorithm", "HMAC_SHA_256", "--region", region, "--output", "json",
        ], cwd=cwd)
    mac = response.get("Mac")
    immutable_key_id = response.get("KeyId")
    if not isinstance(mac, str) or not isinstance(immutable_key_id, str):
        raise EnvelopeError("AWS KMS generate-mac response is incomplete")
    return mac, immutable_key_id


def verify_mac(cwd: Path, key_id: str, region: str, message: bytes, mac_b64: str) -> bool:
    """Verify a completion receipt without exposing the KMS key."""

    mac = base64.b64decode(mac_b64, validate=True)
    with tempfile.TemporaryDirectory(prefix="brain-mac-") as temp:
        payload = Path(temp) / "receipt.bin"
        signature = Path(temp) / "receipt.mac"
        payload.write_bytes(message)
        signature.write_bytes(mac)
        response = _aws([
            "kms", "verify-mac", "--key-id", key_id, "--message", f"fileb://{payload}",
            "--mac", f"fileb://{signature}", "--mac-algorithm", "HMAC_SHA_256",
            "--region", region, "--output", "json",
        ], cwd=cwd)
    return response.get("MacValid") is True


def sign_message(cwd: Path, key_id: str, region: str, message: bytes,
                 profile: str | None = None) -> dict[str, str]:
    """Sign a SHA-256 digest in KMS; verification needs only the pinned public key."""

    immutable_key_id = resolve_key_arn(cwd, key_id, region, profile)
    digest = hashlib.sha256(message).digest()
    with tempfile.TemporaryDirectory(prefix="brain-sign-") as temp:
        payload = Path(temp) / "receipt.sha256"
        payload.write_bytes(digest)
        response = _aws([
            "kms", "sign", "--key-id", immutable_key_id, "--message", f"fileb://{payload}",
            "--message-type", "DIGEST", "--signing-algorithm", "RSASSA_PSS_SHA_256",
            "--region", region, "--output", "json",
        ], cwd=cwd, profile=profile)
    signature = response.get("Signature")
    signed_by = response.get("KeyId")
    if not isinstance(signature, str) or signed_by != immutable_key_id:
        raise EnvelopeError("AWS KMS signed with an unexpected key")
    return {
        "algorithm": "RSASSA_PSS_SHA_256",
        "kms_key_id": immutable_key_id,
        "aws_account_id": _account_from_key_arn(immutable_key_id),
        "region": region,
        "signature_b64": signature,
    }


def verify_signature(public_key_path: Path, message: bytes, signature_b64: str) -> bool:
    signature = base64.b64decode(signature_b64, validate=True)
    public_key = serialization.load_pem_public_key(public_key_path.read_bytes())
    digest = hashlib.sha256(message).digest()
    try:
        public_key.verify(
            signature,
            digest,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
            utils.Prehashed(hashes.SHA256()),
        )
    except InvalidSignature:
        return False
    return True


def public_key_fingerprint(public_key_path: Path) -> str:
    public_key = serialization.load_pem_public_key(public_key_path.read_bytes())
    der = public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()


def encrypt_artifact(source: Path, destination: Path, *, cwd: Path, key_id: str, region: str,
                     run_id: str, source_head: str, artifact_name: str,
                     secondary_key_id: str | None = None, secondary_region: str | None = None,
                     secondary_profile: str | None = None) -> dict[str, object]:
    immutable_key_id = resolve_key_arn(cwd, key_id, region)
    secondary_arn: str | None = None
    if secondary_key_id:
        if not secondary_region:
            raise EnvelopeError("secondary KMS region is required")
        secondary_arn = resolve_key_arn(cwd, secondary_key_id, secondary_region, secondary_profile)
    context = {
        "purpose": "brain-resilience",
        "run_id": run_id,
        "source_head": source_head,
        "artifact": artifact_name,
    }
    aad_document = {
        "schema_version": 1,
        "key_recipients": sorted(item for item in (immutable_key_id, secondary_arn) if item),
        "encryption_context": context,
    }
    aad = canonical_json_bytes(aad_document)
    key, encrypted_key, generated_by = generate_data_key(cwd, immutable_key_id, region, context)
    if generated_by != immutable_key_id:
        wipe(key)
        raise EnvelopeError("AWS KMS generated the data key under an unexpected key")
    wrappings = [{
        "kms_key_id": immutable_key_id,
        "aws_account_id": _account_from_key_arn(immutable_key_id),
        "region": region,
        "profile_hint": "default",
        "encrypted_data_key_b64": encrypted_key,
    }]
    try:
        if secondary_arn:
            wrappings.append(wrap_data_key(
                cwd, bytes(key), secondary_arn, str(secondary_region), context, secondary_profile,
            ))
        metadata = encrypt_stream(source, destination, bytes(key), aad)
    finally:
        wipe(key)
    metadata.update({
        "kms_key_id": immutable_key_id,
        "encrypted_data_key_b64": encrypted_key,
        "key_wrappings": wrappings,
        "encryption_context": context,
        "aad": aad_document,
    })
    return metadata


def decrypt_artifact(source: Path, destination: Path, *, cwd: Path, region: str,
                     metadata: Mapping[str, object], profiles_by_account: Mapping[str, str] | None = None) -> dict[str, object]:
    context = metadata.get("encryption_context")
    aad_document = metadata.get("aad")
    if not isinstance(context, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in context.items()):
        raise EnvelopeError("artifact encryption context is invalid")
    if not isinstance(aad_document, dict):
        raise EnvelopeError("artifact additional data is invalid")
    wrappings = metadata.get("key_wrappings")
    if not isinstance(wrappings, list) or not wrappings:
        wrappings = [{
            "encrypted_data_key_b64": metadata["encrypted_data_key_b64"],
            "kms_key_id": metadata["kms_key_id"],
            "region": region,
            "aws_account_id": _account_from_key_arn(str(metadata["kms_key_id"])),
            "profile_hint": "default",
        }]
    errors: list[str] = []
    for wrapping in wrappings:
        if not isinstance(wrapping, dict):
            continue
        account = str(wrapping.get("aws_account_id", ""))
        hint = str(wrapping.get("profile_hint", "default"))
        profile = profiles_by_account.get(account) if profiles_by_account else (None if hint == "default" else hint)
        try:
            key = decrypt_data_key(
                cwd, str(wrapping["encrypted_data_key_b64"]), str(wrapping["kms_key_id"]),
                str(wrapping.get("region") or region), context, profile,
            )
            try:
                return decrypt_stream(source, destination, bytes(key), canonical_json_bytes(aad_document), metadata)
            finally:
                wipe(key)
        except (KeyError, EnvelopeError) as exc:
            errors.append(f"{account or 'unknown'}: {exc}")
    raise EnvelopeError("no KMS recipient could decrypt the artifact: " + "; ".join(errors))
