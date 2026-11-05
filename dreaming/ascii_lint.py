#!/usr/bin/env python3
"""ascii_lint.py - guard the wake path against the non-ASCII scar.

A single non-ASCII em-dash in a .ps1 string literal once broke the SessionStart hook and stopped every
session waking into the brain (self_model_failure_modes_2026_07_07). The root cause is structural:
PowerShell 5.1 reads a UTF-8-no-BOM file as cp1252, so any non-ASCII byte is mis-decoded - in a comment
it's harmless, in a string literal it's mojibake, and in the wrong spot it's a parse break. WScript
(.vbs launchers) has the same fragility. Pure ASCII is immune (it reads identically as UTF-8 or cp1252),
so the robust rule is simple: the launcher/hook files that PowerShell and WScript execute must be pure
ASCII. This enforces it - fail-closed, so a future edit that sneaks in a smart-quote or em-dash is caught
here, not by the wake going dark.

Scope: every .ps1/.vbs under ~/.claude RECURSIVELY (2026-07-09: the flat two-dir glob missed 23
launcher files in subdirs, 12 carrying non-ASCII - none hook-wired, but the next launcher dropped in
a subdir would have rotted unscanned), minus known one-off archives (crash_recovery_*, __pycache__).
Files referenced by settings.json hooks are always included wherever they live. Python .py hooks are
NOT linted - CPython reads them as UTF-8 regardless, so non-ASCII in a .py string is safe.
Read-only. Exit 0 if clean, 1 if any non-ASCII is found, and 2 if the scan is
incomplete or invoked incorrectly. Usage: python ascii_lint.py
"""
import os, io, sys, json, re, hashlib

ROOT = os.path.expanduser(r"~/.claude")
EXTS = (".ps1", ".vbs")
SKIP_DIRS = re.compile(r"crash_recovery|__pycache__|node_modules|\.git", re.I)
MAX_CODES_PER_LINE = 12
LAUNCHER_SUFFIX_RE = re.compile(r"\.(?:ps1|vbs)(?![A-Za-z0-9])", re.I)
UNSAFE_PATH_RE = re.compile(r'''[\x00-\x1f;^&|<>`$*?"']''')
CERTIFIED_LEGACY_COMMAND_SHA256 = {
    # The repeated Python-only hook.py fallback in the live settings, audited
    # 2026-07-14. Any byte change becomes an incomplete scan instead of being
    # interpreted with shell heuristics.
    "74d58f25a41e72a540e1035f935eb8eb03369bf4a41ac9b52f74ced5c871c68f",
    # The ps1_guard.py PostToolUse hook, audited 2026-07-16: it invokes powershell
    # with -Command (an inline string), never a .ps1/.vbs file, so it contributes
    # no launcher to the scan set. Wired 2026-07-15 16:03; until certified here its
    # interpreter-plus-quoted-arg form read as unbalanced quotes and took the whole
    # scan to incomplete.
    "d162b623c843d102da5d0cf0f690e8dabc112289169b6bd4331d161b0c9d851f",
}


def _ascii_safe(value):
    """Keep diagnostics printable even when the console uses a legacy code page."""
    return ascii(str(value))[1:-1]


def _absolute_source(candidate):
    expanded = os.path.expandvars(os.path.expanduser(candidate))
    drive, _ = os.path.splitdrive(expanded)
    if (not os.path.isabs(expanded) or
        (os.name == "nt" and not drive)):
        raise ValueError("launcher reference is not absolute")
    if UNSAFE_PATH_RE.search(expanded):
        raise ValueError("launcher reference contains shell syntax")
    return os.path.normpath(expanded)


def _exec_source(value):
    """Interpret one already-tokenized command/argument from hook exec form."""
    if not isinstance(value, str):
        raise TypeError("hook token must be text")
    token = value.strip()
    suffixes = list(LAUNCHER_SUFFIX_RE.finditer(token))
    if not suffixes:
        return None
    if token[:1] in ("\"", "'") or token[-1:] in ("\"", "'"):
        if len(token) < 2 or token[0] != token[-1] or token[0] not in ("\"", "'"):
            raise ValueError("launcher token has unbalanced quotes")
        token = token[1:-1]
    if not token.lower().endswith(EXTS):
        raise ValueError("launcher suffix is embedded in a non-path token")
    return _absolute_source(token)


