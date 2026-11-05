#!/usr/bin/env python3
"""Read-only freshness/coverage audit for ``memory_embeddings.npz``.

Presence alone is not freshness: a sidecar can contain nearly the whole historical corpus while
missing every memory written today. Schema-v2+ content hashes let this checker distinguish present,
missing, deleted, and changed rows without loading a sentence model; runtime schema v3 additionally
binds publication-host activation metadata inside the immutable generation.

Usage::

    python embedding_coverage.py
    python embedding_coverage.py --require-complete
    python embedding_coverage.py --write-status
    python embedding_coverage.py --mem-dir PATH --sidecar PATH --expected-model MODEL --json

Without ``--require-complete`` an incomplete but readable sidecar is reported and exits zero, which
is convenient for status observers.  The release gate exits 1 unless the sidecar is exact and fresh;
an unreadable/invalid artifact exits 2.  It never changes embeddings or memories; the optional
``--write-status`` atomically publishes a small observer snapshot for the wake hook.
"""
import argparse
import io
import json
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np

import build_memory_embeddings as builder
import embedding_artifact as artifact


DEFAULT_STATUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".embedding_status.json")
DEFAULT_AUTHORITY = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".embedding_current.json")
DEFAULT_MEMORY_LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory_write.lock")


def _scalar(z, name, default=None):
    if name not in z.files:
        return default
    try:
        return z[name].item()
    except Exception:
        return default


