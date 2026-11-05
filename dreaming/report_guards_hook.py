#!/usr/bin/env python3
"""report_guards_hook.py - Stop hook: BLOCK ending on a stale DEAD/BROKEN/MISSING claim about a named
artifact when NO tool observed it this turn (observed=False).

The 07-12 recurring mistake (mine, 4x): asserting/broadcasting something is dead/broken from a stale or
reconstructed read, without looking. reality_monitor blocks "it works/deployed"; pre_report blocks a wrong
number; this closes the present-tense negative-existence/brokenness claim - the blind-correction class
(owner corrections where the engine fired nothing on the prose).

BLOCK: class A (stale_dead_assert) ONLY - verified 0 false-blocks on 5440 real assistant messages, and it
fires ONLY when observed=False (no Bash/Read/etc ran this turn), so a claim you actually verified is never
blocked. Other classes (settled_while_pending, implausible_stat, unmeasured_regime_timing) are SHADOW-logged
only (their real-prose precision is not yet established - healer-first: prove it before it medicates).

Gated by graph_fire/REPORT_GUARDS_LIVE (absent -> silent; rm to revert, no settings edit). Loop-safe
(honours stop_hook_active), dedup <=1 block/session, fail-safe silent. Transcript-tail read copied from the
proven reality_monitor.py.
"""
import sys, os, io, json, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
GF = os.path.join(HERE, "graph_fire")
LIVE = os.path.join(GF, "REPORT_GUARDS_LIVE")
STATE = os.path.join(GF, ".report_guards_blocked.json")
SHADOW = os.path.join(GF, "report_guards_shadow.tsv")

OBSERVE_RE = re.compile(r'"name"\s*:\s*"(Bash|PowerShell|Read|Grep|Glob|BashOutput|NotebookRead|WebFetch|TaskOutput)"')
HEDGE_RE = re.compile(r"\b(might|may\b|maybe|perhaps|probably|likely|possibly|i think|i believe|seems?|"
                      r"appears?|not\s+(sure|certain|yet)|haven'?t\s+(verified|checked|confirmed)|"
                      r"to\s+be\s+(verified|confirmed)|checking|i'?ll\s+check|hypothes)", re.I)


def _dedup_ok(sess):
    if not sess:
        return True
    now = time.time()
    try:
        st = json.load(io.open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
    except Exception:
        st = {}
    st = {k: v for k, v in st.items() if now - v < 86400}
    if sess in st:
        return False
    st[sess] = now
    try:
        json.dump(st, io.open(STATE, "w", encoding="utf-8"))
    except Exception:
        pass
    return True


def _text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content
                        if isinstance(c, dict) and c.get("type") == "text")
    return ""


def _last_assistant(tp):
    try:
        with open(tp, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            start = max(0, size - 4_000_000)
            f.seek(start)
            tail = f.read().decode("utf-8", "replace")
    except Exception:
        return "", False
    lines = tail.splitlines()
    if start and lines:
        lines = lines[1:]  # bounded read may begin inside a JSONL record
    lines = lines[-4000:]
    # A fixed tail is not a turn: an unrelated tool from the prior continuation used to suppress a
    # current warning. The most recent user TEXT record (owner prompt or Stop-hook continuation) is the
    # boundary. If it fell outside the bounded read, borrow no observation from an unknown turn.
    boundary = -1
    for i, ln in enumerate(lines):
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        msg = obj.get("message", {}) if isinstance(obj.get("message"), dict) else {}
        role = msg.get("role") or obj.get("role") or obj.get("type")
        content = msg.get("content") if "content" in msg else obj.get("content")
        if role == "user" and _text_content(content).strip():
            boundary = i
    current_lines = lines[boundary + 1:] if boundary >= 0 else []
    observed = any(OBSERVE_RE.search(ln) for ln in current_lines)
    last = ""
    for ln in (current_lines or lines):
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        msg = obj.get("message", {}) if isinstance(obj.get("message"), dict) else {}
        role = msg.get("role") or obj.get("role") or obj.get("type")
        if role != "assistant":
            continue
        content = msg.get("content") or obj.get("content") or ""
        if isinstance(content, str):
            txt = content
        elif isinstance(content, list):
            txt = " ".join(c.get("text", "") for c in content
                           if isinstance(c, dict) and c.get("type") == "text")
        else:
            txt = ""
        if txt.strip():
            last = txt
    return last, observed


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    try:
        if data.get("hook_event_name") != "Stop":
            return
        if data.get("stop_hook_active"):
            return
        tp = data.get("transcript_path") or ""
        if not tp or not os.path.exists(tp):
            return
        last, observed = _last_assistant(tp)
        if not last:
            return
        sys.path.insert(0, GF)
        import report_guards as rg
        hedged = bool(HEDGE_RE.search(last))
        block_a = [] if hedged else (rg.stale_dead_assert(last, observed=observed) or [])
        try:
            extra = rg.check_extra(last, observed=observed, hedged=hedged) or []
        except Exception:
            extra = []
        live = os.path.exists(LIVE)
        try:
            if re.match(r"^[0-9a-f]{8}-", data.get("session_id", "") or ""):
                kinds = ",".join(sorted(set(x.get("kind", "?") for x in extra))) or "-"
                with io.open(SHADOW, "a", encoding="utf-8", newline="") as f:
                    f.write("%.0f\t%s\t%s\t%s\n" % (time.time(),
                            "block" if (block_a and live) else "would",
                            kinds, last[:110].replace("\t", " ").replace("\r", " ").replace("\n", " ")))
        except Exception:
            pass
        if not live or not block_a:
            return
        if not _dedup_ok(data.get("session_id") or ""):
            return
        reason = ("[report-guard] You're ending on a present-tense DEAD / BROKEN / MISSING claim about a "
                  "named artifact, and NO tool ran this turn to observe it. This is the exact 07-12 "
                  "recurring mistake - asserting something is dead from a stale or reconstructed read. RUN "
                  "one check against CURRENT state (a Read/grep, or the thing's own output - a DIFFERENT "
                  "mechanism than whatever raised the alarm) before you claim it, or hedge it plainly as a "
                  "hypothesis. A wake-flag or an earlier output is not current truth.")
        sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
    except Exception:
        return


if __name__ == "__main__":
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import brain_switch as _bsw
        _bsw.gate()
    except SystemExit:
        raise
    except Exception:
        pass
    main()