def _exec_sources(command, args):
    """Inspect modern direct-exec hooks with narrow interpreter contracts."""
    if not isinstance(command, str):
        raise TypeError("hook command must be text")
    if (not isinstance(args, list) or
        not all(isinstance(arg, str) for arg in args)):
        raise TypeError("hook args must be a text list")
    executable = command.strip().strip("\"'")
    executable = os.path.basename(executable).lower()
    if executable in ("cmd", "cmd.exe"):
        raise ValueError("cmd hook form is not statically inspectable")
    if executable in ("powershell", "powershell.exe", "pwsh", "pwsh.exe"):
        lowered = [arg.lower() for arg in args]
        if lowered.count("-file") != 1:
            raise ValueError("PowerShell hook must use one literal -File")
        file_index = lowered.index("-file")
        prefix = lowered[:file_index]
        index = 0
        while index < len(prefix):
            if prefix[index] in ("-noprofile", "-noninteractive", "-nologo"):
                index += 1
                continue
            if (prefix[index] == "-executionpolicy" and index + 1 < len(prefix) and
                prefix[index + 1] in ("bypass", "remotesigned", "allsigned",
                                      "restricted", "unrestricted", "default")):
                index += 2
                continue
            raise ValueError("PowerShell pre-File options are not certified")
        if file_index + 1 >= len(args):
            raise ValueError("PowerShell -File source is missing")
        source = _exec_source(args[file_index + 1])
        if source is None or not source.lower().endswith(".ps1"):
            raise ValueError("PowerShell -File source is not literal")
        sources = [source]
        trailing = args[file_index + 2:]
    elif executable in ("wscript", "wscript.exe", "cscript", "cscript.exe"):
        sources = []
        trailing = args
        for token in args:
            source = _exec_source(token)
            if source is not None:
                sources.append(source)
        if not any(source.lower().endswith(".vbs") for source in sources):
            raise ValueError("script-host source is not literal")
        trailing = []
    else:
        sources = []
        trailing = [command] + args
    for token in trailing:
        source = _exec_source(token)
        if source is not None:
            sources.append(source)
    return sorted(set(sources))


def _python_file_form(command):
    """`python [-flags] <one absolute .py path>` -> that path, else None. The one-process hook dispatcher is
    wired in exactly this string form (`python -s C:/.../hook_dispatch.py`, 2026-09-15); a .py contributes no
    launcher to the scan, but the dispatcher's own list does (see _dispatcher_sources)."""
    parts = command.strip().split()
    if not parts or not os.path.basename(parts[0].strip("\"'")).lower().startswith("python"):
        return None
    rest = [p for p in parts[1:] if not p.startswith("-")]
    if len(rest) != 1 or not rest[0].strip("\"'").lower().endswith(".py"):
        return None
    if any(p in ("-c", "-m") for p in parts[1:]):
        return None
    return _absolute_source(rest[0].strip("\"'"))


def _python_inline_form(command):
    """`python [-flags] -c "<code>"`: an inline CPython program that names no .ps1/.vbs file. It contributes no
    launcher to the scan (CPython reads its own -c text as Unicode; the cp1252 scar cannot reach it). The one
    live instance is the error-graph observer the dispatcher list carries for every event."""
    parts = command.strip().split()
    if not parts or not os.path.basename(parts[0].strip("\"'")).lower().startswith("python"):
        return False
    if "-c" not in parts[1:]:
        return False
    return not LAUNCHER_SUFFIX_RE.search(command)


def _dispatcher_sources(dispatcher_path):
    """The launchers among the hooks hook_dispatch.py runs (hook_dispatch_list.json beside it). Every entry is
    inspected with the same rules as a settings.json entry; an uninspectable entry raises, so the scan reports
    itself incomplete rather than silently skipping the load-bearing set."""
    list_path = os.path.join(os.path.dirname(dispatcher_path), "hook_dispatch_list.json")
    with io.open(list_path, encoding="utf-8-sig") as handle:
        blocks = json.load(handle)
    found = []
    for groups in (blocks or {}).values():
        for grp in groups or []:
            for h in grp.get("hooks", []):
                command = h.get("command", "")
                if "args" in h:
                    found.extend(_exec_sources(command, h.get("args")))
                else:
                    found.extend(_legacy_sources(command))
    return found


def _legacy_sources(command):
    """Accept only a certified command, one literal launcher path, or a python-file invocation."""
    if not isinstance(command, str):
        raise TypeError("hook command must be text")
    digest = hashlib.sha256(command.encode("utf-8")).hexdigest()
    if digest in CERTIFIED_LEGACY_COMMAND_SHA256:
        return []
    python_file = _python_file_form(command)
    if python_file is not None:
        if os.path.basename(python_file).lower() == "hook_dispatch.py":
            return _dispatcher_sources(python_file)
        return []
    if _python_inline_form(command):
        return []
    token = command.strip()
    quoted = False
    if token[:1] in ("\"", "'") or token[-1:] in ("\"", "'"):
        if len(token) < 2 or token[0] != token[-1] or token[0] not in ("\"", "'"):
            raise ValueError("legacy launcher path has unbalanced quotes")
        token = token[1:-1]
        quoted = True
    if not quoted and any(character.isspace() for character in token):
        raise ValueError("legacy launcher path with whitespace must be quoted")
    source = _exec_source(token)
    if source is None:
        raise ValueError("legacy command has not been certified")
    return [source]


