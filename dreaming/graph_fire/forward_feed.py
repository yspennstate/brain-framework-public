#!/usr/bin/env python3
"""forward_feed.py - feed felt-errors FORWARD so a just-made mistake arrests the next one.

error_monitor.py already records failures to outcomes.jsonl (the post-hoc ERN), but nothing reads them
BEFORE the next action - so the same failure recurs. On 2026-07-11 three separate sessions ran a Bash
command with a C:\\Users\\... backslash path that Git Bash silently collapses ('Usersowner...': No such
file), each landing in outcomes.jsonl, none warned. This closes that loop two ways:

  1. BACKSLASH-PATH (structural, certain): a Windows drive-letter path with backslashes inside a *Bash*
     command always breaks (Git Bash eats \\U, \\o, ... as escapes). Fire before it runs. Bash-only -
     PowerShell handles backslashes fine, so we never nag there.
  2. RECENT-REPEAT (from outcomes.jsonl): if the pending command resembles one that FAILED in the last
     window, surface that felt-error so it isn't blindly repeated.

Stdlib only, forward-slash paths, fail-safe -> [] on any error. Pure read; never blocks.
"""
import os, io, json, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
OUTCOMES = os.path.join(os.path.dirname(HERE), "outcomes.jsonl")
WINDOW_S = 3600           # look back this far in outcomes.jsonl for a repeat
TAIL_BYTES = 60000        # bound the read

# a real drive-letter path with BACKSLASHES (the shape Git Bash mangles). The hook feeds json.dumps(
# tool_input), so a genuine path 'C:\Users' arrives as 'C:' + TWO backslashes (JSON-escaped), while a
# Python code colon+newline ('try:\n') is ':' + ONE backslash + letter. Requiring a standalone drive
# letter (not the tail of a word like 'try') + a double backslash rejects the code-colon false class
# (measured: it was 65/65 of the false fires). Forward-slash C:/... is fine and never matches.
_BACKSLASH_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\\\")

# lookahead-in-CODE: boolean-indexing / conditioning on a FEATURE value written as bare pandas/numpy, with
# no 'regime'/'filter'/'conditioned' word for the regex layer to catch (the one miss it has). Gated to
# feature-like column names (vix/regime/vol/return/signal/...) so ordinary filtering by date/id stays quiet.
_LOOKAHEAD_CODE = re.compile(
    r"(\[\s*\w{0,20}\.?|\.loc\[[^\]]{0,30}|\.query\([^)]{0,30}|where\(\s*\w{0,20}\.?)"
    r"(vix|vxn|regime|\bvol\b|\brv\b|\biv\b|realized|realised|\bret\b|return|signal|factor|zscore|"
    r"z_score|percentile|forward|future)\w*\s*[<>=!]=?\s*[-\d.]", re.I)


def _recent_failures():
    try:
        if not os.path.exists(OUTCOMES):
            return []
        with open(OUTCOMES, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read().decode("utf-8", "replace")
        now = time.time()
        out = []
        for ln in tail.splitlines()[-120:]:
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except Exception:
                continue
            if now - float(o.get("ts", 0)) <= WINDOW_S:
                cmd = (o.get("cmd") or "")
                if cmd:
                    out.append((cmd, o.get("sig", [])))
        return out
    except Exception:
        return []


def _norm(cmd):
    # collapse whitespace + a couple of volatile bits so 'same shape' matches across sessions
    c = re.sub(r"\s+", " ", (cmd or "")).strip().lower()
    return c[:60]


def forward_feed(text, tool_name=None, session=None):
    """Return caution fires derived from recent felt-errors + structural traps. Fail-safe -> []."""
    try:
        fires = []
        t = text or ""
        # 1. backslash Windows path in a Bash command -> will silently break
        if (tool_name in (None, "Bash")) and _BACKSLASH_PATH.search(t):
            # don't fire if it's clearly already forward-slashed elsewhere and the backslash is inside a
            # quoted regex/string that isn't a path arg - keep it simple + caution-biased: fire on the shape.
            fires.append({
                "id": "windows_backslash_path",
                # certain structural bug (the command WILL break) -> charge>=0.95 so the mindware-override
                # escalation fires when you read it past repeatedly. (Also catches the same backslash inside a
                # python -<<heredoc string literal, where it's a \U unicode-escape - the fix, forward slashes,
                # is the same, so firing is right even though the diagnosis line names Git Bash.)
                "charge": 0.97,
                "gut": ("This Bash command has a C:\\... backslash path - Git Bash silently strips the "
                        "backslashes ('C:\\Users\\<user>' -> 'Users<user>', No such file). Use FORWARD "
                        "slashes (<HOME>/...) or it will fail. This exact bug hit 3 sessions today."),
                "why": "structural: Git Bash eats backslash escapes",
            })
        # 2. lookahead-in-code: conditioning on a feature value with no trigger word (fires on any tool)
        if _LOOKAHEAD_CODE.search(t):
            fires.append({
                "id": "lookahead",
                "gut": ("This conditions/filters on a FEATURE value in code (a df[...] / .loc / where by "
                        "vix/regime/vol/return/signal) with no 'regime'/'filter' word for the gut to catch. "
                        "Ask: could I have known this in real time? Peeking at data the moment wouldn't have "
                        "had is the causal-lookahead class - the May regime-VIX lookahead was 31 backlinks."),
                "why": "code-level conditioning on a feature",
            })
        # 3. resembles a command that just failed
        norm = _norm(t)
        if norm and len(norm) >= 12:
            for cmd, sig in _recent_failures():
                nc = _norm(cmd)
                if nc and (nc[:40] == norm[:40] or (len(nc) >= 20 and nc[:20] == norm[:20])):
                    sigtxt = "; ".join(sig) if isinstance(sig, list) else str(sig)
                    fires.append({
                        "id": "recent_repeat",
                        "gut": ("This resembles a command that just failed (" + (sigtxt or "a hard error") +
                                "). Read what went wrong before repeating it - don't rebuild on a broken step."),
                        "why": "forward-feed from outcomes.jsonl",
                    })
                    break
        return fires
    except Exception:
        return []


def _selftest():
    import json as _j
    # mirror the live hook: it feeds json.dumps(tool_input), so build the text the same way
    cases = [
        (_j.dumps({"command": r"python {{USERPROFILE}}\.claude\dreaming\incubate.py"}), "Bash", True),
        (_j.dumps({"command": "python <HOME>/.claude/dreaming/incubate.py"}), "Bash", False),
        (_j.dumps({"command": r"powershell {{USERPROFILE}}\x.ps1"}), "PowerShell", False),
        (_j.dumps({"command": "for f in rolls:\n  try:\n    pass"}), "Bash", False),  # code colon+newline, NOT a path
        (_j.dumps({"command": "grep -n def main foo.py"}), "Bash", False),
        ("", "Bash", False),
    ]
    ok = True
    for txt, tool, expect in cases:
        got = bool([f for f in forward_feed(txt, tool) if f["id"] == "windows_backslash_path"])
        flag = "ok" if got == expect else "MISS"
        if got != expect:
            ok = False
        print("  [%s] tool=%-11s backslash_fire=%-5s (want %s)" % (flag, tool, got, expect))
    for junk in [None, 123, {"x": 1}]:
        try:
            forward_feed(junk, "Bash")
        except Exception as e:
            print("  FAIL-SAFE BREACH:", e)
            ok = False
    print("forward_feed selftest", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
