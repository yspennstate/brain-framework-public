#!/usr/bin/env python3
"""brain.py - the one-command door into the brain, for any agent mid-work.

The hooks (fok_probe, spread_recall, instinct_prime) push memories AT a session when its prompt
touches them. This is the pull side: a session that WANTS the brain's take asks for it, and gets
the full recall stack - lexical match + spreading activation over the association graph + semantic
ranking against the embedding sidecar - in one call, laptop-safe (no model inference; the semantic
half rides the precomputed sidecar via seed-centroid cosine, the same trick spread_recall uses).

  python brain.py recall "what do we know about grid sim accuracy" [-n 8]
  python brain.py node <slug> [--part N]  # node + every verified attached document
  python brain.py attach <slug> --file notes.md [--wake | --on-demand]
  python brain.py verify-attachments [<slug>]
  python brain.py neighbors <slug> [-n 10]   # a memory's graph + semantic neighborhood (for linking)
  python brain.py remember <slug> --type project --desc "one-line hook"
      [--status provisional|verified|owner-stated] [--evidence "artifact/transcript"]
      [--supersedes <old-slug>] [--body "..." | --body-file f]
      [--repair-index --expected-sha256 <sha> --mesh-agent <id>
       --mesh-script <agent_mesh.py>]
  python brain.py supersede <old-slug> --by <new-slug>       # link two memories that already exist
  python brain.py supersede-part <old-slug> --clause "..." --by <new-slug>   # only that clause moved
      [--mesh-agent <id> --mesh-script <agent_mesh.py>]
  python brain.py status                     # delegates to brain_status.py (health observer)

`remember` scaffolds the conventions agents keep having to re-remember: frontmatter (name,
description, metadata.type), the MEMORY.md pointer line, and a nudge to add [[links]]. It refuses
to overwrite. Env seams for tests: BRAIN_MEM_DIR overrides the memory dir (never test on the live
corpus - the 07-08 fixture-pollution scar).

Read-only except `remember` and `attach`. Reuses spread_recall's tested internals so
on-demand recall and hook recall can't drift apart.
"""
import argparse
import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

DR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DR)
import spread_recall as sr
import brain_node_documents as node_docs
from supersession import SLUG_RX, active_supersessions, load_map, resolve as resolve_supersession


def _brain_slug_resolve():
    """The memory corpus this install belongs to.

    Order matters. $BRAIN_SLUG wins so a caller can point one script at another corpus.
    Then BRAIN_SLUG beside this file, which bootstrap/install.py writes with the slug of the
    --project-dir it was given; without it an install whose project directory is not the home
    directory builds a corpus nothing ever reads. The home-derived value stays last so a tree
    that works today keeps working untouched.
    """
    env = os.environ.get("BRAIN_SLUG", "").strip()
    if env:
        return env
    try:
        marker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "BRAIN_SLUG")
        with open(marker, encoding="utf-8") as fh:
            named = fh.read().strip()
        if named:
            return named
    except OSError:
        pass
    home = os.path.expanduser("~")
    return home.replace(":", "-").replace(chr(92), "-").replace("/", "-")


# memory descriptions carry emoji; a cp1252 console must degrade, not crash (caught first run)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MEM = os.environ.get("BRAIN_MEM_DIR", sr.MEM)
# spread_recall owns shared readers such as _index_lines() and _desc().  Keep those readers on
# the same corpus as this command, especially under the documented BRAIN_MEM_DIR test seam.
sr.MEM = MEM
# Claude Code names a project directory after the home path, with ':' and the
# separators replaced by '-'. Deriving it keeps this tree free of one machine's
# username and correct on any other - and unlike a template token it is a real
# value at import time.
_BRAIN_SLUG = _brain_slug_resolve()
LIVE_MEM = os.path.join(
    os.path.expanduser("~"), ".claude", "projects", _BRAIN_SLUG, "memory")
CANONICAL_MESH_SCRIPT = os.path.join(
    os.path.expanduser("~"), "brain-repo",
    "12_cognitive_architecture", "agent_mesh", "agent_mesh.py")
VALID_TYPES = ("user", "feedback", "project", "reference")
VALID_STATUSES = ("provisional", "verified", "owner-stated")
SUPERSEDED_INDEX = os.environ.get("BRAIN_SUPERSEDED_INDEX", os.path.join(DR, "superseded.tsv"))
RECALL_POLICY = os.environ.get("BRAIN_RECALL_POLICY", os.path.join(DR, "recall_policy.json"))

# A new empirical memory is not established truth merely because it was written fluently.  The
# index tag makes epistemic maturity visible in wake/recall output, and the provisional discount
# stops an intermediate simulation from outranking older validated knowledge.  Legacy memories are
# left neutral: changing all historical scores without a migration/eval would be a different project.
_STATUS_WEIGHT = {"provisional": 0.55, "verified": 1.0, "owner-stated": 1.0, "legacy": 1.0}

# Activation replaces only these two exact assignment lines after installing a
# content-addressed runtime generation.  A live ENABLED marker with empty or
# stale pins is deliberately loud and unusable; importing code through a
# mutable READY pointer would let pointer drift execute unreviewed bytes.
STRONG_NODE_PINNED_RUNTIME_GENERATION = "665a76d980fe5bf377fd98ebca449926129060a4a9c7787dccabfd1e091615a2"
STRONG_NODE_PINNED_RUNTIME_DIR = os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "strong_nodes", "runtime", STRONG_NODE_PINNED_RUNTIME_GENERATION)
_STRONG_NODE_RUNTIME_NAMES = (
    "__init__.py",
    "audit_contract.py",
    "compile_registry.py",
    "core.py",
    "deployment.py",
    "hook.py",
    "promotion.py",
    "production.py",
    "publication.py",
    "recurrence.py",
    "semantic_review.py",
)


def _strong_node_live_root():
    return os.environ.get(
        "BRAIN_STRONG_NODE_LIVE_ROOT",
        os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "strong_nodes"),
    )


def _strong_node_enabled_name():
    """Return the production marker name or the narrow activation-test seam."""

    enabled_name = "ENABLED"
    if os.environ.get("BRAIN_STRONG_NODE_TEST_MODE") == "1":
        enabled_name = os.environ.get(
            "BRAIN_STRONG_NODE_TEST_ENABLED_NAME", enabled_name
        )
        if not re.fullmatch(r"ENABLED(?:\.[A-Za-z0-9_-]{1,80})?", enabled_name):
            raise RuntimeError("strong-node test ENABLED name is invalid")
    return enabled_name


STRONG_NODE_WATCH_MAX_AGE_HOURS = 48
STRONG_NODE_WATCH_SCHEMA = "strong-node-observe-watch-v1"


def _strong_node_watch_latest():
    """Read the observe-only watch's latest report (strong_node_watch.py); never raises.

    Returns (report_or_None, freshness) with freshness in fresh | stale | missing | unreadable.
    While the release stays parked at its human gates, the nightly watch runs the reviewed
    scanner read-only; a fresh report lets strong_node_proposals() show the >10 queue as
    OBSERVE_ONLY instead of a bare DISABLED. It is not coverage: nothing in it fires on
    memory access, and it never places ENABLED (2026-09-05).
    """
    import calendar

    path = os.path.join(os.path.abspath(_strong_node_live_root()), "WATCH_LATEST.json")
    if not os.path.isfile(path):
        return None, "missing"
    try:
        with open(path, "rb") as handle:
            report = json.loads(handle.read().decode("utf-8"))
        if not isinstance(report, dict) or report.get("schema") != STRONG_NODE_WATCH_SCHEMA:
            return None, "unreadable"
        stamp = time.strptime(str(report.get("as_of")), "%Y-%m-%dT%H:%M:%SZ")
        age_hours = (time.time() - calendar.timegm(stamp)) / 3600.0
    except Exception:
        return None, "unreadable"
    if age_hours > STRONG_NODE_WATCH_MAX_AGE_HOURS or age_hours < -1.0:
        return report, "stale"
    return report, "fresh"


