"""Research requests as nodes: the questions people ask the store, kept and linked to what answered them.

The store records claims, evidence, mistakes and open questions, but not the request that set the work going.
Without it an agent cannot tell that the same thing was asked last week, who is already on it, or which cards
came out of it. This module keeps requests in an append-only JSON-lines ledger beside the store. Each request
is a node with a stable id (a hash of its normalized text), and links tie it to store node ids:

    asks_about    the request concerns this card (an open question, a claim to check)
    produced      working on the request wrote this card
    answered_by   this card answers the request
    follows       the request continues an earlier request (a follow-up in the same thread)

The ledger is separate from the SQLite store on purpose. The store's schema refuses node and edge kinds it does
not know, so a new kind there would break every older clone that opens the file. Request ids and store ids
meet only in the links.

    python -m math_brain.requests --ledger requests.jsonl ask "Is the mixing time of ... polynomial?"
    python -m math_brain.requests --ledger requests.jsonl similar "mixing time polynomial"
    python -m math_brain.requests --ledger requests.jsonl link req_ab12cd34ef56 claim_3d7e1176 answered_by
    python -m math_brain.requests --ledger requests.jsonl open
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

RELATIONS = ("asks_about", "produced", "answered_by", "follows")
STATUSES = ("open", "answered", "dropped")
ROLES = ("researcher", "agent", "automation")

_STOP = frozenset(
    "a an and are as at be by can do does for from has have how i if in is it its me of on or show that the "
    "this to was we what when where which who why will with you".split()
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_WIN_HOME = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"']+", re.IGNORECASE)
_NIX_HOME = re.compile(r"/(?:home|Users)/[^/\s\"']+", re.IGNORECASE)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _when(stamp: str) -> datetime | None:
    """An ISO time as an aware UTC datetime; a time with no zone is read as UTC; anything else is None."""
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def normalize(text: str) -> str:
    """Lower case, accents folded, punctuation dropped, whitespace collapsed: two typings of one ask match."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def request_id(text: str) -> str:
    return "req_" + hashlib.sha1(normalize(text).encode("utf-8")).hexdigest()[:12]


def redact(text: str) -> str:
    """Remove e-mail addresses and home-directory paths before a request is written down."""
    text = _EMAIL.sub("<email>", text)
    text = _WIN_HOME.sub("<home>", text)
    return _NIX_HOME.sub("<home>", text)


def words(text: str) -> frozenset[str]:
    return frozenset(w for w in normalize(text).split() if w not in _STOP and len(w) > 1)


@dataclass
class Request:
    id: str
    text: str
    role: str = "researcher"
    first_asked: str = ""
    last_asked: str = ""
    times_asked: int = 0
    status: str = "open"
    status_note: str = ""
    links: list[dict[str, str]] = field(default_factory=list)

    def linked(self, relation: str) -> list[str]:
        return [ln["node"] for ln in self.links if ln["rel"] == relation]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "text": self.text, "role": self.role, "first_asked": self.first_asked,
            "last_asked": self.last_asked, "times_asked": self.times_asked, "status": self.status,
            "status_note": self.status_note, "links": list(self.links),
        }


class LedgerError(ValueError):
    pass