def coverage_report(mem_dir=builder.MEM_DEFAULT, sidecar=builder.OUT, expected_model=None):
    """Return a JSON-safe report without mutating either corpus or sidecar."""
    report = {
        "sidecar": os.path.abspath(sidecar),
        "eligible": 0,
        "embedded": 0,
        "present": 0,
        "coverage": 0.0,
        "fresh_coverage": None,
        "missing": [],
        "extra": [],
        "stale": [],
        "duplicates": [],
        "activation_inconsistent": [],
        "hashes_available": False,
        "schema_version": 0,
        "model": None,
        "built_at": None,
        "age_hours": None,
        "sidecar_sha256": None,
        "sidecar_size": None,
        "corpus_sha256": None,
        "corpus_stat_sha256": None,
        "corpus_stat_files": None,
        "corpus_manifest": {},
        "corpus_readable": False,
        "vector_shape": None,
        "problems": [],
        "readable": False,
        "complete": False,
    }
    try:
        corpus = builder.collect_memories(mem_dir, strict=True)
        current_hash = {slug: digest for slug, _, digest in corpus}
        current = set(current_hash)
        corpus_digest = __import__("hashlib").sha256()
        for slug, digest in sorted(current_hash.items()):
            corpus_digest.update(slug.encode("utf-8"))
            corpus_digest.update(b"\0")
            corpus_digest.update(digest.encode("ascii"))
            corpus_digest.update(b"\n")
        stat_manifest, stat_digest = artifact.corpus_stat_manifest(mem_dir, current)
        report["eligible"] = len(current)
        report["missing"] = sorted(current)
        report["corpus_sha256"] = corpus_digest.hexdigest()
        report["corpus_stat_sha256"] = stat_digest
        report["corpus_stat_files"] = len(stat_manifest)
        report["corpus_manifest"] = stat_manifest
        report["corpus_readable"] = True
    except Exception as exc:
        report["problems"].append("corpus unreadable or unstable: %s" % exc)
        return report
    if not os.path.exists(sidecar):
        report["problems"].append("sidecar missing")
        return report

    try:
        blob, digest, size, mtime_ns = artifact.read_stable_bytes(sidecar)
        report["sidecar_sha256"] = digest
        report["sidecar_size"] = size
        report["age_hours"] = max(
            0.0, datetime.now(timezone.utc).timestamp() - (mtime_ns / 1_000_000_000.0)
        ) / 3600.0
        with np.load(io.BytesIO(blob), allow_pickle=False) as z:
            required = {"slugs", "vecs", "model"}
            absent = required - set(z.files)
            if absent:
                raise ValueError("missing core arrays: %s" % sorted(absent))
            slugs = [str(s) for s in z["slugs"]]
            vecs = np.asarray(z["vecs"])
            model = str(_scalar(z, "model", ""))
            hashes = [str(h) for h in z["text_sha256"]] if "text_sha256" in z.files else None
            schema = int(_scalar(z, "schema_version", 1))
            built_at = str(_scalar(z, "built_at", "")) or None
            source_size = np.asarray(z["source_size"]) if "source_size" in z.files else None
            source_mtime = np.asarray(z["source_mtime_ns"]) if "source_mtime_ns" in z.files else None
            row_safe = np.asarray(z["row_safe"]) if "row_safe" in z.files else None
    except Exception as exc:
        report["problems"].append("sidecar unreadable: %s" % exc)
        return report

    report["readable"] = True
    report["embedded"] = len(slugs)
    report["model"] = model
    report["schema_version"] = schema
    report["built_at"] = built_at
    report["vector_shape"] = list(vecs.shape)
    counts = {}
    for slug in slugs:
        counts[slug] = counts.get(slug, 0) + 1
    report["duplicates"] = sorted(slug for slug, n in counts.items() if n > 1)
    embedded_set = set(slugs)
    report["present"] = len(current & embedded_set)
    report["missing"] = sorted(current - embedded_set)
    report["extra"] = sorted(embedded_set - current)
    report["coverage"] = (report["present"] / len(current)) if current else 1.0

    if expected_model and model != expected_model:
        report["problems"].append("model mismatch: %s != %s" % (model, expected_model))
    if vecs.dtype != np.float32:
        report["problems"].append("vectors are not float32")
    if vecs.ndim != 2 or vecs.shape[0] != len(slugs) or (vecs.ndim == 2 and vecs.shape[1] < 1):
        report["problems"].append("vector matrix shape does not align with slugs")
    elif not np.isfinite(vecs).all():
        report["problems"].append("vectors contain NaN/inf")
    else:
        norms = np.linalg.norm(vecs, axis=1)
        if len(norms) and not np.allclose(norms, 1.0, atol=1e-3, rtol=1e-3):
            report["problems"].append("vectors are not L2-normalized")
    if report["duplicates"]:
        report["problems"].append("duplicate slugs")

    if hashes is None or schema < builder.SCHEMA_VERSION:
        report["problems"].append("legacy sidecar: content freshness is unknowable")
    elif len(hashes) != len(slugs):
        report["problems"].append("content hashes do not align with slugs")
    else:
        report["hashes_available"] = True
        side_hash = {slug: hashes[i] for i, slug in enumerate(slugs)}
        report["stale"] = sorted(slug for slug in current & embedded_set
                                  if side_hash.get(slug) != current_hash[slug])
        fresh = len(current) - len(report["missing"]) - len(report["stale"])
        report["fresh_coverage"] = (fresh / len(current)) if current else 1.0

    if schema >= builder.SCHEMA_VERSION and not built_at:
        report["problems"].append("v2 sidecar has no built_at timestamp")
    if schema >= 3:
        if (source_size is None or source_mtime is None or row_safe is None
                or source_size.dtype != np.int64 or source_mtime.dtype != np.int64
                or row_safe.dtype != np.bool_ or source_size.shape != (len(slugs),)
                or source_mtime.shape != (len(slugs),) or row_safe.shape != (len(slugs),)):
            report["problems"].append("schema-v3 activation arrays are absent or misaligned")
        elif report["hashes_available"]:
            # A normal edit changes source stats and is safely masked. The dangerous inconsistency is
            # stale text whose publication mask and bound stats both claim the row is current: hot
            # consumers would admit it until the mandatory participant hash gate.
            for i, slug in enumerate(slugs):
                current_stat = report["corpus_manifest"].get(slug)
                if (slug in report["stale"] and bool(row_safe[i]) and current_stat is not None
                        and current_stat == [int(source_size[i]), int(source_mtime[i])]):
                    report["activation_inconsistent"].append(slug)
            if report["activation_inconsistent"]:
                report["problems"].append(
                    "schema-v3 activation metadata marks stale text current"
                )
    report["complete"] = bool(
        report["readable"] and report["hashes_available"] and
        not report["missing"] and not report["extra"] and not report["stale"] and
        not report["duplicates"] and not report["problems"]
    )
    return report


def _print_human(r):
    print("embedding_coverage: %d/%d current memories present (%.2f%%); embedded=%d schema=v%d model=%s" %
          (r["present"], r["eligible"], 100.0 * r["coverage"], r["embedded"],
           r["schema_version"], r["model"] or "unknown"))
    fresh = "unknown" if r["fresh_coverage"] is None else "%.2f%%" % (100.0 * r["fresh_coverage"])
    age = "unknown" if r["age_hours"] is None else "%.1fh" % r["age_hours"]
    print("  missing=%d stale=%d extra=%d duplicate=%d fresh=%s age=%s" %
          (len(r["missing"]), len(r["stale"]), len(r["extra"]), len(r["duplicates"]), fresh, age))
    for key in ("missing", "stale", "extra", "duplicates", "activation_inconsistent"):
        if r[key]:
            shown = r[key][:8]
            more = len(r[key]) - len(shown)
            suffix = " (+%d more; full list in the --json report)" % more if more else ""
            print("  %s sample: %s%s" % (key, ", ".join(shown), suffix))
    for problem in r["problems"]:
        print("  problem: %s" % problem)
    print("  verdict: %s" % ("COMPLETE" if r["complete"] else "INCOMPLETE"))