def _strong_node_runtime_pin():
    """Return an exact reviewed runtime dir or raise before importing code."""

    runtime_dir = STRONG_NODE_PINNED_RUNTIME_DIR
    generation = STRONG_NODE_PINNED_RUNTIME_GENERATION
    if (
        os.environ.get("BRAIN_STRONG_NODE_TEST_MODE") == "1"
        and os.environ.get("BRAIN_STRONG_NODE_TEST_RUNTIME_DIR")
    ):
        runtime_dir = os.environ["BRAIN_STRONG_NODE_TEST_RUNTIME_DIR"]
        generation = os.environ.get(
            "BRAIN_STRONG_NODE_TEST_RUNTIME_GENERATION", ""
        )
    if not re.fullmatch(r"[0-9a-f]{64}", generation or ""):
        raise RuntimeError("brain entrypoint has no reviewed runtime-generation pin")
    path = os.path.abspath(runtime_dir or "")
    if (
        not os.path.isabs(runtime_dir or "")
        or os.path.basename(path) != generation
        or not os.path.isdir(path)
    ):
        raise RuntimeError("brain runtime path/generation pin is invalid")
    rows = []
    for name in _STRONG_NODE_RUNTIME_NAMES:
        child = os.path.join(path, name)
        if (
            not os.path.isfile(child)
            or os.path.islink(child)
            or os.path.realpath(child) != os.path.abspath(child)
        ):
            raise RuntimeError("brain runtime file set is incomplete or aliased")
        with open(child, "rb") as src:
            raw = src.read()
        rows.append(
            {
                "name": name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }
        )
    encoded = (
        json.dumps(
            rows,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if hashlib.sha256(encoded).hexdigest() != generation:
        raise RuntimeError("brain runtime bytes differ from the reviewed generation")
    return path, generation


def _strong_node_package(runtime_dir, generation):
    package_name = "_strong_node_runtime_" + generation
    package = sys.modules.get(package_name)
    if package is not None:
        return package
    init_path = os.path.join(runtime_dir, "__init__.py")
    spec = importlib.util.spec_from_file_location(
        package_name,
        init_path,
        submodule_search_locations=[runtime_dir],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("reviewed strong-node runtime is not importable")
    package = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = package
    try:
        spec.loader.exec_module(package)
    except Exception:
        sys.modules.pop(package_name, None)
        raise
    return package


def _strong_node_advisory(slugs):
    """Return one mandatory warning for accessed memory slugs, fail closed."""

    live_root = os.path.abspath(_strong_node_live_root())
    enabled = os.path.join(live_root, _strong_node_enabled_name())
    if not os.path.exists(enabled):
        return ""
    try:
        runtime_dir, generation = _strong_node_runtime_pin()
        package = _strong_node_package(runtime_dir, generation)
        return package.advisory_for_memory_access(
            sorted(set(str(slug) for slug in slugs if slug)),
            registry_path=None,
            enabled_path=enabled,
        )
    except Exception as exc:
        reason = " ".join(str(exc).split())[:400] or exc.__class__.__name__
        return (
            "[STRONG NODE REGISTRY DEGRADED - access warning unavailable]\n"
            "Do not claim strong-node coverage until repaired.\n"
            "REASON: " + reason
        )


def _print_strong_node_advisory(slugs):
    warning = _strong_node_advisory(slugs)
    if warning:
        print(warning)


def strong_node_proposals():
    """Return exact >10 recurrence candidates and their publication state."""

    live_root = Path(_strong_node_live_root()).resolve()
    enabled = live_root / _strong_node_enabled_name()
    if not enabled.exists():
        queue = {
            "schema": "brain-strong-node-designation-queue-v1",
            "status": "DISABLED",
            "actions": [],
            "message": "no ENABLED strong-node generation is installed",
        }
        # OBSERVE-ONLY (2026-09-05): while the release stays parked at its human gates, the
        # nightly strong_node_watch.py still runs the reviewed scanner read-only. A fresh
        # report shows the >10 queue here as OBSERVE_ONLY; promotion and the access
        # warnings stay dark, and nothing here may be read as coverage.
        report, freshness = _strong_node_watch_latest()
        queue["watch"] = {"freshness": freshness, "as_of": (report or {}).get("as_of")}
        if freshness == "fresh" and report.get("status") == "OBSERVED":
            promotion = report.get("promotion") or {}
            queue["status"] = "OBSERVE_ONLY"
            for key in (
                "scan_seconds", "eligible", "blocked_over_threshold", "near_threshold",
                "totals", "pending_release", "promotion", "strict_rule",
            ):
                queue["watch"][key] = report.get(key)
            queue["message"] = (
                "promotion DISABLED - no ENABLED generation (release %s since %s); "
                "the >10 recurrence watch ran read-only at %s (%s eligible, %s blocked "
                "over threshold, %s near); a watch report is not strong-node coverage"
                % (
                    promotion.get("release_state") or "UNKNOWN",
                    promotion.get("release_timestamp") or "UNKNOWN",
                    report.get("as_of"),
                    (report.get("totals") or {}).get("eligible", "?"),
                    (report.get("totals") or {}).get("blocked_over_threshold", "?"),
                    (report.get("totals") or {}).get("near_threshold", "?"),
                )
            )
        elif freshness == "fresh":
            queue["message"] += "; observe-only watch report is fresh but %s: %s" % (
                report.get("status"), report.get("reason") or "no reason recorded")
        elif freshness == "stale":
            queue["message"] += "; observe-only watch report is stale (as_of %s)" % (
                report.get("as_of"))
        else:
            queue["message"] += "; observe-only watch report is %s" % freshness
        return queue
    try:
        runtime_dir, generation = _strong_node_runtime_pin()
        package = _strong_node_package(runtime_dir, generation)
        deployment = importlib.import_module(package.__name__ + ".deployment")
        recurrence = importlib.import_module(package.__name__ + ".recurrence")
        resolved = deployment.resolve_enabled(
            enabled,
            expected_registry_path=None,
        )
        if resolved is None:
            raise RuntimeError("ENABLED disappeared during designation scan")
        registry = package.StrongNodeRegistry.load(resolved.registry_path)
        deployment.verify_registry_binding(registry.payload, resolved)
        proposal_set = recurrence.proposals(
            memory_dir=Path(MEM),
            mistakes_path=Path(MEM) / "MISTAKES.md",
            instincts_path=Path(DR) / "instincts.tsv",
            audit_root=(
                Path.home()
                / "brain-repo"
                / "12_cognitive_architecture"
                / "audits"
            ),
        )
        active = {}
        identity_re = re.compile(
            r"^atomic identity kind=(\S+) mechanism=(\S+) reflex=(\S+)$"
        )
        for node in registry.payload.get("nodes") or []:
            if (
                not isinstance(node, dict)
                or node.get("node_class") != "strong_recurrence"
            ):
                continue
            identity = None
            for evidence in node.get("evidence") or []:
                match = identity_re.fullmatch(str(evidence))
                if match:
                    identity = (
                        match.group(1),
                        match.group(2),
                        "" if match.group(3) == "-" else match.group(3),
                    )
                    break
            if identity is None:
                continue
            active[identity] = {
                "strong_id": node.get("id"),
                "slug": node.get("slug"),
                "occurrence_ids": sorted(
                    str(row.get("occurrence_id"))
                    for row in node.get("occurrence_manifest") or []
                    if isinstance(row, dict)
                ),
            }
        actions = []
        current = []
        for candidate in proposal_set.get("candidates") or []:
            identity = (
                str(candidate.get("source_kind") or ""),
                str(candidate.get("mechanism_id") or ""),
                str(candidate.get("reflex_id") or ""),
            )
            candidate_ids = sorted(
                str(value) for value in candidate.get("occurrence_ids") or []
            )
            installed = active.get(identity)
            if installed is None:
                action = "PROMOTE"
                reason = (
                    "eligible exact recurrence has no active signed strong node"
                )
            elif installed["occurrence_ids"] != candidate_ids:
                action = "REVIEW_RENEWAL"
                reason = (
                    "eligible recurrence evidence changed after the active "
                    "signed generation"
                )
            else:
                current.append(
                    {
                        "identity": identity,
                        "occurrences": len(candidate_ids),
                        **installed,
                    }
                )
                continue
            actions.append(
                {
                    "action": action,
                    "source_kind": identity[0],
                    "mechanism_id": identity[1],
                    "reflex_id": identity[2] or None,
                    "occurrences": len(candidate_ids),
                    "occurrence_ids": candidate_ids,
                    "reason": reason,
                }
            )
        return {
            "schema": "brain-strong-node-designation-queue-v1",
            "status": "ACTION_REQUIRED" if actions else "CURRENT",
            "strict_rule": proposal_set.get("strict_rule"),
            "actions": actions,
            "current": current,
            "not_promoted": proposal_set.get("not_promoted") or [],
            "publication_generation_id": resolved.pair.generation_id,
            "registry_sha256": registry.registry_sha256,
        }
    except Exception as exc:
        return {
            "schema": "brain-strong-node-designation-queue-v1",
            "status": "DEGRADED",
            "actions": [],
            "message": " ".join(str(exc).split())[:600],
        }


def strong_node_agenda_section():
    """Render work that a dream-designated connection agent must not miss."""

    queue = strong_node_proposals()
    if queue.get("status") == "CURRENT":
        return ""
    lines = [
        "## M1. STRONG NODE designation queue (mandatory growth path)",
        "",
        "The recurrence scanner uses exact compiled-reflex corrections and "
        "current hash-bound audit mechanisms, deduplicated conservatively. "
        "More than ten means at least eleven; similarity clusters alone never promote.",
        "",
    ]
    if queue.get("status") == "ACTION_REQUIRED":
        lines.extend(
            [
                "You are the dream-designated connection agent for these rows. "
                "For each one, inspect every listed occurrence, create or renew "
                "one special mistake node, review its related memory/file/folder/"
                "audit edges one by one, obtain the required signed review, publish "
                "an immutable generation, and run the 1,000-access evaluation. "
                "Do not auto-connect by lexical similarity and do not edit ENABLED "
                "before host validation.",
                "",
            ]
        )
        for row in queue["actions"]:
            lines.append(
                "- **%s** `%s/%s` (%d exact occurrences%s): %s"
                % (
                    row["action"],
                    row["source_kind"],
                    row["mechanism_id"],
                    row["occurrences"],
                    (
                        ", reflex `%s`" % row["reflex_id"]
                        if row.get("reflex_id")
                        else ""
                    ),
                    row["reason"],
                )
            )
    elif queue.get("status") == "OBSERVE_ONLY":
        watch = queue.get("watch") or {}
        promotion = watch.get("promotion") or {}
        pending = watch.get("pending_release") or {}
        lines.extend(
            [
                "**PROMOTION DISABLED - the release is parked at its human gates (%s since "
                "%s); the >10 recurrence watch RUNS read-only** (report as of %s, scan %ss). "
                "This is a queue you can see, not strong-node coverage: nothing here fires on "
                "memory access, ENABLED must not be hand-placed, and only the owner's review "
                "opens the gate (strong_nodes/READY.json). Do not auto-connect by lexical "
                "similarity; the connection-agent workflow starts after activation."
                % (
                    promotion.get("release_state") or "UNKNOWN",
                    promotion.get("release_timestamp") or "UNKNOWN",
                    watch.get("as_of"),
                    watch.get("scan_seconds"),
                ),
                "",
            ]
        )
        for row in watch.get("eligible") or []:
            lines.append(
                "- **OBSERVED ELIGIBLE** `%s/%s` (%d exact occurrences, %s..%s%s): "
                "eligible under the strict rule, blocked only by the review gate; "
                "pending July release: %s"
                % (
                    row.get("source_kind"),
                    row.get("mechanism_id"),
                    int(row.get("occurrences") or 0),
                    row.get("first_date"),
                    row.get("last_date"),
                    ", reflex `%s`" % row["reflex_id"] if row.get("reflex_id") else "",
                    row.get("pending_release"),
                )
            )
        for row in watch.get("blocked_over_threshold") or []:
            lines.append(
                "- **BLOCKED OVER THRESHOLD** `%s/%s` (%d occurrences): %s"
                % (
                    row.get("source_kind"),
                    row.get("mechanism_id"),
                    int(row.get("occurrences") or 0),
                    "; ".join(row.get("ineligible_reasons") or []) or "no reason recorded",
                )
            )
        for row in watch.get("near_threshold") or []:
            lines.append(
                "- near threshold `%s/%s` (%d occurrences%s): %s"
                % (
                    row.get("source_kind"),
                    row.get("mechanism_id"),
                    int(row.get("occurrences") or 0),
                    ", reflex `%s`" % row["reflex_id"] if row.get("reflex_id") else "",
                    "; ".join(row.get("ineligible_reasons") or []) or "no reason recorded",
                )
            )
        stale = pending.get("stale_strong_nodes") or []
        if stale:
            lines.append(
                "- the pending release's strong node(s) no longer matched by current "
                "evidence: %s - a signed activation today would need REVIEW_RENEWAL on them"
                % ", ".join("`%s`" % s for s in stale)
            )
    else:
        lines.extend(
            [
                "**STRONG NODE GROWTH DEGRADED.** Do not claim the >10 recurrence "
                "watch is running.",
                "",
                "- " + str(queue.get("message") or queue.get("status")),
            ]
        )
    return "\n".join(lines) + "\n"


@contextlib.contextmanager
def _memory_write_lock(timeout=20.0):
    """Cross-process lock shared by every cooperating `remember` writer.

    The lock is on a stable sidecar, not MEMORY.md itself, because the index is
    atomically replaced.  Locking the replaced inode would not fence the next writer.
    """
    lock_path = os.environ.get("BRAIN_WRITE_LOCK", os.path.join(DR, "memory_write.lock"))
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("timed out waiting for the shared brain memory lock")
                    time.sleep(0.05)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("timed out waiting for the shared brain memory lock")
                    time.sleep(0.05)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


# ----------------------------------------------------------------------------------------------------------------------
# Recall speed layer (R02, 12 Oct 2026). recall() reloaded the same big files on every call: the embedding sidecar twice
# (the hot reader's packed matrix has no dtype, so _semantic_scores read it again with numpy), the 45 MB bm25 payload for a
# list of slugs, the 265k-name walk from JSON, the link table from SQLite, and the bm25 df dict three times. Each cache
# below is bound to the identity its owning module already trusts for its own cache (bm25._index_signature for the index,
# graph_lane._generation for the walk and the link sidecar, the authority pointer's bytes for the embeddings, the open
# postings file for df), and any miss, mismatch or error runs the owning module's loader, so the worst case is today's
# cost plus one small write. Installed for the duration of recall() only. BRAIN_RECALL_FASTCACHE=0 turns it off;
# BRAIN_RECALL_CACHE_DIR moves the cache files (default: recall_fastcache beside this file).
# ----------------------------------------------------------------------------------------------------------------------
import marshal as _marshal
import struct as _fc_struct
import zlib as _fc_zlib

_FC_VERSION = "r02-2"
_FC_DF = {}
_FC_LIGHT_OK = ({}, None, 0.0)   # truthy, and its matrix has no dtype: _semantic_scores goes on to the full reader


def _fc_on():
    return os.environ.get("BRAIN_RECALL_FASTCACHE", "1") != "0"


def _fc_dir():
    return os.environ.get("BRAIN_RECALL_CACHE_DIR", "").strip() or os.path.join(DR, "recall_fastcache")


def _fc_ident(path):
    """A source file's identity for a cache key: path, size, last write, file id and creation time (a file replaced
    with its size and mtime kept still gets a new id and birth time, the case bm25._stat_signature guards against)."""
    try:
        st = os.stat(path)
        return (os.path.abspath(path), st.st_size, st.st_mtime_ns, st.st_ino,
                getattr(st, "st_birthtime_ns", st.st_ctime_ns))
    except (OSError, TypeError, ValueError):
        return None


def _fc_tag(key):
    return repr((_FC_VERSION, sys.version, key))


def _fc_file(name, key):
    h = hashlib.sha1(_fc_tag(key).encode("utf-8", "surrogateescape")).hexdigest()[:24]
    return os.path.join(_fc_dir(), "%s.%s.marshal" % (name, h))


def _fc_get(name, key):
    try:
        with open(_fc_file(name, key), "rb") as fh:
            stored, value = _marshal.loads(fh.read())
        return value if stored == _fc_tag(key) else None
    except Exception:
        return None


def _fc_put(name, key, value, keep=3):
    """Write atomically; keep the newest `keep` files of this kind (older generations are dead weight)."""
    try:
        d = _fc_dir()
        os.makedirs(d, exist_ok=True)
        path = _fc_file(name, key)
        tmp = "%s.tmp.%d" % (path, os.getpid())
        with open(tmp, "wb") as fh:
            fh.write(_marshal.dumps((_fc_tag(key), value)))
        try:
            os.replace(tmp, path)
        except OSError:
            # the target is open in another recall (Windows refuses the replace): drop our copy, keep theirs
            try:
                os.remove(tmp)
            except OSError:
                pass
            return
        now = time.time()
        old = []
        for e in os.scandir(d):
            if not e.name.startswith(name + "."):
                continue
            if e.name.endswith(".marshal"):
                old.append(e)
            elif ".tmp." in e.name:
                try:
                    if now - e.stat().st_mtime > 600:   # left by a recall that died mid-write
                        os.remove(e.path)
                except OSError:
                    pass
        old.sort(key=lambda e: e.stat().st_mtime_ns, reverse=True)
        for e in old[keep:]:
            try:
                os.remove(e.path)
            except OSError:
                pass
    except Exception:
        pass


_FC_FRESH_SIG = None   # the index generation _fc_fresh_memories saw from the start of its scan to the end, else None


def _fc_sidecar_slugs(bm, path, sig):
    """The indexed slugs of generation `sig` in payload order, from its postings sidecar's d table, or None."""
    con = None
    try:
        import sqlite3
        p = bm.postings_path(path, sig)
        if not os.path.exists(p):
            return None
        con = sqlite3.connect("file:%s?mode=ro" % p.replace("?", "%3f"), uri=True)
        h = dict(con.execute("select k,v from hdr"))
        if h.get("schema") != bm.POSTINGS_SCHEMA or h.get("signature") != str(sig):
            return None
        rows = con.execute("select pos, slug from d order by pos").fetchall()
        # positions are ordered, not always 0..n-1: an incremental sidecar (bm25 I1) places past its base's last one
        if len(rows) != float(h["n"]) or any(rows[i][0] >= rows[i + 1][0] for i in range(len(rows) - 1)):
            return None
        return [r[1] for r in rows]
    except Exception:
        return None
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


def _fc_fresh_memories(bm):
    """bm25.fresh_memories with the indexed-slug list (its 'gone' half) read from a cache bound to bm25's own identity of
    the index file, instead of unpickling the whole payload for its keys. On a cache miss the list comes from the
    generation's postings sidecar when it has one (R16, 13 Oct 2026: the first recall of every generation paid the 45 MB
    load for a list of names, and the index publishes a new generation every couple of minutes)."""
    global _FC_FRESH_SIG
    _FC_FRESH_SIG = None
    if not _fc_on():
        return bm.fresh_memories(bm.MEM, bm.INDEX)
    real = bm._load_payload

    def slugs_only(path=bm.INDEX):
        path = os.fspath(path)
        sig = bm._index_signature(path)
        key = (os.path.abspath(path), sig)
        if sig is not None:
            v = _fc_get("tfkeys", key)
            if v is not None:
                return {"tf": dict.fromkeys(v)}
            v = _fc_sidecar_slugs(bm, path, sig)
            if v is not None:
                _fc_put("tfkeys", key, v)
                return {"tf": dict.fromkeys(v)}
        z = real(path)
        if sig is not None and bm._PAYLOAD_CACHE.get((path, sig)) is z:
            _fc_put("tfkeys", key, list(z["tf"]))
        return z

    bm._load_payload = slugs_only
    try:
        sig0 = bm._index_signature(bm.INDEX)
        out = bm.fresh_memories(bm.MEM, bm.INDEX)
        if sig0 is not None and bm._index_signature(bm.INDEX) == sig0:
            _FC_FRESH_SIG = sig0
        return out
    finally:
        bm._load_payload = real


# FRESH-MEMORY OVERLAY (R16, 13 Oct 2026). The fleet files a memory every minute or two, so a recall usually finds
# memories newer than the index, and it merged them into the whole 45 MB payload before scoring (the payload load alone
# measured 5.6 s on the loaded laptop that morning; Codex hands back a brain call still running at 10 s as an empty
# result). When the scanned generation already has its postings sidecar, the merge needs only what a score reads: n and
# avgdl (the header's avgdl is sum(dl)/n over integer lengths, so the old total is round(avgdl * n), checked), the df of
# the words asked about, and their postings. A re-read or removed memory's old length is on any of its old postings, and
# the words of its own name head its text. Scores add per document in BM25.score's sorted-term order and ties keep the
# merged payload's order (old position, then the fresh documents in merge order), so the list is the one
# BM25.from_payload(refresh_in_memory()).score() returns, bit for bit. Anything else (the payload's tf or dl, iterating
# df, a sidecar that disagrees with itself) falls back to that merge. BRAIN_RECALL_OVERLAY=0 turns it off.
def _fc_find(blob, pos):
    """Index of document `pos` in one term's postings (12-byte records in increasing position order), or -1."""
    lo, hi = 0, len(blob) // 12
    while lo < hi:
        mid = (lo + hi) // 2
        p = _fc_struct.unpack_from("<I", blob, mid * 12)[0]
        if p < pos:
            lo = mid + 1
        elif p > pos:
            hi = mid
        else:
            return mid
    return -1


class _FcOverlayDf:
    """The merged df map read per term: get, [] and `in` answer exactly; iterating it or asking its length builds the
    payload merge."""
    __slots__ = ("_ov",)

    def __init__(self, ov):
        self._ov = ov

    def get(self, term, default=None):
        v = self._ov._df(term)
        return v if v > 0 else default

    def __getitem__(self, term):
        v = self._ov._df(term)
        if v > 0:
            return v
        raise KeyError(term)

    def __contains__(self, term):
        return self._ov._df(term) > 0

    def __iter__(self):
        return iter(self._ov._full().df)

    def __len__(self):
        return len(self._ov._full().df)

    def keys(self):
        return self._ov._full().df.keys()

    def items(self):
        return self._ov._full().df.items()

    def values(self):
        return self._ov._full().df.values()


class _FcOverlay:
    """BM25.from_payload(refresh_in_memory()) for one recall, read from the sidecar and the fresh documents."""

    def __init__(self, bm, con, pos_rows, base_df, n_old, sum_old, fresh, gone, docs):
        # pos_rows: the sidecar's (position, slug) rows in position order. Positions are ordered, not always 0..n-1
        # (bm25 I1 places a changed document past the base's last position); the fresh documents rank after them all.
        self._bm, self._con, self._base_df = bm, con, base_df
        self._pos_slug = dict(pos_rows)
        self._base = pos_rows[-1][0] + 1 if pos_rows else 0
        self._blobs, self._dfc, self._fallback = {}, {}, None
        slug_pos = {s: p for p, s in pos_rows}
        self._added = []          # (slug, tf, dl): the fresh slugs that have tokens, in merge order
        self._fresh_df = {}
        for slug in fresh:
            tks = docs.get(slug)
            if tks:
                tf = {}
                for t in tks:
                    tf[t] = tf.get(t, 0) + 1
                self._added.append((slug, tf, len(tks)))
                for t in tf:
                    self._fresh_df[t] = self._fresh_df.get(t, 0) + 1
        self._dropped = {}        # old position -> old length, for every indexed slug re-read or gone
        for slug in list(fresh) + list(gone):
            p = slug_pos.get(slug)
            if p is not None and p not in self._dropped:
                self._dropped[p] = self._old_dl(slug, p, docs.get(slug))
        self._drop_sorted = sorted(self._dropped)
        self.n = n_old - len(self._dropped) + len(self._added)
        if self.n < 1:
            raise ValueError("the merge leaves no documents")
        self.avgdl = (sum_old - sum(self._dropped.values()) + sum(d for _s, _t, d in self._added)) / self.n
        self.df = _FcOverlayDf(self)

    def _blob(self, term):
        b = self._blobs.get(term)
        if b is None:
            row = self._con.execute("select blob from p where term=?", (term,)).fetchone()
            b = _fc_zlib.decompress(row[0]) if row else b""
            if len(b) % 12:
                raise ValueError("postings of %r are %d bytes" % (term, len(b)))
            self._blobs[term] = b
        return b

    def _old_dl(self, slug, pos, tks):
        """An indexed document's length before the merge, from one of its postings: the rarest words of its name first,
        then of its new text."""
        bdf, seen, cands = self._base_df, set(), []
        for group in (self._bm.tokens(slug.replace("_", " ")), tks or ()):
            g = sorted({t for t in group if t not in seen and bdf.get(t, 0) > 0}, key=lambda t: (bdf[t], t))
            seen.update(g)
            cands.extend(g)
        for t in cands[:64]:
            b = self._blob(t)
            i = _fc_find(b, pos)
            if i >= 0:
                return _fc_struct.unpack_from("<I", b, i * 12 + 8)[0]
        raise LookupError("no posting of %s found" % slug)

    def _df(self, term):
        if self._fallback is not None:
            return self._fallback.df.get(term, 0)
        v = self._dfc.get(term)
        if v is None:
            try:
                v = self._base_df.get(term, 0)
                if v and self._drop_sorted:
                    b = self._blob(term)
                    if not b:
                        raise LookupError("df without postings for %r" % (term,))
                    for p in self._drop_sorted:
                        if _fc_find(b, p) >= 0:
                            v -= 1
                v += self._fresh_df.get(term, 0)
            except Exception:
                return self._full().df.get(term, 0)
            self._dfc[term] = v
        return v

    def _full(self):
        if self._fallback is None:
            payload, _added, _gone = self._bm.refresh_in_memory(out=self._bm.INDEX)
            self._fallback = self._bm.BM25.from_payload(payload) if payload is not None else self._bm.BM25()
        return self._fallback

    def __getattr__(self, name):
        if name in ("tf", "dl"):
            return getattr(self._full(), name)
        raise AttributeError(name)

    def score(self, query, topk=20):
        if self._fallback is None:
            try:
                return self._score(query, topk)
            except Exception:
                pass
        return self._full().score(query, topk)

    def _score(self, query, topk):
        import math
        q = [t for t in self._bm.tokens(query) if self._df(t) > 0]
        if not q:
            return []
        n, avgdl, K1, B = self.n, self.avgdl, self._bm.K1, self._bm.B
        idf = {}
        for t in sorted(set(q)):
            d_t = self._df(t)
            idf[t] = math.log(1.0 + (n - d_t + 0.5) / (d_t + 0.5))
        dropped, unpack, scores = self._dropped, _fc_struct.unpack_from, {}
        for t in idf:
            w = idf[t]
            b = self._blob(t)
            for i in range(0, len(b), 12):
                pos, f, d = unpack("<IiI", b, i)
                if pos not in dropped:
                    scores[pos] = scores.get(pos, 0.0) + w * f * (K1 + 1) / (f + K1 * (1 - B + B * d / avgdl))
        ranked = [(p, s) for p, s in scores.items() if s > 0]
        base = self._base
        for j, (_slug, tf, dl) in enumerate(self._added):
            s = 0.0
            for t in idf:
                f = tf.get(t)
                if f:
                    s += idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * dl / avgdl))
            if s > 0:
                ranked.append((base + j, s))
        ranked.sort(key=lambda kv: (-kv[1], kv[0]))
        return [(self._pos_slug[k] if k < base else self._added[k - base][0], s) for k, s in ranked[:topk]]


def _fc_overlay(bm, fresh, gone):
    """(overlay, added, gone) in place of refresh_in_memory's merge for this recall, or None, which sends the caller down
    the payload merge unchanged: the overlay is off, nothing is fresh, the generation is frozen or moved since the scan,
    it has no sidecar yet, more files are fresh than refresh_in_memory merges, or anything about the sidecar is in doubt."""
    if not _fc_on() or os.environ.get("BRAIN_RECALL_OVERLAY", "1") == "0":
        return None
    sig = _FC_FRESH_SIG
    if sig is None or not (fresh or gone) or len(fresh) > bm.REFRESH_FULL_REBUILD_AT:
        return None
    con = None
    try:
        if bm.freeze_state(bm.INDEX)[0]:
            return None
        import sqlite3
        p = bm.postings_path(bm.INDEX, sig)
        if not os.path.exists(p):
            return None
        con = sqlite3.connect("file:%s?mode=ro" % p.replace("?", "%3f"), uri=True)
        h = dict(con.execute("select k,v from hdr"))
        if (h.get("schema") != bm.POSTINGS_SCHEMA or h.get("signature") != str(sig)
                or bm._index_signature(bm.INDEX) != sig):
            raise LookupError("the sidecar is not the scanned generation")
        n_f, avgdl_old = float(h["n"]), float(h["avgdl"])
        n_old = int(n_f)
        sum_old = int(round(avgdl_old * n_old)) if n_old > 0 else 0
        if n_old < 1 or n_old != n_f or sum_old / n_old != avgdl_old:
            raise ValueError("the header's n and avgdl are not a count and a mean of integer lengths")
        rows = con.execute("select pos, slug from d order by pos").fetchall()
        if len(rows) != n_old or rows[0][0] < 0 or any(rows[i][0] >= rows[i + 1][0] for i in range(len(rows) - 1)):
            raise ValueError("the d table is not n increasing positions")
        base_df = _fc_vocab_df(con)
        if base_df is None:
            raise LookupError("no vocabulary")
        if set(fresh) & set(gone):
            raise ValueError("a slug both fresh and gone")
        # each listing of a slug re-appends it, so a slug listed twice sits where it was listed last
        order = list(dict.fromkeys(reversed(fresh)))[::-1]
        docs = {slug: bm.read_doc(bm.MEM, slug) for slug in order}
        ov = _FcOverlay(bm, con, rows, base_df, n_old, sum_old, order, gone, docs)
        # refresh_in_memory counts a slug listed twice twice (one memory with two changed attachments); so does the
        # printed count here
        return ov, sum(1 for slug in fresh if docs.get(slug)), len(gone)
    except Exception:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
        return None


def _fc_light_embeddings():
    """sr._load_embeddings() as _semantic_scores uses it. When the hot reader exists its result is only a gate here (its
    packed matrix has no dtype, so the full reader loads the sidecar again), and the gate's answer is fixed by the sidecar
    bytes and the authority pointer: the full reader verifies the same sha256 against the same pointer, and the hot
    reader's other checks read only those bytes. So a positive answer is cached under the pointer's bytes; a negative
    one is never cached (it may be a passing race)."""
    ea = getattr(sr, "embedding_artifact", None)
    hot = getattr(ea, "load_verified_embeddings_lightweight", None) if ea is not None else None
    if not _fc_on() or hot is None:
        return sr._load_embeddings()
    try:
        with open(sr.EMB_AUTHORITY, "rb") as fh:
            pointer = fh.read()
        key = (hashlib.sha256(pointer).hexdigest(), os.path.abspath(sr.EMB), os.path.abspath(sr.EMB_AUTHORITY),
               os.path.abspath(sr.MEM), sr.EXPECTED_EMB_MODEL, _fc_ident(getattr(ea, "__file__", "")))
    except Exception:
        return sr._load_embeddings()
    if _fc_get("light", key) == 1:
        return _FC_LIGHT_OK
    emb = sr._load_embeddings()
    if emb:
        _fc_put("light", key, 1)
    return emb


def _fc_walk_load(gl, d):
    """Diffusion.cached(...)'s load path for graph_lane's dial, with nodes.json's names (and what _load derives from
    them) read from a cache bound to the generations of the walk cache and nodes.json. None = let graph_lane do it.
    The matrix is a diffusion.CSR, which multiplies with scipy's own routine without importing scipy.sparse (that
    import was 0.8 s of every recall until 13 Oct)."""
    if _fc_gl_path(gl) is None:
        return None
    import collections as _collections
    import diffusion as _diff
    import numpy as np
    store, co_w, cap, alpha, iters = d["store"], d["co_w"], int(d["cap"]), d["alpha"], 20
    tag = "diffusion_cache_c%g_k%d_a%g_i%d" % (co_w, cap, alpha, iters)
    cache_dir = os.path.join(gl.HERE, "graph_lane_cache")
    path = os.path.join(cache_dir, "%s_%s.npz" % (
        hashlib.sha1(os.path.abspath(store).lower().encode("utf-8")).hexdigest()[:12], tag))
    if not os.path.exists(path):
        return None
    if not _diff.Diffusion._cache_matches(path, os.path.join(store, "edges.npz"), _diff.source_identity(store)):
        return None
    nodes = os.path.join(store, "nodes.json")
    gen_npz, gen_nodes = gl._generation(path), gl._generation(nodes)
    key = (gen_npz, gen_nodes, repr(alpha), iters, _fc_ident(getattr(_diff, "__file__", "")))
    v = _fc_get("walk", key)
    if v is None:
        g = _diff.Diffusion._load(path, store, alpha, iters)
        if gl._generation(path) == gen_npz and gl._generation(nodes) == gen_nodes:
            _fc_put("walk", key, {"names": g.names, "meta": g.meta, "slugs": list(g.mem_nodes.keys()),
                                  "rows": list(g.mem_nodes.values()), "ms": g._ms.tolist()}, keep=2)
        return g
    z = np.load(path)
    g = _diff.Diffusion.__new__(_diff.Diffusion)
    g.names, g.meta = v["names"], v["meta"]
    g.N = len(g.names)
    g.id = {n: i for i, n in enumerate(g.names)}
    g.alpha, g.iters, g.dtype = alpha, iters, np.float32
    g.PT = _diff.CSR(z["data"], z["indices"], z["indptr"], g.N)
    g.dangling, g.p0, g.out_sum = z["dangling"], z["p0"], z["out_sum"]
    g._mn, g._ms, g.nnz = z["mn"], z["ms"], int(z["nnz"][0])
    g.mem_nodes = _collections.defaultdict(list)
    g.mem_nodes.update(zip(v["slugs"], v["rows"]))
    g.slug_list = sorted(g.mem_nodes)
    g.slug_ix = {s: k for k, s in enumerate(g.slug_list)}
    g._ms = np.array(v["ms"], np.int64)
    return g


def _fc_gl_path(gl):
    """graph_lane._graph puts graph_lane's folder on sys.path before importing diffusion; do the same."""
    try:
        if gl.HERE not in sys.path:
            sys.path.insert(0, gl.HERE)
        return gl.HERE
    except Exception:
        return None


def _fc_graph_wrap(gl, real):
    def _graph(d, build=False):
        key = (d["store"], d["co_w"], d["cap"], d["alpha"]) + gl._generation(os.path.join(d["store"], "edges.npz"))
        if gl._G.get(key) is None:
            try:
                g = _fc_walk_load(gl, d)
            except Exception:
                g = None
            if g is None:
                return real(d, build)
            gl._keep(gl._G, key, g, 2)
        return gl._G[key]
    _graph._r02 = True
    return _graph


def _fc_links_wrap(gl, real):
    def _link_graph():
        path = os.environ.get("BRAIN_GRAPH_SIDECAR", "").strip() or os.path.join(gl.LIVE, "sidecar.sqlite")
        gen = gl._generation(path)
        if gen in gl._LINKS:
            return gl._LINKS[gen]
        if gen[1] is None:
            return real()
        key = (gen, _fc_ident(getattr(gl, "__file__", "")))
        v = _fc_get("links", key)
        if v is not None and gl._generation(path) == gen:
            return gl._keep(gl._LINKS, gen, (v[0], v[1]), 4)
        res = real()
        if gen in gl._LINKS and gl._LINKS[gen] is res:
            _fc_put("links", key, (res[0], res[1]), keep=2)
        return res
    _link_graph._r02 = True
    return _link_graph


def _fc_vocab_df(con):
    """The df map of the postings sidecar open on `con`, read once per process and kept on disk in marshal form under
    the sidecar's identity (BM25.df below and the fresh-memory overlay both read it), or None."""
    try:
        dbpath = con.execute("PRAGMA database_list").fetchone()[2]
        st = os.stat(dbpath)
        key = (os.path.abspath(dbpath), st.st_ino, st.st_size, st.st_mtime_ns)
    except Exception:
        return None
    df = _FC_DF.get(key)
    if df is None:
        df = _fc_get("df", key)
    if df is None:
        try:
            import pickle as _pickle
            import zlib as _zlib
            row = con.execute("select blob from vocab where k='df'").fetchone()
            if row:
                df = _pickle.loads(_zlib.decompress(row[0]))
                _fc_put("df", key, df, keep=2)
        except Exception:
            df = None
    if df is not None:
        _FC_DF.clear()
        _FC_DF[key] = df
    return df


def _fc_df_getattr(bm, real):
    """BM25.__getattr__ with the postings sidecar's df read once per process (three BM25() objects per recall each read
    and unpickled the same 300k-term blob) and kept on disk in marshal form under the open sidecar's identity."""
    def __getattr__(self, name):
        if name == "df" and _fc_on():
            sc = self.__dict__.get("_sc")
            if sc is not None:
                df = _fc_vocab_df(sc[0])
                if df is not None:
                    self.df = df
                    return df
        return real(self, name)
    __getattr__._r02 = True
    return __getattr__


# ONE LISTING OF THE MEMORY FOLDER PER RECALL (R19, 13 Oct 2026). A recall listed the memory folder (21,700 entries) on
# its main thread three to five times: supersession's listing with a stat per file for the superseded cache's key, the
# same listing two or three more times inside supersession whenever that cache missed (any write to the folder misses
# it), and _corpus_slugs. On the loaded laptop a listing costs about 0.10-0.13 s and the stats bring the key's to about
# 0.23 s. Inside the speed layer the first of these readers takes one os.scandir, noting the time just before it the
# way _fc_md_signature did, and the others read that listing: on Windows a DirEntry's is_file and stat come from the
# listing itself, and listdir returns the names in scandir's order, so each reader sees what a listing of its own would
# have shown at that moment. The listing never outlives the call. BRAIN_RECALL_ONESCAN=0 turns it off.
_MEMLIST = {}


def _memlist_open():
    """Open the call's listing (taken on first use); False when one is already open or the share is switched off."""
    if "lock" in _MEMLIST or os.environ.get("BRAIN_RECALL_ONESCAN", "1") == "0":
        return False
    import threading
    _MEMLIST.clear()
    _MEMLIST["lock"] = threading.Lock()
    return True


def _memlist_close():
    _MEMLIST.clear()


def _memlist():
    """(DirEntry list of MEM, time_ns taken just before listing it) for the open call, or None: no call open, the share
    switched off, or the folder could not be listed (each reader then lists it itself, as before R19)."""
    lock = _MEMLIST.get("lock")
    if lock is None:
        return None
    with lock:
        got = _MEMLIST.get("got")
        if got is None:
            try:
                t = time.time_ns()
                with os.scandir(MEM) as it:
                    got = (list(it), t)
            except Exception:
                got = False
            _MEMLIST["got"] = got
    return got or None


def _memlist_md(got):
    """supersession._md_files over the listing: the .md names that are files, [] on any error as it does."""
    try:
        return [e for e in got[0] if e.name.endswith(".md") and e.is_file()]
    except Exception:
        return []


def _fc_md_files_wrap(ss, real):
    """supersession._md_files reading the call's listing when it is asked for the memory folder (its reader lists the
    folder again on every cache miss); any other folder, or no listing, goes to the real function."""
    mem = os.path.normcase(os.path.abspath(MEM))

    def _md_files(memory_dir):
        got = _memlist()
        if got is not None:
            try:
                same = os.path.normcase(os.path.abspath(memory_dir)) == mem
            except Exception:
                same = False
            if same:
                return _memlist_md(got)
        return real(memory_dir)
    _md_files._r02 = True
    return _md_files


def _fc_md_signature(ss):
    """supersession's own view of the memory folder (_md_files, _stat_key) as one digest, or None when a file was
    written in the last few seconds (supersession re-reads those itself, so its answer may not be the cached one).
    Inside a recall the view is the call's one listing, judged at the time it was taken (R19)."""
    got = _memlist()
    if got is not None:
        entries, now_ns = _memlist_md(got), got[1]
    else:
        now_ns = time.time_ns()
        entries = ss._md_files(MEM)
    rows = []
    for e in entries:
        k = ss._stat_key(e.stat())
        if now_ns - k[1] < 6_000_000_000:
            return None
        rows.append((e.name, k))
    if not rows:
        return None
    return hashlib.sha256(repr(rows).encode("utf-8", "surrogateescape")).hexdigest(), len(rows)


def _fc_superseded_wrap(real):
    """brain._load_superseded (active_supersessions with frontmatter authority) read once per state of the memory
    folder: the key is supersession's own per-file identity (size, mtime, ctime, birth time) over its own listing, the
    supersession index file and supersession.py. A miss calls the real reader and stores its answer under the key taken
    before the call; if the folder moved meanwhile that key belongs to a state that cannot come back (a write moves a
    file's mtime and ctime forward), so the entry is never served."""
    def _load_superseded():
        try:
            import supersession as _ss
            sig = _fc_md_signature(_ss)
            key = (os.path.abspath(MEM), sig, _fc_ident(SUPERSEDED_INDEX), _fc_ident(_ss.__file__),
                   _ss._FM_CACHE_VERSION)
        except Exception:
            return real()
        if sig is None:
            return real()
        v = _fc_get("superseded", key)
        if isinstance(v, dict):
            return v
        res = real()
        if isinstance(res, dict) and _fc_ident(SUPERSEDED_INDEX) == key[2]:
            _fc_put("superseded", key, res)
        return res
    _load_superseded._r02 = True
    return _load_superseded


class _LazyAdj(dict):
    """defaultdict(list) whose values are stored as marshal bytes and decoded on first read (the cached spread graph).
    A read of a missing key through [] inserts [] as defaultdict does; .get leaves the dict alone as it does there.
    Anything that walks the values (items, values, copy, equality, a dict built from it) decodes them all first, and
    iteration is written in Python so CPython's dict-merge fast path, which would copy the raw bytes, never applies."""
    __slots__ = ()
    default_factory = list

    def _one(self, k, v):
        if type(v) is bytes:
            v = _marshal.loads(v)
            dict.__setitem__(self, k, v)
        return v

    def _all(self):
        for k, v in list(dict.items(self)):
            if type(v) is bytes:
                dict.__setitem__(self, k, _marshal.loads(v))

    def get(self, k, d=None):
        v = dict.get(self, k, _LazyAdj)
        return d if v is _LazyAdj else self._one(k, v)

    def __getitem__(self, k):
        v = dict.get(self, k, _LazyAdj)
        if v is _LazyAdj:
            v = []
            dict.__setitem__(self, k, v)
            return v
        return self._one(k, v)

    def __missing__(self, k):
        v = []
        dict.__setitem__(self, k, v)
        return v

    def __iter__(self):
        return iter(list(dict.keys(self)))

    def items(self):
        self._all()
        return dict.items(self)

    def values(self):
        self._all()
        return dict.values(self)

    def copy(self):
        from collections import defaultdict as _dd
        self._all()
        return _dd(list, dict.items(self))

    def setdefault(self, k, d=None):
        if k in self:
            return self[k]
        dict.__setitem__(self, k, d)
        return d

    def pop(self, k, *d):
        v = dict.pop(self, k, *d)
        return _marshal.loads(v) if type(v) is bytes else v

    def popitem(self):
        k, v = dict.popitem(self)
        return k, (_marshal.loads(v) if type(v) is bytes else v)

    def __eq__(self, other):
        self._all()
        if isinstance(other, _LazyAdj):
            other._all()
        return dict.__eq__(self, other)

    __hash__ = None

    def __repr__(self):
        self._all()
        return "defaultdict(%r, %s)" % (list, dict.__repr__(self))

    def __reduce__(self):
        from collections import defaultdict as _dd
        self._all()
        return (_dd, (list,), None, None, iter(dict.items(self)))


def _fc_spread_graph_wrap(srm, real):
    """spread_recall._load_graph with the parsed graph kept under the identity of every edge file the loader reads
    (spread_edges.tsv, and spread_edges_fresh.tsv while it is newer). Keyed on the first file alone, as it was, the
    cache served brain recall a graph 1,385 nodes and 2,686 edges short on 1 Nov (built 22:30, fresh edges 23:44).
    Each node's neighbour list is stored as its own marshal bytes and decoded when a recall first reads it: a recall
    reads 150-1,950 of 29,366 nodes (1 Nov 2026), and unmarshalling every list cost 0.03-0.05 s a cold recall.
    with_keywords is accepted and unused by the loader, and bypasses this cache if ever passed."""
    def _paths():
        return srm._edge_paths() if hasattr(srm, "_edge_paths") else [srm.EDGES]

    def _load_graph(with_keywords=False, include_base=False):
        try:
            idents = tuple(_fc_ident(p) for p in _paths())
            key = (idents, bool(include_base), _fc_ident(srm.__file__))
        except Exception:
            return real(with_keywords, include_base)
        if with_keywords or None in idents:
            return real(with_keywords, include_base)
        v = _fc_get("sgraph_lazy", key)
        if v is None:
            res = real(with_keywords, include_base)
            try:
                now = tuple(_fc_ident(p) for p in _paths())
            except Exception:
                now = None
            if now == idents:
                packed = {k: _marshal.dumps(list(nbs)) for k, nbs in res[0].items()}
                _fc_put("sgraph_lazy", key, [packed] + [dict(x) for x in res[1:]], keep=2)
            return res
        from collections import defaultdict as _dd
        adj = _LazyAdj(v[0])
        deg = _dd(float)
        deg.update(v[1])
        if include_base:
            base = _dd(float)
            base.update(v[2])
            return adj, deg, base
        return adj, deg
    _load_graph._r02 = True
    return _load_graph


@contextlib.contextmanager
def _fc_layer():
    undo = []
    opened = False
    if _fc_on():
        opened = _memlist_open()
        try:
            import supersession as _ss
            if not getattr(_ss._md_files, "_r02", False):
                undo.append((_ss, "_md_files", _ss._md_files))
                _ss._md_files = _fc_md_files_wrap(_ss, _ss._md_files)
        except Exception:
            pass
        g = globals()
        if not getattr(g.get("_load_superseded"), "_r02", False):
            undo.append((g, "_load_superseded", g["_load_superseded"]))
            g["_load_superseded"] = _fc_superseded_wrap(g["_load_superseded"])
        try:
            if not getattr(sr._load_graph, "_r02", False):
                undo.append((sr, "_load_graph", sr._load_graph))
                sr._load_graph = _fc_spread_graph_wrap(sr, sr._load_graph)
        except Exception:
            pass
        try:
            import graph_lane as _gl
            if not getattr(_gl._graph, "_r02", False):
                undo.append((_gl, "_graph", _gl._graph))
                _gl._graph = _fc_graph_wrap(_gl, _gl._graph)
            if not getattr(_gl._link_graph, "_r02", False):
                undo.append((_gl, "_link_graph", _gl._link_graph))
                _gl._link_graph = _fc_links_wrap(_gl, _gl._link_graph)
        except Exception:
            pass
        try:
            import bm25 as _bm
            cur = _bm.BM25.__dict__.get("__getattr__")
            if cur is not None and not getattr(cur, "_r02", False):
                undo.append((_bm.BM25, "__getattr__", cur))
                _bm.BM25.__getattr__ = _fc_df_getattr(_bm, cur)
        except Exception:
            pass
    try:
        yield
    finally:
        for obj, name, val in reversed(undo):
            if isinstance(obj, dict):
                obj[name] = val
            else:
                setattr(obj, name, val)
        if opened:
            _memlist_close()


def _corpus_slugs():
    got = _memlist()
    if got is not None:
        return [e.name[:-3] for e in got[0] if e.name.endswith(".md") and e.name[:-3] not in sr.SKIP]
    try:
        return [fn[:-3] for fn in os.listdir(MEM) if fn.endswith(".md") and fn[:-3] not in sr.SKIP]
    except Exception:
        return []


def _semantic_scores(seeds, diag=None):
    """Cosine of every embedded memory to the strength-weighted seed centroid, bias-calibrated.
    {} if no sidecar/seeds.

    diag, if a dict is passed, is filled with the readout's own evidence-mass numbers (2026-07-23,
    diffusion iteration 5). a finite-sample ratio decomposition gives the exact finite-p
    identity m_p - m_inf = n_hat / B_hat: ALL retrieval error is a centered fluctuation divided by
    the empirical evidence-mass denominator. The implementation's B_hat for a query is
        B_hat = (sum of live seed strengths) * ||strength-weighted mean of the unit seed vectors||
              = evidence mass x seed-cloud coherence,
    which equals the strength-weighted sum of each seed's cosine to the readout centroid - the
    readout's own log-partition denominator, computed from what the query actually touched.
    n_eff = (sum p)^2 / sum p^2 rides along as the secondary diagnostic (how many seeds carry
    B_hat); the sqrt(d_eff/n_eff) initial-slope heuristic stays out of the flag per the chapter-16
    scope check (the growing-dimensional interior law is open). Keys: ready, b_hat, n_eff,
    n_seeds, n_live."""
    if not seeds:   # R02: {} whatever the sidecar holds; both loads below were wasted
        return {}
    emb = _fc_light_embeddings()
    if not emb or not seeds:
        return {}
    try:
        import numpy as np
        idx_e, mat, bias = emb
        if not hasattr(mat, "dtype"):
            # 2026-07-28: sr._load_embeddings returns the numpy-free PackedFloat32Matrix for hook
            # speed; everything below is numpy and threw AttributeError into the bare except, so
            # every deliberate recall since the hot reader landed ran silent semantic-off even
            # with a FRESH sidecar. Deliberate recall is not the 190ms-sensitive hook path:
            # reload through the full verified numpy loader (same authority checks, fail closed).
            emb = _full_embeddings()
            if not emb:
                return {}
            idx_e, mat, bias = emb
        current = {}

        def row_current(slug):
            if slug not in current:
                current[slug] = sr.embedding_artifact.embedding_row_current(emb, MEM, slug)
            return current[slug]

        live = [(idx_e[s], float(seeds[s])) for s in seeds if s in idx_e and row_current(s)]
        if diag is not None:
            diag["ready"] = True
            diag["n_seeds"] = len(seeds)
            diag["n_live"] = len(live)
            if live:
                p = np.array([wt for _, wt in live], dtype=float)
                pm = np.clip(p, 1e-9, None)
                mean_vec = (mat[[r for r, _ in live]] * (pm / pm.sum())[:, None]).sum(axis=0)
                diag["b_hat"] = float(pm.sum() * np.linalg.norm(mean_vec))
                diag["n_eff"] = float(pm.sum() ** 2 / (pm ** 2).sum())
        if not live:
            return {}
        rows = [r for r, _ in live]
        # Strength-weighted centroid (2026-07-23). The unweighted mean collapses the seed
        # set to equal votes; seed sets run to hundreds of slugs on ordinary queries, so a
        # strong lexical/bm25 seed drowns in weak ones. Keeping the seed strengths as
        # evidence weights is the equal-weight -> weighted step of the diffusion
        # readout the diffusion notes and measured better on BOTH
        # regimes of the wiki-link ground truth (eval_score_rerank 5-fold: component MRR
        # .0463->.0693 all pairs, .0111->.0163 hard subset, positive every fold; full
        # pipeline unchanged; positive controls green). BRAIN_SEM_CENTROID=1 restores
        # the unweighted mean.
        if os.environ.get("BRAIN_SEM_CENTROID") == "1":
            c = mat[rows].mean(axis=0)
        else:
            w = np.array([max(wt, 1e-9) for _, wt in live], dtype=mat.dtype)
            w = w / w.sum()
            c = (mat[rows] * w[:, None]).sum(axis=0)
        c = c / (float(np.linalg.norm(c)) + 1e-9)
        cos = mat @ c
        out = {}
        # Exact text validation is an I/O gate, so validate a generous semantic oversample rather
        # than all ~1,800 rows on every deliberate recall. The top 256 is far beyond any displayed
        # result set while cutting warm validation from ~430 ms to tens of milliseconds.
        # Rank only active rows. Digest-bound publication masks and local stat drift can leave
        # inactive matrix rows; letting them enter the top-256 could crowd out every safe result.
        candidates = sorted(
            ((slug, int(row)) for slug, row in idx_e.items()),
            key=lambda item: -float(cos[item[1]]),
        )[:256]
        for s in seeds:
            if s in idx_e and all(s != existing for existing, _ in candidates):
                candidates.append((s, idx_e[s]))
        for s, i in candidates:
            if row_current(s):
                out[s] = max(0.0, (float(cos[i]) - bias) / (1.0 - bias))
        return out
    except Exception:
        return {}


def _query_semantic_scores(query, k=None, floor=None):
    """Cosine of every embedded memory to the QUERY'S OWN embedding (2026-09-13, goal 30h item 7b).

    _semantic_scores above never embeds the question: it builds a centroid from the LEXICAL seeds, so a question
    bm25 cannot reach ("which research box did we turn off and where did its stuff go" - the memory says iced,
    retired, moved) stays blind in every lane. On the 56-question owner-voice bank 12 of the 14 hit@5 misses were
    exactly that shape, with every gold memory embedded (15,923 of 15,941). This lane embeds the query with the
    sidecar's own model through the light ONNX path (query_embed.py, ~1 s cold, 20 ms warm; sentence-transformers
    itself costs a 55 s import here) and returns {slug: cosine} for the top k rows at or above `floor`, current
    rows only. On by default since R11 (13 Oct; BRAIN_RECALL_QUERY_EMBED=0 turns it off); BRAIN_RECALL_QSEM_K
    and BRAIN_RECALL_QSEM_FLOOR size it, 60 and 0 under the default z fusion, 10 and 0.5 under
    BRAIN_RECALL_QSEM_FUSE=convex. {} on any failure or when off.

    MEASURED 2026-09-14 AND LEFT OFF - it was staged "default 0 until measured on the bank", and this is that
    measurement. Both arms end to end on the 56-question owner-voice bank, one frozen index generation:

        off   hit@1 32/56   hit@5 45/56   225 s
        on    hit@1 31/56   hit@5 46/56   446 s

    Four questions of 56 move at all: it gains "why did the dream stop committing to the repo for a week"
    (6 -> 2) and loses "how should the papers and readmes sound" (1 -> 2). A wash.
    AND IT DOES NOT REACH THE QUESTIONS IT WAS BUILT FOR. The worked example in the paragraph above, "which
    research box did we turn off", still misses: of its three golds the lane ranks one at 5 of its own
    10-candidate list and the other two nowhere, and the same holds for "can i run a backtest on the trading
    box" and "the lanes archive filled box 4 disk". The justification above, that 12 of the 14 hit@5 misses
    were this shape, was true of the 09-13 population and is not true now: measured 09-14, of 13 hit@5 misses
    SIX have their gold inside the 60-item lexical pool, four sit at lexical rank 61-136, and only three are
    beyond lexical reach at all.
    THE COST IS THE REAL ARGUMENT: +3.9 s on EVERY deliberate recall. A bank question is a fresh process, which
    is what a hook and the CLI both are, so each pays the cold model and embedding-matrix load - the 20 ms warm
    figure above is a within-process number and does not describe the path anyone actually uses. That is nine
    times the 0.43 s the postings sidecar saved the same morning, bought with a net of zero answers.
    R11 TURNED IT ON (13 Oct 2026, claude-1789621d), changed three ways (the block comment above _BGE_QUERY_PREFIX),
    and measured it end to end on the production path, arms back to back: owner-voice 56 hit@1 35 -> 39, hit@5 49 ->
    48, MRR .729 -> .774 (@1 +4/-0, @5 +0/-1); the 22 Sep bank's dev half, first 100 items, reworded hit@10 31 -> 34,
    MRR .205 -> .211, verbatim .782 -> .786 (a churn, @1 +3/-2); the BRAINDEV check half, all 644 items, hit@1/5/10
    265/437/485 -> 276/452/511, MRR .529 -> .551 (@1 +22/-11, @10 +26/-0); the held half, scored once in the lab
    (502 items): reworded hit@1/5/10 108/177/206 -> 113/182/224, verbatim even. With the lane off the order is the
    pre-R11 order (56/56). Cost: +0.3 to +0.75 s median per recall against the pre-R11 brain on a loaded laptop,
    the encoder's cold start (InferenceSession 0.6 s, imports 0.3-0.4 s, the encode itself 0.02 s), hidden behind
    the lexical work when a core is free. The fill for a memory with no vector changed after the held half was
    scored (the mean cosine sank fresh compaction nodes); every held gold has a vector, and on the dev half the two
    fills give the same aggregates and the same top ten on the 300 questions diffed item by item."""
    _QLANE.clear()
    if not _qlane_on():
        return {}
    try:
        import numpy as np
        fuse = _qlane_fuse()
        k = int(k if k is not None else os.environ.get("BRAIN_RECALL_QSEM_K", "60" if fuse == "z" else "10"))
        floor = float(floor if floor is not None else os.environ.get("BRAIN_RECALL_QSEM_FLOOR",
                                                                         "0" if fuse == "z" else "0.5"))
        qv = _qemb_get(query)
        if qv is None:
            return {}
        emb = _full_embeddings()
        if not emb:
            return {}
        idx_e, mat, _bias = emb
        cos = mat @ qv.astype(mat.dtype)
        ov = _fresh_overlay(int(mat.shape[1]))
        ocos = ov.matrix @ qv.astype(ov.matrix.dtype) if ov is not None else None
        _QLANE.update({"query": query, "cos": cos, "emb": emb, "ov": ov, "ocos": ocos})
        if k <= 0:
            return {}
        m = max(3 * k, 30)
        top = np.argpartition(-cos, min(m, len(cos) - 1))[:m]
        row_slug = {int(r): s for s, r in idx_e.items()}
        out = {}
        for i in sorted(top, key=lambda r: -float(cos[r])):
            c = float(cos[i])
            if c < floor or len(out) >= k:
                break
            s = row_slug.get(int(i))
            if s is not None and sr.embedding_artifact.embedding_row_current(emb, MEM, s):
                out[s] = c
        if ov is not None:
            # a fresh row (R13) enters only if it beats the k-th sidecar nomination; rows are judged current lazily,
            # best first, so a recall reads a handful of memory files, not the whole overlay
            bar = min(out.values()) if len(out) >= k else None
            ov_slug = {r: s for s, r in ov.index.items()}
            fresh = {}
            for r in np.argsort(-ocos):
                c = float(ocos[r])
                if c < floor or (bar is not None and c <= bar) or len(fresh) >= k:
                    break
                s = ov_slug.get(int(r))
                if (s is not None and s not in out and s not in idx_e
                        and sr.embedding_artifact.embedding_row_current(ov, MEM, s)):
                    fresh[s] = c
            if fresh:
                out = dict(sorted(list(out.items()) + list(fresh.items()), key=lambda kv: -kv[1])[:k])
        return out
    except Exception:
        return {}


# ----------------------------------------------------------------------------------------------------------------------
# R11 query-meaning lane (13 Oct 2026, claude-1789621d, T-20261012-066): the lane above, on by default, with three
# changes measured on the 22 Sep 999-item recall bank (dev half = even sha1(id); the held half scored once) and on
# BRAINDEV. (1) The query is encoded with bge's own retrieval instruction ("Represent this sentence for searching
# relevant passages: ") - the sidecar's model was trained to read a query that way, and query_embed.py never added it.
# (2) Fusion by z-scores over EVERY candidate's own cosine (the bm25 body plus the lane's top-k nominations), not a
# convex blend of min-max scores in which a body memory outside the lane's own top-k scored 0 - that cliff is what
# made the 09-14 measurement a wash. (3) The encoder runs on a worker thread from the top of recall and the verified
# sidecar is loaded once per recall call for both meaning lanes, so most of its cost hides behind the lexical work
# (never reused across calls: the loader judges which rows are current at load time - _full_emb_scope).
# BRAIN_RECALL_QUERY_EMBED=0 turns the lane off (the order is then exactly the pre-R11 order);
# BRAIN_RECALL_QSEM_FUSE=convex restores the 09-13 blend; BRAIN_RECALL_QSEM_PREFIX=0 drops the instruction.
# ----------------------------------------------------------------------------------------------------------------------
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
_QEMB = {}
_QEMB_PROC = []     # one entry once this process has encoded a query (see _qemb_proc_send)
_QLANE = {}
_FULL_EMB = {}
_FULL_EMB_LOCK = []


def _qlane_on():
    return os.environ.get("BRAIN_RECALL_QUERY_EMBED", "1") == "1"


def _qlane_fuse():
    f = os.environ.get("BRAIN_RECALL_QSEM_FUSE", "z").strip().lower()
    return f if f in ("z", "convex") else "z"


def _qlane_text(query):
    return (_BGE_QUERY_PREFIX + query) if os.environ.get("BRAIN_RECALL_QSEM_PREFIX", "1") != "0" else query


def _full_embeddings():
    """The verified numpy sidecar, shared by the centroid lane and the query lane inside one recall() call (each
    loaded it on its own before R11, about a second apiece cold). The loader decides which rows are current from the
    memory files' stat at load time, so a load is reused only inside the call that made it: a process that runs many
    recalls (an eval script, a test) loads again for each one, as before R11, and a memory edited between two calls
    is judged afresh. Outside a recall() call nothing is cached. Inside one the cache is also keyed by the authority
    pointer's bytes, and a failed verification is never cached. Thread-safe: a second caller waits for the first
    load instead of starting its own."""
    if not _FULL_EMB_LOCK:
        import threading
        _FULL_EMB_LOCK.append(threading.Lock())
    with _FULL_EMB_LOCK[0]:
        tok = _FULL_EMB.get("call")
        key = None
        if tok is not None:
            try:
                with open(sr.EMB_AUTHORITY, "rb") as fh:
                    key = fh.read()
            except Exception:
                key = None
            if (key is not None and _FULL_EMB.get("tok") is tok and _FULL_EMB.get("key") == key
                    and _FULL_EMB.get("emb")):
                return _FULL_EMB["emb"]
        emb = sr.embedding_artifact.load_verified_embeddings(sr.EMB, sr.EMB_AUTHORITY, sr.MEM, sr.EXPECTED_EMB_MODEL)
        if emb and key is not None and tok is not None and _FULL_EMB.get("call") is tok:
            _FULL_EMB.update(tok=tok, key=key, emb=emb)
        return emb


def _full_emb_scope(opening):
    """Open (with a fresh token) or close the per-call cache of _full_embeddings. recall() brackets every call."""
    if not _FULL_EMB_LOCK:
        import threading
        _FULL_EMB_LOCK.append(threading.Lock())
    with _FULL_EMB_LOCK[0]:
        _FULL_EMB.clear()
        if opening:
            _FULL_EMB["call"] = object()


def _qemb_start(query):
    """Encode the query and load the verified sidecar on a worker thread while recall does its lexical work. The
    vector comes from query_embed_proc.py, a process of its own (_qemb_proc_send): onnxruntime holds the GIL for the
    whole of its session build (~0.65 s; measured 13 Oct, a spinning thread was shut out 96-97% of it), so building
    the encoder on this thread froze the main thread for that long. When no helper runs, or it fails, the worker
    encodes in-thread as before. The sidecar load is many small GIL re-acquisitions, each waiting out the 5 ms switch
    interval behind a busy main thread (0.9 s alone, 4-5 s beside a busy thread, about 1 s at 1 ms), so the interval
    is shortened while the worker runs and restored in _qemb_get."""
    _QEMB.clear()
    if not _qlane_on() or not query or not query.strip():
        return
    try:
        import threading
        import numpy  # noqa: F401  (imported here, on this thread, so the worker never imports it under contention)
        text = _qlane_text(query)
        box = {"query": query, "text": text, "v": None, "done": threading.Event(),
               "switch": sys.getswitchinterval()}

        def work():
            proc = _qemb_proc_send(text)
            try:
                _full_embeddings()
            except Exception:
                pass
            v, how = _qemb_proc_collect(proc) if proc is not None else (None, "none")
            box["proc"] = how
            if how in ("none", "failed"):
                try:
                    import query_embed
                    v = query_embed.embed(text)
                except Exception:
                    v = None
            box["v"] = v
            box["done"].set()

        sys.setswitchinterval(min(box["switch"], 0.001))
        th = threading.Thread(target=work, name="brain-query-embed", daemon=True)
        box["thread"] = th
        _QEMB["box"] = box      # the worker's own dict, by reference: a copy would never see its vector
        th.start()
    except Exception:
        _QEMB.clear()


def _qemb_proc_send(text):
    """Start query_embed_proc.py with the query text on its stdin (no window; stdin, stdout and stderr all named) and
    return the process, or None: BRAIN_QEMB_PROC=0, the helper file missing, the start or the write failing, or this
    process having encoded a query before. A process that runs many recalls pays the in-thread session build once and
    then encodes in about 20 ms a query, cheaper than a helper's ~1.5 s each, so only its first query goes out."""
    first = not _QEMB_PROC
    _QEMB_PROC.append(1)
    if not first or os.environ.get("BRAIN_QEMB_PROC", "1").strip().lower() in ("0", "off", "false", "no"):
        return None
    qe = sys.modules.get("query_embed")
    if qe is not None and "sess" in getattr(qe, "_STATE", {}):
        return None
    helper = os.path.join(DR, "query_embed_proc.py")
    if not sys.executable or not os.path.exists(helper):
        return None
    proc = None
    try:
        import subprocess
        data = text.encode("utf-8")
        proc = subprocess.Popen([sys.executable, helper], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        proc.stdin.write(data)
        proc.stdin.close()
        return proc
    except Exception:
        _qemb_proc_kill(proc)
        return None


def _qemb_proc_kill(proc):
    if proc is None:
        return
    try:
        proc.kill()
        proc.communicate(timeout=5)
    except Exception:
        pass


def _qemb_proc_collect(proc):
    """(vector, "used") from the helper; (None, "used") when it answered that embed gave None; (None, "failed") when it
    exited badly or wrote anything but b"N" or b"V" + float32 bytes (the caller then encodes in-thread); (None,
    "timeout") when it is still silent after BRAIN_RECALL_QSEM_WAIT seconds, recall's own wait for the vector - it is
    killed, and recall goes on without a vector as it did when the in-thread encoder ran past that wait."""
    import subprocess
    try:
        out, _ = proc.communicate(timeout=float(os.environ.get("BRAIN_RECALL_QSEM_WAIT", "20")))
    except subprocess.TimeoutExpired:
        _qemb_proc_kill(proc)
        return None, "timeout"
    except Exception:
        _qemb_proc_kill(proc)
        return None, "failed"
    if proc.returncode != 0 or not out:
        return None, "failed"
    if out == b"N":
        return None, "used"
    if out[:1] != b"V" or len(out) < 5 or (len(out) - 1) % 4:
        return None, "failed"
    try:
        import numpy as np
        return np.frombuffer(out[1:], dtype="<f4").astype(np.float32), "used"
    except Exception:
        return None, "failed"


def _qemb_get(query):
    """The query vector from the worker (waits for it), or encoded here when no worker ran for this query."""
    box = _QEMB.get("box") or {}
    v = None
    if box.get("query") == query and box.get("done") is not None:
        box["done"].wait(float(os.environ.get("BRAIN_RECALL_QSEM_WAIT", "20")))
        v = box.get("v")
        try:
            sys.setswitchinterval(box.get("switch", 0.005))
        except Exception:
            pass
        _QEMB.clear()
        return v
    try:
        import query_embed
        return query_embed.embed(_qlane_text(query))
    except Exception:
        return None


def _qemb_finish():
    """Join a worker that recall never collected (an early return) and put the switch interval back."""
    box = _QEMB.get("box") or {}
    _QEMB.clear()
    if box.get("done") is None:
        return
    try:
        box["done"].wait(10.0)
    except Exception:
        pass
    try:
        sys.setswitchinterval(box.get("switch", 0.005))
    except Exception:
        pass


_FRESH_NPZ = os.path.join(DR, "memory_embeddings_fresh.npz")


def _fresh_overlay(dim):
    """R13 (13 Oct 2026): the overlay fresh_vectors.py writes every 15 minutes - meaning vectors for the memories
    written or edited since the last sidecar build, encoded the same way and keyed by the same canonical-text hash -
    as a VerifiedEmbeddings, or None (absent, empty, another model or width, unreadable, or BRAIN_RECALL_FRESH=0).
    A row is used only for a memory the sidecar does not cover, and only while embedding_row_current says it is
    current, so a stale overlay can withhold a vector but never lend a wrong one."""
    if os.environ.get("BRAIN_RECALL_FRESH", "1").strip() == "0":
        return None
    try:
        import numpy as np
        with np.load(_FRESH_NPZ, allow_pickle=False) as z:
            if str(z["model"].item()) != sr.EXPECTED_EMB_MODEL or int(z["schema"].item()) != 1:
                return None
            slugs = [str(s) for s in z["slugs"]]
            hashes = [str(h) for h in z["text_sha256"]]
            mat = np.asarray(z["vectors"])
        if (not slugs or mat.dtype != np.float32 or mat.shape != (len(slugs), dim) or len(hashes) != len(slugs)
                or len(set(slugs)) != len(slugs) or not np.isfinite(mat).all()):
            return None
        return sr.embedding_artifact.VerifiedEmbeddings({s: i for i, s in enumerate(slugs)}, mat, 0.0,
                                                        dict(zip(slugs, hashes)))
    except Exception:
        return None


def _qlane_cosines(slugs):
    """{slug: query cosine} for the given slugs whose sidecar row is current - or, for a memory the sidecar does not
    cover yet, whose fresh overlay row is (R13); {} when the lane did not run."""
    cos, emb = _QLANE.get("cos"), _QLANE.get("emb")
    if cos is None or not emb:
        return {}
    idx_e = emb[0]
    ov, ocos = _QLANE.get("ov"), _QLANE.get("ocos")
    out = {}
    for s in slugs:
        r = idx_e.get(s)
        if r is None:
            if ov is not None and s in ov.index:
                try:
                    if sr.embedding_artifact.embedding_row_current(ov, MEM, s):
                        out[s] = float(ocos[ov.index[s]])
                except Exception:
                    pass
            continue
        try:
            if sr.embedding_artifact.embedding_row_current(emb, MEM, s):
                out[s] = float(cos[int(r)])
        except Exception:
            continue
    return out


def _tiers():
    """slug -> tier from consciousness.tsv (log-substrate stubs are 'unconscious'). {} if absent."""
    out = {}
    try:
        with io.open(os.path.join(DR, "consciousness.tsv"), encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 2:
                    out[p[0][:-3] if p[0].endswith(".md") else p[0]] = p[1]
    except Exception:
        pass
    return out


def _archived_index_lines():
    """INDEX_ARCHIVE.md lines, same shape as the live index. A deliberate recall should search the
    WHOLE index - compaction is recency-only, and it archived an owner convention the same night it
    was needed (grid_1v1, caught 2026-07-09). Live lines win on duplicates."""
    out = {}
    try:
        with io.open(os.path.join(MEM, "INDEX_ARCHIVE.md"), encoding="utf-8", errors="replace") as f:
            for ln in f:
                m = re.match(r"- \[([^\]]+?)\.md\]", ln)
                if m:
                    out[m.group(1)] = ln.strip()
    except Exception:
        pass
    return out


_USAGE_CACHE = None
USAGE_STALE_HOURS = 12.0


def _usage_lag_hours(act_path):
    """Hours base_activation.tsv trails retrievals.tsv, or None when there is nothing to trail.

    Raises rather than guessing: a freshness check that cannot run has not passed.
    """
    trace = os.path.join(os.path.dirname(act_path), "retrievals.tsv")
    if not os.path.exists(trace):
        return None                      # no input on disk -> nothing to be behind of
    return (os.path.getmtime(trace) - os.path.getmtime(act_path)) / 3600.0


def _usage_staleness(act_path):
    """Reason the usage signal cannot be trusted right now, or "" when it can.

    base_activation.tsv is DERIVED from retrievals.tsv, and a derived table that is
    hours behind its own input looks exactly like a fresh one on disk.  On 2026-08-22
    it was 41 hours behind: three memories still carried the maximum usage weight
    that the live trace had already decayed to ~0.2, and nothing said so, because the
    loader failed open.  Tolerance is generous on purpose - the nightly dream rebuilds
    the trace and this table in the same block, seconds apart, so a real lag means a
    cycle was missed.
    """
    try:
        lag = _usage_lag_hours(act_path)
        if lag is None:
            return ""
        try:
            tol = float(os.environ.get("BRAIN_USAGE_MAX_STALE_H", USAGE_STALE_HOURS))
        except (TypeError, ValueError):
            tol = USAGE_STALE_HOURS
        if lag > tol:
            return ("base_activation.tsv is %.1f h behind retrievals.tsv (tolerance %.1f h)"
                    % (lag, tol))
        return ""
    except Exception as exc:
        return "freshness check failed (%s: %s)" % (type(exc).__name__, exc)


def _load_usage_norm():
    """{slug: Bnorm in [0,1]} from base_activation.tsv (ACT-R base-level activation,
    built by base_activation.py from the retrieval trace).

    WHY (2026-07-30, the dream's p119 gate): usage is the signal the ranker was
    missing - causal AUROC on predicting re-reads: usage 0.704 > recency 0.682 >
    salience 0.670, third consecutive win, largest trace yet (987 memories /
    1,951 events). Salience scores vividness, not workhorse-ness; the most-read
    memories are low-charge workhorses that ranked no better than strangers.

    Fail-CLOSED on staleness (2026-08-22): if the table trails the trace it is built
    from, drop the tilt to the documented no-effect state (1.0 everywhere) and say so
    on stderr, instead of ranking on a signal that has already decayed away.  stderr,
    not stdout, so the several harnesses that parse recall output cannot break.
    BRAIN_RECALL_USAGE=stale keeps the old behaviour; BRAIN_USAGE_MAX_STALE_H moves
    the tolerance.
    Fail-open on absence/corruption: {} -> tilt 1.0 everywhere (no effect)."""
    global _USAGE_CACHE
    if _USAGE_CACHE is not None:
        return _USAGE_CACHE
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "base_activation.tsv")
    stale = _usage_staleness(p)
    if stale and os.environ.get("BRAIN_RECALL_USAGE", "1") != "stale":
        print("  [warn] usage tilt OFF this recall: %s. Rebuild it with `python "
              "base_activation.py` (the nightly dream does this right after the "
              "retrieval-trace step); BRAIN_RECALL_USAGE=stale ranks on it anyway."
              % stale, file=sys.stderr)
        _USAGE_CACHE = {}
        return _USAGE_CACHE
    try:
        rows = {}
        with io.open(p, encoding="utf-8", errors="replace") as fh:
            for i, ln in enumerate(fh):
                if i == 0:
                    continue
                c = ln.rstrip("\n").split("\t")
                if len(c) >= 2:
                    try:
                        rows[c[0]] = float(c[1])
                    except ValueError:
                        pass
        if rows:
            lo, hi = min(rows.values()), max(rows.values())
            span = (hi - lo) or 1.0
            _USAGE_CACHE = {s: (b - lo) / span for s, b in rows.items()}
        else:
            _USAGE_CACHE = {}
    except Exception:
        _USAGE_CACHE = {}
    return _USAGE_CACHE


def _load_superseded():
    """Return old_slug -> correction_slug from the machine-readable supersession index.

    `spread_recall` already uses this index for proactive recall.  Deliberate `brain.py recall`
    historically ignored it, which meant an explicitly superseded fact could still rank first when
    an agent asked the brain directly.  Keep one fail-open reader here so the pull and push paths can
    no longer disagree on whether a belief is current.
    """
    return active_supersessions(
        MEM, index_path=SUPERSEDED_INDEX, include_frontmatter=True)


def _current_slug(slug, superseded, known_slugs=None):
    """Follow a supersession chain; callers may share one corpus snapshot across a recall."""
    if known_slugs is None:
        known_slugs = set(_corpus_slugs())
    return resolve_supersession(slug, superseded, known_slugs=known_slugs)


def _status_from_index(slug, idx):
    """The epistemic tag an index line carries, or "legacy" when it carries none.

    2026-09-14: the pattern here was `] - [TAG]`, but an index line is a markdown LINK - it reads
    `- [slug.md](slug.md) - [VERIFIED] ...`, so the character before the dash is `)`, never `]`.
    The regex therefore matched nothing: 10,863 of 15,633 index lines carry a tag (6,248 provisional,
    4,494 verified, 121 owner-stated) and this reader saw "legacy" for every single one, which made
    _STATUS_WEIGHT - the provisional discount - inert wherever it was applied. Anchored on the link so
    that a tag appearing later in the description cannot be mistaken for the status."""
    line = idx.get(slug, "")
    # RETRACTED added 2026-09-22 (F50): a memory its author took back now carries that tag on its index line,
    # and the lane label prints it, so a retracted claim no longer reads as a live one in the ranked list
    m = re.search(r"\)\s*-\s*\[(PROVISIONAL|VERIFIED|OWNER-STATED|RETRACTED)\]", line, re.I)
    return m.group(1).lower() if m else "legacy"


def _lesson_line(slug):
    """The first prose line of a memory's body - for a FIRE WHEN memory, the rule its trigger points at.
    Skips the frontmatter, blank lines, headings and rules; bounded to 8 KB; fail-safe empty string."""
    try:
        with io.open(os.path.join(MEM, slug + ".md"), encoding="utf-8", errors="replace") as handle:
            head = handle.read(8000)
        fm = re.match(r"\s*---\s*\n.*?\n---[ \t]*\n", head, re.S)
        body = head[fm.end():] if fm else head
        for line in body.splitlines():
            s = line.strip().lstrip("#>*- ").strip()
            # a lesson is a sentence: skip section labels ("FAILURE MECHANISM", "What you asked:"), rules,
            # and one-word lines - the first line with several words and a lowercase letter that is not a label
            if len(s) < 24 or s.startswith("---") or s.endswith(":") or not re.search(r"[a-z]", s) or len(s.split()) < 6:
                continue
            return s[:130]
    except Exception:
        pass
    return ""


SNIPPET_MAX = int(os.environ.get("BRAIN_RECALL_SNIPPET", "320") or 320)


def _full_description(slug):
    """The memory's own frontmatter description, for a row recall is about to display; '' when there is none.
    Bounded to the first 8 KB; a quoted value is unquoted; fail-safe empty string."""
    try:
        with io.open(os.path.join(MEM, slug + ".md"), encoding="utf-8", errors="replace") as handle:
            head = handle.read(8000)
        fm = re.match(r"\s*---\s*\n(.*?)\n---", head, re.S)
        if not fm:
            return ""
        m = re.search(r"^description:[ \t]*(.*)$", fm.group(1), re.M)
        if not m:
            return ""
        v = m.group(1).strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
            if head and m.group(1).strip().startswith('"'):
                v = v.replace('\\"', '"').replace("\\\\", "\\")
        return " ".join(v.split())
    except Exception:
        return ""


def _frontmatter_retracted(slug):
    """True when the memory's own closed frontmatter says its author took it back (status retracted /
    withdrawn / headline-retracted-in-place - the spellings the corpus uses). Read only for the rows
    recall is about to DISPLAY, so a retracted memory whose index line carries no tag still says so in
    the ranked list (2026-09-22, F50: 28 such memories read as live facts). Fail-safe: any error -> False."""
    try:
        with io.open(os.path.join(MEM, slug + ".md"), encoding="utf-8", errors="replace") as handle:
            head = handle.read(8000)
        fm = re.match(r"\s*---\s*\n(.*?)\n---", head, re.S)
        if not fm:
            return False
        m = re.search(r"^\s*status:\s*(\S+)", fm.group(1), re.M)
        return bool(m) and m.group(1).strip().strip("'\"").lower().startswith(("retracted", "withdrawn", "headline-retracted"))
    except Exception:
        return False


def _part_flag(slug, superseded, known_slugs):
    """'partly superseded: <clause> -> <current slug>' for a DISPLAYED row whose own frontmatter says part of it
    was overtaken (superseded_in_part, or the older partially_superseded_by spelling); '' otherwise.

    The row is NOT routed away - that is the point: the clause that moved is named, the rest of the memory is
    still the answer. The successor is carried to the end of its own chain, as a whole-node route would be.
    One frontmatter read per displayed row; any error -> '' (the row prints exactly as before).
    BRAIN_PART_SUPERSESSION=0 turns the flag off."""
    if os.environ.get("BRAIN_PART_SUPERSESSION", "1") == "0":
        return ""
    try:
        import supersession as _sup
        items = _sup.read_part_supersessions(MEM, slug)
        if not items:
            return ""
        shown = []
        for clause, successor in items[:2]:
            # the row label is printed inside [...] and parsed that way by the eval harnesses: no brackets in it
            clause = clause.replace("[", "(").replace("]", ")")
            clause = clause if len(clause) <= 70 else clause[:67].rstrip() + "..."
            if successor:
                shown.append("%s -> %s" % (clause, _current_slug(successor, superseded, known_slugs)))
            else:
                shown.append("%s (in place)" % clause)
        more = (" (+%d more)" % (len(items) - 2)) if len(items) > 2 else ""
        return "partly superseded: " + " / ".join(shown) + more
    except Exception:
        return ""


def _moved_flag(slug, superseded, known_slugs, already=""):
    """'moved: <what> -> <current slug>' for a DISPLAYED row whose memory was filed before a dated change in
    moved_facts.json and whose frontmatter description names the thing that changed (a deleted box or storage
    account, a retired law generation, the laptop GPU stop, the voice change). True by construction - the thing
    did change - and it says nothing about the rest of the memory, so the row is not routed away.

    2026-09-23 (T4, round 6): two Opus judges read 450 recall hits for 90 fresh prompts of his; 31 hits both called
    misleading, nearly all old memories that name something since retired and never point forward. Matching the
    registry on the description flags 19 of those 31 and 13 of 135 useful hits (3.3x the base rate); a lexical
    'claims to be current and is old' test was also tried and was no better than 1.5x. The rule lives in
    moved_facts.py, shared with the association hint (folded in the same day; it was inline here first). At most
    two notes, none for a successor the row's part flag already names. BRAIN_MOVED_FACTS=0 turns it off.
    Fail-safe: ''."""
    if os.environ.get("BRAIN_MOVED_FACTS", "1") == "0":
        return ""
    try:
        import moved_facts
        with io.open(os.path.join(MEM, slug + ".md"), encoding="utf-8", errors="replace") as handle:
            head = handle.read(8000)
        fm = re.match(r"\s*---\s*\n(.*?)\n---", head, re.S)
        found = moved_facts.notes(slug, fm.group(1) if fm else "",
                                  resolve=lambda s: _current_slug(s, superseded, known_slugs),
                                  skip=tuple(re.findall(r"-> (\S+)", already or "")))
        return ("moved: " + " / ".join("%s -> %s" % (w, s) for w, s in found)) if found else ""
    except Exception:
        return ""


def _require_pointer_target(slug, field="superseded_by"):
    """Refuse a correction pointer the resolver cannot follow.

    `resolve()` fails OPEN on a broken route, by design - so a pointer at a file that does not exist leaves
    the stale memory served as CURRENT while its own frontmatter says it was corrected, and nothing anywhere
    says so.  Measured on the live corpus 2026-09-22: five pointers were dangling exactly that way, three of
    them truncated slugs, which places the bug at write time rather than in the resolver.  So the target is
    checked here, where it is written, and the write fails loudly instead.
    """
    if not SLUG_RX.fullmatch(slug or ""):
        raise ValueError("%s must be ONE slug, not %r - the resolver reads a single slug and silently drops "
                         "prose, so a sentence here declares nothing" % (field, slug))
    if not os.path.isfile(os.path.join(MEM, slug + ".md")):
        raise ValueError("%s names %s.md, which does not exist - a dangling pointer is served as current "
                         "because resolve() fails open" % (field, slug))


def _stamp_superseded(body, correction, pending=False):
    """Insert or validate `superseded_by:` in an old memory's frontmatter.

    pending=True is remember()'s own call: the correction is the memory it is about to create under the same write
    lock, so its file cannot exist yet. The slug's shape is checked here, and _record_supersession checks the file
    after it is written and before the old memory is replaced (28 Sep: until then every remember --supersedes had
    failed since the 22 Sep pointer check, because the check ran before the new file existed)."""
    if pending:
        if not SLUG_RX.fullmatch(correction or ""):
            raise ValueError("superseded_by must be ONE slug, not %r" % (correction,))
    else:
        _require_pointer_target(correction, "superseded_by")
    fm = re.match(r"^(---\s*\n.*?\n)(---\s*\n)", body, re.S)
    if not fm:
        raise ValueError("the superseded memory has no frontmatter block")
    old = re.search(r"^\s*superseded_by:\s*([A-Za-z0-9_-]+)\s*$", fm.group(1), re.M)
    if old:
        if old.group(1) != correction:
            raise ValueError("memory is already superseded by %s" % old.group(1))
        return body
    return fm.group(1) + "superseded_by: " + correction + "\n" + fm.group(2) + body[fm.end():]


def _record_supersession(old_slug, correction_slug):
    """Atomically refresh the small old->current index used by both recall paths."""
    _require_pointer_target(correction_slug, "supersession index correction")
    rows = load_map(MEM, index_path=SUPERSEDED_INDEX, include_frontmatter=False)
    existing = rows.get(old_slug)
    if existing and existing != correction_slug:
        raise ValueError("supersession index already maps %s -> %s" % (old_slug, existing))
    rows[old_slug] = correction_slug
    os.makedirs(os.path.dirname(os.path.abspath(SUPERSEDED_INDEX)), exist_ok=True)
    tmp = "%s.tmp.%d.%d" % (SUPERSEDED_INDEX, os.getpid(), time.time_ns())
    try:
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as out:
            out.write("old\tcorrection\n")
            for old, new in sorted(rows.items()):
                out.write("%s\t%s\n" % (old, new))
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, SUPERSEDED_INDEX)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _maybe_compact_index(trigger=22000):
    """Auto-compact the live index when it outgrows the session-load budget.

    Called with the memory write lock HELD (from remember's index write).
    compact_index.py moves the oldest lines into INDEX_ARCHIVE.md; nothing is
    deleted, and recall reads both (see _archived_index_lines). Why: compaction
    was nightly-only, and 2026-08-09 showed a heavy fleet day appending ~20KB in
    five hours - the index crossed the ~24.4KB session-load limit the same
    afternoon it was trimmed. Fail-open: a compaction problem must never fail
    the memory write that triggered it."""
    import subprocess
    try:
        ix = os.path.join(MEM, "MEMORY.md")
        if os.path.getsize(ix) <= trigger:
            return
        out = subprocess.run(
            [sys.executable, os.path.join(DR, "compact_index.py"), "apply"],
            capture_output=True, text=True, timeout=60)
        note = (out.stdout or out.stderr or "").strip().splitlines()
        print("index auto-compacted: %s" % (note[-1] if note else "ok"))
    except Exception as exc:
        print("index auto-compact skipped: %s" % exc)


def _load_recall_policy():
    """Load explicit per-node retrieval modes without mutating immutable attachments.

    ``parent_only`` requires a direct lexical foothold in the node's index entry; attachment,
    graph, and semantic-only matches cannot activate it. ``disabled`` retires the node from
    deliberate recall completely. A malformed existing policy is a hard integrity error because
    silently ignoring it would reactivate routes that were intentionally withdrawn.
    """
    if not os.path.exists(RECALL_POLICY):
        raise FileNotFoundError(
            "required recall policy is missing; refusing to reactivate retired routes")
    st = os.lstat(RECALL_POLICY)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise ValueError("recall policy must be a regular unlinked file")
    with io.open(RECALL_POLICY, encoding="utf-8", errors="strict") as src:
        payload = json.load(src)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("recall policy schema_version must equal 1")
    nodes = payload.get("nodes", {})
    if not isinstance(nodes, dict):
        raise ValueError("recall policy nodes must be an object")
    policies = {}
    for slug, row in nodes.items():
        if not isinstance(slug, str) or not isinstance(row, dict):
            raise ValueError("recall policy entries must map node slugs to objects")
        mode = row.get("mode", "normal")
        if mode not in {"normal", "parent_only", "disabled"}:
            raise ValueError("invalid recall mode for %s: %r" % (slug, mode))
        groups = row.get("trigger_groups", [])
        if not isinstance(groups, list):
            raise ValueError("trigger_groups for %s must be a list" % slug)
        for group in groups:
            if (not isinstance(group, list) or len(group) < 2 or
                    any(not isinstance(term, str) or not term.strip() for term in group)):
                raise ValueError("each trigger group for %s must contain at least two terms" % slug)
        aliases = row.get("token_aliases", {})
        if not isinstance(aliases, dict):
            raise ValueError("token_aliases for %s must be an object" % slug)
        for source, replacements in aliases.items():
            if (not isinstance(source, str) or not source.strip() or
                    not isinstance(replacements, list) or not replacements or
                    any(not isinstance(term, str) or not term.strip()
                        for term in replacements)):
                raise ValueError("token_aliases for %s must map terms to non-empty term lists" % slug)
        blocked = row.get("blocked_phrases", [])
        if (not isinstance(blocked, list) or
                any(not isinstance(phrase, str) or len(re.findall(r"[a-z0-9]+", phrase.lower())) < 2
                    for phrase in blocked)):
            raise ValueError("blocked_phrases for %s must contain phrases of at least two terms" % slug)
        intent_gate = row.get("intent_gate")
        if intent_gate is not None:
            if not isinstance(intent_gate, dict):
                raise ValueError("intent_gate for %s must be an object" % slug)
            for key in (
                    "action_terms", "consume_terms", "plan_terms", "terminal_veto_terms",
                    "anaphora_terms", "ai_anchors", "component_terms", "human_terms",
                    "directive_leads", "descriptive_terms", "negators"):
                values = intent_gate.get(key)
                if (not isinstance(values, list) or not values or
                        any(not isinstance(term, str) or not term.strip() for term in values)):
                    raise ValueError("intent_gate.%s for %s must be a non-empty term list" %
                                     (key, slug))
            for key in ("canonical_groups", "referent_groups", "request_sequences",
                        "prohibition_sequences"):
                values = intent_gate.get(key)
                if (not isinstance(values, list) or not values or
                        any(not isinstance(group, list) or not group or
                            any(not isinstance(term, str) or not term.strip() for term in group)
                            for group in values)):
                    raise ValueError("intent_gate.%s for %s must be non-empty term groups" %
                                     (key, slug))
            for key in ("normalization_map", "action_normalization"):
                mapping = intent_gate.get(key, {})
                if (not isinstance(mapping, dict) or
                        any(not isinstance(source, str) or not source.strip() or
                            not isinstance(target, str) or not target.strip()
                            for source, target in mapping.items())):
                    raise ValueError("intent_gate.%s for %s must map terms to terms" %
                                     (key, slug))
            window = intent_gate.get("negation_window", 4)
            if not isinstance(window, int) or isinstance(window, bool) or not 1 <= window <= 8:
                raise ValueError("intent_gate.negation_window for %s must be in [1,8]" % slug)
        policies[slug] = {
            "mode": mode,
            "trigger_groups": groups,
            "token_aliases": aliases,
            "blocked_phrases": blocked,
            "intent_gate": intent_gate,
        }
    return policies


def _policy_tokens(query, policy):
    """Return raw query tokens enriched by explicitly reviewed policy aliases."""
    tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
    for source, replacements in policy.get("token_aliases", {}).items():
        if source.lower() in tokens:
            tokens.update(term.lower() for term in replacements)
    return tokens


def _policy_triggered(query, policy):
    """Return true when one explicit multi-term trigger group is fully present."""
    tokens = _policy_tokens(query, policy)
    return any(set(term.lower() for term in group) <= tokens
               for group in policy.get("trigger_groups", []))


def _policy_blocked(query, policy):
    """Honor explicit negated/non-action phrases before any positive routing evidence."""
    normalized = " ".join(re.findall(r"[a-z0-9]+", query.lower()))
    for phrase in policy.get("blocked_phrases", []):
        blocked = " ".join(re.findall(r"[a-z0-9]+", phrase.lower()))
        if blocked in normalized:
            return True
    return False


def _policy_parent_strength(words, index_line):
    """Score only a direct parent-index foothold, never full-text/BM25 attachment evidence."""
    if len(words) < 2 or not index_line:
        return 0.0
    cues = sr._distinctive_words(words)
    overlap = sum(1 for word in cues if word in index_line.lower())
    return float(overlap) if overlap >= 2 else 0.0


def _sequence_positions(tokens, sequence):
    """Return every start position of one contiguous normalized token sequence."""
    width = len(sequence)
    return [index for index in range(len(tokens) - width + 1)
            if tokens[index:index + width] == sequence]


# Repository paths and slash commands are not referents (2026-09-05). "the campaign at
# brain-repo\06_math_research\..." tokenized as an AI-memory referent, and
# "/goal work on the math brain" hid its verb behind the command word. Both are stripped before
# the gate reads the clause; a bare "20/80" or "and/or" (one separator) is left alone.
_INTENT_SLASH_COMMAND_RE = re.compile(r"^\s*/[a-z][\w-]*")
_INTENT_PATH_RE = re.compile(
    r"(?:[a-z]:)?(?:[\\/][\w.+~-]+){2,}[\\/]?|[a-z]:[\\/][\w.+~-]+|\bbrain-repo\b")


def _intent_gate_strength(query, gate):
    """Require an actual modification request and an AI persistent-system referent.

    This deliberately separates *what to do* from *what system it targets*. Merely describing
    historical changes, quoting an improvement phrase, or matching attached prose is not intent.
    Negation is evaluated around each action, and a later explicit no-change directive wins.
    """
    text = query.lower()
    text = _INTENT_SLASH_COMMAND_RE.sub(" ", text)
    text = _INTENT_PATH_RE.sub(" ", text)
    for source, target in (
            ("don't", "dont"), ("can't", "cant"), ("won't", "wont"),
            ("let's", "let us"), ("i'd", "i would"), ("we'd", "we would"),
            ("you'd", "you would")):
        text = text.replace(source, target)
    text = re.sub(r"[:;.!?\r\n]+", " clausebreak ", text)
    raw = re.findall(r"[a-z0-9]+", text)
    normalize = {str(key).lower(): str(value).lower()
                 for key, value in gate.get("normalization_map", {}).items()}
    action_normalize = {str(key).lower(): str(value).lower()
                        for key, value in gate.get("action_normalization", {}).items()}
    tokens = [action_normalize.get(normalize.get(token, token),
                                   normalize.get(token, token)) for token in raw]
    if not tokens:
        return 0.0

    canonical_groups = [[term.lower() for term in group]
                        for group in gate["canonical_groups"]]
    actions = {term.lower() for term in gate["action_terms"]}
    consume_actions = {term.lower() for term in gate["consume_terms"]}
    plan_terms = {term.lower() for term in gate["plan_terms"]}
    terminal_veto = {term.lower() for term in gate["terminal_veto_terms"]}
    anaphora = {term.lower() for term in gate["anaphora_terms"]}
    ai_anchors = {term.lower() for term in gate["ai_anchors"]}
    components = {term.lower() for term in gate["component_terms"]}
    human_terms = {term.lower() for term in gate["human_terms"]}
    negators = {term.lower() for term in gate["negators"]}
    descriptive = {term.lower() for term in gate["descriptive_terms"]}
    directive_leads = {term.lower() for term in gate["directive_leads"]}
    window = gate.get("negation_window", 4)
    clause_boundaries = {"clausebreak", "but", "however", "then"}

    def clause_span(position):
        prior = [index for index in range(position) if tokens[index] in clause_boundaries]
        later = [index for index in range(position + 1, len(tokens))
                 if tokens[index] in clause_boundaries]
        return ((prior[-1] + 1) if prior else 0,
                later[0] if later else len(tokens))

    def referent_in(scope):
        """Return (explicit canonical plan, AI cognitive-system referent) for one clause.

        Never combine a command in one clause with an unrelated referent elsewhere.  This
        is an authorization boundary, not a ranking hint: cross-clause token leakage must
        fail closed because it previously activated the governing node for unrelated work.
        """
        scope_set = set(scope)
        canonical_here = any(set(group) <= scope_set for group in canonical_groups)
        ai_anchor = bool(scope_set & ai_anchors)
        human_context = bool(scope_set & human_terms)
        brain_referent = "brain" in scope_set and not (human_context and not ai_anchor)
        grouped = any(set(term.lower() for term in group) <= scope_set
                      for group in gate["referent_groups"])
        system_here = canonical_here or brain_referent or (ai_anchor and bool(scope_set & components))
        if grouped and (ai_anchor or brain_referent) and not (human_context and not ai_anchor):
            system_here = True
        return canonical_here, system_here

    def previous_clause(start):
        if start <= 0:
            return []
        end = start - 1
        prior = [index for index in range(end) if tokens[index] in clause_boundaries]
        begin = (prior[-1] + 1) if prior else 0
        return tokens[begin:end]

    def action_has_target(position, consume=False):
        start, end = clause_span(position)
        scope = tokens[start:end]
        canonical_here, system_here = referent_in(scope)
        if consume:
            if canonical_here or (system_here and bool(set(scope) & plan_terms)):
                return True
        elif system_here:
            return True
        if not (set(scope) & anaphora):
            return False
        prior = previous_clause(start)
        canonical_prior, system_prior = referent_in(prior)
        if consume:
            return canonical_prior or (system_prior and bool(set(prior) & plan_terms))
        return system_prior

    def action_is_negated(position):
        start = max(clause_span(position)[0], position - window)
        for index in range(start, position):
            token = tokens[index]
            if token not in negators:
                continue
            if token == "not" and index + 1 < position and tokens[index + 1] in {
                    "only", "just", "merely"}:
                continue
            return True
        return False

    request_sequences = [[term.lower() for term in group]
                         for group in gate["request_sequences"]]
    leading_fillers = {"and", "also", "then", "please", "kindly", "now"}

    def action_is_requested(position):
        first = clause_span(position)[0]
        while first < len(tokens) and tokens[first] in leading_fillers:
            first += 1
        if position == first:
            return True
        if first < len(tokens) and tokens[first] in directive_leads:
            between = tokens[first + 1:position]
            if not (set(between) & descriptive) or "then" in between:
                return True
        for sequence in request_sequences:
            for start in _sequence_positions(tokens[first:position], sequence):
                start += first
                end = start + len(sequence)
                before_action = tokens[first:position]
                if not (set(before_action) & descriptive) or "then" in before_action:
                    return True
        return False

    action_positions = [index for index, token in enumerate(tokens) if token in actions]
    consume_positions = [index for index, token in enumerate(tokens) if token in consume_actions]
    requested_modifications = [
        position for position in action_positions
        if (not action_is_negated(position) and action_is_requested(position)
            and action_has_target(position))]
    requested_consumption = [
        position for position in consume_positions
        if (not action_is_negated(position) and action_is_requested(position)
            and action_has_target(position, consume=True))]
    requested = sorted(set(requested_modifications + requested_consumption))

    prohibition_positions = []
    for sequence in gate["prohibition_sequences"]:
        normalized_sequence = [action_normalize.get(normalize.get(term.lower(), term.lower()),
                                                normalize.get(term.lower(), term.lower()))
                               for term in sequence]
        prohibition_positions.extend(
            start + len(normalized_sequence) - 1
            for start in _sequence_positions(tokens, normalized_sequence))
    if requested_modifications:
        last_modification = max(requested_modifications)
        later_negated_mutation = any(
            position > last_modification and tokens[position] in terminal_veto
            and action_is_negated(position) and action_has_target(position)
            for position in action_positions)
        if ((prohibition_positions and max(prohibition_positions) > last_modification)
                or later_negated_mutation):
            return 0.0
    if requested:
        return 3.0 + min(2.0, float(len(requested)))

    # An exact short canonical-program name remains a route, but prose *about* the name does not.
    token_set = set(tokens)
    canonical = any(set(group) <= token_set for group in canonical_groups)
    if canonical and not (token_set & negators) and not (token_set & descriptive):
        ignorable = {"the", "current", "program", "please", "our"}
        compact = [token for token in tokens if token not in ignorable]
        if any(compact == group for group in canonical_groups):
            return 3.0
    return 0.0


_SLUG_NAME_TOKENS = {}


def _slug_name_tokens(slug):
    """The memory's name as bm25 tokens - underscores split, date parts dropped, stopwords removed."""
    st = _SLUG_NAME_TOKENS.get(slug)
    if st is None:
        try:
            from bm25 import tokens as _bm25_tokens
            st = frozenset(_bm25_tokens(" ".join(w for w in slug.split("_") if w and not w.isdigit())))
        except Exception:
            st = frozenset()
        _SLUG_NAME_TOKENS[slug] = st
    return st


def _display_rows(scored, n, floor=0.02, floor_break=False):
    """The rows recall shows: at most n, in the order given, skipping any row whose display score is at or below the
    floor. On the bm25-first path the list runs in segment order (the BM25 block, then the lexical, graph and semantic
    tails), not by display score, so a low row is skipped, never the end of the list; until 28 Sep a low row ended it
    and hid later tail rows above the floor (scheduled audit account01/B02 R1). floor_break=True keeps that old stop
    (BRAIN_RECALL_FLOOR_BREAK=1)."""
    out = []
    # Compaction anchors can be a third of the memory files, and one session's compactions repeat the same words, so
    # on some questions they fill half the rows. BRAIN_RECALL_COMPACTION_PER_SESSION keeps the best-placed N per
    # session; 0 (the default) means no cap. A cap of 3 measured no better than none, so it stays off.
    try:
        cap = int(os.environ.get("BRAIN_RECALL_COMPACTION_PER_SESSION", "0"))
    except ValueError:
        cap = 0
    per = {}
    for row in scored:
        if len(out) >= n:
            break
        if row[0] <= floor:
            if floor_break:
                break
            continue
        if cap > 0:
            m = _COMPACTION_SLUG.match(row[4])
            if m:
                if per.get(m.group(1), 0) >= cap:
                    continue
                per[m.group(1)] = per.get(m.group(1), 0) + 1
        out.append(row)
    return out


_COMPACTION_SLUG = re.compile(r"^compaction_((?:claude|codex)_[0-9a-z]+)_\d{8}T\d{6}$")


def recall(query, n=8):
    """Ranked recall for a question (prints). The R02 speed layer (_fc_layer) is installed for the call only. The R11
    query encoder starts on its worker thread here and is always joined before return, early returns included; the
    verified sidecar is shared by the two meaning lanes for this call only (_full_emb_scope)."""
    _full_emb_scope(True)
    _qemb_start(query)
    try:
        with _fc_layer():
            return _recall_body(query, n)
    finally:
        _qemb_finish()
        _full_emb_scope(False)


def _recall_body(query, n=8):
    idx = _archived_index_lines()
    idx.update(sr._index_lines())     # live entries override archived twins
    words = sr._words(query)
    seeds, lex = sr._seeds_from_prompt(words, idx)
    # Some older integrations supplied a mapping for the lexical-match collection.  The
    # public contract is set-like, so normalize once at the boundary before policy filtering.
    lex = set(lex)
    known_slugs = None

    try:
        recall_policy = _load_recall_policy()
    except Exception as exc:
        print("BRAIN RECALL POLICY ERROR: %s: %s" % (type(exc).__name__, exc))
        return 2
    disabled = {slug for slug, row in recall_policy.items() if row["mode"] == "disabled"}
    parent_only = {slug for slug, row in recall_policy.items() if row["mode"] == "parent_only"}
    policy_seeds = {}
    for slug in parent_only:
        intent_gate = recall_policy[slug].get("intent_gate")
        if intent_gate is not None:
            strength = _intent_gate_strength(query, intent_gate)
        else:
            blocked = _policy_blocked(query, recall_policy[slug])
            strength = 0.0 if blocked else _policy_parent_strength(words, idx.get(slug, ""))
            if not blocked and _policy_triggered(query, recall_policy[slug]):
                strength = max(strength, 3.0)
        if strength:
            policy_seeds[slug] = strength
            seeds[slug] = max(seeds.get(slug, 0.0), strength)
            lex.add(slug)
        else:
            # sr._seeds_from_prompt includes a full-text BM25 arm.  Such a hit is useful for
            # normal nodes but cannot masquerade as the direct-parent evidence this policy
            # requires, including when the matching bytes came from an attached document.
            seeds.pop(slug, None)
            lex.discard(slug)
    # A NORMAL node may carry trigger_groups too (2026-09-23, T11). When one whole group is in the query the
    # node is pinned above the BM25 block the way a parent-only foothold is; otherwise it ranks as usual.
    # Built for the standing-orders hubs: T11 found his film orders reaching an agent 35 of 35 times through
    # the hourly prompt block and 24 through recall, because each law memory ranks low on a task's own words
    # and the hub that lists them all ranked nowhere on his 24 real film prompts. BRAIN_RECALL_PINS=0 opts out.
    if os.environ.get("BRAIN_RECALL_PINS", "1") != "0":
        for slug, row in recall_policy.items():
            if row["mode"] != "normal" or not row.get("trigger_groups") or slug in policy_seeds:
                continue
            if not _policy_blocked(query, row) and _policy_triggered(query, row):
                policy_seeds[slug] = 3.0
                seeds[slug] = max(seeds.get(slug, 0.0), 3.0)
                lex.add(slug)
    for slug in disabled:
        seeds.pop(slug, None)
        lex.discard(slug)

    # Attached text is part of its parent node's memory, not an inert filing cabinet.
    # Search the live manifests on every deliberate recall so a new attachment is reachable
    # immediately, even before the nightly BM25 rebuild. Integrity failure is a stop condition:
    # silently skipping a damaged "full notes" file recreates the exact harm this lane closes.
    # One bundle that fails verification is left out of the search and named, not a stop for every query (28 Sep,
    # scheduled audit account03/B02 r68, B02-R1-ATTACHMENT-CORRUPTION-GLOBAL-RECALL-STOP-001): its text and scores never
    # count, the line below says the attachment corpus is incomplete and which bundles, and the rest of recall runs.
    # Until then one broken bundle anywhere ended every deliberate recall before BM25 ranked anything.
    attachment_failures = []
    try:
        attachment_hits = node_docs.search_nodes(MEM, query, limit=10, failures=attachment_failures)
    except Exception as exc:
        print("BRAIN NODE DOCUMENT ERROR: attachment retrieval is unsafe: %s: %s" %
              (type(exc).__name__, exc))
        return 2
    if attachment_failures:
        print("BRAIN NODE DOCUMENT WARNING: INCOMPLETE_ATTACHMENT_CORPUS - %d attachment bundle(s) failed verification and "
              "were left out of this recall, their text and scores counting for nothing: %s. Check them with python "
              "brain.py verify-attachments." % (len(attachment_failures),
                                         ", ".join("%s (%s)" % f for f in attachment_failures[:12])))
        # The seeds above were drawn before the bundles were verified, and spread_recall's BM25 arm reads the published
        # index, which can still hold a failed bundle's text (B02-R23 below). A failed parent keeps only the seed its own
        # index line or file name earns, by spread_recall's overlap rule; a policy or pinned seed stays as it was given.
        _own_words = sr._distinctive_words(words) if len(words) >= 2 else []
        for _slug in sorted({s for s, _e in attachment_failures}):
            if _slug not in seeds or _slug in policy_seeds:
                continue
            _line = (idx.get(_slug) or "").lower()
            _own = sum(1 for w in _own_words if w in _line)
            if _own < 2:
                _own = sum(1 for w in _own_words if len(w) >= 4 and w in _slug.lower())
            if _own >= 2:
                seeds[_slug] = float(_own)
            else:
                seeds.pop(_slug, None)
                lex.discard(_slug)
    attachment_hits = [
        (slug, strength) for slug, strength in attachment_hits
        if slug not in disabled and slug not in parent_only
    ]
    for slug, strength in attachment_hits:
        if slug not in sr.SKIP:
            seeds[slug] = max(seeds.get(slug, 0.0), strength)
            lex.add(slug)
    # A fresh attachment is absent from the nightly BM25 index.  Merely appending it after
    # BM25's 40-row block made the advertised "immediate" lane invisible at the normal n=8.
    # Promote only strong direct-document matches: one exact term for a one-term query, both
    # terms for a two-term query, and roughly half the terms for longer queries.  Weak/common
    # attachment overlap remains a tail signal and cannot jump a real BM25 result.
    attachment_query_terms = len(set(node_docs._tokens(query)))
    attachment_promote_floor = (1.0 if attachment_query_terms <= 1 else
                                max(2.0, 0.55 * attachment_query_terms))
    # The lane exists for attachments the NIGHTLY INDEX has not seen yet. Once the nightly
    # BM25 rebuild has indexed an attachment (its text is part of the parent's BM25 document),
    # bm25 already ranks that parent with proper length normalisation, and promoting it again
    # above the whole bm25 block is double counting - measured 2026-09-02: the 100-prompt
    # coverage ledger and the 25 KB canonical prompt sat at rank 1 on 7 of 40 real owner
    # prompts and displaced the right memory on 7 of 14 positive controls, because a long
    # document that quotes every order matches every query about an order. So: promote only
    # an attachment the index has not read yet; an indexed one stays a tail signal. "Not read yet" is measured
    # from the start of the scan that produced the index (bm25.scan_reference), not from the index file's mtime:
    # an incremental refresh re-reads a parent whose attachment changed since that start, and until 28 Sep a
    # refresh that did not re-read it still made the index newer than the attachment, so the promotion stopped
    # while the index lacked the text (account02/B02 R1, B02-R1-ATTACHMENT-REFRESH-DROPS-LIVE-PROMOTION-001).
    try:
        from bm25 import INDEX as _bm25_index_path, scan_reference as _bm25_scan_reference
        _index_read = _bm25_scan_reference(_bm25_index_path)
    except Exception:
        _index_read = 0.0
    promoted_attachment_hits = [
        (slug, strength) for slug, strength in attachment_hits
        if strength >= attachment_promote_floor
        and node_docs.newest_attachment_mtime(MEM, slug) >= _index_read
    ][:3]

    # no 2-word seed anywhere: fall back to single-word overlap, but DISCIPLINED - demand the
    # overlap scale with query length, and keep only the strongest few (a 1-word match against
    # 1400 memories is noise, not recall; caught on the first live query, 213 junk seeds)
    if not seeds and words:
        need = max(1, len(words) // 3)
        slugs = _corpus_slugs()
        known_slugs = set(slugs)
        lines = {s: (idx.get(s, "") + " " + s).lower() for s in slugs}
        # rarity-weight the overlap so 'watchdog' outvotes 'short' (df over index lines)
        import math
        df = {w: sum(1 for s in slugs if w in lines[s]) for w in words}
        cand = []
        for slug in slugs:
            if slug in disabled:
                continue
            if slug in parent_only and slug not in policy_seeds:
                continue
            hits = [w for w in words if w in lines[slug]]
            if len(hits) >= need:
                cand.append((sum(math.log(1.0 + len(slugs) / (1.0 + df[w])) for w in hits), slug))
        cand.sort(reverse=True)
        seeds = {slug: ov for ov, slug in cand[:25]}

    adj, deg = sr._load_graph()
    act = {}
    if seeds and adj:
        act, _parent = sr._spread(seeds, adj, deg)
    sem = _semantic_scores(seeds)
    # The query-meaning lane (on since R11, 13 Oct; BRAIN_RECALL_QUERY_EMBED=0 turns it off) is read where it is first
    # used, at the fusion after the bm25 block (R19, 13 Oct): the work in between runs while the helper process encodes
    # the query, where recall used to stand and wait for the vector (0.31 and 0.35 s a call in the R19 timelines). Nothing
    # in the lane prints, and the legacy blend (BRAIN_RECALL_BLEND=1) never reads it.
    qsem = None
    sal = sr._load_salience()
    tier = _tiers()

    if not seeds:
        print("no lexical foothold in the index for that query - try different words, or grep the")
        print("memory dir directly:  grep -ril \"term\" \"%s\"" % MEM)
        return

    lmax = max(seeds.values()) or 1.0
    amax = max(act.values()) if act else 1.0
    # Collapse every stale candidate onto the terminal correction BEFORE ranking.  The correction
    # inherits the stale memory's lexical/graph/semantic foothold, so even a query phrased entirely
    # in the old fact's vocabulary reaches current truth.  This closes the premise-awareness hole
    # measured by eval_stale_fact (a correction was absent from 5/11 stale-premise candidate pools).
    superseded = _load_superseded()
    # Resolving one candidate used to relist the entire memory directory.  A normal recall can
    # resolve hundreds of candidates, turning one O(|corpus|) snapshot into O(|candidates| *
    # |corpus|) directory work.  Keep a single, internally consistent view for this recall while
    # retaining _current_slug's lazy snapshot for standalone callers.
    if known_slugs is None:
        known_slugs = set(_corpus_slugs())
    components = {}
    lineage = {}
    for slug in set(list(seeds) + list(act) + list(sem)):
        if slug in sr.SKIP or slug in disabled:
            continue
        if slug in parent_only and slug not in policy_seeds:
            continue
        ls = seeds.get(slug, 0.0) / lmax
        gs = (act.get(slug, 0.0) / amax) if act else 0.0
        ss = sem.get(slug, 0.0)
        current = _current_slug(slug, superseded, known_slugs)
        if current in sr.SKIP or current in disabled:
            continue
        if current in parent_only and current not in policy_seeds:
            continue
        prev = components.get(current, (0.0, 0.0, 0.0))
        components[current] = (max(prev[0], ls), max(prev[1], gs), max(prev[2], ss))
        if current != slug:
            lineage.setdefault(current, set()).add(slug)

    # Ranking is BM25-FIRST since 2026-07-15 (OQ#1164, implemented deliberately as its own
    # verdict asked): rank bm25(query) in bm25's own fine-grained order, then a graph tail,
    # then a semantic tail. The old 0.625/0.25/0.125 blend NORMALISED bm25 into a 0..1 term
    # and diluted it - measured worse on BOTH deliberate ground truths (eval_bm25first,
    # re-run day-of-implementation on the grown corpus: wiki-link hit@10 0.364 vs 0.232,
    # MRR 0.163 vs 0.099, n=423; real-prompt hit@10 ~0.59 vs 0.51). Wiki-partners are mostly
    # topical, so bm25 finds them directly; true cross-topic association is the proactive
    # hook's job (spread_recall stays graph-dominant). Kept from the old path, deliberately:
    # superseded-collapse (a stale hit surfaces its correction), SKIP, and the
    # unconscious-tier gate (log-substrate stubs must not outrank woven memories - the eval's
    # ground truth never contained stubs, so bm25-first earned no license to promote them).
    # It is a gate on lexical evidence, not a demotion (see the bm25 block). Escape hatch:
    # BRAIN_RECALL_BLEND=1 restores the blend unchanged.
    use_blend = os.environ.get("BRAIN_RECALL_BLEND") == "1"
    # USAGE TILT (2026-07-30, dream task off p119 + base_activation --eval receipt):
    # boost-only, ub in [1.0, 1.15] - a workhorse wins ties, never floods, and a
    # memory ABSENT from the trace gets exactly 1.0 (never penalized for being
    # untracked; the boost-only floor avoids inverting fresh vs rarely-read).
    # BRAIN_RECALL_USAGE=0 opts out. Same-inputs receipt: receipt_before/after
    # tsv, 2026-07-30 session (12 fixed queries).
    _usage = _load_usage_norm() if os.environ.get("BRAIN_RECALL_USAGE", "1") != "0" else {}
    _note_demote = os.environ.get("BRAIN_RECALL_NOTE_DEMOTE", "1") != "0"   # see the bm25 block below
    _name_cov = os.environ.get("BRAIN_RECALL_NAME_COVERAGE", "1") != "0"   # see the bm25 block below
    _qcover_on = os.environ.get("BRAIN_RECALL_QCOVER", "1") != "0"        # see the bm25 block below
    _qtoks = set()
    _absent_terms = []   # query words no memory contains (see the bm25 block and the [absent] line below)
    scored = []
    _lex_path, _lex_s = None, None   # which lexical path ran and how long the block took (the recall log's lex, lex_s)
    if not use_blend:
        _lex_t0 = time.time()
        try:
            from bm25 import BM25, QueryNormalizer
            # WRITE-TIME LANE (2026-09-02): a memory file newer than the bm25 index is invisible to this
            # block, and this block ranks first - a box-icing law filed at 01:2xZ was dark for the whole
            # fleet until the index was rebuilt by hand. The 15-minute BrainBM25Refresh task and
            # remember() keep the index current; this gate is the self-heal when the task has not run
            # (index older than 15 min): one incremental refresh, a few seconds, then recall as usual.
            _mem_payload = None
            _ovl_bm = None
            try:
                import bm25 as _bm25mod
                _idx_age = time.time() - os.path.getmtime(_bm25mod.INDEX)
                # 2026-09-14 02:2x: the trigger is no longer the index's AGE but whether any memory file is newer
                # than it (fresh_memories: one scandir, ~0.1-1.4 s). remember()'s awaited indexing times out under
                # load (every DEFERRED_TIMEOUT tonight), so a memory written a minute ago was dark to deliberate
                # recall until the 15-minute task ran; now the next recall merges it in memory and hands the
                # publish to the detached refresh. An index with nothing fresh costs one scandir and moves on.
                _fresh_mem, _gone_mem = [], []
                # R02 (12 Oct): only the refresh below reads these, so with BRAIN_RECALL_REFRESH=0 the scan is skipped;
                # otherwise the indexed-slug list comes from the speed layer instead of the 45 MB payload.
                if os.environ.get("BRAIN_RECALL_REFRESH", "1") != "0":
                    try:
                        _fresh_mem, _gone_mem = _fc_fresh_memories(_bm25mod)
                    except Exception:
                        _fresh_mem = ["?"] if _idx_age > 900 else []
                if (_fresh_mem or _gone_mem) and os.environ.get("BRAIN_RECALL_REFRESH", "1") != "0":
                    if os.environ.get("BRAIN_RECALL_PUBLISH", "defer") == "defer":
                        # 2026-09-13: the publish is the cost (99 of 147 s measured), not the indexing.
                        # Merge the fresh memories in memory, score THIS query on the merged payload
                        # (identical ranking to the published index), and let a detached windowless
                        # refresh publish the generation. BRAIN_RECALL_PUBLISH=sync restores the old
                        # synchronous publish on the read path.
                        # R16 (13 Oct 2026): when the scanned generation has its postings sidecar and no attachment
                        # bundle failed above, the same merge is read from the sidecar and the fresh documents
                        # (_fc_overlay) instead of the 45 MB payload; otherwise the payload merge runs as before.
                        _ovl = None if attachment_failures else _fc_overlay(_bm25mod, _fresh_mem, _gone_mem)
                        if _ovl is not None:
                            _ovl_bm, _added, _gone = _ovl
                        else:
                            _mem_payload, _added, _gone = _bm25mod.refresh_in_memory(out=_bm25mod.INDEX)
                        if _mem_payload is not None or _ovl_bm is not None:
                            _pid = _bm25mod.spawn_detached_refresh()
                            print("  [bm25] index was %.0f min old: merged %d fresh memor%s (dropped %d) in memory;"
                                  " publish handed to a detached refresh%s"
                                  % (_idx_age / 60, _added, "y" if _added == 1 else "ies", _gone,
                                     (" (pid %d)" % _pid) if _pid else " (spawn failed; the 15-min task will publish)"))
                    else:
                        _added, _gone = _bm25mod.refresh(quiet=True)
                        if _added or _gone:
                            print("  [bm25] index was %.0f min old: indexed %d fresh memor%s, dropped %d"
                                  % (_idx_age / 60, _added, "y" if _added == 1 else "ies", _gone))
            except Exception as _rf_exc:
                _mem_payload = None
                _ovl_bm = None
                print("  [warn] bm25 refresh skipped (%s: %s)" % (type(_rf_exc).__name__, _rf_exc))
            # A bundle that failed verification above is re-read here before anything is scored, refresh or no refresh:
            # the index may hold its text from before the damage, and a damage that kept the bundle's mtime is never
            # fresh to the refresh (28 Sep, account03/B02 r71, B02-R23-ATTACHMENT-STALE-BM25-DIGEST-BYPASS-001: the
            # parent ranked on the rejected text at exactly its healthy score while the warning said it counted for
            # nothing). Only the failed parents are re-read; nothing is published.
            _failed_parents = sorted({s for s, _e in attachment_failures})
            if _failed_parents:
                try:
                    import bm25 as _bm25scrub
                    _mem_payload = _bm25scrub.scrub_in_memory(_failed_parents, out=_bm25scrub.INDEX, payload=_mem_payload)
                except Exception as _sc_exc:
                    print("  [warn] could not re-read the %d parent(s) whose attachments failed (%s: %s); their old "
                          "attachment text may still rank them" % (len(_failed_parents), type(_sc_exc).__name__, _sc_exc))
            if _ovl_bm is not None:
                _bm, _lex_path = _ovl_bm, "overlay"
            elif _mem_payload is not None:
                _bm, _lex_path = BM25.from_payload(_mem_payload), "merge"
            else:
                _bm = BM25()
                _lex_path = "sidecar" if _bm.__dict__.get("_sc") is not None else "payload"
            # 2026-07-24: the proactive seeder (spread_recall) spell-normalizes OOV query tokens
            # (measured beneficial, eval_prompt_recall); this deliberate bm25-first block did not, so a
            # typo silently dropped its term. 2026-07-28 (owner: ship prevention fixes): default ON -
            # the owner's own queries carry typos and BM25 scored them raw. BRAIN_RECALL_SPELLFIX=0 opts out.
            _q = QueryNormalizer(_bm.df).normalize(query) if os.environ.get("BRAIN_RECALL_SPELLFIX", "1") != "0" else query
            # ABSENT WORDS (2026-09-23, round 6 T1). Asked about "moving the live production stack onto ARM servers", recall
            # returned plausible neighbours and the agent could rule the premise out only by grep. A query word that NO
            # memory contains - and that the spell normaliser did not correct to a known word - is named after the
            # results: either the premise is not in the brain, or the corpus says it in other words. Cheap: the
            # document-frequency table is already loaded. BRAIN_RECALL_ABSENT=0 turns it off.
            if os.environ.get("BRAIN_RECALL_ABSENT", "1") != "0":
                try:
                    import bm25 as _bm25tok
                    _norm_toks = set(_bm25tok.tokens(_q))
                    for _t in _bm25tok.tokens(query):
                        if (_t not in _absent_terms and len(_t) >= 4 and not _t.isdigit()
                                and _bm.df.get(_t, 0) == 0 and _t in _norm_toks):
                            _absent_terms.append(_t)
                except Exception:
                    _absent_terms = []
            # BRAIN_LEX_TOPK (2026-09-14, staged for measurement): the lexical seed cap is a CLIFF - the gold of
            # "when should beta be zero" sits at lexical rank exactly 40 and fell to unranked under two separate
            # index perturbations tonight because it dropped off the seed list, not because it scored worse.
            # Measured 02:1x on the 56-question owner-voice bank: 40 / 60 / 80 read identical (hit@1 28, hit@5 43,
            # one rank moved); 60 landed as the margin - neutral on the bank, and the gold that lived at rank 40
            # no longer sits on the edge.
            bm_hits = _bm.score(_q, int(os.environ.get("BRAIN_LEX_TOPK", "60") or 60))
            if _lex_path == "overlay" and _ovl_bm._fallback is not None:
                _lex_path = "overlay-fallback"
            try:
                _qtoks = set(_bm25mod.tokens(_q))
            except Exception:
                _qtoks = set()
        except Exception as _bm_exc:
            # A dead bm25 block is LEXICAL BLINDNESS - recall then leans on
            # graph/semantic tails alone and can print "nothing above the floor"
            # for content the corpus plainly holds (task #319: a FakeBM25 without
            # .df died here SILENTLY for days). Say it loudly; never swallow.
            print("  [warn] bm25 block failed (%s: %s) - lexical channel dark this recall"
                  % (type(_bm_exc).__name__, _bm_exc))
            bm_hits = []
            _lex_path = "failed"
        _lex_s = round(time.time() - _lex_t0, 3)
        # bm25 block: the whole-corpus content match, in bm25's own order (the eval's method -
        # hits outside the seed/spread candidate set are legitimate and rank on content alone).
        # Strong live attachment matches precede the stale nightly block, with a synthetic score
        # just above its peak.  This is not a generic attachment boost: the coverage floor above
        # is the gate, and weak matches still enter only through the lexical tail.
        order = []
        seen_rank = set()
        demoted = []
        _head_set = set()   # the rows promoted into the head: policy routes and fresh attachments (see HEAD BY PROMOTION)
        bm_peak = max((score for _slug, score in bm_hits), default=1.0) or 1.0
        policy_seed_hits = sorted(
            policy_seeds.items(),
            key=lambda item: (-item[1], item[0]),
        )
        policy_peak = max((strength for _slug, strength in policy_seed_hits), default=1.0) or 1.0
        attachment_peak = max((strength for _slug, strength in promoted_attachment_hits),
                              default=1.0) or 1.0
        # A parent-only route has deliberately surrendered attachment/graph/semantic activation.
        # When its precise parent index entry *does* match directly, promote that policy-approved
        # foothold above broad corpus BM25 results so the safety boundary does not make the route
        # unreachable.
        for raw_slug, strength in policy_seed_hits:
            slug = _current_slug(raw_slug, superseded, known_slugs)
            if slug in sr.SKIP or slug in seen_rank:
                continue
            seen_rank.add(slug)
            order.append((slug, bm_peak + 2.0 + strength / policy_peak))
            _head_set.add(slug)
        for raw_slug, strength in promoted_attachment_hits:
            if raw_slug in disabled or raw_slug in parent_only:
                continue
            slug = _current_slug(raw_slug, superseded, known_slugs)
            if slug in sr.SKIP or slug in disabled or slug in seen_rank:
                continue
            if slug in parent_only and slug not in policy_seeds:
                continue
            seen_rank.add(slug)
            order.append((slug, bm_peak + strength / attachment_peak))
            _head_set.add(slug)
        for raw_slug, bsc in bm_hits:
            if raw_slug in disabled:
                continue
            if raw_slug in parent_only and raw_slug not in policy_seeds:
                continue
            slug = _current_slug(raw_slug, superseded, known_slugs)
            if slug in sr.SKIP or slug in disabled or slug in seen_rank:
                continue
            if slug in parent_only and slug not in policy_seeds:
                continue
            seen_rank.add(slug)
            if tier.get(slug) == "unconscious":
                # THE UNCONSCIOUS TIER IS A GATE ON LEXICAL EVIDENCE (stated 28 Sep, scheduled audit account03/B02 r67,
                # B02-R1-UNCONSCIOUS-TIER-LEXICAL-SUPPRESSION-001). A log-substrate stub found by its words is marked
                # seen here, so its bm25 score and its lexical component count for nothing: it is listed after every
                # tail with a display score of 0.5 * graph + 0.25 * semantic, and without graph or semantic support
                # it is not shown. The comment here used to call this a demotion. A demotion was built and measured
                # (the stub last, its lexical evidence at 0.35): on 30 queries made of stubs' own names, each stub the
                # top bm25 hit, it showed none at 8 or 60 rows, the same as the gate, because the tails always fill
                # the list first; on the 56 owner-voice questions and 40 recent real ones a stub reached the bm25 top
                # 60 once and showed never. Census: brain_graph/results/UNCONSCIOUS_DEMOTION_CENSUS_2026-09-28.json.
                # A stub is read by name: python brain.py node <slug>.
                demoted.append(slug)
                continue
            # WORKING-NOTE DEMOTION (2026-09-03). job_* memories are the job cycle's per-task readmes -
            # working records, not distilled knowledge - and on the owner's own questions they outranked
            # the law that answered him because they happen to carry his incidental words ("allowed",
            # "anymore", "stuff"): on a 25-question owner-voice bank the right memory was first 5/25 and
            # in the top five 15/25. A mild factor on their bm25 score reorders only near-ties: measured
            # offline on the same bm25 top-40, hit@5 15 -> 18 of 25 with no loss, and on the real-prompt
            # episodes (eval_prompt_recall_fullstack's 90, 166 of whose 692 read slugs ARE job_ notes)
            # hit@10 +1 / -0. First-pass factor, not swept (25 items would overfit). Bank + lab:
            # eval_brain_recall_fresh.py; campaign_2026-09-03_20h/fresh_test_20260903/recall_rerank_lab.py.
            # BRAIN_RECALL_NOTE_DEMOTE=0 opts out.
            if _note_demote and slug.startswith("job_"):
                bsc = bsc * 0.85
            # NAME COVERAGE (2026-09-11 evening). When the query carries most of the words in the memory's
            # NAME, the name is the match: "the cpu load of the live protected production stack" covers four of the five
            # words of cpu_map_of_the_live_trading_stack and still ranked 8th, because BM25 saturates per
            # term and long bodies carry the same words many times over. Coverage c = |query words in the
            # name| / |name words| (dates and stopwords excluded); at c >= 0.6 the bm25 score is multiplied
            # by 1 + c. Measured in the recall lab on the bm25 top-40: owner-voice bank hit@1 22 -> 26,
            # hit@5 36 -> 38 of 50, paired +3 / -0; the 67 real-prompt episodes hit@5 0.209 -> 0.239,
            # hit@10 unchanged, MRR 0.128 -> 0.141. BRAIN_RECALL_NAME_COVERAGE=0 opts out.
            if _name_cov and _qtoks:
                _st = _slug_name_tokens(slug)
                if _st:
                    _c = len(_st & _qtoks) / len(_st)
                    if _c >= 0.6:
                        bsc = bsc * (1.0 + _c)
            # QUESTION-SIDE COVERAGE (2026-09-14). MEASURED, then flipped ON the same day - it ships
            # DEFAULT ON; BRAIN_RECALL_QCOVER=0 opts out. (This header read "default OFF" until 09-14
            # 18:3x, four lines above the line saying =0 opts out - the staging note outlived the staging.)
            # The block above asks how much of the NAME the question covers, and divides by the name's
            # length - so a long slug can never score well however exactly it answers. This asks the
            # other direction: how much of the QUESTION does this name account for. That is the
            # direction the math store measured as its best ranking signal on 2026-09-13, and it is
            # applied smoothly to every candidate rather than at a threshold, because the threshold is
            # what makes the long names unreachable. Tokens match on a five-character prefix too, so
            # "bill" reaches "billing", "read" reaches "reading", "committing" reaches "committed" -
            # three of the misses this was built for turn on exactly that.
            # Selected in the lab (lexical lane, bm25 top-60, the 56-question bank): baseline hit@1 23 /
            # hit@5 40, the live name-coverage rule 27 / 42, this 32 / 45 - and the lab is a SELECTOR,
            # not the verdict, because real recall walks the graph and blends the semantic lane from
            # these seeds. END TO END on the full stack, one frozen index generation, the control arm
            # reproducing its baseline exactly: hit@1 28 -> 32, hit@5 43 -> 45 of 56, paired +5 / -1 at
            # hit@1 and +3 / -1 at hit@5, twelve of fifty-six questions moving at all. The one loss at
            # hit@5 is "what time does the live rebalancing bot trade now", 5 -> 7.
            # FALSIFIER, on a bank it was NOT tuned on: 58 real owner prompts scored against what those
            # sessions went on to read, through this same shipped path (eval_recall_real_prompts.py) -
            # hit@8 14/58 unchanged, MRR 0.1188 -> 0.1203. That is NEUTRAL, not confirmation: it says
            # the gain on his own questions is not bought by damage elsewhere, nothing more.
            # BRAIN_RECALL_QCOVER=0 opts out.
            if _qcover_on and _qtoks:
                _st = _slug_name_tokens(slug)
                if _st:
                    _hit = 0
                    for _t in _qtoks:
                        if _t in _st or (len(_t) >= 5 and any(len(_u) >= 5 and _u[:5] == _t[:5] for _u in _st)):
                            _hit += 1
                    bsc = bsc * (1.0 + _hit / float(len(_qtoks)))
            order.append((slug, bsc))
        if _note_demote or _name_cov or _qcover_on:
            order.sort(key=lambda item: -item[1])   # a demoted note may now sit below its neighbours
        # POLICY ROUTES STAY ON TOP (2026-09-23). A policy seed enters at bm_peak + 2 + strength, an ADDITIVE margin,
        # while the name- and question-coverage boosts above MULTIPLY a bm25 score by up to 2x; after the re-sort a
        # well-named bm25 hit could outrank a policy route. recall_policy_regression read three canonical positives
        # at rank 2 on 09-23 ("port ... vanilla brain safely" under vanilla_brain_what_works). Each policy seed
        # is lifted back above the best other score, in its own strength order.
        _pol = {}
        for _raw, _st in policy_seed_hits:
            _cur = _current_slug(_raw, superseded, known_slugs)
            _pol[_cur] = max(_pol.get(_cur, 0.0), _st)
        if _pol and order:
            _rest = max((b for s, b in order if s not in _pol), default=bm_peak)
            _lift = {s: _rest + 2.0 + _pol[s] / policy_peak for s in _pol}
            order = sorted([(s, _lift.get(s, b)) for s, b in order], key=lambda item: -item[1])
        # HEAD BY PROMOTION (28 Sep, scheduled audit account03/B01 auto63, B01-R1-BOOSTED-BM25-CROSSES-PROTECTED-HEAD-
        # BOUNDARY-20260928). The query-semantic and graph blends below split the order into a head they leave alone and a
        # body they blend, and they drew the line at the lexical peak: every row scoring above bm_peak was head. The name-
        # and question-coverage boosts multiply a bm25 score by up to four, so a well-named ordinary hit crossed that line,
        # skipped both blends and was left out of the graph lane's seeds (it seeds from the body's top ten), and it could
        # outrank a fresh attachment, whose promotion sits at most one point above the peak and, unlike a policy route,
        # was never lifted again after the boosts. The head is now the set of rows promoted into it; an attachment is
        # lifted back above the best body row when a boosted row passed it, below every policy route; a boosted bm25 row
        # stays in the body with its boosted score. BRAIN_RECALL_HEAD_STRUCT=0 restores the score line exactly.
        _struct_head = os.environ.get("BRAIN_RECALL_HEAD_STRUCT", "1") != "0"
        _crossed = sum(1 for s, b in order if b > bm_peak and s not in _head_set)
        _att = {s for s in _head_set if s not in _pol}
        _body_best = max((b for s, b in order if s not in _head_set), default=None)
        _att_passed = sum(1 for s, b in order if s in _att and _body_best is not None and _body_best >= b)
        if _struct_head and _att_passed:
            _alift = _body_best + 1.0 - bm_peak
            order = sorted([(s, b + _alift if s in _att else b) for s, b in order], key=lambda item: -item[1])
            if _pol:
                _rest = max((b for s, b in order if s not in _pol), default=bm_peak)
                _lift = {s: _rest + 2.0 + _pol[s] / policy_peak for s in _pol}
                order = sorted([(s, _lift.get(s, b)) for s, b in order], key=lambda item: -item[1])

        def _in_head(slug, score):
            return slug in _head_set if _struct_head else score > bm_peak
        # QUERY-SEMANTIC FUSION (2026-09-13; since 13 Oct the default is the Z FUSION below and this convex form
        # runs only under BRAIN_RECALL_QSEM_FUSE=convex - see _query_semantic_scores). The policy
        # and attachment promotions keep the head (their scores sit above bm_peak). The bm25 body and the
        # query-semantic list are fused as a CONVEX BLEND of min-max normalised scores: fused = (1-w) * bm25_norm
        # + w * cosine_norm, a memory absent from a lane scoring 0 there, w = BRAIN_RECALL_QSEM_W (0.40). Swept
        # offline on the 56-question owner-voice bank (lanes/m7_brain/FUSION_SWEEP*.txt, 2026-09-13): reciprocal-
        # rank fusion at equal weight read +8/-4 at hit@1 and +10/-5 at hit@5 - real losses, because a lane's
        # rank-1 is trusted whatever its margin - while the blend at 0.40 read +6/-0 and +6/-0 (0.45 starts to
        # lose, 0.30 gains less). A bm25 winner keeps its place unless the semantic lane ALSO ranks the rival
        # high; a question bm25 cannot reach gets its answer from the lane that can. Fused scores are rescaled
        # onto the bm25 body's own range so the display and the 0.05 floor below read as before. Off, this
        # block is a no-op and the order is byte-identical.
        # Z FUSION, ON BY DEFAULT (R11, 13 Oct 2026; BRAIN_RECALL_QSEM_FUSE=convex keeps the blend above). The convex
        # blend scored every body memory outside the lane's own top ten at 0 on the meaning side, so the lane could only
        # promote its ten and quietly pushed the rest of the body down. Here every candidate - the bm25 body plus the
        # lane's top 60 nominations - is scored on BOTH channels: bm25 as a z-score over the body (a nomination bm25
        # never ranked gets the body's lowest), the query cosine as a z-score over the candidates, fused =
        # (1 - w) z_bm25 + w z_cos with w = BRAIN_RECALL_QSEM_W (0.3), rescaled onto the body's range. A memory with no
        # current sidecar row - one written or edited since the last build, which runs every three hours (compaction
        # nodes are most of them) - has no meaning score: the cosine z-scores are taken over the candidates that have
        # one, and such a memory is scored on its bm25 z alone. Filling it with the mean cosine (z 0) instead sank it
        # under any rival with an above-mean cosine: 72 fresh compaction nodes asked in their own description, end to
        # end, hit@1 35 -> 24, 33 down and none up. With this rule 83 such nodes read hit@1 39 -> 32, hit@5 79 -> 73,
        # and every row that passes one is an older compaction node of its own session, which has a vector. Encoding
        # the missing rows during recall costs about 0.5 s a passage on a loaded laptop, so since R13 (13 Oct) a hidden
        # 15-minute task encodes them instead (fresh_vectors.py -> memory_embeddings_fresh.npz, read by _fresh_overlay):
        # a fresh memory is scored like any other once the overlay holds it, and the bm25-alone rule covers only the
        # minutes before that. BRAIN_RECALL_FRESH=0 ignores the overlay. The query is encoded with bge's
        # retrieval instruction (_qlane_text). MEASURED on the 22 Sep 999-item recall bank, dev half (494 items,
        # bm25-first lab without the graph lane), hit@1 / hit@5 / hit@10 / MRR: reworded questions 111/176/210/0.289
        # -> 113/184/220/0.299; the questions in the memory's own words 360/458/474/0.815 -> 369/455/475/0.829. w 0.25
        # to 0.35 is a plateau; 0.45 gains more on reworded questions and loses 10 at hit@5 on the verbatim ones.
        # Without the instruction the lane alone finds 58 of the reworded answers in its top five, with it 73. The
        # end-to-end A/B, the held half and BRAINDEV are in improve_1012/fronts/R11/NOTES.md.
        if qsem is None:
            qsem = _query_semantic_scores(query)
        _qfuse = _qlane_fuse()
        if _qfuse == "z":
            if _QLANE.get("query") == query and _QLANE.get("cos") is not None:
                try:
                    _qw = float(os.environ.get("BRAIN_RECALL_QSEM_W", "0.3"))
                except Exception:
                    _qw = 0.3
                _head = [(s, b) for s, b in order if _in_head(s, b)]
                _body = [(s, b) for s, b in order if not _in_head(s, b)]
                _head_slugs = {s for s, _ in _head}
                _nom = {}
                for _s, _c in sorted(qsem.items(), key=lambda it: -it[1]):
                    _cur = _current_slug(_s, superseded, known_slugs)
                    if _cur in sr.SKIP or _cur in disabled or _cur in _head_slugs:
                        continue
                    if _cur in parent_only and _cur not in policy_seeds:
                        continue
                    if tier.get(_cur) == "unconscious":
                        continue
                    _nom[_cur] = max(_nom.get(_cur, -1.0), float(_c))
                if _body and _qw > 0:
                    _bd = dict(_body)
                    _U = [s for s, _ in _body] + [s for s in _nom if s not in _bd]
                    _cs = _qlane_cosines([s for s in _U if s not in _nom])
                    _cs.update(_nom)
                    if _cs:
                        _cl = list(_cs.values())
                        _bl = list(_bd.values())
                        _bm = sum(_bl) / len(_bl)
                        _bsd = (sum((x - _bm) ** 2 for x in _bl) / len(_bl)) ** 0.5 or 1.0
                        _cm = sum(_cl) / len(_cl)
                        _csd = (sum((x - _cm) ** 2 for x in _cl) / len(_cl)) ** 0.5 or 1.0
                        _hi, _lo = max(_bl), min(_bl)
                        _bfloor = (_lo - _bm) / _bsd
                        _f = {}
                        for _s in _U:
                            _zb = (_bd[_s] - _bm) / _bsd if _s in _bd else _bfloor
                            _c = _cs.get(_s)
                            _f[_s] = _zb if _c is None else (1.0 - _qw) * _zb + _qw * (_c - _cm) / _csd
                        _fs = sorted(_f.items(), key=lambda it: (-it[1], it[0]))
                        _fhi, _flo = _fs[0][1], _fs[-1][1]
                        _fspan = (_fhi - _flo) or 1.0
                        _body = [(s, _lo + (_hi - _lo) * (v - _flo) / _fspan) for s, v in _fs]
                        order = _head + _body
                        seen_rank.update(_nom)
        elif qsem:
            try:
                _qw = float(os.environ.get("BRAIN_RECALL_QSEM_W", "0.4"))
            except Exception:
                _qw = 0.4
            _head = [(s, b) for s, b in order if _in_head(s, b)]
            _body = [(s, b) for s, b in order if not _in_head(s, b)]
            _head_slugs = {s for s, _ in _head}
            _qcos = {}
            for _s, _c in sorted(qsem.items(), key=lambda it: -it[1]):
                _cur = _current_slug(_s, superseded, known_slugs)
                if _cur in sr.SKIP or _cur in disabled or _cur in _head_slugs:
                    continue
                if _cur in parent_only and _cur not in policy_seeds:
                    continue
                if tier.get(_cur) == "unconscious":
                    continue
                _qcos[_cur] = max(_qcos.get(_cur, 0.0), float(_c))
            if _qcos and _body:
                _hi = max(b for _, b in _body)
                _lo = min(b for _, b in _body)
                _span = (_hi - _lo) or 1.0
                _bnorm = {s: (b - _lo) / _span for s, b in _body}
                _chi = max(_qcos.values())
                _clo = min(_qcos.values())
                _cspan = (_chi - _clo) or 1.0
                _cnorm = {s: (c - _clo) / _cspan for s, c in _qcos.items()}
                _fused = {s: (1.0 - _qw) * _bnorm.get(s, 0.0) + _qw * _cnorm.get(s, 0.0) for s in set(_bnorm) | set(_cnorm)}
                _fs = sorted(_fused.items(), key=lambda it: -it[1])
                _body = [(s, _lo + (_hi - _lo) * f) for s, f in _fs]
                order = _head + _body
                seen_rank.update(_qcos)
        # USE-GROWN GRAPH LANE (2026-09-26, his 04:28 order: build the edge and diffusion part now "and then put a
        # paramter that will tune this vs the simple work thing that is working now"). graph_lane.py walks the graph grown
        # from every touch agents make (claude-9209cf81's capture and store: words, tools, files, folders, memories read
        # and written, notes served, reflexes, errors) by personalised PageRank (diffusion.py) from the query's words and
        # bm25's top memories, and blends its ranking into the bm25 body the way the query-semantic lane is blended: a
        # convex mix of min-max scores, rescaled onto the body's range. The policy/attachment head keeps its place.
        # THE DIAL: BRAIN_GRAPH_LAMBDA (0 = the order before this block, byte-identical; 1 = the graph's order alone),
        # default and store path in brain_graph_dial.json. MEASURED 26 Sep on 948 pilot questions (one per memory,
        # written in other words; 46 pct of the answers are graph nodes): bm25 alone hit@5 412, MRR 0.352; the graph
        # alone MRR 0.01 from the words, 0.19 with bm25's top five as seeds; over restart 0.3/0.6, lift 0/0.5/1, both
        # seedings and both fusions no blend beat lambda 0 (best, convex 0.1: MRR 0.352, hit@5 +2, 16 answers up and 73
        # down), and claude-825b1305's held-out 968-question bank agreed (bm25 hit@5 0.344, best lambda 0). So the
        # default is 0 until a measurement earns it a weight: brain_improvement/campaign_2026-09-26_graph_lane.
        # The walk is prebuilt (python graph_lane.py --build); recall never builds it. Any failure leaves the order
        # as it was.
        try:
            import graph_lane as _glane
            _gdial = _glane.dial()
        except Exception:
            _gdial = {"lambda": 0.0}
        # LIVE FROM 26 Sep 16:xx (his 15:5x order: "implemented live ... with a weight of not being zero, and the edges
        # growing and learning"): lambda 0.1 by default, the use-grown walk now combined with a walk over the memories'
        # own links (live sidecar: [[links]], superseded_by, the E1 typed links; measured hit@5 0.348 vs 0.342 at 0.1),
        # every recall logged to ~/.claude/brain_graph/live/recall_log.jsonl, and graph_learn.py moving lambda within
        # 0.05..0.4 toward the weight under which the memories agents then opened ranked higher.
        if _gdial.get("lambda", 0.0) > 0 and order:
            try:
                _ghead = [(s, b) for s, b in order if _in_head(s, b)]
                _gbody = [(s, b) for s, b in order if not _in_head(s, b)]
                _ghs = {s for s, _ in _ghead}
                _gbody0 = list(_gbody)
                try:
                    _guse = _glane.ranked(query, allowed=known_slugs, lex_top=_gbody[:10], d=_gdial)
                except Exception:
                    _guse = []
                try:
                    _glink = _glane.link_ranked(_gbody[:10]) if _gdial.get("links", True) else []
                except Exception:
                    _glink = []
                _graw = _glane.combine(_guse, _glink, float(_gdial.get("mix_links", 0.5)))
                _gl = []
                _gseen = set()
                for _s, _v in _graw:
                    _cur = _current_slug(_s, superseded, known_slugs)
                    if _cur in sr.SKIP or _cur in disabled or _cur in _ghs or _cur in _gseen:
                        continue
                    if _cur in parent_only and _cur not in policy_seeds:
                        continue
                    if tier.get(_cur) == "unconscious":
                        continue
                    _gseen.add(_cur)
                    _gl.append((_cur, _v))
                if _gl and _gbody:
                    _gbody = _glane.fuse(_gbody, _gl, float(_gdial["lambda"]), _gdial.get("fuse", "convex"))
                    order = _ghead + _gbody
                    seen_rank.update(_gseen)
                _glane.log_recall(query, float(_gdial["lambda"]), _gbody0, _gl, [s for s, _ in order[:10]],
                                  {"n_use": len(_guse), "n_link": len(_glink), "mix": _gdial.get("mix_links", 0.5),
                                   "head_struct": _struct_head, "head_n": len(_ghead), "crossed": _crossed,
                                   "att_passed": _att_passed, "lex": _lex_path, "lex_s": _lex_s})
            except Exception as _g_exc:
                print("  [warn] graph lane skipped (%s: %s)" % (type(_g_exc).__name__, _g_exc))
        # A newly attached document is searchable immediately, before the nightly BM25 rebuild.
        # Keep lexical-only parents in the ranked tail; the previous graph/semantic-only tails
        # accidentally discarded exactly those fresh attachment seeds.
        ltail = sorted(((ls, s) for s, (ls, gs, ss) in components.items()
                        if s not in seen_rank and ls > 0), reverse=True)
        lnorm = {s: ls for ls, s in ltail}
        seen_rank.update(s for _, s in ltail)
        gtail = sorted(((gs, s) for s, (ls, gs, ss) in components.items()
                        if s not in seen_rank and gs > 0), reverse=True)
        seen_rank.update(s for _, s in gtail)
        stail = sorted(((ss, s) for s, (ls, gs, ss) in components.items()
                        if s not in seen_rank and ss > 0), reverse=True)
        seen_rank.update(s for _, s in stail)
        # PROVISIONAL DISCOUNT ON THE LIVE PATH (2026-09-14). _STATUS_WEIGHT was written to "stop an
        # intermediate simulation from outranking older validated knowledge", and it is applied in the
        # legacy blend below - but the bm25-first path that replaced that blend on 2026-07-15 never
        # applied it, so the discount has been dead on every live recall for two months. Measured on the
        # 56-question owner-voice bank that night: of the top-three memories served on the 13 questions
        # that MISS, 14 of 39 are `provisional`, while the golds are almost all verified or owner-stated
        # (9 verified, 3 owner-stated, 1 provisional). Re-applied here inside the bm25 block ONLY and to
        # the block's own score, so a provisional memory with a real lexical lead keeps its place and a
        # marginal one falls; the tails, the segment order and every non-provisional score are untouched.
        # BRAIN_RECALL_STATUS_ORDER=0 restores the two-month behaviour exactly.
        if order and os.environ.get("BRAIN_RECALL_STATUS_ORDER", "0") != "0":
            try:
                _w = [(s, b * _STATUS_WEIGHT.get(_status_from_index(s, idx), 1.0)) for s, b in order]
                if any(abs(a[1] - b[1]) > 1e-12 for a, b in zip(_w, order)):
                    order = sorted(_w, key=lambda it: -it[1])
            except Exception:
                pass
        ranked = ([s for s, _ in order] + [s for _, s in ltail] +
                  [s for _, s in gtail] + [s for _, s in stail]
                  + demoted)
        bmax = max((b for _, b in order), default=1.0) or 1.0
        bnorm = {s: b / bmax for s, b in order}
        for i, slug in enumerate(ranked):
            ls, gs, ss = components.get(slug, (0.0, 0.0, 0.0))
            # display score: bm25 block keeps its normalized content score (floored above the
            # 0.02 display cutoff - it earned the rank); tails keep their own weak-signal
            # values so the existing cutoff still suppresses genuine noise.
            disp = bnorm.get(slug)
            if disp is not None:
                disp = max(0.05, disp)
            else:
                disp = max(0.5 * lnorm.get(slug, 0.0), 0.5 * gs + 0.25 * ss)
            status = _status_from_index(slug, idx)
            disp *= 1.0 + 0.15 * _usage.get(slug, 0.0)   # usage tilt (boost-only)
            scored.append((disp - i * 1e-6, ls, gs, ss, slug, status))
    else:
        for slug, (ls, gs, ss) in components.items():
            # legacy blend (pre-07-15): eval_deliberate_recall placed the word-query basin at
            # high-lexical / low-semantic; superseded by bm25-first above (eval_bm25first).
            score = 0.625 * ls + 0.25 * gs + 0.125 * ss
            salb = 0.9 + 0.2 * (sal.get(slug, 50.0) / 100.0)   # gentle salience tilt, never a gate
            if tier.get(slug) == "unconscious":
                salb *= 0.35   # log-substrate stubs: reachable, but they must not outrank woven-in memories
            status = _status_from_index(slug, idx)
            ub = 1.0 + 0.15 * _usage.get(slug, 0.0)   # usage tilt (boost-only), same knob as bm25 path
            scored.append((score * salb * ub * _STATUS_WEIGHT.get(status, 1.0), ls, gs, ss, slug, status))
        scored.sort(reverse=True)

    # GATED QUESTION-LANE MERGE. When the question lane's own top match stands clear of its second (cosine margin
    # >= BRAIN_RECALL_GATE_T, e.g. 0.02), the top twenty shown are merged with the lane's top 200 by reciprocal rank
    # (k=10); otherwise nothing changes. The z fusion above takes the lane at weight 0.3, so a memory the lane is sure
    # of can still sit at 2 or 3 under bm25 neighbours, mostly fresh memories whose words the body has not seen
    # repeated. An ungated merge loses (the lane's rank 1 trusted whatever its margin); the margin is the guard. On
    # questions about recent memories the gate helped (one pre-registered set of 39: hit@1 18 -> 24, none lost); on a
    # bank of older questions it moved one answer. Off by default: BRAIN_RECALL_GATE_T=0 keeps the order unchanged.
    try:
        _gate_t = float(os.environ.get("BRAIN_RECALL_GATE_T", "0") or 0)
    except Exception:
        _gate_t = 0.0
    if _gate_t > 0 and scored and _qlane_on():
        try:
            _gsc = _query_semantic_scores(query, k=200)
            _gD, _gC = [], []
            for _s in sorted(_gsc, key=lambda s: -_gsc[s]):
                _cur = _current_slug(_s, superseded, known_slugs)
                if _cur not in _gD:
                    _gD.append(_cur)
                    _gC.append(_gsc[_s])
            if len(_gC) > 1 and _gC[0] - _gC[1] >= _gate_t:
                _gD = [s for s in _gD if s not in sr.SKIP and s not in disabled and tier.get(s) != "unconscious"]
                _gL = [r[4] for r in _display_rows(scored, 20)]
                _grr = {}
                for _lst in (_gL, _gD):
                    for _j, _s in enumerate(_lst):
                        _grr[_s] = _grr.get(_s, 0.0) + 1.0 / (10 + _j + 1)
                _gtop = sorted(_grr, key=lambda s: -_grr[s])[:len(_gL)]
                _rows = {r[4]: r for r in scored}
                _vals = sorted((_rows[s][0] for s in _gL), reverse=True)
                _new = []
                for _i, _s in enumerate(_gtop):
                    _r = _rows.get(_s)
                    if _r is None:
                        _ls, _gs, _ss = components.get(_s, (0.0, 0.0, 0.0))
                        _r = (0.0, _ls, _gs, _ss, _s, _status_from_index(_s, idx))
                    _new.append((_vals[_i],) + tuple(_r[1:]))
                _taken = set(_gtop)
                scored = _new + [r for r in scored if r[4] not in _taken]
        except Exception as _gate_exc:
            print("  [warn] gated lane merge skipped (%s: %s)" % (type(_gate_exc).__name__, _gate_exc))

    _dump = os.environ.get("BRAIN_RECALL_DUMP_SCORED")          # evaluation only: the list the display reads, in order
    if _dump:
        try:
            with open(_dump, "a", encoding="utf-8") as _fh:
                _fh.write(json.dumps({"q": query, "n": n, "scored": [[round(r[0], 6), r[4]] for r in scored[:120]]},
                                     ensure_ascii=False) + "\n")
        except Exception:
            pass
    _floor_break = os.environ.get("BRAIN_RECALL_FLOOR_BREAK", "0") != "0"
    displayed = []
    for sc, ls, gs, ss, slug, status in _display_rows(scored, n, floor_break=_floor_break):
        # an index line without a tag says nothing about status; the memory's own frontmatter does, and a
        # retraction is the one status a reader must not miss (F50) - bounded to the displayed rows
        if status == "legacy" and _frontmatter_retracted(slug):
            status = "retracted"
        displayed.append((sc, ls, gs, ss, slug, status))
    _print_strong_node_advisory(row[4] for row in displayed)
    print("recall: %r  (lex %d seeds | graph %s | semantic %s)" % (
        query, len(seeds), "on" if act else "off", "on" if sem else "off"))
    shown = 0
    for sc, ls, gs, ss, slug, status in displayed:
        # the memory's own description, not its MEMORY.md line (2026-09-23): the index line repeats the slug twice
        # inside a 200-character cap, so the snippet ran a median 73 characters against the description's 237 and
        # a question-first description lost its answer; read for the displayed rows only
        d = _full_description(slug) or sr._desc(slug, idx) or "(no description)"
        # a FIRE WHEN memory's description is its trigger, so the snippet echoes the question back (Test A:
        # eight `node` opens just to read the rule); show the lesson line beside it, for displayed rows only
        if re.match(r"\s*(?:\[[A-Z][A-Z ,+-]*\]\s*)*FIRE\b", d):
            _lesson = _lesson_line(slug)
            if _lesson:
                d = d[:90].rstrip() + " -> " + _lesson
        why = "+".join(t for t, v in (("lex", ls), ("graph", gs), ("sem", ss)) if v > 0.15) or "weak"
        flags = []
        if status != "legacy":
            flags.append(status)
        if lineage.get(slug):
            flags.append("supersedes " + ",".join(sorted(lineage[slug])[:3]))
        _part = _part_flag(slug, superseded, known_slugs)
        if _part:
            flags.append(_part)
        _moved = _moved_flag(slug, superseded, known_slugs, _part)
        if _moved:
            flags.append(_moved)
        label = why + (("; " + "; ".join(flags)) if flags else "")
        print("  %.2f [%s] %s" % (sc, label, slug))
        print("        %s" % (d if len(d) <= SNIPPET_MAX else d[:SNIPPET_MAX].rstrip() + " ..."))
        shown += 1
    if _absent_terms:
        print("  [absent] no memory contains: %s - if the question rests on %s, the brain may not hold it at all; "
              "otherwise the corpus says it in other words, so rephrase." % (
                  ", ".join(_absent_terms[:6]), "it" if len(_absent_terms) == 1 else "them"))
    if shown == 0:
        print("  nothing above the floor - the corpus may genuinely not hold this. grep to be sure.")
    else:
        # LOW-SEPARATION advisory (2026-09-22, campaign_2026-09-22_recall F31/F36/F36-B). Nothing computable
        # from the QUERY predicts a miss (best in-sample threshold 0.674 against a 0.668 base rate), but the
        # shape of what came back does: when the top hit and the fifth sit within 0.10, or the lowest returned
        # score is above 0.85, four in five such queries had the right memory outside the top 5 - and on a
        # bank it was never fitted on it fired on 1.4 pct of the questions that succeeded. A hint, never a
        # verdict: the caller's cheapest move is one more call in the corpus's own words.
        if len(displayed) >= 5:
            _top, _fifth, _low = displayed[0][0], displayed[4][0], displayed[-1][0]
            if (_top - _fifth) <= 0.10 or _low >= 0.85:
                print("  [low-separation] nothing in this list stands out (top-fifth %.2f, lowest %.2f) - the"
                      " words of your question may not be the corpus's words; rephrase in the vocabulary a"
                      " memory about this would use and ask again." % (_top - _fifth, _low))
                # 26 Sep (claude-825b1305): the rephrasing, done by a small model and measured - three Haiku rewrites
                # plus the question put the source in a top five for 132 of 300 reworded questions against 104
                # (memory a_cheap_rewrite_before_recall_finds_the_note_far_more_often_2026_09_25).
                print("  [rewrite] python %s %s  (three rewrites and the question, interleaved; about 20 s)" % (
                    os.path.join(DR, "recall_rewrite.py"), json.dumps(query)))
        print("read one WITH all attached documents:  python %s node <slug>" %
              os.path.join(DR, "brain.py"))

        # The top deliberate hit is the node the ranker actually selected. Put all of its
        # bounded auto-read documents directly in this command's output: an instruction to
        # "remember to read the file" is documentation, not invocation.
        top_slug = displayed[0][4]
        try:
            # The per-candidate notes go out BEFORE the top node's auto-read block: that block can run to
            # kilobytes, and an agent reading this output through a capped read lost the notes that came
            # after it (Test A tool report, 2026-09-22). The block itself is unchanged.
            for _sc, _ls, _gs, _ss, candidate, _status in displayed[1:]:
                try:
                    docs = node_docs.summaries(MEM, candidate)
                except Exception as _doc_exc:       # one candidate's broken bundle is named, the others still listed
                    print("  attachment note: %s's attachments failed verification (%s); not read" %
                          (candidate, type(_doc_exc).__name__))
                    continue
                if docs:
                    print("  attachment note: %s carries %d document(s); read via brain.py node %s" %
                          (candidate, len(docs), candidate))
            attached = node_docs.render_auto_documents(MEM, top_slug)
            if attached:
                print("\n--- AUTO-READ DOCUMENTS ATTACHED TO TOP NODE %s ---" % top_slug)
                print(attached, end="" if attached.endswith("\n") else "\n")
                print("--- END AUTO-READ DOCUMENTS (all hashes/byte counts verified) ---")
        except Exception as exc:
            print("BRAIN NODE DOCUMENT ERROR: selected node attachments were not fully read: %s: %s" %
                  (type(exc).__name__, exc))
            return 2
    return 0


def neighbors(slug, n=10):
    idx = sr._index_lines()
    if not os.path.exists(os.path.join(MEM, slug + ".md")):
        print("no such memory: %s" % slug)
        return
    superseded = active_supersessions(MEM, index_path=SUPERSEDED_INDEX)
    current = superseded.get(slug, slug)
    if current != slug:
        print("NOTICE: %s is superseded by %s; read the correction before using the old fact." %
              (slug, current))
    adj, deg = sr._load_graph()
    graph_scores = {}
    for neighbor, weight in adj.get(current, ()):
        endpoint = superseded.get(neighbor, neighbor)
        if endpoint != current:
            graph_scores[endpoint] = max(graph_scores.get(endpoint, 0.0), weight)
    g = sorted(graph_scores.items(), key=lambda t: -t[1])[:n]
    sem_raw = _semantic_scores({current: 1.0})
    semantic_scores = {}
    for neighbor, score in sem_raw.items():
        endpoint = superseded.get(neighbor, neighbor)
        if endpoint != current:
            semantic_scores[endpoint] = max(semantic_scores.get(endpoint, 0.0), score)
    s = sorted(semantic_scores.items(), key=lambda t: -t[1])[:n]
    _print_strong_node_advisory(
        [current] + [nb for nb, _weight in g] + [nb for nb, _score in s]
    )
    print("neighbors of %s" % current)
    print("  graph (co-read/wiki-link weight):")
    for nb, w in g:
        print("    %.2f %s" % (w, nb))
    if s:
        print("  semantic (calibrated cosine, sidecar %s):" % "on")
        for nb, c in s:
            print("    %.2f %s" % (c, nb))
    if not g and not s:
        print("  (isolated node - no edges, not in sidecar)")


def _render_memory_text(slug, mtype, desc, body, status, evidence, created,
                        supersedes="", created_at=""):
    evidence_line = json.dumps((evidence or "").strip(), ensure_ascii=False)
    supersedes_line = ("  supersedes: %s\n" % supersedes) if supersedes else ""
    # created_at (2026-09-22, round 5 W4): the filing TIME beside the filing date - two orders filed the same day
    # could not be put in order (all nine inverted verdicts of the F54 owner-pair judge). Emitted only when given,
    # so a memory filed before it re-renders byte-identically (repair mode compares bytes).
    created_at_line = ("  created_at: %s\n" % created_at) if created_at else ""
    return ("---\n"
            "name: %s\n"
            "description: %s\n"
            "metadata:\n"
            "  type: %s\n"
            "  created: %s\n"
            "%s"
            "  status: %s\n"
            "  evidence: %s\n"
            "%s"
            "---\n\n%s\n" %
            (slug, desc, mtype, created, created_at_line, status, evidence_line,
             supersedes_line, body or ""))


def _filing_clock():
    """(created, created_at) from ONE local clock reading, so the date and the time can never straddle midnight."""
    import datetime as _dt
    now = _dt.datetime.now().astimezone()
    return now.strftime("%Y-%m-%d"), now.isoformat(timespec="seconds")


_INDEX_TAGS = re.compile(r"^(?:\s*\[[A-Z][A-Z -]*\]\s*)+")
_INDEX_QUESTION = re.compile(r"(?i)^(?:why|how|what|which|who|whom|whose|where|when|is|are|was|were|do|does|did|can|"
                             r"could|should|would|will|has|have|had|may|might|must)\b")


def _index_row_text(desc, budget):
    """The description text an index row carries (2026-09-23).

    MEMORY.md is the one brain surface every agent loads whether it recalls or not, and a row is the slug written twice
    plus about 46 characters of description. Since 2026-09-22 descriptions open with the question they answer ("Is the
    GPU box up, and can we get in? Up since ..."), so 32 of 81 rows showed the question and lost the answer to the
    line cap. The slug already names the topic, so a description that opens with a question is indexed by its answer;
    the file's frontmatter keeps the question, which is what recall matches. Leading [STATUS] tags are dropped because
    the row adds its own (14 of 81 rows carried two). BRAIN_INDEX_ANSWER_FIRST=0 restores the plain prefix.
    """
    try:
        from index_text import index_row_text
    except ImportError:   # brain.py loaded by file path from another folder: its own folder may not be on sys.path
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from index_text import index_row_text
    return index_row_text(desc, budget)


def _same_slug_index_lines(lines, slug):
    pattern = re.compile(
        r"^\s*- \[%s\.md\]\(%s\.md\) - " %
        (re.escape(slug), re.escape(slug)))
    return [line for line in lines if pattern.match(line)]


def _assert_regular_unlinked(path, label="memory"):
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("%s is not a regular non-link file" % label)
    attrs = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if attrs & reparse:
        raise ValueError("%s is a Windows reparse point" % label)


def _assert_directory_unlinked(path, label="memory directory"):
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("%s is not a regular non-link directory" % label)
    attrs = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if attrs & reparse:
        raise ValueError("%s is a Windows reparse point" % label)


def _read_regular_utf8(path, label):
    _assert_regular_unlinked(path, label)
    with open(path, "rb") as src:
        raw = src.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("%s is not strict UTF-8" % label)
    return raw, text


def _insert_index_entry(lines, entry):
    """Return a copy with one canonical row and no line-boundary corruption."""
    result = list(lines)
    eol = "\r\n" if any(line.endswith("\r\n") for line in result) else "\n"
    canonical_entry = entry.rstrip("\r\n") + eol
    for i, line in enumerate(result):
        if line.startswith("# "):
            if not line.endswith(("\r", "\n")):
                result[i] = line + eol
            result.insert(i + 1, canonical_entry)
            break
    else:
        result.insert(0, canonical_entry)
    return result


def _is_live_memory_root():
    return (
        os.path.normcase(os.path.realpath(os.path.abspath(MEM)))
        == os.path.normcase(os.path.realpath(os.path.abspath(LIVE_MEM)))
    )


def _mesh_assert(mesh_script, mesh_agent, resource):
    resolved_script = os.path.realpath(os.path.abspath(mesh_script))
    canonical_script = os.path.realpath(os.path.abspath(CANONICAL_MESH_SCRIPT))
    if _is_live_memory_root() and (
            os.path.normcase(resolved_script)
            != os.path.normcase(canonical_script)):
        raise RuntimeError(
            "live memory repair requires the canonical agent_mesh.py")
    _assert_regular_unlinked(resolved_script, "mesh assertion script")
    result = subprocess.run(
        [sys.executable, resolved_script, "--json", "assert",
         "--agent", mesh_agent,
         "--resource", resource],
        capture_output=True, text=True, timeout=30)
    if result.returncode:
        detail = (result.stdout + "\n" + result.stderr).strip()
        raise RuntimeError("mesh assert failed for %s: %s" % (resource, detail))
    try:
        receipt = json.loads(result.stdout)
    except Exception:
        raise RuntimeError(
            "mesh assert returned no structured receipt for %s" % resource)
    if (
            receipt.get("agent") != mesh_agent
            or not isinstance(receipt.get("claims"), list)
            or not any(
                row.get("resource") == resource
                and row.get("mode") in ("write", "exclusive")
                for row in receipt["claims"]
                if isinstance(row, dict))):
        raise RuntimeError(
            "mesh assert receipt does not bind agent/resource for %s" % resource)


def _repair_memory_index(slug, mtype, desc, body, status, evidence,
                         expected_sha256, mesh_agent, mesh_script):
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256 or ""):
        print("--expected-sha256 must be a lowercase 64-character SHA-256")
        return 1
    if not mesh_agent or not mesh_script:
        print("--repair-index requires --mesh-agent and --mesh-script")
        return 1
    if not os.path.isfile(mesh_script):
        print("--mesh-script does not exist: %s" % mesh_script)
        return 1
    path = os.path.join(MEM, slug + ".md")
    ix = os.path.join(MEM, "MEMORY.md")
    if not os.path.exists(path):
        print("--repair-index requires an existing memory: %s" % path)
        return 1
    tag = "[%s] " % status.upper()
    # INDEX LINE BUDGET (2026-08-08): cap the WHOLE line, not just the
    # description. The pointer prefix alone runs 93-176 chars because the
    # slug is written twice, so desc[:200] produced lines up to 376 and
    # MEMORY.md reached 44 KB against a ~24.4 KB read limit - every
    # session was loading only part of its own index, silently.
    _link = "- [%s.md](%s.md) - %s" % (slug, slug, tag)
    entry = "%s%s\n" % (_link, _index_row_text(desc, 200 - len(_link)))
    node_resource = "memory:brain/%s" % slug
    index_resource = "memory:brain/index"
    tmp = None
    try:
        # Assert before taking the file lock so a missing lease never makes
        # other legitimate memory writers wait.
        _mesh_assert(mesh_script, mesh_agent, node_resource)
        _mesh_assert(mesh_script, mesh_agent, index_resource)
        with _memory_write_lock():
            _assert_directory_unlinked(MEM)
            _assert_regular_unlinked(path)
            with open(path, "rb") as src:
                raw = src.read()
            actual_sha256 = hashlib.sha256(raw).hexdigest()
            if actual_sha256 != expected_sha256:
                raise ValueError("memory SHA-256 differs from the reviewed value")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError("memory is not strict UTF-8")
            created_matches = re.findall(
                r"(?m)^  created:\s*(\d{4}-\d{2}-\d{2})\s*$", text)
            if len(created_matches) != 1:
                raise ValueError("memory must carry exactly one ISO created date")
            created_at_matches = re.findall(r"(?m)^  created_at:\s*(\S+)\s*$", text)
            if len(created_at_matches) > 1:
                raise ValueError("memory must carry at most one created_at time")
            expected = _render_memory_text(
                slug, mtype, desc, body, status, evidence,
                created_matches[0],
                created_at=(created_at_matches[0] if created_at_matches else "")).encode("utf-8")
            if raw != expected:
                raise ValueError(
                    "memory bytes do not match the complete intended metadata/body")
            index_raw, index_text = _read_regular_utf8(ix, "memory index")
            lines = index_text.splitlines(True)
            matches = _same_slug_index_lines(lines, slug)
            canonical_matches = [
                line for line in matches
                if line.startswith("- ")
                and line.rstrip("\r\n") == entry.rstrip("\r\n")
            ]
            if len(matches) == 1 and matches == canonical_matches:
                print(json.dumps({
                    "status": "ALREADY_INDEXED",
                    "slug": slug,
                    "memory_sha256": actual_sha256,
                    "index_sha256": hashlib.sha256(index_raw).hexdigest(),
                }, sort_keys=True))
                return 0
            if matches:
                raise ValueError(
                    "same-slug MEMORY.md row is mismatched or duplicated")
            lines = _insert_index_entry(lines, entry)
            rendered_index = "".join(lines).encode("utf-8")
            # Reassert immediately before the only mutation. Repair mode never
            # writes, replaces, or deletes the memory itself.
            _mesh_assert(mesh_script, mesh_agent, node_resource)
            _mesh_assert(mesh_script, mesh_agent, index_resource)
            tmp = "%s.repair.%d.%d" % (ix, os.getpid(), time.time_ns())
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(rendered_index)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, ix)
            tmp = None
            persisted_raw, persisted_text = _read_regular_utf8(
                ix, "memory index")
            persisted = persisted_text.splitlines(True)
            persisted_matches = _same_slug_index_lines(persisted, slug)
            if (
                    len(persisted_matches) != 1
                    or not persisted_matches[0].startswith("- ")
                    or persisted_matches[0].rstrip("\r\n")
                    != entry.rstrip("\r\n")):
                raise IOError("repaired index did not persist exactly one row")
            _assert_regular_unlinked(path)
            with open(path, "rb") as src:
                after_raw = src.read()
            if hashlib.sha256(after_raw).hexdigest() != expected_sha256:
                raise IOError("memory changed while its index was repaired")
            print(json.dumps({
                "status": "REPAIRED_INDEX",
                "slug": slug,
                "memory_sha256": expected_sha256,
                "index_sha256": hashlib.sha256(persisted_raw).hexdigest(),
            }, sort_keys=True))
            return 0
    except Exception as err:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass
        print("memory index repair FAILED: %s" % err)
        return 2


