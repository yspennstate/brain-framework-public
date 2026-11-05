"""Prompt hook: before an agent starts on a request, show it the earlier requests that look like this one.

An agent that is handed "is every group of prime order cyclic?" a second time should know that the question was
asked on 3 October, that it produced claim_ab12, and that the answer was later refuted. This hook reads the prompt
from the hook's JSON on stdin, looks it up in the request ledger (see docs/prompts-as-nodes.md) and prints at most
three earlier requests with their status, how often they were asked and the store nodes they produced. It prints
nothing when the ledger is not configured, when nothing is similar, or on any error.

    MATH_BRAIN_REQUESTS=requests.jsonl python -m math_brain.request_hook            (Claude Code, plain text)
    MATH_BRAIN_REQUESTS=requests.jsonl python -m math_brain.request_hook --codex    (Codex, JSON envelope)

With MATH_BRAIN_REQUESTS_RECORD=1 the prompt is also recorded in the ledger, after the lookup, so it never matches
itself. Prompts shorter than four words ("ok", "go on") are neither looked up nor recorded.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from .requests import RequestLedger, words

MIN_WORDS = 4


def lines_for(ledger: RequestLedger, prompt: str, *, limit: int = 3, threshold: float = 0.35) -> list[str]:
    hits = ledger.similar(prompt, limit=limit, threshold=threshold)
    out = []
    for score, req in hits:
        made = req.linked("produced") + req.linked("answered_by")
        bits = [f"{req.status}", f"asked {req.times_asked}x since {req.first_asked[:10]}"]
        if req.status_note:
            bits.append(req.status_note[:80])
        if made:
            bits.append("nodes " + ", ".join(made[:4]) + (" ..." if len(made) > 4 else ""))
        text = req.text if len(req.text) <= 140 else req.text[:137] + "..."
        out.append(f"  {req.id} ({score:.2f}; {'; '.join(bits)}): {text}")
    if out:
        out.insert(0, "[earlier requests] This looks like work asked for before. Read what it produced before "
                      "starting again; a match is a hint, not proof that the question is the same:")
    return out


def run(event: dict[str, Any], env: dict[str, str]) -> str:
    path = env.get("MATH_BRAIN_REQUESTS")
    prompt = (event.get("prompt") or "").strip()
    if not path or len(words(prompt)) < MIN_WORDS:
        return ""
    ledger = RequestLedger(path)
    text = "\n".join(lines_for(ledger, prompt))
    if env.get("MATH_BRAIN_REQUESTS_RECORD") == "1":
        ledger.ask(prompt, role="researcher")
    return text


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        # bytes, decoded as UTF-8 with an optional byte-order mark: Windows PowerShell adds one when it pipes,
        # and the console code page would garble a prompt that is not plain ASCII
        buf = getattr(sys.stdin, "buffer", None)
        raw = buf.read().decode("utf-8-sig", "replace") if buf is not None else sys.stdin.read().lstrip("﻿")
        text = run(json.loads(raw) if raw.strip() else {}, dict(os.environ))
    except Exception:
        return 0
    if not text:
        return 0
    if "--codex" in argv:
        text = json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}})
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
