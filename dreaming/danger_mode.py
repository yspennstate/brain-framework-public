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
import sys, os, io, json, re, time, fnmatch

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


# ARGMAX GUARD (2026-11-01, Mac). The memory folder holds ~29,100 .md files whose paths total ~3.7 MB, past the
# 1 MB kernel argument limit, so an unquoted `*.md` there never runs: the shell prints "argument list too long" and a
# `2>/dev/null` hides even that, leaving an empty result that reads as "no match". Eight recorded slips, the rule
# already in the brain each time. Replayed over 28,704 Mac Bash calls since 30 Oct: 34 shell refusals, 31 caught;
# of the 49 fires with no refusal line 48 had stderr hidden and the last was a refusal whose message the transcript
# lost. It lists only real directories named by a glob word (or the cwd / a `cd` earlier in the command).
_AM_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1[^\n]*\n.*?(?:\n[ \t]*\2[ \t]*(?=\n|$)|\Z)", re.S)
_AM_ARITH = re.compile(r"\$\(\([^()]*\)\)")
_AM_QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
_AM_ASSIGN = re.compile(r"(?:^|[\s;&|(])([A-Za-z_][A-Za-z0-9_]*)=([^\s;&|()]+)")
_AM_VAR = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
_AM_WORD = re.compile(r"[^\s;&|()<>]+")
_AM_CD = re.compile(r"(?:^|[;&|(]\s*|\s)cd\s+([^\s;&|()]+)")
_ARG_MAX = 1048576
try:
    _ARG_MAX = os.sysconf("SC_ARG_MAX")
except (ValueError, OSError, AttributeError):
    pass


_AM_SUBST = re.compile(r"\$\(([^()]*)\)")


def _am_unquote(m):
    s = m.group(0)
    if s[0] == '"' and "$(" in s:
        # a command substitution runs even inside double quotes; keep its words, drop its own quoted strings
        return " ; ".join(_AM_SUBST.findall(re.sub(r"'[^']*'", " ", s))) or " "
    # a quoted glob never expands, so it goes; a quoted path stays (cd "$M" still moves the shell)
    return " " if "*" in s or " " in s.strip("'\"") else s[1:-1]


def _am_dir(d, cwd, env):
    d = _AM_VAR.sub(lambda v: env.get(v.group(1), v.group(0)), d)
    d = os.path.expanduser(d.replace("$HOME", "~").replace("${HOME}", "~"))
    if "$" in d:
        return ""
    return os.path.normpath(os.path.join(cwd or os.getcwd(), d))


def _argmax_trap(cmd, cwd=""):
    """'' unless an unquoted glob in a Bash command expands past the argument limit, else the glob and its size."""
    if "*" not in (cmd or ""):
        return ""
    rest = _AM_QUOTED.sub(_am_unquote, _AM_ARITH.sub(" ", _AM_HEREDOC.sub(" ", cmd)))
    if "*" not in rest:
        return ""
    env = {}
    for a in _AM_ASSIGN.finditer(rest):
        env.setdefault(a.group(1), _AM_VAR.sub(lambda v: env.get(v.group(1), v.group(0)), a.group(2)))
    cds = [(m.start(), m.group(1)) for m in _AM_CD.finditer(rest)]
    for w in _AM_WORD.finditer(rest):
        word = w.group(0)
        if "*" not in word or word.startswith("-") or "=" in word.split("/")[0]:
            continue
        base = cwd or ""
        for at, d in cds:
            if at < w.start():
                base = _am_dir(d, base, env) or base
        head, pat = os.path.split(word)
        if "*" in head or "*" not in pat:
            continue
        directory = _am_dir(head, base, env) if head else base
        try:
            names = os.listdir(directory) if directory else []
        except OSError:
            continue
        hit = [n for n in fnmatch.filter(names, pat) if pat.startswith(".") or not n.startswith(".")]
        size = sum(len(os.path.join(directory, n)) + 1 for n in hit)
        if size > _ARG_MAX * 0.9:
            return "%s (%d names, %.1f MB of a %.1f MB limit)" % (word, len(hit), size / 1e6, _ARG_MAX / 1e6)
    return ""


