"""The nightly pass over a store: run consolidation, keep its proposals in a review queue that remembers.

`consolidation.consolidate` turns the graph into review proposals (orphans, claims with no evidence,
contradictions to resolve, reflexes to rehearse, associations to check), but a single call forgets them. This
module is what runs it each night. It opens the store read-only, runs the cycle, and appends to a JSON-lines
queue, so that across nights one can see which proposals are new, which keep coming back unattended, and
which disappeared because the graph changed. A person or an agent closes a proposal with `review`.

Nothing here writes to the store, and nothing in the queue is evidence: the proposals stay quarantined exactly
as consolidation made them.

    python -m math_brain.nightly run --store brain.sqlite --queue dream/queue.jsonl --digest dream/DIGEST.md
    python -m math_brain.nightly review --queue dream/queue.jsonl proposal_0a1b2c... done --note "edge added"
    python -m math_brain.nightly show --queue dream/queue.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import warnings
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .consolidation import consolidate
from .store import ResearchStore

VERDICTS = ("done", "dismissed")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class QueuedProposal:
    id: str
    phase: str
    kind: str
    node_ids: list[str]
    hypothesis: str
    required_actions: list[str]
    priority: int
    first_seen: str
    last_seen: str = ""
    nights_seen: int = 0
    present: bool = True
    verdict: str = ""
    note: str = ""

    @property
    def open(self) -> bool:
        return self.present and not self.verdict


@dataclass
class Queue:
    path: Path
    items: dict[str, QueuedProposal] = field(default_factory=dict)
    last_input: str = ""
    nights: int = 0

    @classmethod
    def load(cls, path: str | Path) -> Queue:
        q = cls(Path(path))
        if q.path.exists():
            lines = q.path.read_text(encoding="utf-8").splitlines()
            for n, line in enumerate(lines, 1):
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    if n == len(lines):
                        break  # a torn last line from an interrupted append
                    raise
                q._apply(ev)
        return q

    def _apply(self, ev: dict[str, Any]) -> None:
        op = ev["op"]
        if op == "new":
            p = ev["proposal"]
            self.items[p["id"]] = QueuedProposal(first_seen=ev["at"], **p)
        elif op == "night":
            self.nights += 1
            self.last_input = ev["input"]
            seen = set(ev["seen"])
            for item in self.items.values():
                item.present = item.id in seen
                if item.present:
                    item.nights_seen += 1
                    item.last_seen = ev["at"]
        elif op == "review":
            item = self.items.get(ev["id"])
            if item is not None:
                item.verdict, item.note = ev["verdict"], ev.get("note", "")

    def _append(self, events: Iterable[dict[str, Any]]) -> None:
        events = list(events)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        for ev in events:
            self._apply(ev)

    def review(self, proposal_id: str, verdict: str, note: str = "") -> QueuedProposal:
        if verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {VERDICTS}")
        if proposal_id not in self.items:
            raise KeyError(proposal_id)
        self._append([{"op": "review", "id": proposal_id, "verdict": verdict, "note": note, "at": now()}])
        return self.items[proposal_id]

    def open_items(self) -> list[QueuedProposal]:
        return sorted((i for i in self.items.values() if i.open), key=lambda i: (-i.priority, -i.nights_seen, i.id))


def _load_records(store: ResearchStore, table: str, getter: Callable[[str], Any], damaged: list[str]) -> list[Any]:
    out = []
    for row in store.connection.execute(f"SELECT id FROM {table} ORDER BY id"):  # table is one of two literals
        try:
            rec = getter(row["id"])
        except (ValueError, TypeError, KeyError) as exc:
            damaged.append(f"{table} {row['id']}: {exc}")
            continue
        if rec is not None:
            out.append(rec)
    return out


def run(
    store_path: str | Path,
    queue_path: str | Path,
    *,
    max_rem_proposals: int = 25,
    requests_path: str | Path | None = None,
    requests_in_graph: bool = False,
    status_of: Callable[[str], Any] | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    """One night. Returns a report; appends to the queue only when the store changed since the last night."""
    at = at or now()
    damaged: list[str] = []
    with ResearchStore.open_read_only(store_path) as store:
        nodes = list(store.iter_nodes())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            edges = list(store.iter_edges(skip_unknown=True))
        unknown = store.unknown_edge_kinds()
        mistakes = _load_records(store, "mistakes", store.get_mistake, damaged)
        reflexes = _load_records(store, "reflexes", store.get_reflex, damaged)
    if requests_path is not None and requests_in_graph:
        from .requests import RequestLedger

        req_nodes, req_edges = RequestLedger(requests_path).graph()
        nodes += req_nodes
        edges += req_edges
    cycle = consolidate(nodes, edges, mistakes=mistakes, reflexes=reflexes, max_rem_proposals=max_rem_proposals)
    input_digest = "sha256:" + hashlib.sha256("\n".join(cycle.input_digests).encode()).hexdigest()

    queue = Queue.load(queue_path)
    report: dict[str, Any] = {
        "at": at, "nodes": len(nodes), "edges": len(edges), "unknown_edge_kinds": unknown,
        "damaged_records": damaged, "proposals": len(cycle.proposals), "changed": input_digest != queue.last_input,
        "new": [], "gone": [], "open": 0, "requests": None,
    }
    if report["changed"]:
        before = {i.id for i in queue.items.values() if i.present}
        events: list[dict[str, Any]] = []
        for p in cycle.proposals:
            if p.id not in queue.items:
                events.append({"op": "new", "at": at, "proposal": {
                    "id": p.id, "phase": p.phase.value, "kind": p.kind.value, "node_ids": list(p.node_ids),
                    "hypothesis": p.hypothesis, "required_actions": list(p.required_actions), "priority": p.priority,
                }})
                report["new"].append(p.id)
        seen = [p.id for p in cycle.proposals]
        events.append({"op": "night", "at": at, "input": input_digest, "seen": seen})
        queue._append(events)
        report["gone"] = sorted(before - set(seen))
    report["open"] = len(queue.open_items())
    if requests_path is not None:
        from .requests import RequestLedger

        led = RequestLedger(requests_path)
        report["requests"] = led.consolidate(status_of=status_of)
        report["request_backlog"] = led.backlog()
    return report


def digest(queue: Queue, report: dict[str, Any], *, top: int = 20) -> str:
    """A short page a person can read in the morning."""
    lines = [f"# Nightly pass, {report['at'][:16].replace('T', ' ')} UTC", ""]
    lines.append(f"{report['nodes']} nodes, {report['edges']} edges, {report['proposals']} proposals, "
                 f"{report['open']} open in the queue, {len(report['new'])} new tonight, {len(report['gone'])} gone.")
    if not report["changed"]:
        lines.append("The store has not changed since the last night; the queue was not touched.")
    if report["unknown_edge_kinds"]:
        lines.append(f"Edge kinds this build does not know were skipped: {report['unknown_edge_kinds']}.")
    if report["damaged_records"]:
        lines.append(f"{len(report['damaged_records'])} mistake or reflex records failed their own checks:")
        lines.extend(f"- {d}" for d in report["damaged_records"][:10])
    lines += ["", "## Open, highest priority first", ""]
    for item in queue.open_items()[:top]:
        age = f"{item.nights_seen} night{'s' if item.nights_seen != 1 else ''}"
        lines.append(f"- `{item.id}` {item.kind}, priority {item.priority}, {age}: {item.hypothesis}")
        lines.append(f"  nodes {', '.join(item.node_ids)}; first step: {item.required_actions[0]}")
    req = report.get("requests")
    if req:
        lines += ["", "## Requests", ""]
        for key in ("answered", "reopened", "unresolved", "repeated", "stale"):
            if req.get(key):
                lines.append(f"- {key}: {len(req[key])} ({', '.join(str(x).split()[0] for x in req[key][:8])})")
    backlog = report.get("request_backlog") or []
    if backlog:
        lines += ["", "## Open requests by day", ""]
        shown = 0
        for day, rows in backlog:
            if shown >= top:
                lines.append(f"- and {sum(len(r) for _, r in backlog) - shown} more open; `python -m math_brain.requests backlog`")
                break
            lines.append(f"- {day}")
            for e in rows[: top - shown]:
                work = f"{e['produced']} produced" if e["produced"] else "not started"
                lines.append(f"  - `{e['id']}` asked {e['times_asked']}x, idle {e['idle_days']}d, {work}: {e['text'][:100]}")
            shown += min(len(rows), top - shown)
    return "\n".join(lines) + "\n"


def _main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m math_brain.nightly", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--store", required=True)
    r.add_argument("--queue", required=True)
    r.add_argument("--requests")
    r.add_argument("--digest")
    r.add_argument("--max-rem", type=int, default=25)
    r.add_argument("--requests-in-graph", action="store_true",
                   help="consolidate the request proxies and their links with the store's graph")
    v = sub.add_parser("review")
    v.add_argument("--queue", required=True)
    v.add_argument("id")
    v.add_argument("verdict", choices=VERDICTS)
    v.add_argument("--note", default="")
    s = sub.add_parser("show")
    s.add_argument("--queue", required=True)
    s.add_argument("--top", type=int, default=20)
    a = ap.parse_args(list(argv) if argv is not None else None)
    if a.cmd == "run":
        report = run(a.store, a.queue, max_rem_proposals=a.max_rem, requests_path=a.requests,
                     requests_in_graph=a.requests_in_graph)
        page = digest(Queue.load(a.queue), report)
        if a.digest:
            Path(a.digest).parent.mkdir(parents=True, exist_ok=True)
            Path(a.digest).write_text(page, encoding="utf-8")
        print(page, end="")
    elif a.cmd == "review":
        try:
            item = Queue.load(a.queue).review(a.id, a.verdict, a.note)
        except KeyError:
            print(f"no proposal {a.id}", file=sys.stderr)
            return 2
        print(item.id, item.verdict)
    elif a.cmd == "show":
        for item in Queue.load(a.queue).open_items()[: a.top]:
            print(f"{item.priority:3} x{item.nights_seen:<3} {item.id} {item.kind:22} {item.hypothesis[:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