class RequestLedger:
    """Append-only event log; the current state of every request is folded from it on load."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.requests: dict[str, Request] = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as exc:
                        # A torn last line (a crash mid-append) is skipped; damage earlier in the file is not.
                        if fh.readline() == "":
                            break
                        raise LedgerError(f"{self.path}:{n}: {exc}") from exc
                    self._apply(event)

    # -- folding ---------------------------------------------------------------------------------------------
    def _apply(self, ev: dict[str, Any]) -> None:
        op = ev.get("op")
        rid = ev.get("id", "")
        if op == "ask":
            req = self.requests.get(rid)
            if req is None:
                req = self.requests[rid] = Request(id=rid, text=ev["text"], role=ev.get("role", "researcher"),
                                                   first_asked=ev["at"])
            req.times_asked += 1
            req.last_asked = max(req.last_asked, ev["at"])
            req.first_asked = min(req.first_asked, ev["at"])
            if req.status == "answered" and ev.get("reopen", True):
                # Asking again after an answer means the answer did not settle it.
                req.status, req.status_note = "open", "asked again after an answer"
        elif op == "link":
            req = self.requests.get(rid)
            if req is not None:
                link = {"rel": ev["rel"], "node": ev["node"], "at": ev["at"]}
                if not any(ln["rel"] == link["rel"] and ln["node"] == link["node"] for ln in req.links):
                    req.links.append(link)
                if ev["rel"] == "answered_by" and req.status == "open":
                    req.status, req.status_note = "answered", f"answered by {ev['node']}"
        elif op == "status":
            req = self.requests.get(rid)
            if req is not None:
                req.status, req.status_note = ev["status"], ev.get("note", "")

    def _append(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._apply(event)

    # -- writing ---------------------------------------------------------------------------------------------
    def ask(self, text: str, *, role: str = "researcher", at: str | None = None, follows: str | None = None) -> Request:
        if role not in ROLES:
            raise LedgerError(f"role must be one of {ROLES}")
        clean = redact(text).strip()
        if not normalize(clean):
            raise LedgerError("an empty request")
        rid = request_id(clean)
        self._append({"op": "ask", "id": rid, "text": clean, "role": role, "at": at or now()})
        if follows:
            self.link(rid, follows, "follows", at=at)
        return self.requests[rid]

    def link(self, rid: str, node: str, rel: str, *, at: str | None = None) -> Request:
        if rel not in RELATIONS:
            raise LedgerError(f"relation must be one of {RELATIONS}")
        if rid not in self.requests:
            raise LedgerError(f"no request {rid}")
        if rel == "follows" and node not in self.requests:
            raise LedgerError(f"follows must name an earlier request, not {node}")
        self._append({"op": "link", "id": rid, "node": node, "rel": rel, "at": at or now()})
        return self.requests[rid]

    def import_prompts(self, rows: Iterable[dict[str, Any]], *, automation: bool = False,
                       min_words: int = 3) -> dict[str, int]:
        """Add the requests in a prompt archive (the framework's PROMPTS.jsonl rows) to the ledger.

        Each row needs `text` and `ts`; `ts` without a zone is read as this machine's local time, which is how the
        archive writes it. A row whose `origin` is "automation" (a loop re-firing a prompt, a launcher's preamble)
        is skipped unless `automation` is true, and then it is kept with the role "automation". A row with fewer
        than `min_words` content words ("ok do it", "yes") is skipped: a follow-up like that means something only
        beside the request before it. Importing the same archive twice adds nothing, because an ask already in the
        ledger at the same time is not written again.
        """
        seen = set()
        if self.path.exists():
            with self.path.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("op") == "ask":
                        seen.add((ev.get("id"), ev.get("at")))
        out = {"added": 0, "again": 0, "already": 0, "automation": 0, "short": 0, "unreadable": 0}
        for row in rows:
            text = redact(str(row.get("text") or "")).strip()
            try:
                t = datetime.fromisoformat(str(row.get("ts", "")).replace("Z", "+00:00"))
            except ValueError:
                out["unreadable"] += 1
                continue
            if not normalize(text):
                out["unreadable"] += 1
                continue
            role = "researcher"
            if row.get("origin") == "automation":
                if not automation:
                    out["automation"] += 1
                    continue
                role = "automation"
            if len(words(text)) < min_words:
                out["short"] += 1
                continue
            at = t.astimezone(UTC).isoformat(timespec="seconds")
            rid = request_id(text)
            if (rid, at) in seen:
                out["already"] += 1
                continue
            out["again" if rid in self.requests else "added"] += 1
            self.ask(text, role=role, at=at)
            seen.add((rid, at))
        return out

    def set_status(self, rid: str, status: str, note: str = "", *, at: str | None = None) -> Request:
        if status not in STATUSES:
            raise LedgerError(f"status must be one of {STATUSES}")
        if rid not in self.requests:
            raise LedgerError(f"no request {rid}")
        self._append({"op": "status", "id": rid, "status": status, "note": note, "at": at or now()})
        return self.requests[rid]

    # -- reading ---------------------------------------------------------------------------------------------
    def get(self, rid: str) -> Request | None:
        return self.requests.get(rid)

    def similar(self, text: str, *, limit: int = 5, threshold: float = 0.3) -> list[tuple[float, Request]]:
        """Earlier requests that share most of their words with this one: was it asked before?"""
        q = words(text)
        if not q:
            return []
        scored = []
        for req in self.requests.values():
            w = words(req.text)
            if w:
                score = len(q & w) / len(q | w)
                if score >= threshold:
                    scored.append((round(score, 3), req))
        scored.sort(key=lambda sr: (-sr[0], sr[1].first_asked))
        return scored[:limit]

    def for_node(self, node: str) -> list[tuple[str, Request]]:
        """Every request linked to a store node, with how: what was asked that led to this card."""
        return [(ln["rel"], req) for req in self.requests.values() for ln in req.links if ln["node"] == node]

    def open_requests(self) -> list[Request]:
        return sorted((r for r in self.requests.values() if r.status == "open"), key=lambda r: r.first_asked)

    def backlog(self, *, today: datetime | None = None, days: int | None = None) -> list[tuple[str, list[dict[str, Any]]]]:
        """Open requests grouped by the day they were first asked, newest day first.

        Each entry says how many times the request was asked, how many days have passed since anyone touched it
        (an ask or a link), and how many nodes it has produced. An entry with nothing produced is a request no
        one has started on. `days` keeps only the requests first asked in the last `days` days.
        """
        today = today or datetime.now(UTC)
        out: dict[str, list[dict[str, Any]]] = {}
        for req in self.open_requests():
            first = _when(req.first_asked)
            if days is not None and first is not None and today - first > timedelta(days=days):
                continue
            touched = [t for t in (_when(s) for s in [req.last_asked] + [ln["at"] for ln in req.links]) if t]
            last = max(touched) if touched else (first or today)
            out.setdefault(req.first_asked[:10] or "unknown", []).append({
                "id": req.id, "text": req.text, "times_asked": req.times_asked,
                "idle_days": max(0, (today - last).days), "produced": len(req.linked("produced")),
            })
        for rows in out.values():
            rows.sort(key=lambda e: (-e["times_asked"], e["id"]))
        return sorted(out.items(), key=lambda kv: kv[0], reverse=True)

    def made_since(self, rid: str, nodes: Iterable[Any], *, limit: int = 5, threshold: float = 0.6,
                   min_shared: int = 2) -> list[tuple[float, Any]]:
        """Store nodes made after a request was last asked that cover most of its words: candidates for `produced`.

        `nodes` is anything with id, title, body and created_at (the store's GraphNode). A node already linked to
        the request, or made before it was last asked, is left out. The score is the share of the request's words
        that the node's title and body contain, so a long card is not penalised for saying more than was asked.
        These are candidates, not links: a person or an agent confirms each one with `link`.
        """
        req = self.requests.get(rid)
        if req is None:
            raise LedgerError(f"no request {rid}")
        q = words(req.text)
        since = _when(req.last_asked or req.first_asked)
        linked = {ln["node"] for ln in req.links}
        if not q or since is None:
            return []
        out = []
        for node in nodes:
            made = _when(getattr(node, "created_at", ""))
            if node.id in linked or made is None or made < since:
                continue
            shared = q & words(f"{node.title} {getattr(node, 'body', '') or ''}")
            score = len(shared) / len(q)
            if len(shared) >= min_shared and score >= threshold:
                out.append((round(score, 3), made, node))
        out.sort(key=lambda smn: (-smn[0], smn[1], smn[2].id))
        return [(score, node) for score, _, node in out[:limit]]

    def thread(self, rid: str) -> list[Request]:
        """The request with everything it follows and everything that follows it, oldest first."""
        seen: set[str] = set()
        todo = [rid]
        while todo:
            cur = todo.pop()
            if cur in seen or cur not in self.requests:
                continue
            seen.add(cur)
            todo.extend(self.requests[cur].linked("follows"))
            todo.extend(r.id for r in self.requests.values() if cur in r.linked("follows"))
        return sorted((self.requests[i] for i in seen), key=lambda r: r.first_asked)

    def graph(self) -> tuple[list[Any], list[Any]]:
        """The ledger as graph records a recall or consolidation pass can walk, without touching the store.

        Each request becomes an external-reference proxy node (namespace "requests"), and each link becomes a
        bridges_to edge from the store node to that proxy, so a card can be followed back to the request behind
        it. A follow-up is a bridges_to edge from the later request's proxy to the earlier one's. Ids and times
        come from the ledger itself, so the same ledger always yields the same records."""
        from .models import EdgeKind, GraphEdge
        from .overlay import external_reference_node

        proxies: dict[str, Any] = {}
        for req in sorted(self.requests.values(), key=lambda r: r.id):
            node = external_reference_node("requests", req.id, req.text[:200])
            proxies[req.id] = replace(node, created_at=req.first_asked or node.created_at)
        edges: dict[str, Any] = {}
        for req in sorted(self.requests.values(), key=lambda r: r.id):
            for ln in req.links:
                if ln["rel"] == "follows":
                    if ln["node"] not in proxies:
                        continue
                    source, target = proxies[req.id].id, proxies[ln["node"]].id
                else:
                    source, target = ln["node"], proxies[req.id].id
                eid = "edge_req_" + hashlib.sha1(f"{source}|{target}|{ln['rel']}".encode()).hexdigest()[:20]
                edges[eid] = GraphEdge(source, target, EdgeKind.BRIDGES_TO, f"request link: {ln['rel']}",
                                       id=eid, created_at=ln.get("at") or req.first_asked)
        return list(proxies.values()), list(edges.values())

    # -- the nightly pass ------------------------------------------------------------------------------------
    def consolidate(self, *, status_of: Any = None, stale_days: int = 14, today: datetime | None = None) -> dict[str, list[str]]:
        """What a dream pass does with the ledger. Returns what it changed and what it wants a person to look at.

        status_of, when given, maps a store node id to its status. With a store, pass
        ``lambda node: recompute_current_status(store, node, verifier)`` from math_brain.verification: the
        store's own current_status refuses any claim that has verification events. An open request whose
        'produced' card is now verified is marked answered by that card; one whose answer was refuted,
        retracted or found to have a gap is reopened. A node whose status cannot be read is listed under
        'unresolved', never taken as an answer and never skipped in silence. Open requests untouched for
        stale_days are listed, not dropped: only a person drops a request.
        """
        today = today or datetime.now(UTC)
        out: dict[str, list[str]] = {"answered": [], "reopened": [], "stale": [], "repeated": [], "unresolved": []}
        for req in list(self.requests.values()):
            if status_of is not None:
                for node in req.linked("answered_by") + req.linked("produced"):
                    try:
                        raw = status_of(node)
                    except Exception as exc:  # noqa: BLE001 - reported below, never taken as an answer
                        out["unresolved"].append(f"{req.id} {node}: {type(exc).__name__}: {exc}")
                        continue
                    st = str(getattr(raw, "value", raw))
                    if req.status == "open" and st == "verified":
                        self.link(req.id, node, "answered_by")
                        out["answered"].append(req.id)
                        break
                    if req.status == "answered" and node in req.linked("answered_by") and st in ("refuted", "retracted", "gap_found"):
                        self.set_status(req.id, "open", f"answer {node} is {st}")
                        out["reopened"].append(req.id)
                        break
            if req.status == "open":
                last = max([req.last_asked] + [ln["at"] for ln in req.links])
                try:
                    age = today - datetime.fromisoformat(last)
                except ValueError:
                    age = timedelta(0)
                if age > timedelta(days=stale_days):
                    out["stale"].append(req.id)
            if req.times_asked > 1 and req.status == "open":
                out["repeated"].append(req.id)
        return out


def _main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m math_brain.requests", description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", default=os.environ.get("MATH_BRAIN_REQUESTS", "requests.jsonl"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ask"); p.add_argument("text"); p.add_argument("--role", default="researcher"); p.add_argument("--follows")
    p = sub.add_parser("link"); p.add_argument("id"); p.add_argument("node"); p.add_argument("rel", choices=RELATIONS)
    p = sub.add_parser("status"); p.add_argument("id"); p.add_argument("status", choices=STATUSES); p.add_argument("--note", default="")
    p = sub.add_parser("similar"); p.add_argument("text"); p.add_argument("--limit", type=int, default=5)
    p = sub.add_parser("node"); p.add_argument("node")
    p = sub.add_parser("show"); p.add_argument("id")
    sub.add_parser("open")
    p = sub.add_parser("backlog", help="open requests by the day they were first asked, newest first")
    p.add_argument("--days", type=int, help="only requests first asked in the last N days")
    p = sub.add_parser("consolidate"); p.add_argument("--stale-days", type=int, default=14)
    p = sub.add_parser("import", help="add the requests in a prompt archive (PROMPTS.jsonl)")
    p.add_argument("archive"); p.add_argument("--automation", action="store_true")
    p.add_argument("--min-words", type=int, default=3)
    p = sub.add_parser("made", help="store nodes made after a request that look like its answer")
    p.add_argument("id"); p.add_argument("--store", required=True); p.add_argument("--limit", type=int, default=5)
    p.add_argument("--threshold", type=float, default=0.6)
    p.add_argument("--link", action="store_true", help="record each candidate as produced by the request")
    a = ap.parse_args(list(argv) if argv is not None else None)
    led = RequestLedger(a.ledger)
    try:
        if a.cmd == "ask":
            before = led.similar(a.text, limit=3)
            req = led.ask(a.text, role=a.role, follows=a.follows)
            print(req.id, f"(asked {req.times_asked} time{'s' if req.times_asked > 1 else ''}, {req.status})")
            for score, old in before:
                if old.id != req.id:
                    print(f"  close to {old.id} ({score}, {old.status}): {old.text[:100]}")
        elif a.cmd == "link":
            print(led.link(a.id, a.node, a.rel).status)
        elif a.cmd == "status":
            print(led.set_status(a.id, a.status, a.note).status)
        elif a.cmd == "similar":
            for score, req in led.similar(a.text, limit=a.limit):
                print(f"{score:.2f} {req.id} {req.status:8} x{req.times_asked} {req.text[:110]}")
        elif a.cmd == "node":
            for rel, req in led.for_node(a.node):
                print(f"{rel:12} {req.id} {req.status:8} {req.text[:110]}")
        elif a.cmd == "show":
            req = led.get(a.id)
            if req is None:
                print("no such request", file=sys.stderr)
                return 1
            print(json.dumps(req.as_dict(), ensure_ascii=False, indent=1))
            for r in led.thread(a.id):
                print(f"  thread {r.first_asked[:10]} {r.id} {r.text[:90]}")
        elif a.cmd == "open":
            for req in led.open_requests():
                print(f"{req.first_asked[:10]} {req.id} x{req.times_asked} {req.text[:110]}")
        elif a.cmd == "backlog":
            for day, rows in led.backlog(days=a.days):
                print(f"{day} ({len(rows)} open)")
                for e in rows:
                    work = f"{e['produced']} produced" if e["produced"] else "nothing produced"
                    print(f"  {e['id']} x{e['times_asked']} idle {e['idle_days']}d, {work}: {e['text'][:90]}")
        elif a.cmd == "consolidate":
            print(json.dumps(led.consolidate(stale_days=a.stale_days), indent=1))
        elif a.cmd == "import":
            rows = []
            with open(a.archive, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            print(json.dumps(led.import_prompts(rows, automation=a.automation, min_words=a.min_words)))
        elif a.cmd == "made":
            from .store import ResearchStore

            with ResearchStore.open_read_only(a.store) as store:
                found = led.made_since(a.id, list(store.iter_nodes()), limit=a.limit, threshold=a.threshold)
            for score, node in found:
                if a.link:
                    led.link(a.id, node.id, "produced")
                print(f"{score:.2f} {node.id} {str(node.created_at)[:16]} {node.title[:90]}"
                      + ("  (linked)" if a.link else ""))
    except LedgerError as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main())