def status_snapshot(report, checked_at=None):
    """Compact, content-free observer state; full slug lists remain in the foreground report."""
    checked_at = checked_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    unsafe_slugs = sorted(set(report["stale"] + report["extra"] + report["duplicates"]))
    return {
        "schema": artifact.OBSERVER_SCHEMA,
        "checked_at": checked_at,
        "complete": bool(report["complete"]),
        "readable": bool(report["readable"]),
        "runtime_safe": bool(report["readable"] and report["corpus_readable"]
                             and not report["problems"]),
        "sidecar": report["sidecar"],
        "sidecar_sha256": report["sidecar_sha256"],
        "sidecar_size": report["sidecar_size"],
        "corpus_sha256": report["corpus_sha256"],
        "corpus_stat_sha256": report["corpus_stat_sha256"],
        "corpus_stat_files": report["corpus_stat_files"],
        "eligible": int(report["eligible"]),
        "embedded": int(report["embedded"]),
        "present": int(report["present"]),
        "missing": len(report["missing"]),
        "stale": len(report["stale"]),
        "extra": len(report["extra"]),
        "duplicates": len(report["duplicates"]),
        "activation_inconsistent": len(report["activation_inconsistent"]),
        "model": report["model"],
        "built_at": report["built_at"],
        "sidecar_age_hours": report["age_hours"],
        "problems": list(report["problems"]),
        # Filenames are metadata, not memory content. Runtime recall uses this narrow list to mask a
        # stale/deleted vector while retaining fresh rows; missing memories already have no vector.
        "unsafe_slugs": unsafe_slugs,
    }


def _write_status_snapshot(report, path=DEFAULT_STATUS, checked_at=None):
    """Low-level observer write; callers must already hold both publication locks."""
    snapshot = status_snapshot(report, checked_at)
    payload = (json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    artifact.atomic_replace_bytes(path, payload)
    return snapshot


def _active_sidecar(canonical, authority_path):
    if os.path.exists(authority_path):
        return artifact.resolve_authority_sidecar(canonical, authority_path)[0]
    return os.path.abspath(canonical)


def active_coverage_report(mem_dir, canonical, expected_model, authority_path):
    """Audit the authority generation; expose a broken pointer instead of falling back green."""
    try:
        if not os.path.exists(authority_path):
            raise FileNotFoundError("embedding authority is missing")
        active, authority = artifact.resolve_authority_sidecar(canonical, authority_path)
        report = coverage_report(mem_dir, active, expected_model)
        bindings = {
            "sidecar_sha256": report["sidecar_sha256"],
            "sidecar_size": report["sidecar_size"],
            "model": report["model"],
            "built_at": report["built_at"],
        }
        mismatched = [key for key, actual in bindings.items()
                      if authority.get(key) != actual]
        if report.get("schema_version") != artifact.RUNTIME_SCHEMA_VERSION:
            mismatched.append("schema_version")
        if mismatched:
            report["problems"].append(
                "embedding authority does not bind audited generation: %s"
                % ", ".join(sorted(set(mismatched)))
            )
            report["complete"] = False
        return report
    except Exception as exc:
        report = coverage_report(mem_dir, canonical, expected_model)
        report["problems"].append("embedding authority unreadable: %s" % exc)
        report["complete"] = False
        return report


def write_status(mem_dir=builder.MEM_DEFAULT, sidecar=builder.OUT,
                 expected_model=builder.DEFAULT_MODEL, path=DEFAULT_STATUS, checked_at=None,
                 authority_path=DEFAULT_AUTHORITY, lock_path=None,
                 memory_lock_path=DEFAULT_MEMORY_LOCK):
    """Re-audit under shared locks and atomically publish a non-authoritative wake observer."""
    lock_path = lock_path or (os.path.abspath(sidecar) + ".publish.lock")
    with artifact.file_lock(lock_path):
        with artifact.file_lock(memory_lock_path):
            report = active_coverage_report(mem_dir, sidecar, expected_model, authority_path)
            snapshot = _write_status_snapshot(report, path, checked_at)
    return snapshot, report


def _args(argv):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mem-dir", default=builder.MEM_DEFAULT)
    p.add_argument("--sidecar", default=builder.OUT)
    p.add_argument("--expected-model", default=builder.DEFAULT_MODEL)
    p.add_argument("--authority", default=DEFAULT_AUTHORITY)
    p.add_argument("--require-complete", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--write-status", nargs="?", const=DEFAULT_STATUS,
                   help="atomically publish a compact status snapshot (default: %(const)s)")
    return p.parse_args(argv)


def main(argv=None):
    args = _args(sys.argv[1:] if argv is None else argv)
    if args.write_status:
        _, report = write_status(args.mem_dir, args.sidecar, args.expected_model,
                                 args.write_status, authority_path=args.authority)
    else:
        report = active_coverage_report(
            args.mem_dir, args.sidecar, args.expected_model, args.authority
        )
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_human(report)
    if not report["readable"]:
        return 2
    if args.require_complete and not report["complete"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
