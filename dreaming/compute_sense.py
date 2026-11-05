#!/usr/bin/env python3
"""compute_sense.py - the compute instinct: any session automatically feels machine distress.

Registered on UserPromptSubmit and PreToolUse (Bash|PowerShell). Reads the state file the
compute governor publishes every ~20s (~/.claude/compute/compute_state.json) and,
when the machine (this PC or the remote compute box) is in distress, injects the compute contract as
context - so every working Claude session "just knows" and switches to fixing it before its
task. Silent when all is well. Doctrine: 12_cognitive_architecture/compute_governor/COMPUTE_DOCTRINE.md.

The local-GPU crash gate is evaluated before governor state and fails closed only for
positively identified CUDA/diffusers execution. Everything else preserves the historical
fail-open behavior: unexpected errors produce no output and never block unrelated work.
"""
import sys, os, io, json, time, re, ast, tokenize, threading, ctypes, math
import calendar

# Derived, so this tree carries no single machine's username.
_HOME = os.path.expanduser("~")

STATE_DIR = os.path.join(_HOME, ".claude", "compute")
STATE_FILE = os.path.join(STATE_DIR, "compute_state.json")
SEEN_FILE = os.path.join(STATE_DIR, ".compute_sense_seen.json")
STALE_SEC = 600          # governor heartbeat older than this = governor itself is down
DEDUP_DISTRESS = 600     # re-remind at most every 10 min per session while distress persists
DEDUP_WARN = 1800
DEDUP_TRADING = 21600    # operational observation, not a compute-pressure interrupt
DEDUP_STALE = 14400      # governor-down nag: once per session per 4h

LOCAL_GPU_DENY_REASON = (
    "BLOCKED (local GPU safety): local CUDA/diffusers execution can crash this laptop. "
    "Run this workload over SSH on a remote compute box. "
    "Read-only GPU diagnostics remain allowed."
)
GPU_SOURCE_LIMIT = 512 * 1024
GPU_SOURCE_TIMEOUT = 0.075
EXACT_CRASH_SCRIPT = os.path.normcase(
    os.path.normpath(os.path.join(_HOME, "film_project", "tools", "render_stills.py"))
)
EXACT_CRASH_MODULE = "tools.render_stills"
_PYTHON_EXE = re.compile(
    r"^(?:pythonw?|py)(?:\d+(?:\.\d+)*)?(?:\.exe)?$", re.IGNORECASE
)
_GPU_CODE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        r"\bfrom\s+diffusers(?:\.[\w.]+)?\s+import[^\r\n;]*"
        r"(?:StableDiffusionXLPipeline|DiffusionPipeline|AutoPipelineFor\w+)",
        r"\b(?:StableDiffusionXLPipeline|DiffusionPipeline|AutoPipelineFor\w+)"
        r"\s*\.\s*from_pretrained\s*\(",
        r"\bdiffusers\s*\.\s*"
        r"(?:StableDiffusionXLPipeline|DiffusionPipeline|AutoPipelineFor\w+)\b",
        r"\btorch\s*\.\s*Generator\s*\([^)]*[\"']cuda(?:\:\d+)?[\"']",
        r"\btorch\s*\.\s*device\s*\(\s*[\"']cuda(?:\:\d+)?[\"']",
        r"\btorch\s*\.\s*set_default_device\s*\(\s*[\"']cuda(?:\:\d+)?[\"']",
        r"\.\s*to\s*\(\s*[\"']cuda(?:\:\d+)?[\"']",
        r"\.\s*cuda\s*\(",
        r"\bdevice\s*=\s*[\"']cuda(?:\:\d+)?[\"']",
        r"\bdevice_map\s*=\s*[\"']cuda(?:\:\d+)?[\"']",
        r"\benable_model_cpu_offload\s*\(",
        r"^\s*(?:from\s+cupy\b|import\s+cupy\b)",
        r"^\s*(?:from\s+numba\s+import\s+cuda\b|import\s+numba\s*\.\s*cuda\b)",
    )
)


