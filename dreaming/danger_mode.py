#!/usr/bin/env python3
"""danger_mode.py - a heightened-caution STATE for high-consequence territory.

The instinct layer fires specific reflexes; this is different - a whole posture shift. When the brain
recognizes it is about to touch real money, a security credential, the owner's phone, or an
IRREVERSIBLE action on a real system, it goes EXTRA careful: slow down, pre-mortem, verify the live
state, prefer the reversible path, and for the live production stack or a secret, do NOT act without the
owner's explicit confirmation. This is the amygdala / high-reliability-org "preoccupation with failure"
made into a mode - and, per the humility focus, the humble move in danger is to verify or ASK, not to
act fast.

Detection weights ACTIONS on the live stack and secrets highest; a mere mention scores low (editing a
local copy of a production file is fine - the danger is RUNNING/changing the live thing). Fires on PreToolUse
(Bash/PowerShell/Edit/Write) and UserPromptSubmit; fail-safe silent otherwise. Never hard-blocks (the
owner acts through this brain) - it raises caution, it doesn't lock the door.
"""
import sys, os, io, json, re, time

HERE = os.path.dirname(os.path.abspath(__file__))

# BACKSLASH GUARD. The Bash tool of Claude Code turns every double backslash into one before the shell runs - inside
# single quotes and quoted heredocs too, where plain bash keeps both (measured: A, backslash, backslash, n typed in a
# quoted heredoc reaches python as A and a newline). A regex, an escape or a Windows path written that way arrives
# changed, silently. The tool input still carries both backslashes, so a PreToolUse hook can see the hazard before the
# command runs. It is a mechanical fact, not a judgment, so the check speaks every time it applies, with no back-off.
# Double-quoted shell text collapses in plain bash as well and is left alone, except inside a python -c program.
_HEREDOC = re.compile(r"<<-?[ \t]*(['\"])(\w+)\1[^\n]*\n(.*?)(?:\n[ \t]*\2[ \t]*(?:\n|$)|\Z)", re.S)
_PY_C = re.compile(r"python[\w.]*(?:\s+-[\w-]+)*\s+-c\s")
_TWO = "\\\\"


def _backslash_trap(cmd):
    """'' when no double backslash in a Bash command is at risk, else where it sits."""
    if _TWO not in (cmd or ""):
        return ""
    for m in _HEREDOC.finditer(cmd):
        if _TWO in m.group(3):
            return "a quoted heredoc (<<'%s')" % m.group(2)
    rest = _HEREDOC.sub("", cmd)
    if _PY_C.search(rest) and re.search(r"\\\\[A-Za-z]", rest):
        return "a python -c program"
    for m in re.finditer(r"'[^']*'", rest):
        if _TWO in m.group(0):
            return "a single-quoted string"
    return ""


def _backslash_line(data):
    """The guard's one line for a PreToolUse Bash call, or ''. BACKSLASH_GUARD=0 turns it off. Fail-safe."""
    try:
        if os.environ.get("BACKSLASH_GUARD", "1") == "0" or data.get("tool_name") != "Bash":
            return ""
        where = _backslash_trap(((data.get("tool_input") or {}).get("command")) or "")
        if not where:
            return ""
        return ("[backslash-guard] a double backslash sits inside %s in this command. The Bash tool turns every "
                "double backslash into one before the shell runs, so a regex, an escape or a Windows path written "
                "this way arrives changed (A, backslash, backslash, n reaches python as A and a newline). Text with "
                "backslashes goes through the Write or Edit tool; if one backslash is what you mean, type one." % where)
    except Exception:
        return ""


def _emit_guard(ev, guard):
    if guard:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": ev, "additionalContext": guard}}))

# (label, regex, weight). Action-on-live and secrets are weight 3; irreversible / phone 2; mention 1.
# The process/host names below are EXAMPLES - populate the rosters for your deployment (the live
# services, hosts, and account patterns that are dangerous to touch on YOUR systems).
CATS = [
    ("a LIVE production action", 3,
     r"(place|cancel|submit|send|route|modify)\w*\s+\w*\s*(order|trade)|"
     r"(start|run|launch|deploy|restart|kill|stop)\w*\s+\w*\s*(example_prod_service|gateway|example_prod_bot)|"
     r"ssh\b[^\n]{0,50}(<box-ip>|<prod-host>|<prod-user>@)|\brebalanc\w*\b|example_approval_daemon|example_dispatcher|example_folder_guard|place_order\b"),
    ("a secret / security credential", 3,
     r"-----BEGIN [A-Z ]*PRIVATE KEY|\bapi[_-]?key\b|\bsecret\b|access[_-]?token|\b(sudo )?password\b|\.pem\b|\.env\b|\bcredential|\b2fa\b"),
    ("an IRREVERSIBLE action", 2,
     r"rm -rf|--force\b|force[- ]?push|git push\s+-f|DROP TABLE|DELETE FROM|truncate\b|robocopy[^\n]*/MIR|Remove-Item[^\n]*-Recurse|reset --hard|\bshutdown\b|\breboot\b|Stop-Process"),
    ("the owner's phone (alerts)", 2,
     r"\btelegram\b|approval card|@ExampleAlertBot|send\w*\s+\w*\s*card|notify\w*\s+\w*\s*owner"),
    ("the live production stack", 1,
     r"\bexample_prod_service\b|example_prod_stack|\bgateways?\b|\bexample_prod_bot\b|live (book|account|system)|real money|the live stack"),
]


