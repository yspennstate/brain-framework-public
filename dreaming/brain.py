#!/usr/bin/env python3
"""brain.py - the one-command door into the brain, for any agent mid-work.

The hooks (fok_probe, spread_recall, instinct_prime) push memories AT a session when its prompt
touches them. This is the pull side: a session that WANTS the brain's take asks for it, and gets
the full recall stack - lexical match + spreading activation over the association graph + semantic
ranking against the embedding sidecar - in one call, laptop-safe (no model inference; the semantic
half rides the precomputed sidecar via seed-centroid cosine, the same trick spread_recall uses).

  python brain.py recall "what do we know about grid sim accuracy" [-n 8]
  python brain.py recall "<wording 1>" --or "<wording 2>"   # wordings fused by rank (k=60) into one list
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
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "recall":
    # RECALL-10X lead 1 (laptop 3 Nov 2026; Mac 4 Nov, 1.94x paired, 20/20 identical): a resident server answers
    # recall with its built state warm (brain_recall_server.py). Any failure falls through to the cold path below;
    # BRAIN_RECALL_SERVER=0 turns it off.
    try:
        import brain_recall_client as _brc
        _brc_rc = _brc.run(sys.argv[1:])
    except Exception:
        _brc_rc = None
    if _brc_rc is not None:
        sys.exit(_brc_rc)
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "where":
    # 5 Nov 2026: "where in the brain's code does X live" goes to code_map.py, which maps
    # every function and class in ~/.claude/dreaming; recall indexes memories, not code (held-out bench: right
    # file in the top 3 for 9 of 10 questions against recall's 3). Routed before the recall imports load.
    import code_map as _cm
    _wq = " ".join(a for a in sys.argv[2:] if not a.startswith("--") and not a.isdigit())
    if not _wq.strip():
        print('usage: brain.py where "which hook refuses a billed CI push" [--budget 800] [--files 5]')
        sys.exit(1)
    _wb = int(sys.argv[sys.argv.index("--budget") + 1]) if "--budget" in sys.argv else 800
    _wf = int(sys.argv[sys.argv.index("--files") + 1]) if "--files" in sys.argv else 5
    print(_cm.query(_wq, _wb, _wf))
    sys.exit(0)
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
_FC_ROWS = {}   # postings sidecar identity -> (pos_slug, slug_pos, base): its d table, read and checked once
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


# COMPACTION TAIL LANE (31 Oct 2026, Mac). A compaction's anchor memory carries an excerpt of its summary; the rest (68%
# of the lines) lives only in ~/.claude/brain_graph/compactions, and no lane read it: a line found only there brought
# its compaction into the top 10 for 1 of 20 questions. Here each anchor's tail - the summary lines the anchor does not
# carry, capped at bm25.MAX_ATTACHMENT_TOKENS - is its own small bm25 document scored on the live index's n and df, and
# a tail hit stands for its anchor at max(anchor, tail). Appending the tail to the anchor instead diluted the anchor's
# own text (head control 13 -> 9 of 57) and lost the bank 3 / 12. Measured through fused recall on the 60 newest
# compactions and a 600-row bank: tail top 10 6 -> 38 (0 worse), head 22 -> 21, bank identical at every k; on 36 of his
# questions no top 5 moved (codex_fleet/recall_warm/tools/ctail_fused.py, w06_diff_round.py). The tails are cached in
# CTAIL_CACHE (marshal) and rebuilt when a day folder of compactions changes. BRAIN_RECALL_CTAIL=0 turns the lane off.
CTAIL_DIR = os.path.expanduser("~/.claude/brain_graph/compactions")
CTAIL_CACHE = os.path.join(CTAIL_DIR, ".ctail_index.marshal")
_CTAIL = {}
_CTAIL_FS = re.compile(r"^\s*full_summary:\s*(\S.*?)\s*$", re.M)


def _ctail_index(bm25mod):
    """(tails, tdf): anchor slug -> (term counts, length) of its summary's lines outside the anchor, and their df."""
    import marshal
    key = _ctail_key()
    # The tails are read through anchors in bm25mod.MEM, so the cache belongs to that folder. One shared file let a
    # recall on another folder (BRAIN_MEM_DIR) reuse the live folder's tails, which then stood for anchors that folder
    # does not hold, and let it write back an index built from no anchors under the live key (5 Nov 2026).
    cache = _mem_scoped(CTAIL_CACHE, bm25mod)
    if _CTAIL.get("key") == key and _CTAIL.get("mem") == bm25mod.MEM:
        return _CTAIL["ix"]
    prev = {}
    try:
        with open(cache, "rb") as fh:
            got = marshal.load(fh)
        if len(got) == 3:
            prev = got[2]
        if tuple(tuple(x) for x in got[0]) == key:
            _CTAIL.update(key=key, mem=bm25mod.MEM, ix=got[1])
            return got[1]
    except Exception:
        pass
    # A new compaction moves its day folder, and every summary was re-read on each one (0.5-0.6 s cold, one every
    # few minutes, 1 Nov 2026). Each file's result is kept with the stat of its anchor and its summary, so only new or
    # changed ones are read; the walk and its order are the same as a full build's.
    files = {}
    tails, tdf = {}, {}
    for d, _m in key:
        dp = os.path.join(CTAIL_DIR, d)
        for fn in sorted(os.listdir(dp)):
            if not (fn.startswith("compaction_") and fn.endswith(".md")):
                continue
            ap = os.path.join(bm25mod.MEM, fn)
            asig = None
            try:
                st = os.stat(ap)
                asig = (st.st_mtime_ns, st.st_size)
                old = prev.get(fn)
                if old is not None and tuple(old[0]) == asig:
                    if old[1] is None:
                        files[fn] = old
                        continue
                    st = os.stat(old[1])
                    if (st.st_mtime_ns, st.st_size) == tuple(old[2]):
                        files[fn] = old
                        if old[3] is not None:
                            tails[fn[:-3]] = (old[3], old[4])
                            for t in old[3]:
                                tdf[t] = tdf.get(t, 0) + 1
                        continue
            except (OSError, TypeError, ValueError, IndexError):
                pass
            if asig is None:
                continue
            try:
                with io.open(ap, encoding="utf-8", errors="replace") as fh:
                    anchor = fh.read()
                m = _CTAIL_FS.search(anchor[:3000])
                if not m or os.path.basename(m.group(1)) != fn:
                    files[fn] = (asig, None, None, None, 0)
                    continue
                st = os.stat(m.group(1))
                with io.open(m.group(1), encoding="utf-8", errors="replace") as fh:
                    lines = [l.strip(" -*#>") for l in fh.read().splitlines()]
            except OSError:
                continue
            flat = " ".join(anchor.split())
            outside = [l for l in lines if l and " ".join(l.split()) not in flat]
            tks = bm25mod.tokens("\n".join(outside))[:bm25mod.MAX_ATTACHMENT_TOKENS] if outside else []
            if not tks:
                files[fn] = (asig, m.group(1), (st.st_mtime_ns, st.st_size), None, 0)
                continue
            tf = {}
            for t in tks:
                tf[t] = tf.get(t, 0) + 1
            files[fn] = (asig, m.group(1), (st.st_mtime_ns, st.st_size), tf, len(tks))
            tails[fn[:-3]] = (tf, len(tks))
            for t in tf:
                tdf[t] = tdf.get(t, 0) + 1
    ix = (tails, tdf)
    try:
        if not _mem_scope_throwaway(bm25mod):
            tmp = "%s.%d.tmp" % (cache, os.getpid())
            with open(tmp, "wb") as fh:
                marshal.dump((key, ix, files), fh)
            os.replace(tmp, cache)
    except Exception:
        pass
    _CTAIL.update(key=key, mem=bm25mod.MEM, ix=ix)
    return ix


def _ctail_merge(hits, query, bm, bm25mod, topk):
    """The lexical hits with each tail's score (a compaction's, or a long memory's) standing for its own doc where it
    beats that doc's own."""
    import array, math
    ix = _tail_postings(bm25mod)
    names, dl, post = ix["names"], ix["dl"], ix["post"]
    lim = ix["nc"] if os.environ.get("BRAIN_RECALL_MTAIL", "1") == "0" else len(names)
    pairs = {}
    for t in sorted({t for t in bm25mod.tokens(query) if t in post}):
        a = array.array(ix["tc"], post[t])
        p = [(a[j], a[j + 1]) for j in range(0, len(a), 2) if a[j] < lim]
        if p:
            pairs[t] = p
    if not pairs:
        return hits
    n = bm.n + lim
    avgdl = (bm.avgdl * bm.n + (ix["dl_c"] if lim == ix["nc"] else ix["dl_all"])) / n
    k1, b = bm25mod.K1, bm25mod.B
    score = {}
    for t, p in pairs.items():      # terms in sorted order, so each doc's sum adds them as the dict version did
        df = bm.df.get(t, 0) + len(p)
        idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
        for i, f in p:
            score[i] = score.get(i, 0.0) + idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl[i] / avgdl))
    best = dict(hits)
    moved = False
    for i in sorted(score):
        s = score[i]
        if s > best.get(names[i], 0.0):
            best[names[i]] = s
            moved = True
    return sorted(best.items(), key=lambda kv: -kv[1])[:topk] if moved else hits


# Every tail as one doc in ONE packed postings file, term -> (doc index, tf) pairs as uint16 bytes, so a cold recall
# loads it once (0.03-0.04 s) and reads only the query's terms. Loading the two tf-dict caches instead cost a cold
# process 0.16 s of a ~1 s recall (224,381 counts unmarshalled, 1 Nov 2026). The file is current while the compaction
# day folders and every known long memory keep their stat; a memory that turns long, or a new long one, waits for the
# next full rescan, at most TAIL_FULL_SCAN_S after the memory folder moves (its first MAX_DOC_TOKENS tokens are indexed
# at once anyway).
TAIL_POST = os.path.join(CTAIL_DIR, ".tail_postings.marshal")
TAIL_FULL_SCAN_S = 3600
_TPOST = {}
_MEM_SCOPE = {}


def _mem_scoped(path, bm25mod=None):
    """`path` for the live memory folder; any other folder bm25 reads (BRAIN_MEM_DIR: an eval, a recall run on the brain
    repo's copy) gets its own file beside it. With one shared file the two folders overwrote each other's per-file
    stats, and every rescan on either side re-read all 6,700 long memories, 8-12 s of CPU (2 Nov 2026)."""
    if bm25mod is None:
        return path
    mem = bm25mod.MEM
    tag = _MEM_SCOPE.get(mem)
    if tag is None:
        real = os.path.realpath(mem)
        live = os.path.realpath(os.path.join(bm25mod.HOME, ".claude", "projects", bm25mod._BRAIN_SLUG, "memory"))
        tag = _MEM_SCOPE[mem] = "" if real == live else "." + hashlib.sha1(real.encode("utf-8")).hexdigest()[:12]
    return path + tag


def _mem_scope_throwaway(bm25mod=None):
    """True when bm25 reads a memory folder under the temp dir. Its scoped tail files are never read again once the
    folder is gone, and each sandboxed recall left a 3.4 MB postings file in the live compactions folder: 220 of them,
    478 MB, by 5 Nov 2026. Such a recall keeps its tails in memory and writes nothing."""
    if bm25mod is None:
        return False
    import tempfile
    root = os.path.join(os.path.realpath(tempfile.gettempdir()), "")
    return os.path.realpath(bm25mod.MEM).startswith(root)


def _ctail_key():
    return tuple((d, os.stat(os.path.join(CTAIL_DIR, d)).st_mtime_ns) for d in sorted(os.listdir(CTAIL_DIR))
                 if os.path.isdir(os.path.join(CTAIL_DIR, d)))


def _tpost_valid(ix, bm25mod):
    try:
        return ix.get("v") == 2 and ix["ckey"] == _ctail_key() and _tpost_mem_valid(ix, bm25mod)
    except (OSError, KeyError, TypeError):
        return False


def _tpost_mem_valid(ix, bm25mod):
    """The long-memory half of _tpost_valid: every known long memory keeps its stat, and the folder has not moved or
    was scanned under TAIL_FULL_SCAN_S ago."""
    import time
    try:
        if ix.get("v") != 2:
            return False
        for fn, sig in ix["sigs"].items():
            st = os.stat(os.path.join(bm25mod.MEM, fn))
            if (st.st_mtime_ns, st.st_size) != tuple(sig):
                return False
        return os.stat(bm25mod.MEM).st_mtime_ns == ix["dkey"] or time.time() - ix["scanned"] < TAIL_FULL_SCAN_S
    except (OSError, KeyError, TypeError):
        return False


# The file is a small header (every key but "post", plus each bucket's span) and the postings split into TPOST_NB
# term-hash buckets, each marshalled on its own, so a recall unmarshals the header and the few buckets its query
# terms fall in. Loading the whole 2.6 MB dict cost 41-65 ms a recall at 60,351 terms; header plus five buckets is
# about 0.1 ms (1 Nov 2026).
TPOST_MAGIC = b"TPB2"
TPOST_NB = 256


def _tpost_bucket(term):
    import zlib
    return zlib.crc32(term.encode("utf-8", "surrogatepass")) % TPOST_NB


class _TpostBuckets(object):
    """The postings of a v2 file: term -> packed bytes, unmarshalling a bucket the first time one of its terms is
    asked. Holds the bytes read at load, so every bucket comes from the same file version as the header."""

    def __init__(self, raw, base, spans):
        self._raw, self._base, self._spans, self._got = raw, base, spans, {}

    def _bucket(self, term):
        import marshal
        k = _tpost_bucket(term)
        b = self._got.get(k)
        if b is None:
            off, n = self._spans[k]
            b = self._got[k] = marshal.loads(self._raw[self._base + off:self._base + off + n])
        return b

    def __contains__(self, term):
        return term in self._bucket(term)

    def __getitem__(self, term):
        return self._bucket(term)[term]


def _tpost_write(ix, bm25mod=None):
    import marshal, struct
    buckets = [dict() for _ in range(TPOST_NB)]
    for t, b in ix["post"].items():
        buckets[_tpost_bucket(t)][t] = b
    blobs = [marshal.dumps(b) for b in buckets]
    spans, off = [], 0
    for b in blobs:
        spans.append((off, len(b)))
        off += len(b)
    head = dict((k, v) for k, v in ix.items() if k != "post")
    head["spans"] = spans
    hb = marshal.dumps(head)
    if _mem_scope_throwaway(bm25mod):
        return
    path = _mem_scoped(TAIL_POST, bm25mod)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "wb") as fh:
        fh.write(TPOST_MAGIC + struct.pack("<Q", len(hb)))
        fh.write(hb)
        for b in blobs:
            fh.write(b)
    os.replace(tmp, path)


def _tpost_read(bm25mod=None):
    """The v2 file as an ix whose "post" loads buckets on demand; None if missing, an older format or damaged."""
    import marshal, struct
    try:
        with open(_mem_scoped(TAIL_POST, bm25mod), "rb") as fh:
            raw = fh.read()
        if raw[:4] != TPOST_MAGIC:
            return None
        hn = struct.unpack("<Q", raw[4:12])[0]
        ix = marshal.loads(raw[12:12 + hn])
        spans = ix.pop("spans")
        base = 12 + hn
        if len(spans) != TPOST_NB or sum(n for _, n in spans) != len(raw) - base:
            return None
        ix["post"] = _TpostBuckets(raw, base, spans)
        return ix
    except Exception:
        return None


def _tail_postings(bm25mod):
    """The packed postings of every compaction tail (docs [0, nc)) and long-memory tail, rebuilt when not current."""
    import array, marshal, time
    ix = _TPOST.get("ix")
    if ix is None:
        ix = _tpost_read(bm25mod)
    if ix is not None and _tpost_valid(ix, bm25mod):
        _TPOST["ix"] = ix
        return ix
    ckey = _ctail_key()     # taken before the build: a compaction landing during it makes the next call rebuild
    dkey = os.stat(bm25mod.MEM).st_mtime_ns
    scanned = time.time()
    c_tails = _ctail_index(bm25mod)[0]
    # When only the compaction half is stale (a new compaction, the usual case), the long-memory half is as current
    # as _tpost_valid would call it: keep the last scan's tails, folder key, scan time and stats, and skip restatting
    # the whole memory folder (0.15-0.2 s cold).
    m_tails = sigs = None
    if ix is not None and _tpost_mem_valid(ix, bm25mod):
        m_tails = _mtail_cached(ix["sigs"], bm25mod)
        if m_tails is not None:
            dkey, scanned, sigs = ix["dkey"], ix["scanned"], ix["sigs"]
    if m_tails is None:
        m_tails = _mtail_index(bm25mod, rescan=True)[0]
        sigs = {n: tuple(v[0]) for n, v in (_MTAIL.get("files") or {}).items() if v[2]}
    names = list(c_tails) + [s for s in m_tails if s not in c_tails]
    tc = "H" if len(names) < 65535 else "I"
    cap = 65535 if tc == "H" else 2 ** 32 - 1
    dl, post = [], {}
    for i, s in enumerate(names):
        tf, n = c_tails[s] if i < len(c_tails) else m_tails[s]
        dl.append(n)
        for t, f in tf.items():
            post.setdefault(t, array.array(tc)).extend((i, min(f, cap)))
    ix = {"v": 2, "ckey": ckey, "dkey": dkey, "scanned": scanned, "names": names, "nc": len(c_tails),
          "dl": dl, "dl_c": sum(dl[:len(c_tails)]), "dl_all": sum(dl), "tc": tc, "sigs": sigs,
          "post": {t: a.tobytes() for t, a in post.items()}}
    try:
        _tpost_write(ix, bm25mod)
    except Exception:
        pass
    _TPOST["ix"] = ix
    return ix


