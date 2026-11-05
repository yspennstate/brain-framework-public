"""Explicitly authorized email boundary. No inbox reading or automatic delivery."""
from dataclasses import dataclass
from email.message import EmailMessage
import hashlib
import re
import sqlite3


def message_bytes(sender, recipient, subject, body):
    # Deliberately narrow single-mailbox syntax: no display names or recipient lists.
    address = r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}"
    if not all(isinstance(v, str) and re.fullmatch(address, v) for v in (sender, recipient)):
        raise ValueError("invalid_mailbox")
    if not isinstance(subject, str) or len(subject) > 200 or any(ord(c) < 32 for c in subject):
        raise ValueError("invalid_subject")
    if not isinstance(body, str) or len(body) > 8000:
        raise ValueError("invalid_body")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, recipient, subject
    message.set_content(body)
    return message.as_bytes()


@dataclass(frozen=True)
class Approval:
    """Created by trusted application AFTER user approves these exact bytes."""
    recipient: str
    sha256: str


class EmailAdapter:
    def __init__(self, allowed_recipients, ledger_path, deliver=None):
        self.allowed = frozenset(allowed_recipients)
        self.deliver = deliver  # Existing authorized mail integration; no auto setup.
        self.ledger = sqlite3.connect(ledger_path)
        self.ledger.execute("CREATE TABLE IF NOT EXISTS attempted(digest TEXT PRIMARY KEY)")
        self.ledger.commit()

    def close(self):
        self.ledger.close()

    def prepare(self, sender, recipient, subject, body):
        if recipient not in self.allowed:
            raise ValueError("recipient_not_allowed")
        raw = message_bytes(sender, recipient, subject, body)
        return raw, hashlib.sha256(raw).hexdigest()

    def send(self, sender, recipient, subject, body, *, approval=None, dry_run=True):
        raw, digest = self.prepare(sender, recipient, subject, body)
        if dry_run:
            return {"status": "dry_run", "sha256": digest, "message": raw}
        if approval != Approval(recipient, digest) or self.deliver is None:
            raise ValueError("explicit_approval_required")
        # Consume BEFORE invoking: a failed transport may already have delivered.
        # No automatic retries. Persist uncertain outcomes in the integrating app.
        try:
            self.ledger.execute("INSERT INTO attempted VALUES (?)", (digest,))
            self.ledger.commit()
        except sqlite3.IntegrityError:
            raise ValueError("approval_already_consumed") from None
        try:
            self.deliver(raw)
        except Exception:
            raise RuntimeError("delivery_outcome_unknown_do_not_retry") from None
        return {"status": "submitted", "sha256": digest}