# A local phone-flood sender being EXECUTED (not merely mentioned): its .py at a command boundary, after
# a Python launcher (py / python.exe / python3 / pipenv|poetry|conda run / nohup / start), or via ./ or .\
# An adversarial pass showed a plain "python\w*" prefix missed `py` and `python.exe`; enumerating launchers
# is fragile, so this also fires on a bare invocation at a command boundary. Mentions in an argument
# ("--scan example_dispatcher.py") or a note ("check example_dispatcher.py later") are NOT execution contexts.
# Roster rationale: a real incident where a local daemon kept paging the owner's phone. Populate for
# your deployment: the local processes that must never RUN on this machine.
_SENDER = r"(?:example_approval_daemon|example_dispatcher|example_folder_guard)"
# Deny a sender being RUN. The old launcher-enumeration regex missed the NATURAL invocations - an
# adversarial review got a sender past it with python "x.py" (a quote before the name),
# a quoted full path, Start-Process -ArgumentList "x.py" (a hidden-window launch pattern), and
# python -m module. A false negative here is the whole pager-flood failure mode, so this errs hard toward deny:
# fire when the sender NAME appears AND any EXECUTION context is present. A pure reference (grep/cat/mv/
# cp/git the sender - no launcher, and the sender not sitting at a command boundary) has no exec context,
# so it still does NOT deny. Over-denying a command that both runs python AND names a sender as an arg is
# the accepted cost of never missing a run.
_SENDER_NAME = re.compile(r"\b" + _SENDER + r"\b", re.I)
_EXEC_CTX = re.compile(
    r"(?<![\w.\\/])(?:python[\w.]*|py|pyw|pythonw)\b"            # a python LAUNCHER (lookbehind stops the ".py" extension counting as "py")
    r"|\b(?:Start-Process|Invoke-Expression|iex)\b"             # PowerShell execution
    r"|(?:^|[\n;&|`])\s*(?:sudo\s+|nohup\s+|time\s+|start\s+|exec\s+)*(?:\.[\\/])?(?:[\w.\-]+[\\/])?"
    + _SENDER + r"\.py\b",                                       # bare or ./ invocation at a command boundary
    re.I | re.M)


def _runs_sender(cmd):
    """True if a command RUNS a local phone-flood sender: the sender NAME appears AND an execution context
    (any python launcher, a PowerShell process-starter, or a bare/./ invocation) is present. Errs toward
    deny by design; a pure reference (no launcher, sender not at a boundary) does not match."""
    return bool(cmd) and bool(_SENDER_NAME.search(cmd)) and bool(_EXEC_CTX.search(cmd))


# A REAL secret VALUE (not the word "secret") - the only thing that makes WRITING a file dangerous.
# Editing a memory or hook that merely mentions "secret" / "api_key" / risky words is the session's
# normal work; the dream flagged danger-mode crying wolf on every such edit, which trains sessions to
# click past the alarm ("wallpaper is how the next flood gets in"). So on an edit we fire only when an
# actual credential value is being written - the token-leak shape. These are the push-scanner's VENDOR
# credential patterns (scan_secrets.py), kept in sync so the edit-advisory and the gate agree; an
# adversarial review caught an older set missing sk-ant, github fine-grained PATs, and the
# broader telegram/google shapes.
SECRET_VALUE = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"  # PEM private key
    r"|gh[pousr]_[A-Za-z0-9]{30,}|github" r"_pat_[A-Za-z0-9_]{22,}"    # github token / fine-grained PAT
    r"|sk-(?:ant-)?[A-Za-z0-9_\-]{20,}"                            # openai / anthropic key
    r"|AKIA[0-9A-Z]{16}"                                           # aws access key id
    r"|\b\d{8,10}:[A-Za-z0-9_-]{35}\b"                            # telegram bot token
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"                              # slack token
    r"|AIza[0-9A-Za-z_\-]{35}",                                   # google api key
    re.I)