def _argmax_line(data):
    """The guard's one line for a PreToolUse Bash call, or ''. ARGMAX_GUARD=0 turns it off. Fail-safe."""
    try:
        if os.environ.get("ARGMAX_GUARD", "1") == "0" or data.get("tool_name") != "Bash":
            return ""
        where = _argmax_trap(((data.get("tool_input") or {}).get("command")) or "", data.get("cwd") or "")
        if not where:
            return ""
        return ("[argmax-guard] the unquoted glob %s expands past the kernel argument limit, so the shell will refuse "
                "that command with 'argument list too long' and run nothing (a 2>/dev/null hides even that, and the "
                "empty output reads as 'no match'). Let the tool walk the folder instead: grep -rl --include='*.md' "
                "PATTERN DIR, or find DIR -name '*.md' -print0 | xargs -0 ..." % where)
    except Exception:
        return ""


def _emit_guard(ev, guard):
    if guard:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": ev, "additionalContext": guard}}))


# MESH LITERAL DOLLAR (30 Sep 2026). A mesh claim, assert or release whose --resource builds a Windows path with a
# backslash in front of a shell variable - "C:\\...\\memory\\$f" typed in a Bash command - reaches the mesh as the
# literal text '...memory$f': the Bash tool halves the double backslash, and bash then reads backslash-dollar as an
# escaped dollar. The lease covers no file, the command exits 0, and the write it was meant to guard goes ahead with
# no claim over it. It happened on 14 Sep, on 27 Sep (thirty claims in one batch) and on 30 Sep, each time with the
# reflex for it on record; a PreToolUse note is read only after the command has run, so the note could not stop it.
# No one means a literal "$name" as a resource, so this refuses the command. A variable holding the whole path, a
# path with forward slashes, and a logical resource (memory:, topic:) pass untouched.
# 2 Oct 2026: the same slip arrived one step removed - W="C:\\...\\memory\\$S.md" assigned in the command, then
# --resource "$W". The assignment already made the dollar literal, so a resource that is only a variable is judged by
# that variable's assignments in the same command; a variable set elsewhere still passes.
_MESH_CMD = re.compile(r"agent_mesh\.py[\"']?\s+(?:--json\s+)?(?:claim|assert|release)\b")
_RESOURCE_ARG = re.compile(r"--resource(?:=|\s+)(\"(?:[^\"\\]|\\.)*\"|'[^']*'|[^\s;&|]+)")
_DOLLAR = re.compile(r"(\\*)\$(?=[A-Za-z_{])")
_VAR_ONLY = re.compile(r"^[\"']?\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))[\"']?$")
_ASSIGN = re.compile(r"(?:^|[\s;&|(])(?:export\s+|local\s+)?([A-Za-z_][A-Za-z0-9_]*)=(\"(?:[^\"\\]|\\.)*\"|'[^']*'|[^\s;&|]+)")


def _literal_dollar_in(value):
    """True when bash will pass this word on (as typed into the Bash tool) with a literal '$name' in it."""
    if value.startswith("'"):
        return bool(re.search(r"\$(?=[A-Za-z_{])", value))   # single quotes never expand
    for d in _DOLLAR.finditer(value):
        # the Bash tool turns each double backslash into one (k -> ceil(k/2)); an odd count left escapes the dollar
        if (len(d.group(1)) + 1) // 2 % 2 == 1:
            return True
    return False


def _mesh_literal_dollar(cmd):
    """The first --resource of a mesh claim, assert or release that bash will pass on with a literal '$name', or ''."""
    if "agent_mesh" not in (cmd or "") or "$" not in cmd or not _MESH_CMD.search(cmd):
        return ""
    for m in _RESOURCE_ARG.finditer(cmd):
        value = m.group(1)
        if _literal_dollar_in(value):
            return value
        v = _VAR_ONLY.match(value)
        if v:
            name = v.group(1) or v.group(2)
            for a in _ASSIGN.finditer(cmd):
                if a.group(1) == name and _literal_dollar_in(a.group(2)):
                    return "%s (set in the command as %s=%s)" % (value, name, a.group(2))
    return ""

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


