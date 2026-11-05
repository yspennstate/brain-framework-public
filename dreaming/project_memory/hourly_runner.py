"""Occurrence reconciliation and exactly-once hourly project review publication."""
from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from .classify import CLASSIFIER_VERSION, classification_digest, discover_candidates, load_config
from .hot_cache import build_contract_cache
from .reclassify import apply_reclassification, prepare_reclassification
from .review import acknowledge, hour_bucket, mark_shard_served, offer_context, publish_all, read_offer
from .sources import PromptOccurrence, commit_manifests, scan_sources
from .store import ProjectMemoryStore, config_path, utc_now


def _below_normal() -> None:
    if os.name != "nt":
        try:
            os.nice(10)
        except OSError:
            pass
        return
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = ()
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.GetPriorityClass.argtypes = (ctypes.c_void_p,)
    kernel32.GetPriorityClass.restype = ctypes.c_uint32
    kernel32.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.SetPriorityClass.restype = ctypes.c_int
    handle = kernel32.GetCurrentProcess()
    below_normal = 0x00004000
    if not kernel32.SetPriorityClass(handle, below_normal):
        error = ctypes.get_last_error()
        raise OSError(error, "failed to set BelowNormal process priority")
    if kernel32.GetPriorityClass(handle) != below_normal:
        raise RuntimeError("BelowNormal process priority did not persist")


def _compute_mode() -> tuple[str, str, bool]:
    configured = os.environ.get("PROJECT_MEMORY_COMPUTE_STATE")
    path = (Path(configured).expanduser().resolve() if configured
            else Path.home() / ".brain-framework" / "compute_state.json")
    try:
        row = json.loads(path.read_text(encoding="utf-8-sig"))
        age = max(0.0, __import__("time").time() - path.stat().st_mtime)
        if age > 180:
            return "stale", "stale", True
        return (str(row.get("status") or "unknown"), str(row.get("mode") or "unknown"),
                bool(row.get("owner_active", True)))
    except Exception:
        return "unknown", "unknown", True


def _compute_deferral(status: str, mode: str, owner_active: bool) -> str:
    if status in {"distress", "stale", "unknown"}:
        return "compute_" + status
    if owner_active:
        return "compute_owner_active"
    if mode == "throttle":
        return "compute_" + mode
    return ""