# The same lane for ordinary memories longer than bm25's head budget. read_doc keeps a memory's first MAX_DOC_TOKENS
# tokens (slug x3 + body); the rest was invisible to the lexical lane. Measured through fused recall on 60 long memories
# (codex_fleet/recall_warm/tools/long_tail_ab.py, 31 Oct): a line past the cut finds its memory in the top 10 23 -> 53
# (37 better, 1 worse), a line inside it 49 -> 49 (1 better, 2 worse). Each tail is cached per file (mtime, size) in
# MTAIL_CACHE, so a rescan re-reads only files that changed; files under MTAIL_MIN_BYTES are never long (0 of 22,300,
# 31 Oct). Recall reads the tails from TAIL_POST below. BRAIN_RECALL_MTAIL=0 turns this half off.
MTAIL_CACHE = os.path.join(CTAIL_DIR, ".mtail_index.marshal")
MTAIL_MIN_BYTES = 5000
_MTAIL = {}
_MTAIL_FM = re.compile(r"^---.*?---", re.S)
# A compaction node anchor's slug. Real memories also start with compaction_ (the hub, the coverage checks), so the
# prefix alone is not the test.
_ANCHOR = re.compile(r"^compaction_(claude|codex)_[0-9a-f]{8}_([0-9a-f]+_)?20\d{6}T\d{6}")


def _mtail_index(bm25mod, rescan=False):
    """(tails, tdf) for memories whose read_doc head is cut: slug -> (term counts, length) of the dropped tokens.
    rescan=True stats every candidate file even when the memory folder has not moved (an edit in place)."""
    import marshal
    mem = bm25mod.MEM
    try:
        dkey = os.stat(mem).st_mtime_ns
    except OSError:
        return {}, {}
    if _MTAIL.get("dkey") == dkey and not rescan:
        return _MTAIL["ix"]
    files = _MTAIL.get("files")
    if files is None:
        try:
            with open(_mem_scoped(MTAIL_CACHE, bm25mod), "rb") as fh:
                k, files = marshal.load(fh)
            if k == dkey and not rescan:
                ix = _mtail_fold(files)
                _MTAIL.update(dkey=dkey, files=files, ix=ix)
                return ix
        except Exception:
            files = {}
    plumb = lambda m: re.sub(r"^(name|metadata|node_type|type|originSessionId).*$", "", m.group(0), flags=re.M)
    new = {}
    for e in os.scandir(mem):
        n = e.name
        if not n.endswith(".md") or _ANCHOR.match(n) or n[:1].isupper():
            continue
        try:
            st = e.stat()
        except OSError:
            continue
        if st.st_size < MTAIL_MIN_BYTES:
            continue
        sig = (st.st_mtime_ns, st.st_size)
        old = files.get(n)
        if old is not None and tuple(old[0]) == sig:
            new[n] = old
            continue
        try:
            with io.open(e.path, encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except OSError:
            continue
        sw = n[:-3].replace("_", " ")
        tks = bm25mod.tokens(sw + " " + sw + " " + sw + " " + _MTAIL_FM.sub(plumb, body, count=1))
        rest = tks[bm25mod.MAX_DOC_TOKENS:bm25mod.MAX_DOC_TOKENS + bm25mod.MAX_ATTACHMENT_TOKENS]
        tf = {}
        for t in rest:
            tf[t] = tf.get(t, 0) + 1
        new[n] = (sig, tf, len(rest))
    try:
        if _mem_scope_throwaway(bm25mod):
            raise OSError("a throwaway memory folder keeps its tails in memory")
        cache = _mem_scoped(MTAIL_CACHE, bm25mod)
        tmp = "%s.%d.tmp" % (cache, os.getpid())
        with open(tmp, "wb") as fh:
            marshal.dump((dkey, new), fh)
        os.replace(tmp, cache)
    except Exception:
        pass
    ix = _mtail_fold(new)
    _MTAIL.update(dkey=dkey, files=new, ix=ix)
    return ix


def _mtail_fold(files):
    tails, tdf = {}, {}
    for n, (_sig, tf, dl) in files.items():
        if dl:
            tails[n[:-3]] = (tf, dl)
            for t in tf:
                tdf[t] = tdf.get(t, 0) + 1
    return tails, tdf


def _mtail_cached(sigs, bm25mod=None):
    """The tails of the last scan as MTAIL_CACHE holds them, without statting the folder, or None when that scan's long
    memories are not exactly `sigs` (the scan the postings were built from)."""
    import marshal
    files = _MTAIL.get("files")
    if files is None:
        try:
            with open(_mem_scoped(MTAIL_CACHE, bm25mod), "rb") as fh:
                files = marshal.load(fh)[1]
        except Exception:
            return None
    try:
        if {n: tuple(v[0]) for n, v in files.items() if v[2]} != {n: tuple(s) for n, s in sigs.items()}:
            return None
        return _mtail_fold(files)[0]
    except Exception:
        return None


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
        # the call's one listing of the memory folder (R19) instead of a second scandir (29 Oct 2026, RECALL-10X
        # lead 6: 1.3 s -> 0.25 s per recall on the 38k-entry folder, output equal)
        got = _memlist()
        out = None
        if got is not None:
            try:
                if os.path.normcase(os.path.abspath(bm.MEM)) == os.path.normcase(os.path.abspath(MEM)):
                    out = bm.fresh_memories(bm.MEM, bm.INDEX, entries=got[0])
            except TypeError:
                out = None
        if out is None:
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

    def __init__(self, bm, con, pos_rows, base_df, n_old, sum_old, fresh, gone, docs, maps=None):
        # pos_rows: the sidecar's (position, slug) rows in position order. Positions are ordered, not always 0..n-1
        # (bm25 I1 places a changed document past the base's last position); the fresh documents rank after them all.
        # maps: (pos_slug, slug_pos, base) already built from those rows (_fc_rows keeps them per sidecar); read only.
        self._bm, self._con, self._base_df = bm, con, base_df
        if maps is None:
            maps = (dict(pos_rows), {s: p for p, s in pos_rows}, pos_rows[-1][0] + 1 if pos_rows else 0)
        self._pos_slug, slug_pos, self._base = maps
        self._blobs, self._dfc, self._fallback = {}, {}, None
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
        # A recall on another memory folder (BRAIN_MEM_DIR on the ai-memories clone, as the recall tests set it) finds
        # ~16.7k indexed memories gone, and looking up each one's old length was 9.5 s of an 11.8 s call (3 Nov 2026).
        # The sidecar's fp row hashes a document's words, counts and length, so a length cached under that hash is the
        # length _old_dl would read; unchanged documents keep their hash across generations, so the cache outlives the
        # index's minute-by-minute publishes. BRAIN_RECALL_OLDDL_CACHE=0 turns it off.
        fps, dlc, dlc_new = {}, {}, False
        if os.environ.get("BRAIN_RECALL_OLDDL_CACHE", "1") != "0" and len(fresh) + len(gone) > 64:
            try:
                fps = dict(con.execute("select pos, h from fp").fetchall())
                dlc = _fc_get("olddl", ("olddl-v1",)) or {}
            except Exception:
                fps, dlc = {}, {}
        for slug in list(fresh) + list(gone):
            p = slug_pos.get(slug)
            if p is not None and p not in self._dropped:
                h = fps.get(p)
                dl = dlc.get(h) if h is not None else None
                if dl is None:
                    dl = self._old_dl(slug, p, docs.get(slug))
                    if h is not None:
                        dlc[h] = dl
                        dlc_new = True
                self._dropped[p] = dl
        if dlc_new:
            live = set(fps.values())
            _fc_put("olddl", ("olddl-v1",), {h: v for h, v in dlc.items() if h in live}, keep=1)
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
        then of its new text. Each group's df is read in one batch, and the text group only when the name finds nothing
        (it nearly always does: one re-read memory asked df of its 283 words one query at a time for a name hit)."""
        bdf, seen, left = self._base_df, set(), 64
        pf = getattr(bdf, "prefetch", None)
        for group in (self._bm.tokens(slug.replace("_", " ")), tks or ()):
            u = {t for t in group if t not in seen}
            if pf is not None and u:
                pf(u)
            g = sorted({t for t in u if bdf.get(t, 0) > 0}, key=lambda t: (bdf[t], t))
            seen.update(g)
            for t in g[:left]:
                b = self._blob(t)
                i = _fc_find(b, pos)
                if i >= 0:
                    return _fc_struct.unpack_from("<I", b, i * 12 + 8)[0]
            left -= min(left, len(g))
            if not left:
                break
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
        if (bm.read_merge_frozen(bm.INDEX) if hasattr(bm, "read_merge_frozen") else bm.freeze_state(bm.INDEX)[0]):
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
        maps = _fc_rows(con, p, h, n_old)
        base_df = _fc_vocab_df(con)
        if base_df is None:
            raise LookupError("no vocabulary")
        if set(fresh) & set(gone):
            raise ValueError("a slug both fresh and gone")
        # each listing of a slug re-appends it, so a slug listed twice sits where it was listed last
        order = list(dict.fromkeys(reversed(fresh)))[::-1]
        docs = {slug: bm.read_doc(bm.MEM, slug) for slug in order}
        ov = _FcOverlay(bm, con, None, base_df, n_old, sum_old, order, gone, docs, maps=maps)
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


def _fc_rows(con, path, hdr, n_old):
    """(pos_slug, slug_pos, base) of the postings sidecar open on `con`, at `path`: its d table read, checked (n rows,
    positions increasing from >= 0) and turned into the two dicts once per sidecar identity. Every folder drop used to
    do all three again, about 0.14 s warm on 51k rows (overlay_split, 4 Nov 2026). A sidecar is built under a temporary
    name and renamed into place, and os.replace over an open sqlite file is refused on Windows, so the path's stat plus
    the header keys it. The dicts are shared by every overlay of that sidecar and only read. BRAIN_OVL_ROWC=0 reads the
    table every call."""
    key = None
    if os.environ.get("BRAIN_OVL_ROWC", "1") != "0":
        try:
            st = os.stat(path)
            key = (os.path.abspath(path), st.st_ino, st.st_size, st.st_mtime_ns,
                   hdr.get("signature"), hdr.get("n"), hdr.get("avgdl"))
        except Exception:
            key = None
        v = _FC_ROWS.get(key) if key is not None else None
        if v is not None:
            return v
    rows = con.execute("select pos, slug from d order by pos").fetchall()
    if len(rows) != n_old or rows[0][0] < 0 or any(rows[i][0] >= rows[i + 1][0] for i in range(len(rows) - 1)):
        raise ValueError("the d table is not n increasing positions")
    v = (dict(rows), {s: p for p, s in rows}, rows[-1][0] + 1)
    if key is not None:
        _FC_ROWS.clear()
        _FC_ROWS[key] = v
    return v


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
    # sorted-order index, as Diffusion._load (2 Nov); BRAIN_WALK_NAME_INDEX=0 keeps the dict. An older diffusion.py
    # beside this file has no _name_index (6 Nov 2026): the dict, as before.
    _ni = getattr(_diff, "_name_index", None)
    g.id = _ni(g.names, path, store) if _ni is not None else {n: i for i, n in enumerate(g.names)}
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


# A recall only ever asks df about a few hundred words: the query's, their one-edit spellings, a fresh memory's. Loading
# the whole 393k-term map for that cost about 0.18 s of every one-shot recall (30 Oct 2026, fresh processes, load ~19);
# the sidecar's own p table carries the same df per term (equal on all 393,256 terms) under a primary key, so this
# reads the words asked about, a batch at a time, and loads the full map only for something that iterates it.
# Each misspelt or rare query word asks about its ~600 one-edit spellings (about 15 ms); a query with fourteen of them
# cost as much as the full map, so past _FC_LAZY_DF_MAX words read the map is loaded after all.
# BRAIN_RECALL_LAZY_DF=0 goes back to the full map.
_FC_LAZY_DF = os.environ.get("BRAIN_RECALL_LAZY_DF", "1") != "0"
_FC_LAZY_DF_MAX = 6000


class _FcLazyDf:
    def __init__(self, dbpath, key):
        self._path, self._key = dbpath, key
        self._c, self._full, self._con = {}, None, None

    def prefetch(self, terms):
        if self._full is not None:
            return
        need = [t for t in set(terms) if t not in self._c]
        if len(self._c) + len(need) > _FC_LAZY_DF_MAX:
            self._load()
            return
        try:
            if need and self._con is None:
                import sqlite3
                self._con = sqlite3.connect("file:%s?mode=ro" % self._path.replace("?", "%3f"), uri=True,
                                            check_same_thread=False)
            for i in range(0, len(need), 900):
                chunk = need[i:i + 900]
                got = dict(self._con.execute("select term, df from p where term in (%s)" % ",".join("?" * len(chunk)),
                                             chunk))
                for t in chunk:
                    self._c[t] = got.get(t, 0)
        except Exception:
            self._load()

    def _load(self):
        if self._full is None:
            self._full = _fc_vocab_df_full(self._path, self._key) or {}
        return self._full

    def get(self, term, default=None):
        if self._full is None and term not in self._c:
            self.prefetch((term,))
        if self._full is not None:
            return self._full.get(term, default)
        return self._c.get(term) or default

    def __getitem__(self, term):
        v = self.get(term)
        if v is None:
            raise KeyError(term)
        return v

    def __contains__(self, term):
        return self.get(term) is not None

    def __iter__(self):
        return iter(self._load())

    def __len__(self):
        return len(self._load())

    def keys(self):
        return self._load().keys()

    def items(self):
        return self._load().items()

    def values(self):
        return self._load().values()


def _fc_vocab_df(con):
    """The df map of the postings sidecar open on `con` (BM25.df below and the fresh-memory overlay both read it): a
    _FcLazyDf, or with BRAIN_RECALL_LAZY_DF=0 the full map read once per process and kept on disk in marshal form under
    the sidecar's identity. None when the sidecar cannot be identified."""
    try:
        dbpath = con.execute("PRAGMA database_list").fetchone()[2]
        st = os.stat(dbpath)
        key = (os.path.abspath(dbpath), st.st_ino, st.st_size, st.st_mtime_ns)
    except Exception:
        return None
    df = _FC_DF.get(key)
    if df is None and _FC_LAZY_DF:
        df = _FcLazyDf(dbpath, key)
        _FC_DF.clear()
        _FC_DF[key] = df
        return df
    return df if df is not None else _fc_vocab_df_full(dbpath, key)


def _fc_vocab_df_full(dbpath, key):
    df = _FC_DF.get(key)
    if df is not None and not isinstance(df, _FcLazyDf):
        return df
    df = _fc_get("df", key)
    if df is None:
        import sqlite3
        con = None
        try:
            con = sqlite3.connect("file:%s?mode=ro" % dbpath.replace("?", "%3f"), uri=True)
            import pickle as _pickle
            import zlib as _zlib
            row = con.execute("select blob from vocab where k='df'").fetchone()
            if row:
                df = _pickle.loads(_zlib.decompress(row[0]))
                _fc_put("df", key, df, keep=2)
        except Exception:
            df = None
        finally:
            if con is not None:
                con.close()
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


# THE LISTING WITH ITS STATS IN ONE WALK (30 Oct 2026, Mac). On macOS a DirEntry has no stat of its own: the readers of
# the call's listing (supersession's key, the embedding row check, the fresh-memory scan) made one stat call per file,
# 28k of them, about 0.2 CPU-s (mostly kernel) and 0.3-0.65 s wall at load 25. getattrlistbulk returns the names with
# their size and times a few hundred at a time; the listing plus stats measured 0.09 CPU-s, and matched os.stat on all
# 28,467 files (size, mtime_ns, ctime_ns, ino). The stand-in entries carry only what those readers ask for; anything
# but a regular file or a folder in the listing, or any error, and the call lists with os.scandir as before.
# BRAIN_RECALL_BULKSTAT=0 turns it off.
_BULK = []


class _BulkStat:
    __slots__ = ("st_size", "st_mtime_ns", "st_ctime_ns", "st_mtime", "st_ctime", "st_ino")


class _BulkEntry:
    __slots__ = ("name", "_folder", "_dir", "_st")

    @property
    def path(self):                         # joined on demand: few readers want it, and 28k joins cost ~0.03 CPU-s
        return os.path.join(self._folder, self.name)

    def is_file(self, follow_symlinks=True):
        return not self._dir

    def is_dir(self, follow_symlinks=True):
        return self._dir

    def is_symlink(self):
        return False

    def inode(self):
        return self._st.st_ino

    def stat(self, follow_symlinks=True):
        if self._dir:                       # a folder's size is not in the walk: ask for it, as a DirEntry would
            return os.stat(self.path)
        return self._st


def _bulk_listing(folder):
    """[_BulkEntry] for every entry of `folder`, or None (not macOS, switched off, an entry neither file nor folder,
    any error)."""
    if sys.platform != "darwin" or os.environ.get("BRAIN_RECALL_BULKSTAT", "1") == "0":
        return None
    try:
        import ctypes
        if not _BULK:

            class _AttrList(ctypes.Structure):
                _fields_ = [("bitmapcount", ctypes.c_ushort), ("reserved", ctypes.c_uint16),
                            ("commonattr", ctypes.c_uint32), ("volattr", ctypes.c_uint32), ("dirattr", ctypes.c_uint32),
                            ("fileattr", ctypes.c_uint32), ("forkattr", ctypes.c_uint32)]
            # libc by its path: find_library("c") spends 19-37 ms a recall looking for what is always here (2 Nov 2026)
            try:
                libc = ctypes.CDLL("/usr/lib/libc.dylib", use_errno=True)
            except OSError:
                import ctypes.util
                libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
            fn = libc.getattrlistbulk
            fn.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint64]
            fn.restype = ctypes.c_int
            # returned attrs, name, objtype, crtime, modtime, chgtime, fileid; file data length. Packed in bit order.
            al = _AttrList(5, 0, 0x80000000 | 0x1 | 0x8 | 0x200 | 0x400 | 0x800 | 0x2000000, 0, 0, 0x200, 0)
            _BULK.append((fn, al))
        fn, al = _BULK[0]
        size = 256 * 1024
        buf = ctypes.create_string_buffer(size)
        unpack = _fc_struct.unpack_from
        out = []
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            while True:
                n = fn(fd, ctypes.byref(al), buf, size, 0x8)       # FSOPT_PACK_INVAL_ATTRS: a fixed layout
                if n < 0:
                    return None
                if n == 0:
                    return out
                raw = buf.raw
                off = 0
                for _ in range(n):
                    length, = unpack("<I", raw, off)
                    p = off + 24                                    # length + attribute_set_t (5 x u32)
                    name_off, name_len = unpack("<iI", raw, p)
                    objtype, _cs, _cn, ms, mn, cs, cn, ino, dlen = unpack("<IqqqqqqQq", raw, p + 8)
                    if objtype not in (1, 2):                       # VREG, VDIR
                        return None
                    e = _BulkEntry()
                    e.name = raw[p + name_off:p + name_off + name_len - 1].decode("utf-8", "surrogateescape")
                    e._folder = folder
                    e._dir = objtype == 2
                    st = _BulkStat()
                    st.st_size = dlen
                    st.st_mtime_ns, st.st_ctime_ns = ms * 1_000_000_000 + mn, cs * 1_000_000_000 + cn
                    st.st_mtime, st.st_ctime = ms + mn * 1e-9, cs + cn * 1e-9
                    st.st_ino = ino
                    e._st = st
                    out.append(e)
                    off += length
        finally:
            os.close(fd)
    except Exception:
        return None


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
                lst = _fast_listing(MEM)
                if lst is None:
                    lst = _bulk_listing(MEM)
                if lst is not None:
                    got = (lst, t)
                else:
                    with os.scandir(MEM) as it:
                        got = (list(it), t)
            except Exception:
                got = False
            _MEMLIST["got"] = got
    return got or None