def _print_prior_art_advisory(slug, desc, supersedes=""):
    """Show the nearest existing memories before a new one is filed.

    Three times on 2026-08-15 a session measured something that was already on
    file and VERIFIED - the /eod quote window (08-12), the one-year variance
    premium (07-23), and the speed-not-depth shape of a long-vol position
    (08-07). Each existing node surfaced only AFTER the work, on a keyword that
    only made sense once the answer was known.

    The obvious fix - a reflex that fires when you are about to measure
    something - was tried and measured and does not work. `check_the_brain_first`
    is keyed to STRUGGLE ("that didn't work, let me try another approach"):
    4/4 on genuine walls, 0/6 on those re-derivations, because none of them
    involved a wall. A trigger broadened to catch the action instead
    ("let me measure/compute/derive") fires on 71% of the job journal, which is
    wallpaper, and wallpaper is what gets read past.

    Filing is the one moment where the check is both cheap and precise: there is
    a description to match, and it happens once per finding rather than once per
    thought. Retroactively it puts the prior node at rank 2 and rank 3 for two of
    those three; the third missed the exact node but returned four
    variance-premium memories in the top four, which is enough to make a reader
    stop. So this is not a duplicate detector - it is "look at five neighbours
    before you call it new", and it is advisory on purpose. Blocking a filing on
    a lexical score would eventually eat a real finding.
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import bm25
        hits = bm25.BM25().score(desc, topk=8)
    except Exception:
        return                      # never let the advisory break a filing
    skip = {slug, (supersedes or "").strip()}
    # Floor picked by measurement, not taste: over 120 real memory descriptions
    # the top non-self score has p05 21.5 and median 50.1, while 120 rare-token
    # word salads top out at 17.2. A floor of 20 sits in the gap - it keeps 97%
    # of real filings and admits none of the salad. Silence when nothing is close
    # is the point: an advisory that prints five score-10 strangers every time
    # becomes wallpaper, and wallpaper is exactly the failure this is meant to fix.
    rows = [(s, sc) for s, sc in hits if s not in skip and sc >= 20.0][:5]
    if not rows:
        return
    print("nearest existing memories to this description:")
    for s, sc in rows:
        print("   %6.2f  %s" % (sc, s))
    print("   if one of these already says it, link or update it rather than "
          "filing a twin; if it does not, say so in the body and keep going.")


# OWNER-ORDER ADVISORY (2026-09-22, round 5 W4; F41, F54). Every misleading memory recall served on his 180 real
# prompts was an owner order overtaken by a later owner order with no `superseded_by` - nothing asked the filer of
# the later order to link the earlier one. Round 2 found 23 such overtakings among 200 judged owner-order pairs and
# 15 orders filed two to four times in a day; landing 24 links took 19 stale deliveries on 16 of the 180 prompts to
# zero. Filing is the one moment the filer knows what the new order replaces, so an owner order now prints its
# nearest owner orders already on file, with their filing stamps and the exact command that links one. Advisory
# only: it never blocks, never links anything by itself, fails silent, and gives up past its time budget.
_OWNER_DESC_RX = re.compile(r"\s*[\"']?\[?\s*(?:OWNER|Owner)\b")
_OWNER_INDEX_LINE_RX = re.compile(r"-\s*\[([^\]]+?)\.md\]\([^)]*\)\s*[-\u2014]*\s*(.*)$")
_OWNER_STATUS_TAG_RX = re.compile(r"\[(PROVISIONAL|VERIFIED|OWNER-STATED|RETRACTED)\]\s*")
OWNER_ADVISORY_BUDGET_S = 1.5     # the join: what the filer waits at most, plus the OS's scheduling slack
OWNER_ADVISORY_ROWS = 5


def _is_owner_order(slug, desc, status=""):
    """The owner family of F54: an owner_* slug or a description that opens with an OWNER tag - plus a memory
    filed with --status owner-stated, which is an owner order by its own declaration."""
    return (bool(slug) and slug.startswith("owner_")) or status == "owner-stated" or bool(
        _OWNER_DESC_RX.match(desc or ""))


def _owner_family_texts(exclude):
    """slug -> description text for every owner-family memory the two index files carry (MEMORY.md and
    INDEX_ARCHIVE.md: two sequential reads, no directory scan and no per-file reads). Measured 2026-09-23 on the
    live corpus: 378 family memories this way; 3 of the 194 owner_* files carry no index line and are not seen.
    The 17k-entry directory scan that would find them was the largest steady cost and the step a metadata storm
    stalls, so it is not paid; a row's file is checked when it is about to be shown."""
    texts = {}
    lines = _archived_index_lines()
    lines.update(sr._index_lines())
    for slug, line in lines.items():
        m = _OWNER_INDEX_LINE_RX.match(line)
        text = m.group(2) if m else ""
        tag = _OWNER_STATUS_TAG_RX.match(text)
        bare = text[tag.end():] if tag else text
        if slug.startswith("owner_") or (tag and tag.group(1) == "OWNER-STATED") or _OWNER_DESC_RX.match(bare):
            texts[slug] = bare
    return {s: t for s, t in texts.items() if s not in exclude and s not in sr.SKIP}