# python with '-' as its script reads the program from stdin; under Git Bash an empty heredoc or /dev/null is a
# character device (NUL), Python 3.14 takes it for a console and opens pyrepl, which loops on WinError 123 writing
# tracebacks and hangs the whole command (memory python_dash_with_dev_null_opens_a_looping_repl_on_windows_2026_10_19:
# six recurrences across four sessions on 19-20 Oct, each after the memory was in the index). So it is refused, not
# warned. claude-5693aca3, 20 Oct 2026. PYDASH_GUARD=0 turns it off.
_PY = re.compile(r"^(?:py|python[0-9.]*)(?:\.exe)?$", re.I)


def _python_dash(cmd):
    """The first command segment that runs python with '-' as the script, or ''."""
    import shlex
    # PY=.../python.exe; $PY - : the interpreter behind a variable set in the same command (shell state does not
    # persist between calls). It walked past this guard on 1 Nov 17:57 and spun for 90 s. claude-ebf5f521.
    py_vars = {n for n, v in re.findall(r"(?:^|[\s;&|])([A-Za-z_][A-Za-z0-9_]*)=[\"']?([^\s\"';|&]+)", cmd or "")
               if _PY.match(os.path.basename(v.replace("\\", "/")))}
    for seg in re.split(r"\n|;|&&|\|\||\|", cmd or ""):
        try:
            toks = shlex.split(seg.strip(), posix=True)
        except ValueError:
            continue
        while toks and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):   # VAR=x python ...
            toks = toks[1:]
        if toks and toks[0] in ("timeout", "nohup", "time", "exec") and len(toks) > 1:
            toks = toks[2:] if toks[0] == "timeout" and len(toks) > 2 else toks[1:]
        if toks:
            m = re.match(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$", toks[0])
            if m and m.group(1) in py_vars:
                toks[0] = "python"
        if not toks or not _PY.match(os.path.basename(toks[0].replace("\\", "/"))):
            continue
        i = 1
        while i < len(toks):
            t = toks[i]
            if t == "-":
                return seg.strip()[:120]
            if t in ("-c", "-m") or not t.startswith("-"):
                break
            i += 2 if t in ("-X", "-W") else 1
    return ""


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


# A command that only READS (grep, cat, a brain recall ...) cannot start a production service, so a production word
# inside its pattern or query is a mention, not an action. Before 31 Oct a recall whose question named a live
# service, and a grep for this file's own alarm text, each raised the full LIVE-action alarm; an alarm that fires
# on reads is the wallpaper this hook warns about. Every segment must be a reader; ssh, any other python script,
# a launcher, $( ) or a backtick keeps the full scoring (31 Oct 2026).
_READERS = {"grep", "egrep", "fgrep", "rg", "cat", "head", "tail", "less", "wc", "ls", "echo", "printf", "cd", "pwd",
            "sort", "uniq", "cut", "tr", "cmp", "diff", "stat", "file", "test", "[", "true", "false", "date",
            "Select-String", "Get-Content", "Get-ChildItem", "Test-Path", "Measure-Object", "Select-Object", "Write-Output"}
_READ_SCRIPTS = {"brain.py": {"recall", "node", "neighbors", "search", "show"}, "math_recall.py": None,
                 "problem_recall.py": None}
_SHELL_KW = {"do", "then", "else", "elif", "if", "while", "until", "!"}


def _read_only(cmd):
    """True when every segment of a shell command only reads files or queries the brain."""
    if not cmd or "`" in cmd or "$(" in cmd or "<(" in cmd:
        return False
    for stages in _split_cmd(cmd):
        for seg in stages:
            if _seg_kind(seg) not in ("empty", "reader"):
                return False
    return True


# Scripts whose arguments are TEXT for a record (a checklist line, a mesh message, a memory), not an action: a
# production word in "done T-1 'the gateway now restarts cleanly'" is a mention. Before 1 Nov such a line raised the
# full LIVE alarm. Only the named subcommands count; a segment holding $( ) or a backtick is never masked, because
# the shell runs those inside double quotes (1 Nov 2026).
_NOTE_SCRIPTS = {
    "task_checklist_hook.py": {"working", "note", "done", "blocked", "drop", "reopen", "list"},
    "checklist.py": {"working", "note", "done", "blocked", "drop", "reopen", "list"},
    "agent_mesh.py": {"send", "hello", "pulse", "claim", "release", "bye", "digest", "inbox", "status", "assert",
                      "ack", "--json"},
    "brain.py": {"remember", "dream-about"},
    "fleet_board.py": {"broadcast", "ack", "check"},
    "live_inbox.py": {"event"},
}


def _seg_kind(seg):
    """'empty', 'reader' (only reads), 'note' (records text), or 'other' for one shell segment."""
    import shlex
    seg = seg.strip()
    if not seg or seg in ("done", "fi", "esac", "{", "}", "(", ")"):
        return "empty"
    try:
        # posix shlex eats the backslashes of a Windows path (C:\Users\...\x.py), so normalise them first
        toks = shlex.split(seg.replace("\\", "/"), posix=True)
    except ValueError:
        return "other"
    while toks and toks[0] in _SHELL_KW | {"{", "("}:
        toks = toks[1:]
    while toks and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):
        toks = toks[1:]
    if toks and toks[0] == "timeout" and len(toks) > 2:
        toks = toks[2:]
    if not toks:
        return "empty"
    head = os.path.basename(toks[0].replace("\\", "/"))
    if head == "for":
        return "empty"
    if head in _READERS:
        if head in ("grep", "rg") and any(t in ("--exec", "-exec") for t in toks):
            return "other"
        return "reader"
    if head == "sed" and "-n" in toks and not any(t.startswith("-i") for t in toks):
        return "reader"
    if _PY.match(head):
        args = [t for t in toks[1:] if not t.startswith("-")]
        if args:
            script = os.path.basename(args[0].replace("\\", "/"))
            if script in _READ_SCRIPTS:
                subs = _READ_SCRIPTS[script]
                if subs is None or (len(args) > 1 and args[1] in subs):
                    return "reader"
            if script in _NOTE_SCRIPTS:
                rest = toks[toks.index(args[0]) + 1:]
                sub = next((t for t in rest if not t.startswith("-") or t == "--json"), "")
                if sub in _NOTE_SCRIPTS[script]:
                    return "note"
    return "other"