# FAST LISTING (29 Oct 2026, RECALL-10X lead 6). The call's one listing of the 38k-entry memory folder was the largest
# piece left on recall's main thread (2-7 s inclusive under load). One FindFirstFileExW pass with FindExInfoBasic and
# FIND_FIRST_EX_LARGE_FETCH returns the same names in the same order with the same attributes, size and times that a
# Windows DirEntry carries (its stat also comes from the listing; ino, dev and nlink are 0 there too), so _FastEntry
# answers name, path, is_file, is_dir, is_symlink and stat() as DirEntry does. Equal on all 38,423 entries; median
# 1.12 s against scandir's 2.22 s over 12 rotated rounds on the loaded laptop. None (scandir is used) off Windows, on
# any API error, on a reparse point, or with BRAIN_RECALL_FASTLIST=0.
_FL_API = []
_FL_EPOCH = 116444736000000000


class _FastStat(object):
    __slots__ = ("st_size", "st_mtime_ns", "st_ctime_ns", "st_birthtime_ns", "st_atime_ns", "st_file_attributes",
                 "st_mode")
    st_ino = st_dev = st_nlink = st_uid = st_gid = st_reparse_tag = 0

    @staticmethod
    def _f(ns):
        s, n = divmod(ns, 1000000000)
        return s + n * 1e-9

    st_mtime = property(lambda self: self._f(self.st_mtime_ns))
    st_ctime = property(lambda self: self._f(self.st_ctime_ns))
    st_birthtime = property(lambda self: self._f(self.st_birthtime_ns))
    st_atime = property(lambda self: self._f(self.st_atime_ns))


class _FastEntry(object):
    __slots__ = ("name", "path", "_st", "_dir")

    def is_dir(self, follow_symlinks=True):
        return self._dir

    def is_file(self, follow_symlinks=True):
        return not self._dir

    def is_symlink(self):
        return False

    def is_junction(self):
        return False

    def stat(self, follow_symlinks=True):
        return self._st

    def inode(self):
        return os.stat(self.path, follow_symlinks=False).st_ino

    def __fspath__(self):
        return self.path


def _fast_listing(folder):
    """[_FastEntry] in os.scandir's order for folder, or None (the caller lists with os.scandir)."""
    if os.name != "nt" or os.environ.get("BRAIN_RECALL_FASTLIST", "1") == "0":
        return None
    try:
        import ctypes
        if not _FL_API:
            import struct
            from ctypes import wintypes

            class _FT(ctypes.Structure):
                _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

            class _WFD(ctypes.Structure):
                _fields_ = [("attr", wintypes.DWORD), ("ctime", _FT), ("atime", _FT), ("mtime", _FT),
                            ("size_hi", wintypes.DWORD), ("size_lo", wintypes.DWORD), ("r0", wintypes.DWORD),
                            ("r1", wintypes.DWORD), ("name", ctypes.c_wchar * 260), ("alt", ctypes.c_wchar * 14),
                            ("x0", wintypes.DWORD), ("x1", wintypes.DWORD), ("x2", wintypes.WORD)]

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            first = k32.FindFirstFileExW
            first.restype = wintypes.HANDLE
            first.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(_WFD), ctypes.c_int, ctypes.c_void_p,
                              wintypes.DWORD]
            nxt = k32.FindNextFileW
            nxt.restype = wintypes.BOOL
            nxt.argtypes = [wintypes.HANDLE, ctypes.POINTER(_WFD)]
            close = k32.FindClose
            close.restype = wintypes.BOOL
            close.argtypes = [wintypes.HANDLE]
            # attr, ctime, atime, mtime, size_hi, size_lo: the first 36 bytes of WIN32_FIND_DATAW
            _FL_API.append((_WFD, first, nxt, close, wintypes.HANDLE(-1).value, struct.Struct("<IQQQII").unpack_from))
        _WFD, first, nxt, close, invalid, unpack = _FL_API[0]
        folder = os.fspath(folder)
        d = _WFD()
        ref = ctypes.byref(d)
        h = first(os.path.join(folder, "*"), 1, ref, 0, None, 2)   # FindExInfoBasic, FIND_FIRST_EX_LARGE_FETCH
        if h is None or h == invalid:
            return None
        out = []
        join, sb, addr, E = os.path.join, ctypes.string_at, ctypes.addressof(d), _FL_EPOCH
        try:
            while True:
                attr, c, a, m, shi, slo = unpack(sb(addr, 36))
                name = d.name
                if name != "." and name != "..":
                    if attr & 0x400:            # reparse point: scandir follows it for is_file and stat
                        return None
                    st = _FastStat()
                    st.st_size = (shi << 32) | slo
                    st.st_mtime_ns = (m - E) * 100
                    c = (c - E) * 100
                    st.st_ctime_ns = c
                    st.st_birthtime_ns = c
                    st.st_atime_ns = (a - E) * 100
                    st.st_file_attributes = attr
                    isdir = bool(attr & 0x10)
                    st.st_mode = (0o40111 if isdir else 0o100000) | (0o444 if attr & 0x1 else 0o666)
                    e = _FastEntry()
                    e.name = name
                    e.path = join(folder, name)
                    e._st = st
                    e._dir = isdir
                    out.append(e)
                if not nxt(h, ref):
                    if ctypes.get_last_error() != 18:   # ERROR_NO_MORE_FILES
                        return None
                    break
        finally:
            close(h)
        return out
    except Exception:
        return None


def _memlist_md(got):
    """supersession._md_files over the listing: the .md names that are files, [] on any error as it does. Filtered once
    per listing (a recall asked four times) and handed out as a copy."""
    md = _MEMLIST.get("md")
    if md is None or md[0] is not got:
        try:
            md = (got, [e for e in got[0] if e.name.endswith(".md") and e.is_file()])
        except Exception:
            return []
        if _MEMLIST.get("got") is got:
            _MEMLIST["md"] = md
    return list(md[1])


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


def _fc_mem_names_wrap(real):
    """spread_recall._mem_md_names (the filename seed arm's listing) read from the call's listing when spread_recall
    looks at this memory folder: the listing is taken once per recall anyway, in listdir's order (5/5 on 30 Oct), so
    the arm stops paying its own os.listdir of 28.5k names (18 -> 3.5 ms)."""
    mem = os.path.normcase(os.path.abspath(MEM))

    def _mem_md_names():
        got = _memlist()
        if got is not None:
            try:
                same = os.path.normcase(os.path.abspath(sr.MEM)) == mem
            except Exception:
                same = False
            if same:
                return [e.name[:-3] for e in got[0] if e.name.endswith(".md")]
        return real()
    _mem_md_names._r02 = True
    return _mem_md_names


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
    # Names NUL-joined and the stat keys as packed int64s, not repr() of 28k tuples: 42 -> 19 ms a recall (30 Oct). The
    # digest is only this process's cache key, so the new format costs one miss. A non-int stat value takes the old path.
    import array
    # BRAIN_RECALL_SIG_YOUNG_HASH: a young file is keyed by its bytes as well as its stat key, instead of making the
    # whole recall skip the cache. Agents file a memory every few minutes, so 7 of 15 recalls 20 s apart found one
    # younger than 6 s and paid the full read (~380 ms thread CPU, 0.8-1.6 s wall) (30 Oct). On by default after 120
    # synthetic calls and 20 live ones matched the real reader with no miss; =0 restores the skip.
    young_hash = os.environ.get("BRAIN_RECALL_SIG_YOUNG_HASH", "1") not in ("", "0")
    names, nums, rows, young = [], array.array("q"), [], []
    for e in entries:
        k = ss._stat_key(e.stat())
        if now_ns - k[1] < 6_000_000_000:
            if not young_hash or len(young) >= 32:
                return None
            young.append(e)
        names.append(e.name)
        rows.append(k)
    if not names:
        return None
    if young:
        yh = hashlib.sha256()
        try:
            for e in young:
                with open(e.path, "rb") as fh:
                    yh.update(e.name.encode("utf-8", "surrogateescape") + b"\0" + hashlib.sha256(fh.read()).digest())
        except OSError:
            return None
        # a third field marks a key that holds young bytes: the caller stores under it only if a second read agrees
        tail = (yh.hexdigest(),)
    else:
        tail = ()
    try:
        for k in rows:
            nums.extend(k)
    except TypeError:
        return (hashlib.sha256(repr(list(zip(names, rows))).encode("utf-8", "surrogateescape")).hexdigest(),
                len(names)) + tail
    h = hashlib.sha256("\0".join(names).encode("utf-8", "surrogateescape"))
    h.update(nums.tobytes())
    return (h.hexdigest(), len(names)) + tail


_SUP_DELTA_MAX = 64   # files a delta may look at; a larger change goes to the real reader


_SUPROWC_ON = os.environ.get("BRAIN_SUP_ROWC", "1") != "0"
_SUPROWC = {}


def _sup_rows(ss):
    """supersession's view of the memory folder as [(name, stat key)] in listing order, and the moment it was taken:
    the call's one listing inside a recall (R19), else a listing of its own."""
    got = _memlist()
    if got is not None and _SUPROWC_ON:
        # The call's listing is the recall server's resident one, which keeps an unchanged file's entry object and
        # replaces it when the folder watcher reports that name; so an entry object seen before has the stat already
        # read: name -> (entry, row or None). Same answer as the list below on 22 of 22 forced folder drops, row build
        # 112 -> 19 ms on the 51k-entry folder (4 Nov 2026, claude-738954b6, TOP10 rank 3,
        # recall_10x_2026_10_29/folder_drop_2026_11_04/incr_bench.py, incr_parity.py). BRAIN_SUP_ROWC=0 turns it off.
        try:
            ents, now_ns = got[0], got[1]
            out, get, sk = [], _SUPROWC.get, ss._stat_key
            for e in ents:
                n = e.name
                v = get(n)
                if v is not None and v[0] is e:
                    if v[1] is not None:
                        out.append(v[1])
                    continue
                r = (n, tuple(sk(e.stat()))) if (n.endswith(".md") and e.is_file()) else None
                _SUPROWC[n] = (e, r)
                if r is not None:
                    out.append(r)
            if len(_SUPROWC) > 2 * len(ents) + 1000:
                _SUPROWC.clear()
            return out, now_ns
        except Exception:
            _SUPROWC.clear()
    if got is not None:
        entries, now_ns = _memlist_md(got), got[1]
    else:
        now_ns = time.time_ns()
        entries = ss._md_files(MEM)
    return [(e.name, tuple(ss._stat_key(e.stat()))) for e in entries], now_ns