def _nearest_owner_orders(slug, desc, supersedes="", k=OWNER_ADVISORY_ROWS, budget_s=OWNER_ADVISORY_BUDGET_S,
                          exclude=()):
    """[(slug, score, via), ...] - the k owner-family memories nearest the new order by BM25 over description
    + slug words (bm25's own tokeniser and scorer, fitted on the family itself, read-only), each carried to the
    current end of its supersession chain. A row needs two shared words that are not family-wide (df <= a
    quarter of the family) - two orders that share only 'owner', 'law' and 'box' are not neighbours. None past
    the time budget; [] when nothing qualifies. `exclude` hides more slugs (a replay hides later filings)."""
    import math
    import bm25
    import supersession as _sup
    deadline = time.monotonic() + budget_s
    texts = _owner_family_texts({slug, (supersedes or "").strip()} | set(exclude or ()))
    if not texts:
        return []

    def text_of(s, text):
        return " ".join(w for w in s.split("_") if w and not w.isdigit()) + " " + (text or "")

    docs = {s: bm25.tokens(text_of(s, t)) for s, t in texts.items()}
    docs = {s: toks for s, toks in docs.items() if toks}
    if not docs:
        return []
    scorer = bm25.BM25.from_docs(docs)
    query = text_of(slug, desc)
    cap = max(3, int(math.ceil(0.25 * len(docs))))
    informative = {t for t in set(bm25.tokens(query)) if 0 < scorer.df.get(t, 0) <= cap}
    if time.monotonic() > deadline:
        return None
    routes = _sup.load_map(MEM, index_path=SUPERSEDED_INDEX, include_frontmatter=False)
    exists = lambda s: os.path.isfile(os.path.join(MEM, s + ".md"))   # noqa: E731
    rows, seen = [], set()
    for cand, score in scorer.score(query, topk=len(docs)):
        if len(informative & set(docs[cand])) < 2:
            continue
        current = _sup.resolve(cand, routes)
        if current != cand and not exists(current):
            current = cand            # a route to a missing file fails open, as resolve() does with known slugs
        if current in (slug, (supersedes or "").strip()) or current in seen or not exists(current):
            continue      # already linked to this filing, its chain already listed, or an index line with no file
        seen.add(current)
        rows.append((current, score, cand if current != cand else ""))
        if len(rows) >= k or time.monotonic() > deadline:
            break
    return None if time.monotonic() > deadline else rows


