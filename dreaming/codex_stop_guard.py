#!/usr/bin/env python3
"""Turn-bound Codex Stop guard for owner-correction carry reflexes.

done_means_verified checks strong completion claims that lack direct evidence or
an unverified disclosure. keep_working checks a final/idle response under an
explicit timer or the one generation created by a bare keep-working prompt.

Per-session hashed state contains only generations, deadlines, and fired
markers—never prompt/response text. State is saved before block,
stop_hook_active passes except while the outgoing text still contains the
explicitly incomplete-financial-scope contradiction, and every error fails open.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Optional

HERE = Path(__file__).resolve().parent
STATE_DIR = Path(os.environ.get("CODEX_STOP_GUARD_STATE_DIR") or HERE / ".codex_stop_guard")
MAX_PAYLOAD = 512 * 1024
MAX_STATE = 64 * 1024
MAX_CONTRACT_SECONDS = 48 * 60 * 60
STATE_TTL_SECONDS = 7 * 24 * 60 * 60
CONTINUATION_PREFIX = "Owner-correction lifecycle warning:"
OWNER_REPORT_CONTRACT = HERE / "owner_report_contract.py"

_DURATION = re.compile(
    r"\bfor\s+(?:the\s+)?next\s+(\d+(?:\.\d+)?)\s*"
    r"(hours?|hrs?|hr|minutes?|mins?|min)\b", re.I)
_STANDING = re.compile(
    r"(?:\bkeep\s+working\b|\bcontinue\s+working\b|"
    r"\bdo\s+not\s+stop(?:\s+working)?\b|\bdon'?t\s+stop(?:\s+working)?\b|"
    r"\bcontinuously\b|\bevery\s+second\b|"
    r"\b(?:work|working|continue\s+working)\s+(?:until|through)\b|\btimed[- ]work\b)", re.I)
_CANCEL = re.compile(
    r"(?:^\s*(?:please\s+)?(?:stop|pause|cancel)\b|"
    r"\b(?:you\s+(?:can|may)|let'?s)\s+(?:stop|pause|cancel)\b)", re.I)
_COMPLETION = re.compile(
    r"\b(?:(?:it'?s|it\s+is|task|work|everything|all|implementation|fix|audit|verification)"
    r"\s+(?:is\s+|are\s+)?(?:done|complete|completed|finished|fully\s+verified)|"
    r"fully\s+verified|all\s+done)\b", re.I)
_NEGATION = re.compile(
    r"\b(?:not|isn'?t|wasn'?t|never|pending|remain(?:s|ing)?|not\s+yet)\b", re.I)
_VERIFICATION_DISCLOSURE = re.compile(
    r"(?:\bverified\s+end[- ]to[- ]end\b|\bend[- ]to[- ]end\s+verified\b|"
    r"\bpersonally\s+verified\b|\b(?:unverified|not\s+verified|not\s+checked)\b|"
    r"\b(?:did\s+not|didn'?t)\s+(?:verify|check)\b|"
    r"\bremaining\s+(?:gap|risk|uncertaint|unverified))", re.I)
_PERSONAL_EVIDENCE = re.compile(
    r"\bI\s+(?:ran|opened|read|observed|inspected|reproduced|verified|tested|checked)\b"
    r"[^.\n]{0,140}\b(?:test|command|file|output|result|runtime|ui|page|endpoint|live|directly|myself)\b",
    re.I)
_IDLE = re.compile(
    r"(?:\blet\s+me\s+know\b|\bawait(?:ing)?\s+(?:direction|instructions?)\b|"
    r"\bstand(?:ing)?\s+by\b|\bfor\s+now\b|"
    r"\bif\s+you(?:'d)?\s+like,?\s+i\s+can\b|\bi\s+can\s+continue\s+if\b|"
    r"\bready\s+when\s+you\s+are\b|\bpoint\s+me\s+to\b)", re.I)


def _session_token(payload: Mapping[str, Any]) -> str:
    raw = str(payload.get("session_id") or payload.get("thread_id")
              or payload.get("session") or payload.get("transcript_path") or "")
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:32] if raw else ""


def _state_path(token: str, state_dir: Path) -> Path:
    return state_dir / (token + ".json")


def _load(token: str, state_dir: Path, now: float) -> dict[str, Any]:
    path = _state_path(token, state_dir)
    try:
        if path.stat().st_size > MAX_STATE:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema") != 1:
            return {}
        if now - float(value.get("updated_at") or 0.0) > STATE_TTL_SECONDS:
            return {}
        return value
    except Exception:
        return {}


def _prune(state_dir: Path, now: float) -> None:
    try:
        cutoff = now - STATE_TTL_SECONDS
        for path in list(state_dir.glob("*.json"))[:64]:
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
    except Exception:
        pass


def _save(token: str, state: Mapping[str, Any], state_dir: Path, now: float) -> bool:
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        path = _state_path(token, state_dir)
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(state_dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(dict(state), handle, ensure_ascii=True, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
        _prune(state_dir, now)
        return True
    except Exception:
        return False


def _duration_deadline(prompt: str, now: float) -> float:
    seconds = 0.0
    for amount, unit in _DURATION.findall(prompt):
        value = float(amount)
        seconds = max(seconds, value * (3600.0 if unit.lower().startswith(("h", "hr")) else 60.0))
    return now + min(seconds, MAX_CONTRACT_SECONDS) if seconds else 0.0


def _completion_claim(text: str) -> bool:
    for match in _COMPLETION.finditer(text):
        prefix = text[max(0, match.start() - 28):match.start()]
        if not _NEGATION.search(prefix):
            return True
    return False


def _has_verification_disclosure(text: str) -> bool:
    return bool(_VERIFICATION_DISCLOSURE.search(text) or _PERSONAL_EVIDENCE.search(text))


def _durable_report_result(text: str) -> dict[str, Any]:
    """Return one current-frame owner invariant, never a remembered numeric oracle."""
    try:
        spec = importlib.util.spec_from_file_location("_codex_owner_report_contract",
                                                      OWNER_REPORT_CONTRACT)
        if spec is None or spec.loader is None:
            return {}
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.check(text)
        if not isinstance(result, dict):
            return {}
        code = str(result.get("code") or "")
        reason = str(result.get("reason") or "")
        if not code or not reason:
            return {}
        persistent = bool(getattr(module, "is_persistent_code", lambda value: False)(code))
        return {"code": code, "reason": reason, "persistent": persistent}
    except Exception:
        return {}


def _record_prompt(payload: Mapping[str, Any], token: str, state_dir: Path, now: float) -> None:
    prompt = str(payload.get("prompt") or payload.get("text") or "")
    if CONTINUATION_PREFIX in prompt:
        return
    state = _load(token, state_dir, now)
    generation = int(state.get("generation") or 0) + 1
    state.update({"schema": 1, "generation": generation, "updated_at": now})
    fired = state.get("fired") if isinstance(state.get("fired"), dict) else {}

    if _CANCEL.search(prompt):
        state["timed_until"] = 0.0
        state["keep_generation"] = 0
        state["contract_kind"] = ""
    else:
        deadline = _duration_deadline(prompt, now)
        if deadline:
            existing = float(state.get("timed_until") or 0.0)
            state["timed_until"] = max(existing if existing > now else 0.0, deadline)
            state["keep_generation"] = generation
            state["contract_kind"] = "explicit-duration"
            fired.pop("keep_working", None)
        elif _STANDING.search(prompt):
            state["keep_generation"] = generation
            if not (state.get("contract_kind") == "explicit-duration"
                    and now < float(state.get("timed_until") or 0.0)):
                state["timed_until"] = 0.0
                state["contract_kind"] = "standing-once"
            fired.pop("keep_working", None)
    state["fired"] = fired
    _save(token, state, state_dir, now)


def _brain_wake_prompt(payload: Mapping[str, Any], wake_state_path: Optional[Path]):
    try:
        import codex_brain_wake_guard
        return codex_brain_wake_guard.prompt_output(payload, state_path=wake_state_path)
    except Exception as exc:
        detail = "%s: %s" % (type(exc).__name__, str(exc)[:500])
        return {"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                "--- BRAIN SELF-CARE: OWNER ALERT REQUIRED NOW ---\n"
                "The Codex brain-wake guard itself failed (%s). Tell the owner in the first "
                "user-visible update; do not silently call this wake healthy." % detail),
        }}


def _brain_wake_stop_reason(payload: Mapping[str, Any], wake_state_path: Optional[Path]) -> str:
    try:
        import codex_brain_wake_guard
        return codex_brain_wake_guard.stop_block_reason(payload, state_path=wake_state_path)
    except Exception as exc:
        text = str(payload.get("last_assistant_message") or payload.get("text") or "")
        if re.search(r"brain\s+(?:self[- ]care|wake).{0,160}(?:failed|unavailable|unverified)",
                     text, re.I | re.S):
            return ""
        return (
            "[brain_selfcare_owner_alert] The brain-wake guard itself failed (%s: %s). "
            "The outgoing answer must tell the owner that brain self-care is unverified; "
            "do not leave this only in hook output." % (type(exc).__name__, str(exc)[:500])
        )


def process(payload: Mapping[str, Any], *, state_dir: Optional[Path] = None,
            now: Optional[float] = None, enforce_wake: bool = False,
            wake_state_path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    event = str(payload.get("hook_event_name") or payload.get("event_name") or "")
    root = Path(state_dir) if state_dir is not None else STATE_DIR
    timestamp = float(time.time() if now is None else now)

    if event == "UserPromptSubmit":
        token = _session_token(payload)
        if token:
            _record_prompt(payload, token, root, timestamp)
        return _brain_wake_prompt(payload, wake_state_path) if enforce_wake else None
    if event != "Stop":
        return None
    text = str(payload.get("last_assistant_message") or payload.get("text") or "").strip()
    if not text:
        return None
    if enforce_wake:
        wake_reason = _brain_wake_stop_reason(payload, wake_state_path)
        if wake_reason:
            return {"decision": "block", "reason": CONTINUATION_PREFIX + "\n" + wake_reason}
    report_result = _durable_report_result(text)
    persistent_report = bool(report_result.get("persistent"))
    if payload.get("stop_hook_active") and not persistent_report:
        return None
    token = _session_token(payload)
    if not token:
        # Structural incomplete-scope contradictions must not disappear merely because a
        # host revision omitted its session key.  They are safe to re-evaluate without
        # mutable state: correction of the outgoing text is the only exit.  Nonpersistent
        # rules still require a token so their once-per-generation recovery contract holds.
        report_warning = str(report_result.get("reason") or "")
        if persistent_report and report_warning:
            return {
                "decision": "block",
                "reason": CONTINUATION_PREFIX + "\n" + report_warning,
            }
        return None

    state = _load(token, root, timestamp)
    generation = int(state.get("generation") or 0)
    fired = state.get("fired") if isinstance(state.get("fired"), dict) else {}
    completion = _completion_claim(text)
    contract_active = (timestamp < float(state.get("timed_until") or 0.0)
                       or int(state.get("keep_generation") or 0) == generation)
    due_done = (completion and not _has_verification_disclosure(text)
                and int(fired.get("done_means_verified") or 0) != generation)
    due_keep = (contract_active and (completion or bool(_IDLE.search(text)))
                and int(fired.get("keep_working") or 0) != generation)
    report_warning = str(report_result.get("reason") or "")
    report_generation = generation or 1
    due_report = bool(report_warning) and (
        persistent_report
        or int(fired.get("owner_report_contract") or 0) != report_generation
    )
    if not (due_done or due_keep or due_report):
        return None

    reasons = []
    if due_report:
        if not persistent_report:
            fired["owner_report_contract"] = report_generation
        reasons.append(report_warning)
    if due_done:
        fired["done_means_verified"] = generation
        reasons.append(
            "[done_means_verified] Before finalizing, name what you personally verified end to end "
            "and state plainly what remains unverified; a status light or component test is not completion.")
    if due_keep:
        fired["keep_working"] = generation
        reasons.append(
            "[keep_working] The owner's work instruction is still active for this task generation. "
            "Continue with the next genuinely useful in-scope implementation, verification, or hardening "
            "front instead of winding down.")
        if state.get("contract_kind") == "standing-once":
            state["keep_generation"] = 0
            state["contract_kind"] = ""
    state["fired"] = fired
    state.setdefault("schema", 1)
    state.setdefault("generation", report_generation)
    state["updated_at"] = timestamp
    if not _save(token, state, root, timestamp) and not (due_report and persistent_report):
        return None
    return {"decision": "block", "reason": CONTINUATION_PREFIX + "\n" + "\n".join(reasons)}


def _read_payload() -> Optional[Mapping[str, Any]]:
    source = getattr(sys.stdin, "buffer", sys.stdin)
    raw = source.read(MAX_PAYLOAD + 1)
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "replace")
    if len(raw) > MAX_PAYLOAD:
        return None
    value = json.loads(raw.decode("utf-8", "replace") or "{}")
    return value if isinstance(value, Mapping) else None


def main() -> int:
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    started = time.perf_counter()
    event = ""
    status = "ok"
    try:
        payload = _read_payload()
        if payload is None:
            status = "oversize"
            return 0
        event = str(payload.get("hook_event_name") or "")
        output = process(payload, enforce_wake=True)
        if output:
            sys.stdout.write(json.dumps(output, ensure_ascii=True))
    except Exception:
        status = "error"
    finally:
        try:
            import codex_hook_telemetry
            codex_hook_telemetry.record(event, "block", status, started)
        except Exception:
            pass
    return 0


def _selftest() -> int:
    try:
        def check(condition: object, label: str) -> None:
            # ``assert`` disappears under python -O; a safety selftest must not become an automatic PASS.
            if not condition:
                raise AssertionError(label)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            timed = {"hook_event_name": "UserPromptSubmit", "session_id": "timed",
                     "prompt": "Keep working for the next 2 hours."}
            check(process(timed, state_dir=root, now=1000.0) is None, "timed prompt emitted output")
            token = _session_token(timed)
            state = _load(token, root, 1001.0)
            check(state["timed_until"] == 8200.0 and state["generation"] == 1, "timed deadline")
            raw_state = _state_path(token, root).read_text(encoding="utf-8")
            check("Keep working" not in raw_state and "prompt" not in raw_state, "state retained prose")

            check(process({"hook_event_name": "UserPromptSubmit", "session_id": "timed",
                           "prompt": "keep working"}, state_dir=root, now=1100.0) is None,
                  "bare carry emitted output")
            state = _load(token, root, 1101.0)
            check(state["timed_until"] == 8200.0 and state["generation"] == 2, "bare carry extended deadline")
            first = process({"hook_event_name": "Stop", "session_id": "timed",
                             "last_assistant_message": "Task complete. Let me know what to do next."},
                            state_dir=root, now=1110.0)
            check(first and all(x in first["reason"] for x in ("done_means_verified", "keep_working")),
                  "combined Stop reason")
            check(process({"hook_event_name": "Stop", "session_id": "timed",
                            "stop_hook_active": True, "last_assistant_message": "Task complete."},
                           state_dir=root, now=1120.0) is None, "stop_hook_active loop guard")
            check(process({"hook_event_name": "Stop", "session_id": "timed",
                            "last_assistant_message": "Task complete."},
                           state_dir=root, now=1121.0) is None, "generation fired twice")

            negative = {"hook_event_name": "UserPromptSubmit", "session_id": "negative",
                        "prompt": "Don't stop working."}
            check(process(negative, state_dir=root, now=2000.0) is None, "standing carry emitted output")
            ns = _load(_session_token(negative), root, 2001.0)
            check(ns["contract_kind"] == "standing-once" and ns["keep_generation"], "don't-stop parsing")
            check(process({"hook_event_name": "UserPromptSubmit", "session_id": "negative",
                            "prompt": "You can stop working now."},
                           state_dir=root, now=2010.0) is None, "cancellation emitted output")
            ns = _load(_session_token(negative), root, 2011.0)
            check(not ns.get("timed_until") and not ns.get("keep_generation"), "cancellation failed")

            for index, prompt in enumerate(("Read through the docs.", "Retry until green.")):
                plain = {"hook_event_name": "UserPromptSubmit", "session_id": "plain-%d" % index,
                         "prompt": prompt}
                check(process(plain, state_dir=root, now=2100.0 + index) is None,
                      "plain prompt emitted output")
                ps = _load(_session_token(plain), root, 2101.0 + index)
                check(not ps.get("timed_until") and not ps.get("keep_generation"), "plain prompt armed contract")

            for index, message in enumerate((
                "I verified end-to-end on the live endpoint. Task complete.",
                "Task complete. Unverified: production deployment.",
                "The task is not complete; I am continuing.")):
                sid = "honest-%d" % index
                check(process({"hook_event_name": "UserPromptSubmit", "session_id": sid,
                                "prompt": "Check this."},
                               state_dir=root, now=3000.0 + index) is None, "honest setup emitted output")
                check(process({"hook_event_name": "Stop", "session_id": sid,
                                "last_assistant_message": message},
                               state_dir=root, now=3010.0 + index) is None, "honest completion blocked")

            sid = "done"
            check(process({"hook_event_name": "UserPromptSubmit", "session_id": sid,
                            "prompt": "Check this."}, state_dir=root, now=4000.0) is None
                  , "done setup emitted output")
            done = process({"hook_event_name": "Stop", "session_id": sid,
                            "last_assistant_message": "Task complete."},
                           state_dir=root, now=4010.0)
            check(done and "done_means_verified" in done["reason"], "unverified done not blocked")
            check(process({"hook_event_name": "Stop", "session_id": sid,
                            "last_assistant_message": "Task complete."},
                           state_dir=root, now=4020.0) is None, "done generation fired twice")

            report_cases = (
                ("settled number", "The pipeline earns $123k per year.", False),
                ("settled loss", "The current strategy lost $40,000 last month.", False),
                ("incomplete P&L",
                 "Account 7 P&L is incomplete; 3 legs are missing, "
                 "so -$4,120 is understated.", True),
            )
            for index, (label, message, persistent) in enumerate(report_cases):
                report_sid = "report-%d" % index
                out = process({"hook_event_name": "Stop", "session_id": report_sid,
                               "last_assistant_message": message},
                              state_dir=root, now=5000.0 + index)
                check(out and "owner_report_contract" in out["reason"], label + " was not blocked")
                check("$" not in out["reason"], label + " reason restored a numeric oracle")
                repeat = process({"hook_event_name": "Stop", "session_id": report_sid,
                                  "last_assistant_message": message},
                                 state_dir=root, now=5010.0 + index)
                if persistent:
                    check(repeat and "owner_report_contract" in repeat["reason"],
                          label + " was read past")
                    continued = process({"hook_event_name": "Stop", "session_id": report_sid,
                                         "stop_hook_active": True,
                                         "last_assistant_message": message},
                                        state_dir=root, now=5020.0 + index)
                    check(continued and "owner_report_contract" in continued["reason"],
                          label + " escaped through stop_hook_active")
                    corrected = process({"hook_event_name": "Stop", "session_id": report_sid,
                                         "stop_hook_active": True,
                                         "last_assistant_message": "DATA INCOMPLETE; no exact result is available."},
                                        state_dir=root, now=5030.0 + index)
                    check(corrected is None, label + " had no correction escape")
                else:
                    check(repeat is None, label + " fired twice")
            blocked_state = root / "not-a-directory"
            blocked_state.write_text("x", encoding="utf-8")
            no_state = process(
                {"hook_event_name": "Stop", "session_id": "state-failure",
                 "last_assistant_message": (
                     "Account 7 P&L is incomplete because two fills are missing; "
                     "the account result is -$4,120."
                 )},
                state_dir=blocked_state, now=5090.0)
            check(no_state and "owner_report_contract" in no_state["reason"],
                  "persistent report depended on writable state")
            stale = process({"hook_event_name": "Stop", "session_id": "stale-rate",
                             "last_assistant_message": "The grid backtest earns $180k per year."},
                            state_dir=root, now=5100.0)
            check(stale is None, "retired remembered magnitude blocked")

            # The actual main() enables the session-bound wake lane. Exercise both sides here so
            # hook liveness cannot report PASS when the guard dependency is missing or stale.
            wake_state = root / "brain-health.json"
            wake_prompt = {"hook_event_name": "UserPromptSubmit", "session_id": "wake-session",
                           "prompt": "Start the task."}
            missing = process(wake_prompt, state_dir=root, now=5200.0,
                              enforce_wake=True, wake_state_path=wake_state)
            check(missing and "WAKE REQUIRED" in missing["hookSpecificOutput"]["additionalContext"],
                  "missing session wake was not injected")
            missing_stop = process(
                {"hook_event_name": "Stop", "session_id": "wake-session",
                 "last_assistant_message": "Here is the result."},
                state_dir=root, now=5201.0, enforce_wake=True, wake_state_path=wake_state)
            check(missing_stop and "brain_wake_receipt" in missing_stop["reason"],
                  "missing session wake did not block Stop")
            import codex_brain_wake_guard as _wake_guard
            wake_state.write_text(json.dumps({
                "schema": _wake_guard.STATE_SCHEMA,
                "incidents": {},
                "checks": {_wake_guard.CHECK_KEY: {
                    "schema": _wake_guard.SNAPSHOT_SCHEMA,
                    "checked_at": "2030-01-01T00:00:00Z",
                    "finding_fingerprints": [],
                    "rendered_chars": 100,
                    "rendered_sha256": "a" * 64,
                    "budget_chars": 40000,
                    "truncated": False,
                    "session_fingerprint": _wake_guard.session_fingerprint("wake-session"),
                    "observations": [],
                }},
            }), encoding="utf-8")
            healthy = process(wake_prompt, state_dir=root, now=5202.0,
                              enforce_wake=True, wake_state_path=wake_state)
            check(healthy is None, "matching healthy session wake was not silent")
            healthy_stop = process(
                {"hook_event_name": "Stop", "session_id": "wake-session",
                 "last_assistant_message": "Here is the result."},
                state_dir=root, now=5203.0, enforce_wake=True, wake_state_path=wake_state)
            check(healthy_stop is None, "matching healthy session wake blocked Stop")
        print("codex_stop_guard selftest: PASS")
        return 0
    except Exception as exc:
        print("codex_stop_guard selftest: FAIL", repr(exc))
        return 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    raise SystemExit(main())