def _sup_base(ss, rows):
    """The pointer facts behind the answer the real reader just gave, from supersession's own frontmatter cache:
    {name: (strict, candidate)} for every file that carries a pointer, and the case-folded names a route can touch
    (pointer holders, their targets and candidates, both sides of every index line). None unless that cache describes
    exactly this listing (another process may have rewritten it since)."""
    try:
        _fmp = (ss._fm_cache_path(MEM) if hasattr(ss, "_fm_cache_path")
                else os.path.join(ss.HERE, ss._FM_CACHE_NAME))
        with io.open(_fmp, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("version") != ss._FM_CACHE_VERSION or data.get("memory_dir") != os.path.abspath(MEM):
            return None
        files = data["files"]
        ptr, miss = {}, []
        for name, k in rows:
            v = files.get(name)
            if not (isinstance(v, list) and len(v) == 6 and tuple(v[:4]) == k):
                miss.append((name, k))
                continue
            if v[4] or v[5]:
                ptr[name] = (v[4], v[5])
        if len(miss) > 256:
            return None
        for name, k in miss:
            p = os.path.join(MEM, name)
            head = ss._read_frontmatter(p)
            if tuple(ss._stat_key(os.stat(p))) != tuple(k):
                return None
            strict = ss._frontmatter_target(head)
            cand = "" if strict else ss._lenient_candidate(head)
            if strict or cand:
                ptr[name] = (strict, cand)
        nc = os.path.normcase
        inv = set()
        for name, pair in ptr.items():
            inv.add(nc(name[:-3]))
            inv.update(nc(t) for t in pair if t)
        with io.open(SUPERSEDED_INDEX, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                inv.update(nc(t.strip()) for t in line.rstrip("\n").split("\t")[:2] if t.strip())
        return ptr, sorted(inv)
    except Exception:
        return None


def _sup_quiet(ss, changed, cur, ptr, inv):
    """True when no changed file can move the answer: it carried no pointer before, carries none now, and no pointer
    or index line names it. Then the routes are the same and the file only adds or drops a slug nothing resolves
    through, so active_supersessions gives the same map."""
    nc = os.path.normcase
    for name in changed:
        if name in ptr or nc(name[:-3]) in inv:
            return False
        if name in cur:
            try:
                head = ss._read_frontmatter(os.path.join(MEM, name))
                if ss._frontmatter_target(head) or ss._lenient_candidate(head):
                    return False
            except Exception:
                return False
    return True


# The memo below outlives the call (6 Nov 2026, Mac): _fc_layer builds a new wrap for every recall, so a memo made
# inside it was empty each time and the resident server rebuilt the 46k-row digest on every call (44 ms of 200, 12 of
# 12 calls in a profile). Every entry is checked against its own key or rows before it is used.
_FC_SUP_MEMO = {}


def _fc_superseded_wrap(real):
    """brain._load_superseded (active_supersessions with frontmatter authority) read once per state of the memory
    folder, and carried across other agents' writes. The head entry, keyed by the index file and supersession.py,
    holds the digest of the folder's (name, stat key) listing, the answer, and the pointer facts behind it; the listing
    itself is stored beside it. Same digest and no file under 6 s old: the answer. Otherwise the files whose key moved
    (or that are that young, or that went away) are looked at; when none carries a pointer or is named by one
    (_sup_quiet), the answer cannot have moved and is served, and the head moves to the new listing. Anything else,
    or more than _SUP_DELTA_MAX files, goes to the real reader, whose answer is stored under the listing taken before
    the call, with the pointer facts read back from supersession's own cache for that same listing.
    (29 Oct 2026: the old key was the digest alone, so any write to the folder between two recalls cost the next one
    the real reader, about 2.7 s on the loaded laptop; a file under 6 s old disabled the cache outright.)"""
    # In-process memo (3 Nov 2026, claude-738954b6): the resident server served every recall through the delta path,
    # because CONSCIENCE.md is rewritten between almost any two calls; each call re-made repr() of the 50k-row listing
    # (0.28 s) and re-read and re-dicted the stored listing (0.16 s). The digest is now joined from per-row repr
    # strings kept across calls (the same string, so the same digest), and the last stored listing is kept as a dict.
    # BRAIN_SUP_MEMO=0 turns it off.
    memo = _FC_SUP_MEMO

    def _sig(rows):
        if os.environ.get("BRAIN_SUP_MEMO", "1") == "0":
            return hashlib.sha256(repr(rows).encode("utf-8", "surrogateescape")).hexdigest()
        # An unchanged folder gives back the same row tuples (_sup_rows' entry cache), so comparing with the last rows
        # is a pointer walk; the digest is the one the last call made. 74 -> 0.5 ms a call on 52k rows (4 Nov 2026,
        # claude-738954b6, recall_10x_2026_10_29/served_profile_20261104/sigbench.py).
        last = memo.get("last")
        if last is not None and last[0] == rows:
            return last[1]
        s = _sig_build(rows)
        memo["last"] = (rows, s)
        return s

    def _sig_build(rows):
        rr = memo.get("rr")
        if rr is None or len(rr) > 2 * len(rows) + 1000:
            rr = memo["rr"] = {}
        parts = []
        for n, k in rows:
            v = rr.get(n)
            if v is None or v[0] != k:
                v = rr[n] = (k, repr((n, k)))
            parts.append(v[1])
        return hashlib.sha256(("[" + ", ".join(parts) + "]").encode("utf-8", "surrogateescape")).hexdigest()

    def _old_rows(key, hsig):
        on = os.environ.get("BRAIN_SUP_MEMO", "1") != "0"
        got = memo.get("old") if on else None
        if got is not None and got[0] == (key, hsig):
            return got[1]
        old = _fc_get("superseded_rows", (key, hsig))
        if not isinstance(old, list):
            return None
        old = dict(old)
        if on:
            memo["old"] = ((key, hsig), old)
        return old

    def _load_superseded():
        try:
            import supersession as _ss
            rows, now_ns = _sup_rows(_ss)
            key = (os.path.abspath(MEM), _fc_ident(SUPERSEDED_INDEX), _fc_ident(_ss.__file__), _ss._FM_CACHE_VERSION)
        except Exception:
            return real()
        if not rows:
            return real()
        recent = {n for n, k in rows if now_ns - k[1] < 6_000_000_000}
        sig = _sig(rows)
        head = _fc_get("superseded_head", key)
        if isinstance(head, (list, tuple)) and len(head) == 4 and isinstance(head[1], dict):
            hsig, ans, ptr, inv = head
            if hsig == sig and not recent:
                return ans
            old = _old_rows(key, hsig)
            if old is not None:
                cur = dict(rows)
                changed = [n for n, k in rows if n in recent or old.get(n) != k]
                changed += [n for n in old if n not in cur]
                if len(changed) <= _SUP_DELTA_MAX and _sup_quiet(_ss, changed, cur, ptr, set(inv)):
                    if not recent and hsig != sig:
                        _fc_put("superseded_rows", (key, sig), rows, keep=2)
                        _fc_put("superseded_head", key, (sig, ans, ptr, inv), keep=2)
                        if os.environ.get("BRAIN_SUP_MEMO", "1") != "0":
                            memo["old"] = ((key, sig), cur)   # the rows just stored under sig, as the next call reads them
                    return ans
        res = real()
        if isinstance(res, dict) and _fc_ident(SUPERSEDED_INDEX) == key[1]:
            base = _sup_base(_ss, rows)
            if base is not None:
                _fc_put("superseded_rows", (key, sig), rows, keep=2)
                _fc_put("superseded_head", key, (sig, res, base[0], base[1]), keep=2)
        return res
    _load_superseded._r02 = True
    return _load_superseded


def _fc_att_row(r):
    """A copy of one corpus row, either shape: (slug, dl, counts) from these node docs or (slug, tokens) from an older
    node-docs module (6 Nov 2026): unpacking three on pair rows raised inside the store's try, so the memo never filled
    there and every recall reloaded and re-verified every bundle. Stored as attcorpus4, so a brain.py from before this
    fix never reads pair rows under the name it unpacks as triples."""
    return tuple(dict(x) if isinstance(x, dict) else list(x) if isinstance(x, list) else x for x in r)


def _fc_attach_corpus_wrap(nd, real):
    """node_docs._search_corpus (every bundle loaded, verified and tokenized: 0.12-0.23 s of a one-shot recall, 30 Oct
    2026) kept under the identity of every file in the bundle store (size, mtime, inode, ctime) and the module. A miss
    runs the real loader, verification and lock included, and is stored only when no bundle failed and the store did
    not move during the call; a bundle whose bytes change gets a new key and is verified again."""
    def _key(mem):
        root = os.path.join(os.fspath(mem), nd.BUNDLE_DIRNAME)
        rows = []
        for dp, dn, fn in os.walk(root):
            dn.sort()
            for f in sorted(fn):
                st = os.lstat(os.path.join(dp, f))
                rows.append((os.path.relpath(dp, root), f, st.st_size, st.st_mtime_ns, st.st_ino, st.st_ctime_ns))
        return (os.path.abspath(root), tuple(rows), _fc_ident(nd.__file__))

    def _search_corpus(mem, failures=None):
        try:
            key = _key(mem)
        except Exception:
            return real(mem, failures)
        v = _fc_get("attcorpus4", key)
        if isinstance(v, list):
            return [_fc_att_row(r) for r in v]
        n0 = len(failures) if failures is not None else 0
        res = real(mem, failures)
        try:
            if (failures is None or len(failures) == n0) and _key(mem) == key:
                _fc_put("attcorpus4", key, [_fc_att_row(r) for r in res])
        except Exception:
            pass
        return res
    _search_corpus._r02 = True
    return _search_corpus


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
            if hasattr(sr, "_mem_md_names") and not getattr(sr._mem_md_names, "_r02", False):
                undo.append((sr, "_mem_md_names", sr._mem_md_names))
                sr._mem_md_names = _fc_mem_names_wrap(sr._mem_md_names)
        except Exception:
            pass
        try:
            if not getattr(sr._load_graph, "_r02", False):
                undo.append((sr, "_load_graph", sr._load_graph))
                sr._load_graph = _fc_spread_graph_wrap(sr, sr._load_graph)
        except Exception:
            pass
        # the seed arm's filename match reads this call's listing instead of a second os.listdir (3 Nov 2026,
        # claude-738954b6; 2.2 s a warm served call). BRAIN_SEED_ONESCAN=0 turns it off.
        if opened and os.environ.get("BRAIN_SEED_ONESCAN", "1") != "0" and getattr(sr, "MEM_NAMES", 0) is None:
            def _seed_names():
                got = _memlist()
                return [e.name for e in got[0]] if got else None
            undo.append((sr, "MEM_NAMES", None))
            sr.MEM_NAMES = _seed_names
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
            if not getattr(node_docs._search_corpus, "_r02", False):
                undo.append((node_docs, "_search_corpus", node_docs._search_corpus))
                node_docs._search_corpus = _fc_attach_corpus_wrap(node_docs, node_docs._search_corpus)
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
        # numpy 2.2 on Apple Accelerate raises divide/overflow/invalid warnings from a clean matmul (30 Oct: every
        # row finite and unit-norm, result within 1e-7 of a float64 einsum); they only scared readers of the CLI
        with np.errstate(all="ignore"):
            cos = mat @ c
        out = {}
        # Exact text validation is an I/O gate, so validate a generous semantic oversample rather
        # than all ~1,800 rows on every deliberate recall. The top 256 is far beyond any displayed
        # result set while cutting warm validation from ~430 ms to tens of milliseconds.
        # Rank only active rows. Digest-bound publication masks and local stat drift can leave
        # inactive matrix rows; letting them enter the top-256 could crowd out every safe result.
        _passed = set()
        if os.environ.get("BRAIN_EMB_LAZY") == "1":
            _order = sorted(
                ((slug, int(row)) for slug, row in idx_e.items()),
                key=lambda item: -float(cos[item[1]]),
            )
            # lazy load: idx_e holds every row, current or not, so walk the cosine order until 256 pass
            candidates = []
            for _s, _i in _order:
                if row_current(_s):
                    candidates.append((_s, _i))
                    _passed.add(_s)
                    if len(candidates) >= 256:
                        break
        else:
            # numpy stable argsort = the old sorted() over every row, ties in dict order; a set for the seed check
            # (the all() scan was ~1M generator steps per recall). Same candidates, same order (30 Oct).
            _sl = list(idx_e)
            _rw = np.fromiter((int(r) for r in idx_e.values()), dtype=np.int64, count=len(_sl))
            _top = np.argsort(-cos[_rw], kind="stable")[:256]
            candidates = [(_sl[j], int(_rw[j])) for j in _top]
        _have = {s for s, _ in candidates}
        for s in seeds:
            if s in idx_e and s not in _have:
                candidates.append((s, idx_e[s]))
                _have.add(s)
        for s, i in candidates:
            if s in _passed or row_current(s):
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
        with np.errstate(all="ignore"):   # spurious Accelerate matmul warnings, see _semantic_scores
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
        entries = None
        if tok is not None and os.environ.get("BRAIN_EMB_SHARED_LISTING", "1") != "0":
            got = _memlist()
            if got is not None and os.path.abspath(sr.MEM) == os.path.abspath(MEM):
                entries = _memlist_md(got)
        emb = sr.embedding_artifact.load_verified_embeddings(sr.EMB, sr.EMB_AUTHORITY, sr.MEM, sr.EXPECTED_EMB_MODEL,
                                                             memory_entries=entries)
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
        box = {"query": query, "text": text, "v": None, "done": threading.Event(), "abandon": threading.Event(),
               "switch": sys.getswitchinterval()}

        def work():
            proc = _qemb_proc_send(text)
            try:
                _full_embeddings()
            except Exception:
                pass
            v, how = _qemb_proc_collect(proc, box) if proc is not None else (None, "none")
            box["proc"] = how
            if how in ("none", "failed"):
                try:
                    import query_embed
                    v = query_embed.embed(text)
                except Exception:
                    v = None
                if v is None:
                    _INTHREAD_ENC[:] = [False]
            box["v"] = v
            box["done"].set()

        sys.setswitchinterval(min(box["switch"], 0.001))
        th = threading.Thread(target=work, name="brain-query-embed", daemon=True)
        box["thread"] = th
        _QEMB["box"] = box      # the worker's own dict, by reference: a copy would never see its vector
        th.start()
    except Exception:
        _QEMB.clear()


_INTHREAD_ENC = []


def _inthread_encoder_ok():
    """Whether this interpreter can run the in-thread encoder (asked once per process; query_embed reaches the user
    site under python -s). Set false for good when an in-thread encode comes back empty, so the helper takes over."""
    if not _INTHREAD_ENC:
        try:
            import query_embed
            _INTHREAD_ENC.append(bool(query_embed.runtime_reachable()))
        except Exception:
            _INTHREAD_ENC.append(False)
    return _INTHREAD_ENC[0]


def _qemb_proc_send(text):
    """Start query_embed_proc.py with the query text on its stdin (no window; stdin, stdout and stderr all named) and
    return the process, or None. OFF BY DEFAULT since 2 Nov 2026 (claude-1e0a23c2): on a loaded box the helper's process
    start made recall wait a median 6-7 s for the vector; in-thread the recall body ran 6.4 s against 14.1 s median,
    7 of 9 pairs faster, same answers (brain_math_loop_1e0a/op/recall10x/ab_proc_out.txt). BRAIN_QEMB_PROC=1 brings it back.
    None also when: BRAIN_QEMB_PROC unset or 0, the helper file missing, the start or the write failing, or this
    process having encoded a query before. A process that runs many recalls pays the in-thread session build once and
    then encodes in about 20 ms a query, cheaper than a helper's ~1.5 s each, so only its first query goes out.

    Unless this process cannot encode at all (30 Oct 2026): onnxruntime lives in the user site-packages on the Mac, so
    a process started with python -s (the hooks, most harnesses) has no in-thread encoder, query_embed.embed returns
    None, and every recall after the first ran with no query lane. 84 bank questions in one -s process: hit@5 74 -> 69,
    15 worse and 1 better, against one fresh process per question. Such a process keeps sending each query out."""
    first = not _QEMB_PROC
    _QEMB_PROC.append(1)
    _qp = os.environ.get("BRAIN_QEMB_PROC", "").strip().lower()
    if _qp in ("0", "off", "false", "no"):
        return None
    # Unset: encode in-thread when this interpreter can (laptop, 2 Nov: a helper start cost a median 6-7 s on a
    # loaded box); the helper only when it cannot (the Mac under python -s, 30 Oct). BRAIN_QEMB_PROC=1: the
    # first query of a process goes out, as before.
    if (not first or _qp == "") and _inthread_encoder_ok():
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
        # 30 Oct 2026 (Mac): POSIX communicate() flushes a non-None stdin first, and a closed one raises ValueError,
        # which _qemb_proc_collect counted as "failed"; the in-thread fallback then ran under -s without onnxruntime,
        # so every recall on the Mac ran with no query vector. Windows' communicate() never flushes.
        proc.stdin = None
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


def _qemb_proc_collect(proc, box=None):
    """(vector, "used") from the helper; (None, "used") when it answered that embed gave None; (None, "failed") when it
    exited badly or wrote anything but b"N" or b"V" + float32 bytes (the caller then encodes in-thread); (None,
    "timeout") when it is still silent after BRAIN_RECALL_QSEM_WAIT seconds, recall's own wait for the vector - it is
    killed, and recall goes on without a vector as it did when the in-thread encoder ran past that wait.

    With the worker's box (3 Nov 2026) that wait is counted from the moment recall asks for the vector at fusion,
    not from the moment the worker starts collecting: the helper is killed when _qemb_get gives up (box["abandon"])
    or after BRAIN_RECALL_QSEM_CAP seconds (120). Under swap a fresh recall's helper passed its own 20 s while the
    lexical work still had 20 s to go, and recall printed a ranking with no meaning lane and no word of it (1 of 4
    calls, 3 Nov). BRAIN_RECALL_QSEM_LATE=0 restores the fixed wait."""
    import subprocess
    late = box is not None and box.get("abandon") is not None and \
        os.environ.get("BRAIN_RECALL_QSEM_LATE", "1").strip().lower() not in ("0", "off", "false", "no")
    try:
        if late:
            end = time.monotonic() + float(os.environ.get("BRAIN_RECALL_QSEM_CAP", "120"))
            while True:
                try:
                    out, _ = proc.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    if box["abandon"].is_set() or time.monotonic() >= end:
                        _qemb_proc_kill(proc)
                        return None, "timeout"
        else:
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
        if not box["done"].wait(float(os.environ.get("BRAIN_RECALL_QSEM_WAIT", "20"))) and box.get("abandon"):
            box["abandon"].set()
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
    if box.get("abandon") is not None:
        box["abandon"].set()
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
    was needed (grid_1v1, caught 2026-07-09). Live lines win on duplicates. Within the archive the FIRST line for a
    slug wins: compact_index prepends, so the first is the newest (1 Nov: keeping the last served the oldest
    description for 177 of 294 slugs that had more than one archive line)."""
    out = {}
    try:
        with io.open(os.path.join(MEM, "INDEX_ARCHIVE.md"), encoding="utf-8", errors="replace") as f:
            for ln in f:
                # same as re.match(r"- \[([^\]]+?)\.md\]", ln): the name holds no "]", so the first "]" must close
                # ".md]". 57 -> 28 ms a recall on 28k lines (30 Oct), identical dict and order.
                if ln.startswith("- ["):
                    k = ln.find("]", 3)
                    if k >= 7 and ln[k - 3:k] == ".md":
                        out.setdefault(ln[3:k - 3], ln.strip())
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


_MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                             "dec"))}


def _session_clock(query, known_slugs, idx, k=3):
    """Up to k compaction slugs of the session the query names, nearest the clock time it names; [] when it names no
    session with compactions or no time. A date ("30 Oct", "2026-10-30") narrows the day; without one each node is
    measured against the time on its own day, the newer day winning a tie."""
    q = query.lower()
    tm = re.search(r"(?<![\d:.])([01]?\d|2[0-3])[:.h]([0-5]\d)(?![\d])", q)
    if not tm:
        return []
    want = int(tm.group(1)) * 60 + int(tm.group(2))
    day = None
    dm = re.search(r"\b(20\d\d)-(\d\d)-(\d\d)\b", q)
    if dm:
        day = (int(dm.group(2)), int(dm.group(3)))
    else:
        dm = re.search(r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", q) or \
            re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})\b", q)
        if dm:
            a, b = dm.group(1), dm.group(2)
            day = (_MONTHS[b[:3]], int(a)) if a.isdigit() else (_MONTHS[a[:3]], int(b))
    pool = known_slugs if known_slugs is not None else _corpus_slugs()
    for sid in re.findall(r"\b([0-9a-f]{8})\b", q):
        rx = re.compile(r"^compaction_[a-z]+_%s_(\d{4})(\d\d)(\d\d)T(\d\d)(\d\d)" % sid)
        nodes = []
        for s in pool:
            m = rx.match(s)
            if m:
                y, mo, d, h, mi = (int(x) for x in m.groups())
                if day and (mo, d) != day:
                    continue
                gap = h * 60 + mi - want
                nodes.append(((gap if gap >= 0 else -2 * gap), -(y * 10000 + mo * 100 + d), s))
        if nodes:
            return [s for _g, _d, s in sorted(nodes)[:k]]
    return []


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
    # 3 Nov 2026 (RECALL-10X lever 14): runs once per ranked candidate, 23-32k times a recall. The pattern is
    # compiled once and the answer memoised per line text (the answer depends on the line alone, so a changed
    # line is a new key); 0.047-0.094 -> 0.016 s CPU per recall warm, same status on 104,716 of 104,716 calls.
    v = _STATUS_MEMO.get(line)
    if v is None:
        m = _STATUS_RX.search(line)
        v = m.group(1).lower() if m else "legacy"
        if len(_STATUS_MEMO) > 400000:
            _STATUS_MEMO.clear()
        _STATUS_MEMO[line] = v
    return v


_STATUS_RX = re.compile(r"\)\s*-\s*\[(PROVISIONAL|VERIFIED|OWNER-STATED|RETRACTED)\]", re.I)
_STATUS_MEMO = {}


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

    Called AFTER remember releases the memory write lock: compact_index.py takes that lock itself
    (31 Oct), so a call from inside it waited 20 s on its own parent and printed SKIPPED, and the
    index grew to 35 KB between dreams. compact_index.py moves the oldest lines into INDEX_ARCHIVE.md; nothing is
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


_SLUG_DATE = re.compile(r"_20\d\d_(\d\d)_\d\d")
_MONTH_FULL = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                           "september", "october", "november", "december"), 1)}
_MONTH_ABBR = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10,
               "nov": 11, "dec": 12}


def _query_months(q):
    """Months a question names, for the month prior: full names or 3-letter forms; 'may' only as 'May' or after
    in/early/late/mid (PLAN.md month_prior_2026_11_03)."""
    out = set()
    for m, i in _MONTH_FULL.items():
        if m == "may":
            if re.search(r"\bMay\b", q) or re.search(r"\b(in|early|late|mid)[\s-]+may\b", q, re.I):
                out.add(5)
        elif re.search(r"\b%s\b" % m, q, re.I):
            out.add(i)
    for a, i in _MONTH_ABBR.items():
        if re.search(r"\b%s\b" % a, q, re.I):
            out.add(i)
    return out


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
@contextlib.contextmanager
def _gc_paused():
    """The cyclic collector off for one recall, back on after if it was on. It spent 29-45 ms of a 0.55-0.65 s cold
    recall (8 questions, gc.callbacks, load 8, 2 Nov 2026) walking objects a recall frees by refcount anyway; the
    prompt hook pauses it the same way. BRAIN_RECALL_GC=1 leaves it running."""
    import gc
    if os.environ.get("BRAIN_RECALL_GC") == "1" or not gc.isenabled():
        yield
        return
    gc.disable()
    try:
        yield
    finally:
        gc.enable()


def recall(query, n=8):
    """Ranked recall for a question (prints). The R02 speed layer (_fc_layer) is installed for the call only. The R11
    query encoder starts on its worker thread here and is always joined before return, early returns included; the
    verified sidecar is shared by the two meaning lanes for this call only (_full_emb_scope)."""
    _full_emb_scope(True)
    _qemb_start(query)
    try:
        with _gc_paused(), _fc_layer():
            return _recall_body(query, n)
    finally:
        _qemb_finish()
        _full_emb_scope(False)


_OR_LINE = re.compile(r"^(\s+)([0-9.]+)( \[[^\]]*\] )(\S+)")


@contextlib.contextmanager
def _or_tables_once():
    """Inside one recall_or, read the query-independent tables once instead of once per wording: the archived index
    lines, consciousness tiers, corpus slug list and salience. Each wording still gets its own copy (_recall_body
    updates the index dict in place). On 1 Nov, timed per wording inside a 3-wording call, these cost 0.037, 0.023,
    0.010 and 0.026 s on each wording after the first. The fresh-memory scan is left alone: it sets the refresh
    signature that the same wording then acts on."""
    g = globals()
    saved = [(g, "_archived_index_lines", dict), (g, "_tiers", dict), (g, "_corpus_slugs", list),
             (vars(sr), "_load_salience", dict)]
    saved = [(ns, name, ns[name], cp) for ns, name, cp in saved]

    def once(f, cp):
        box = []

        def wrapped():
            if not box:
                box.append(f())
            return cp(box[0])
        return wrapped
    for ns, name, f, cp in saved:
        ns[name] = once(f, cp)
    try:
        yield
    finally:
        for ns, name, f, cp in saved:
            ns[name] = f


def recall_or(queries, n=8, k=60):
    """Recall for several wordings of one question, fused by reciprocal rank (k=60) into one list (prints). Measured
    29 Oct on a 120-question bank, top 5: fused 112/120 against 96 and 106 for each wording alone and 111 for reading
    both lists (two_wordings_fused_by_rank_beat_one_recall_query_112_vs_96_of_120_2026_10_29)."""
    import io
    fused, blocks, foot, outs = {}, {}, [], []
    # One call scope for all wordings: the sidecar, listing and supersession map load once, not per wording
    # (a second wording cost a full warm recall, ~1 CPU-s, before this).
    _full_emb_scope(True)
    try:
        with _gc_paused(), _fc_layer(), _or_tables_once():
            for q in queries:
                buf = io.StringIO()
                _qemb_start(q)
                try:
                    with contextlib.redirect_stdout(buf):
                        _recall_body(q, max(n, 10))
                finally:
                    _qemb_finish()
                outs.append(buf)
    finally:
        _full_emb_scope(False)
    for buf in outs:
        rank, cur, foot = 0, None, []
        for ln in buf.getvalue().splitlines()[1:]:
            m = _OR_LINE.match(ln)
            if m:
                rank += 1
                cur = m.group(4)
                fused[cur] = fused.get(cur, 0.0) + 1.0 / (k + rank)
                if cur in blocks:
                    cur = None
                else:
                    blocks[cur] = [ln]
            elif ln.startswith("        ") and cur:
                blocks[cur].append(ln)
            elif not ln.startswith("        "):
                cur = None
                if rank:
                    foot.append(ln)
    print("recall --or: %s  (%d wordings, fused by rank, k=%d)" % (" | ".join(repr(q) for q in queries), len(queries), k))
    order = sorted(fused, key=lambda s: -fused[s])[:n]
    top = fused[order[0]] if order else 1.0
    for s in order:
        m = _OR_LINE.match(blocks[s][0])
        print("%s%.2f%s%s" % (m.group(1), fused[s] / top, m.group(3), s))
        for ln in blocks[s][1:]:
            print(ln)
    for ln in foot:
        print(ln)
    return 0


def _recall_body(query, n=8):
    # q61c (4 Nov 2026, claude-68ceaf54): ACRONYM JOIN FROM A FROZEN TABLE. A question that spells out a term
    # ("in the money") joins the acronym ("itm") only for rows where the notes use the acronym at least as often as
    # the phrase (q61d_scan document counts; pnl/tts/fps/wer/oom dropped). BRAIN_RECALL_ACRONYMS=0 turns it off.
    if os.environ.get("BRAIN_RECALL_ACRONYMS", "1") != "0" and query:
        try:
            _rows = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "recall_acronyms.json"), encoding="utf-8"))
            _add = []
            for _p, _a in _rows:
                if re.search(r"\b" + r"[\s_-]+".join(map(re.escape, _p.split())) + r"\b", query, re.I) \
                        and not re.search(r"\b%s\b" % re.escape(_a), query, re.I) and _a not in _add:
                    _add.append(_a)
            if _add:
                if os.environ.get("Q60_LOG"):
                    open(os.environ["Q60_LOG"], "a", encoding="utf-8").write("%s\t%s\n" % (" ".join(_add), query))
                query = query + " " + " ".join(_add)
        except Exception as _acr_exc:
            print("  [warn] acronym join skipped (%s: %s)" % (type(_acr_exc).__name__, _acr_exc))
    idx = _archived_index_lines()
    idx.update(sr._index_lines())     # live entries override archived twins
    words = sr._words(query)
    seeds, lex = sr._seeds_from_prompt(words, idx)
    # TASK-ID SEEDS (2 Nov 2026, claude-5693aca3). sr._words keeps letter-led words only, so a query that is a bare
    # checklist id ('T-20261101-017') gave no seeds and recall stopped at "no lexical foothold" before the bm25 block,
    # although bm25.tokens keeps the id as t_20261101_017. When the query holds an id, BM25's hits on the raw query
    # join the seeds at the same strength the seeder's own BM25 arm uses. Measured on 10 checklist ids
    # (brain_everything_node_5693/eval_taskid_recall.py): bare id 2/10 -> 10/10 with a memory naming the id in the top 8;
    # 'status of task <id>' 10/10 -> 10/10. BRAIN_RECALL_TASKID_SEEDS=0 turns it off.
    if os.environ.get("BRAIN_RECALL_TASKID_SEEDS", "1") != "0" and re.search(r"\bT-20\d{6}-\d{3}\b", query or "", re.I):
        try:
            import bm25 as _bm25tid
            _tid_hits = _bm25tid.BM25().score(query, 5)
            if _tid_hits:
                _tid_max = _tid_hits[0][1] or 1.0
                for _slug, _s in _tid_hits:
                    seeds[_slug] = max(seeds.get(_slug, 0.0), 1.5 + 1.5 * _s / _tid_max)
                    lex = set(lex) | {_slug}
        except Exception as _tid_exc:
            print("  [warn] task-id seeds skipped (%s: %s)" % (type(_tid_exc).__name__, _tid_exc))
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

    # BARE-IDENTIFIER SEEDS (6 Nov 2026). The task-id seeds above close this gap for checklist ids only. A query that is
    # just a serial, a short hash, a run id or a block height ('7KDP-2210', 'e1f2a3b', '604417') leaves sr._words with
    # fewer than two letter-led words, every arm of the seeder comes back empty, and recall stopped at "no lexical
    # foothold" although bm25.tokens keeps those digits. Here, after every other arm has had its turn, a query that still
    # has no seed and carries a digit takes BM25's hits on the raw query, at the task-id seeds' strength. A query that
    # seeded before is untouched, so its output is unchanged. BRAIN_RECALL_ID_SEEDS=0 turns it off.
    if not seeds and os.environ.get("BRAIN_RECALL_ID_SEEDS", "1") != "0" and re.search(r"\d", query or ""):
        try:
            import bm25 as _bm25id
            _id_hits = [h for h in _bm25id.BM25().score(query, 5) if h[0] not in disabled]
            if _id_hits:
                _id_max = _id_hits[0][1] or 1.0
                for _slug, _s in _id_hits:
                    seeds[_slug] = 1.5 + 1.5 * _s / _id_max
                    lex = set(lex) | {_slug}
        except Exception as _id_exc:
            print("  [warn] identifier seeds skipped (%s: %s)" % (type(_id_exc).__name__, _id_exc))

    adj, deg = sr._load_graph()
    act = {}
    if seeds and adj:
        act, _parent = sr._spread(seeds, adj, deg)
    # _semantic_scores waits for the sidecar the query worker is still loading (0.07-0.27 s on the Mac, 30 Oct), so it
    # runs after the salience, tier and supersession reads below, which do not need it. BRAIN_RECALL_SEM_LATE=0 restores
    # the old order.
    sem_late = os.environ.get("BRAIN_RECALL_SEM_LATE", "1") != "0"
    sem = None if sem_late else _semantic_scores(seeds)
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
    if sem is None:
        sem = _semantic_scores(seeds)
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
    # The factor. 0.85 was a first pass on 25 questions (3 Sep); 0.95 since 1 Nov 12:2x: on the recall wave's bank the
    # 0.85 cost job-gold questions far more than it saved others, and 0.95, picked by a sweep, passed a pre-registered
    # held-out test (100 job + 300 other questions): bank-weighted or3 hit@1 +0.79pp, hit@5 +0.50pp, owner-voice
    # hit@1 +0.01pp, hit@5 +0.28pp; controls -2 of 300 at or3 hit@1. Memory job_demotion_net_near_zero_job_gold_pays_2026_11_01,
    # codex_fleet/recall_mass/demote_ab.py. BRAIN_RECALL_NOTE_DEMOTE_FACTOR overrides it.
    try:
        _note_demote_f = float(os.environ.get("BRAIN_RECALL_NOTE_DEMOTE_FACTOR", "0.95"))
    except ValueError:
        _note_demote_f = 0.95
    _name_cov = os.environ.get("BRAIN_RECALL_NAME_COVERAGE", "1") != "0"   # see the bm25 block below
    _qcover_on = os.environ.get("BRAIN_RECALL_QCOVER", "1") != "0"        # see the bm25 block below
    _qc_digit_prefix = os.environ.get("BRAIN_RECALL_QCOVER_DIGIT_PREFIX", "0") == "1"
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
                            # the pair just scanned, while the index is the generation it was scanned against
                            # (29 Oct 2026, RECALL-10X lead 6: refresh_in_memory scanned the folder again, 2.2 s)
                            _found = None
                            try:
                                if _FC_FRESH_SIG is not None and _bm25mod._index_signature(_bm25mod.INDEX) == _FC_FRESH_SIG:
                                    _found = (_fresh_mem, _gone_mem)
                            except Exception:
                                _found = None
                            try:
                                _mem_payload, _added, _gone = _bm25mod.refresh_in_memory(out=_bm25mod.INDEX, found=_found)
                            except TypeError:
                                _mem_payload, _added, _gone = _bm25mod.refresh_in_memory(out=_bm25mod.INDEX)
                        # 1 Nov 2026 (claude-68ceaf54, RECALL-10X): a recall spawns the publish only when the index is
                        # at least BRAIN_RECALL_SPAWN_AGE s old (600). remember() kicks its own refresh and the 15-min
                        # task publishes, so a young index needs no extra 13-17 s CPU refresh per recall (26 ran in
                        # one hour on 1 Nov); this recall is scored on the merged payload either way.
                        _spawn_age = float(os.environ.get("BRAIN_RECALL_SPAWN_AGE", "600") or 0)
                        if (_mem_payload is not None or _ovl_bm is not None) and _idx_age < _spawn_age:
                            print("  [bm25] index was %.0f min old: merged %d fresh memor%s (dropped %d) in memory;"
                                  " publish left to remember() and the 15-min task"
                                  % (_idx_age / 60, _added, "y" if _added == 1 else "ies", _gone))
                        elif _mem_payload is not None or _ovl_bm is not None:
                            os.environ["BM25_REFRESH_ORIGIN"] = "recall"
                            _pid = _bm25mod.spawn_detached_refresh()
                            # a notes folder that is not the live one never gets the live index published from it
                            # (5 Oct scar), so say so instead of promising a publish that will not come
                            if _pid:
                                _how = " (pid %d)" % _pid
                            elif _bm25mod.publishes_live_from_elsewhere():
                                _how = (" - refused, BRAIN_MEM_DIR is not the live folder; set BRAIN_BM25_INDEX to a"
                                        " shadow index to keep one for it")
                            else:
                                _how = " (spawn failed; the 15-min task will publish)"
                            print("  [bm25] index was %.0f min old: merged %d fresh memor%s (dropped %d) in memory;"
                                  " publish handed to a detached refresh%s"
                                  % (_idx_age / 60, _added, "y" if _added == 1 else "ies", _gone, _how))
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
            # SPACED NUMBERS (2026-10-29, W06). Slugs join a short word and its number (acct5, box3, b04) but questions
            # space them ("acct 5", "box 3"), so the specific memories lost to generic ones (memory
            # recall_misses_spaced_numbers_box_3_vs_box3_2026_10_29). Add the joined token when the index knows it;
            # an unknown join adds nothing. BRAIN_RECALL_JOIN_NUMBERS=0 turns it off.
            if os.environ.get("BRAIN_RECALL_JOIN_NUMBERS", "1") != "0":
                try:
                    _joins = []
                    # 30 Oct: a long word joins by its 2-4 letter prefix ("episode 15" -> ep15, "generation 9" ->
                    # gen9), still only when the index knows the token. Shadow A/B on the 84 bank questions whose
                    # query changes: hit@1 52 -> 60 (+9/-1), MRR .695 -> .780 (~/codex_fleet/recall_join).
                    for _w, _d in re.findall(r"\b([A-Za-z]{1,})\s+(\d{1,3})\b", query):
                        _cands = [_w] if len(_w) <= 6 else [_w[:2], _w[:3], _w[:4]]
                        for _c in _cands:
                            _j = (_c + _d).lower()
                            if _bm.df.get(_j, 0) > 0 and _j not in _joins:
                                _joins.append(_j)
                    if _joins:
                        _q = _q + " " + " ".join(_joins)
                except Exception:
                    pass
            # KEEP THE WORD THE NORMALISER CHANGED (2026-11-02 laptop, RECALL-10X probe 22, claude-68ceaf54; on the Mac 4 Nov
            # 2026): the snap replaces a rare word with a common neighbour, but a memory that quotes him holds his typo and many
            # rare words are real. Each changed word is scored again and added (BM25 sums over terms, so this is exact).
            # Laptop, 496 stored questions with a changed word: top-1 280 -> 411, top-8 384 -> 487.
            # BRAIN_RECALL_KEEPTYPO=0 off; BRAIN_RECALL_KEEPTYPO_W weight (1.0).
            _keep = []
            if _q is not query and os.environ.get("BRAIN_RECALL_KEEPTYPO", "1") != "0":
                try:
                    import bm25 as _bm25kt
                    _qnk = QueryNormalizer(_bm.df)
                    for _t in _bm25kt.tokens(query):
                        if _t not in _keep and _qnk._correct(_t) != _t:
                            _keep.append(_t)
                except Exception:
                    _keep = []
            # QUERY SYNONYMS (2026-10-31, RECALL-10X, claude-68ceaf54): a memory names a failure by its cause ("stale",
            # "shows nothing"), a question by what was seen ("too old", "nothing in it"). brain_syn appends the memory's word;
            # it matches on the RAW query because the normaliser above drops short words ('too old' -> 'old'). Live recall,
            # 12 questions incl. 7 fresh: top-8 7 -> 10, no loss (brain_improvement/recall_10x_2026_10_29/content_gap_68ce/
            # RESULT_synonym_31oct.md). Fail-open. BRAIN_RECALL_SYN=0 turns it off.
            try:
                import brain_syn as _bsyn
                _q = _bsyn.expand(_q, query)
            except Exception:
                pass
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
            # BRITISH/US SPELLING (2026-11-01, RECALL-10X probe 15, claude-68ceaf54): the memories split common words
            # (neighbours 390 files / neighbors 64, catalogue 427 / catalog 1050) and the normaliser only snaps a word seen
            # in at most 8 memories, so "no neighbors" never reached a memory that says neighbours (BM25 rank 28). The other
            # spelling is scored at weight 0.3 and added: BM25 sums over terms, so s(q) + 0.3 s(other) is the weighted query
            # exactly. Appending it at full weight lost on 750 stored questions (top-1 434 -> 369); at 0.3 top-1 429 -> 437,
            # top-8 565 -> 567, dev 3 better 0 worse (content_gap_68ce/RESULT_probe15_1nov.md). BRAIN_RECALL_SPELL=0 off.
            if os.environ.get("BRAIN_RECALL_SPELL", "1") != "0":
                try:
                    import bm25 as _bm25sp
                    import brain_syn as _bsyn2
                    _have = set(_bm25sp.tokens(_q))
                    _sw = [w for w in _bsyn2.spelling_fold(query) if w not in _have]
                    if _sw:
                        _k = int(os.environ.get("BRAIN_LEX_TOPK", "60") or 60)
                        _wide = max(_k, 400)
                        _sw_w = float(os.environ.get("BRAIN_RECALL_SPELL_W", "0.3") or 0.3)
                        _acc = {_s1: _v1 for _s1, _v1 in _bm.score(_q, _wide)}
                        for _s1, _v1 in _bm.score(" ".join(_sw), _wide):
                            _acc[_s1] = _acc.get(_s1, 0.0) + _sw_w * _v1
                        bm_hits = sorted(_acc.items(), key=lambda kv: -kv[1])[:_k]
                except Exception:
                    pass
            if _keep:
                try:
                    _k = int(os.environ.get("BRAIN_LEX_TOPK", "60") or 60)
                    _wide = max(_k, 400)
                    _kt_w = float(os.environ.get("BRAIN_RECALL_KEEPTYPO_W", "1.0") or 1.0)
                    _acc = dict(bm_hits)
                    for _s1, _v1 in _bm.score(_q, _wide):
                        _acc.setdefault(_s1, _v1)
                    for _s1, _v1 in _bm.score(" ".join(_keep), _wide):
                        _acc[_s1] = _acc.get(_s1, 0.0) + _kt_w * _v1
                    bm_hits = sorted(_acc.items(), key=lambda kv: -kv[1])[:_k]
                except Exception:
                    pass
            if os.environ.get("BRAIN_RECALL_CTAIL", "1") != "0":
                try:     # the compaction tail lane (_ctail_merge above)
                    bm_hits = _ctail_merge(bm_hits, _q, _bm, _bm25mod,
                                           int(os.environ.get("BRAIN_LEX_TOPK", "60") or 60))
                except Exception:
                    pass
            # COMPACTION-STACK LIFT (2026-11-02, RECALL-10X probe 24b, claude-68ceaf54; ON by default, BRAIN_RECALL_LIFT=0 turns it off).
            # A compaction summary repeats his order word for word, so on his own quotes a stack of compaction anchors
            # can bury the memory that records the order. Damping the anchors as a class (probe 23) or by place in the
            # stack (probe 24) loses the sessions. This moves ONE row: if the best non-compaction row sits below rank R
            # and scores at least F of the top score, it is given a score between ranks R-1 and R. On BM25 alone
            # (R 8, F 0.7): his quotes top-8 173 -> 176, compaction golds 482 -> 480, memory golds 852 -> 852
            # Full recall path, his 187 quotes: top-1 97 -> 98, top-8 168 -> 170, worse 0 (content_gap_68ce/RESULT_probe24b_2nov.md).
            if os.environ.get("BRAIN_RECALL_LIFT", "1") == "1" and len(bm_hits) > 1:
                try:
                    _lr = max(2, int(os.environ.get("BRAIN_RECALL_LIFT_R", "8") or 8))
                    _lf = float(os.environ.get("BRAIN_RECALL_LIFT_F", "0.7") or 0.7)
                    _crx = re.compile(r"^compaction_(claude|codex)_[0-9a-z]+_\d{8}T\d{6}$")
                    _lj = next((i for i, (_s1, _v1) in enumerate(bm_hits) if not _crx.match(_s1)), None)
                    if _lj is not None and _lj > _lr - 1 and bm_hits[_lj][1] >= _lf * bm_hits[0][1]:
                        _lv = 0.5 * (bm_hits[_lr - 2][1] + bm_hits[_lr - 1][1])
                        _row = (bm_hits[_lj][0], _lv)
                        bm_hits = bm_hits[:_lr - 1] + [_row] + bm_hits[_lr - 1:_lj] + bm_hits[_lj + 1:]
                except Exception:
                    pass
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
        # PERMISSION RESCUE, part 1 (2026-11-03, RECALL-10X q19, claude-68ceaf54): on a permission question ("can I ...",
        # "is it ok to ..."), score the owner family alone (owner_* slugs, OWNER-tagged and owner-stated memories) by BM25
        # on the question minus its permission words; part 2 shows the best of them at rank 3. BRAIN_RECALL_PERMIT=0 off.
        _permit_picks = []
        if os.environ.get("BRAIN_RECALL_PERMIT", "1") != "0" and (_PERMIT_RX.search(query) or _permit_lead(query) or (
                os.environ.get("BRAIN_RECALL_PERMIT_SHAPE", "1") != "0" and _PERMIT_RX2.search(query)) or (
                os.environ.get("BRAIN_RECALL_PERMIT_ELLIPSIS", "1") != "0" and _PERMIT_RX3.search(query))):
            try:
                _pk = int(os.environ.get("BRAIN_RECALL_PERMIT_K", "1") or 1)
                _core = " ".join(_PERMIT_STRIP.sub(" ", query).split())
                # q21 (3 Nov): a law names the act in its own verbs (stops, kills, suspends, deleted); the agent asks with
                # another (pause, truncate). Add the law-side verbs for the few action words below. BRAIN_RECALL_PERMIT_EXPAND=0 off.
                if os.environ.get("BRAIN_RECALL_PERMIT_EXPAND", "1") != "0":
                    _px = [_PERMIT_VERBS[w.lower()] for w in _core.split() if w.lower() in _PERMIT_VERBS]
                    if _px:
                        _core = _core + " " + " ".join(_px)
                # q54 (4 Nov): a law names a script, gate or folder joined (token_ledger, stage_gate); the agent says
                # it with a space. Add the joined form of adjacent core words when the index holds it.
                # BRAIN_RECALL_PERMIT_JOIN=0 off.
                if os.environ.get("BRAIN_RECALL_PERMIT_JOIN", "1") != "0":
                    try:
                        _vdf = getattr(_bm, "df", None) or {}
                        _cw = [w.lower() for w in re.findall(r"[A-Za-z]+", _core)]
                        _jn = [a + "_" + b for a, b in zip(_cw, _cw[1:]) if (a + "_" + b) in _vdf]
                        if _jn:
                            _core = _core + " " + " ".join(dict.fromkeys(_jn))
                    except Exception:
                        pass
                # q48 (3 Nov): a question that hands work to ANOTHER agent (another / worker / task / two agents;
                # delegate, offload, spawn) asks the no-subagents order in words it does not use. Add its words only
                # then - a bare 'agent' (the mesh, the desk, codex agents) is not delegation. BRAIN_RECALL_PERMIT_DELEG=0 off.
                if os.environ.get("BRAIN_RECALL_PERMIT_DELEG", "1") != "0" and _DELEG_RX.search(query):
                    _core = _core + " subagent helper"
                # q22 (3 Nov): an overtaken rule is not an answer to 'can I'; drop superseded memories from the family
                # (87 of 605 on 3 Nov). BRAIN_RECALL_PERMIT_SUPX=0 off.
                _fam_x = set()
                if os.environ.get("BRAIN_RECALL_PERMIT_SUPX", "1") != "0":
                    try:
                        _fam_x = set(superseded or {})
                    except Exception:
                        _fam_x = set()
                _fam = _owner_family_texts(_fam_x)
                _pf = float(os.environ.get("BRAIN_RECALL_PERMIT_FLOOR", "12.5") or 12.5)
                _fh = [(s, _v) for s, _v in _bm.score(_core, 5000) if s in _fam and _v >= _pf]
                # q34 (3 Nov): his later words win. Among family rows scoring within BRAIN_RECALL_PERMIT_NEWEST (0.75) of
                # the best, show the newest by the slug's date (30 Oct order over 14 Oct and 7 Sep ones). =0 off.
                _nr = float(os.environ.get("BRAIN_RECALL_PERMIT_NEWEST", "0.81") or 0)
                if _fh and _nr > 0:
                    _top = _fh[0][1]
                    # q42: the floor gates the top; rows within the band of a top that passed it may be under it.
                    _near = [(s, _v) for s, _v in _bm.score(_core, 5000) if s in _fam and _v >= _nr * _top]
                    # q36: only his orders jump the queue; a newer method note never displaces an older law.
                    if os.environ.get("BRAIN_RECALL_PERMIT_NEWEST_OWNER", "1") != "0":
                        # q40: an owner_ slug alone is not an order (owner_orders_are_missed_..._10_31 is a recall
                        # note); only rows stated as his order by tag or by an order-kind slug jump on date.
                        _near = [t for t in _near if t[0] in _OWNER_STATED or _OWNER_ORDER_SLUG_RX.match(t[0])
                                 or t == _fh[0]]
                    # q42: an order scoped to the Mac is his word for the Mac, not for this laptop: on Windows, unless
                    # the question names the Mac, Mac-only rows leave the near band (the top row too) when another stays.
                    if (sys.platform == "win32" and os.environ.get("BRAIN_RECALL_PERMIT_SCOPE", "1") != "0"
                            and not re.search(r"\bmac\b", query, re.I)):
                        _nm = [t for t in _near if not (_MAC_SCOPE_RX.search(_fam.get(t[0], ""))
                               and not _ALL_SCOPE_RX.search(_fam.get(t[0], "")))]
                        if _nm:
                            _near = _nm
                    def _sd(s):
                        m = re.search(r"(20\d\d)_(\d\d)_(\d\d)$", s)
                        return m.group(0) if m else ""
                    _near.sort(key=lambda t: (_sd(t[0]), t[1]), reverse=True)
                    _fh = _near + [t for t in _fh if t not in _near]
                _permit_picks = [s for s, _v in _fh][:_pk]
                # q43: a second row - the newest of his orders within BRAIN_RECALL_PERMIT_SECOND of the top.
                _sb = float(os.environ.get("BRAIN_RECALL_PERMIT_SECOND", "0.75") or 0)
                if _permit_picks and _sb > 0 and _fh:
                    _all2 = [(s, _v) for s, _v in _bm.score(_core, 5000) if s in _fam
                             and (s in _OWNER_STATED or _OWNER_ORDER_SLUG_RX.match(s))]
                    # q44: when the question names an agent (codex, gpt-5, claude ...), the band is read among
                    # his orders that name it, so a stronger order about something else does not set the bar.
                    _anm = {w.lower() for w in re.findall(r"\b(codex|claude|gpt[-\w.]*|gemini|sonnet|opus|fable)\b", query, re.I)}
                    if _anm:
                        _nrx = re.compile(r"\b(" + "|".join(re.escape(w) for w in _anm) + r")\b", re.I)
                        _sub = [t for t in _all2 if _nrx.search(_fam.get(t[0], ""))]
                        if _sub:
                            _all2 = _sub
                    _t2 = max([_v for _s, _v in _all2] or [0])
                    _o2 = [(s, _v) for s, _v in _all2 if _v >= _sb * _t2 and s not in _permit_picks]
                    if (sys.platform == "win32" and os.environ.get("BRAIN_RECALL_PERMIT_SCOPE", "1") != "0"
                            and not re.search(r"\bmac\b", query, re.I)):
                        _o2 = [t for t in _o2 if not (_MAC_SCOPE_RX.search(_fam.get(t[0], ""))
                               and not _ALL_SCOPE_RX.search(_fam.get(t[0], "")))]
                    if _o2:
                        _d2 = lambda s: (re.search(r"(20\d\d)_(\d\d)_(\d\d)$", s) or [""])[0]
                        _o2.sort(key=lambda t: (_d2(t[0]), t[1]), reverse=True)
                        _permit_picks.append(_o2[0][0])
            except Exception:
                _permit_picks = []
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
                bsc = bsc * _note_demote_f
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
                    # A five-character prefix is a word stem, not a number (2 Nov 2026, claude-5693aca3): on a bare task id
                    # the query token 20261028 matched every compaction name's 20261030T180042 on "20261", so each
                    # compaction got x1.33 and the task's own node (name digits dropped) fell from bm25 rank 1 to last.
                    # BRAIN_RECALL_QCOVER_DIGIT_PREFIX=1 restores the old match.
                    for _t in _qtoks:
                        if _t in _st or (len(_t) >= 5 and (_qc_digit_prefix or not _t[:5].isdigit())
                                         and any(len(_u) >= 5 and _u[:5] == _t[:5] for _u in _st)):
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
                        # BRAIN_RECALL_QSEM_WFLAT="T:w" (off by default, under test 31 Oct): a flat bm25 head (top-1 over
                        # the 10th score below T) means bm25 is likely lost, so the meaning lane gets weight w. On the
                        # 3,626-row bank the plain-bm25 top1/top10 ratio told misses from rank 1-10 hits at AUC 0.84.
                        _top = sorted(_bl, reverse=True)
                        _QLANE["flat_ratio"] = _top[0] / max(_top[min(9, len(_top) - 1)], 1e-9)
                        _QLANE["head_n"] = len(_head)
                        _wf = os.environ.get("BRAIN_RECALL_QSEM_WFLAT", "")
                        if _wf:
                            try:
                                _wt, _ww = (float(x) for x in _wf.split(":"))
                                if _QLANE["flat_ratio"] < _wt:
                                    _qw = _ww
                            except ValueError:
                                pass
                        # BRAIN_RECALL_QSEM_NOMZ=mean scores a nomination bm25 never ranked at the body's mean (z 0)
                        # instead of its lowest; under test (30 Oct): owner-voice misses whose gold the meaning lane
                        # alone ranks first sink under word-matched noise at the floor
                        if os.environ.get("BRAIN_RECALL_QSEM_NOMZ", "floor") == "mean":
                            _bfloor = 0.0
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
        # Margin lift: when the query-meaning lane's best memory beats its second by at least the margin in cosine,
        # it goes first. Default 0.04 since 30 Oct 13:0x; BRAIN_RECALL_QSEM_LIFT=<margin> overrides, off/0 disables.
        # Live A/B on recall_mass, one warm process: on the 125 rows where it can fire, hit@1 112 -> 120 (+9 -1),
        # hit@5 121 -> 123 (+2 -0); on 60 rows below the margin, 0 changes. (The offline replay on the batch's
        # top-10s said +24 -2; those rows were scored by older code.)
        _lift = os.environ.get("BRAIN_RECALL_QSEM_LIFT", "0.04").strip().lower()
        if _lift in ("", "0", "off", "none", "false"):
            _lift = None
        if _lift and qsem and order:
            try:
                _qs = sorted(qsem.items(), key=lambda it: -it[1])
                if len(_qs) >= 2 and _qs[0][1] - _qs[1][1] >= float(_lift):
                    _top = _current_slug(_qs[0][0], superseded, known_slugs)
                    if (_top not in sr.SKIP and _top not in disabled and tier.get(_top) != "unconscious"
                            and (_top not in parent_only or _top in policy_seeds) and order[0][0] != _top):
                        _sc = dict(order).get(_top, order[0][1])
                        order = [(_top, max(_sc, order[0][1]))] + [(s, b) for s, b in order if s != _top]
                        ltail = [t for t in ltail if t[1] != _top]
                        gtail = [t for t in gtail if t[1] != _top]
                        stail = [t for t in stail if t[1] != _top]
                        demoted = [s for s in demoted if s != _top]
            except Exception:
                pass
        ranked = ([s for s, _ in order] + [s for _, s in ltail] +
                  [s for _, s in gtail] + [s for _, s in stail]
                  + demoted)
        # Compaction cap: compaction nodes are a fifth of the memory folder and filled 35 of the 55 top-5 slots on
        # the owner's 11 distinct asks of 29-30 Oct, above the lessons those asks needed. Past the first <cap> of
        # them, the rest go to the end of the list. Default 1 since 30 Oct; BRAIN_RECALL_COMPACT_CAP=<cap> overrides,
        # off disables; skipped when the query names a compaction or a session id. Bank A/B, 200 rows (3 with
        # compaction gold): no gold rank moved at cap 1 or 2. On his asks cap 1 lifted the lessons Q0/Q2 needed.
        _ccap = os.environ.get("BRAIN_RECALL_COMPACT_CAP", "1").strip().lower()
        if _ccap.isdigit() and not re.search(r"compact|\b[0-9a-f]{8}\b", query.lower()):
            _keep, _late, _seen = [], [], 0
            for s in ranked:
                if _ANCHOR.match(s):
                    _seen += 1
                    if _seen > int(_ccap):
                        _late.append(s)
                        continue
                _keep.append(s)
            ranked = _keep + _late
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

    # Month prior: a question that names a month ("in august when ...") halves the score of every dated memory
    # (_YYYY_MM_DD in the slug) from another month, then the list is sorted by score (stable). Undated memories keep
    # their score. Pre-registered (baabdcd6, PLAN.md in brain_improvement/month_prior_2026_11_03); F chosen on round 8
    # H4W (top-5 8 -> 12 of 138), scored once on held-out round 7 H4W: top-5 11 -> 20 of 350, +9 -0, exact McNemar
    # p=0.0039, guard rows (gold from another month) 0 lost of 5; a re-sort without the halving gave 6 and 12, so the
    # gain is the prior. BRAIN_RECALL_MONTH_PRIOR=<factor> (default 0.5); 1 turns it off.
    try:
        _mp_f = float(os.environ.get("BRAIN_RECALL_MONTH_PRIOR", "0.5"))
    except ValueError:
        _mp_f = 0.5
    if _mp_f != 1.0 and scored:
        try:
            _mp_months = _query_months(query)
            if _mp_months:
                _mp = []
                for r in scored:
                    _d = _SLUG_DATE.findall(r[4])
                    if _d and int(_d[-1]) not in _mp_months:
                        r = (r[0] * _mp_f,) + tuple(r[1:])
                    _mp.append(r)
                scored = sorted(_mp, key=lambda r: -r[0])
        except Exception:
            pass

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

    # Semantic rescue (claude-68ceaf54, 30 Oct 2026, RECALL-10X): the query-meaning lane's own top two memories
    # (current slugs) are shown at ranks 4 and 5 unless the fused order already has them at that rank or better.
    # The misses it targets are fusion losses: on heldout16 the lane alone had the gold at rank 1-5 for four
    # questions the fused top 8 lost. MEASURED end to end (semr_ab.py, 83 questions, one run, hit@1/2/3/8):
    #   owner56   off 38/43/48/49  on 38/43/48/50      heldout16 off 3/5/6/7  on 3/5/6/9
    #   now8      off 4/6/8/8      on 4/6/8/8          controls  3/3/3/3 both
    # No question lost a gold at any depth. Ranks 1-3 are never touched. Caveat: heldout16 helped choose the rule
    # (K=2 at rank 4 of 18 offline arms), so its +2 is in-sample. BRAIN_RECALL_SEM_RESCUE=0 turns it off; record in
    # brain_improvement/recall_10x_2026_10_29/content_gap_68ce/RESULT_sem_rescue_2026_10_30.md.
    if os.environ.get("BRAIN_RECALL_SEM_RESCUE", "1") != "0" and scored:
        try:
            _semr_dbg = os.environ.get("BRAIN_RECALL_SEM_RESCUE_DEBUG", "0") != "0"
            _disp = list(_display_rows(scored, n))
            if _semr_dbg:
                print("  [semr] before " + "|".join(r[4] for r in _disp))
            _sk = qsem if isinstance(qsem, dict) and qsem else (_query_semantic_scores(query) or {})
            _picks = []
            for _s in sorted(_sk, key=lambda s: -_sk[s]):
                _cur = _current_slug(_s, superseded, known_slugs)
                if _cur in sr.SKIP or _cur in disabled or tier.get(_cur) == "unconscious":
                    continue
                if _cur not in _picks:
                    _picks.append(_cur)
                if len(_picks) >= 2:
                    break
            _byslug = {r[4]: r for r in scored}
            for _j, _p in enumerate(_picks):
                _at = 3 + _j
                _slugs = [r[4] for r in _disp]
                if _p in _slugs[:_at + 1]:
                    continue
                _row = _byslug.get(_p) or (0.05, 0.0, 0.0, float(_sk.get(_p, 0.0)), _p, _status_from_index(_p, idx))
                _disp = [r for r in _disp if r[4] != _p]
                _sc = _disp[_at][0] if _at < len(_disp) else max(0.05, _row[0])
                _disp.insert(min(_at, len(_disp)), (max(_sc, 0.03),) + tuple(_row[1:]))
            _ds = {r[4] for r in _disp}
            scored = _disp + [r for r in scored if r[4] not in _ds]
            if _semr_dbg:
                print("  [semr] picks " + "|".join(_picks))
        except Exception as _semr_exc:
            print("  [warn] sem rescue skipped (%s: %s)" % (type(_semr_exc).__name__, _semr_exc))

    # Slot 5 for the meaning lane's best memory: when the query-meaning lane's top memory is not in the first five rows,
    # it takes row five (rows 1-4 never move). For casual asks the word lanes can bury a memory the meaning lane ranks
    # first: his "open another terminal for me" asks had the terminal memory at meaning rank 1-3 and BM25 64-326, final
    # 12-18, and the 0.04 margin lift does not fire at such margins. Offline on 182 bank rows (30 Oct): hit@5 146 -> 153
    # (+11 -4; every loss a gold pushed from 5 to 6). Live A/B on 181 disjoint rows plus his 3 asks: hit@5 150 -> 156
    # (+8 -2, both losses 5 -> 6), hit@1 114 both arms; pooled +19 -6, sign test p ~0.015. On by default;
    # BRAIN_RECALL_QSEM_SLOT=0 turns it off.
    if os.environ.get("BRAIN_RECALL_QSEM_SLOT", "1") not in ("", "0") and qsem and len(scored) > 5:
        try:
            _top = _current_slug(max(qsem.items(), key=lambda it: it[1])[0], superseded, known_slugs)
            _pos = next((i for i, r in enumerate(scored) if r[4] == _top), None)
            if (_pos is not None and _pos > 4 and scored[_pos][0] > 0.02 and _top not in sr.SKIP
                    and _top not in disabled and tier.get(_top) != "unconscious"
                    and (_top not in parent_only or _top in policy_seeds)):
                scored.insert(4, scored.pop(_pos))
        except Exception:
            pass

    # Session clock: a question that names a session id and a clock time ("what was session a52370f8 doing around
    # 09:50") wants that session's compaction from that moment. The lanes found the session (40 of 40 asks had one of
    # its compactions in the top 5) but not the moment (5 of 40 at row 1): every ask about one session got the same
    # three nodes whatever the hour. The one nearest the time (the first written at or after it, which summarises that
    # moment; an earlier one counts twice as far away) takes row 1 when row 1 is already a compaction, else row 2: an
    # incidental mention ("claude-04570302 sent a message at 15:53 about per-term weights") keeps its lesson at row 1
    # (putting three compactions first dropped it to row 4). 40 asks: exact node at row 1 5 -> 40.
    # BRAIN_RECALL_SESSION_CLOCK=0 disables.
    if scored and os.environ.get("BRAIN_RECALL_SESSION_CLOCK", "1") != "0":
        try:
            _sc_rows = _session_clock(query, known_slugs, idx, k=1)
            if _sc_rows:
                s = _sc_rows[0]
                r = next((x for x in scored if x[4] == s), None) or (0.0, 0.0, 0.0, 0.0, s, _status_from_index(s, idx))
                rest = [x for x in scored if x[4] != s]
                pos = 0 if not rest or _ANCHOR.match(rest[0][4]) else 1
                sc = rest[pos - 1][0] - 1e-6 if pos else max(rest[0][0], 0.05) + 1e-3
                scored = rest[:pos] + [(max(sc, 0.05),) + tuple(r[1:])] + rest[pos:]
        except Exception:
            pass
    # PERMISSION RESCUE, part 2: the picks from part 1 are shown from rank PERMIT_AT (default 3) unless already there or
    # better. Measured on q19 (content_gap_68ce/RESULT_q19_3nov.md).
    try:
        _pp = _permit_picks
    except NameError:
        _pp = []
    if _pp and scored:
        try:
            _disp = list(_display_rows(scored, n))
            _byslug = {r[4]: r for r in scored}
            _at0 = int(os.environ.get("BRAIN_RECALL_PERMIT_AT", "3") or 3) - 1
            for _j, _p0 in enumerate(_pp):
                _p = _current_slug(_p0, superseded, known_slugs)
                if _p in sr.SKIP or _p in disabled:
                    continue
                _at = _at0 + _j
                if _p in [r[4] for r in _disp][:_at + 1]:
                    continue
                _row = _byslug.get(_p) or (0.05, 0.0, 0.0, 0.0, _p, _status_from_index(_p, idx))
                _disp = [r for r in _disp if r[4] != _p]
                _sc = _disp[_at][0] if _at < len(_disp) else max(0.05, _row[0])
                _disp.insert(min(_at, len(_disp)), (max(_sc, 0.03),) + tuple(_row[1:]))
            _ds = {r[4] for r in _disp}
            scored = _disp + [r for r in scored if r[4] not in _ds]
        except Exception as _pr_exc:
            print("  [warn] permission rescue skipped (%s: %s)" % (type(_pr_exc).__name__, _pr_exc))

    # PART LIFT (q65, 4 Nov): a memory that is only PARTLY superseded was served above its successor when the question
    # asked about exactly the clause that moved ('which voice must narrate a film': his_standing_film_orders_2026_09_23
    # at 1, its narration-voice clause overtaken by owner_order_d1b_is_the_only_film_voice_2026_10_13 at 4). When a
    # displayed row's superseded_in_part clause shares BRAIN_RECALL_PART_LIFT_MIN (2) content words with the question,
    # its successor goes directly above it. Measured in brain_recall_68ceaf54/RESULT_q65_4nov.md. BRAIN_RECALL_PART_LIFT=0 off.
    if scored and os.environ.get("BRAIN_RECALL_PART_LIFT", "1") != "0":
        try:
            import supersession as _sup
            _pl_min = int(os.environ.get("BRAIN_RECALL_PART_LIFT_MIN", "2") or 2)
            _pl_stop = {"which", "what", "when", "where", "must", "should", "would", "could", "does", "this",
                        "that", "with", "from", "have", "into", "about", "there", "their", "they", "them",
                        "then", "than", "also", "only", "every", "still", "instead", "myself", "used", "uses"}
            _pl_stem = lambda t: {w[:5] for w in re.findall(r"[a-z]{4,}", (t or "").lower()) if w not in _pl_stop}
            _pl_q = _pl_stem(query)
            # q68 (4 Nov): the words a which/what question asks for ("which engine generation ..." -> engin, gener).
            _pl_wh = set()
            if os.environ.get("BRAIN_RECALL_PART_LIFT_WH", "1") != "0":
                _whm = re.match(r"\s*(?:which|what)\s+((?:[a-z0-9-]+\s+){0,2}[a-z0-9-]+)", (query or "").lower())
                if _whm:
                    for _w in _whm.group(1).split()[:2]:
                        if len(_w) < 4 or _w in _pl_stop or _w in ("is", "are", "was", "were", "does", "do"):
                            break
                        _pl_wh.add(_w[:5])
            # q66 (4 Nov): the rest of the old memory - slug words and frontmatter description - for the clause test.
            _pl_rest_on = os.environ.get("BRAIN_RECALL_PART_LIFT_REST", "1") != "0"
            _pl_rest_memo = {}
            def _pl_rest(_slug):
                if _slug not in _pl_rest_memo:
                    _txt = _slug.replace("_", " ")
                    try:
                        with open(os.path.join(MEM, _slug + ".md"), encoding="utf-8", errors="replace") as _fh:
                            _mm = re.search(r"^description:\s*(.*)$", _fh.read(8000), re.M)
                        if _mm:
                            _txt += " " + _mm.group(1)
                    except OSError:
                        pass
                    _pl_rest_memo[_slug] = _pl_stem(_txt)
                return _pl_rest_memo[_slug]
            _disp = list(_display_rows(scored, n))
            _byslug = {r[4]: r for r in scored}
            _pl_done = set()
            # q67 (4 Nov): examine a lifted successor too (chains 3c-D -> 3c-F -> generation 9); rows already examined skip.
            _pl_seen = set()
            _pl_hops = int(os.environ.get("BRAIN_RECALL_PART_LIFT_HOPS", "6") or 6)
            _i = 0
            while _i < len(_disp) and len(_pl_q) >= _pl_min:
                _lifted = False
                if _disp[_i][4] in _pl_seen:
                    _i += 1
                    continue
                _pl_seen.add(_disp[_i][4])
                for _cl, _succ in _sup.read_part_supersessions(MEM, _disp[_i][4]):
                    if not _succ or len(_pl_q & _pl_stem(_cl)) < _pl_min:
                        continue
                    if _pl_rest_on:
                        _pl_cs = _pl_stem(_cl)
                        if not (_pl_wh and _pl_wh <= _pl_cs) and \
                                len(_pl_q & _pl_cs) <= len(_pl_q & (_pl_rest(_disp[_i][4]) - _pl_cs)):
                            continue
                    _cur = _current_slug(_succ, superseded, known_slugs)
                    if _cur in _pl_done or _cur in sr.SKIP or _cur in disabled or _cur == _disp[_i][4]:
                        continue
                    _pl_done.add(_cur)
                    if _cur in [r[4] for r in _disp[:_i]]:
                        continue
                    _row = _byslug.get(_cur) or (0.05, 0.0, 0.0, 0.0, _cur, _status_from_index(_cur, idx))
                    _sc = _disp[_i][0]
                    _disp = [r for r in _disp if r[4] != _cur]
                    _disp.insert(_i, (max(_sc, 0.03),) + tuple(_row[1:]))
                    _lifted = True
                    break
                if _lifted:
                    _pl_hops -= 1
                    if _pl_hops <= 0:
                        break
                else:
                    _i += 1
            _ds = {r[4] for r in _disp}
            scored = _disp + [r for r in scored if r[4] not in _ds]
        except Exception as _pl_exc:
            print("  [warn] part lift skipped (%s: %s)" % (type(_pl_exc).__name__, _pl_exc))

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
        # a description that opens [VERIFIED] on a provisional memory (498 on 1 Nov, filed without --status verified)
        # showed two answers on one hit; the header carries the status, so the contradicting tag is dropped here,
        # as the index row already drops it. Retracted/superseded tags are never dropped.
        _tag = re.match(r"\s*[\"']?\[(VERIFIED|PROVISIONAL|OWNER-STATED)\]\s*", d)
        if _tag and status in ("verified", "provisional", "owner-stated") and _tag.group(1).lower() != status:
            d = d[_tag.end():] or d
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
            # CLOSE-TOP-TWO (30 Oct, memory recall_is_wrong_only_when_its_top_two_are_close_2026_10_30): on 124
            # labelled asks every wrong #1 but four sat within 0.10 of #2, and no #1 that led by 0.65 was wrong.
            # The advisory above caught 4 of those 19 wrong #1s; this one catches 15, flagging 5 right ones.
            elif _top - displayed[1][0] < 0.10:
                print("  [close-top-two] #1 leads #2 by %.2f - at that margin #1 is often the wrong memory; read"
                      " both before trusting #1." % (displayed[0][0] - displayed[1][0]))
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


def _yaml_desc(desc):
    """The description as a YAML double-quoted scalar (2026-11-04, TOP10 rank 8 item 7). Written raw, 27% of notes
    failed a strict YAML parser: a leading [PROVISIONAL] opens a flow sequence, an inner ': ' starts a second
    mapping. json.dumps escapes backslash and quote, which is valid YAML double-quoted; ensure_ascii=False keeps
    non-ASCII readable for the readers that only strip the quotes. A value already wrapped in double quotes is
    left as given so a caller that quotes it itself is not quoted twice."""
    d = desc or ""
    if len(d) >= 2 and d.startswith('"') and d.endswith('"'):
        return d
    return json.dumps(d, ensure_ascii=False)


def _render_memory_text(slug, mtype, desc, body, status, evidence, created,
                        supersedes="", created_at="", origin_session="", legacy_desc=False):
    # legacy_desc=True renders the description raw, as every memory before 2026-11-04 was written; repair mode
    # tries it second so a memory filed just before the change can still be repaired after it.
    if not legacy_desc:
        desc = _yaml_desc(desc)
    evidence_line = json.dumps((evidence or "").strip(), ensure_ascii=False)
    supersedes_line = ("  supersedes: %s\n" % supersedes) if supersedes else ""
    # originSessionId (2026-10-30): the session that filed it, in the same key the harness-written memories carry, so
    # transactive_index.py can say which session knew a topic. Emitted only when given, like created_at.
    origin_line = ("  originSessionId: %s\n" % origin_session) if origin_session else ""
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
            "%s"
            "---\n\n%s\n" %
            (slug, desc, mtype, created, created_at_line, status, evidence_line,
             origin_line, supersedes_line, body or ""))


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
            origin_matches = re.findall(r"(?m)^  originSessionId:\s*(\S+)\s*$", text)
            if len(origin_matches) > 1:
                raise ValueError("memory must carry at most one originSessionId")
            expected = _render_memory_text(
                slug, mtype, desc, body, status, evidence,
                created_matches[0],
                created_at=(created_at_matches[0] if created_at_matches else ""),
                origin_session=(origin_matches[0] if origin_matches else "")).encode("utf-8")
            if raw != expected:
                expected = _render_memory_text(
                    slug, mtype, desc, body, status, evidence,
                    created_matches[0],
                    created_at=(created_at_matches[0] if created_at_matches else ""),
                    origin_session=(origin_matches[0] if origin_matches else ""),
                    legacy_desc=True).encode("utf-8")
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


_PRIOR_ROWS = []    # the advisory's rows for this filing, read again by the closing link hint


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
    _PRIOR_ROWS[:] = rows
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
# The early laws (June-July 2026) were filed before the [OWNER-STATED] tag and open with markup: "**<emoji> 2026-06-10 OWNER
# HARD RULE: ...". The family takes them too (q20, 3 Nov 2026: 600 -> 605 family memories, every one an owner rule).
_OWNER_FAMILY_RX2 = re.compile(r"[\s\"'\[\(\*_~#>|\u2600-\u27BF\u2B00-\u2BFF\U0001F000-\U0001FAFF\uFE0F\u200d\u2605\u2606]*"
                               r"(?:\d{4}-\d{2}-\d{2}[\sT:0-9x]*)?[\s\*_\-\u2014,:\u2600-\u27BF\U0001F000-\U0001FAFF\uFE0F]*"
                               r"(?:OWNER|Owner)(?:'s)?\s+(?:HARD\s+|STANDING\s+|STANDARD\s+)?"
                               r"(?:RULE|LAW|ORDER|POLICY|CORRECTION|DIRECTIVE)", re.U)
_OWNER_INDEX_LINE_RX = re.compile(r"-\s*\[([^\]]+?)\.md\]\([^)]*\)\s*[-\u2014]*\s*(.*)$")
_OWNER_STATUS_TAG_RX = re.compile(r"\[(PROVISIONAL|VERIFIED|OWNER-STATED|RETRACTED)\]\s*")
_OWNER_STATED = set()  # q40: slugs whose index line carries [OWNER-STATED], filled by _owner_family_texts
_OWNER_ORDER_SLUG_RX = re.compile(r"owner_(order|law|rule|directive|allows)_")
OWNER_ADVISORY_BUDGET_S = 1.5     # the join: what the filer waits at most, plus the OS's scheduling slack
OWNER_ADVISORY_ROWS = 5


_PERMIT_RX = re.compile(r"^\s*(?:can|could|may|should|shall)\s+(?:i|we)\b|^\s*is\s+it\s+(?:ok|okay|fine|safe|alright|allowed)\b"
                         r"|^\s*am\s+i\s+allowed\b|^\s*do\s+i\s+need\s+permission\b"
                         # q42: a third-person agent subject asks the same thing (can a codex lane take ...)
                         r"|^\s*(?:can|could|may|should|shall)\s+(?:a|an|the|this|that|my|our|one|another)\s+"
                         r"(?:\S+\s+)?(?:codex|claude|gpt\S*|agent|agents|worker|workers|lane|lanes|solver|solvers|"
                         r"session|subagent|fleet|helper|job|runner)\b", re.I)
# q49 (3 Nov): permission asked about an act rather than by a person - "is spawning an agent for X allowed",
# "would launching ... be fine", "are we allowed to", "is it permitted". The gerund keeps "is the render fine" out.
_PERMIT_RX2 = re.compile(r"^\s*(?:are|am)\s+(?:we|i)\s+(?:allowed|permitted)\b|^\s*is\s+it\s+permitted\b"
                          r"|^\s*(?:is|would|will)\s+(?!(?:every|any|no|some)?thing\b|morning\b|evening\b|string\b|spring\b)\w+ing\b[^.?!]*\b(?:allowed|permitted|ok|okay|fine|alright|acceptable)\b",
                          re.I)
# q50 (3 Nov): the elliptical permission question names no person and no gerund - "ok to hand X to an agent?",
# "another agent for X - fine?", "X, allowed?". A permission word opens it before "to", or closes it after a dash,
# colon or comma right before the question mark.
_PERMIT_RX3 = re.compile(r"^\s*(?:is\s+it\s+)?(?:ok|okay|fine|alright|allowed|acceptable|permitted)\s+to\s+\w"
                          r"|[-:,\u2013\u2014]\s*(?:is\s+(?:that|this|it)\s+)?(?:ok|okay|fine|alright|allowed|acceptable|permitted)\s*\?+\s*$",
                          re.I)
# q51 (3 Nov): the permission head after a leading clause. A hyphen counts only with spaces round it, so a name
# like vanilla-brain is not a break.
_LEAD_BREAK_RX = re.compile(r"[,;:.\u2013\u2014]|\s-+\s")


def _permit_lead(q):
    if os.environ.get("BRAIN_RECALL_PERMIT_LEAD", "1") == "0":
        return False
    for m in _LEAD_BREAK_RX.finditer(q or ""):
        tail = q[m.end():]
        if _PERMIT_RX.search(tail) or _PERMIT_RX2.search(tail):
            return True
    return False


_MAC_SCOPE_RX = re.compile(r"\bon the mac\b|\bmac mini\b|\bmac clock\b", re.I)
_ALL_SCOPE_RX = re.compile(r"laptop|the research box|all three|three computers|every computer|both computers|this pc", re.I)
_PERMIT_VERBS = {"pause": "suspend stop", "paused": "suspend stop", "suspend": "stop pause", "halt": "stop suspend",
                 "kill": "stop terminate", "terminate": "kill stop", "throttle": "lower priority",
                 "deprioritize": "lower priority throttle", "truncate": "delete", "wipe": "delete", "purge": "delete",
                 "prune": "delete", "erase": "delete", "rm": "delete", "remove": "delete", "clear": "delete",
                 "trim": "delete", "rotate": "delete",
                 # q33 (3 Nov): a launch question names the act and the worker in its own words (spin up codex solvers);
                 # the order says launch / start / workers / agents.
                 "spin": "launch start", "launch": "start run", "start": "launch run", "relaunch": "launch restart",
                 "restart": "relaunch start", "kick": "start launch", "solvers": "workers agents", "solver": "worker agent",
                 "workers": "agents", "worker": "agent"}
_DELEG_RX = re.compile(r"\bsub-?agents?\b|\b(?:another|other|second|separate|extra|worker|task|helper|child|sub|parallel|background|two|three|several|more)\s+agents?\b|\b(?:delegat\w*|offload\w*|outsourc\w*|spawn\w*|fork\w*)\b[^.?!]*\bagents?\b", re.I)
_PERMIT_STRIP = re.compile(r"\b(?:can|could|may|should|shall|i|we|is|it|ok|okay|fine|safe|alright|allowed|am|to|the|a|an|"
                           r"this|that|these|those|my|our|here|so|just|quick|quickly)\b", re.I)


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
        if tag and tag.group(1) == "OWNER-STATED":
            _OWNER_STATED.add(slug)
        if (slug.startswith("owner_") or (tag and tag.group(1) == "OWNER-STATED") or _OWNER_DESC_RX.match(bare)
                or _OWNER_FAMILY_RX2.match(bare)):
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


def _kick_fresh_vectors():
    """Start the meaning-vector overlay refresh (fresh_vectors.py) in the background and return at once. The Mac has no
    15-minute task for it: on 30 Oct 26 memories went unencoded for an hour, invisible to the meaning lane. Single-flight
    inside fresh_vectors; BRAIN_REMEMBER_REFRESH=0 skips it like the BM25 step."""
    if os.environ.get("BRAIN_REMEMBER_REFRESH", "1") == "0" or os.name == "nt":   # Windows keeps its scheduled task
        return
    try:
        import fresh_vectors
        pid = fresh_vectors.spawn_detached()
        print("vectors: overlay refresh %s" % ("running (pid %d)" % pid if pid else "not started; the memory is saved"))
    except Exception as exc:
        print("vectors: overlay refresh not started (%s); the memory is saved" % type(exc).__name__)


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
            _origin = re.sub(r"[^0-9A-Za-z_-]", "",
                             os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CODEX_THREAD_ID") or "")
            rendered = _render_memory_text(
                slug, mtype, desc, body, status, evidence,
                _created, supersedes, created_at=_created_at, origin_session=_origin)
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
    except Exception as err:
        for leftover in (tmp, old_tmp):
            if leftover:
                try:
                    os.remove(leftover)
                except OSError:
                    pass
        print("memory write FAILED: %s" % err)
        return 2
    _maybe_compact_index()
    print("wrote %s\nindexed in MEMORY.md as %s" % (path, status))
    _kick_bm25_refresh(slug, supersedes)
    _kick_fresh_vectors()
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
    # A link to an existing memory under the wrong date dangles and the graph drops the edge: 17 of 167 dangling
    # dated targets on 30 Oct were real memories one day off (1000-ways D6 #27). Warn only; never breaks a write.
    try:
        for _x, _real in _misdated_links(body or ""):
            print("link [[%s]] has no memory; did you mean [[%s]]?" % (_x, _real))
    except Exception:
        pass
    # Name the real neighbour, not a fixed example (1000-ways D8 #45, 31 Oct): of 78 Mac filings since 16 Oct whose
    # nearest earlier memory scored >= 50, 44 did not link it, and 13 of 16 of those judged were follow-ups that
    # should have. This rule fires on 23 of 232 of those filings. Advisory only; never breaks a write.
    try:
        _linked = set(re.findall(r"\[\[([^\]|#]+)", body or ""))
        _near = _PRIOR_ROWS[0] if _PRIOR_ROWS and _PRIOR_ROWS[0][1] >= 50.0 and not (
            _linked & {s for s, _ in _PRIOR_ROWS}) else None
    except Exception:
        _near = None
    if _near:
        # say why in the same line: explained links were followed 12 of 186 times vs bare 5 of 220 (D9 #97, 31 Oct)
        print("not linked: the nearest earlier memory is [[%s]] (bm25 %.0f). If this builds on it, add "
              "'Related: [[%s]] - <how it bears on this, in a few words>' to %s (a reason gets the link followed)"
              % (_near[0], _near[1], _near[0], path))
    else:
        print("now link it: mention related memories in the body as double-bracketed slugs with a few words on why, "
              "e.g. 'same failure as [[kettle_descaling_routine_2026_01_01]]' (the graph builds from these)")
    return 0


def _misdated_links(text):
    """[[links]] in text with no memory file whose slug minus the _YYYY_MM_DD date matches exactly one memory
    under another date. Returns [(linked, existing)]."""
    import glob
    out = []
    for x in sorted(set(re.findall(r"\[\[([a-z0-9][a-z0-9_\-]{2,120})\]\]", text))):
        m = re.match(r"^(.*)_20\d\d_\d\d_\d\d$", x)
        if not m or os.path.exists(os.path.join(MEM, x + ".md")):
            continue
        hits = [f[:-3] for f in glob.glob(os.path.join(MEM, glob.escape(m.group(1)) + "_20[0-9][0-9]_*.md"))]
        hits = [os.path.basename(h) for h in hits if re.match(r"^_20\d\d_\d\d_\d\d$", os.path.basename(h)[len(m.group(1)):])]
        if len(hits) == 1:
            out.append((x, hits[0]))
    return out


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
        if parsed.part <= 1 and _mem_is_live():
            # a whole-node read is a memory READ: keep it for the usage trace (read_trace.py). Later parts of the
            # same node are not counted again, and a run against a copied memory dir is not use.
            try:
                import read_trace
                read_trace.append(os.path.basename(parsed.slug).removesuffix(".md"),
                                  os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CODEX_THREAD_ID"),
                                  "node")
            except Exception:
                pass
            # ... and a co-read, the same as a Read of the file (1 Nov 2026, D9 #78/#87): since 29 Oct memories are
            # opened through node, so the co-read logs stayed empty and 38 folds added no pair. Claude sessions only:
            # the Stop fold that consumes the log keys it by the Claude session id.
            try:
                _sess = os.environ.get("CLAUDE_CODE_SESSION_ID")
                _slug = os.path.basename(parsed.slug).removesuffix(".md")
                if _sess and re.fullmatch(r"[A-Za-z0-9_.-]+", _sess) and not _slug.startswith("dream_"):
                    _cl = os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "coactivation_session")
                    os.makedirs(_cl, exist_ok=True)
                    with io.open(os.path.join(_cl, _sess + ".log"), "a", encoding="utf-8") as _f:
                        _f.write(_slug + "\n")
            except Exception:
                pass
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


def _no_power_throttling():
    """Windows runs a windowless python (every hook and agent call of recall) under EcoQoS by default: 1 Nov 2026,
    12 alternating recalls on one query, median wall 19.4 s default vs 7.4 s opted out, cpu ~5-7 s both, answers equal.
    Opt this process out (speed policy controlled, state off); priority is untouched. Never fails the command."""
    if os.name != "nt":
        return
    try:
        import ctypes
        import ctypes.wintypes as _w

        class _PPTS(ctypes.Structure):
            _fields_ = [("Version", _w.ULONG), ("ControlMask", _w.ULONG), ("StateMask", _w.ULONG)]
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentProcess.restype = _w.HANDLE
        k.SetProcessInformation.argtypes = [_w.HANDLE, ctypes.c_int, ctypes.c_void_p, _w.DWORD]
        k.SetProcessInformation.restype = _w.BOOL
        st = _PPTS(1, 1, 0)
        k.SetProcessInformation(k.GetCurrentProcess(), 4, ctypes.byref(st), ctypes.sizeof(st))
    except Exception:
        pass


def main():
    args = sys.argv[1:]
    _no_power_throttling()
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip().split("\n\n")[1])
        return 0
    cmd = args[0]
    if cmd == "recall":
        # RECALL-10X (2026-11-02, claude-1e0a23c2): the CLI recall loads the vector sidecar lazily - every user of it in
        # this file already checks each row it uses (row_current / embedding_row_current), so the eager stat scan was
        # duplicate work (load median 4.3 -> 1.2 s, top-8 unchanged; recall10x/ab_walk_out.txt). Only the CLI, never the
        # importable recall(): hooks import this module in the dispatcher. BRAIN_EMB_LAZY=0 turns it off.
        # Not inside the recall server (6 Nov 2026): there the parsed sidecar stays resident between calls, and a
        # lazy load re-read it on every call, 166 -> 259 ms p50 over 24 served recalls, same output.
        if __name__ != "brain_recall_server_module":
            os.environ.setdefault("BRAIN_EMB_LAZY", "1")
        # a query is never a flag (2026-09-22, Test A): a FIRE WHEN line pasted as "- the situation ..." begins
        # with "-" and used to be dropped whole, returning usage text - 322 of 1,038 lines in the F42 bank and
        # every bullet-led query the Opus agent sent. Only a bare option-shaped token is a flag.
        def _is_flag(a):
            return bool(re.match(r"^--?[A-Za-z][\w-]*$", a))

        def _parse(toks):
            toks = ["recall"] + toks
            q = " ".join(a for a in toks[1:] if not _is_flag(a))
            n = None
            if "-n" in toks:
                try:
                    n = int(toks[toks.index("-n") + 1])
                    q = " ".join(a for i, a in enumerate(toks[1:], 1)
                                 if not _is_flag(a) and i != toks.index("-n") + 1)
                except Exception:
                    pass
            return re.sub(r"^[\-\*>\s]+", "", q), n   # strip a leading bullet or quote marker
        # --or: several wordings of one question, fused by rank (30 Oct)
        parts, cur = [], []
        for a in args[1:]:
            if a == "--or":
                parts.append(cur)
                cur = []
            else:
                cur.append(a)
        parts.append(cur)
        parsed = [_parse(p) for p in parts]
        qs = [q for q, _ in parsed if q.strip()]
        n = next((pn for _, pn in parsed if pn), 8)
        if not qs:
            print("usage: brain.py recall \"your question\" [--or \"another wording\" ...] [-n 8]")
            return 1
        if len(qs) > 1:
            return recall_or(qs, n)
        rc = recall(qs[0], n)
        # one line on stderr (stdout parsers never see it) when a question came with one wording: on the 181-row bank
        # on 1 Nov, three wordings via --or took hit@1 112 -> 138 (30 gained, 4 lost); agents used --or on one recall in
        # eight on 30 Oct and 100 of 220 in the 24 h before this line. A hint keyed to flat scores was rejected (it flags
        # 79 asks to catch 20 misses), so this one is unconditional. BRAIN_RECALL_OR_TIP=0 turns it off.
        if os.environ.get("BRAIN_RECALL_OR_TIP", "1") != "0":
            try:
                sys.stderr.write("recall: one wording. A second via --or \"...\" (the object, method or name the memory "
                                 "would use) took the bank's rank-1 hits 112 -> 138 of 181.\n")
            except Exception:
                pass
            # 2 Nov: the stderr line is mostly never read. Of the single-wording calls in the Claude transcripts, 37%
            # sent stderr to /dev/null and 34% more piped it through head or tail, and --or use fell from 45% of
            # recalls on 31 Oct to 27% on 2 Nov. So a successful recall also ends stdout with a plain line that the
            # row parsers (recall_policy_regression, eval_brain_recall_fresh, compact_nodes: all match "score [tags]
            # slug") skip. On the 29 Oct M1 audit misses, one --or call put gold in the top 8 for 86 of 100, the
            # first wording alone for 28. OR2 (2 Nov, 300 bank questions, Codex wordings): a second wording guessed
            # before looking lifted top-8 175 -> 203 of 289 but top-1 only 110 -> 116; one written after reading what
            # came back took top-1 65 -> 81 of 223. So the line says to look first.
            if rc == 0:
                try:
                    print("  (one wording; if the memory you want is not first, rerun with --or \"...\" in its own words)")
                except Exception:
                    pass
        return rc
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
              "neighbors | remember | supersede | supersede-part | status | where | "
              "dream-about \"topic\" [--why ...] | dream-requests | "
              "strong-node-proposals" % cmd)
        return 1
    return 0


def _exit(code):
    """sys.exit(code), except when onnxruntime was loaded in this process (the in-thread encoder): then the exit
    handlers run and stdio is flushed here, and the process ends with os._exit, so onnxruntime's C++ teardown never
    runs. In 1.31.1 that teardown (OneDsTelemetry::Shutdown, called from exit()) can spin on sched_yield for good: two
    recalls from 30 Oct were found still in it 22 h later, about 220 CPU-minutes each, on 31 Oct."""
    if "onnxruntime" not in sys.modules:
        sys.exit(code)
    if code is None:
        rc = 0
    elif isinstance(code, int):
        rc = code
    else:
        print(code, file=sys.stderr)
        rc = 1
    try:
        import atexit
        atexit._run_exitfuncs()
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(rc)


if __name__ == "__main__":
    try:
        _rc = main()
    except SystemExit as e:
        _rc = e.code
    except KeyboardInterrupt:
        _rc = 130
    except BaseException:
        import traceback
        traceback.print_exc()
        _rc = 1
    _exit(_rc)