def _owner_advisory_lines(slug, desc, status="", supersedes=""):
    """The advisory's text, or [] when there is nothing to say (see _print_owner_supersession_advisory)."""
    import supersession as _sup
    rows = _nearest_owner_orders(slug, desc, supersedes)
    if not rows:
        return []
    out = ["owner-order check (advisory): an order that a later one overtakes stays current in recall until "
           "the two are linked. Nearest owner orders already on file:"]
    for i, (other, _score, via) in enumerate(rows, 1):
        front = _sup.frontmatter_block(os.path.join(MEM, other + ".md"))
        stamp = _sup.memory_stamp(front)["text"] or "undated"
        dm = _sup.FM_DESC_RX.search(front)
        line = " ".join(((dm.group(1) if dm else "") or sr._desc(other, {}) or "").split())
        parts = _sup.part_supersessions(front, MEM)
        note = ("  [reached through %s, which it overtook]" % via) if via else ""
        if parts:
            note += "  [already partly superseded: %s]" % "; ".join(c for c, _s in parts)[:80]
        out.append("   %d. %s   filed %s%s" % (i, other, stamp, note))
        out.append("        %s" % (line[:130] + ("..." if len(line) > 130 else "")))
    first = rows[0][0]
    cmd = os.path.join(DR, "brain.py")
    out.append("   if the order just filed overtakes one of these, link it now (the after-filing form of "
               "--supersedes):")
    out.append("     python %s supersede %s --by %s" % (cmd, first, slug))
    out.append("   if it overtakes only one clause of it and the rest still stands:")
    out.append("     python %s supersede-part %s --clause \"<what moved, in a few words>\" --by %s"
               % (cmd, first, slug))
    out.append("   (the commands name row 1; put the slug of the one it overtakes. Nothing was linked.)")
    return out


