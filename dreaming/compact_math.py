"""Stage an explicitly selected math-only solver digest for peer review.

Never opens a math store. Files end in .json.pending so the population
integrator's bundle_*.json scan cannot ingest an unaudited digest.
"""
import hashlib
import json
import os
import re
import uuid

SCHEMA = "math-solver-compaction-v1"
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
LOGICAL_ID = re.compile(r"math:[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+\Z")


def _text(value, field, minimum=1, maximum=12000):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError("invalid math digest " + field)
    # Reject rather than silently changing or clipping mathematical symbols.
    value.encode("utf-8")
    return value.strip()


def normalize_packet(packet):
    if not isinstance(packet, dict) or packet.get("schema") != SCHEMA:
        raise ValueError("unsupported math digest schema")
    if packet.get("export_scope") != "public_math" or packet.get("contains_test_material") is not False:
        raise ValueError("math digest needs an explicit public-math, test-free declaration")
    stage = _text(packet.get("stage_id"), "stage_id", maximum=128)
    if not IDENTIFIER.fullmatch(stage):
        raise ValueError("invalid math digest stage_id")
    author = _text(packet.get("author_agent"), "author_agent", maximum=128)
    if not IDENTIFIER.fullmatch(author):
        raise ValueError("invalid math digest author_agent")
    session = _text(packet.get("session_id"), "session_id", maximum=128)
    if not IDENTIFIER.fullmatch(session):
        raise ValueError("invalid math digest session_id")
    title = _text(packet.get("title"), "title", maximum=160)
    statement = _text(packet.get("statement"), "statement", minimum=20)
    domain = _text(packet.get("domain", "probability"), "domain", maximum=120)
    assumptions = packet.get("assumptions", [])
    references = packet.get("references", [])
    if not isinstance(assumptions, list) or len(assumptions) > 30:
        raise ValueError("invalid math digest assumptions")
    assumptions = [_text(a, "assumption", maximum=2000) for a in assumptions]
    if not isinstance(references, list) or len(references) > 30:
        raise ValueError("invalid math digest references")
    for ref in references:
        if not isinstance(ref, str) or not LOGICAL_ID.fullmatch(ref):
            raise ValueError("invalid existing math logical identifier")
    return {"schema": SCHEMA, "stage_id": stage, "author_agent": author, "session_id": session,
            "title": title, "statement": statement, "domain": domain,
            "assumptions": assumptions, "references": sorted(set(references))}


def _publish(path, text):
    tmp = os.path.join(os.path.dirname(path), ".part-" + uuid.uuid4().hex)
    with open(tmp, "x", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def stage_math_node(packet, compaction_name, directory, session_id):
    """Return an auditable draft reference; consume only the selected digest."""
    selected = normalize_packet(packet)
    if selected["session_id"] != session_id:
        return None
    canonical = json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    logical_name = "stage-" + digest
    logical_id = "math:solver.compaction:" + logical_name
    if logical_id in selected["references"]:
        raise ValueError("math digest cannot cite itself")
    # This is a concept draft describing a solver approach. A compaction alone
    # supplies no proof review, verification event, or theorem certificate.
    entry = {"key": "solver-digest", "namespace": "solver.compaction",
             "logical_name": logical_name, "kind": "concept", "title": selected["title"],
             "statement": selected["statement"], "proof_text": None,
             "assumptions": selected["assumptions"], "domain": selected["domain"],
             "source": {"source_id": "solver-compaction:" + digest,
                        "url": "urn:math-solver-compaction:" + digest,
                        "revision": digest, "license": "owner-own-work (provisional machine digest)",
                        "excerpt": selected["statement"]}}
    bundle = {"spec": "mathbrain-draft-v1", "author_agent": selected["author_agent"],
              "bundle_slug": "solver-compaction-" + digest, "entries": [entry],
              "relations": [{"from_key": "solver-digest", "to_existing_logical": ref,
                             "kind": "cites", "rationale": "The selected solver digest cites this math card; audit the relation before ingestion."}
                            for ref in selected["references"]],
              "review_status": "pending", "node_status": "provisional",
              "solver_stage": {"schema": SCHEMA, "stage_id": selected["stage_id"],
                               "session_id": selected["session_id"],
                               "first_compaction": compaction_name, "digest_sha256": digest}}
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "bundle_solver_compaction_" + digest[:24] + ".json.pending")
    source = os.path.join(directory, "solver_digest_" + digest[:24] + ".md")
    source_text = ("# " + selected["title"] + "\n\nProvisional solver stage: " + selected["stage_id"] +
                   "\nDigest: " + digest + "\n\n" + selected["statement"] + "\n\nAssumptions:\n" +
                   "\n".join("- " + a for a in selected["assumptions"]) + "\n")
    # Repeated hooks/sweeps retain the first provenance. A torn/invalid pending
    # artifact is repaired atomically on the next replay.
    existing = None
    try:
        with open(path, encoding="utf-8") as fh:
            existing = json.load(fh)
    except (OSError, ValueError):
        pass
    if (isinstance(existing, dict) and isinstance(existing.get("solver_stage"), dict)
            and isinstance(existing["solver_stage"].get("first_compaction"), str)):
        comparable = dict(existing)
        comparable["solver_stage"] = dict(comparable.get("solver_stage") or {})
        comparable["solver_stage"]["first_compaction"] = compaction_name
        if comparable == bundle:
            bundle["solver_stage"]["first_compaction"] = existing["solver_stage"]["first_compaction"]
            try:
                with open(source, encoding="utf-8") as fh:
                    source_matches = fh.read() == source_text
            except OSError:
                source_matches = False
            if source_matches:
                return {"draft": path, "source_artifact": source, "logical_id": logical_id,
                        "digest_sha256": digest, "new": False}
    _publish(source, source_text)
    _publish(path, json.dumps(bundle, ensure_ascii=False, indent=2) + "\n")
    return {"draft": path, "source_artifact": source, "logical_id": logical_id,
            "digest_sha256": digest, "new": True}
