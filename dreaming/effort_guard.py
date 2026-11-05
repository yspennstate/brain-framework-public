"""effort_guard.py - every Claude agent runs at effort HIGH, not max (a standing rule since 14 Oct 2026).

The rule: every Claude terminal and every Claude subagent runs at high thinking effort, not max, and the rule is
held by a hook and by the brain rather than by memory.

The lock itself is "maxEffortLevel": "high" in ~/.claude/settings.json: Claude Code clamps every ask above it (an
--effort flag, CLAUDE_CODE_EFFORT_LEVEL, an /effort or /model pick, a model default) for every process that loads
user settings, subagents included. Proved 14 Oct 22:4x: --effort max plus env max recorded effort "high" in the
transcript; the control without user settings recorded "max". This hook guards the ways around that lock.

PreToolUse (Bash, PowerShell, Write, Edit, MultiEdit) refuses:
  - a command that launches claude with --effort max or xhigh, or sets CLAUDE_CODE_EFFORT_LEVEL to max or xhigh;
  - a claude launch with --setting-sources that leaves out "user" (that skips the cap), or with
    CLAUDE_CODE_EXTRA_BODY carrying an effort (the cap does not see it);
  - a write to a launcher, script or settings file (.ps1 .py .cmd .bat .vbs .sh .json .toml) whose new text does
    any of the above or sets maxEffortLevel to anything but high.
Since his order of 2 Nov 2026 23:3x ("all the cluade tmerinals here and headless agents work on opu 5.5 hgh thinking
nothing mroe then that, and all codex work on 6.1 high thinking nothing more then that") it also refuses a claude
launch with --model fable, and a Codex launch or launcher/config write that names a model outside gpt-6.1-* or sets
model_reasoning_effort to xhigh, max or ultra. The Codex homes default to gpt-6.1-sol at high. Markdown is not checked,
so notes and memories may quote the old values. Refusals are logged to ~/.claude/brain_graph/logs/effort_guard.log.

SessionStart (run with --session-start) prints one line only when the settings lock has drifted.
Only his words in a live session change this.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)
import json
import os
import re
import sys
import time

LOG = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "logs", "effort_guard.log")
SETTINGS = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
SELF = os.path.basename(__file__).lower()

ABOVE = r"(?:max|xhigh)"
CLAUDE_WORD = re.compile(r"(?i)(?:^|[\s\"'`;&|(\\/])claude(?:-max)?(?:\.exe|\.cmd|\.ps1)?(?=[\s\"'`;&|)]|$)")
# --effort max, --effort=max, and the list form a launcher writes: "--effort", "max"
EFFORT_FLAG = re.compile(r"(?i)--effort(?:\s+|=|[\"']\s*,\s*)[\"']?" + ABOVE + r"\b")
ENV_ABOVE = re.compile(r"(?i)CLAUDE_CODE_EFFORT_LEVEL[\"']?\s*(?:=|:|,)\s*[\"']?" + ABOVE + r"\b")
SOURCES = re.compile(r"(?i)--setting-sources(?:\s+|=)[\"']?([a-z,]+)")
EXTRA_BODY = re.compile(r"(?i)CLAUDE_CODE_EXTRA_BODY[^\n]*effort")
CAP_WRONG = re.compile(r"(?i)[\"']maxEffortLevel[\"']\s*:\s*[\"'](?!high[\"'])\w+")
# 2 Nov 2026 23:3x his order: Claude on Opus 5.5 at high, Codex on 6.1 at high, "nothing more then that".
CLAUDE_MODEL_ABOVE = re.compile(r"(?i)--model(?:\s+|=|[\"']\s*,\s*)[\"']?(?:claude-)?fable")
CODEX_WORD = re.compile(r"(?i)(?:^|[\s\"'`;&|(\\/])codex(?:\.exe|\.cmd)?(?=[\s\"'`;&|)]|$)")
CODEX_EFFORT_ABOVE = re.compile(r"(?i)model_reasoning_effort[\"']?\s*=\s*\\?[\"']?(?:xhigh|max|ultra)\b")
CODEX_MODEL = re.compile(r"(?i)(?:(?<![\w-])-m|--model)(?:\s+|=|[\"']\s*,\s*)[\"']?(gpt-[\w.-]+)|(?m:^\s*model\s*=\s*[\"'](gpt-[\w.-]+))")
GUARDED_EXT = (".ps1", ".psm1", ".py", ".cmd", ".bat", ".vbs", ".sh", ".json", ".toml")


def problems(text, launches=True):
    """Reasons this text would run (or write) a Claude agent above high. launches=False skips the launch-word test."""
    out = []
    if ENV_ABOVE.search(text):
        out.append("sets CLAUDE_CODE_EFFORT_LEVEL above high")
    if CAP_WRONG.search(text):
        out.append("sets maxEffortLevel to something other than high")
    if launches and not CLAUDE_WORD.search(text):
        return out
    if EFFORT_FLAG.search(text):
        out.append("launches claude with --effort max/xhigh")
    for m in SOURCES.finditer(text):
        if "user" not in m.group(1).lower().split(","):
            out.append("launches claude with --setting-sources that skips user settings (and the cap)")
    if EXTRA_BODY.search(text):
        out.append("passes an effort through CLAUDE_CODE_EXTRA_BODY, which the cap does not clamp")
    if CLAUDE_MODEL_ABOVE.search(text):
        out.append("launches claude on a model above Opus 5.5 (Fable)")
    return out


def codex_problems(text, launches=True):
    """Reasons this text would run (or write) a Codex agent above gpt-6.1 at high (his order of 2 Nov 2026 23:3x)."""
    out = []
    if CODEX_EFFORT_ABOVE.search(text):
        out.append("sets Codex model_reasoning_effort above high")
    if launches and not CODEX_WORD.search(text):
        return out
    for m in CODEX_MODEL.finditer(text):
        name = m.group(1) or m.group(2)
        if not name.lower().startswith("gpt-6.1-"):
            out.append("runs Codex on %s, not a gpt-6.1 model" % name)
    return out


def tool_text(data):
    name = data.get("tool_name")
    ti = data.get("tool_input") or {}
    if name in ("Bash", "PowerShell"):
        return str(ti.get("command") or ""), True, None
    if name in ("Write", "Edit", "MultiEdit"):
        path = str(ti.get("file_path") or "")
        low = path.lower()
        if not low.endswith(GUARDED_EXT) or os.path.basename(low) == SELF or ".bak" in os.path.basename(low):
            return "", False, path
        if name == "Write":
            return str(ti.get("content") or ""), False, path
        if name == "Edit":
            return str(ti.get("new_string") or ""), False, path
        return "\n".join(str(e.get("new_string") or "") for e in (ti.get("edits") or [])), False, path
    return "", False, None


def session_start():
    try:
        d = json.load(open(SETTINGS, encoding="utf-8"))
    except Exception as e:
        print("[effort] could not read settings.json (%s); the high-effort lock is unverified" % e)
        return
    cap = d.get("maxEffortLevel")
    env = (d.get("env") or {}).get("CLAUDE_CODE_EFFORT_LEVEL")
    per_model = {m: s.get("maxEffortLevel") for m, s in (d.get("modelSettings") or {}).items()
                 if isinstance(s, dict) and s.get("maxEffortLevel") not in (None, "high")}
    bad = []
    if cap != "high":
        bad.append("maxEffortLevel is %r, not 'high'" % cap)
    if env not in (None, "high"):
        bad.append("env CLAUDE_CODE_EFFORT_LEVEL is %r" % env)
    if per_model:
        bad.append("modelSettings exempt or raise %s" % per_model)
    if bad:
        print("[effort] HIS LAW (14 Oct 22:26): every Claude agent runs at HIGH, not max. settings.json has drifted: "
              + "; ".join(bad) + ". Put \"maxEffortLevel\": \"high\" back at the top level of ~/.claude/settings.json.")


def main():
    if "--session-start" in sys.argv:
        return session_start()
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    # A temporary hold (14 Oct, about a day): no Claude agents, Codex only. While ~/claude_pool/NO_CLAUDE_AGENTS
    # holds, an Agent-tool subagent is refused in every session; headless launches are refused by claude_pool itself.
    if data.get("tool_name") in ("Agent", "Task"):
        try:
            sys.path.insert(0, os.path.expanduser("~/claude_pool"))
            import claude_pool
            why = claude_pool.claude_hold()
        except Exception:
            why = None
        if why:
            sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": "NO CLAUDE AGENTS (a standing hold: Codex workers only while it is set). Start a Codex worker instead. " + why}}))
        return
    text, launches, path = tool_text(data)
    if not text:
        return
    hit = problems(text, launches)
    codex_hit = codex_problems(text, launches)
    if codex_hit:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": "EVERY CODEX AGENT RUNS gpt-6.1-sol AT HIGH, NOTHING ABOVE (his order, 2 Nov 2026 "
            "23:3x: 'all codex work on 6.1 high thinking nothing more then that'). This %s %s. Use -m gpt-6.1-sol with "
            "model_reasoning_effort high, medium or low." % ("write" if path else "command", "; ".join(codex_hit + hit))}}))
        return
    if not hit:
        return
    reason = ("EVERY CLAUDE AGENT RUNS AT HIGH EFFORT, NOT MAX (a standing rule: terminals and subagents alike). "
              "This %s %s. Use --effort high "
              "(or no flag: settings.json caps every session at high through maxEffortLevel). Codex is not covered."
              % ("write" if path else "command", "; ".join(hit)))
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write("%s refused (%s) %s: %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), str(data.get("session_id"))[:8],
                                                   path or "", (text[:300]).replace("\n", " ")))
    except Exception:
        pass
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                        "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    main()