def _split_shell(command):
    """Split executable shell clauses without splitting separators inside quotes."""
    clauses, current = [], []
    quote = None
    escaped = False
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            current.append(ch)
            if escaped:
                escaped = False
            elif ch == "`" or (
                quote == '"'
                and ch == "\\"
                and i + 1 < len(command)
                and command[i + 1] == '"'
            ):
                escaped = True
            elif ch == quote:
                # PowerShell escapes a single quote inside a single-quoted string as ''.
                if quote == "'" and i + 1 < len(command) and command[i + 1] == "'":
                    current.append(command[i + 1])
                    i += 1
                else:
                    quote = None
            i += 1
            continue
        if (
            ch in ("`", "\\")
            and i + 1 < len(command)
            and command[i + 1] in ("\r", "\n")
        ):
            # PowerShell (`) and Bash (\) line continuations are whitespace, not
            # command boundaries. Normalize them before clause routing.
            current.append(" ")
            i += 2
            if command[i - 1] == "\r" and i < len(command) and command[i] == "\n":
                i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
            i += 1
            continue
        ampersand_is_redirection = (
            ch == "&"
            and i > 0
            and command[i - 1] in (">", "<")
            and i + 1 < len(command)
            and command[i + 1].isdigit()
        )
        ampersand_is_boundary = (
            ch == "&"
            and not ampersand_is_redirection
            and (
                (i + 1 < len(command) and command[i + 1] == "&")
                or bool("".join(current).strip())
            )
        )
        if ch in ("\r", "\n", ";", "|") or ampersand_is_boundary:
            clause = "".join(current).strip()
            if clause:
                clauses.append(clause)
            current = []
            if ch in ("|", "&") and i + 1 < len(command) and command[i + 1] == ch:
                i += 1
            i += 1
            continue
        current.append(ch)
        i += 1
    clause = "".join(current).strip()
    if clause:
        clauses.append(clause)
    return clauses


def _shell_tokens(clause):
    """Small cross-shell tokenizer sufficient for executable and Python argument routing."""
    tokens, current = [], []
    quote = None
    escaped = False
    i = 0
    while i < len(clause):
        ch = clause[i]
        if quote:
            if escaped:
                current.append(ch)
                escaped = False
            elif ch == "`" or (
                quote == '"'
                and ch == "\\"
                and i + 1 < len(clause)
                and clause[i + 1] == '"'
            ):
                escaped = True
            elif ch == quote:
                if quote == "'" and i + 1 < len(clause) and clause[i + 1] == "'":
                    current.append("'")
                    i += 1
                else:
                    quote = None
            else:
                current.append(ch)
        elif ch in ("'", '"'):
            quote = ch
        elif ch.isspace():
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(ch)
        i += 1
    if current:
        tokens.append("".join(current))
    return tokens


def _basename(token):
    return token.replace("/", os.sep).rsplit("\\", 1)[-1].lower()


def _strip_shell_prefixes(tokens):
    i = 0
    while i < len(tokens) and tokens[i] in ("&", ".", "command"):
        i += 1
    while i < len(tokens) and re.match(
        r"^(?:\$env:|[A-Za-z_][A-Za-z0-9_]*)[^=]*=", tokens[i], re.IGNORECASE
    ):
        i += 1
    if i < len(tokens) and _basename(tokens[i]) == "env":
        i += 1
        while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("-"):
            i += 1
    if i < len(tokens) and _basename(tokens[i]) == "nice":
        i += 1
        if i < len(tokens) and tokens[i] == "-n":
            i += 2
    return tokens[i:]


