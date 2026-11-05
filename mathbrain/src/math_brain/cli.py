"""Command-line entry point for local, deny-by-default operation."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

from .models import Claim, ClaimKind
from .privacy import (
    PrivacyViolation,
    check_manifest_source_path,
    load_allowlist_manifest,
)
from .privacy_scan import load_scan_policy, scan_repository_files
from .publication import check_public_export
from .retrieval import retrieve, search
from .store import ResearchStore
from .triggers import load_triggers, match_triggers



# M7 is a separate identifier-only background channel. The live kill switch is
# installed before code landing; its presence retains the original CLI output.
BACKGROUND_OFF = Path(__file__).resolve().parents[2] / "BACKGROUND_OFF"
_BACKGROUND_INDEX_CACHE = None


def _background_enabled() -> bool:
    return not BACKGROUND_OFF.exists()


def _serving_background(nodes, edges):
    from .background_hubs import CommunityIndex, _projection, _fingerprint
    from .retrieval import RetrievalGuard, guard_nodes, current_revision_nodes
    global _BACKGROUND_INDEX_CACHE
    # Population cards may predate split labels; explicit blocked roles,
    # quarantine/privacy markers and labelled evaluation splits still fail closed.
    guard = RetrievalGuard(evaluation_split="m7_serving", require_split_label=False)
    accepted, _ = guard_nodes(nodes, guard)
    current, _ = current_revision_nodes(accepted, edges, known_ids=(n.id for n in nodes))
    ids = set()
    for node in current:
        domain = node.metadata.get("domain", "")
        if str(node.kind) == "claim" and isinstance(domain, str):
            domain = domain.casefold()
            if "probab" in domain or "stoch" in domain:
                ids.add(node.id)
    links = _projection(ids, edges)
    fingerprint = _fingerprint(ids, links)
    if _BACKGROUND_INDEX_CACHE is None or _BACKGROUND_INDEX_CACHE[0] != fingerprint:
        index = CommunityIndex.build(ids, edges, resolution=.05, theta=.01,
                                     seed=152, iterations=2)
        _BACKGROUND_INDEX_CACHE = (fingerprint, index)
    return _BACKGROUND_INDEX_CACHE[1].prepare(eligible_ids=ids, edges=edges)


def _background_json(result) -> list[dict]:
    return [{"id": candidate.node_id, "score": candidate.activation,
             "seed_ids": list(candidate.seed_ids), "community_id": candidate.community_id,
             "direct": candidate.direct} for candidate in result.background]

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="math-brain")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="initialize an empty local brain database")
    init.add_argument("database", type=Path)

    policy = sub.add_parser("policy", help="evaluate a source path without reading it")
    policy_sub = policy.add_subparsers(dest="policy_command", required=True)
    check = policy_sub.add_parser("check", help="check a file against a run-local manifest")
    check.add_argument("path", type=Path)
    check.add_argument("--manifest", type=Path, required=True)
    check.add_argument("--source-id", required=True)
    check.add_argument("--repository-root", type=Path, default=Path.cwd())

    scan = policy_sub.add_parser("scan", help="scan explicitly named repository files")
    scan.add_argument("paths", type=Path, nargs="+")
    scan.add_argument("--repository-root", type=Path, default=Path.cwd())
    scan.add_argument("--config", type=Path, default=Path("config/privacy.toml"))

    export = policy_sub.add_parser("export", help="validate one public artifact and sidecar")
    export.add_argument("artifact", type=Path)
    export.add_argument("--manifest", type=Path, required=True)
    export.add_argument("--repository-root", type=Path, default=Path.cwd())

    add = sub.add_parser("add-claim")
    add.add_argument("database", type=Path)
    add.add_argument("statement")
    add.add_argument("--kind", choices=[kind.value for kind in ClaimKind], required=True)
    add.add_argument("--domain", default="")

    recall = sub.add_parser("recall")
    recall.add_argument("database", type=Path, nargs="?", default=os.environ.get("MATHBRAIN_STORE") or None,
                        help="the store; defaults to MATHBRAIN_STORE")
    recall.add_argument("query")
    recall.add_argument("--limit", type=int, default=10)
    recall.add_argument(
        "--skip-unknown-edges",
        action="store_true",
        help="skip edge rows whose kind this build does not define (one warning per kind) "
        "instead of refusing; task 462",
    )
    recall.add_argument(
        "--decision",
        action="store_true",
        help="print retrieve()'s whole decision - abstained, reason, exclusions and each hit's "
        "support and trace - instead of the plain hit list",
    )
    recall.add_argument("--min-support", type=float, default=0.15,
                        help="with --decision: the support threshold below which it abstains")
    recall.add_argument("--min-margin", type=float, default=0.0,
                        help="with --decision: the margin below which two unlinked hits abstain")

    current_node = sub.add_parser("current-node", help="read one complete current revision without modifying the store")
    current_node.add_argument("database", type=Path)
    current_node.add_argument("node_id")
    current_node.add_argument("--artifact-root", type=Path)

    # Typed paths (w15 mechanism 6): read-only views of a card's revisions, source and typed edges; text, not JSON.
    for name, text in (("history", "a card's revision family, oldest first: what each retired version changed"),
                       ("source", "where the current version comes from: provenance, record fields, papers, cited cards"),
                       ("links", "the current version's typed edges, in and out, retired ends shown at their head")):
        typed = sub.add_parser(name, help=text)
        typed.add_argument("database", type=Path)
        typed.add_argument("node_id")
    typed_path = sub.add_parser("path", help="the shortest chain of typed edges from one card to another")
    typed_path.add_argument("database", type=Path)
    typed_path.add_argument("source_id")
    typed_path.add_argument("target_id")
    typed_path.add_argument("--undirected", action="store_true", help="follow edges both ways")
    typed_path.add_argument("--kinds", help="comma-separated edge kinds (default: every typed kind but supersedes)")

    trigger = sub.add_parser("triggers")
    trigger.add_argument("text")
    trigger.add_argument("--config", type=Path, default=Path("config/triggers.toml"))
    return parser



def _decision_json(result) -> dict:
    """retrieve()'s result as JSON: the verdict first, then each hit with its support and trace."""
    return {
        "abstained": result.abstained,
        "reason": result.reason.value if result.reason is not None else None,
        "exclusions": [{"id": e.node_id, "reason": e.reason} for e in result.exclusions],
        "hits": [
            {
                "id": hit.node.id,
                "kind": hit.node.kind.value,
                "title": hit.node.title,
                "score": hit.total_score,
                "support": hit.support_score,
                "warning_score": hit.warning_score,
                "trace": [
                    {"channel": tr.channel.value, "amount": tr.amount, "detail": tr.detail}
                    for tr in hit.trace
                ],
            }
            for hit in result.hits
        ],
    }

