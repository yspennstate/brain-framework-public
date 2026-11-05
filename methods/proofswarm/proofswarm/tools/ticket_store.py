"""Local JSON ticket persistence.  Remote storage is intentionally unavailable."""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterator, Optional

from proofswarm.config import LOCAL_TICKET_DIR, STORAGE_BACKEND
from proofswarm.schemas import ProofTicket


if STORAGE_BACKEND != "local":  # defensive: config already rejects this
    raise RuntimeError("ProofSwarm ticket storage must remain local")

_LOCAL_DIR = LOCAL_TICKET_DIR.resolve()
MAX_TICKET_BYTES = 8 * 1024 * 1024


class TicketStoreConflict(RuntimeError):
    """A ticket write was stale, concurrent, corrupt, or too large."""


def _sanitize(ticket_id: str) -> str:
    """Replace filesystem-unsafe chars in ticket IDs."""

    return ticket_id.replace(":", "_").replace("/", "_").replace("\\", "_")


def _ticket_path(ticket_id: str) -> Path:
    """Use an injective content address instead of a collision-prone sanitized ID."""

    digest = hashlib.sha256(ticket_id.encode("utf-8")).hexdigest()
    return _LOCAL_DIR / f"ticket-{digest}.json"


def _legacy_ticket_path(ticket_id: str) -> Path:
    return _LOCAL_DIR / f"{_sanitize(ticket_id)}.json"


def _read_ticket_file(path: Path) -> ProofTicket:
    raw = path.read_bytes()
    if len(raw) > MAX_TICKET_BYTES:
        raise TicketStoreConflict(f"ticket file exceeds {MAX_TICKET_BYTES} bytes: {path.name}")
    return ProofTicket(**json.loads(raw))


def _current_ticket(ticket_id: str) -> Optional[ProofTicket]:
    current_path = _ticket_path(ticket_id)
    if current_path.exists():
        current = _read_ticket_file(current_path)
        if current.ticket_id != ticket_id:
            raise TicketStoreConflict("content-addressed ticket ID mismatch")
        return current
    legacy_path = _legacy_ticket_path(ticket_id)
    if legacy_path.exists():
        legacy = _read_ticket_file(legacy_path)
        if legacy.ticket_id == ticket_id:
            return legacy
    return None


@contextmanager
def _exclusive_ticket_lock(ticket_id: str) -> Iterator[None]:
    """Acquire one nonblocking machine-wide write lock for a ticket ID."""

    lock_dir = _LOCAL_DIR / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(ticket_id.encode("utf-8")).hexdigest()
    handle = (lock_dir / f"ticket-{digest}.lock").open("a+b")
    acquired = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise TicketStoreConflict(
                f"ticket {ticket_id!r} has another active local writer"
            ) from exc
        yield
    finally:
        try:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()


def save_ticket(ticket: ProofTicket) -> None:
    """CAS-write one local ticket under a machine lock; never contact remote storage."""

    _LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    with _exclusive_ticket_lock(ticket.ticket_id):
        current = _current_ticket(ticket.ticket_id)
        if current is None:
            if ticket.store_revision != 0:
                raise TicketStoreConflict("ticket disappeared before a revisioned write")
            next_revision = 1
        else:
            if current.store_revision != ticket.store_revision:
                raise TicketStoreConflict(
                    f"stale ticket revision {ticket.store_revision}; "
                    f"current revision is {current.store_revision}"
                )
            next_revision = current.store_revision + 1

        candidate = ticket.model_copy(update={"store_revision": next_revision})
        payload = candidate.model_dump_json(indent=2).encode("utf-8")
        if len(payload) > MAX_TICKET_BYTES:
            raise TicketStoreConflict(
                f"ticket exceeds the fixed {MAX_TICKET_BYTES}-byte persistence bound"
            )
        target = _ticket_path(ticket.ticket_id)
        with NamedTemporaryFile(
            mode="wb", prefix=f".{target.stem}.", suffix=".tmp", dir=_LOCAL_DIR, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        ticket.store_revision = next_revision


def load_ticket(ticket_id: str) -> Optional[ProofTicket]:
    return _current_ticket(ticket_id)


def list_tickets(problem_id: Optional[str] = None) -> list[ProofTicket]:
    if not _LOCAL_DIR.is_dir():
        return []
    by_id: dict[str, tuple[bool, ProofTicket]] = {}
    for path in sorted(_LOCAL_DIR.glob("*.json")):
        ticket = _read_ticket_file(path)
        if problem_id is None or ticket.problem_id == problem_id:
            is_current_path = path.name == _ticket_path(ticket.ticket_id).name
            previous = by_id.get(ticket.ticket_id)
            if previous is None or (is_current_path and not previous[0]):
                by_id[ticket.ticket_id] = (is_current_path, ticket)
    return [by_id[ticket_id][1] for ticket_id in sorted(by_id)]