def _score(text):
    hits = {}
    live_action = False
    for label, w, pat in CATS:
        if re.search(pat, text, re.I):
            # if the strong LIVE action already matched, don't also add the weak "mention"
            if label == "the live production stack" and live_action:
                continue
            hits[label] = w
            if label == "a LIVE production action":
                live_action = True
    return hits


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
        ev = data.get("hook_event_name", "")
        if ev == "UserPromptSubmit":
            text = data.get("prompt", "") or ""
        elif ev == "PreToolUse":
            text = json.dumps(data.get("tool_input", {}) or {}, ensure_ascii=False)
        else:
            return
        if not text:
            return
        # HARD BLOCK - the one never-do that earns a deny, not a warning: RUNNING a local phone-flood
        # sender (rationale: a real incident where a local daemon kept paging the owner's phone). Only
        # on a COMMAND (Bash/PowerShell), so editing a file that merely mentions a sender is never
        # blocked. An adversarial pass showed a launcher-prefix regex missed `py` / `python.exe` /
        # `nohup ...`, so instead: fire whenever a sender's .py is invoked and is NOT immediately
        # preceded by a read/move verb (grep/cat/ls/mv/cp/git...). Erring toward blocking - a false
        # negative here is the whole scar.
        if ev == "PreToolUse" and data.get("tool_name") in ("Bash", "PowerShell") and \
                _runs_sender((data.get("tool_input", {}) or {}).get("command", "") or ""):
            sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": ("BLOCKED (danger mode): running a local phone-flood sender "
                    "(example_approval_daemon / example_dispatcher / example_folder_guard). In a real "
                    "incident a local daemon kept paging the owner's phone independently of any kill "
                    "switch; senders on this roster must NEVER run locally. If the owner explicitly "
                    "asked to run one on the LIVE production host, do that there with their "
                    "confirmation - not here.")}}))
            return
        # On an EDIT/WRITE, the danger is not the text mentioning money/secrets/production - it's writing
        # a real credential VALUE (the token-leak shape). Score edits on that alone, so the gut stops
        # crying wolf on every memory/hook edit that quotes a risky word or the word "secret" (the dream's
        # anti-wallpaper finding). Commands and prompts still get the full mention-aware scoring below.
        tool = data.get("tool_name", "")
        guard = _backslash_line(data) if ev == "PreToolUse" else ""
        if ev == "PreToolUse" and tool in ("Edit", "Write", "MultiEdit"):
            hits = {"a secret / security credential": 3} if SECRET_VALUE.search(text) else {}
        else:
            hits = _score(text)
        if not hits:
            _emit_guard(ev, guard)
            return
        level = sum(hits.values())
        # anti-wallpaper (research: a safety alert on every step gets tuned out): surface a given danger
        # TIER once per session per ~5 min, so it marks ENTERING danger, not every step. The hard-deny
        # above is never deduped - a block must always fire.
        sess = data.get("session_id") or ""
        if sess:
            tier = 2 if level >= 5 else (1 if level >= 3 else 0)
            key = sess + "|" + str(tier)
            now = time.time()
            statef = os.path.join(HERE, ".danger_fired.json")
            try:
                st = json.load(io.open(statef, encoding="utf-8")) if os.path.exists(statef) else {}
            except Exception:
                st = {}
            st = {k: v for k, v in st.items() if now - v < 86400}
            if now - st.get(key, 0) < 300:
                _emit_guard(ev, guard)
                return
            st[key] = now
            try:
                # atomic replace (2026-09-02): same lost-update race as the reflex hook's shared backoff file
                _tmp = statef + ".%d.tmp" % os.getpid()
                with io.open(_tmp, "w", encoding="utf-8") as _f:
                    json.dump(st, _f)
                os.replace(_tmp, statef)
            except Exception:
                pass
        cats = "; ".join(hits.keys())
        if level >= 5:
            body = ("DANGER MODE (maximum) - this touches " + cats + ". High-consequence and hard to undo. "
                    "Do NOT proceed on the live production stack, a secret, or the owner's phone without their "
                    "EXPLICIT confirmation this session. First: say exactly what you're about to do and why; "
                    "pre-mortem the top 2 ways it goes wrong and check them; verify the real/live state; prefer a "
                    "dry-run or read-only path. When unsure here, the humble move is to ASK or abstain, not to act.")
        elif level >= 3:
            body = ("DANGER MODE - this touches " + cats + ". High-consequence: slow down. Pre-mortem the top 2 "
                    "failure modes and check them; verify the live/real state before AND after; prefer the "
                    "reversible / read-only path. If it runs or changes the live stack, confirm it's intended first.")
        else:
            body = ("heightened caution - this touches " + cats + ". Verify the real state and prefer the "
                    "reversible path; a mistake here costs more than usual, so trade speed for care.")
        out = {"hookSpecificOutput": {"hookEventName": ev,
                                      "additionalContext": "[danger-sense] " + body + ("\n" + guard if guard else "")}}
        if ev == "PreToolUse":
            out["hookSpecificOutput"]["permissionDecision"] = "allow"
        sys.stdout.write(json.dumps(out))
    except Exception:
        return


if __name__ == "__main__":
    main()