@contextmanager
def _single_runner(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    path = root / "hourly.lock"
    handle = open(path, "a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                locked = True
            except OSError as exc:
                raise RuntimeError("hourly_runner_already_active") from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as exc:
                raise RuntimeError("hourly_runner_already_active") from exc
        yield
    finally:
        if locked:
            try:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def ingest_occurrences(store: ProjectMemoryStore, rows: list[PromptOccurrence], config: dict) -> tuple[int, int]:
    inserted = existing = 0
    for row in rows:
        _event_id, created = store.ingest_prompt(
            row.text, source_id=row.source_id, source_seq=row.source_seq, session=row.session,
            occurred_at=row.occurred_at, provenance=row.provenance, runtime=row.runtime,
            cwd=row.cwd, source_project=row.source_project, config=config,
        )
        inserted += int(created)
        existing += int(not created)
    return inserted, existing


def discover(cfg_path: Path) -> int:
    base = load_config(None)
    base["sources"] = json.loads(os.environ.get("PROJECT_MEMORY_SOURCES_JSON", "[]"))
    with ProjectMemoryStore() as store:
        rows, _source_rows, _pending = scan_sources(store, base)
    metadata = [{"project": row.source_project, "cwd": row.cwd} for row in rows]
    config = discover_candidates(metadata)
    config["sources"] = base["sources"]
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cfg_path.with_name(cfg_path.name + ".tmp-%d" % os.getpid())
    tmp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, cfg_path)
    print("project-memory discover: wrote %d deployment-local candidates" % len(config["projects"]))
    return 0


def _health(store: ProjectMemoryStore, status: str, error: str | None = None) -> None:
    with store.immediate():
        if status == "running":
            store.db.execute(
                "INSERT INTO runner_health(runner_id,attempt_started_at,status,error_code) VALUES('hourly',?,'running',NULL) "
                "ON CONFLICT(runner_id) DO UPDATE SET attempt_started_at=excluded.attempt_started_at,status='running',error_code=NULL",
                (utc_now(),),
            )
        else:
            store.db.execute(
                "UPDATE runner_health SET completed_at=?,status=?,error_code=? WHERE runner_id='hourly'",
                (utc_now(), status, error[:120] if error else None),
            )


def _record_deferral_in_store(store: ProjectMemoryStore, reason: str) -> None:
    now = utc_now()
    with store.immediate():
        store.db.execute(
            "INSERT INTO hourly_deferrals(hour_bucket,reason_code,recorded_at) VALUES(?,?,?) "
            "ON CONFLICT(hour_bucket) DO UPDATE SET reason_code=excluded.reason_code,"
            "recorded_at=excluded.recorded_at,resolved_at=NULL",
            (hour_bucket(), reason[:80], now),
        )
        store.db.execute(
            "INSERT INTO runner_health(runner_id,attempt_started_at,completed_at,status,error_code) "
            "VALUES('hourly',?,?, 'deferred',?) ON CONFLICT(runner_id) DO UPDATE SET "
            "attempt_started_at=excluded.attempt_started_at,completed_at=excluded.completed_at,"
            "status='deferred',error_code=excluded.error_code",
            (now, now, reason[:120]),
        )


def _record_deferral(reason: str) -> None:
    with ProjectMemoryStore() as store:
        _record_deferral_in_store(store, reason)


def _inventory_coverage(store: ProjectMemoryStore) -> tuple[int, int]:
    """Count only manifests that still correspond to the current source inventory."""
    current = set(getattr(store, "last_source_inventory_ids", ()))
    expected = int(getattr(store, "last_source_inventory_total", 0))
    if len(current) != expected:
        raise RuntimeError("source_inventory_identity_count_mismatch")
    manifests = {str(row[0]) for row in store.db.execute("SELECT source_id FROM source_manifests")}
    return len(current.intersection(manifests)), len(manifests.difference(current))


def _publish_and_refresh_cache(store: ProjectMemoryStore, *, source_rows: dict,
                               config: dict) -> list[dict]:
    windows = publish_all(store, source_rows=source_rows, config=config)
    # Review publication changes ``attempt_unresolved`` and generations used by
    # the hot hook.  Keep the pre-publication cache as a fail-safe, then replace
    # it with the successful final state before reporting the run complete.
    build_contract_cache(store, config)
    return windows


def hourly(cfg_path: Path) -> int:
    _below_normal()
    status, mode, owner_active = _compute_mode()
    deferral = _compute_deferral(status, mode, owner_active)
    if deferral:
        _record_deferral(deferral)
        print("project-memory hourly: deferred (compute state %s/%s)" % (status, mode))
        return 75
    if not cfg_path.exists():
        print("project-memory hourly: no local config; run discover first", file=sys.stderr)
        return 2
    config = load_config(cfg_path)
    with ProjectMemoryStore() as store, _single_runner(store.root):
        _health(store, "running")
        try:
            digest = classification_digest(config)
            prior_digest = store.db.execute(
                "SELECT value FROM schema_meta WHERE key='classification_config_digest'"
            ).fetchone()
            if prior_digest and str(prior_digest[0]) != digest:
                raise RuntimeError("classification_config_changed_requires_explicit_rebuild")
            prior_version = store.db.execute(
                "SELECT value FROM schema_meta "
                "WHERE key='classification_classifier_version'"
            ).fetchone()
            if prior_version and str(prior_version[0]) != CLASSIFIER_VERSION:
                raise RuntimeError(
                    "classification_version_changed_requires_explicit_rebuild")
            with store.immediate():
                store.db.execute(
                    "INSERT OR REPLACE INTO schema_meta(key,value) VALUES('classification_config_digest',?)",
                    (digest,),
                )
                store.db.execute(
                    "INSERT OR REPLACE INTO schema_meta(key,value) "
                    "VALUES('classification_classifier_version',?)",
                    (CLASSIFIER_VERSION,),
                )
            rows, source_rows, pending_manifests = scan_sources(store, config)
            inserted, existing = ingest_occurrences(store, rows, config)
            commit_manifests(store, pending_manifests)
            inventory_total = int(getattr(store, "last_source_inventory_total", 0))
            inventory_covered, inventory_stale = _inventory_coverage(store)
            with store.immediate():
                store.db.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES('source_inventory_total',?)",
                                 (str(inventory_total),))
                store.db.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES('source_inventory_covered',?)",
                                 (str(inventory_covered),))
                store.db.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES('source_inventory_stale',?)",
                                 (str(inventory_stale),))
            if mode == "throttle":
                with store.immediate():
                    store.db.execute(
                        "INSERT INTO hourly_deferrals(hour_bucket,reason_code,recorded_at) VALUES(?,?,?) "
                        "ON CONFLICT(hour_bucket) DO UPDATE SET reason_code=excluded.reason_code,"
                        "recorded_at=excluded.recorded_at,resolved_at=NULL",
                        (hour_bucket(), "compute_throttle", utc_now()),
                    )
                build_contract_cache(store, config)
                _health(store, "incomplete", "compute_throttle")
                print("project-memory hourly: ingestion complete; publication deferred (throttle)")
                return 75
            # Contract enforcement depends only on ingested clauses and source
            # coverage.  A private review-packet publication failure must not
            # leave the signed hot cache stale for every unrelated operation.
            build_contract_cache(store, config)
            windows = _publish_and_refresh_cache(
                store, source_rows=source_rows, config=config)
            unresolved = int(store.db.execute(
                "SELECT COUNT(*) FROM project_review_state WHERE attempt_generation>published_generation"
            ).fetchone()[0])
            backfill_pending = inventory_covered < inventory_total
            _health(store, "ok" if not unresolved and not backfill_pending else "incomplete",
                    "source_backfill_pending" if backfill_pending else
                    None if not unresolved else "unresolved_publication_attempt")
            with store.immediate():
                store.db.execute("UPDATE hourly_deferrals SET resolved_at=? WHERE resolved_at IS NULL", (utc_now(),))
            print("project-memory hourly: occurrences=%d inserted=%d existing=%d windows=%d unresolved=%d mode=%s"
                  % (len(rows), inserted, existing, len(windows), unresolved, mode))
            return 0 if not unresolved and not backfill_pending else 3
        except Exception as exc:
            _health(store, "error", type(exc).__name__ + ":" + str(exc))
            raise


