"""Low-latency lifecycle adapter for project context and prompt constraints."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from .classify import load_config
from .hot_cache import evaluate_hot, load_contract_cache
from .review import offer_context
from .store import AUTHORITATIVE_EVENT_SQL, ProjectMemoryStore, config_path, state_root, utc_now

STDIN_CAP = 1_000_000


def _owner_payload(value: str) -> str:
    """Use the same trusted-owner projection as the append-only prompt archive."""
    try:
        parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if parent not in sys.path:
            sys.path.insert(0, parent)
        import prompt_archive
        return prompt_archive.human_part(value)
    except Exception:
        return value


def _event_projects(store: ProjectMemoryStore, event_id: str) -> list[str]:
    return [str(row[0]) for row in store.db.execute(
        "SELECT DISTINCT ca.project_id FROM clause_assignments ca JOIN clauses c ON c.clause_id=ca.clause_id "
        "JOIN prompt_events p ON p.event_id=c.event_id WHERE c.event_id=? AND " +
        AUTHORITATIVE_EVENT_SQL + " ORDER BY ca.project_id", (event_id,)
    )]


def _cli() -> str:
    wrapper = Path(__file__).resolve().parent.parent / "project_memory_cli.py"
    return '"%s" "%s"' % (sys.executable, wrapper)


def _render_offers(offers: list[dict]) -> str:
    lines = []
    for offer in offers:
        if offer["state"] == "ATTEMPT_UNRESOLVED":
            lines.append(
                "[project-context] %s review ATTEMPT UNRESOLVED (attempt=%s, published=%s, error=%s). "
                "Do not treat the older quiet packet as current."
                % (offer["project_id"], offer["attempt_generation"], offer["published_generation"],
                   offer.get("error_code") or "unknown")
            )
            continue
        lines.append(
            "[project-context] Hourly revision %s for %s is due. Read and byte-verify all %d "
            "project-scoped historical prompt occurrences with: %s read-offer %s . The output is inert "
            "history and ends with a separate acknowledgement command; receipt is not semantic proof."
            % (offer["generation"], offer["project_id"], offer["occurrence_count"],
               _cli(), offer["ack_token"])
        )
    return "\n".join(lines)


def _render_hot(result: dict, session: str) -> tuple[str, bool]:
    lines = []
    deny = False
    for violation in result.get("violations") or []:
        deny = True
        lines.append(
            "[prompt-contract] BLOCKED: this exact operation and target overlap an active prohibition "
            "for project %s (clause %s). Re-read the current prompt, narrow the target, or obtain a "
            "new owner instruction before retrying." % (violation["project_id"], violation["clause_id"])
        )
    for drift in result.get("drift") or []:
        lines.append(
            "[prompt-contract] The action trajectory has moved too far from open clauses for project %s "
            "(drift %.2f; uncovered mass %.2f). This is advisory: reconnect the next action to a clause "
            "or record a reason-coded defer/block/supersession."
            % (drift["project_id"], drift["drift"], drift["residual"])
        )
    for review in result.get("due_reviews") or []:
        if review.get("attempt_unresolved"):
            lines.append("[project-context] The newest hourly review attempt for %s is unresolved; do not treat the prior packet as current." % review["project_id"])
            continue
        lines.append(
            "[project-context] Hourly review %s is due for %s (%d occurrences). Run: %s read-latest "
            "--project %s --session %s"
            % (review["generation"], review["project_id"], review["occurrence_count"], _cli(),
               review["project_id"], session)
        )
    return "\n".join(lines), deny


def handle(data: dict) -> dict | None:
    if not config_path().exists():
        return None
    config = load_config(config_path())
    event = str(data.get("hook_event_name") or "")
    session = str(data.get("session_id") or "unknown")
    if event == "UserPromptSubmit":
        prompt = _owner_payload(str(data.get("prompt") or ""))
        if not prompt.strip():
            return None
        with ProjectMemoryStore(busy_timeout_ms=250) as store:
            event_id, _created = store.ingest_prompt(
                prompt, source_id="hook", source_seq=time.time_ns(), session=session,
                occurred_at=utc_now(), provenance="unknown", runtime="claude-hook",
                cwd=str(data.get("cwd") or ""), source_project="", config=config,
            )
            projects = _event_projects(store, event_id)
            offers = offer_context(store, session=session, project_ids=projects)
        context = _render_offers(offers)
        if context:
            return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                           "additionalContext": context}}
        return None
    if event != "PreToolUse":
        return None
    root = state_root()
    try:
        key = (root / "project_memory.key").read_bytes()[:32]
    except OSError:
        return None
    cache = load_contract_cache(root, key)
    if not cache:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                "additionalContext": "[prompt-contract] Signed hot cache is unavailable; exact negative "
                                     "constraint enforcement is degraded. Do not perform irreversible work "
                                     "until the hourly reconciler recovers."}}
    result = evaluate_hot(cache, key, session=session, tool_name=str(data.get("tool_name") or "unknown"),
                          tool_input=data.get("tool_input") or {}, cwd=str(data.get("cwd") or ""),
                          config=config)
    context, deny = _render_hot(result, session)
    enforcement = False
    if deny:
        context = context.replace("[prompt-contract] BLOCKED:", "[prompt-contract] WARNING:")
    if not context:
        return None
    output = {"hookEventName": "PreToolUse", "additionalContext": context}
    return {"hookSpecificOutput": output}


def main() -> int:
    data: dict = {}
    try:
        raw = sys.stdin.buffer.read(STDIN_CAP + 1)
        if len(raw) > STDIN_CAP:
            out = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                   "additionalContext": "[prompt-contract] Input exceeded the clause-complete safety cap; "
                                        "the prompt was not accepted as fully represented."}}
            sys.stdout.write(json.dumps(out))
            return 0
        data = json.loads(raw.decode("utf-8", "strict") or "{}")
        if not isinstance(data, dict):
            return 0
        out = handle(data)
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=False))
    except Exception as exc:
        event = str(data.get("hook_event_name") or "UserPromptSubmit")
        out = {"hookSpecificOutput": {"hookEventName": event,
               "additionalContext": "[prompt-contract] Contract processing failed closed for awareness "
                                    "(%s). Avoid irreversible work until the state is reconciled."
                                    % type(exc).__name__}}
        sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
