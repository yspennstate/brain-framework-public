"""Whether the heed and ack gates may deny. Enforcement is the default.

The original polarity was fail-open: deleting one marker silently turned a gate off. That
happened twice on 2026-07-25, and the owner explicitly ordered enforcement restored. Marker
files are now informational. Their absence cannot disable either gate.

The named disable token is an operational intent fence, not cryptographic authentication:
all local agents run as the same OS user. An agent must independently verify a direct owner
instruction before writing it. The exact token, authorization sentence, and reason make an
override visible and auditable; a missing, unreadable, or unsigned token leaves enforcement on.
"""
import io
import os
import time


GFDIR = os.path.dirname(os.path.abspath(__file__))
DISABLE = os.path.join(GFDIR, "OWNER_DISABLE_ENFORCEMENT")
TAMPER_LOG = os.path.join(GFDIR, "enforcement_audit.tsv")
AUTHORIZATION = "OWNER AUTHORIZED: disable brain enforcement"
MARKERS = ("HEED_ENABLED", "ACK_ENABLED")


def _audit(event, detail=""):
    try:
        with io.open(TAMPER_LOG, "a", encoding="utf-8") as handle:
            handle.write(
                "%.0f\t%s\t%s\n"
                % (time.time(), event, (detail or "").replace("\t", " ")[:300])
            )
    except Exception:
        pass


def owner_disable_reason(audit=True):
    """Return an explicit owner-authorized disable reason, otherwise None."""
    try:
        if not os.path.exists(DISABLE):
            return None
        with io.open(DISABLE, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except Exception:
        return None
    if AUTHORIZATION in text:
        return text.strip()[:400]
    if audit:
        _audit(
            "disable_attempt_unsigned",
            "disable token lacks the owner's authorization line; enforcement stays LIVE",
        )
    return None


def is_live(audit=True):
    """True unless the explicit disable-token contract is satisfied. Any error stays live."""
    try:
        return owner_disable_reason(audit=audit) is None
    except Exception:
        return True


def restore_markers():
    """Recreate informational markers. Their absence never changes ``is_live``."""
    restored = []
    for name in MARKERS:
        path = os.path.join(GFDIR, name)
        if os.path.exists(path):
            continue
        try:
            with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    "LIVE. Informational marker; enforcement is default-live and cannot be\n"
                    "disabled by deleting this file. An explicit owner-authorized override is\n"
                    "required at graph_fire/OWNER_DISABLE_ENFORCEMENT.\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            restored.append(name)
        except Exception:
            pass
    if restored:
        _audit("markers_restored", ",".join(restored))
    return restored