def _print_owner_supersession_advisory(slug, desc, status="", supersedes=""):
    """Print the nearest owner orders on file after an owner order is filed; silent on any error or overrun.

    The search runs in a daemon worker joined for OWNER_ADVISORY_BUDGET_S: on this machine a steady-state call
    takes ~0.25 s, but a disk stall held single calls for 2.0 s and 6.0 s when it was measured (2026-09-23, the
    live-corpus control B9), and a deadline checked between steps cannot interrupt a read that is already blocked.
    The join bounds what the filer waits whatever the disk does; a late result is dropped, never printed."""
    if os.environ.get("BRAIN_OWNER_ADVISORY", "1") == "0" or not _is_owner_order(slug, desc, status):
        return
    try:
        import threading
        box = {}

        def work():
            try:
                box["lines"] = _owner_advisory_lines(slug, desc, status, supersedes)
            except Exception:
                box["lines"] = []

        worker = threading.Thread(target=work, name="owner-order-advisory", daemon=True)
        worker.start()
        worker.join(OWNER_ADVISORY_BUDGET_S)
        lines = [] if worker.is_alive() else box.get("lines") or []
        if lines:
            print("\n".join(lines))
    except Exception:
        return


def _secret_refusal(*parts):
    """Fail-closed secret gate at the memory-WRITE boundary. "" means clean.

    scan_secrets.py already refuses staged content and outgoing Git objects, so a
    credential typed into a memory today is caught only on its way out to a repo - it
    lives in the corpus, in MEMORY.md, and in every recall until then, and it survives
    in the index even if the file is later deleted. This is the same pattern set at the
    earlier boundary, which is where vanilla-brain enforces it: refuse it at `remember`
    and the push gate never has to see it. Defence in depth, one shared pattern list,
    so the two boundaries can never disagree about what a secret is.

    A scanner that cannot run is a refusal, not a pass. Findings name the matched
    PATTERN only, never the value or a prefix of it (scan_secrets keeps that contract;
    do not print match text here either).
    """
    try:
        import scan_secrets
        hits = scan_secrets.scan("\n".join(p for p in parts if p))
    except Exception as exc:
        return ("the secret scan could not run (%s: %s); a gate that cannot run has not "
                "passed" % (type(exc).__name__, exc))
    if not hits:
        return ""
    names = sorted({name for name, _ in hits})
    return ("content matches %d secret pattern(s): %s. Rewrite without the credential "
            "(and rotate it if it was ever real) - the corpus is not a place to park "
            "one. No override: the push gate has none either."
            % (len(hits), ", ".join(names)))


