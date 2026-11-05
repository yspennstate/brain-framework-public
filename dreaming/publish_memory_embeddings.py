#!/usr/bin/env python3
"""Publish an immutable embedding generation through one atomic authority pointer.

The large NPZ is never replaced in place. It is copied to a content-addressed sibling, validated
against a strict stable corpus snapshot, and preflighted through the real consumer. Only then is the
tiny .embedding_current.json pointer atomically replaced. A crash or Windows sharing violation
therefore leaves either the old valid pointer or the new valid pointer; byte rollback is unnecessary.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

import build_memory_embeddings as builder
import embedding_artifact as artifact
import embedding_coverage as coverage


PUBLISHED_SCHEMA = artifact.RUNTIME_SCHEMA_VERSION


def generation_path(canonical, digest):
    canonical = os.path.abspath(canonical)
    stem, ext = os.path.splitext(os.path.basename(canonical))
    return os.path.join(os.path.dirname(canonical), "%s.%s%s" % (stem, digest, ext))


def _authority_snapshot(report, committed_at=None):
    if (not report["readable"] or not report["corpus_readable"] or report["problems"]
            or not report["sidecar_sha256"] or not report["sidecar_size"]):
        raise ValueError("unsafe report cannot become embedding authority")
    committed_at = committed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schema": artifact.AUTHORITY_SCHEMA,
        "committed_at": committed_at,
        "sidecar": report["sidecar"],
        "sidecar_sha256": report["sidecar_sha256"],
        "sidecar_size": report["sidecar_size"],
        "model": report["model"],
        "built_at": report["built_at"],
    }


def _coherent_report(mem_dir, sidecar, expected_model, expected_sha, expected_size,
                     require_complete=True):
    """Two strict reads must describe the same safe corpus and exact immutable bytes."""
    first = coverage.coverage_report(mem_dir, sidecar, expected_model)
    second = coverage.coverage_report(mem_dir, sidecar, expected_model)
    stable_keys = (
        "sidecar_sha256", "sidecar_size", "corpus_sha256", "corpus_stat_sha256",
        "corpus_stat_files", "eligible", "embedded", "model", "built_at",
    )
    if any(first.get(k) != second.get(k) for k in stable_keys):
        raise OSError("corpus or staged generation changed during publication audit")
    if (not second["readable"] or not second["corpus_readable"] or second["problems"]
            or second["sidecar_sha256"] != expected_sha or second["sidecar_size"] != expected_size
            or (require_complete and not second["complete"])):
        raise ValueError("sidecar is not an exact safe current-corpus generation")
    return second


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _stamp_activation(staged_blob, report):
    """Bind publication-host row stats and safety decisions inside immutable NPZ bytes."""
    import io
    import numpy as np

    required = {"slugs", "vecs", "model", "text_sha256", "built_at", "schema_version"}
    with np.load(io.BytesIO(staged_blob), allow_pickle=False) as z:
        if set(z.files) - (required | {"source_size", "source_mtime_ns", "row_safe"}):
            raise ValueError("staged sidecar contains unrecognized arrays")
        if required - set(z.files):
            raise ValueError("staged sidecar is missing core arrays")
        values = {name: np.asarray(z[name]).copy() for name in required if name != "schema_version"}
    slugs = [str(slug) for slug in values["slugs"]]
    if len(slugs) != report["embedded"]:
        raise ValueError("publication report and staged rows do not align")
    unsafe = set(report["stale"] + report["extra"] + report["duplicates"])
    sizes, mtimes, safe = [], [], []
    for slug in slugs:
        metadata = report["corpus_manifest"].get(slug, [-1, -1])
        sizes.append(int(metadata[0]))
        mtimes.append(int(metadata[1]))
        safe.append(slug not in unsafe)
    out = io.BytesIO()
    # Runtime loads on every prompt; a ~1 MB larger uncompressed bundle saves roughly 15 ms of
    # Windows hook latency at the current 1,800-row scale. Staging may remain compressed in transit.
    np.savez(
        out,
        slugs=values["slugs"],
        vecs=values["vecs"],
        model=values["model"],
        text_sha256=values["text_sha256"],
        built_at=values["built_at"],
        schema_version=np.asarray(PUBLISHED_SCHEMA, dtype=np.int64),
        source_size=np.asarray(sizes, dtype=np.int64),
        source_mtime_ns=np.asarray(mtimes, dtype=np.int64),
        row_safe=np.asarray(safe, dtype=np.bool_),
    )
    return out.getvalue(), len(safe) - sum(not value for value in safe)


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _commit_authority(path, payload):
    """Commit the pointer and resolve an exception-after-replace without a false failure."""
    try:
        artifact.atomic_replace_bytes(path, payload)
        return True
    except BaseException:
        try:
            current = artifact.read_stable_bytes(path)[0]
        except BaseException:
            raise
        if current != payload:
            raise
        # The atomic replace linearized even though its tail was interrupted.
        return False


def _authority_matches(path, payload):
    try:
        return artifact.read_stable_bytes(path)[0] == payload
    except BaseException:
        return False


def _install_generation(production, blob):
    digest = hashlib.sha256(blob).hexdigest()
    size = len(blob)
    generation = generation_path(production, digest)
    if os.path.exists(generation):
        existing, existing_sha, existing_size, _ = artifact.read_stable_bytes(generation)
        if existing_sha != digest or existing_size != size or existing != blob:
            raise RuntimeError("content-addressed generation path contains different bytes")
    else:
        artifact.atomic_replace_bytes(generation, blob)
    return generation, digest, size


def _require_disjoint(named_paths):
    seen = {}
    for role, path in named_paths.items():
        key = os.path.normcase(os.path.realpath(os.path.abspath(path)))
        if key in seen:
            raise ValueError("%s path aliases %s" % (role, seen[key]))
        for other_role, other_path in seen.values():
            if os.path.exists(path) and os.path.exists(other_path):
                try:
                    if os.path.samefile(path, other_path):
                        raise ValueError("%s path aliases %s" % (role, other_role))
                except OSError:
                    pass
        seen[key] = (role, path)


def publish(staged, production=builder.OUT, authority_path=coverage.DEFAULT_AUTHORITY,
            status_path=coverage.DEFAULT_STATUS, mem_dir=builder.MEM_DEFAULT,
            expected_model=builder.DEFAULT_MODEL, lock_path=None, memory_lock_path=None,
            lock_timeout=30.0, verifier=artifact.load_verified_embeddings, fault=None):
    """Validate, preflight, and commit one content-addressed generation.

    The private fault seam is restricted to isolated regressions. Every injected failure happens
    before the authority commit, so the previously active generation remains byte-identical.
    """
    staged = os.path.abspath(staged)
    production = os.path.abspath(production)
    authority_path = os.path.abspath(authority_path)
    status_path = os.path.abspath(status_path)
    lock_path = os.path.abspath(lock_path or (production + ".publish.lock"))
    memory_lock_path = os.path.abspath(
        memory_lock_path or os.path.join(os.path.dirname(authority_path), "memory_write.lock")
    )
    _require_disjoint({
        "staged": staged,
        "canonical production": production,
        "authority": authority_path,
        "observer": status_path,
        "publication lock": lock_path,
        "memory lock": memory_lock_path,
    })
    if not os.path.isfile(staged):
        raise FileNotFoundError(staged)

    with artifact.file_lock(lock_path, lock_timeout):
        with artifact.file_lock(memory_lock_path, lock_timeout):
            staged_blob, staged_sha, staged_size, _ = artifact.read_stable_bytes(staged)
            staged_report = _coherent_report(
                mem_dir, staged, expected_model, staged_sha, staged_size
            )
            generation_blob, expected_active = _stamp_activation(staged_blob, staged_report)
            if expected_active != staged_report["embedded"]:
                raise ValueError("complete publication unexpectedly produced unsafe rows")
            pending_sha = hashlib.sha256(generation_blob).hexdigest()
            pending_generation = generation_path(production, pending_sha)
            _require_disjoint({
                "generation": pending_generation,
                "authority": authority_path,
                "observer": status_path,
                "publication lock": lock_path,
                "memory lock": memory_lock_path,
            })
            generation, generation_sha, generation_size = _install_generation(
                production, generation_blob
            )
            _require_disjoint({
                "generation": generation,
                "authority": authority_path,
                "observer": status_path,
                "publication lock": lock_path,
                "memory lock": memory_lock_path,
            })
            report = _coherent_report(
                mem_dir, generation, expected_model, generation_sha, generation_size
            )
            if report["schema_version"] != PUBLISHED_SCHEMA:
                raise ValueError("publisher did not produce a schema-v3 generation")
            authority = _authority_snapshot(report)
            payload = _json_bytes(authority)
            outcome = {
                "status": "published",
                "sidecar": generation,
                "authority_path": authority_path,
                "observer_path": status_path,
                "sha256": generation_sha,
                "size": generation_size,
                "staged_sha256": staged_sha,
                "embedded": report["embedded"],
                "eligible": report["eligible"],
                "model": report["model"],
                "built_at": report["built_at"],
                "observer_updated": False,
                "commit_call_returned": False,
            }
            preflight = None
            try:
                fd, preflight = tempfile.mkstemp(
                    prefix=".embedding_current.preflight.", suffix=".json",
                    dir=os.path.dirname(authority_path),
                )
                os.close(fd)
                artifact.atomic_replace_bytes(preflight, payload)
                if fault == "verify":
                    raise RuntimeError("injected consumer verification failure")
                preflight_loaded = verifier(production, preflight, mem_dir, expected_model)
                if preflight_loaded is None or len(preflight_loaded[0]) != report["embedded"]:
                    raise RuntimeError("real semantic consumer rejected the candidate authority")
                if fault == "status":
                    raise RuntimeError("injected authority publication failure")
                if fault == "interrupt":
                    raise KeyboardInterrupt("injected pre-commit interruption")
                outcome["commit_call_returned"] = _commit_authority(authority_path, payload)
                if preflight:
                    _remove(preflight)
                    preflight = None
                # Pointer replacement is the publication linearization point. The identical
                # preflight pointer was accepted by the real consumer under both locks.
                coverage._write_status_snapshot(report, status_path)
                outcome["observer_updated"] = True
                return outcome
            except BaseException:
                if not _authority_matches(authority_path, payload):
                    if preflight:
                        _remove(preflight)
                    raise
                # Any exception after the linearization point is a committed outcome. Cleanup and
                # the wake observer are best-effort and can be retried independently.
                if preflight:
                    try:
                        _remove(preflight)
                    except BaseException:
                        pass
                outcome["observer_updated"] = False
                return outcome


def adopt_current(production=builder.OUT, authority_path=coverage.DEFAULT_AUTHORITY,
                  status_path=coverage.DEFAULT_STATUS, mem_dir=builder.MEM_DEFAULT,
                  expected_model=builder.DEFAULT_MODEL, lock_path=None, memory_lock_path=None,
                  lock_timeout=30.0, verifier=artifact.load_verified_embeddings):
    """One-time migration of a safe legacy fixed-name generation into pointer authority.

    Incomplete is allowed only here: stale/extra rows are explicitly masked and missing rows have no
    vector. The operation refuses once any authority exists, so it cannot become a bypass for normal
    complete-generation publication.
    """
    production = os.path.abspath(production)
    authority_path = os.path.abspath(authority_path)
    status_path = os.path.abspath(status_path)
    lock_path = os.path.abspath(lock_path or (production + ".publish.lock"))
    memory_lock_path = os.path.abspath(
        memory_lock_path or os.path.join(os.path.dirname(authority_path), "memory_write.lock")
    )
    _require_disjoint({
        "canonical production": production,
        "authority": authority_path,
        "observer": status_path,
        "publication lock": lock_path,
        "memory lock": memory_lock_path,
    })
    with artifact.file_lock(lock_path, lock_timeout):
        with artifact.file_lock(memory_lock_path, lock_timeout):
            if os.path.exists(authority_path):
                raise FileExistsError("embedding authority already exists; one-time adoption refused")
            source_blob, source_digest, source_size, _ = artifact.read_stable_bytes(production)
            source_report = _coherent_report(
                mem_dir, production, expected_model, source_digest, source_size,
                require_complete=False
            )
            generation_blob, expected_active = _stamp_activation(source_blob, source_report)
            pending_sha = hashlib.sha256(generation_blob).hexdigest()
            pending_generation = generation_path(production, pending_sha)
            _require_disjoint({
                "generation": pending_generation,
                "authority": authority_path,
                "observer": status_path,
                "publication lock": lock_path,
                "memory lock": memory_lock_path,
            })
            generation, digest, size = _install_generation(production, generation_blob)
            _require_disjoint({
                "generation": generation,
                "authority": authority_path,
                "observer": status_path,
                "publication lock": lock_path,
                "memory lock": memory_lock_path,
            })
            report = _coherent_report(
                mem_dir, generation, expected_model, digest, size, require_complete=False
            )
            if report["schema_version"] != PUBLISHED_SCHEMA:
                raise ValueError("adoption did not produce a schema-v3 generation")
            authority = _authority_snapshot(report)
            payload = _json_bytes(authority)
            outcome = {
                "status": "adopted",
                "sidecar": generation,
                "authority_path": authority_path,
                "sha256": digest,
                "embedded": report["embedded"],
                "active": expected_active,
                "missing": len(report["missing"]),
                "stale": len(report["stale"]),
                "observer_updated": False,
                "commit_call_returned": False,
            }
            preflight = None
            try:
                fd, preflight = tempfile.mkstemp(
                    prefix=".embedding_current.adopt.", suffix=".json",
                    dir=os.path.dirname(authority_path),
                )
                os.close(fd)
                artifact.atomic_replace_bytes(preflight, payload)
                loaded = verifier(production, preflight, mem_dir, expected_model)
                if loaded is None or len(loaded[0]) != expected_active:
                    raise RuntimeError("real semantic consumer rejected one-time authority adoption")
                outcome["commit_call_returned"] = _commit_authority(authority_path, payload)
                if preflight:
                    _remove(preflight)
                    preflight = None
                coverage._write_status_snapshot(report, status_path)
                outcome["observer_updated"] = True
                return outcome
            except BaseException:
                if not _authority_matches(authority_path, payload):
                    if preflight:
                        _remove(preflight)
                    raise
                if preflight:
                    try:
                        _remove(preflight)
                    except BaseException:
                        pass
                outcome["observer_updated"] = False
                return outcome


def _args(argv):
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged")
    mode.add_argument("--adopt-current", action="store_true")
    p.add_argument("--production", default=builder.OUT,
                   help="canonical basename used to derive immutable generation names")
    p.add_argument("--authority", default=coverage.DEFAULT_AUTHORITY)
    p.add_argument("--status", default=coverage.DEFAULT_STATUS,
                   help="non-authoritative wake observer")
    p.add_argument("--mem-dir", default=builder.MEM_DEFAULT)
    p.add_argument("--expected-model", default=builder.DEFAULT_MODEL)
    p.add_argument("--lock")
    p.add_argument("--memory-lock")
    p.add_argument("--lock-timeout", type=float, default=30.0)
    return p.parse_args(argv)


def main(argv=None):
    args = _args(sys.argv[1:] if argv is None else argv)
    try:
        if args.adopt_current:
            result = adopt_current(
                args.production, args.authority, args.status, args.mem_dir, args.expected_model,
                args.lock, args.memory_lock, args.lock_timeout
            )
        else:
            result = publish(
                args.staged, args.production, args.authority, args.status, args.mem_dir,
                args.expected_model, args.lock, args.memory_lock, args.lock_timeout
            )
    except BaseException as exc:
        if isinstance(exc, (SystemExit, KeyboardInterrupt)):
            print(
                "publish_memory_embeddings: interrupted; the durable authority pointer is the "
                "source of truth, so audit it before retrying",
                file=sys.stderr,
            )
            return 130
        print("publish_memory_embeddings: failed: %s" % exc, file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