def main(argv=None):
    argv = [] if argv is None else list(argv)
    if argv:
        print("ascii_lint: ERROR - unknown argument(s); usage: python ascii_lint.py")
        return 2
    operational_errors = []
    files = []

    def walk_error(error):
        operational_errors.append(("launcher tree", type(error).__name__))

    for dirpath, dirnames, filenames in os.walk(ROOT, onerror=walk_error):
        dirnames[:] = [d for d in dirnames if not SKIP_DIRS.search(d)]
        for fn in filenames:
            if fn.lower().endswith(EXTS):
                files.append(os.path.join(dirpath, fn))
    # plus anything settings.json hooks execute, wherever it lives (the load-bearing set)
    settings_path = os.path.join(ROOT, "settings.json")
    if os.path.exists(settings_path):
        try:
            with io.open(settings_path, encoding="utf-8-sig") as handle:
                st = json.load(handle)
            for ev in (st.get("hooks") or {}).values():
                for grp in ev:
                    for h in grp.get("hooks", []):
                        command = h.get("command", "")
                        args_present = "args" in h
                        args = h.get("args")
                        try:
                            if not args_present:
                                files.extend(_legacy_sources(command))
                            else:
                                files.extend(_exec_sources(command, args))
                        except (TypeError, ValueError) as error:
                            operational_errors.append(
                                ("hook configuration", type(error).__name__)
                            )
        except Exception as error:
            operational_errors.append(("settings.json", type(error).__name__))
    else:
        operational_errors.append(("settings.json", "FileNotFoundError"))
    files = sorted(set(files))
    bad = []
    for source_number, f in enumerate(files, 1):
        extension = ".ps1" if f.lower().endswith(".ps1") else ".vbs"
        label = "launcher-%03d%s" % (source_number, extension)
        try:
            with io.open(f, "rb") as handle:
                raw = handle.read()
        except Exception as error:
            operational_errors.append((label, type(error).__name__))
            continue
        # Windows PowerShell 5.1 accepts a UTF-8 BOM and uses it to select the
        # right decoder. Windows Script Host instead rejects that same BOM as
        # an invalid first VBScript character, so only PowerShell may strip it.
        encoding = "utf-8-sig" if f.lower().endswith(".ps1") else "utf-8"
        b = raw.decode(encoding, errors="replace")
        hits = []
        for i, line in enumerate(b.split("\n"), 1):
            na = [(j, c) for j, c in enumerate(line) if ord(c) > 127]
            if na:
                codes = [f"U+{ord(c):04X}" for _, c in na[:MAX_CODES_PER_LINE]]
                omitted = len(na) - len(codes)
                if omitted:
                    codes.append("omitted=%d" % omitted)
                hits.append((i, codes))
        # Report BOM state for context. It is accepted for .ps1 and remains a
        # visible U+FEFF hit for .vbs, matching the real interpreter behavior.
        bom = raw[:3] == b"\xef\xbb\xbf"
        if hits:
            bad.append((label, hits, bom))

    print(f"ascii_lint: checked {len(files)} .ps1/.vbs launcher/hook files")
    if operational_errors:
        print(
            "  ERROR - could not inspect %d required input(s); result is incomplete:"
            % len(operational_errors)
        )
        for name, error_type in operational_errors[:6]:
            print("      %s (%s)" % (_ascii_safe(name), _ascii_safe(error_type)))
        if len(operational_errors) > 6:
            print("      omitted=%d" % (len(operational_errors) - 6))
        if bad:
            print("      non-ASCII findings also present=%d" % len(bad))
        return 2
    if not bad:
        print("  CLEAN - all pure ASCII. The wake path is immune to the cp1252-misread scar.")
        return 0
    print(f"  FAIL - {len(bad)} file(s) carry non-ASCII (a re-encode away from breaking their interpreter):")
    for label, hits, bom in bad:
        print(f"  {label}  (BOM: {'yes' if bom else 'NO - cp1252 misread risk'})")
        for ln, codes in hits[:6]:
            print(f"      line {ln}: {', '.join(codes)}")
        print(f"      fix: replace with ASCII (em-dash->'-', smart-quotes->\"'\", ellipsis->'...'), "
              f"then parse-validate: [Parser]::ParseFile")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