def _kick_bm25_refresh(slug, supersedes=""):
    """Await windowless BM25 indexing and verify saved targets before claiming it.

    BRAIN_REMEMBER_REFRESH=0 remains the explicit evaluation opt-out. A failed
    or frozen refresh leaves the memory saved and reports pending indexing.
    """
    if os.environ.get("BRAIN_REMEMBER_REFRESH", "1") == "0":
        return
    try:
        from remember_index import ensure_indexed
        targets = [slug] + ([supersedes] if supersedes else [])
        result = ensure_indexed(targets, mem_dir=MEM)
        if result["status"] == "VERIFIED_CURRENT_BM25_CONTENT":
            print("bm25: saved memory token content verified in the current index; recall ranking not checked")
        elif result["status"] == "DEFERRED_CUSTOM_CORPUS_INDEX_REQUIRED":
            print("bm25: custom corpus memory saved; indexing requires an explicit paired index")
        else:
            print("bm25: indexing remains pending (%s); the memory is saved, and the scheduled refresh can retry"
                  % result["status"])
        return result
    except Exception as exc:
        print("bm25: indexing remains unverified (%s); the memory is saved"
              % type(exc).__name__)
        return {"status": "DEFERRED_ERROR", "reason": type(exc).__name__}


def remember(slug, mtype, desc, body, status="provisional", evidence="",
             supersedes="", repair_index=False, expected_sha256="",
             mesh_agent="", mesh_script=""):
    _print_strong_node_advisory([slug, supersedes])
    if not re.fullmatch(r"[a-z0-9][a-z0-9_\-]{2,80}", slug):
        print("slug must be short kebab/snake case, e.g. grid_fill_asymmetry_2026_07_09")
        return 1
    if mtype not in VALID_TYPES:
        print("--type must be one of %s" % (VALID_TYPES,))
        return 1
    if status not in VALID_STATUSES:
        print("--status must be one of %s" % (VALID_STATUSES,))
        return 1
    if status in ("verified", "owner-stated") and len((evidence or "").strip()) < 8:
        print("--evidence is required for %s memories; name the observation, transcript, or artifact" % status)
        return 1
    if not desc or len(desc) < 8:
        print("--desc is the one-line index hook; make it carry the finding")
        return 1
    if "\r" in desc or "\n" in desc:
        print("--desc must be a single line")
        return 1
    refusal = _secret_refusal(slug, desc, evidence, body)
    if refusal:
        print("refusing to write this memory: %s" % refusal)
        return 1
    if repair_index:
        if supersedes:
            print("--repair-index does not support --supersedes")
            return 1
        return _repair_memory_index(
            slug, mtype, desc, body, status, evidence, expected_sha256,
            mesh_agent, mesh_script)
    if supersedes:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,80}", supersedes):
            print("--supersedes must be an existing memory slug")
            return 1
        if supersedes == slug or not os.path.exists(os.path.join(MEM, supersedes + ".md")):
            print("--supersedes target does not exist (or points to itself): %s" % supersedes)
            return 1
    _print_prior_art_advisory(slug, desc, supersedes)
    path = os.path.join(MEM, slug + ".md")
    ix = os.path.join(MEM, "MEMORY.md")
    tag = "[%s] " % status.upper()
    # INDEX LINE BUDGET (2026-08-08): cap the WHOLE line, not just the
    # description. The pointer prefix alone runs 93-176 chars because the
    # slug is written twice, so desc[:200] produced lines up to 376 and
    # MEMORY.md reached 44 KB against a ~24.4 KB read limit - every
    # session was loading only part of its own index, silently.
    _link = "- [%s.md](%s.md) - %s" % (slug, slug, tag)
    entry = "%s%s\n" % (_link, _index_row_text(desc, 200 - len(_link)))
    tmp = None
    old_tmp = None
    try:
        with _memory_write_lock():
            _assert_directory_unlinked(MEM)
            old_path = os.path.join(MEM, supersedes + ".md") if supersedes else ""
            old_body = old_new = None
            if old_path:
                existing = _load_superseded().get(supersedes)
                if existing and existing != slug:
                    raise ValueError("supersession index already maps %s -> %s" % (supersedes, existing))
                with io.open(old_path, encoding="utf-8", errors="replace") as src:
                    old_body = src.read()
                old_new = _stamp_superseded(old_body, slug, pending=True)
            _index_raw, index_text = _read_regular_utf8(ix, "memory index")
            pre_lines = index_text.splitlines(True)
            stale = _same_slug_index_lines(pre_lines, slug)
            if stale:
                if os.path.exists(path):
                    # Real duplicate: index line AND memory file both exist.
                    # O_EXCL below is the second fence; refuse loudly here first.
                    raise ValueError(
                        "MEMORY.md already carries this slug and the memory file exists")
                # REPAIR (task #319, test_remember_repairs_missing_ledger_for_
                # pre_stamped_old): an index line whose memory FILE is absent is a
                # stale remnant of a legacy partial write - it can never recall.
                # Drop the stale line(s); the fresh entry below replaces them.
                # Refusing here (pre-07-30 behavior) stranded BOTH the stale line
                # and the new memory - the exact a_memory_that_files_but_cannot_
                # recall harm this guard was meant to prevent.
                pre_lines = [ln for ln in pre_lines if ln not in stale]
                print("repaired: dropped %d stale index line(s) for absent memory %s"
                      % (len(stale), slug))
            next_lines = _insert_index_entry(pre_lines, entry)
            next_index_raw = "".join(next_lines).encode("utf-8")
            # O_EXCL is the second fence: even a non-cooperating creator cannot make
            # two same-slug writers both believe they won.
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                print("refusing to overwrite existing memory: %s" % path)
                return 1
            _created, _created_at = _filing_clock()
            rendered = _render_memory_text(
                slug, mtype, desc, body, status, evidence,
                _created, supersedes, created_at=_created_at)
            # created: is the immutable birth date; mtimes lie after a restore. created_at orders a day's filings.
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
                out.write(rendered)
                out.flush()
                os.fsync(out.fileno())

            if old_path:
                # Publish or repair the route even when frontmatter was stamped by an earlier
                # partial attempt. Hot readers are ledger-only, so an already-stamped old file
                # must never suppress reconstruction of a missing ledger row.
                _record_supersession(supersedes, slug)
                if old_new != old_body:
                    old_tmp = "%s.tmp.%d.%d" % (old_path, os.getpid(), time.time_ns())
                    with io.open(old_tmp, "w", encoding="utf-8", newline="\n") as out:
                        out.write(old_new)
                        out.flush()
                        os.fsync(out.fileno())
                    os.replace(old_tmp, old_path)
                    old_tmp = None

            tmp = "%s.tmp.%d.%d" % (ix, os.getpid(), time.time_ns())
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(next_index_raw)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, ix)
            tmp = None
            _persisted_raw, persisted_text = _read_regular_utf8(
                ix, "memory index")
            persisted_lines = persisted_text.splitlines(True)
            persisted_matches = _same_slug_index_lines(
                persisted_lines, slug)
            if (
                    len(persisted_matches) != 1
                    or not persisted_matches[0].startswith("- ")
                    or persisted_matches[0].rstrip("\r\n")
                    != entry.rstrip("\r\n")):
                raise IOError("index entry was not present after atomic replace")
            _maybe_compact_index()
    except Exception as err:
        for leftover in (tmp, old_tmp):
            if leftover:
                try:
                    os.remove(leftover)
                except OSError:
                    pass
        print("memory write FAILED: %s" % err)
        return 2
    print("wrote %s\nindexed in MEMORY.md as %s" % (path, status))
    _kick_bm25_refresh(slug, supersedes)
    if not (body or "").strip():
        print("WARNING: EMPTY BODY -- this memory is a description-only stub, invisible to body-level "
              "recall. brain.py does NOT read stdin; pass --body-file <path> to add the content, or edit "
              "%s. (A brain-wide audit on 2026-07-12 found dozens of these.)" % path)
    if supersedes:
        print("superseded %s -> %s (both recall paths now follow the correction)" % (supersedes, slug))
    _print_owner_supersession_advisory(slug, desc, status, supersedes)
    # A chained ratio written into the corpus with no per-book test beside it. status_append.py
    # has refused this since 2026-08-14, but on that same night the claim simply left through the
    # unguarded exits instead - two memories and a mesh message - and reached the owner-facing
    # decision list attached to a question about a LIVE dial. A guard bound to one tool decays as
    # the work changes tools, so the check now also sits here. WARNS rather than refuses: brain.py
    # is shared machinery every agent writes through, and a false positive that blocks a memory
    # costs more than the failure it prevents. Wrapped so it can never break a write.
    try:
        import importlib.util as _ilu
        _gp = os.path.join(os.path.expanduser("~"), "brain-repo",
                           "12_cognitive_architecture", "audits", "tools", "ratio_guard.py")
        if os.path.exists(_gp):
            _sp = _ilu.spec_from_file_location("ratio_guard", _gp)
            _rg = _ilu.module_from_spec(_sp)
            _sp.loader.exec_module(_rg)
            _ok, _why = _rg.check(body or "")
            if not _ok:
                print(_why)
    except Exception:
        pass
    print("now link it: mention related memories in the body as double-bracketed slugs, e.g. [[kettle_descaling_routine_2026_01_01]] (the graph builds from these)")
    return 0