def _gpu_code(text):
    if not text:
        return False
    # Comments and docstrings must not turn a read-only/CPU script into a denial.
    docstrings = set()
    try:
        tree = ast.parse(text)
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if (
                isinstance(body, list)
                and body
                and isinstance(body[0], ast.Expr)
                and isinstance(getattr(body[0], "value", None), ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(
                    (
                        body[0].value.lineno,
                        body[0].value.col_offset,
                        body[0].value.end_lineno,
                        body[0].value.end_col_offset,
                    )
                )
    except (SyntaxError, ValueError, TypeError):
        pass
    try:
        cleaned = []
        for token_info in tokenize.generate_tokens(io.StringIO(text).readline):
            replacement = token_info.string
            if token_info.type == tokenize.COMMENT:
                replacement = ""
            elif token_info.type == tokenize.STRING and any(
                token_info.start >= span[:2] and token_info.end <= span[2:]
                for span in docstrings
            ):
                replacement = '""'
            cleaned.append(
                tokenize.TokenInfo(
                    token_info.type,
                    replacement,
                    token_info.start,
                    token_info.end,
                    token_info.line,
                )
            )
        text = tokenize.untokenize(cleaned)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return any(pattern.search(text) for pattern in _GPU_CODE_PATTERNS)


def _fixed_local_path(path):
    normalized = os.path.normcase(os.path.normpath(os.path.abspath(path)))
    if normalized.startswith(("\\\\", r"\??\\")):
        return False
    drive, _ = os.path.splitdrive(normalized)
    if not drive:
        return False
    try:
        # DRIVE_FIXED=3, DRIVE_RAMDISK=6. Reject UNC and mapped drives before IO.
        return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") in (3, 6)
    except Exception:
        return drive.lower() == "c:"


def _source_text(path):
    normalized = os.path.normcase(os.path.normpath(os.path.abspath(path)))
    if not _fixed_local_path(normalized):
        return ""
    result = []

    def read_source():
        try:
            with io.open(normalized, "rb") as source:
                result.append(
                    source.read(GPU_SOURCE_LIMIT + 1)[:GPU_SOURCE_LIMIT].decode(
                        "utf-8", errors="replace"
                    )
                )
        except Exception:
            result.append("")

    # A fixed-drive junction can still target a slow volume. A daemon reader
    # preserves the hook's never-block contract even in that case.
    reader = threading.Thread(target=read_source, daemon=True)
    reader.start()
    reader.join(GPU_SOURCE_TIMEOUT)
    return result[0] if result else ""


def _script_candidate(script, cwd):
    script = os.path.expandvars(os.path.expanduser(script))
    # Git Bash/MSYS and WSL spell Windows paths as /c/... or /mnt/c/....
    match = re.match(r"^/([A-Za-z])/(.*)$", script)
    if not match:
        match = re.match(r"^/mnt/([A-Za-z])/(.*)$", script)
    if match:
        script = match.group(1) + ":\\" + match.group(2).replace("/", os.sep)
    candidate = script if os.path.isabs(script) else os.path.join(cwd, script)
    return os.path.normcase(os.path.normpath(os.path.abspath(candidate)))


def _python_invocation_is_gpu(args, cwd):
    if not args:
        return False
    joined_args = " ".join(args).replace("/", os.sep).lower()
    if (
        EXACT_CRASH_SCRIPT.lower() in joined_args
        or EXACT_CRASH_MODULE in joined_args.replace("\\", ".")
    ):
        return True
    for i, arg in enumerate(args):
        if arg == "-c":
            code = " ".join(args[i + 1:])
            return _gpu_code(code)
        if arg == "-m" and i + 1 < len(args):
            module = args[i + 1].strip(" \t\r\n'\"")
            if module.lower() == EXACT_CRASH_MODULE:
                return True
            module_path = module.replace(".", os.sep) + ".py"
            candidate = _script_candidate(module_path, cwd)
            if candidate == EXACT_CRASH_SCRIPT:
                return True
            return _gpu_code(_source_text(candidate))

    cleaned_args = [
        arg.strip(" \t\r\n'\"@()[]{},") for arg in args
    ]
    script = next(
        (arg for arg in cleaned_args if arg.lower().endswith((".py", ".pyw"))), ""
    )
    if not script:
        return False
    normalized = _script_candidate(script, cwd)
    if normalized == EXACT_CRASH_SCRIPT:
        return True
    return _gpu_code(_source_text(normalized))


def _clause_is_local_gpu(clause, cwd):
    tokens = _strip_shell_prefixes(_shell_tokens(clause))
    if not tokens:
        return False
    executable = _basename(tokens[0])
    if executable in ("ssh", "ssh.exe", "plink", "plink.exe"):
        return False

    # Wrapper handling closes common local-shell bypasses without treating remote
    # commands as local merely because their quoted payload contains "python".
    if executable in ("cmd", "cmd.exe"):
        for i, token in enumerate(tokens[1:], 1):
            if token.lower() in ("/c", "/k"):
                return any(
                    _clause_is_local_gpu(inner, cwd)
                    for inner in _split_shell(" ".join(tokens[i + 1:]))
                )
        return False
    if executable in ("powershell", "powershell.exe", "pwsh", "pwsh.exe"):
        for i, token in enumerate(tokens[1:], 1):
            if token.lower() in ("-command", "-c"):
                return any(
                    _clause_is_local_gpu(inner, cwd)
                    for inner in _split_shell(" ".join(tokens[i + 1:]))
                )
        return False
    if executable in ("bash", "bash.exe", "sh", "sh.exe", "dash", "zsh"):
        for i, token in enumerate(tokens[1:], 1):
            if token.lower() == "--command" or re.fullmatch(
                r"-[A-Za-z]*c[A-Za-z]*", token
            ):
                return any(
                    _clause_is_local_gpu(inner, cwd)
                    for inner in _split_shell(" ".join(tokens[i + 1:]))
                )
        return False
    if executable in ("start-process", "start"):
        nested = _strip_shell_prefixes(tokens[1:])
        target_index = None
        for i, token in enumerate(nested):
            if token.lower() in ("-filepath", "-file"):
                if i + 1 < len(nested):
                    target_index = i + 1
                break
            if not token.startswith("-"):
                target_index = i
                break
        if target_index is None:
            return False
        target = _basename(nested[target_index])
        if not _PYTHON_EXE.match(target):
            return False
        argument_marker = next(
            (
                i for i, token in enumerate(nested)
                if token.lower() in ("-argumentlist", "-args")
            ),
            None,
        )
        raw_arguments = (
            nested[argument_marker + 1:]
            if argument_marker is not None
            else nested[target_index + 1:]
        )
        arguments = []
        for token in raw_arguments:
            # PowerShell commonly supplies ArgumentList as one quoted string.
            expanded = _shell_tokens(token)
            arguments.extend(expanded or [token])
        return _python_invocation_is_gpu(arguments, cwd)
    if executable == "uv" and len(tokens) > 2 and tokens[1].lower() == "run":
        if _basename(tokens[2]) == "render_stills.py":
            return True
        if _PYTHON_EXE.match(_basename(tokens[2])):
            return _python_invocation_is_gpu(tokens[3:], cwd)
        return False
    if not _PYTHON_EXE.match(executable):
        return False
    return _python_invocation_is_gpu(tokens[1:], cwd)


def _local_gpu_deny(data):
    """Return the deny response for dangerous local GPU execution, else None."""
    event = data.get("hook_event_name") or (
        "PreToolUse" if data.get("tool_name") else ""
    )
    tool = str(data.get("tool_name") or "")
    if event != "PreToolUse" or tool.lower() not in ("bash", "powershell"):
        return None
    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    cwd = data.get("cwd") or tool_input.get("cwd") or os.getcwd()
    for clause in _split_shell(command):
        tokens = _strip_shell_prefixes(_shell_tokens(clause))
        if tokens and _basename(tokens[0]) in ("cd", "chdir", "set-location", "pushd"):
            targets = [token for token in tokens[1:] if not token.startswith("-")]
            if targets:
                target = os.path.expandvars(os.path.expanduser(targets[0]))
                cwd = target if os.path.isabs(target) else os.path.abspath(
                    os.path.join(cwd, target)
                )
            continue
        if _clause_is_local_gpu(clause, cwd):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": LOCAL_GPU_DENY_REASON,
                }
            }
    return None


