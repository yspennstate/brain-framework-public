"""Priority owner questions and scoped evidence. Never executes approved actions.

The verifier is trusted application code backed by an EXISTING authenticated
platform. There is deliberately no verifier for text, email, voice or hashes.
"""
from dataclasses import dataclass
import hashlib
import json
import sqlite3
import time
from hub import Rejected, canonical, fields, ident, integer, text


@dataclass(frozen=True)
class VerifiedDecision:
    principal: str
    source_receipt: str
    question_id: str
    requester: str
    scope_sha256: str
    decision: str
    issued_at: int
    expires_at: int
    revoked: bool


def scope_digest(scope):
    fields(scope, ("action", "target", "data", "limits"))
    for key in ("action", "target"):
        if not text(scope[key]):
            raise Rejected("empty_scope")
    if type(scope["data"]) is not dict or type(scope["limits"]) is not dict:
        raise Rejected("invalid_scope")
    try:
        raw = canonical(scope).encode()
    except (ValueError, TypeError, RecursionError):
        raise Rejected("invalid_scope") from None
    if len(raw) > 16000:
        raise Rejected("scope_too_large")
    return hashlib.sha256(raw).hexdigest()


class OwnerQuestions:
    def __init__(self, path, expected_owner, verifier=None, clock=time.time):
        if not isinstance(expected_owner, str) or not expected_owner or len(expected_owner) > 240:
            raise ValueError("invalid_expected_owner")
        self.owner = expected_owner
        self.verifier = verifier
        self.clock = clock
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS owner_questions(
            id TEXT PRIMARY KEY, requester TEXT NOT NULL, task TEXT NOT NULL,
            priority INTEGER NOT NULL, body TEXT NOT NULL, options TEXT NOT NULL,
            scope TEXT NOT NULL, digest TEXT NOT NULL, status TEXT NOT NULL,
            receipt TEXT UNIQUE);
          CREATE TABLE IF NOT EXISTS owner_receipts(
            id TEXT PRIMARY KEY, question TEXT NOT NULL UNIQUE, evidence TEXT NOT NULL,
            consumed INTEGER NOT NULL DEFAULT 0);
        ''')

    def close(self):
        self.db.close()

    def submit(self, requester, question_id, task, priority, body, options, scope):
        # Integrator supplies requester from authenticated transport, not peer JSON.
        ident(requester); ident(question_id); ident(task); integer(priority, 1, 10)
        text(body)
        if type(options) is not list or not 1 <= len(options) <= 10:
            raise Rejected("invalid_options")
        for option in options:
            if not text(option):
                raise Rejected("invalid_options")
        digest = scope_digest(scope)
        values = (question_id, requester, task, priority, body, canonical(options), canonical(scope), digest, "pending", None)
        try:
            self.db.execute("BEGIN IMMEDIATE")
            old = self.db.execute("SELECT * FROM owner_questions WHERE id=?", (question_id,)).fetchone()
            if old:
                if tuple(old)[:8] != values[:8]:
                    raise Rejected("question_id_conflict")
            else:
                self.db.execute("INSERT INTO owner_questions VALUES (?,?,?,?,?,?,?,?,?,?)", values)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return question_id

    def pending(self, limit=100):
        integer(limit, 1, 100)
        rows = self.db.execute("SELECT * FROM owner_questions WHERE status='pending' ORDER BY priority,id LIMIT ?", (limit,))
        return [dict(row) for row in rows]

    def _verify(self, question, receipt_id, expected_scope):
        ident(receipt_id)
        digest = scope_digest(expected_scope)
        if digest != question["digest"]:
            raise Rejected("scope_mismatch")
        if self.verifier is None:
            raise Rejected("trusted_verifier_unavailable")
        try:
            # MUST resolve against an authenticated authoritative service, including
            # current revocation state. Never trust caller-supplied receipt fields.
            evidence = self.verifier(receipt_id)
        except Exception:
            raise Rejected("verification_unavailable") from None
        now = int(self.clock())
        if (type(evidence) is not VerifiedDecision or
            any(type(getattr(evidence, key)) is not str for key in ("principal", "source_receipt", "question_id", "requester", "scope_sha256", "decision")) or
            evidence.principal != self.owner or evidence.source_receipt != receipt_id or
            evidence.question_id != question["id"] or evidence.requester != question["requester"] or evidence.scope_sha256 != digest or
            evidence.decision not in ("approved", "denied") or type(evidence.revoked) is not bool or
            evidence.revoked or type(evidence.issued_at) is not int or type(evidence.expires_at) is not int or
            not evidence.issued_at <= now < evidence.expires_at):
            raise Rejected("decision_not_verified")
        return evidence

    def _question(self, question_id):
        ident(question_id)
        row = self.db.execute("SELECT * FROM owner_questions WHERE id=?", (question_id,)).fetchone()
        if row is None:
            raise Rejected("question_not_found")
        return row

    def record(self, question_id, receipt_id, expected_scope):
        """Record verified decision. Replay re-verifies but never resets consumption."""
        try:
            self.db.execute("BEGIN IMMEDIATE")
            question = self._question(question_id)
            evidence = self._verify(question, receipt_id, expected_scope)
            encoded = canonical(evidence.__dict__)
            old = self.db.execute("SELECT * FROM owner_receipts WHERE id=?", (receipt_id,)).fetchone()
            if old:
                if old["question"] != question_id or old["evidence"] != encoded:
                    raise Rejected("receipt_conflict")
            elif question["status"] != "pending":
                raise Rejected("question_already_decided")
            else:
                self.db.execute("INSERT INTO owner_receipts(id,question,evidence) VALUES (?,?,?)", (receipt_id, question_id, encoded))
                self.db.execute("UPDATE owner_questions SET status=?,receipt=? WHERE id=?", (evidence.decision, receipt_id, question_id))
            self.db.execute("COMMIT")
            return evidence.decision
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def consume(self, question_id, receipt_id, expected_scope):
        """Return single-use verified evidence, NOT permission to bypass policy gates.

        A lost reply stays consumed. Never automatically retry an external action.
        Revocation can race external execution; caller must perform action-time
        authorization with its platform when required. This method runs no action.
        """
        try:
            self.db.execute("BEGIN IMMEDIATE")
            question = self._question(question_id)
            evidence = self._verify(question, receipt_id, expected_scope)
            row = self.db.execute("SELECT * FROM owner_receipts WHERE id=?", (receipt_id,)).fetchone()
            if (row is None or row["question"] != question_id or row["consumed"] or
                row["evidence"] != canonical(evidence.__dict__) or evidence.decision != "approved"):
                raise Rejected("receipt_not_usable")
            self.db.execute("UPDATE owner_receipts SET consumed=1 WHERE id=?", (receipt_id,))
            self.db.execute("UPDATE owner_questions SET status='consumed' WHERE id=?", (question_id,))
            self.db.execute("COMMIT")
            return evidence
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
