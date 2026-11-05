#!/usr/bin/env python3
"""forward_feed.py - feed felt-errors FORWARD so a just-made mistake arrests the next one.

error_monitor.py already records failures to outcomes.jsonl (the post-hoc ERN), but nothing reads them
BEFORE the next action - so the same failure recurs. On 2026-07-11 three separate sessions ran a Bash
command with a C:\\Users\\... backslash path that Git Bash silently collapses ('Usersowner...': No such
file), each landing in outcomes.jsonl, none warned. This closes that loop two ways:

  1. BACKSLASH-PATH (structural): a Windows drive-letter path with backslashes inside a *Bash* command
     breaks where bash or Python reads its backslashes - an unquoted word, a plain Python string, a .py or
     .json heredoc, a trailing backslash before a closing quote. Quoted, raw or heredoc-held it runs fine
     (2026-09-25: firing on the shape alone was right about 2 times in 100). Fire before it runs. Bash-only -
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

# 2026-09-25: WHERE the path sits decides whether it breaks. Over the 6,148 Bash commands of the week to 25 Sep that
# carried a drive-letter backslash path, bash left it alone in double quotes, single quotes and heredoc bodies, and
# Python left it alone in raw strings; the breaks were unquoted words (bash eats the backslashes) and plain Python
# string literals ('C:\Users' is a \U escape, 'C:\temp\new' a tab and a newline). Firing on the shape alone was
# right about 2 times in 100 and ack-blocked 63 commands in that week, 58 of which would have run fine. So the rule
# lexes the command and fires on those two contexts, plus $'...' and a double-quoted path ending in a lone
# backslash before the closing quote; when the command cannot be recovered from the hook's text it falls back to
# firing on the shape, as before.
_RAW_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\")
_HEREDOC_OPEN = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_PYTHON_WORD = re.compile(r"\bpython[0-9.w]*(?:\.exe)?\b", re.I)
_PY_DASH_C = re.compile(r"\bpython[0-9.w]*(?:\.exe)?\b[^\n|;&]*\s-c\s*$", re.I)
_SEP = re.compile(r"&&|\|\||[;|]")
_PY_TARGET = re.compile(r"(?:>>?|\btee\b(?:\s+-a)?)\s*['\"]?[^\s'\"<>|;&]+\.py['\"]?(?![\w.])", re.I)
_JSON_TARGET = re.compile(r"(?:>>?|\btee\b(?:\s+-a)?)\s*['\"]?[^\s'\"<>|;&]+\.json['\"]?(?![\w.])", re.I)


def _command_of(text):
    """The Bash command inside the hook's json.dumps(tool_input) text, which the hook may have cut at MATCH_CAP.
    None when it cannot be recovered."""
    try:
        obj = json.loads(text)
        cmd = obj.get("command") if isinstance(obj, dict) else None
        return cmd if isinstance(cmd, str) else None
    except Exception:
        pass
    m = re.match(r'\s*\{\s*"command"\s*:\s*"', text or "")
    if not m:
        return None
    body = text[m.end():]
    i, n = 0, len(body)
    while i < n and body[i] != '"':
        i += 2 if body[i] == "\\" else 1
    body = body[:min(i, n)]
    body = re.sub(r"\\u[0-9a-fA-F]{0,3}$", "", body)
    if (len(body) - len(body.rstrip("\\"))) % 2:
        body = body[:-1]
    try:
        return json.loads('"' + body + '"')
    except Exception:
        return None


def _py_literal_states(code, positions):
    """Inside Python source `code`, one pass: for each offset in positions, 'raw' / 'plain' when it sits in a
    string literal, 'comment' in a comment, None in bare code."""
    todo = sorted(set(p for p in positions if 0 <= p < len(code)))
    out = dict.fromkeys(positions)
    t, i, n = 0, 0, len(code)
    while i < n and t < len(todo):
        c = code[i]
        if c == "#":
            e = code.find("\n", i)
            e = n if e < 0 else e
            while t < len(todo) and todo[t] < e:
                if todo[t] >= i:
                    out[todo[t]] = "comment"
                t += 1
            i = e + 1
            continue
        if c in "'\"":
            j = i
            while j > 0 and code[j - 1] in "rRbBfFuU" and i - j < 2:
                j -= 1
            if j > 0 and (code[j - 1].isalnum() or code[j - 1] == "_"):
                j = i
            raw = "r" in code[j:i].lower()
            q = code[i:i + 3] if code[i:i + 3] in ('"""', "'''") else c
            k = i + len(q)
            while k < n:
                if code[k] == "\\" and (not raw or code[k + 1:k + 2] == c):
                    k += 2
                    continue
                if code.startswith(q, k) or (len(q) == 1 and code[k] == "\n"):
                    break
                k += 1
            while t < len(todo) and todo[t] < k:
                if todo[t] >= i:
                    out[todo[t]] = "raw" if raw else "plain"
                t += 1
            i = k + len(q)
            continue
        while t < len(todo) and todo[t] <= i:
            t += 1
        i += 1
    return out


