#!/usr/bin/env python3
"""Local, read-only web server for the live brain network.

The viewer deliberately reads the deployed brain in place. It does not copy the
network, mutate memory, or install a background service. The only listening
socket is 127.0.0.1.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable


APP_ID = "brain-live"
APP_VERSION = "1.0.0"
DEFAULT_PORT = 8765
MAX_VISIBLE_NODES = 220
ACTIVATION_HALFLIFE_S = 1500.0
ACTIVITY_HALFLIFE_S = 90.0
LIVE_ACTIVITY_WINDOW_S = 600.0

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}

NODE_EXPLANATIONS = {
    "memory": "A durable memory: a named piece of knowledge stored in the brain.",
    "prompt": "An archived request or conversational moment retained as an episode.",
    "concept": "A recurring keyword or concept shared by many memories and prompts.",
    "tool": "A tool or action class used by an agent.",
    "reflex": "A scar-backed safety reflex that fired in response to the current work.",
    "agent": "A collaborator currently registered in the live agent mesh.",
    "session": "A live or recently active thinking session.",
    "file": "A real file observed in current tool activity or protected by a write claim.",
    "folder": "A directory currently protected or used by an agent.",
    "resource": "A logical or remote resource currently claimed by an agent.",
}

FILE_EXTENSIONS = (
    "pyw|py|md|jsonl|json|sqlite3|sqlite|db|tsv|csv|ps1|vbs|mjs|js|html|"
    "css|tex|txt|log|yaml|yml|toml|ini|cfg|lnk|pdf|docx|xlsx|pptx|sh|bat"
)
WINDOWS_FILE_RE = re.compile(
    rf"(?i)([a-z]:(?:\\\\|\\)[^\"'\t\r\n<>|;&]*?\.(?:{FILE_EXTENSIONS}))"
)
POSIX_WINDOWS_FILE_RE = re.compile(
    rf"(?i)(/[a-z]/users/[^/\"'\t\r\n<>|;&]+/[^\"'\t\r\n<>|;&]*?\.(?:{FILE_EXTENSIONS}))"
)
SAFE_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def iso_from_epoch(ts: float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(float(ts), timezone.utc).astimezone().isoformat(timespec="seconds")


def age_seconds(ts: float | None, now: float | None = None) -> float | None:
    if not ts:
        return None
    return max(0.0, (now or time.time()) - float(ts))


def decay(value: float, elapsed: float, half_life: float) -> float:
    if value <= 0:
        return 0.0
    return value * (0.5 ** (max(0.0, elapsed) / half_life))


def pretty_label(value: str, limit: int = 44) -> str:
    text = value.replace("_", " ").replace("-", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def read_tail_lines(path: Path, max_bytes: int = 512 * 1024) -> list[str]:
    """Read a bounded tail without loading multi-megabyte live ledgers."""
    try:
        size = path.stat().st_size
        start = max(0, size - max_bytes)
        with path.open("rb") as handle:
            handle.seek(start)
            data = handle.read()
        if start:
            first_newline = data.find(b"\n")
            data = data[first_newline + 1 :] if first_newline >= 0 else b""
        return data.decode("utf-8", "replace").splitlines()
    except (OSError, ValueError):
        return []


def normalize_observed_path(raw: str) -> str | None:
    candidate = raw.strip().rstrip(".,);]}")
    while "\\\\" in candidate:
        candidate = candidate.replace("\\\\", "\\")
    if re.match(r"(?i)^/[a-z]/users/", candidate):
        drive = candidate[1].upper()
        candidate = drive + ":\\" + candidate[3:].replace("/", os.sep)
    if not re.match(r"(?i)^[a-z]:\\", candidate):
        return None
    try:
        return str(Path(candidate))
    except (OSError, ValueError):
        return None


def extract_file_paths(text: str) -> list[str]:
    found: list[str] = []
    for match in WINDOWS_FILE_RE.finditer(text):
        path = normalize_observed_path(match.group(1))
        if path:
            found.append(path)
    for match in POSIX_WINDOWS_FILE_RE.finditer(text):
        path = normalize_observed_path(match.group(1))
        if path:
            found.append(path)
    # Preserve order while de-duplicating a command that mentions the same file twice.
    return list(dict.fromkeys(found))


@contextmanager
def sqlite_readonly(path: Path):
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.35)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        yield connection
    finally:
        connection.close()


@dataclass(frozen=True)
class BrainPaths:
    app_dir: Path
    network_db: Path
    memory_dir: Path
    spread_edges: Path
    activation_dir: Path
    fire_log: Path
    instincts_tsv: Path
    mesh_db: Path

    @classmethod
    def live(cls, app_dir: Path) -> "BrainPaths":
        home = Path.home()
        project_key = re.sub(r"[:\\/]", "-", str(home))
        dreaming = Path(
            os.environ.get("BRAIN_DREAMING_DIR", home / ".claude" / "dreaming")
        )
        return cls(
            app_dir=app_dir,
            network_db=Path(
                os.environ.get("BRAIN_NETWORK_DB", dreaming / "network" / "net.sqlite3")
            ),
            memory_dir=Path(
                os.environ.get(
                    "BRAIN_MEMORY_DIR",
                    home / ".claude" / "projects" / project_key / "memory",
                )
            ),
            spread_edges=Path(
                os.environ.get("BRAIN_SPREAD_EDGES", dreaming / "spread_edges.tsv")
            ),
            activation_dir=Path(
                os.environ.get("BRAIN_ACTIVATION_DIR", dreaming / "graph_fire")
            ),
            fire_log=Path(
                os.environ.get(
                    "BRAIN_FIRE_LOG", dreaming / "graph_fire" / "gf_shadow_fires.tsv"
                )
            ),
            instincts_tsv=Path(
                os.environ.get("BRAIN_INSTINCTS_TSV", dreaming / "instincts.tsv")
            ),
            mesh_db=Path(
                os.environ.get(
                    "BRAIN_MESH_DB",
                    home / ".agents" / "coordination" / "agent_mesh.sqlite3",
                )
            ),
        )


class GraphBuilder:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add_node(self, node_id: str, node_type: str, label: str, **attrs: Any) -> None:
        if not node_id:
            return
        current = self.nodes.get(node_id)
        if current is None:
            current = {
                "id": node_id,
                "type": node_type,
                "label": label or pretty_label(node_id.split(":", 1)[-1]),
                "activation": 0.0,
                "direct": False,
                "hot": False,
            }
            self.nodes[node_id] = current
        if attrs.get("activation") is not None:
            current["activation"] = max(
                float(current.get("activation") or 0.0),
                float(attrs.get("activation") or 0.0),
            )
        current["direct"] = bool(current.get("direct") or attrs.pop("direct", False))
        current["hot"] = bool(current.get("hot") or attrs.pop("hot", False))
        for key, value in attrs.items():
            if value is None:
                continue
            if key == "sessions":
                old = set(current.get("sessions") or [])
                old.update(value)
                current["sessions"] = sorted(old)
            elif key not in current or current[key] in (None, "", [], {}):
                current[key] = value
            elif key in {"age_s", "last_seen_epoch"}:
                current[key] = min(float(current[key]), float(value))
            elif key == "raw_activation":
                current[key] = max(float(current[key]), float(value))

    def add_edge(
        self,
        source: str,
        target: str,
        relation: str,
        weight: float = 1.0,
        flow: float = 0.0,
        direct: bool = False,
    ) -> None:
        if not source or not target or source == target:
            return
        key = (source, target, relation)
        current = self.edges.get(key)
        if current is None:
            current = {
                "id": f"{source}|{relation}|{target}",
                "source": source,
                "target": target,
                "relation": relation,
                "weight": round(float(weight), 4),
                "flow": round(float(flow), 4),
                "direct": bool(direct),
            }
            self.edges[key] = current
        else:
            current["weight"] = max(float(current["weight"]), float(weight))
            current["flow"] = max(float(current["flow"]), float(flow))
            current["direct"] = bool(current["direct"] or direct)

    def merge(self, other: "GraphBuilder") -> None:
        for node in other.nodes.values():
            attrs = dict(node)
            node_id = attrs.pop("id")
            node_type = attrs.pop("type")
            label = attrs.pop("label")
            self.add_node(node_id, node_type, label, **attrs)
        for edge in other.edges.values():
            self.add_edge(
                edge["source"],
                edge["target"],
                edge["relation"],
                edge.get("weight", 1.0),
                edge.get("flow", 0.0),
                edge.get("direct", False),
            )

    def finalize(self, max_nodes: int = MAX_VISIBLE_NODES) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if len(self.nodes) > max_nodes:
            degree: defaultdict[str, int] = defaultdict(int)
            for edge in self.edges.values():
                degree[edge["source"]] += 1
                degree[edge["target"]] += 1

            def priority(node: dict[str, Any]) -> tuple[float, float, int, str]:
                fixed = 4.0 if node.get("hot") else 0.0
                fixed += 2.0 if node.get("direct") else 0.0
                fixed += 1.0 if node.get("selected") else 0.0
                return (
                    fixed,
                    float(node.get("activation") or 0.0),
                    degree[node["id"]],
                    node["id"],
                )

            keep = {
                node["id"]
                for node in sorted(self.nodes.values(), key=priority, reverse=True)[:max_nodes]
            }
            self.nodes = {node_id: node for node_id, node in self.nodes.items() if node_id in keep}
            self.edges = {
                key: edge
                for key, edge in self.edges.items()
                if edge["source"] in keep and edge["target"] in keep
            }
        nodes = sorted(
            self.nodes.values(),
            key=lambda node: (
                not bool(node.get("hot")),
                not bool(node.get("direct")),
                -float(node.get("activation") or 0.0),
                node["label"].lower(),
            ),
        )
        edges = list(self.edges.values())
        return nodes, edges


class LiveBrain:
    def __init__(self, paths: BrainPaths) -> None:
        self.paths = paths
        self._cache_lock = threading.RLock()
        self._spread_mtime = -1.0
        self._spread_adj: dict[str, list[tuple[str, float]]] = {}
        self._spread_degree: dict[str, float] = {}
        self._instincts_mtime = -1.0
        self._instincts: dict[str, dict[str, str]] = {}
        self._memory_files_mtime = -1.0
        self._memory_slugs: list[str] = []
        self._stats_at = 0.0
        self._stats: dict[str, Any] = {}
        self._details: dict[str, dict[str, Any]] = {}
        self._last_good_state: dict[str, Any] | None = None
        self._state_build_lock = threading.Lock()
        self._state_cache: dict[tuple[str, str, str, str], tuple[float, dict[str, Any]]] = {}

    # ---------- source loading ----------

    def _load_spread_graph(self) -> tuple[dict[str, list[tuple[str, float]]], dict[str, float]]:
        try:
            mtime = self.paths.spread_edges.stat().st_mtime
        except OSError:
            return {}, {}
        with self._cache_lock:
            if mtime == self._spread_mtime:
                return self._spread_adj, self._spread_degree
            adjacency: defaultdict[str, list[tuple[str, float]]] = defaultdict(list)
            degree: defaultdict[str, float] = defaultdict(float)
            try:
                with self.paths.spread_edges.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                    reader = csv.DictReader(handle, delimiter="\t")
                    for row in reader:
                        left = (row.get("a") or "").strip()
                        right = (row.get("b") or "").strip()
                        if not left or not right or left == right:
                            continue
                        try:
                            weight = max(0.0, float(row.get("weight") or 0.0))
                        except ValueError:
                            continue
                        adjacency[left].append((right, weight))
                        adjacency[right].append((left, weight))
                        degree[left] += weight
                        degree[right] += weight
            except OSError:
                return self._spread_adj, self._spread_degree
            for slug in adjacency:
                adjacency[slug].sort(key=lambda item: item[1], reverse=True)
                adjacency[slug] = adjacency[slug][:48]
            self._spread_adj = dict(adjacency)
            self._spread_degree = dict(degree)
            self._spread_mtime = mtime
            return self._spread_adj, self._spread_degree

    def _load_instincts(self) -> dict[str, dict[str, str]]:
        try:
            mtime = self.paths.instincts_tsv.stat().st_mtime
        except OSError:
            return {}
        with self._cache_lock:
            if mtime == self._instincts_mtime:
                return self._instincts
            instincts: dict[str, dict[str, str]] = {}
            try:
                with self.paths.instincts_tsv.open(
                    "r", encoding="utf-8", errors="replace", newline=""
                ) as handle:
                    for row in csv.DictReader(handle, delimiter="\t"):
                        reflex_id = (row.get("id") or "").strip()
                        if reflex_id:
                            instincts[reflex_id] = {
                                "mode": row.get("mode") or "",
                                "validity": row.get("validity") or "",
                                "meaning": row.get("gut") or "",
                                "scar": row.get("sting") or "",
                                "source": row.get("source") or "",
                                "charge": row.get("charge") or "",
                            }
            except OSError:
                return self._instincts
            self._instincts = instincts
            self._instincts_mtime = mtime
            return self._instincts

    def _load_memory_slugs(self) -> list[str]:
        try:
            mtime = self.paths.memory_dir.stat().st_mtime
        except OSError:
            return []
        with self._cache_lock:
            if mtime == self._memory_files_mtime and self._memory_slugs:
                return self._memory_slugs
            try:
                slugs = sorted(
                    path.stem
                    for path in self.paths.memory_dir.glob("*.md")
                    if path.is_file()
                )
            except OSError:
                return self._memory_slugs
            self._memory_slugs = slugs
            self._memory_files_mtime = mtime
            return slugs

    def network_stats(self) -> dict[str, Any]:
        now = time.time()
        with self._cache_lock:
            if self._stats and now - self._stats_at < 15.0:
                return dict(self._stats)
        stats: dict[str, Any] = {
            "node_count": 0,
            "edge_count": 0,
            "episode_count": 0,
            "node_kinds": {},
            "edge_kinds": {},
            "network_updated": None,
        }
        try:
            with sqlite_readonly(self.paths.network_db) as connection:
                kinds = connection.execute(
                    "SELECT kind, COUNT(*) AS n FROM nodes GROUP BY kind"
                ).fetchall()
                edge_kinds = connection.execute(
                    "SELECT kind, COUNT(*) AS n FROM edges GROUP BY kind"
                ).fetchall()
                episode = connection.execute(
                    "SELECT COUNT(*) AS n, MAX(ts) AS latest FROM episodes"
                ).fetchone()
                stats["node_kinds"] = {row["kind"]: row["n"] for row in kinds}
                stats["edge_kinds"] = {row["kind"]: row["n"] for row in edge_kinds}
                stats["node_count"] = sum(stats["node_kinds"].values())
                stats["edge_count"] = sum(stats["edge_kinds"].values())
                stats["episode_count"] = int(episode["n"] if episode else 0)
                stats["latest_episode"] = iso_from_epoch(
                    float(episode["latest"]) if episode and episode["latest"] else None
                )
                stats["network_updated"] = iso_from_epoch(self.paths.network_db.stat().st_mtime)
        except (OSError, sqlite3.Error, ValueError):
            pass
        with self._cache_lock:
            self._stats = stats
            self._stats_at = now
        return dict(stats)

    # ---------- live state ----------

    def collect_agents(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        agents: list[dict[str, Any]] = []
        claims: list[dict[str, Any]] = []
        if not self.paths.mesh_db.exists():
            return agents, claims
        now_iso = utc_now_iso()
        try:
            with sqlite_readonly(self.paths.mesh_db) as connection:
                agents = [
                    dict(row)
                    for row in connection.execute(
                        """
                        SELECT agent_id, runtime, session_key, parent_agent, cwd, task,
                               status, started_at, last_seen, lease_until
                        FROM agents
                        WHERE lease_until > ? AND status IN ('active','working','monitoring','blocked')
                        ORDER BY last_seen DESC
                        """,
                        (now_iso,),
                    ).fetchall()
                ]
                claims = [
                    dict(row)
                    for row in connection.execute(
                        """
                        SELECT claim_id, agent_id, resource, resource_kind, mode, purpose,
                               acquired_at, renewed_at, expires_at
                        FROM claims
                        WHERE released_at IS NULL AND expires_at > ?
                        ORDER BY renewed_at DESC
                        """,
                        (now_iso,),
                    ).fetchall()
                ]
        except sqlite3.Error:
            return [], []
        return agents, claims

    def collect_activations(
        self, selected_session: str = "all"
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        now = time.time()
        aggregate: dict[str, dict[str, Any]] = {}
        sessions: list[dict[str, Any]] = []
        try:
            files = list(self.paths.activation_dir.glob(".abuf_*.json"))
        except OSError:
            return aggregate, sessions
        for path in files:
            try:
                mtime = path.stat().st_mtime
                if now - mtime > 3 * 3600:
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                stored_ts = float(payload.get("ts") or mtime)
                elapsed = max(0.0, now - stored_ts)
                session_id = path.name[len(".abuf_") : -len(".json")]
                values = {
                    slug: decay(float(value), elapsed, ACTIVATION_HALFLIFE_S)
                    for slug, value in (payload.get("nodes") or {}).items()
                    if float(value) > 0
                }
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            values = {slug: value for slug, value in values.items() if value >= 0.02}
            sessions.append(
                {
                    "id": session_id,
                    "label": session_id[:12],
                    "age_s": round(now - stored_ts, 1),
                    "node_count": len(values),
                    "danger": round(
                        decay(
                            float(payload.get("danger") or 0.0),
                            elapsed,
                            1200.0,
                        ),
                        4,
                    ),
                }
            )
            if selected_session not in ("", "all") and not (
                session_id == selected_session
                or session_id.startswith(selected_session)
                or selected_session.startswith(session_id[:12])
            ):
                continue
            for slug, value in values.items():
                item = aggregate.setdefault(
                    slug, {"raw": 0.0, "sessions": [], "direct": True}
                )
                item["raw"] = max(float(item["raw"]), value)
                item["sessions"].append(session_id)
        sessions.sort(key=lambda item: (item["age_s"], item["id"]))
        return aggregate, sessions

    def collect_recent_activity(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        now = time.time()
        file_events: dict[tuple[str, str], dict[str, Any]] = {}
        reflex_events: dict[tuple[str, str], dict[str, Any]] = {}
        for line in read_tail_lines(self.paths.fire_log):
            parts = line.split("\t", 3)
            if len(parts) < 4:
                continue
            try:
                ts = float(parts[0])
            except ValueError:
                continue
            age = max(0.0, now - ts)
            if age > LIVE_ACTIVITY_WINDOW_S:
                continue
            session = parts[1].strip() or "unknown"
            reflexes = [
                item.strip()
                for item in parts[2].split(",")
                if item.strip() and item.strip() != "-"
            ]
            payload = parts[3]
            strength = min(1.0, max(0.08, decay(1.0, age, ACTIVITY_HALFLIFE_S)))
            operation = "used"
            lower = payload.lower()
            if "update file" in lower or "apply_patch" in lower or "old_string" in lower:
                operation = "write"
            elif "get-content" in lower or "read" in lower or "open" in lower:
                operation = "read"
            for reflex in reflexes:
                key = (session, reflex)
                existing = reflex_events.get(key)
                if existing is None or ts > existing["ts"]:
                    reflex_events[key] = {
                        "session": session,
                        "reflex": reflex,
                        "ts": ts,
                        "age_s": round(age, 1),
                        "strength": round(strength, 4),
                    }
            for file_path in extract_file_paths(payload):
                key = (session, file_path.lower())
                existing = file_events.get(key)
                if existing is None or ts > existing["ts"]:
                    file_events[key] = {
                        "session": session,
                        "path": file_path,
                        "ts": ts,
                        "age_s": round(age, 1),
                        "strength": round(strength, 4),
                        "operation": operation,
                        "reflexes": reflexes[:6],
                    }
        return (
            sorted(file_events.values(), key=lambda item: item["ts"], reverse=True),
            sorted(reflex_events.values(), key=lambda item: item["ts"], reverse=True),
        )

    @staticmethod
    def match_agent(session: str, agents: Iterable[dict[str, Any]]) -> str | None:
        session_l = session.lower()
        prefix = session_l[:8]
        for agent in agents:
            agent_id = str(agent.get("agent_id") or "")
            session_key = str(agent.get("session_key") or "")
            if prefix and (prefix in agent_id.lower() or session_key.lower().startswith(prefix)):
                return agent_id
        return None

    def build_runtime_graph(
        self,
        agents: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        file_events: list[dict[str, Any]],
        reflex_events: list[dict[str, Any]],
    ) -> tuple[GraphBuilder, list[dict[str, Any]], int]:
        graph = GraphBuilder()
        feed: list[dict[str, Any]] = []
        details: dict[str, dict[str, Any]] = {}
        instincts = self._load_instincts()
        now = time.time()

        for agent in agents:
            node_id = f"agent:{agent['agent_id']}"
            graph.add_node(
                node_id,
                "agent",
                pretty_label(agent["agent_id"], 32),
                activation=0.75,
                direct=True,
                hot=True,
                runtime=agent.get("runtime"),
                task=agent.get("task") or "",
                status=agent.get("status"),
                last_seen=agent.get("last_seen"),
            )
            details[node_id] = {
                **graph.nodes[node_id],
                "meaning": NODE_EXPLANATIONS["agent"],
                "task": agent.get("task") or "",
                "working_directory": agent.get("cwd") or "",
                "started_at": agent.get("started_at"),
                "last_seen": agent.get("last_seen"),
                "lease_until": agent.get("lease_until"),
            }

        claimed_paths: set[str] = set()
        claimed_file_paths: set[str] = set()
        for claim in claims:
            resource = str(claim.get("resource") or "")
            is_path = claim.get("resource_kind") == "path" or bool(
                re.match(r"(?i)^[a-z]:\\", resource)
            )
            if is_path:
                path = Path(resource)
                node_type = "file" if path.suffix else "folder"
                label = path.name or resource
                node_id = f"{node_type}:{resource}"
                claimed_paths.add(resource.lower())
                if node_type == "file":
                    claimed_file_paths.add(resource.lower())
            else:
                node_type = "resource"
                label = resource.split("/", 1)[-1]
                node_id = f"resource:{resource}"
            graph.add_node(
                node_id,
                node_type,
                pretty_label(label, 38),
                activation=1.0,
                direct=True,
                hot=True,
                path=resource if is_path else None,
                activity="claimed",
                mode=claim.get("mode"),
                purpose=claim.get("purpose") or "",
                agent_id=claim.get("agent_id"),
            )
            agent_id = f"agent:{claim['agent_id']}"
            if agent_id not in graph.nodes:
                graph.add_node(agent_id, "agent", pretty_label(claim["agent_id"], 32), activation=0.5)
            graph.add_edge(agent_id, node_id, f"{claim.get('mode') or 'active'} claim", 1.0, 1.0, True)
            details[node_id] = {
                **graph.nodes[node_id],
                "meaning": NODE_EXPLANATIONS[node_type],
                "claim_id": claim.get("claim_id"),
                "claimed_by": claim.get("agent_id"),
                "purpose": claim.get("purpose") or "",
                "acquired_at": claim.get("acquired_at"),
                "renewed_at": claim.get("renewed_at"),
                "expires_at": claim.get("expires_at"),
            }
            feed.append(
                {
                    "ts": claim.get("renewed_at") or claim.get("acquired_at"),
                    "epoch": now,
                    "type": "claim",
                    "label": f"{claim.get('agent_id')} · {claim.get('mode')} · {pretty_label(label, 48)}",
                    "node_id": node_id,
                }
            )

        reflex_by_session: defaultdict[str, list[str]] = defaultdict(list)
        for event in reflex_events:
            reflex_by_session[event["session"]].append(event["reflex"])
            reflex_id = f"reflex:{event['reflex']}"
            info = instincts.get(event["reflex"], {})
            graph.add_node(
                reflex_id,
                "reflex",
                pretty_label(event["reflex"], 34),
                activation=event["strength"],
                direct=True,
                hot=event["age_s"] <= 120,
                age_s=event["age_s"],
                meaning=info.get("meaning") or "",
                validity=info.get("validity") or "",
            )
            agent_name = self.match_agent(event["session"], agents)
            source_id = (
                f"agent:{agent_name}" if agent_name else f"session:{event['session']}"
            )
            if source_id not in graph.nodes:
                graph.add_node(
                    source_id,
                    "session",
                    f"session {event['session'][:12]}",
                    activation=event["strength"],
                    direct=True,
                    hot=event["age_s"] <= 120,
                    age_s=event["age_s"],
                )
            graph.add_edge(
                source_id,
                reflex_id,
                "fired",
                event["strength"],
                event["strength"],
                True,
            )
            anchor = info.get("source") or ""
            if anchor:
                memory_id = f"mem:{anchor}"
                graph.add_node(
                    memory_id,
                    "memory",
                    pretty_label(anchor),
                    activation=min(0.65, event["strength"] * 0.65),
                    sessions=[event["session"]],
                    anchor=True,
                )
                graph.add_edge(
                    reflex_id,
                    memory_id,
                    "scar anchor",
                    float(info.get("charge") or 0.5),
                    event["strength"] * 0.55,
                )
            details[reflex_id] = {
                **graph.nodes[reflex_id],
                "meaning": info.get("meaning") or NODE_EXPLANATIONS["reflex"],
                "scar": info.get("scar") or "",
                "source_memory": anchor,
                "mode": info.get("mode") or "",
                "validity": info.get("validity") or "",
                "last_fired": iso_from_epoch(event["ts"]),
            }

        active_files: set[str] = set()
        for event in file_events:
            path = event["path"]
            node_id = f"file:{path}"
            hot = event["age_s"] <= 120 or path.lower() in claimed_paths
            if hot:
                active_files.add(path.lower())
            graph.add_node(
                node_id,
                "file",
                pretty_label(Path(path).name or path, 38),
                activation=event["strength"],
                direct=True,
                hot=hot,
                path=path,
                operation=event["operation"],
                age_s=event["age_s"],
                sessions=[event["session"]],
                activity="current tool use" if hot else "recent tool use",
            )
            agent_name = self.match_agent(event["session"], agents)
            source_id = (
                f"agent:{agent_name}" if agent_name else f"session:{event['session']}"
            )
            if source_id not in graph.nodes:
                graph.add_node(
                    source_id,
                    "session",
                    f"session {event['session'][:12]}",
                    activation=event["strength"],
                    direct=True,
                    hot=event["age_s"] <= 120,
                    age_s=event["age_s"],
                )
            graph.add_edge(
                source_id,
                node_id,
                event["operation"],
                event["strength"],
                event["strength"],
                True,
            )
            for reflex in event["reflexes"][:3]:
                reflex_id = f"reflex:{reflex}"
                if reflex_id in graph.nodes:
                    graph.add_edge(
                        reflex_id,
                        node_id,
                        "guarded activity",
                        event["strength"],
                        event["strength"] * 0.8,
                    )
            details[node_id] = {
                **graph.nodes[node_id],
                "meaning": NODE_EXPLANATIONS["file"],
                "last_used": iso_from_epoch(event["ts"]),
                "observed_session": event["session"],
                "reflexes": event["reflexes"],
            }
            feed.append(
                {
                    "ts": iso_from_epoch(event["ts"]),
                    "epoch": event["ts"],
                    "type": "file",
                    "label": (
                        f"{event['operation']} · {Path(path).name}"
                        + (f" · {event['reflexes'][0]}" if event["reflexes"] else "")
                    ),
                    "node_id": node_id,
                }
            )

        feed.sort(key=lambda item: float(item.get("epoch") or 0), reverse=True)
        with self._cache_lock:
            self._details.update(details)
        return graph, feed[:18], len(active_files | claimed_file_paths)

    # ---------- stored graph views ----------

    def _memory_search(self, query: str, limit: int = 16) -> list[str]:
        query = query.strip().lower()
        if not query:
            return []
        terms = [term for term in re.split(r"\s+", query) if term]
        ranked: list[tuple[int, int, str]] = []
        for slug in self._load_memory_slugs():
            text = slug.lower().replace("_", " ")
            hits = sum(term in text for term in terms)
            if hits:
                starts = int(text.startswith(terms[0]))
                ranked.append((hits, starts, slug))
        ranked.sort(key=lambda item: (-item[0], -item[1], len(item[2]), item[2]))
        return [item[2] for item in ranked[:limit]]

    def build_memory_graph(
        self,
        activations: dict[str, dict[str, Any]],
        layer: str,
        query: str,
        focus: str,
    ) -> GraphBuilder:
        adjacency, degree = self._load_spread_graph()
        graph = GraphBuilder()
        active_ranked = sorted(
            activations.items(), key=lambda item: float(item[1]["raw"]), reverse=True
        )
        seed_strength: dict[str, float] = {
            slug: min(1.0, float(info["raw"]) / 6.0)
            for slug, info in active_ranked[:36]
        }
        direct_slugs = set(seed_strength)

        search_slugs = self._memory_search(query)
        for slug in search_slugs:
            seed_strength[slug] = max(seed_strength.get(slug, 0.0), 0.82)
            direct_slugs.add(slug)

        focus_slug = focus.split(":", 1)[1] if focus.startswith("mem:") else ""
        if focus_slug and SAFE_SLUG_RE.fullmatch(focus_slug):
            seed_strength[focus_slug] = max(seed_strength.get(focus_slug, 0.0), 1.0)
            direct_slugs.add(focus_slug)

        if layer == "memory" and not seed_strength:
            for slug, _ in sorted(degree.items(), key=lambda item: item[1], reverse=True)[:30]:
                seed_strength[slug] = 0.12
        elif layer == "live" and not seed_strength:
            for slug, _ in sorted(degree.items(), key=lambda item: item[1], reverse=True)[:10]:
                seed_strength[slug] = 0.08

        max_seeds = 48 if layer == "memory" else 34
        ranked_seeds = sorted(
            seed_strength.items(), key=lambda item: item[1], reverse=True
        )[:max_seeds]
        visible: set[str] = {slug for slug, _ in ranked_seeds}
        edge_candidates: dict[tuple[str, str], float] = {}

        hops = 2 if layer == "memory" and (query or focus_slug) else 1
        frontier = list(visible)
        per_node = 7 if layer == "memory" else 5
        for hop in range(hops):
            next_frontier: list[str] = []
            for slug in frontier[:80]:
                neighbors = adjacency.get(slug, [])[:per_node]
                total = sum(weight for _, weight in neighbors) or 1.0
                source_strength = seed_strength.get(slug, 0.06 if hop else 0.0)
                for neighbor, weight in neighbors:
                    if len(visible) >= (190 if layer == "memory" else 125):
                        break
                    visible.add(neighbor)
                    next_frontier.append(neighbor)
                    propagated = source_strength * 0.35 * weight / total
                    seed_strength[neighbor] = max(
                        seed_strength.get(neighbor, 0.0), propagated
                    )
                    edge_candidates[tuple(sorted((slug, neighbor)))] = max(
                        edge_candidates.get(tuple(sorted((slug, neighbor))), 0.0),
                        weight,
                    )
            frontier = next_frontier

        for slug in visible:
            info = activations.get(slug, {})
            raw = float(info.get("raw") or 0.0)
            strength = max(seed_strength.get(slug, 0.0), min(1.0, raw / 6.0))
            graph.add_node(
                f"mem:{slug}",
                "memory",
                pretty_label(slug),
                activation=strength,
                raw_activation=raw,
                direct=slug in direct_slugs,
                hot=raw >= 1.0,
                sessions=info.get("sessions") or [],
                selected=slug == focus_slug,
                degree=round(degree.get(slug, 0.0), 4),
            )

        for (left, right), weight in edge_candidates.items():
            left_strength = float(graph.nodes.get(f"mem:{left}", {}).get("activation") or 0.0)
            right_strength = float(graph.nodes.get(f"mem:{right}", {}).get("activation") or 0.0)
            if right_strength > left_strength:
                left, right = right, left
                left_strength, right_strength = right_strength, left_strength
            flow = min(1.0, left_strength * 0.8 + right_strength * 0.2)
            graph.add_edge(
                f"mem:{left}",
                f"mem:{right}",
                "memory association",
                weight,
                flow,
                left in direct_slugs and right in direct_slugs,
            )
        return graph

    def _association_rows_for_search(self, query: str, limit: int = 18) -> list[sqlite3.Row]:
        if not query.strip() or not self.paths.network_db.exists():
            return []
        like = f"%{query.strip().lower()}%"
        try:
            with sqlite_readonly(self.paths.network_db) as connection:
                return connection.execute(
                    """
                    SELECT n.id, n.kind, n.label, n.df, n.idf
                    FROM nodes n
                    LEFT JOIN node_content c ON c.id = n.id
                    WHERE LOWER(n.id) LIKE ? OR LOWER(COALESCE(n.label,'')) LIKE ?
                       OR LOWER(COALESCE(c.content,'')) LIKE ?
                    ORDER BY n.df DESC, n.id
                    LIMIT ?
                    """,
                    (like, like, like, limit),
                ).fetchall()
        except sqlite3.Error:
            return []

    def build_association_graph(
        self,
        activations: dict[str, dict[str, Any]],
        query: str,
        focus: str,
    ) -> GraphBuilder:
        graph = GraphBuilder()
        if not self.paths.network_db.exists():
            return graph
        seed_ids: list[str] = []
        for slug, _ in sorted(
            activations.items(), key=lambda item: float(item[1]["raw"]), reverse=True
        )[:24]:
            seed_ids.append(f"mem:{slug}")
        seed_ids.extend(row["id"] for row in self._association_rows_for_search(query))
        if focus and ":" in focus and not focus.startswith(
            ("agent:", "session:", "file:", "folder:", "resource:", "reflex:")
        ):
            seed_ids.append(focus)
        seed_ids = list(dict.fromkeys(seed_ids))
        try:
            with sqlite_readonly(self.paths.network_db) as connection:
                if not seed_ids:
                    for kind, count in (("mem", 14), ("tool", 10), ("kw", 18)):
                        seed_ids.extend(
                            row["id"]
                            for row in connection.execute(
                                "SELECT id FROM nodes WHERE kind=? ORDER BY df DESC LIMIT ?",
                                (kind, count),
                            ).fetchall()
                        )
                seed_ids = seed_ids[:52]
                node_ids = set(seed_ids)
                edge_rows: list[sqlite3.Row] = []
                for node_id in seed_ids:
                    rows = connection.execute(
                        """
                        SELECT src, dst, kind, w, n
                        FROM edges
                        WHERE src=?
                        ORDER BY CASE kind WHEN 'co' THEN 0 ELSE 1 END, w DESC
                        LIMIT 7
                        """,
                        (node_id,),
                    ).fetchall()
                    edge_rows.extend(rows)
                    for row in rows:
                        if len(node_ids) < 190:
                            node_ids.add(row["dst"])
                if not node_ids:
                    return graph
                placeholders = ",".join("?" for _ in node_ids)
                rows = connection.execute(
                    f"SELECT id, kind, label, df, idf FROM nodes WHERE id IN ({placeholders})",
                    tuple(node_ids),
                ).fetchall()
                known = {row["id"]: row for row in rows}
                for row in rows:
                    node_id = row["id"]
                    kind = row["kind"]
                    node_type = {
                        "mem": "memory",
                        "kw": "concept",
                        "prompt": "prompt",
                        "tool": "tool",
                    }.get(kind, kind)
                    suffix = node_id.split(":", 1)[-1]
                    info = activations.get(suffix, {}) if kind == "mem" else {}
                    raw = float(info.get("raw") or 0.0)
                    graph.add_node(
                        node_id,
                        node_type,
                        pretty_label(row["label"] or suffix),
                        activation=min(1.0, raw / 6.0),
                        raw_activation=raw,
                        direct=node_id in seed_ids,
                        hot=raw >= 1.0,
                        sessions=info.get("sessions") or [],
                        selected=node_id == focus,
                        frequency=int(row["df"] or 0),
                        specificity=round(float(row["idf"] or 0.0), 4),
                    )
                for row in edge_rows:
                    if row["src"] not in known or row["dst"] not in known:
                        continue
                    source_activation = float(
                        graph.nodes[row["src"]].get("activation") or 0.0
                    )
                    flow = min(1.0, source_activation * 0.9)
                    graph.add_edge(
                        row["src"],
                        row["dst"],
                        "co-activation" if row["kind"] == "co" else "sequence",
                        float(row["w"] or row["n"] or 0.0),
                        flow,
                        row["src"] in seed_ids,
                    )
        except sqlite3.Error:
            return graph
        return graph

    # ---------- public API ----------

    def source_status(self) -> list[dict[str, Any]]:
        sources = [
            ("association network", self.paths.network_db),
            ("live activation", self.paths.activation_dir),
            ("file/reflex activity", self.paths.fire_log),
            ("agent mesh", self.paths.mesh_db),
            ("memory links", self.paths.spread_edges),
        ]
        now = time.time()
        output = []
        for name, path in sources:
            try:
                if name == "live activation" and path.is_dir():
                    mtimes = [
                        item.stat().st_mtime
                        for item in path.glob(".abuf_*.json")
                        if item.is_file()
                    ]
                    mtime = max(mtimes) if mtimes else path.stat().st_mtime
                else:
                    mtime = path.stat().st_mtime
                output.append(
                    {
                        "name": name,
                        "ok": True,
                        "updated": iso_from_epoch(mtime),
                        "age_s": round(max(0.0, now - mtime), 1),
                    }
                )
            except OSError:
                output.append({"name": name, "ok": False, "updated": None, "age_s": None})
        return output

    def build_state(
        self,
        layer: str = "live",
        selected_session: str = "all",
        query: str = "",
        focus: str = "",
    ) -> dict[str, Any]:
        key = (layer, selected_session, query.strip()[:120], focus.strip()[:500])
        now = time.monotonic()
        with self._state_build_lock:
            cached = self._state_cache.get(key)
            if cached and now - cached[0] < 2.0:
                return cached[1]
            state = self._build_state_uncached(
                layer=layer,
                selected_session=selected_session,
                query=query,
                focus=focus,
            )
            self._state_cache[key] = (time.monotonic(), state)
            if len(self._state_cache) > 32:
                oldest = sorted(
                    self._state_cache.items(), key=lambda item: item[1][0]
                )[:8]
                for old_key, _ in oldest:
                    self._state_cache.pop(old_key, None)
            return state

    def _build_state_uncached(
        self,
        layer: str = "live",
        selected_session: str = "all",
        query: str = "",
        focus: str = "",
    ) -> dict[str, Any]:
        if layer not in {"live", "memory", "association"}:
            layer = "live"
        query = query.strip()[:120]
        focus = focus.strip()[:500]
        try:
            activations, sessions = self.collect_activations(selected_session)
            agents, claims = self.collect_agents()
            file_events, reflex_events = self.collect_recent_activity()
            runtime_graph, feed, active_file_count = self.build_runtime_graph(
                agents, claims, file_events, reflex_events
            )
            if layer == "association":
                graph = self.build_association_graph(activations, query, focus)
            else:
                graph = self.build_memory_graph(activations, layer, query, focus)
            if layer == "live":
                graph.merge(runtime_graph)
            nodes, edges = graph.finalize()
            stats = self.network_stats()
            source_status = self.source_status()
            freshest = min(
                (
                    source["age_s"]
                    for source in source_status
                    if source["ok"] and source["age_s"] is not None
                ),
                default=None,
            )
            hot_memories = sum(
                1 for info in activations.values() if float(info.get("raw") or 0.0) >= 0.2
            )
            metrics = {
                **stats,
                "visible_nodes": len(nodes),
                "visible_edges": len(edges),
                "active_memories": hot_memories,
                "active_files": active_file_count,
                "live_agents": len(agents),
                "live_sessions": len(sessions),
                "freshest_age_s": freshest,
            }
            warnings = [
                f"{source['name']} source is unavailable"
                for source in source_status
                if not source["ok"]
            ]
            state = {
                "app": APP_ID,
                "version": APP_VERSION,
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "layer": layer,
                "query": query,
                "focus": focus,
                "metrics": metrics,
                "sources": source_status,
                "warnings": warnings,
                "sessions": sessions,
                "nodes": nodes,
                "edges": edges,
                "activity": feed,
            }
            with self._cache_lock:
                for node in nodes:
                    self._details.setdefault(node["id"], dict(node))
                self._last_good_state = state
            return state
        except Exception as exc:  # A viewer must not take the brain down with it.
            with self._cache_lock:
                if self._last_good_state:
                    stale = dict(self._last_good_state)
                    stale["warnings"] = list(stale.get("warnings") or []) + [
                        f"Live refresh failed; showing the last good frame ({type(exc).__name__})"
                    ]
                    stale["stale"] = True
                    return stale
            return {
                "app": APP_ID,
                "version": APP_VERSION,
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "layer": layer,
                "metrics": {},
                "sources": self.source_status(),
                "warnings": [f"Live refresh failed ({type(exc).__name__})"],
                "sessions": [],
                "nodes": [],
                "edges": [],
                "activity": [],
                "stale": True,
            }

    def _memory_detail(self, slug: str) -> dict[str, Any] | None:
        if not SAFE_SLUG_RE.fullmatch(slug):
            return None
        path = self.paths.memory_dir / f"{slug}.md"
        try:
            resolved = path.resolve()
            if resolved.parent != self.paths.memory_dir.resolve() or not resolved.is_file():
                return None
            text = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        description = ""
        body = text
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end >= 0:
                frontmatter = text[3:end]
                body = text[end + 4 :]
                match = re.search(
                    r"(?m)^description:\s*(?:\"([^\"]*)\"|'([^']*)'|(.+))$",
                    frontmatter,
                )
                if match:
                    description = next(
                        (group.strip() for group in match.groups() if group), ""
                    )
        links = list(dict.fromkeys(re.findall(r"\[\[([A-Za-z0-9_.-]+)", text)))[:20]
        excerpt = re.sub(r"\s+", " ", body).strip()[:2400]
        adjacency, _ = self._load_spread_graph()
        neighbors = [
            {"id": f"mem:{neighbor}", "label": pretty_label(neighbor), "weight": weight}
            for neighbor, weight in adjacency.get(slug, [])[:12]
        ]
        return {
            "id": f"mem:{slug}",
            "type": "memory",
            "label": pretty_label(slug),
            "meaning": NODE_EXPLANATIONS["memory"],
            "description": description,
            "excerpt": excerpt,
            "path": str(resolved),
            "updated": iso_from_epoch(resolved.stat().st_mtime),
            "links": links,
            "neighbors": neighbors,
        }

    def _network_node_detail(self, node_id: str) -> dict[str, Any] | None:
        if not self.paths.network_db.exists() or len(node_id) > 500:
            return None
        try:
            with sqlite_readonly(self.paths.network_db) as connection:
                row = connection.execute(
                    """
                    SELECT n.id, n.kind, n.label, n.df, n.idf, c.content
                    FROM nodes n
                    LEFT JOIN node_content c ON c.id=n.id
                    WHERE n.id=?
                    """,
                    (node_id,),
                ).fetchone()
                if row is None:
                    return None
                neighbors = [
                    {
                        "id": edge["dst"],
                        "label": pretty_label(edge["dst"].split(":", 1)[-1]),
                        "relation": edge["kind"],
                        "weight": round(float(edge["w"] or edge["n"] or 0.0), 4),
                    }
                    for edge in connection.execute(
                        """
                        SELECT dst, kind, w, n
                        FROM edges WHERE src=?
                        ORDER BY w DESC LIMIT 12
                        """,
                        (node_id,),
                    ).fetchall()
                ]
        except sqlite3.Error:
            return None
        node_type = {
            "mem": "memory",
            "kw": "concept",
            "prompt": "prompt",
            "tool": "tool",
        }.get(row["kind"], row["kind"])
        return {
            "id": row["id"],
            "type": node_type,
            "label": pretty_label(row["label"] or row["id"].split(":", 1)[-1]),
            "meaning": NODE_EXPLANATIONS.get(node_type, "A typed node in the association network."),
            "description": "",
            "excerpt": (row["content"] or "")[:2400],
            "frequency": int(row["df"] or 0),
            "specificity": round(float(row["idf"] or 0.0), 4),
            "neighbors": neighbors,
        }

    def node_detail(self, node_id: str) -> dict[str, Any]:
        node_id = urllib.parse.unquote(node_id)[:1000]
        if node_id.startswith("mem:"):
            detail = self._memory_detail(node_id[4:])
            if detail:
                with self._cache_lock:
                    cached = self._details.get(node_id, {})
                return {**cached, **detail}
        if node_id.startswith("reflex:"):
            reflex = node_id.split(":", 1)[1]
            info = self._load_instincts().get(reflex, {})
            with self._cache_lock:
                cached = self._details.get(node_id, {})
            return {
                **cached,
                "id": node_id,
                "type": "reflex",
                "label": pretty_label(reflex),
                "meaning": info.get("meaning") or NODE_EXPLANATIONS["reflex"],
                "scar": info.get("scar") or "",
                "source_memory": info.get("source") or "",
                "mode": info.get("mode") or "",
                "validity": info.get("validity") or "",
            }
        if node_id.startswith(("file:", "folder:")):
            node_type, raw_path = node_id.split(":", 1)
            with self._cache_lock:
                cached = self._details.get(node_id, {})
            stat_info: dict[str, Any] = {}
            try:
                stat = Path(raw_path).stat()
                stat_info = {
                    "exists": True,
                    "size_bytes": stat.st_size,
                    "updated": iso_from_epoch(stat.st_mtime),
                }
            except OSError:
                stat_info = {"exists": False}
            return {
                **cached,
                "id": node_id,
                "type": node_type,
                "label": Path(raw_path).name or raw_path,
                "meaning": NODE_EXPLANATIONS[node_type],
                "path": raw_path,
                **stat_info,
            }
        with self._cache_lock:
            cached = self._details.get(node_id)
        if cached:
            return {
                **cached,
                "meaning": cached.get("meaning")
                or NODE_EXPLANATIONS.get(cached.get("type"), "A live brain node."),
            }
        detail = self._network_node_detail(node_id)
        if detail:
            return detail
        return {
            "id": node_id,
            "type": "unknown",
            "label": pretty_label(node_id.split(":", 1)[-1]),
            "meaning": "This node is no longer present in the current live frame.",
        }


class BrainHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], brain: LiveBrain):
        super().__init__(address, BrainRequestHandler)
        self.brain = brain
        self.last_request_at = time.time()


class BrainRequestHandler(BaseHTTPRequestHandler):
    server: BrainHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *args: Any) -> None:
        return

    def _common_headers(self, content_type: str, content_length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._common_headers("application/json; charset=utf-8", len(body))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, relative: str, content_type: str) -> None:
        path = self.server.brain.paths.app_dir / relative
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self._common_headers(content_type, len(body))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self.server.last_request_at = time.time()
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path in STATIC_FILES:
            relative, content_type = STATIC_FILES[parsed.path]
            self._send_static(relative, content_type)
            return
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/api/health":
            self._send_json(
                {
                    "app": APP_ID,
                    "version": APP_VERSION,
                    "ok": True,
                    "time": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
            )
            return
        if parsed.path == "/api/state":
            state = self.server.brain.build_state(
                layer=(query.get("layer") or ["live"])[0],
                selected_session=(query.get("session") or ["all"])[0],
                query=(query.get("q") or [""])[0],
                focus=(query.get("focus") or [""])[0],
            )
            self._send_json(state)
            return
        if parsed.path == "/api/node":
            node_id = (query.get("id") or [""])[0]
            self._send_json(self.server.brain.node_detail(node_id))
            return
        self.send_error(HTTPStatus.NOT_FOUND)


def existing_server(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health", timeout=0.35
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload.get("app") == APP_ID and bool(payload.get("ok"))
    except Exception:
        return False


def open_browser(url: str) -> None:
    try:
        webbrowser.open(url, new=2)
    except Exception:
        pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open the local live brain viewer")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    parser.add_argument("--idle-timeout", type=int, default=1200)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    app_dir = Path(__file__).resolve().parent
    paths = BrainPaths.live(app_dir)

    for port in range(args.port, args.port + 11):
        if existing_server(port):
            if args.open_browser:
                open_browser(f"http://127.0.0.1:{port}/")
            return 0

    brain = LiveBrain(paths)
    server: BrainHTTPServer | None = None
    port = args.port
    for candidate in range(args.port, args.port + 11):
        try:
            server = BrainHTTPServer(("127.0.0.1", candidate), brain)
            port = candidate
            break
        except OSError:
            continue
    if server is None:
        return 2

    if args.open_browser:
        opener = threading.Timer(
            0.35, open_browser, args=(f"http://127.0.0.1:{port}/",)
        )
        opener.daemon = True
        opener.start()

    if args.idle_timeout > 0:
        def idle_watch() -> None:
            while True:
                time.sleep(30)
                if time.time() - server.last_request_at > args.idle_timeout:
                    server.shutdown()
                    return

        watcher = threading.Thread(target=idle_watch, daemon=True)
        watcher.start()

    try:
        server.serve_forever(poll_interval=0.35)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