def _governor_alive():
    """Is the process named in governor.pid actually running? (cheap, no subprocess)

    Returns True/False, or None if it cannot be determined. A LIVE process changes
    the meaning of a stale heartbeat completely: the watchdog is not down, so
    "restart it" is the wrong instruction (a second governor is worse than a late
    one - both would renice the same processes and race on the state file).
    """
    try:
        import ctypes
        with io.open(os.path.join(STATE_DIR, "governor.pid"), "r") as f:
            pid = int(f.read().strip())
        if os.name != "nt":
            import posix_proc
            return posix_proc.alive(pid)
        # PROCESS_QUERY_LIMITED_INFORMATION - works without elevation
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == 259  # STILL_ACTIVE
            return True
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        return None


def _slept_recently(gap_sec):
    """Did this machine spend the stale window suspended rather than wedged?

    Modern Standby freezes desktop processes (the Desktop Activity Moderator) while
    the system clocks keep ticking - so GetTickCount64 and QueryUnbiasedInterruptTime
    are BOTH useless here (measured 2026-07-15: neither excluded a 60.7-min standby).
    The event log is the only honest source, and it costs ~1s - which is why this is
    called ONLY on the rare alarm path, never on the silent fast path.
    """
    try:
        import subprocess
        mins = max(2, int(gap_sec / 60) + 5)
        ps = (
            "$e = Get-WinEvent -FilterHashtable @{LogName='System';"
            "ProviderName='Microsoft-Windows-Kernel-Power';Id=507;"
            "StartTime=(Get-Date).AddMinutes(-%d)} -MaxEvents 1 -ErrorAction SilentlyContinue;"
            "if ($e) { 'RESUMED' }" % mins
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=8,
            creationflags=(0x08000000 if os.name == "nt" else 0),  # CREATE_NO_WINDOW - never flash a console
        )
        return "RESUMED" in (out.stdout or "")
    except Exception:
        return False


def _load_seen():
    try:
        with io.open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_seen(seen):
    try:
        if len(seen) > 400:
            seen = dict(sorted(seen.items(), key=lambda kv: kv[1])[-200:])
        with io.open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(seen, f)
    except Exception:
        pass


def _dedup(seen, key, gap):
    now = time.time()
    last = seen.get(key, 0)
    if now - last < gap:
        return False
    seen[key] = now
    return True


def _fmt_top(top):
    try:
        items = []
        for t in (top or [])[:4]:
            items.append("%s(pid %s) %s%%" % (t.get("name"), t.get("pid"), t.get("cpu_pct")))
        return ", ".join(items) if items else "n/a"
    except Exception:
        return "n/a"


def _nonnegative_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _fmt_number(value):
    number = _nonnegative_number(value)
    if number is None:
        return None
    return "{:,.1f}".format(number).rstrip("0").rstrip(".")


def _is_memory_pressure(reasons):
    if isinstance(reasons, str):
        reasons = [reasons]
    try:
        return any(str(reason).startswith("memory_") for reason in (reasons or []))
    except TypeError:
        return False


def _fmt_top_private(top_private):
    if not isinstance(top_private, (list, tuple)):
        return ""
    items = []
    for item in top_private:
        if len(items) >= 4:
            break
        if not isinstance(item, dict):
            continue
        private_mb = _fmt_number(item.get("private_mb"))
        if private_mb is None:
            continue
        name = str(item.get("name") or "unknown").replace("\r", " ").replace("\n", " ")
        pid = item.get("pid")
        label = "%s(pid %s)" % (name, pid) if pid is not None else name
        detail = "%s private=%sMB" % (label, private_mb)
        working_mb = _fmt_number(item.get("working_mb"))
        if working_mb is not None:
            detail += " working=%sMB" % working_mb
        items.append(detail)
    return ", ".join(items)


