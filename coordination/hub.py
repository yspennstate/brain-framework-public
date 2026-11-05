"""Single-authority coordination core. MIT; no runtime data belongs in Git."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

LIMIT = 32768
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")


class Rejected(Exception):
    """Public error codes only; never include submitted text or local paths."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def ident(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise Rejected("invalid_identifier")
    return value


def text(value):
    if not isinstance(value, str) or len(value) > 8000 or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise Rejected("invalid_text")
    return value


def integer(value, low=0, high=2**53-1):
    if type(value) is not int or not low <= value <= high:
        raise Rejected("invalid_integer")
    return value


def decode(raw):
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise Rejected("duplicate_field")
            out[k] = v
        return out
    try:
        if len(raw) > LIMIT:
            raise Rejected("request_too_large")
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(Rejected("invalid_json")))
        if type(value) is not dict:
            raise Rejected("invalid_request")
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise Rejected("invalid_json") from None


def fields(data, required, optional=()):
    if type(data) is not dict or not set(required) <= data.keys() or data.keys() - set(required) - set(optional):
        raise Rejected("invalid_fields")


class Hub:
    def __init__(self, path, agents, clock=time.time):
        self.path = Path(path)
        self.agents = frozenset(ident(a) for a in agents)
        self.clock = clock
        # Caller must supply a private, precreated local directory, never NFS.
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS objects(kind TEXT, id TEXT, value TEXT,
            PRIMARY KEY(kind,id));
          CREATE TABLE IF NOT EXISTS receipts(agent TEXT, key TEXT, digest TEXT, reply TEXT,
            PRIMARY KEY(agent,key));
          CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value INTEGER);
        ''')

    def close(self):
        self.db.close()

    def get(self, kind, key):
        row = self.db.execute("SELECT value FROM objects WHERE kind=? AND id=?", (kind, key)).fetchone()
        if not row:
            raise Rejected("not_found")
        return json.loads(row[0])

    def save(self, kind, obj):
        self.db.execute("INSERT OR REPLACE INTO objects VALUES (?,?,?)", (kind, obj["id"], canonical(obj)))
        return obj

    def new(self, kind, obj):
        if self.db.execute("SELECT 1 FROM objects WHERE kind=? AND id=?", (kind, obj["id"])).fetchone():
            raise Rejected("already_exists")
        return self.save(kind, obj)

    def execute(self, agent, request):
        if agent not in self.agents:
            return {"ok": False, "error": "unauthorized"}
        try:
            fields(request, ("key", "op", "data"))
            key = ident(request["key"])
            op = ident(request["op"])
            raw = canonical(request)
            if len(raw.encode()) > LIMIT:
                raise Rejected("request_too_large")
            digest = hashlib.sha256(raw.encode()).hexdigest()
            self.db.execute("BEGIN IMMEDIATE")
            old = self.db.execute("SELECT digest,reply FROM receipts WHERE agent=? AND key=?", (agent, key)).fetchone()
            if old:
                if old[0] != digest:
                    raise Rejected("idempotency_conflict")
                self.db.execute("COMMIT")
                return json.loads(old[1])
            now = int(self.clock())
            last = self.db.execute("SELECT value FROM metadata WHERE key='clock'").fetchone()
            if last and now < last[0]:
                raise Rejected("clock_rollback")
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('clock',?)", (now,))
            self.db.execute("SAVEPOINT operation")
            try:
                result = self.apply(agent, op, request["data"], now)
                reply = {"ok": True, "result": result}
            except Rejected as exc:
                self.db.execute("ROLLBACK TO operation")
                reply = {"ok": False, "error": str(exc)}
            self.db.execute("RELEASE operation")
            self.db.execute("INSERT INTO receipts VALUES (?,?,?,?)", (agent, key, digest, canonical(reply)))
            self.db.execute("COMMIT")
            return reply
        except Rejected as exc:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            return {"ok": False, "error": str(exc)}
        except (ValueError, TypeError, OverflowError, RecursionError):
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            return {"ok": False, "error": "invalid_request"}
        except sqlite3.Error:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            return {"ok": False, "error": "storage_unavailable"}

    def peer(self, value):
        ident(value)
        if value not in self.agents:
            raise Rejected("unknown_recipient")
        return value

    def revision(self, obj, data):
        if obj["revision"] != integer(data["revision"], 1):
            raise Rejected("stale_revision")

    def owned(self, obj, agent, data, now):
        if obj["owner"] != agent or obj["status"] != "claimed" or obj["until"] <= now:
            raise Rejected("lease_not_owned")
        if integer(data["fence"], 1) != obj["fence"]:
            raise Rejected("stale_fence")

    def apply(self, agent, op, d, now):
        if op == "read":
            fields(d, ("kind", "id"))
            if d["kind"] not in ("task", "message", "schedule"):
                raise Rejected("invalid_kind")
            obj = self.get(d["kind"], ident(d["id"]))
            if d["kind"] != "task" and agent not in (obj["sender"], obj["recipient"]):
                raise Rejected("forbidden")
            return obj
        if op == "list":
            fields(d, ("kind",), ("after",))
            if d["kind"] not in ("task", "message", "schedule"):
                raise Rejected("invalid_kind")
            after = ident(d["after"]) if d.get("after") else ""
            # SQL filtering before LIMIT prevents invisible rows hiding later mail.
            rows = self.db.execute("SELECT value FROM objects WHERE kind=? AND id>? ORDER BY id", (d["kind"], after))
            result = []
            byte_budget = 1024 * 1024
            for row in rows:
                obj = json.loads(row[0])
                if d["kind"] == "task" or agent in (obj["sender"], obj["recipient"]):
                    size = len(canonical(obj).encode()) + 1
                    if size > byte_budget:
                        break
                    byte_budget -= size
                    result.append(obj)
                    if len(result) == 100:
                        break
            return result
        if op == "post":
            fields(d, ("id", "title", "body"))
            return self.new("task", dict(id=ident(d["id"]), title=text(d["title"]), body=text(d["body"]),
                creator=agent, owner=None, status="open", revision=1, fence=0, until=0, offer=None, result=""))
        if op in ("claim", "renew", "update", "finish", "release", "offer", "accept", "reassign"):
            required = {"id", "revision"}
            extra = {"claim": {"ttl"}, "renew": {"ttl", "fence"}, "update": {"body", "fence"},
                "finish": {"result", "fence"}, "release": {"fence"}, "offer": {"recipient", "fence"},
                "accept": {"ttl"}, "reassign": {"recipient"}}[op]
            fields(d, required | extra)
            obj = self.get("task", ident(d["id"]))
            self.revision(obj, d)
            if op == "claim":
                if obj["status"] == "done" or (obj["status"] == "claimed" and obj["until"] > now):
                    raise Rejected("not_claimable")
                if obj["offer"] and obj["offer"] != agent and obj["status"] == "open":
                    raise Rejected("assigned_elsewhere")
                obj.update(owner=agent, status="claimed", until=now+integer(d["ttl"], 1, 3600), fence=obj["fence"]+1, offer=None)
            elif op == "accept":
                if obj["offer"] != agent or obj["status"] != "claimed" or obj["until"] <= now:
                    raise Rejected("handoff_unavailable")
                obj.update(owner=agent, until=now+integer(d["ttl"], 1, 3600), fence=obj["fence"]+1, offer=None)
            elif op == "reassign":
                if obj["creator"] != agent:
                    raise Rejected("forbidden")
                if obj["status"] == "done":
                    raise Rejected("already_done")
                obj.update(owner=None, status="open", until=0, fence=obj["fence"]+1, offer=self.peer(d["recipient"]))
            else:
                self.owned(obj, agent, d, now)
                if op == "renew":
                    obj["until"] = now + integer(d["ttl"], 1, 3600)
                elif op == "update":
                    obj["body"] = text(d["body"])
                elif op == "finish":
                    obj.update(status="done", result=text(d["result"]), until=0, offer=None)
                elif op == "release":
                    obj.update(status="open", owner=None, until=0, offer=None, fence=obj["fence"]+1)
                elif op == "offer":
                    obj["offer"] = self.peer(d["recipient"])
            obj["revision"] += 1
            return self.save("task", obj)
        if op in ("send", "schedule"):
            required = {"id", "recipient", "body"}
            if op == "schedule":
                required |= {"not_before", "expires"}
            fields(d, required)
            obj = dict(id=ident(d["id"]), sender=agent, recipient=self.peer(d["recipient"]),
                       body=text(d["body"]), status="pending", revision=1)
            if op == "schedule":
                start = integer(d["not_before"], now, now+366*86400)
                end = integer(d["expires"], start+1, now+367*86400)
                obj.update(not_before=start, expires=end)
            return self.new("message" if op == "send" else "schedule", obj)
        if op in ("ack", "decide"):
            fields(d, ("id", "revision") if op == "ack" else ("id", "revision", "decision"))
            kind = "message" if op == "ack" else "schedule"
            obj = self.get(kind, ident(d["id"]))
            self.revision(obj, d)
            decision = "acknowledged" if op == "ack" else d["decision"]
            if decision not in ("acknowledged", "accepted", "rejected", "cancelled") or (op == "decide" and decision == "acknowledged"):
                raise Rejected("invalid_decision")
            allowed = obj["sender"] if decision == "cancelled" else obj["recipient"]
            if agent != allowed:
                raise Rejected("forbidden")
            if obj["status"] != "pending":
                raise Rejected("already_decided")
            if kind == "schedule" and now >= obj["expires"]:
                raise Rejected("request_expired")
            obj.update(status=decision, revision=obj["revision"]+1)
            return self.save(kind, obj)
        raise Rejected("unknown_operation")


def serve(hub, agent, source, dest):
    """Exactly one bounded request per authenticated SSH process."""
    try:
        request = decode(source.read(LIMIT+1))
        reply = hub.execute(agent, request)
    except Rejected as exc:
        reply = {"ok": False, "error": str(exc)}
    except Exception:
        reply = {"ok": False, "error": "internal_error"}
    dest.write(canonical(reply) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True)
    p.add_argument("--agent", required=True, help="Trusted forced-command identity, never taken from stdin")
    p.add_argument("--agents", required=True, help="Trusted comma-separated allowlist")
    args = p.parse_args()
    try:
        hub = Hub(args.db, args.agents.split(","))
        try:
            serve(hub, args.agent, sys.stdin.buffer, sys.stdout)
        finally:
            hub.close()
    except Exception:
        print('{"ok":false,"error":"startup_unavailable"}')
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