def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "current-node":
        from .current_note import NoteReadError, read_current_note
        try:
            result = read_current_note(args.database, args.node_id, artifact_root=args.artifact_root)
        except NoteReadError as exc:
            print(json.dumps({"schema": "current-full-math-note-error-v1", "reason": str(exc), "body_returned": False}))
            return 2
        print(json.dumps(result, ensure_ascii=True))
        return 0
    if args.command in ("history", "source", "links", "path"):
        from . import typed_paths
        db = str(args.database)
        con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True, timeout=10)
        try:
            st = typed_paths.Store(con, refusal=typed_paths.reader_refusal(db))
            if args.command == "path":
                kinds = tuple(args.kinds.split(",")) if args.kinds else typed_paths.PATH_KINDS
                print(typed_paths.path(st, args.source_id, args.target_id, kinds=kinds, undirected=args.undirected))
            else:
                print(getattr(typed_paths, args.command)(st, args.node_id))
        finally:
            con.close()
        return 0
    if args.command == "init":
        with ResearchStore(args.database):
            pass
        print(json.dumps({"database": str(args.database), "initialized": True}))
        return 0
    if args.command == "policy":
        if args.policy_command == "check":
            try:
                manifest = load_allowlist_manifest(
                    args.manifest,
                    repository_root=args.repository_root,
                )
                decision = check_manifest_source_path(
                    args.path,
                    manifest=manifest,
                    source_id=args.source_id,
                )
            except PrivacyViolation as exc:
                decision = {"allowed": False, "code": exc.code, "reason": str(exc)}
            else:
                decision = {
                    "allowed": decision.allowed,
                    "code": decision.code,
                    "reason": decision.reason,
                }
            print(json.dumps(decision, ensure_ascii=False))
            return 0 if decision["allowed"] else 2
        if args.policy_command == "scan":
            try:
                scan_policy = load_scan_policy(
                    args.config,
                    repository_root=args.repository_root,
                )
                findings = scan_repository_files(
                    repository_root=args.repository_root,
                    relative_paths=args.paths,
                    policy=scan_policy,
                )
            except PrivacyViolation as exc:
                payload = {"allowed": False, "code": exc.code, "reason": str(exc)}
                print(json.dumps(payload, ensure_ascii=False))
                return 2
            payload = {
                "allowed": not findings,
                "findings": [
                    {
                        "code": finding.code,
                        "path": finding.path,
                        "line": finding.line,
                        "column": finding.column,
                        "message": finding.message,
                    }
                    for finding in findings
                ],
            }
            print(json.dumps(payload, ensure_ascii=False))
            return 0 if not findings else 2
        if args.policy_command == "export":
            decision = check_public_export(
                args.artifact,
                manifest_path=args.manifest,
                repository_root=args.repository_root,
            )
            print(
                json.dumps(
                    {
                        "allowed": decision.allowed,
                        "code": decision.code,
                        "reason": decision.reason,
                    },
                    ensure_ascii=False,
                )
            )
            return 0 if decision.allowed else 2
        raise AssertionError("unreachable policy command")
    if args.command == "add-claim":
        claim = Claim(args.statement, ClaimKind(args.kind), domain=args.domain)
        with ResearchStore(args.database) as store:
            store.add_claim(claim)
        print(json.dumps({"claim_id": claim.id, "digest": claim.digest()}))
        return 0
    if args.command == "recall":
        if args.database is None:
            raise SystemExit("math-brain recall: give the store path or set MATHBRAIN_STORE")
        args.database = Path(args.database)
        # Read-only: a recall changes nothing, and a mistyped path must not become a new store.
        with ResearchStore.open_read_only(args.database) as store:
            if _background_enabled():
                nodes = tuple(store.iter_nodes())
                edges = tuple(store.iter_edges(skip_unknown=args.skip_unknown_edges))
                hubs = _serving_background(nodes, edges)
                result = retrieve(
                    args.query, nodes=nodes, edges=edges, limit=args.limit,
                    min_support_score=args.min_support if args.decision else 0.0,
                    min_margin=args.min_margin if args.decision else 0.0,
                    background_hubs=hubs, background_limit=5,
                )
                if args.decision:
                    output = _decision_json(result)
                else:
                    output = {"hits": [{"id": hit.node.id, "kind": hit.node.kind.value,
                                        "title": hit.node.title, "score": hit.total_score,
                                        "warning_score": hit.warning_score}
                                       for hit in result.hits]}
                output["background"] = _background_json(result)
                print(json.dumps(output, ensure_ascii=False))
                return 0
            if args.decision:
                # search() forces the support threshold to 0 and returns hits only, so the plain
                # output never shows an abstention (account04/B01 r3,
                # B01-R1-CLI-DECISION-TRACE-PROJECTION-20260929-01).
                result = retrieve(
                    args.query,
                    nodes=store.iter_nodes(),
                    edges=store.iter_edges(skip_unknown=args.skip_unknown_edges),
                    limit=args.limit,
                    min_support_score=args.min_support,
                    min_margin=args.min_margin,
                )
                print(json.dumps(_decision_json(result), ensure_ascii=False))
                return 0
            hits = search(
                args.query,
                nodes=store.iter_nodes(),
                edges=store.iter_edges(skip_unknown=args.skip_unknown_edges),
                limit=args.limit,
            )
        print(
            json.dumps(
                [
                    {
                        "id": hit.node.id,
                        "kind": hit.node.kind.value,
                        "title": hit.node.title,
                        "score": hit.total_score,
                        "warning_score": hit.warning_score,
                    }
                    for hit in hits
                ],
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "triggers":
        matches = match_triggers(args.text, load_triggers(args.config))
        print(
            json.dumps(
                [
                    {
                        "id": match.trigger.id,
                        "keywords": match.matched_keywords,
                        "action": match.trigger.action,
                    }
                    for match in matches
                ],
                ensure_ascii=False,
            )
        )
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
