#!/usr/bin/env python3
"""ack_gate.py - a one-time token-registration gate for explicit warning engagement.

The second heed methodology (owner, 2026-07-25 evening), complementary to heed_gate.py.
heed_gate is the hard speed bump for EVIDENCE-LINKED REPEATS (read past 2+ times, command
linked to a recent real failure). This gate covers the FIRST encounter, and its mechanism is
different: instead of only delivering a warning, it asks the agent to PRODUCE a bounded
connection in its own words. Token registration does not establish comprehension,
downstream execution, a safe or correct outcome, or a causal effect.

Protocol: when a top-charge ACT-HAZARD reflex fires on a real act and this (session, reflex)
has not been acknowledged, deny ONCE with a short instruction: re-issue the same command with
an inert comment line

    # heeded:<reflex_id> <one line: how the scar applies here, or why it does not>

The gate validates the signature mechanically (no model call, one round-trip) and registers the
token; even a weak rationale unlocks this gate. Other hooks, tool execution, and outcome
verification remain separate. ack_log.tsv is best-effort registration telemetry, and rationale
quality is currently unjudged. A voluntary pre-signed command (token present on first attempt)
is registered and never denied by this gate.

Hard laws inherited from heed_gate: the (session, reflex) claim persists BEFORE any deny, so a
retry can never be blocked twice; each session starts with two deny tokens, replenishes one token
per hour, and can never accumulate a burst larger than two; there is no lifetime shutoff. Only
uuid-shaped sessions persist state (replay/fixture purity); ANY error -> allow. Without the
explicit owner-authorized disable contract, enforcement is live by default. ACK_ENABLED is an
informational marker, not a delete-to-disable switch.
"""
import io
import json
import math
import os
import re
import time
import uuid
from contextlib import contextmanager

GFDIR = os.path.dirname(os.path.abspath(__file__))
ENABLED = os.path.join(GFDIR, "ACK_ENABLED")     # informational; see _enforcement_live()
STATE = os.path.join(GFDIR, ".ack_state.json")
LOG = os.path.join(GFDIR, "ack_log.tsv")         # ts, session, reflex, mode, event, text

MIN_CHARGE = 0.95
MAX_DENIES_PER_SESSION = 2       # token-bucket capacity; retained for compatibility
DENY_REPLENISH_S = 3600.0
TOKEN = re.compile(r"heeded:([a-z0-9_]{3,64})\b[ \t]*([^\r\n]{8,240})", re.I)
_UUID = re.compile(r"^[0-9a-f]{8}-")
_LOCK_TIMEOUT_S = 0.25
_BUDGET_MODE = "budget"
_BUDGET_SUFFIX = "|@deny_budget"


def _enforcement_live():
    """Default live. A missing/broken control module cannot silently disable the gate."""
    try:
        import enforcement
        return enforcement.is_live()
    except Exception:
        return True

# act-hazard scope mirrors heed_gate: a deny is an act-surface intervention. Fall back to a
# frozen core set if the classifier is unavailable - fail toward FEWER denies, never more.
_CORE_ACT_HAZARDS = frozenset((
    "no_local_privileged_stack", "secrets_in_git", "preserve_before_destroy", "lookahead",
    "concurrent_races", "his_never_is_machinery", "no_flashing_windows", "windowless",
    "pinned_file_two_tripwires", "gate_tool_needs_env", "healer_first_do_no_harm",
))


def _is_act_hazard(reflex_id):
    try:
        import habituation_filter
        return habituation_filter.danger_surface(reflex_id) == "act"
    except Exception:
        return reflex_id in _CORE_ACT_HAZARDS


def _lock_byte(handle, unlock=False):
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        mode = msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK
        msvcrt.locking(handle.fileno(), mode, 1)
    else:
        import fcntl
        mode = fcntl.LOCK_UN if unlock else (fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle.fileno(), mode)


@contextmanager
def _state_lock():
    """Cross-process advisory lock. Failure/timeout yields False so the gate fails open."""
    handle = None
    acquired = False
    try:
        handle = open(STATE + ".lock", "a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + _LOCK_TIMEOUT_S
        while True:
            try:
                _lock_byte(handle)
                acquired = True
                break
            except (OSError, IOError):
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.005)
    except Exception:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        yield False
        return
    try:
        yield acquired
    finally:
        if acquired and handle is not None:
            try:
                _lock_byte(handle, unlock=True)
            except Exception:
                pass
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