def _load_is_io_not_cpu(az):
    """Flag a high loadavg that is I/O wait rather than CPU saturation.

    Linux loadavg counts processes in uninterruptible sleep, so a box doing heavy
    I/O reads a high load1 while its CPUs sit idle. Measured 2026-08-08: the PC
    copy of the Azure reading said load1 24.71 with psi_cpu60 3.37 and psi_io60
    52.34, while the BOX's own sentinel said status=ok load1 4.19 and two live
    reads found 5.14 and 4.09. Agents read "load 24.7" as saturation and paused
    real work over it. PSI averages 60s and cannot be spiked by one process, so
    it is the better witness - say so in the same breath as the load figure.
    """
    try:
        load1 = float(az.get("load1"))
        psi_cpu = float(az.get("psi_cpu_some_avg60"))
    except (TypeError, ValueError):
        return ""
    if load1 < 8.0 or psi_cpu >= 20.0:
        return ""
    try:
        psi_io = float(az.get("psi_io_some_avg60"))
    except (TypeError, ValueError):
        psi_io = None
    if psi_io is not None and psi_io > 2 * psi_cpu:
        return (" NOTE: load1 %.1f with CPU pressure only %.1f%% and I/O pressure "
                "%.1f%% over the same 60s - this load is I/O WAIT, not CPU "
                "saturation. Do not read it as a busy box; verify live before "
                "pausing work over it." % (load1, psi_cpu, psi_io))
    return (" NOTE: load1 %.1f but CPU pressure is only %.1f%% over 60s - those "
            "disagree, so treat the load figure as unconfirmed and verify live."
            % (load1, psi_cpu))


def _memory_pressure_context(st, reasons):
    if not _is_memory_pressure(reasons):
        return ""
    metrics = []
    commit_pct = _fmt_number(st.get("commit_used_pct"))
    commit_used = _fmt_number(st.get("commit_used_mb"))
    commit_limit = _fmt_number(st.get("commit_limit_mb"))
    commit_headroom = _fmt_number(st.get("commit_avail_mb"))
    if commit_pct is not None:
        metrics.append("commit=%s%%" % commit_pct)
    if commit_used is not None and commit_limit is not None:
        metrics.append("used/limit=%s/%sMB" % (commit_used, commit_limit))
    if commit_headroom is not None:
        metrics.append("headroom=%sMB" % commit_headroom)
    if metrics:
        context = " Commit: %s." % ", ".join(metrics)
    else:
        context = " Commit telemetry is unavailable."
    top_private = _fmt_top_private(st.get("top_private"))
    if top_private:
        context += " Top private-memory consumers: %s." % top_private
    return context


# Processes agents cannot exit or restart without elevation: exiting agent model
# workers cannot release commit these hold. Exact lowercase name -> the owner-level
# release. Measured case 2026-08-06: SearchIndexer at 14.7GB private, unsheddable by
# every agent for three dream shifts while the guidance said "exit model workers".
UNSHEDDABLE_HOLDERS = {
    "searchindexer": "an elevated Restart-Service WSearch, or a reboot",
    "searchindexer.exe": "an elevated Restart-Service WSearch, or a reboot",
    "memory compression": "a reboot",
    "vmmem": "shutting down the Hyper-V VM that owns it",
    "vmmemwsl": "wsl --shutdown, by the owner of that VM",
}


def _dominant_unsheddable(st):
    """The top private-memory holder, iff agents cannot release it and it dominates.

    Dominance guard: the known-unsheddable process must be the #1 holder, hold at
    least 8GB private, and at least twice the #2 holder - otherwise agent work may
    still be a meaningful share of commit and the shed advice stands unchanged.
    Returns (holder_label, owner_fix) or None.
    """
    top = st.get("top_private") if isinstance(st, dict) else None
    if not isinstance(top, (list, tuple)) or not top or not isinstance(top[0], dict):
        return None
    first = top[0]
    fix = UNSHEDDABLE_HOLDERS.get(str(first.get("name") or "").strip().lower())
    if not fix:
        return None
    first_mb = _nonnegative_number(first.get("private_mb"))
    if first_mb is None or first_mb < 8000:
        return None
    second_mb = 0.0
    if len(top) > 1 and isinstance(top[1], dict):
        second_mb = _nonnegative_number(top[1].get("private_mb")) or 0.0
    if first_mb < 2 * second_mb:
        return None
    label = str(first.get("name") or "unknown")
    pid = first.get("pid")
    if pid is not None:
        label = "%s(pid %s)" % (label, pid)
    return "%s holding %sMB private" % (label, _fmt_number(first_mb)), fix


