"""Send a hook's standing text once per context, then only what changed (claude-fb4584c3, 4 Nov 2026).

Measured on 24 h of the hook dispatcher's overflow files on the first deployment: most hook
text is the same paragraph re-sent on every prompt to a session that already has it - waste_sense 1,926 chars a
fire, 95 pct repeated. A context keeps what it was told until a compaction, so a standing paragraph needs to go
whole once per context (again after a compaction or after `resend` seconds) and otherwise not at all.

    from once_per_context import filter_lines
    out, whole, at = filter_lines(session_id, "waste_sense", lines, keep=lambda l: l.startswith("  - pid"))

Lines are keyed with their digits masked, so a census count that moved does not count as new text. `keep` names
lines that always go (the live part). Any doubt (no session, unreadable state or compaction index) sends it whole.
ONCE_PER_CONTEXT_OFF=1 turns it off for every hook.
"""
import datetime
import hashlib
import io
import json
import os
import re
import time

STATE = os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "state", "once_per_context.json")
COMPACTION_INDEX = os.path.expanduser("~/.claude/brain_graph/compactions/INDEX.jsonl")


def last_compaction_at(sess):
    """Epoch of this session's newest compaction in the brain's compaction index (0 = none, None = unreadable)."""
    try:
        with open(COMPACTION_INDEX, "rb") as f:
            f.seek(0, 2)
            n = f.tell()
            f.seek(max(0, n - 262144))
            tail = f.read().decode("utf-8", "replace")
    except Exception:
        return None
    best = 0.0
    for line in tail.splitlines():
        if sess not in line:
            continue
        try:
            o = json.loads(line)
            if o.get("session_id") == sess:
                best = max(best, datetime.datetime.fromisoformat(o["at"]).timestamp())
        except Exception:
            continue
    return best


def _key(line):
    return hashlib.md5(re.sub(r"\d+", "#", line).encode("utf-8")).hexdigest()[:12]


def filter_lines(sess, name, lines, keep=None, resend=3 * 3600):
    """Return (lines to send, sent_whole, epoch the whole text last went)."""
    now = time.time()
    if not sess or os.environ.get("ONCE_PER_CONTEXT_OFF"):
        return list(lines), True, now
    keys = [_key(l) for l in lines]
    try:
        state = json.load(io.open(STATE, encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except Exception:
        state = {}
    sk = name + "|" + sess
    prev = state.get(sk)
    comp = last_compaction_at(sess)
    whole = (not prev or comp is None or comp >= prev.get("at", 0) - 5 or now - prev.get("at", 0) > resend)
    if whole:
        state[sk] = {"at": now, "keys": keys}
        out, at = list(lines), now
    else:
        seen = set(prev.get("keys", []))
        out = [l for l, k in zip(lines, keys) if (keep and keep(l)) or (k not in seen and l.strip())]
        prev["keys"] = list(seen | set(keys))[-4000:]
        at = prev.get("at", now)
    try:
        state = {k: v for k, v in state.items() if now - v.get("at", 0) < 86400}
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = "%s.%d.tmp" % (STATE, os.getpid())
        json.dump(state, io.open(tmp, "w", encoding="utf-8"))
        os.replace(tmp, STATE)
    except Exception:
        pass
    return out, whole, at