def _load_state_locked():
    """Return a dict, or None for corrupt/unreadable state. Never recast corruption as empty."""
    if not os.path.exists(STATE):
        return {}
    try:
        state = json.load(io.open(STATE, encoding="utf-8"))
        if not isinstance(state, dict):
            return None
        for key, value in state.items():
            if not isinstance(key, str) or not isinstance(value, list) or len(value) < 3:
                return None
            if isinstance(value[0], bool):
                return None
            stamp = float(value[0])
            if not math.isfinite(stamp):
                return None
            mode = value[1]
            if mode == _BUDGET_MODE:
                if not key.endswith(_BUDGET_SUFFIX) or isinstance(value[2], bool):
                    return None
                tokens = float(value[2])
                if not math.isfinite(tokens) or not 0.0 <= tokens <= MAX_DENIES_PER_SESSION:
                    return None
            elif mode in ("denied", "acked"):
                if key.endswith(_BUDGET_SUFFIX) or not isinstance(value[2], str):
                    return None
            else:
                return None
        return state
    except Exception:
        return None


def _save_state_locked(state, session, now=None):
    """Durably replace state while the caller holds _state_lock."""
    if not _UUID.match(session or ""):
        return False
    try:
        clock = time.time() if now is None else float(now)
        if not math.isfinite(clock):
            return False
        state = {k: v for k, v in state.items()
                 if isinstance(v, list) and v and clock - float(v[0]) < 3 * 86400}
        tmp = STATE + ".tmp-%d-%s" % (os.getpid(), uuid.uuid4().hex)
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(state, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, STATE)
        return True
    except Exception:
        return False


def _deny_tokens(state, session, now):
    """Return this session's refilled deny tokens, migrating a legacy ledger in memory."""
    budget_key = session + _BUDGET_SUFFIX
    budget = state.get(budget_key)
    if budget is not None:
        last = float(budget[0])
        tokens = float(budget[2])
        elapsed = max(0.0, now - last)
        return min(float(MAX_DENIES_PER_SESSION), tokens + elapsed / DENY_REPLENISH_S)

    # Legacy ledgers had no budget row. Replay their still-live unacknowledged denies so an
    # upgrade cannot reset a spent session to a fresh two-stop burst. ACK rows are intentionally
    # excluded: they overwrote the matching legacy deny and therefore carry no trustworthy
    # consumption history.
    denied_at = sorted(
        float(value[0])
        for key, value in state.items()
        if key.startswith(session + "|")
        and key != budget_key
        and value[1] == "denied"
    )
    if not denied_at:
        return float(MAX_DENIES_PER_SESSION)
    tokens = float(MAX_DENIES_PER_SESSION)
    last = denied_at[0]
    for stamp in denied_at:
        tokens = min(
            float(MAX_DENIES_PER_SESSION),
            tokens + max(0.0, stamp - last) / DENY_REPLENISH_S,
        )
        tokens = max(0.0, tokens - 1.0)
        last = max(last, stamp)
    return min(
        float(MAX_DENIES_PER_SESSION),
        tokens + max(0.0, now - last) / DENY_REPLENISH_S,
    )