_PATH_SEG = re.compile(r"[A-Za-z]:[^\s'\"`;|&<>(),]*")


def _py_escape_breaks(seg):
    """Python reads this path text inside a plain string literal: True when an escape turns it into an error
    (\\U, \\u, \\N, \\x) or silently into other characters (\\n, \\t, \\r, \\a, \\b, \\f, \\v, octal)."""
    k = 0
    while k < len(seg) - 1:
        if seg[k] == "\\":
            nxt = seg[k + 1]
            if nxt == "\\":
                k += 2
                continue
            if nxt in "UuNxntrabfv01234567":
                return True
        k += 1
    return False


def _bash_dq(seg):
    """What bash hands on from a double-quoted (or unquoted-heredoc) run: backslash-backslash becomes one."""
    return re.sub(r"\\([\\$`\"])", r"\1", seg)


def _harness_effective(cmd):
    """The command as the shell receives it. Claude Code's Bash tool collapses each written pair of backslashes into
    one before the shell runs, except a pair that stands before a double quote (od -c, 2026-09-25: p\\\\q -> p\\q,
    r\\\\\\\\s -> r\\\\s, p\\\\" kept), so a path written C:\\\\Users reaches bash and python as C:\\Users."""
    return re.sub(r'\\\\(?!")', lambda m: "\\", cmd)


def _backslash_breaks(cmd):
    """True when a drive-letter backslash path in this Bash command sits where it breaks (see _backslash_reason)."""
    return _backslash_reason(cmd) is not None


