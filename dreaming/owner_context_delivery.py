"""Bounded owner-history emission, serialized per session and committed after stdout.

An emitted packet is not a receiver-side delivery receipt. This helper makes the
sender honest and retryable; the actual harness transcript must still be checked.
No tool argument is treated as a new owner prompt on the pre-tool path.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

# the launcher heads of headless batch workers - the same two the prompt ledger and the checklist skip
_HEADLESS_HEAD = re.compile(r"^\s*(?:﻿)?(?:\[headless-worker\]|You are (?:a|the) picture maker for [^.\n]{0,40}?\bdocumentar(?:y|ies)\b)", re.I)

HISTORY_NOTICE = ("[owner-prompts] Historical owner context: current instructions and "
                  "the current task's scope take precedence.\n")


def byte_length(text):
    return len(text.encode("utf-8"))


@contextmanager
def session_lock(path, timeout=1.0):
    """An OS lock releases on process death; no stale lockfile needs stealing."""
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    if handle.seek(0, 2) == 0:
        handle.write(b"\0")
        handle.flush()
    locked = False
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("owner context session is busy")
                time.sleep(0.01)
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def save_state_atomic(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    raw = (json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fit_bytes(engine, text, maximum):
    if byte_length(text) <= maximum:
        return text, None
    limit = min(len(text) - 1, maximum)
    while limit > 0:
        first, rest = engine.refit(text, limit)
        size = byte_length(first)
        if size <= maximum:
            return first, rest
        reduced = int(limit * maximum / size) - 8
        limit = min(limit - 1, reduced)
    raise ValueError("owner context header exceeds the available budget")


# THE TOKEN BUDGET (8 Oct 2026: the prompt history must not waste
# tokens). Every tool step drained the next 9.5 KB part of the hourly block into the context - 71 parts an hour - and
# every later turn re-read them: on 7 Oct 63 pct of a coordinator's recent context was this hook. The knob file caps it
# until the important mode lands: tool_drain false = parts arrive with his prompts only; max_parts N = a block reaches
# the session as its first N parts (what he keeps saying, then the session's own threads) and the rest stays on disk.
# Absent or unreadable file = the delivery as before.
BUDGET_FILE = os.environ.get("OWNER_PROMPTS_BUDGET_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "owner_prompts_budget.json")
BUDGET_NOTE = ("[owner-prompts] %d more parts of this block were not sent, to save tokens (token budget, 8 Oct). "
               "His full text stays on disk: python ~/.claude/dreaming/owner_prompts_hook.py --show "
               "\"<thread>\" --hours 48 (or --show 100 for his last hundred asks).\n")


def _budget():
    try:
        with open(BUDGET_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _apply_budget(cfg, parts, state):
    """Cut the session's queue to the block's first max_parts parts; the cut is counted on the state and named once."""
    try:
        mp = int(cfg.get("max_parts") or 0)
    except (TypeError, ValueError):
        mp = 0
    pend = state.get("pending") if isinstance(state, dict) else None
    if mp < 1 or not isinstance(pend, list) or len(pend) <= mp - 1:
        return parts
    keep = list(pend[:mp - 1])
    dropped = len(pend) - len(keep)
    state["budget_dropped"] = int(state.get("budget_dropped") or 0) + dropped
    parts = [p.replace("(remaining parts follow during prompts and active tool steps)",
                       "(only the first %d are sent, to save tokens; see the note at the end)" % mp) if p else p
             for p in parts]
    if keep:
        keep[-1] = keep[-1].rstrip("\n") + "\n" + BUDGET_NOTE % dropped
        state["pending"] = keep
        return parts
    state["pending"] = []
    return list(parts) + [BUDGET_NOTE % dropped] if any(parts) else parts


def emit(engine, payload, runtime, writer, *, base_output=None, budget=9000, now=None):
    """Write a complete hook result; advance its cursor only after a successful flush.

    Return True only when this helper wrote stdout. False leaves the caller's
    existing safety/reflex output entirely in its hands. Writer is a text stream.
    """
    if not isinstance(payload, dict):
        return False
    event = str(payload.get("hook_event_name") or payload.get("event") or "UserPromptSubmit")
    if event not in {"UserPromptSubmit", "SessionStart", "PreToolUse"}:
        return False
    session = engine.session_id(payload, runtime)
    if not session:
        return False
    # A SUBAGENT'S TOOL STEP IS NOT THE SESSION'S (2026-09-23, round 6). A subagent's tool calls fire the parent's
    # PreToolUse hooks under the parent's session id, so each of them drained the next part of the PARENT's queue into
    # the subagent. Measured on the 06:07 delivery of session 96cda5f6: of 24 parts the session itself received 10;
    # its two test subagents took 11 and 3 - history the session never saw, pushed into a control arm that was meant to
    # have none. A tool step that names an agent (agent_id / agent_type, or a transcript under .../subagents/) is left
    # alone; the parent's next prompt or tool step takes the part instead.
    if event == "PreToolUse":
        _tp = str(payload.get("transcript_path") or payload.get("agent_transcript_path") or "").replace("\\", "/")
        _why = ("agent_id" if payload.get("agent_id") else "agent_type" if payload.get("agent_type")
                else "subagent transcript" if "/subagents/" in _tp else "")
        if _why:
            try:
                engine._log("%s PreToolUse %s skipped: subagent tool step (%s)" % (runtime, str(session)[:8], _why))
            except Exception:
                pass
            return False
    cfg = _budget()
    if event == "PreToolUse" and cfg.get("tool_drain") is False:
        return False
    base = dict(base_output or {})
    hso = dict(base.get("hookSpecificOutput") or {})
    if hso.get("permissionDecision") in {"deny", "ask"} or base.get("decision") == "block":
        return False
    existing = hso.get("additionalContext") or ""
    if not isinstance(existing, str):
        return False
    separator = "\n" if existing else ""
    available = min(engine.CHUNK_MAX, budget - byte_length(existing + separator + HISTORY_NOTICE))
    if available < engine.MIN_PART:
        return False
    # Brain-off benchmark sessions must not acquire history through a side door.
    try:
        import brain_switch
        if brain_switch.is_offline(session):
            return False
    except ImportError:
        pass
    prompt = "" if event == "PreToolUse" else str(
        payload.get("prompt") or payload.get("user_prompt") or payload.get("text") or "")
    when = time.time() if now is None else now
    try:
        with session_lock(engine.state_path(session), timeout=1.0):
            original = engine.load_state(session)
            # Tool events drain a known session; they do not invent a wake from
            # arbitrary tool text or mutate another unknown session's queue.
            if event == "PreToolUse" and not original:
                return False
            # A HEADLESS WORKER IS NOT AN AGENT THAT NEEDS HIS HISTORY (2026-09-23, round 6). The world-history picture
            # workers start one `codex exec` per batch of five pictures; each batch session was handed the whole
            # hourly block, ~9 KB per tool step ("pending=22" at 06:17), about 200 KB of his prompts per batch across
            # ~150 batches a night, to a worker whose only job is to draw. The launcher head that the ledger and the
            # checklist already skip (memory headless_codex_batches_were_recorded_as_his_asks_2026_09_23) marks the
            # session headless on its first prompt; its tool steps then drain nothing. OWNER_PROMPTS_HEADLESS_SKIP=0
            # restores the old behaviour.
            if os.environ.get("OWNER_PROMPTS_HEADLESS_SKIP", "1") != "0":
                if isinstance(original, dict) and original.get("headless"):
                    return False
                if event == "UserPromptSubmit" and _HEADLESS_HEAD.match(prompt or ""):
                    save_state_atomic(engine.state_path(session), {"headless": True, "since": when})
                    try:
                        engine._log("%s %s %s skipped: headless worker session" % (runtime, event, str(session)[:8]))
                    except Exception:
                        pass
                    return False
            try:
                from prompt_archive import redact_tree
                # Old queues may contain text saved before a redaction repair.
                # Filter before decide/refit can split a credential from its label.
                original = redact_tree(original)
                prompt = redact_tree(prompt)
            except Exception:
                engine._log("owner context withheld: history redaction unavailable")
                return False
            previous_limit = engine.CHUNK_MAX
            try:
                engine.CHUNK_MAX = min(previous_limit, available)
                # the transcript lets the context schedule see a compaction (owner_prompts_hook.SCHEDULE_FILE)
                parts, state = engine.decide(event, session, prompt, original, when,
                                             transcript=str(payload.get("transcript_path") or ""), runtime=runtime)
            finally:
                engine.CHUNK_MAX = previous_limit
            parts = _apply_budget(cfg, parts, state)
            try:
                text = redact_tree("".join(part for part in parts if part))
                state = redact_tree(state)
            except Exception:
                engine._log("owner context withheld: history redaction failed")
                return False
            keep_quiet_change = bool(state.pop("_save", None))
            if not text:
                # a fire that printed nothing but moved a clock (the context schedule's hour, its shadow, a Codex
                # rollout found) keeps the change; any other silent fire writes nothing, as before
                if keep_quiet_change:
                    save_state_atomic(engine.state_path(session), state)
                return False
            text, rest = fit_bytes(engine, text, available)
            if rest:
                state["pending"] = [rest] + list(state.get("pending") or [])
            context = existing + separator + HISTORY_NOTICE + text
            if byte_length(context) > budget:
                raise ValueError("owner context exceeded the output budget")
            hso.update({"hookEventName": event, "additionalContext": context})
            base["hookSpecificOutput"] = hso
            rendered = (json.dumps(base, ensure_ascii=False) if runtime == "codex" or event == "PreToolUse"
                        else context)
            writer.write(rendered)
            writer.flush()
            state["last_emission"] = {"event": event, "utc_epoch": when,
                                      "utf8_bytes": byte_length(context),
                                      "context_sha256": hashlib.sha256(context.encode("utf-8")).hexdigest(),
                                      "remaining_parts": len(state.get("pending") or []),
                                      "evidence_stage": "emitted_stdout_not_receiver_verified"}
            save_state_atomic(engine.state_path(session), state)
            engine._log("%s %s %s %dB %s pending=%d emitted_stdout" % (
                runtime, event, session[:8], byte_length(context), state.get("why", ""),
                len(state.get("pending") or [])))
            return True
    except TimeoutError:
        engine._log("%s %s %s deferred: session delivery lock busy" % (runtime, event, session[:8]))
        return False