# Processes an agent can actually exit. Everything else in the top-private list is
# owner-side - a sync client, a browser, a VM, a Windows service - and no amount of
# agent load shedding releases a byte of it. Measured 2026-08-08: top four were
# GoogleDriveFS 4,773MB, vmmem 4,104MB, brain-framework pythonw 1,579MB and a Claude
# session 1,252MB, so agent work was 24% of measured commit with nothing individually
# large - and the guidance still said "exit enough restartable model workers".
AGENT_PROCESS_NAMES = ("claude", "python", "pythonw", "node", "ollama", "llama",
                       "llama.cpp", "torch", "uvicorn", "jupyter")

# An agent process this big IS worth exiting on its own, whatever the share.
AGENT_SINGLE_MB = 2000.0
# Below this share of measured commit, shedding every agent process cannot fix it.
AGENT_SHARE_MAX = 0.30


def _agent_shed_is_futile(st):
    """True when no agent-side process holds enough commit for shedding to matter.

    Returns (agent_mb, total_mb, owner_side_label) or None when the telemetry is
    too thin to judge - in which case the caller keeps the standard shed advice,
    because a missing measurement is not evidence of a clean footprint.
    """
    top = st.get("top_private") if isinstance(st, dict) else None
    if not isinstance(top, (list, tuple)) or len(top) < 3:
        return None
    agent_mb = 0.0
    total_mb = 0.0
    biggest_agent = 0.0
    owner_side = []
    for entry in top:
        if not isinstance(entry, dict):
            return None
        mb = _nonnegative_number(entry.get("private_mb"))
        if mb is None:
            return None
        total_mb += mb
        name = str(entry.get("name") or "").strip().lower()
        base = name[:-4] if name.endswith(".exe") else name
        if base in AGENT_PROCESS_NAMES:
            agent_mb += mb
            biggest_agent = max(biggest_agent, mb)
        else:
            owner_side.append("%s %sMB" % (entry.get("name") or "unknown",
                                           _fmt_number(mb)))
    if total_mb <= 0 or not owner_side:
        return None
    if biggest_agent >= AGENT_SINGLE_MB:
        return None
    if agent_mb >= AGENT_SHARE_MAX * total_mb:
        return None
    return agent_mb, total_mb, ", ".join(owner_side[:3])


def _memory_pressure_guidance(reasons, st=None):
    if not _is_memory_pressure(reasons):
        return ""
    unsheddable = _dominant_unsheddable(st or {})
    if unsheddable:
        holder, fix = unsheddable
        return (
            "Memory pressure with an agent-unsheddable holder: %s is a SYSTEM/service-side "
            "process - exiting agent model workers cannot release this commit, so do not "
            "abandon or pause light work over it. Do not launch new model-heavy work and "
            "keep your own footprint to short-lived processes. Releasing this commit "
            "requires the owner: %s. If your report has not yet said so plainly, say it."
            % (holder, fix)
        )
    futile = _agent_shed_is_futile(st or {})
    if futile:
        agent_mb, total_mb, owner_side = futile
        return (
            "Memory pressure, but NOTHING OF OURS IS HOLDING IT: agent-side processes are "
            "%sMB of the %sMB measured top consumers (%.0f%%), none individually large. "
            "The commit is owner-side: %s. Shedding or pausing agent work cannot release "
            "it, so do NOT abandon your task over this - that is the click-past trap in "
            "reverse. Do keep your local footprint to short-lived processes and put heavy "
            "work on the research boxes. If your report has not named which owner-side "
            "apps hold the commit, say it plainly."
            % (_fmt_number(agent_mb), _fmt_number(total_mb),
               100.0 * agent_mb / total_mb, owner_side)
        )
    return (
        "Memory pressure requires load shedding: do not launch new model-heavy work. "
        "Priority changes alone cannot release commit; checkpoint the current atomic unit "
        "if safe, then exit enough restartable model workers to release commit before resuming."
    )


def _protected_alert_context(st):
    azure = st.get("azure") or {}
    alerts = st.get("azure_protected_alerts") or azure.get("protected_alerts") or []
    if not alerts:
        return None, None
    signature = ",".join(sorted(str(alert) for alert in alerts))
    stale_note = ""
    if "protected_telemetry_stale" in alerts:
        stale_note = " Trading-unit telemetry is stale; no last-known unit state is current."
    context = (
        "[protected-health] OBSERVATION on the remote box: %s.%s This alert alone is not "
        "compute distress and does not authorize pausing or shedding research load; it "
        "never weakens any simultaneous compute-pressure instruction. Preserve the alert "
        "for the owner/operator; never touch, restart, renice, or signal a protected unit "
        "(COMPUTE_DOCTRINE.md)."
        % ("; ".join(str(alert) for alert in alerts), stale_note)
    )
    return signature, context


def _clear_protected_dedup(seen, sid):
    prefix = sid + "|protected-health|"
    for key in list(seen):
        if key.startswith(prefix):
            del seen[key]


def _protected_alert_can_emit(status, compute_context):
    # Never let a newly observed protected alert replace a still-active, deduplicated
    # compute-distress instruction with softer language.
    return status != "distress" or bool(compute_context)


