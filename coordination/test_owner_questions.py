"""Synthetic authenticated-service substitutes, never real owner decisions."""
import concurrent.futures
from dataclasses import replace
from pathlib import Path
import contextlib
import sqlite3
import tempfile
import unittest
from owner_questions import OwnerQuestions, VerifiedDecision, scope_digest
from hub import Rejected
from client import Outbox


class OwnerQuestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/"questions.sqlite3"
        self.scope = {"action": "review", "target": "synthetic-document", "data": {}, "limits": {"external_sends": 0}}
        self.evidence = VerifiedDecision("synthetic-owner", "receipt-1", "question-1", "alpha", scope_digest(self.scope), "approved", 900, 1100, False)
        self.store = OwnerQuestions(self.path, "synthetic-owner", lambda _: self.evidence, lambda: 1000)
        self.store.submit("alpha", "question-1", "task-1", 4, "Synthetic question", ["Approve", "Deny"], self.scope)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_default_deny_and_verifier_failure(self):
        self.store.verifier = None
        with self.assertRaisesRegex(Rejected, "trusted_verifier_unavailable"):
            self.store.record("question-1", "receipt-1", self.scope)
        def failed(_):
            raise RuntimeError("private-marker")
        self.store.verifier = failed
        with self.assertRaisesRegex(Rejected, "verification_unavailable"):
            self.store.record("question-1", "receipt-1", self.scope)
        self.store.verifier = lambda _: {"principal": "synthetic-owner", "decision": "approved"}
        with self.assertRaises(Rejected):
            self.store.record("question-1", "receipt-1", self.scope)

    def test_priority_and_immutable_questions(self):
        self.store.submit("beta", "question-2", "task-2", 1, "Synthetic urgent question", ["Deny"], self.scope)
        self.assertEqual([q["id"] for q in self.store.pending()], ["question-2", "question-1"])
        self.store.submit("alpha", "question-1", "task-1", 4, "Synthetic question", ["Approve", "Deny"], self.scope)
        with self.assertRaisesRegex(Rejected, "question_id_conflict"):
            self.store.submit("alpha", "question-1", "task-1", 4, "Changed", ["Approve"], self.scope)

    def test_identity_scope_question_requester_and_expiry(self):
        original = self.evidence
        changes = [dict(principal="impostor"), dict(source_receipt="other"), dict(question_id="question-2"),
                   dict(requester="beta"), dict(scope_sha256="wrong"), dict(decision="APPROVED"),
                   dict(issued_at=1001), dict(expires_at=1000), dict(revoked=True), dict(revoked=0), dict(expires_at=True)]
        for change in changes:
            self.evidence = replace(original, **change)
            with self.assertRaises(Rejected, msg=str(change)):
                self.store.record("question-1", "receipt-1", self.scope)
        self.evidence = original
        with self.assertRaisesRegex(Rejected, "scope_mismatch"):
            self.store.record("question-1", "receipt-1", {**self.scope, "target": "different-target"})

    def test_record_replay_consumption_and_restart(self):
        self.assertEqual(self.store.record("question-1", "receipt-1", self.scope), "approved")
        self.assertEqual(self.store.record("question-1", "receipt-1", self.scope), "approved")
        self.assertEqual(self.store.consume("question-1", "receipt-1", self.scope), self.evidence)
        self.store.close()
        self.store = OwnerQuestions(self.path, "synthetic-owner", lambda _: self.evidence, lambda: 1000)
        self.store.record("question-1", "receipt-1", self.scope)
        with self.assertRaisesRegex(Rejected, "receipt_not_usable"):
            self.store.consume("question-1", "receipt-1", self.scope)

    def test_denied_and_revoked_never_usable(self):
        self.evidence = replace(self.evidence, decision="denied")
        self.store.record("question-1", "receipt-1", self.scope)
        with self.assertRaises(Rejected):
            self.store.consume("question-1", "receipt-1", self.scope)

    def test_revocation_rechecked_at_consumption(self):
        self.store.record("question-1", "receipt-1", self.scope)
        self.evidence = replace(self.evidence, revoked=True)
        with self.assertRaises(Rejected):
            self.store.consume("question-1", "receipt-1", self.scope)

    def test_consumption_race_one_winner(self):
        self.store.record("question-1", "receipt-1", self.scope)
        def consume(_):
            store = OwnerQuestions(self.path, "synthetic-owner", lambda _: self.evidence, lambda: 1000)
            try:
                store.consume("question-1", "receipt-1", self.scope)
                return True
            except Rejected:
                return False
            finally:
                store.close()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertEqual(sum(pool.map(consume, range(2))), 1)

    def test_route_initialization_race_and_corrupt_routes(self):
        path = Path(self.tmp.name)/"routes.sqlite3"
        def initialize(destination):
            try:
                box = Outbox(path, destination)
                box.close()
                return True
            except ValueError:
                return False
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertEqual(sum(pool.map(initialize, ("route-a", "route-b"))), 1)
        with contextlib.closing(sqlite3.connect(path)) as db, db:
            self.assertEqual(db.execute("SELECT count(*) FROM route").fetchone()[0], 1)
            db.execute("INSERT INTO route VALUES ('corrupt-route')")
        with self.assertRaisesRegex(ValueError, "outbox_destination_mismatch"):
            Outbox(path, "corrupt-route")


if __name__ == "__main__":
    unittest.main()