def _split_cmd(cmd):
    """[[stage, ...], ...]: pipelines split on newline ; && || and stages on |, OUTSIDE quotes. A plain regex split
    cut quoted note text at its ';' and scored the pieces as commands (31 Oct 21:4x, a brain.py remember whose
    --evidence held a ';'). An unclosed quote returns the whole command as one stage (never masked)."""
    pipes, stages, cur, q, i, s = [], [], [], "", 0, cmd or ""
    while i < len(s):
        c = s[i]
        if q:
            cur.append(c)
            if c == "\\" and q == '"' and i + 1 < len(s):
                cur.append(s[i + 1]); i += 2; continue
            if c == q:
                q = ""
            i += 1; continue
        if c in "'\"":
            q = c; cur.append(c); i += 1; continue
        two = s[i:i + 2]
        if two in ("&&", "||") or c in "\n;":
            stages.append("".join(cur)); pipes.append(stages); stages, cur = [], []
            i += 2 if two in ("&&", "||") else 1; continue
        if c == "|":
            stages.append("".join(cur)); cur = []; i += 1; continue
        cur.append(c); i += 1
    if q:
        return [[s]]
    stages.append("".join(cur)); pipes.append(stages)
    return pipes


def _action_text(cmd):
    """The command with reader segments and note-writer segments removed: what the production labels are scored on.
    A segment holding $( ), a backtick or <( ) is kept whole, as is anything shlex cannot parse."""
    keep = []
    for stages in _split_cmd(cmd):
        pipeline = "|".join(stages)
        # a reader piped into anything that runs ("grep -r x . | xargs python") feeds that stage: keep the whole pipeline
        if "`" in pipeline or "$(" in pipeline or "<(" in pipeline or \
                any(_seg_kind(s) == "other" for s in stages):
            keep.append(pipeline)
    return "\n".join(keep)


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
        # REFUSED, not warned: a mesh lease on a literal "$name" (see _mesh_literal_dollar). MESH_DOLLAR_GUARD=0 turns it off.
        if ev == "PreToolUse" and data.get("tool_name") == "Bash" and os.environ.get("MESH_DOLLAR_GUARD", "1") != "0":
            _res = _mesh_literal_dollar((data.get("tool_input", {}) or {}).get("command", "") or "")
            if _res:
                sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": ("REFUSED (mesh literal dollar): the resource %s would reach the mesh as "
                        "a literal '$name'. The Bash tool halves a double backslash and bash then reads backslash-dollar "
                        "as an escaped dollar, so the lease would cover no file while the command exits 0. Write the path "
                        "with forward slashes (~/...), or keep the whole path in the variable, or put the "
                        "loop in a script file." % _res[:160])}}))
                return
        # REFUSED, not warned: python with '-' as its script (see _python_dash).
        if ev == "PreToolUse" and data.get("tool_name") == "Bash" and os.environ.get("PYDASH_GUARD", "1") != "0":
            _seg = _python_dash((data.get("tool_input", {}) or {}).get("command", "") or "")
            if _seg:
                sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": ("REFUSED (python dash): '%s' runs python with '-' as the script. Under "
                        "Git Bash an empty heredoc or /dev/null reads as a console, Python opens its REPL and loops on "
                        "WinError 123, hanging the whole command (six times on 19-20 Oct). Write the script with the Write "
                        "tool and run it by file name, or use python -c for one line." % _seg)}}))
                return
        # On an EDIT/WRITE, the danger is not the text mentioning money/secrets/production - it's writing
        # a real credential VALUE (the token-leak shape). Score edits on that alone, so the gut stops
        # crying wolf on every memory/hook edit that quotes a risky word or the word "secret" (the dream's
        # anti-wallpaper finding). Commands and prompts still get the full mention-aware scoring below.
        tool = data.get("tool_name", "")
        guard = "\n".join(x for x in (_backslash_line(data), _argmax_line(data)) if x) if ev == "PreToolUse" else ""
        if ev == "PreToolUse" and tool in ("Edit", "Write", "MultiEdit"):
            hits = {"a secret / security credential": 3} if SECRET_VALUE.search(text) else {}
        else:
            hits = _score(text)
            if ev == "PreToolUse" and tool in ("Bash", "PowerShell"):
                # production labels count only on what the command RUNS: reader segments and the text arguments
                # of note writers (checklist, mesh, brain remember) are dropped first; other labels stay as scored.
                _left = _score(_action_text((data.get("tool_input", {}) or {}).get("command", "") or ""))
                for _lab in ("a LIVE production action", "the live production stack"):
                    if _lab in _left:
                        hits[_lab] = _left[_lab]
                    else:
                        hits.pop(_lab, None)
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
        # No permissionDecision: until 29 Sep a PreToolUse warning here also said "allow", which skips the permission
        # prompt of the very call it warns about. The context reaches the model without a decision.
        out = {"hookSpecificOutput": {"hookEventName": ev,
                                      "additionalContext": "[danger-sense] " + body + ("\n" + guard if guard else "")}}
        sys.stdout.write(json.dumps(out))
    except Exception:
        return


if __name__ == "__main__":
    main()