def _backslash_reason(cmd, where=None):
    """Where a drive-letter backslash path in this Bash command breaks, or None: an unquoted word with a lone
    backslash, a $'...' string, a plain Python string literal whose escapes change it (python reads the heredoc or
    the -c string, or the heredoc becomes a .py file), JSON text with a lone backslash (the heredoc becomes a .json
    file), or a double-quoted path whose lone trailing backslash escapes the closing quote. Judged on the command
    as the shell will receive it."""
    cmd = _harness_effective(cmd)
    hits = [m.start() for m in _RAW_PATH.finditer(cmd)]
    if not hits:
        return False
    spans = []          # (start, end, kind); anything not covered is an unquoted word
    i, n = 0, len(cmd)
    pending = []
    line_start = 0
    while i < n:
        c = cmd[i]
        if c == "\\":
            i += 2
            continue
        if c == "<" and cmd.startswith("<<<", i):
            i += 3
            continue
        if c == "#" and (i == 0 or cmd[i - 1] in " \t\n;&|("):
            e = cmd.find("\n", i)
            e = n if e < 0 else e
            spans.append((i, e, "comment"))
            i = e
            continue
        if c == "$" and cmd.startswith("$'", i):
            j = i + 2
            while j < n and cmd[j] != "'":
                j += 2 if cmd[j] == "\\" else 1
            spans.append((i, j + 1, "ansi"))
            i = j + 1
            continue
        if c in "'\"":
            j = i + 1
            if c == "'":
                j = cmd.find("'", j)
                j = n if j < 0 else j
            else:
                while j < n and cmd[j] != '"':
                    j += 2 if cmd[j] == "\\" else 1
            is_py = bool(_PY_DASH_C.search(cmd[line_start:i]))
            spans.append((i + 1, j, ("py-" if is_py else "") + ("single" if c == "'" else "double")))
            i = j + 1
            continue
        if c == "<":
            m = _HEREDOC_OPEN.match(cmd, i)
            if m:
                e = cmd.find("\n", i)
                e = n if e < 0 else e
                seps = list(_SEP.finditer(cmd, line_start, i))
                pre = cmd[seps[-1].end() if seps else line_start:i]
                nxt = _SEP.search(cmd, m.end(), e)
                simple = pre + " " + cmd[m.end():nxt.start() if nxt else e]
                # python reads the body (python ... <<EOF), or the body becomes a .py or .json file
                target = ("py-" if _PYTHON_WORD.search(pre) or _PY_TARGET.search(simple) else
                          "json-" if _JSON_TARGET.search(simple) else "")
                pending.append((m.group(2), target, bool(m.group(1))))
                i = m.end()
                continue
        if c == "\n":
            i += 1
            for delim, target, quoted in pending:
                start = i
                while i < n:
                    e = cmd.find("\n", i)
                    e = n if e < 0 else e
                    if cmd[i:e].strip() == delim:
                        break
                    i = e + 1
                spans.append((start, min(i, n), target + "heredoc" + ("-q" if quoted else "")))
                if i < n:
                    e = cmd.find("\n", i)
                    i = n if e < 0 else e + 1
            pending = []
            line_start = i
            continue
        i += 1
    span_of = {}
    for pos in hits:
        for sp in spans:
            if sp[0] <= pos < sp[1]:
                span_of[pos] = sp
                break
    py_state = {}
    for sp in set(span_of.values()):
        if sp[2].startswith("py-"):
            mine = [p - sp[0] for p, x in span_of.items() if x == sp]
            for off, st in _py_literal_states(cmd[sp[0]:sp[1]], mine).items():
                py_state[off + sp[0]] = st
    for pos in hits:
        kind = span_of.get(pos)
        seg = _PATH_SEG.match(cmd, pos).group(0)
        if len(seg.rstrip("\\")) < 5:
            continue    # 'C:\' alone or a label such as "C:\n" is not a path
        why = None
        if kind is None:
            # unquoted: bash turns a lone backslash into nothing and a doubled one into one
            if "\\" in seg.replace("\\\\", ""):
                why = "unquoted word"
        else:
            s, e, k = kind
            sees = seg if k in ("py-single", "py-heredoc-q", "json-heredoc-q") else _bash_dq(seg)
            if k == "ansi":
                why = "$'...' string"
            elif k.startswith("py-"):
                if py_state.get(pos) == "plain" and _py_escape_breaks(sees):
                    why = "plain Python string (%s)" % k
            elif k.startswith("json-"):
                if "\\" in sees.replace("\\\\", ""):
                    why = "JSON text (%s)" % k
            elif k == "double" and cmd[max(0, pos - 2):pos] != '\\"':
                # a lone trailing backslash eats the closing quote (ls "C:\dir\" 2>&1 -> "cannot access
                # 'C:\dir" 2>&1 ...'); an escaped quote that another quote follows is a nested quote on purpose
                # (schtasks /TR "wscript.exe //B \"C:\...\x.vbs\"", cmd //c "rd /s /q \"\\?\C:\...\"")
                run = re.match(r'[A-Za-z]:[^\s"]*', cmd[pos:]).group(0)
                after = pos + len(run)
                if (after < n and cmd[after] == '"' and (len(run) - len(run.rstrip("\\"))) % 2 == 1
                        and cmd[after + 1:after + 2] not in ('"', "'")):
                    why = "trailing backslash escapes the closing quote"
        if why:
            if where is not None:
                where.append(pos)
            return why
    return None

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
        # 1. backslash Windows path in a Bash command, where it breaks (see _backslash_reason)
        why_bs = None
        if tool_name in (None, "Bash") and _BACKSLASH_PATH.search(t):
            _cmd = _command_of(t)
            why_bs = _backslash_reason(_cmd) if _cmd is not None else "command text unreadable, judged on its shape"
        if why_bs:
            fires.append({
                "id": "windows_backslash_path",
                # a structural break (the command WILL fail or change) -> charge>=0.95 so the mindware-override
                # escalation fires when you read it past repeatedly
                "charge": 0.97,
                "gut": ("A C:\\ path in this Bash command breaks where it sits (%s): Git Bash strips the backslashes "
                        "from an unquoted C:\\Users\\user ('C:Usersuser', No such file); Python reads 'C:\\Users' "
                        "in a plain string as a \\U escape and \\n, \\t, \\v in a path as control characters; a "
                        "backslash before a closing quote escapes it. The Bash tool also halves a written double "
                        "backslash, so C:\\\\Users reaches python as C:\\Users. Use forward slashes (C:/Users/user/...) "
                        "or a raw string r'...', or put text with backslashes through the Write tool." % why_bs),
                "why": "structural: " + why_bs,
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
        (_j.dumps({"command": r"python C:\Users\user\.claude\dreaming\incubate.py"}), "Bash", True),
        (_j.dumps({"command": "python <HOME>/.claude/dreaming/incubate.py"}), "Bash", False),
        (_j.dumps({"command": r"powershell C:\Users\user\x.ps1"}), "PowerShell", False),
        (_j.dumps({"command": "for f in rolls:\n  try:\n    pass"}), "Bash", False),  # code colon+newline, NOT a path
        (_j.dumps({"command": "grep -n def main foo.py"}), "Bash", False),
        ("", "Bash", False),
        # 2026-09-25: where the path sits decides. Each case is a command as an agent writes it.
        (_j.dumps({"command": r'python "C:\Users\user\x.py"'}), "Bash", False),                 # double quotes: bash keeps it
        (_j.dumps({"command": r"cat 'C:\Users\user\x.txt'"}), "Bash", False),                   # single quotes
        (_j.dumps({"command": "python - <<'EOF'\np = r'C:\\Users\\user\\x'\nEOF"}), "Bash", False),   # raw literal
        (_j.dumps({"command": "python - <<'EOF'\np = 'C:\\Users\\user\\x'\nEOF"}), "Bash", True),     # \U escape
        (_j.dumps({"command": "python - <<'EOF'\np = 'C:\\\\Users\\\\user'\nEOF"}), "Bash", True),    # the tool halves \\
        (_j.dumps({"command": "python - <<'EOF'\n# under C:\\Users\\user\nEOF"}), "Bash", False),      # a comment
        (_j.dumps({"command": "cat > t.py <<'EOF'\nP = \"C:\\Users\\user\"\nEOF"}), "Bash", True),    # becomes a .py file
        (_j.dumps({"command": "cat > t.json <<'EOF'\n{\"p\": \"C:\\Users\"}\nEOF"}), "Bash", True),    # invalid JSON
        (_j.dumps({"command": "cat > t.md <<'EOF'\nsee C:\\Users\\user\\notes\nEOF"}), "Bash", False), # prose file
        (_j.dumps({"command": r'python -c "import sys; sys.path.insert(0, r' + "'" + r'C:\Users\user\x' + "'" + ')"'}), "Bash", False),
        (_j.dumps({"command": r'python -c "open(' + "'" + r'C:\Users\user\x' + "'" + ')"'}), "Bash", True),
        (_j.dumps({"command": r'ls -la "C:\Users\user\dir\" 2>&1 | head'}), "Bash", True),     # \" eats the quote
        (_j.dumps({"command": r'schtasks /Create /TN X /TR "wscript.exe //B \"C:\Users\user\x.vbs\"" /F'}), "Bash", False),
        (_j.dumps({"command": r'cmd.exe //c "rd /s /q \"\\?\C:\Users\user\wt\x\""'}), "Bash", False),
        (_j.dumps({"command": r"test -f C:\Users\user\x.py && echo yes"}), "Bash", True),      # unquoted
        (_j.dumps({"command": "python - <<'EOF'\nprint('log10 C:\\n', 1)\nEOF"}), "Bash", False),     # a label, not a path
        ('{"command": "python C:\\\\Users\\\\user\\\\x.py ' + "y" * 40, "Bash", True),         # cut text: still judged
        ('{"timeout": 5, "command": "python C:\\\\Users\\\\user\\\\x.py', "Bash", True),         # unreadable: fire on shape
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