def _log(session, reflex, mode, event, text):
    try:
        if not _UUID.match(session or ""):
            return
        snip = re.sub(r"sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----",
                      "<masked>", text or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")[:200]
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write("%.0f\t%s\t%s\t%s\t%s\t%s\n" % (time.time(), session[:12], reflex, mode, event, snip))
    except Exception:
        pass


def _command_text(data):
    ti = (data or {}).get("tool_input", {}) or {}
    cmd = ti.get("command", "")
    if isinstance(cmd, str) and cmd:
        return cmd
    return ""


def _candidate(fired):
    """The first rendered reflex that can actually govern this command."""
    if not isinstance(fired, list):
        return None, ""
    for item in fired:
        try:
            rid = (item.get("id") or "").lower()
            if rid and float(item.get("charge", 0)) >= MIN_CHARGE and _is_act_hazard(rid):
                return item, rid
        except Exception:
            continue
    return None, ""


def evaluate(event, data, fired, now=None):
    """None to allow, or {"decision": "deny", "reason": str}. Fail-safe: ANY error -> None."""
    try:
        clock = time.time() if now is None else float(now)
        if not math.isfinite(clock):
            return None
        if event != "PreToolUse":
            return None
        session = (data or {}).get("session_id", "") or ""
        if not _UUID.match(session):
            return None
        cmd = _command_text(data)
        # only a real act with a command surface can carry the signature protocol
        tn = (data or {}).get("tool_name", "")
        if tn not in ("Bash", "PowerShell") or not cmd:
            return None
        top, rid = _candidate(fired) if fired else (None, "")
        # INVARIANT (2026-07-28): a signed retry MUST register even when `fired` is empty or
        # lists other reflexes. The deny's own fire spends instinct_prime's 600s dedup window,
        # so the STANDARD compliant retry arrives with its reflex absent from `fired`; the old
        # `not fired -> return` guard made the ledger blind to exactly the behaviour this gate
        # exists to produce (measured 07-28: 3/3 signed retries in one session, 0 credited;
        # the fleet 'signed rationale' rate read 18% off this artifact). A token registers ONLY
        # against this session's OWN existing denied claim, or the live candidate - junk tokens
        # for arbitrary ids stay inert, and an unrelated token still cannot bypass a fresh deny.
        if not top and not TOKEN.search(cmd):
            return None
        key = (session + "|" + rid) if rid else ""
        live = _enforcement_live()

        # Read, check, and claim are one cross-process transaction. A corrupt ledger is preserved
        # byte-for-byte and the action is allowed; treating corruption as {} caused both evidence
        # loss and repeated denies.
        with _state_lock() as locked:
            if not locked:
                return None
            state = _load_state_locked()
            if state is None:
                return None

            # A signature acknowledges only the applicable reflex: the live candidate, or a
            # PRIOR deny of this same session whose reflex did not re-fire (dedup window).
            # An unrelated valid token is inert; it cannot bypass a first deny for another scar.
            rationale = None
            signed_prior = []
            for match in TOKEN.finditer(cmd):
                trid = match.group(1).lower()
                if rid and trid == rid:
                    if rationale is None:
                        rationale = match.group(2).strip()
                    continue
                prev = state.get(session + "|" + trid)
                if prev is not None and prev[1] == "denied" and \
                        all(trid != t for t, _ in signed_prior):
                    signed_prior.append((trid, match.group(2).strip()))
            registered = list(signed_prior)
            if rationale is not None and rid:
                registered.append((rid, rationale))
            if registered:
                # Preserve the consumption represented by a legacy denied row before ACK
                # overwrites it. ACK never refunds a token.
                budget_key = session + _BUDGET_SUFFIX
                for trid, text in registered:
                    tkey = session + "|" + trid
                    if (
                        budget_key not in state
                        and tkey in state
                        and state[tkey][1] == "denied"
                    ):
                        state[budget_key] = [clock, _BUDGET_MODE, _deny_tokens(state, session, clock)]
                    state[tkey] = [clock, "acked", (text or "")[:160]]
                if not _save_state_locked(state, session, now=clock):
                    return None
                for trid, text in registered:
                    _log(session, trid, "live" if live else "shadow", "ack", text)
                if rationale is not None:
                    return None
            if not top:
                return None   # token-only registration: nothing fired to deny
            if key in state:
                return None   # already acknowledged or denied once - never a second stop
            tokens = _deny_tokens(state, session, clock)
            if tokens < 1.0:
                return None

            # Claim and token debit are one durable transaction BEFORE deny. A failed persist
            # means no deny (a broken disk can never loop).
            state[key] = [clock, "denied", ""]
            state[session + _BUDGET_SUFFIX] = [clock, _BUDGET_MODE, tokens - 1.0]
            if not _save_state_locked(state, session, now=clock):
                return None

        _log(session, rid, "live" if live else "shadow", "deny" if live else "would_deny", cmd)
        if not live:
            return None
        gut = (top.get("gut") or "")[:220]
        return {"decision": "deny",
                "reason": ("[ack-gate] ACK GATE (one-time): the scar '%s' fired on this exact action and has not been "
                           "answered. Re-run the SAME command adding one inert comment line:  # heeded:%s "
                           "<one line: how this scar applies here, or why it does not>.  A valid token "
                           "registers this gate's acknowledgment; downstream hooks, tool execution, and "
                           "outcome verification remain separate. The scar: %s" % (rid, rid, gut))}
    except Exception:
        return None