def status_command() -> int:
    with ProjectMemoryStore() as store:
        prompts = int(store.db.execute("SELECT COUNT(*) FROM prompt_events").fetchone()[0])
        clauses = int(store.db.execute("SELECT COUNT(*) FROM clauses").fetchone()[0])
        projects = int(store.db.execute("SELECT COUNT(*) FROM projects WHERE active=1").fetchone()[0])
        gaps = int(store.db.execute(
            "SELECT COUNT(*) FROM project_review_state WHERE attempt_generation>published_generation"
        ).fetchone()[0])
        deferrals = int(store.db.execute(
            "SELECT COUNT(*) FROM hourly_deferrals WHERE resolved_at IS NULL"
        ).fetchone()[0])
        health = store.db.execute("SELECT status,error_code,completed_at FROM runner_health WHERE runner_id='hourly'").fetchone()
        inventory = dict(store.db.execute(
            "SELECT key,value FROM schema_meta WHERE key IN "
            "('source_inventory_total','source_inventory_covered','source_inventory_stale')"
        ).fetchall())
        print("project-memory status: prompts=%d clauses=%d active_projects=%d unresolved_attempts=%d "
              "unresolved_deferrals=%d source_files=%s/%s stale_manifests=%s health=%s"
              % (prompts, clauses, projects, gaps, deferrals,
                 inventory.get("source_inventory_covered", "?"), inventory.get("source_inventory_total", "?"),
                 inventory.get("source_inventory_stale", "?"),
                 dict(health) if health else "never-run"))
        return 0 if not gaps and not deferrals and (not health or health["status"] == "ok") else 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_discover = sub.add_parser("discover")
    p_discover.add_argument("--out", type=Path, default=config_path())
    p_hourly = sub.add_parser("hourly")
    p_hourly.add_argument("--config", type=Path, default=config_path())
    p_prepare = sub.add_parser("reclassify-prepare")
    p_prepare.add_argument("--config", type=Path, default=config_path())
    p_prepare.add_argument("--prior-config", type=Path)
    p_prepare.add_argument("--prior-without-project", action="append", default=[])
    p_prepare.add_argument("--output-dir", type=Path)
    p_apply = sub.add_parser("reclassify-apply")
    p_apply.add_argument("artifact", type=Path)
    p_apply.add_argument("--config", type=Path, default=config_path())
    p_read = sub.add_parser("read-offer")
    p_read.add_argument("token")
    p_read.add_argument("--shard", type=int)
    p_latest = sub.add_parser("read-latest")
    p_latest.add_argument("--project", required=True)
    p_latest.add_argument("--session", required=True)
    p_ack = sub.add_parser("ack")
    p_ack.add_argument("token")
    p_ack.add_argument("--occurrences", required=True, type=int)
    p_ack.add_argument("--packet-sha256", required=True)
    sub.add_parser("status")
    args = parser.parse_args(argv)
    if args.command == "discover":
        return discover(args.out.resolve())
    if args.command == "hourly":
        return hourly(args.config.resolve())
    if args.command == "status":
        return status_command()
    if args.command in {"reclassify-prepare", "reclassify-apply"}:
        _below_normal()
        print(
            "project-memory reclassification: low-priority compute guard active",
            file=sys.stderr,
            flush=True,
        )
        status, mode, owner_active = _compute_mode()
        deferral = _compute_deferral(status, mode, owner_active)
        if deferral:
            _record_deferral(deferral)
            print("project-memory reclassification: deferred (compute state %s/%s)"
                  % (status, mode))
            return 75
        with ProjectMemoryStore() as store, _single_runner(store.root):
            progress = lambda message: print(
                "project-memory reclassification: " + message,
                file=sys.stderr,
                flush=True,
            )
            if args.command == "reclassify-prepare":
                artifact = prepare_reclassification(
                    store, target_config_path=args.config.resolve(),
                    prior_config_path=(
                        args.prior_config.resolve() if args.prior_config else None
                    ),
                    prior_without_projects=tuple(args.prior_without_project),
                    output_dir=(
                        args.output_dir.resolve() if args.output_dir else None
                    ),
                    progress=progress,
                )
                print("project-memory reclassification candidate: " + str(artifact))
                return 0
            receipt = apply_reclassification(
                store, artifact_path=args.artifact.resolve(),
                target_config_path=args.config.resolve(),
                progress=progress,
            )
            print("project-memory reclassification applied: " +
                  json.dumps(receipt, sort_keys=True))
            return 0
    with ProjectMemoryStore() as store:
        if args.command == "read-offer":
            payload = read_offer(store, args.token, shard_index=args.shard, record_delivery=False)
            sys.stdout.write(payload)
            sys.stdout.flush()
            if args.shard is not None:
                mark_shard_served(store, args.token, args.shard)
            return 0
        if args.command == "read-latest":
            offers = offer_context(store, session=args.session, project_ids=[args.project])
            due = next((offer for offer in offers if offer.get("state") == "DUE"), None)
            if not due:
                print("project-memory: latest project window is already acknowledged or unavailable")
                return 0
            sys.stdout.write(read_offer(store, due["ack_token"]))
            return 0
        ack_id = acknowledge(store, args.token, occurrence_count=args.occurrences,
                             packet_sha256=args.packet_sha256)
        build_contract_cache(store, load_config(config_path()))
        print("project-memory acknowledgement recorded: " + ack_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
