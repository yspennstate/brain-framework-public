"""Save an agent-authored manual compact and a linked, provisional brain anchor.

This command preserves context; it does not invoke native context reduction.
No model call, daemon, timer, transcript modification, or background process.
Default is a read-only plan. --save uses the caller's mesh identity and claims.
The full summary stays local; its searchable anchor contains metadata only.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import uuid

import compact_nodes as nodes

MAX_SUMMARY_BYTES = 256 * 1024
SLUG = re.compile(r"[A-Za-z0-9_.-]+\Z")
TASK = re.compile(r"T-\d{8}-\d+\Z")
POOL_ACTOR = re.compile(r"w[0-9]+\Z")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def bounded(path, limit):
    with Path(path).open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("input exceeds the bounded read limit")
    return raw


def manual_rows():
    path = Path(nodes.ROOT) / "MANUAL_INDEX.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in bounded(path, 32 * 1024 * 1024).splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("manual ledger is malformed; preserve it and repair before saving")
    return rows


def under(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def verify(row):
    node, anchor = Path(row["node"]), Path(row["anchor"])
    if (not under(node, Path(nodes.ROOT) / "manual") or not under(anchor, nodes.MEM)
            or node.name != row["name"] + ".md" or anchor.name != row["name"] + ".md"):
        raise ValueError("saved paths do not match their confined destinations")
    if sha(bounded(node, MAX_SUMMARY_BYTES)) != row["sha256"]:
        raise ValueError("saved summary hash mismatch")
    if sha(bounded(anchor, 64 * 1024)) != row["anchor_sha256"]:
        raise ValueError("saved anchor hash mismatch")
    if not Path(nodes.MEM, nodes.HUB + ".md").is_file():
        raise ValueError("compaction hub is missing; linked node is incomplete")
    return row


def render_anchor(row):
    """Fixed text plus validated metadata; never summary prose."""
    return "\n".join([
        "---", "name: " + row["name"],
        'description: "[PROVISIONAL] Manual context checkpoint for Codex session ' + row["session_id"][:8] + '"',
        "metadata:", "  type: project", "  status: provisional", "  node_kind: manual_compaction_checkpoint",
        "  runtime: codex", "  session_id: " + row["session_id"],
        "  agent: " + row["agent"],
        "  created_at: " + row["at"], "  full_summary: " + row["node"],
        "  summary_sha256: " + row["sha256"], "  native_context_reduced: false", "---", "",
        "FIRE WHEN resuming this session or recovering its manual compact.",
        "This is the agent's provisional recovery checkpoint. It is not proof that native context was reduced.",
        "Full summary stays local at the path above; verify its SHA-256 before relying on it.",
        "Tasks: " + (", ".join(row["tasks"]) or "none currently registered"), "", "Connected:",
        *["- [[" + slug + "]]" for slug in row["related"]], "",
    ]).encode("utf-8")


def recover_anchor(row):
    """The saved anchor is already a receipt; recover it before trusting a missing ledger."""
    original = bounded(row["anchor"], 64 * 1024)
    text = original.decode("utf-8")

    def field(key):
        values = re.findall(r"(?m)^\s*" + re.escape(key) + r": ([^\r\n]+)$", text)
        if len(values) != 1:
            raise ValueError("incomplete anchor receipt: " + key)
        return values[0]

    if (field("name"), field("session_id"), field("summary_sha256")) != (row["name"], row["session_id"], row["sha256"]):
        raise ValueError("existing anchor receipt belongs to different content")
    when = dt.datetime.fromisoformat(field("created_at"))
    if when.utcoffset() is None:
        raise ValueError("anchor receipt time has no timezone")
    task_text = field("Tasks")
    tasks = [] if task_text == "none currently registered" else task_text.split(", ")
    links = re.findall(r"(?m)^- \[\[([A-Za-z0-9_.-]+)\]\]$", text)
    if any(not TASK.fullmatch(t) for t in tasks) or len(set(tasks)) != len(tasks):
        raise ValueError("invalid task identifiers in anchor receipt")
    if not links or links[0] != nodes.HUB or len(set(links)) != len(links):
        raise ValueError("invalid graph links in anchor receipt")
    recovered = {**row, "at": when.isoformat(timespec="seconds"), "node": field("full_summary"),
                 "tasks": tasks, "related": links, "previous": None}
    previous = [r for r in nodes._index_rows() if r.get("name") in links and r.get("session_id") == row["session_id"]]
    if previous:
        recovered["previous"] = sorted(previous, key=lambda r: r.get("at", ""))[-1]["name"]
    # Exact reconstruction prevents a damaged receipt from importing arbitrary prose.
    if render_anchor(recovered) != original:
        raise ValueError("existing anchor differs from the fixed metadata-only format")
    recovered["anchor_sha256"] = sha(original)
    verify(recovered)
    return recovered, original


def validate_actor(agent, session_id, payload):
    """Use the existing mesh actor; a pool name alone proves no session binding."""
    if agent == "codex-" + session_id[:8]:
        return None
    if not POOL_ACTOR.fullmatch(agent):
        raise ValueError("agent must be this native session's mesh identity or a bound pool worker")
    pool = Path(nodes.HOME) / "brain_workers"
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or Path(cwd).resolve() != (pool / agent).resolve():
        raise ValueError("pool actor does not match native cwd")
    state = json.loads(bounded(pool / "state.json", 8 * 1024 * 1024))
    worker = state.get(agent) if isinstance(state, dict) else None
    if not isinstance(worker, dict) or worker.get("thread") != session_id:
        raise ValueError("pool actor is not bound to this current native thread")
    return agent


def prepare(summary_path, session_id, rollout_path, agent, when=None):
    session_id = str(uuid.UUID(session_id))
    rollout = Path(rollout_path).resolve()
    if not any(p.name.startswith(".codex") and under(rollout, p / "sessions") for p in rollout.parents):
        raise ValueError("rollout must belong to a native profile sessions directory")
    with rollout.open("rb") as handle:
        header = handle.readline(2 * 1024 * 1024 + 1)
    if len(header) > 2 * 1024 * 1024 or not header.endswith(b"\n"):
        raise ValueError("native session header is incomplete or oversized")
    record = json.loads(header)
    if record.get("type") != "session_meta" or (record.get("payload") or {}).get("id") != session_id:
        raise ValueError("native session identity mismatch")
    pool_actor = validate_actor(agent, session_id, record["payload"])
    raw = bounded(summary_path, MAX_SUMMARY_BYTES)
    summary = raw.decode("utf-8")
    if not summary.strip() or not re.search(r"(?mi)^\s*NEXT:\s*\S", summary):
        raise ValueError("manual summary must contain a concrete NEXT: line")
    digest = sha(raw)
    name = "manual_compaction_codex_" + session_id + "_" + digest[:16]
    matches = [r for r in manual_rows() if r.get("name") == name]
    if matches:
        if len(matches) != 1 or matches[0].get("sha256") != digest or matches[0].get("session_id") != session_id:
            raise ValueError("manual ledger identity collision or duplicate rows")
        return {"existing": verify(matches[0]), "agent": agent}
    now = when or dt.datetime.now().astimezone()
    if now.utcoffset() is None:
        raise ValueError("checkpoint time must carry a timezone")
    # Content identity, not today's date: a crash followed by a retry tomorrow
    # must find the same already-written full summary.
    node = Path(nodes.ROOT) / "manual" / session_id / (name + ".md")
    anchor = Path(nodes.MEM) / (name + ".md")
    if not Path(nodes.MEM, nodes.HUB + ".md").is_file():
        raise ValueError("compaction hub is missing")
    tasks = [t for t in nodes._open_tasks(session_id, "codex", extra_agent=pool_actor) if TASK.fullmatch(t)]
    links = [nodes.HUB]
    rows = nodes._index_rows()
    previous = [r for r in rows if r.get("session_id") == session_id and r.get("name") != name]
    previous.sort(key=lambda r: r.get("at", ""))
    peers = [r for r in rows if set(r.get("tasks") or []) & set(tasks) and r.get("session_id") != session_id]
    for r in previous[-1:] + peers[-5:]:
        slug = r.get("name", "")
        if SLUG.fullmatch(slug) and Path(nodes.MEM, slug + ".md").is_file() and slug not in links:
            links.append(slug)
    row = {"at": now.isoformat(timespec="seconds"), "name": name, "runtime": "codex",
           "session_id": session_id, "agent": agent, "trigger": "manual", "capture_kind": "manual_checkpoint",
           "native_context_reduced": False, "chars": len(summary), "bytes": len(raw), "sha256": digest,
           "anchor_privacy_sha256": nodes.manual_privacy_hash(summary),
           "node": node.as_posix(), "anchor": anchor.as_posix(),
           "tasks": tasks, "related": links, "previous": previous[-1].get("name") if previous else None,
           "source": str(Path(summary_path).resolve()), "native_rollout": rollout.as_posix(),
           "native_header_sha256": sha(header)}
    if anchor.exists():
        row, text = recover_anchor(row)
    else:
        if node.exists():
            if bounded(node, MAX_SUMMARY_BYTES) != raw:
                raise ValueError("existing full summary differs; preserve and investigate")
            # No anchor yet: its first actual write time is the available receipt.
            row["at"] = dt.datetime.fromtimestamp(node.stat().st_mtime, dt.timezone.utc).isoformat(timespec="seconds")
        text = render_anchor(row)
        row["anchor_sha256"] = sha(text)
    return {"row": row, "raw": raw, "anchor_bytes": text, "agent": agent}


@contextlib.contextmanager
def mesh_connection():
    """Use the canonical mesh API without launching three extra interpreters."""
    script = Path(nodes.HOME) / "brain-repo/12_cognitive_architecture/agent_mesh/agent_mesh.py"
    try:
        spec = importlib.util.spec_from_file_location("_codex_manual_compact_mesh", script)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        client = module.Mesh(module.DEFAULT_DB)
    except Exception as exc:
        raise RuntimeError("mesh initialization refused: " + str(exc)[-1600:]) from exc

    def fence(operation, agent, resources):
        try:
            if operation == "claim":
                return client.claim(agent, resources, mode="write", purpose="Save verified manual context checkpoint", ttl=120)
            if operation == "assert":
                return client.assert_claim(agent, resources)
            if operation == "release":
                return client.release(agent, resources)
            raise ValueError("unsupported mesh operation")
        except Exception as exc:
            raise RuntimeError("mesh " + operation + " refused: " + str(exc)[-1600:]) from exc

    try:
        yield fence
    finally:
        client.close()


def put_once(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if bounded(path, max(len(raw), 1)) != raw:
            raise ValueError("existing bytes differ; preserved without overwrite: " + str(path))


def recheck_pool_binding(row, agent):
    if POOL_ACTOR.fullmatch(agent):
        # A recycled worker label must not authorize a stale prepared write.
        with Path(row["native_rollout"]).open("rb") as handle:
            header = handle.readline(2 * 1024 * 1024 + 1)
        if sha(header) != row["native_header_sha256"]:
            raise ValueError("native pool header changed after preparation")
        validate_actor(agent, row["session_id"], json.loads(header)["payload"])


def save(plan, mesh=None):
    if "existing" in plan:
        return {"status": "ALREADY_SAVED", **verify(plan["existing"])}
    if mesh is None:
        with mesh_connection() as fence:
            return save(plan, mesh=fence)
    row = plan["row"]
    recheck_pool_binding(row, plan["agent"])
    ledger = Path(nodes.ROOT) / "MANUAL_INDEX.jsonl"
    resources = [row["node"], row["anchor"], ledger.as_posix()]
    mesh("claim", plan["agent"], resources)
    try:
        mesh("assert", plan["agent"], resources)
        recheck_pool_binding(row, plan["agent"])
        # A peer may have completed an identical request before this claim.
        matches = [r for r in manual_rows() if r.get("name") == row["name"]]
        if matches:
            if len(matches) != 1 or matches[0]["sha256"] != row["sha256"]:
                raise ValueError("concurrent ledger identity conflict")
            return {"status": "ALREADY_SAVED", **verify(matches[0])}
        put_once(row["node"], plan["raw"])
        put_once(row["anchor"], plan["anchor_bytes"])
        verify(row)
        encoded = (json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")
        with ledger.open("ab") as handle:
            if handle.write(encoded) != len(encoded):
                raise OSError("short ledger write")
            handle.flush()
            os.fsync(handle.fileno())
        matches = [r for r in manual_rows() if r.get("name") == row["name"]]
        if matches != [row]:
            raise ValueError("manual ledger readback differs")
        return {"status": "SAVED", **verify(row)}
    finally:
        mesh("release", plan["agent"], resources)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--rollout", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)
    plan = prepare(args.summary, args.session_id, args.rollout, args.agent)
    result = save(plan) if args.save else {"status": "PLAN", **plan.get("row", plan.get("existing"))}
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print("manual compact NOT VERIFIED: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