def _warn_notch(st, reasons):
    if st.get("mode") != "throttle":
        return ""
    azure = st.get("azure") or {}
    if (
        azure.get("telemetry_degraded")
        or "azure_sentinel_stale" in reasons
        or "azure_unreachable" in reasons
    ):
        return (
            " Remote-box telemetry is degraded: take parallelism down a notch until direct "
            "pressure measurements recover."
        )
    if "saturation_throttle" in reasons:
        return (
            " CPU/GPU is saturated: take your parallelism down a notch NOW "
            "(fewer workers, smaller batch) - do not wait for distress."
        )
    if _is_memory_pressure(reasons):
        return " Memory pressure is active: no new model-heavy launches."
    return " Throttle mode is active: use fewer workers and smaller batches for now."


def _warn_guidance(st, reasons):
    azure = st.get("azure") or {}
    guidance = []
    memory_guidance = _memory_pressure_guidance(reasons, st)
    if memory_guidance:
        guidance.append(memory_guidance)
    if azure.get("telemetry_degraded") or "azure_unreachable" in reasons:
        guidance.append(
            "Keep existing remote-box work low-parallelism and do not start anything heavy "
            "there until direct telemetry recovers (COMPUTE_DOCTRINE.md)."
        )
    elif not guidance:
        guidance.append(
            "Prefer lighter parallelism, BelowNormal priority, and the remote compute box "
            "for anything heavy (COMPUTE_DOCTRINE.md)."
        )
    return " ".join(guidance)


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    try:
        raw = sys.stdin.read()
        i = raw.find("{")
        if i > 0:
            raw = raw[i:]   # PS pipes prepend a BOM (locale-mangled); skip to the JSON
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        if os.environ.get("CS_DEBUG"):
            raise
        return
    try:
        gpu_deny = _local_gpu_deny(data)
        if gpu_deny:
            sys.stdout.write(json.dumps(gpu_deny))
            return
        sid = (data.get("session_id") or "nosession")[:16]
        ev = data.get("hook_event_name") or ("PreToolUse" if data.get("tool_name") else "UserPromptSubmit")
        if not os.path.exists(STATE_FILE):
            return  # governor not installed; stay silent
        age = time.time() - os.path.getmtime(STATE_FILE)
        with io.open(STATE_FILE, "r", encoding="utf-8-sig") as f:
            st = json.load(f)
        if st.get("status") == "retired":
            return  # deliberate uninstall; never nag
        seen = _load_seen()
        ctx = None

        if age > STALE_SEC:
            # A stale heartbeat has TWO causes that look identical in the file's mtime,
            # and only one of them is an outage:
            #   (a) the machine was suspended (Modern Standby freezes the governor with
            #       every other process; on wake it rewrites within ~20s). Benign - and
            #       the common case on a laptop that sleeps whenever the owner steps away.
            #   (b) the governor is genuinely down or wedged. Actionable.
            # Telling (a) to "restart the watchdog" is the healer-is-the-outage failure:
            # it restarts a healthy governor, or worse spawns a second one. So pay for the
            # real checks HERE, on the rare alarm path only. (2026-07-15: this alarm fired
            # at a session after a 60.7-min standby; the governor had already resumed 2s
            # before the read. A two-second race, reported as "the watchdog may be down".)
            alive = _governor_alive()
            if alive and _slept_recently(age):
                pass  # sleep explains the gap and the governor is running: stay silent
            elif alive:
                if _dedup(seen, sid + "|wedged", DEDUP_STALE):
                    ctx = ("[compute-instinct] The compute governor process is ALIVE but its heartbeat is %dmin old "
                           "on %s, and no recent resume-from-standby explains the gap - it may be wedged. Do NOT "
                           "blind-restart it: a second governor racing the first is worse than a late one. Read "
                           "%USERPROFILE%\\.claude\\compute\\governor.log, confirm the process is really stuck "
                           "(watch compute_state.json's ts for ~30s), and only then stop the old one before starting "
                           "a new one." % (int(age // 60), st.get("host", "this machine")))
            elif alive is False:
                if _dedup(seen, sid + "|stale", DEDUP_STALE):
                    ctx = ("[compute-instinct] The compute governor is DOWN on %s (no live process for the pid in "
                           "governor.pid; heartbeat %dmin old). The watchdog that protects the owner's responsiveness "
                           "and the protected stack is not running. Check "
                           "%USERPROFILE%\\.claude\\compute\\governor.log, restart it (wscript //B "
                           "launch_compute_governor.vbs in 12_cognitive_architecture/compute_governor/), fix what "
                           "killed it, and improve the apparatus before returning to your task."
                           % (st.get("host", "this machine"), int(age // 60)))
        else:
            status = st.get("status", "ok")
            reasons = st.get("reasons") or []
            if status == "distress":
                sig = ",".join(sorted(reasons))
                if _dedup(seen, sid + "|distress|" + sig, DEDUP_DISTRESS):
                    az = st.get("azure") or {}
                    az_line = ""
                    if az:
                        # AGE. azure.ts is the BOX's clock, which runs Etc/UTC,
                        # while this file's top-level ts is local - three hours
                        # apart today, unlabelled, sitting next to each other.
                        # An agent reading them as one timezone concludes a
                        # fresh reading is hours stale (or the reverse). Do the
                        # arithmetic once, here, instead of in every session.
                        age_txt = ""
                        try:
                            _t = time.strptime(str(az.get("ts")), "%Y-%m-%d %H:%M:%S")
                            _age = time.time() - calendar.timegm(_t)
                            if _age >= 900:
                                age_txt = (" This remote-box reading is %dm old - VERIFY LIVE"
                                           " before acting on it." % int(_age // 60))
                            elif _age >= 0:
                                age_txt = " Remote-box reading %dm old." % int(_age // 60)
                        except Exception:
                            age_txt = " Remote-box reading age unknown."
                        az_line = (" Remote box: status=%s psi_cpu60=%s load1=%s.%s%s" %
                                   (az.get("status"), az.get("psi_cpu_some_avg60"),
                                    az.get("load1"), age_txt,
                                    _load_is_io_not_cpu(az)))
                    ctx = ("[compute-instinct] COMPUTE DISTRESS on %s: %s. cpu=%s%% mem_avail=%sMB owner_active=%s "
                           "hung_windows=%s. Top consumers: %s.%s"
                           % (st.get("host"), ", ".join(reasons), st.get("cpu_pct"), st.get("mem_avail_mb"),
                              st.get("owner_active"), st.get("hung_windows"), _fmt_top(st.get("top")), az_line))
                    ctx += _memory_pressure_context(st, reasons)
                    ctx += "\n"
                    memory_guidance = _memory_pressure_guidance(reasons, st)
                    if memory_guidance:
                        ctx += memory_guidance + "\n"
                    # The contract's default is "PAUSE and shed". When the shed
                    # is measurably futile - nothing of ours holds the commit -
                    # that instruction CONTRADICTS the guidance printed directly
                    # above it ("do NOT abandon your task over this"), and an
                    # agent follows whichever it read last. Keep the doctrine's
                    # priorities, drop the impossible order.
                    if _agent_shed_is_futile(st or {}):
                        ctx += (
                            "The contract (COMPUTE_DOCTRINE.md): the owner's responsiveness and the protected stack "
                            "still outrank every research job, but there is no agent-side load to shed here - do "
                            "NOT pause or abandon your task for this. Keep your local footprint short-lived, put "
                            "anything heavy on the research boxes, and name the owner-side holders in your report "
                            "so he can act if he wants the commit back. If the cause is systemic, improve the "
                            "governor/sentinel/doctrine and write the lesson to the brain "
                            "(python ~/.claude/dreaming/brain.py remember ...)."
                        )
                    else:
                        ctx += (
                            "The contract (COMPUTE_DOCTRINE.md): PAUSE your current work now. Shed or fix the load - "
                            "your own heavy processes first (demote/checkpoint/stop them), then the listed offenders. "
                            "The owner's responsiveness and the protected stack outrank every research job. If the cause "
                            "is systemic, improve the governor/sentinel/doctrine and write the lesson to the brain "
                            "(python ~/.claude/dreaming/brain.py remember ...). Only then return to your task."
                        )
            elif status == "warn":
                sig = ",".join(sorted(reasons))
                if _dedup(seen, sid + "|warn|" + sig, DEDUP_WARN):
                    notch = _warn_notch(st, reasons)
                    ctx = ("[compute-instinct] compute pressure on %s (%s, mode=%s): cpu=%s%% mem=%sMB."
                           % (st.get("host"), ", ".join(reasons), st.get("mode"), st.get("cpu_pct"),
                              st.get("mem_avail_mb")))
                    ctx += _memory_pressure_context(st, reasons)
                    ctx += "%s %s" % (notch, _warn_guidance(st, reasons))

            protected_sig, protected_ctx = _protected_alert_context(st)
            if not protected_ctx:
                _clear_protected_dedup(seen, sid)
            elif _protected_alert_can_emit(status, ctx):
                if _dedup(
                    seen, sid + "|protected-health|" + protected_sig, DEDUP_TRADING
                ):
                    ctx = (ctx + "\n" + protected_ctx) if ctx else protected_ctx

        _save_seen(seen)
        if not ctx:
            return
        if ev == "PreToolUse":
            out = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                          "permissionDecision": "allow",
                                          "additionalContext": ctx}}
        else:
            out = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                          "additionalContext": ctx}}
        sys.stdout.write(json.dumps(out))
    except Exception:
        if os.environ.get("CS_DEBUG"):
            raise
        return


if __name__ == "__main__":
    main()