def _remember_cli(argv):
    parser = argparse.ArgumentParser(
        prog="brain.py remember",
        description="Create a memory, or repair only its missing MEMORY.md row.")
    parser.add_argument("slug")
    parser.add_argument("--type", required=True, choices=VALID_TYPES)
    parser.add_argument("--desc", required=True)
    bodies = parser.add_mutually_exclusive_group()
    bodies.add_argument("--body", default="")
    bodies.add_argument("--body-file", default="")
    parser.add_argument("--status", default="provisional", choices=VALID_STATUSES)
    parser.add_argument("--evidence", default="")
    parser.add_argument("--supersedes", default="")
    parser.add_argument("--repair-index", action="store_true")
    parser.add_argument("--expected-sha256", default="")
    parser.add_argument("--mesh-agent", default="")
    parser.add_argument("--mesh-script", default="")
    parsed = parser.parse_args(argv)
    body = parsed.body
    if parsed.body_file:
        try:
            with io.open(parsed.body_file, encoding="utf-8") as src:
                body = src.read()
        except Exception as err:
            print("cannot read --body-file: %s" % err)
            return 1
    if not parsed.repair_index and (
            parsed.expected_sha256 or parsed.mesh_agent or parsed.mesh_script):
        print("--expected-sha256/--mesh-agent/--mesh-script require --repair-index")
        return 1
    return remember(
        (parsed.slug[:-3] if parsed.slug.endswith(".md") else parsed.slug),
        parsed.type, parsed.desc, body,
        parsed.status, parsed.evidence, parsed.supersedes,
        parsed.repair_index, parsed.expected_sha256, parsed.mesh_agent,
        parsed.mesh_script)


def _routes_to(start, target, routes, max_hops=64):
    """True when following superseded_by routes from `start` reaches `target` (a link target -> start would loop)."""
    current, seen = start, set()
    for _ in range(max_hops):
        if current == target:
            return True
        if current in seen or current not in routes:
            return False
        seen.add(current)
        current = routes[current]
    return False


def supersede(old, new, clause=None, mesh_agent="", mesh_script="", part=None):
    """Link two memories that already exist: `old` is overtaken by `new` - wholly, or (with a clause) in part.

    WHOLE (no clause): the after-filing form of `remember --supersedes`, through the same internals in the same
    order under the same lock - the ledger row first (_record_supersession), then the old memory's own
    `superseded_by:` (_stamp_superseded), replaced atomically. Refuses a second different successor and a link
    that would close a loop (resolve() fails open on a cycle, so both ends would be served as current).
    PART (clause): adds `<clause> -> <new>` to the old memory's `superseded_in_part:` list. No ledger row and no
    routing: the old memory stays the answer for everything but the clause, and every reader flags the clause.
    Refused when the old memory is already overtaken as a whole - recall routes it away, the flag would never
    be read. Use it only when the rest of the old memory still stands; a part-link on a memory that is wholly
    dead keeps a retired rule current with a footnote, which is worse than no link.
    Both: the filing stamps are compared and a successor filed BEFORE the memory it overtakes draws a warning
    (all nine inverted verdicts of the F54 owner-pair judge were same-day orders in the wrong order); a warning,
    not a refusal, because an order can be filed after the order that replaced it. --mesh-agent/--mesh-script,
    when given, assert the claim on memory:brain/<old> before the lock and again immediately before the write."""
    import supersession as _sup
    _print_strong_node_advisory([old, new])
    for label, value in (("old slug", old), ("--by", new)):
        if not SLUG_RX.fullmatch(value or ""):
            print("%s must be ONE memory slug, not %r" % (label, value))
            return 1
    if old == new:
        print("a memory cannot supersede itself")
        return 1
    if bool(mesh_agent) != bool(mesh_script):
        print("--mesh-agent and --mesh-script go together")
        return 1
    # a clause that was GIVEN makes this a part link even when it is blank - a blank one is refused below, it
    # must never fall through to a whole-node link (caught by the fixture control before it shipped)
    part = (clause is not None) if part is None else bool(part)
    if part and not _sup.clean_part_clause(clause or ""):
        print("--clause must name what moved, in a few words")
        return 1
    old_path = os.path.join(MEM, old + ".md")
    if not os.path.isfile(old_path):
        print("no such memory: %s" % old_path)
        return 1
    try:
        _require_pointer_target(new, "--by")
    except ValueError as err:
        print("refusing: %s" % err)
        return 1
    order = _sup.order_stamps(_sup.memory_stamp(_sup.frontmatter_block(os.path.join(MEM, new + ".md"))),
                              _sup.memory_stamp(_sup.frontmatter_block(old_path)))
    if order < 0:
        print("WARNING: %s was filed BEFORE %s by their own filing stamps - check the direction. (A later filing "
              "can record an earlier order, so this is not refused.)" % (new, old))
    resource = "memory:brain/%s" % old
    tmp = None
    try:
        if mesh_agent:
            _mesh_assert(mesh_script, mesh_agent, resource)
        with _memory_write_lock():
            _assert_directory_unlinked(MEM)
            _raw, old_text = _read_regular_utf8(old_path, "superseded memory")
            routes = load_map(MEM, index_path=SUPERSEDED_INDEX, include_frontmatter=True)
            existing = routes.get(old)
            if part:
                if existing:
                    print("refusing: %s is already superseded as a whole by %s - recall routes it there, so a "
                          "part flag on it would never be read" % (old, existing))
                    return 1
                new_text = _sup.stamp_part(old_text, clause, new)
            else:
                if existing and existing != new:
                    print("refusing: %s is already superseded by %s; link that one, or correct it first"
                          % (old, existing))
                    return 1
                if _routes_to(new, old, routes):
                    print("refusing: %s already routes (through its own chain) to %s - linking back would "
                          "close a loop" % (new, old))
                    return 1
                _fm = _sup.FRONTMATTER_RX.search(old_text)
                if new in _sup.partial_successors(_fm.group(1) if _fm else "", MEM):
                    print("refusing: %s declares %s a PARTIAL successor (superseded_in_part) - a whole route would "
                          "hide the part that still stands; if all of it is now overtaken, remove that item first"
                          % (old, new))
                    return 1
                new_text = _stamp_superseded(old_text, new)
            if mesh_agent:
                _mesh_assert(mesh_script, mesh_agent, resource)
            if not part:
                _record_supersession(old, new)      # ledger first: hot readers are ledger-only
            if new_text != old_text:
                tmp = "%s.tmp.%d.%d" % (old_path, os.getpid(), time.time_ns())
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as out:
                    out.write(new_text.encode("utf-8"))
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(tmp, old_path)
                tmp = None
    except Exception as err:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass
        print("supersession write FAILED: %s" % err)
        return 2
    if part:
        print("partly superseded %s: %r -> %s (not routed: recall still serves it, flagged)"
              % (old, _sup.clean_part_clause(clause), new))
    else:
        print("superseded %s -> %s (both recall paths now follow the correction)" % (old, new))
    _kick_bm25_refresh(old)
    return 0


def _supersede_cli(argv, part=False):
    name = "supersede-part" if part else "supersede"
    parser = argparse.ArgumentParser(
        prog="brain.py %s" % name,
        description=("Record that part of an existing memory - one clause - was overtaken by a later memory; the "
                     "old memory stays current for the rest and is flagged." if part else
                     "Record that an existing memory was overtaken by a later memory (the after-filing form of "
                     "remember --supersedes); add --clause to record a partial overtaking instead."))
    parser.add_argument("old_slug")
    parser.add_argument("--by", required=True, dest="new_slug")
    parser.add_argument("--clause", required=part, default=None)
    parser.add_argument("--mesh-agent", default="")
    parser.add_argument("--mesh-script", default="")
    parsed = parser.parse_args(argv)
    strip = lambda s: s[:-3] if s.endswith(".md") else s   # noqa: E731
    return supersede(strip(parsed.old_slug), strip(parsed.new_slug), parsed.clause,
                     parsed.mesh_agent, parsed.mesh_script, part=(part or parsed.clause is not None))


def _node_cli(argv):
    parser = argparse.ArgumentParser(
        prog="brain.py node",
        description="Read a memory node together with every verified attached text document.")
    parser.add_argument("slug")
    parser.add_argument("--part", type=int, default=0)
    parser.add_argument("--part-chars", type=int, default=node_docs.DEFAULT_PART_CHARS)
    parser.add_argument("--attachments-only", action="store_true")
    parser.add_argument("--auto-only", action="store_true")
    parsed = parser.parse_args(argv)
    try:
        if parsed.part:
            payload, _count = node_docs.render_part(
                MEM, parsed.slug, parsed.part, part_chars=parsed.part_chars,
                attachments_only=parsed.attachments_only,
                auto_only=parsed.auto_only)
        else:
            payload = node_docs.render_node(
                MEM, parsed.slug, attachments_only=parsed.attachments_only,
                auto_only=parsed.auto_only)
        sys.stdout.write(payload)
        return 0
    except Exception as exc:
        print("BRAIN NODE DOCUMENT ERROR: %s: %s" % (type(exc).__name__, exc))
        return 2


def _mem_is_live():
    return os.path.normcase(os.path.abspath(MEM)) == os.path.normcase(os.path.abspath(LIVE_MEM))


def _attach_cli(argv):
    parser = argparse.ArgumentParser(
        prog="brain.py attach",
        description="Copy one immutable UTF-8 .md/.txt document into a memory node bundle.")
    parser.add_argument("slug")
    parser.add_argument("--file", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--description", default="")
    parser.add_argument("--on-demand", action="store_true")
    parser.add_argument("--wake", action="store_true")
    parser.add_argument("--mesh-agent", default="")
    parser.add_argument("--mesh-script", default="")
    parsed = parser.parse_args(argv)
    slug = parsed.slug[:-3] if parsed.slug.endswith(".md") else parsed.slug
    if _mem_is_live():
        if not parsed.mesh_agent or not parsed.mesh_script:
            print("live attachment writes require --mesh-agent and --mesh-script plus claims on "
                  "memory:brain/%s and memory:brain/attachments/%s" % (slug, slug))
            return 1
        try:
            _mesh_assert(parsed.mesh_script, parsed.mesh_agent, "memory:brain/%s" % slug)
            _mesh_assert(parsed.mesh_script, parsed.mesh_agent,
                         "memory:brain/attachments/%s" % slug)
        except Exception as exc:
            print("attachment mesh claim verification FAILED: %s" % exc)
            return 2
    try:
        doc = node_docs.attach(
            MEM, slug, parsed.file, name=parsed.name,
            description=parsed.description, required=True,
            auto_read=not parsed.on_demand, wake=parsed.wake)
        print(json.dumps({
            "status": "ATTACHED", "node": doc.node, "name": doc.name,
            "sha256": doc.sha256, "bytes": doc.bytes,
            "auto_read": doc.auto_read, "wake": doc.wake,
        }, sort_keys=True))
        print("retrieval is immediate through the live attachment arm; the nightly BM25 build "
              "will fold this text into the parent index too")
        return 0
    except Exception as exc:
        print("BRAIN NODE DOCUMENT ERROR: %s: %s" % (type(exc).__name__, exc))
        return 2


def _verify_attachments_cli(argv):
    parser = argparse.ArgumentParser(prog="brain.py verify-attachments")
    parser.add_argument("slug", nargs="?")
    parsed = parser.parse_args(argv)
    try:
        if parsed.slug:
            result = {"node": parsed.slug, "ok": True,
                      "documents": node_docs.summaries(MEM, parsed.slug)}
            rc = 0
        else:
            result = node_docs.verify_all(MEM)
            rc = 2 if any(not row.get("ok") for row in result) else 0
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return rc
    except Exception as exc:
        print("BRAIN NODE DOCUMENT ERROR: %s: %s" % (type(exc).__name__, exc))
        return 2


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip().split("\n\n")[1])
        return 0
    cmd = args[0]
    if cmd == "recall":
        # a query is never a flag (2026-09-22, Test A): a FIRE WHEN line pasted as "- the situation ..." begins
        # with "-" and used to be dropped whole, returning usage text - 322 of 1,038 lines in the F42 bank and
        # every bullet-led query the Opus agent sent. Only a bare option-shaped token is a flag.
        def _is_flag(a):
            return bool(re.match(r"^--?[A-Za-z][\w-]*$", a))
        q = " ".join(a for a in args[1:] if not _is_flag(a))
        n = 8
        if "-n" in args:
            try:
                n = int(args[args.index("-n") + 1])
                q = " ".join(a for i, a in enumerate(args[1:], 1)
                             if not _is_flag(a) and i != args.index("-n") + 1)
            except Exception:
                pass
        q = re.sub(r"^[\-\*>\s]+", "", q)   # strip a leading bullet or quote marker
        if not q.strip():
            print("usage: brain.py recall \"your question\" [-n 8]")
            return 1
        return recall(q, n)
    elif cmd == "node":
        return _node_cli(args[1:])
    elif cmd == "attach":
        return _attach_cli(args[1:])
    elif cmd == "verify-attachments":
        return _verify_attachments_cli(args[1:])
    elif cmd == "neighbors" and len(args) >= 2:
        n = 10
        if "-n" in args:
            try:
                n = int(args[args.index("-n") + 1])
            except Exception:
                pass
        neighbors(args[1].replace(".md", ""), n)
    elif cmd == "remember":
        return _remember_cli(args[1:])
    elif cmd == "supersede":
        return _supersede_cli(args[1:])
    elif cmd == "supersede-part":
        return _supersede_cli(args[1:], part=True)
    elif cmd == "status":
        return subprocess.call([sys.executable, os.path.join(DR, "brain_status.py")])
    elif cmd == "dream-about" and len(args) >= 2:
        # queue a targeted dream: the next agenda build surfaces it as section R
        opt = {"--why": "", "--from": ""}
        i = 2
        while i < len(args):
            if args[i] in opt and i + 1 < len(args):
                opt[args[i]] = args[i + 1]
                i += 2
            else:
                i += 1
        import dream_requests as _dr
        row = _dr.add(args[1], why=opt["--why"], requested_by=opt["--from"] or "brain.py")
        if row:
            print("queued for the next dream: %s  %s" % (row["id"], row["topic"]))
            return 0
        print("nothing queued (empty topic)")
        return 1
    elif cmd == "dream-requests":
        import dream_requests as _dr
        return _dr.main(["list"])
    elif cmd == "strong-node-proposals":
        print(json.dumps(strong_node_proposals(), indent=2, sort_keys=True))
        return 0
    else:
        print("unknown command %r - recall | node | attach | verify-attachments | "
              "neighbors | remember | supersede | supersede-part | status | "
              "dream-about \"topic\" [--why ...] | dream-requests | "
              "strong-node-proposals" % cmd)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
