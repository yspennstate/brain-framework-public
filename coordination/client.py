"""Durable retry queue and SSH transport; imports do not perform network I/O."""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
import subprocess
import uuid
import threading
from dataclasses import dataclass
from hub import LIMIT, Rejected, canonical, decode, fields, ident


class TransportFailure(Exception):
    """Outcome unknown: retain the same request key and retry, never invent a new one."""


class SSHTransport:
    def __init__(self, alias):
        # Use an operator-configured SSH alias, never a URL or remote command.
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", alias):
            raise ValueError("invalid SSH alias")
        self.alias = alias
        self.destination = alias

    def __call__(self, request):
        raw = canonical(request).encode()
        if len(raw) > LIMIT:
            raise TransportFailure("request_too_large")
        command = ["ssh", "-T", "-oBatchMode=yes", "-oStrictHostKeyChecking=yes",
                   "-oConnectTimeout=10", "-oConnectionAttempts=1", "-oClearAllForwardings=yes",
                   self.alias, "coordination-v1"]
        try:
            raw_reply = run_bounded(command, raw)
            reply = json.loads(raw_reply)
            if type(reply) is not dict or type(reply.get("ok")) is not bool:
                raise TransportFailure("invalid_reply")
            return reply
        except (OSError, subprocess.SubprocessError, ValueError, RecursionError):
            raise TransportFailure("transport_unavailable") from None


def run_bounded(command, raw):
    """Bound output memory and elapsed time even if the remote side misbehaves."""
    result = []
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        def exchange():
            try:
                process.stdin.write(raw)
                process.stdin.close()
                reply = process.stdout.read(2*1024*1024+1)
                if len(reply) > 2*1024*1024:
                    process.kill()
                    return
                if process.wait() == 0:
                    result.append(reply)
            except (OSError, ValueError):
                pass
        worker = threading.Thread(target=exchange, daemon=True)
        worker.start()
        worker.join(30)
        if worker.is_alive():
            process.kill()
            worker.join(5)
        if not result:
            raise TransportFailure("transport_unavailable")
        return result[0]



@dataclass(frozen=True)
class BoundTransport:
    """Explicit trusted integration adapter; destination includes hub and identity."""
    destination: str
    send: object

    def __call__(self, request):
        return self.send(request)


class Outbox:
    """One private local outbox per SSH identity/hub. Store it outside the repo."""
    def __init__(self, path, destination):
        self.db = sqlite3.connect(path)
        self.db.executescript('''CREATE TABLE IF NOT EXISTS route(value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS blocked(key TEXT PRIMARY KEY);
          CREATE TABLE IF NOT EXISTS pending(seq INTEGER PRIMARY KEY, key TEXT UNIQUE,
            request TEXT NOT NULL, reply TEXT);''')
        try:
            self.db.execute("BEGIN IMMEDIATE")
            routes = self.db.execute("SELECT value FROM route").fetchall()
            if len(routes) > 1 or (routes and routes[0][0] != destination):
                raise ValueError("outbox_destination_mismatch")
            if not routes:
                self.db.execute("INSERT INTO route VALUES (?)", (destination,))
            self.db.commit()
        except Exception:
            self.db.rollback()
            self.db.close()
            raise
        self.destination = destination

    def close(self):
        self.db.close()

    def enqueue(self, op, data, key=None):
        request = {"key": ident(key or str(uuid.uuid4())), "op": ident(op), "data": data}
        raw = canonical(request)
        decode(raw.encode())
        old = self.db.execute("SELECT request FROM pending WHERE key=?", (request["key"],)).fetchone()
        if old:
            if old[0] != raw:
                raise ValueError("idempotency_conflict")
            return request["key"]
        self.db.execute("INSERT INTO pending(key,request) VALUES (?,?)", (request["key"], raw))
        self.db.commit()
        return request["key"]

    def flush(self, transport):
        if getattr(transport, "destination", None) != self.destination:
            raise ValueError("transport_destination_mismatch")
        if self.db.execute("SELECT 1 FROM blocked").fetchone():
            raise ValueError("resolve_rejected_request_first")
        # Stop persistently on rejection until explicit operator reconciliation.
        for key, raw in self.db.execute("SELECT key,request FROM pending WHERE reply IS NULL ORDER BY seq").fetchall():
            reply = transport(json.loads(raw))
            if type(reply) is not dict or type(reply.get("ok")) is not bool:
                raise TransportFailure("invalid_reply")
            self.db.execute("UPDATE pending SET reply=? WHERE key=?", (canonical(reply), key))
            if not reply["ok"]:
                self.db.execute("INSERT INTO blocked VALUES (?)", (key,))
            self.db.commit()
            yield key, reply
            if not reply["ok"]:
                break

    def resolve_rejection(self, key):
        """Acknowledge reviewed rejection; does not replay or change its outcome."""
        if not self.db.execute("SELECT 1 FROM blocked WHERE key=?", (key,)).fetchone():
            raise ValueError("no_matching_rejection")
        self.db.execute("DELETE FROM blocked WHERE key=?", (key,))
        self.db.commit()

    def result(self, key):
        row = self.db.execute("SELECT reply FROM pending WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None
