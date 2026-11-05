#!/usr/bin/env python3
"""Codex bridge to the proven lexical/graph reflex engine.

The prospective error graph remains shadow until its independent gate clears.
This adapter instead reuses instinct_prime, which already runs live for Claude,
and exposes its bounded additionalContext on Codex UserPromptSubmit and
PreToolUse.  Generic deny/allow decisions from the underlying Claude hook remain
stripped.  One narrow, locally checkable exception prevents a recurring failure:
direct ``python script.py --help`` discovery is denied until the caller supplies
an approval bound to the SHA-256 of the exact inspected entry-point bytes.

Fail-open: malformed input, disabled brain, import errors, or malformed child
output produce no stdout and exit zero.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable, Mapping, Optional


HERE = os.path.dirname(os.path.abspath(__file__))
SUPPORTED = frozenset(("UserPromptSubmit", "PreToolUse"))
_SHELL_KEYS = ("command", "cmd", "input", "code")
_PYTHON = re.compile(r"^(?:python(?:\d+(?:\.\d+)*)?|pypy\d*|py)(?:\.exe)?$", re.I)
_UV = re.compile(r"^uv(?:\.exe)?$", re.I)
_HELP_FLAGS = frozenset(("--help", "-h"))
_HASH = re.compile(r"^[0-9a-f]{64}$", re.I)
_BYPASS = "HELP_INSPECTED"
_LITERAL_HELP_CONCAT = re.compile(
    r"\(\s*(['\"])([^'\"\r\n]*)\1\s*\+\s*(['\"])([^'\"\r\n]*)\3\s*\)"
)


def _strings(value: Any) -> Iterable[str]:
    """Yield bounded command-like strings without serializing arbitrary payloads."""
    if isinstance(value, str):
        yield value[:256_000]
    elif isinstance(value, Mapping):
        for key in _SHELL_KEYS:
            child = value.get(key)
            if isinstance(child, str):
                yield child[:256_000]


def _command_candidates(payload: Mapping[str, Any]) -> list[str]:
    """Return only structured shell input at the real execution boundary.

    ``functions.exec`` source is deliberately not regex-parsed.  Codex invokes
    this hook again for each nested ``shell_command`` with structured input; that
    boundary covers JS literals, variables, and templates without guessing JS.
    """
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, Mapping):
        return []
    return list(_strings(tool_input))


def _is_python(token: str) -> bool:
    return bool(_PYTHON.fullmatch(os.path.basename(token)))


def _shell_segments(command: str) -> list[list[str]]:
    """Lex the small shell subset needed to identify command-position Python.

    Quotes concatenate with adjacent text (PowerShell ``--hel\"p\"``), comments
    are discarded, and command separators create independent segments.  This is
    intentionally not a general shell parser: dynamic/unknown forms are left to
    the exact nested execution boundary and the default-deny approval workflow.
    """
    segments: list[list[str]] = []
    words: list[str] = []
    buf: list[str] = []
    quote = ""
    i = 0

    def end_word() -> None:
        if buf:
            words.append("".join(buf))
            buf.clear()

    def end_segment() -> None:
        end_word()
        if words:
            segments.append(words.copy())
            words.clear()

    while i < len(command):
        ch = command[i]
        if quote:
            if ch == quote:
                if quote == "'" and i + 1 < len(command) and command[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                quote = ""
            elif ch == "`" and quote == '"' and i + 1 < len(command):
                i += 1
                buf.append(command[i])
            else:
                buf.append(ch)
            i += 1
            continue
        if command.startswith("@'", i) and command[i + 2:i + 3] in ("\r", "\n"):
            # A single-quoted PowerShell here-string is literal data. Ordinary
            # apostrophes inside it do not terminate the string, so lexing it
            # as a quote would turn examples into false executable commands.
            body_start = i + 2
            if command.startswith("\r\n", body_start):
                body_start += 2
            else:
                body_start += 1
            closing = re.search(r"(?m)^[ \t]*'@", command[body_start:])
            if not closing:
                end_segment()
                break
            buf.append("<literal-here-string>")
            i = body_start + closing.end()
            continue
        if ch in "'\"":
            quote = ch
            i += 1
            continue
        if command.startswith("<#", i):
            end = command.find("#>", i + 2)
            if end < 0:
                end_segment()
                break
            i = end + 2
            continue
        if ch == "#" and not buf:
            while i < len(command) and command[i] not in "\r\n":
                i += 1
            end_segment()
            continue
        if ch in "\r\n;|":
            end_segment()
            if ch == "|" and i + 1 < len(command) and command[i + 1] == "|":
                i += 1
            i += 1
            continue
        if ch == "&":
            if not words and not buf:
                words.append("&")
            else:
                end_segment()
            if i + 1 < len(command) and command[i + 1] == "&":
                i += 1
            i += 1
            continue
        if ch.isspace():
            end_word()
            i += 1
            continue
        if ch == "`" and i + 1 < len(command):
            i += 1
            buf.append(command[i])
            i += 1
            continue
        buf.append(ch)
        i += 1
    end_segment()
    return segments


def _assignment(words: list[str]) -> Optional[tuple[str, str]]:
    """Parse a simple scalar assignment segment used by ordinary PowerShell."""
    if len(words) == 1 and "=" in words[0]:
        name, value = words[0].split("=", 1)
    elif len(words) == 3 and words[1] == "=":
        name, value = words[0], words[2]
    else:
        return None
    if not re.fullmatch(r"(?:\$env:)?\$?[A-Za-z_][A-Za-z0-9_]*", name, re.I):
        return None
    return name.lower(), value


def _resolve_word(word: str, variables: Mapping[str, str]) -> str:
    braced = re.fullmatch(r"\$\{((?:env:)?[A-Za-z_][A-Za-z0-9_]*)\}", word, re.I)
    if braced:
        return variables.get(braced.group(1).lower(), word)
    if re.fullmatch(r"\$(?:env:)?[A-Za-z_][A-Za-z0-9_]*", word, re.I):
        return variables.get(word[1:].lower(), word)
    # PowerShell folds this ordinary constant expression before invocation.
    compact = word.strip("()")
    if "+" in compact and compact.replace("+", "").lower() in _HELP_FLAGS:
        return compact.replace("+", "")
    return word


def _help_targets(command: str) -> list[tuple[str, Optional[str]]]:
    """Return ``(script, approval_hash)`` for local script help invocations."""
    targets: list[tuple[str, Optional[str]]] = []
    variables: dict[str, str] = {}
    pending_approval: Optional[str] = None
    command = _LITERAL_HELP_CONCAT.sub(
        lambda m: (m.group(2) + m.group(4))
        if (m.group(2) + m.group(4)).lower() in _HELP_FLAGS else m.group(0),
        command,
    )
    for original in _shell_segments(command):
        assignment = _assignment(original)
        if assignment:
            name, value = assignment
            if name == "$env:help_inspected" and _HASH.fullmatch(value):
                pending_approval = value.lower()
            else:
                pending_approval = None  # any intervening statement consumes an approval
            if name.startswith("$"):
                variables[name[1:]] = value
            else:
                variables[name] = value
            continue

        approval = pending_approval
        pending_approval = None  # a PowerShell approval binds to the immediate command only
        words = [_resolve_word(word, variables) for word in original]
        while words and re.fullmatch(r"HELP_INSPECTED=[0-9a-f]{64}", words[0], re.I):
            approval = words.pop(0).split("=", 1)[1].lower()
        if words and words[0] == "&":
            words.pop(0)
        if words and words[0] == "{":
            words.pop(0)
            if words and words[-1] == "}":
                words.pop()
        if not words:
            continue

        # Constant child-shell/evaluator forms are recursively visible here.
        exe = os.path.basename(words[0]).lower()
        if exe in ("invoke-expression", "iex") and len(words) >= 2:
            nested_text = " ".join(words[1:]).strip()
            while nested_text.startswith("(") and nested_text.endswith(")"):
                nested_text = nested_text[1:-1].strip()
            nested = _help_targets(nested_text)
            targets.extend((path, approval or inner) for path, inner in nested)
            continue
        if exe in ("cmd", "cmd.exe") and len(words) > 2 and words[1].lower() in ("/c", "/k"):
            nested = _help_targets(" ".join(words[2:]))
            targets.extend((path, approval or inner) for path, inner in nested)
            continue
        elif exe in ("powershell", "powershell.exe", "pwsh", "pwsh.exe"):
            try:
                pos = next(i for i, word in enumerate(words[1:], 1)
                           if word.lower() in ("-command", "-c"))
            except StopIteration:
                pos = -1
            if pos >= 0 and pos + 1 < len(words):
                nested = _help_targets(" ".join(words[pos + 1:]))
                targets.extend((path, approval or inner) for path, inner in nested)
            continue

        # Transparent wrappers do not change the following Python argv.
        while words and os.path.basename(words[0]).lower() in ("command", "env", "sudo"):
            wrapper = os.path.basename(words.pop(0)).lower()
            value_options = {
                "env": frozenset(("-u", "--unset", "-C", "--chdir", "-S", "--split-string")),
                "sudo": frozenset(("-u", "--user", "-g", "--group", "-h", "--host",
                                   "-p", "--prompt", "-C", "--close-from", "-r", "--role",
                                   "-t", "--type")),
                "command": frozenset(),
            }[wrapper]
            while words:
                if words[0] == "--":
                    words.pop(0)
                    break
                if words[0] in value_options:
                    if len(words) < 2:
                        words.clear()
                        break
                    del words[:2]
                    continue
                if words[0].startswith("-"):
                    words.pop(0)
                    continue
                if wrapper == "env" and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
                    words.pop(0)
                    continue
                break
        if not words:
            continue

        j = 1
        if _is_python(words[0]):
            pass
        elif _UV.fullmatch(os.path.basename(words[0])) and len(words) > 1 and words[1].lower() == "run":
            j = 2
            uv_value_options = frozenset((
                "--python", "-p", "--directory", "--project", "--env-file", "--with",
                "--with-editable", "--index", "--default-index", "--config-file",
                "--python-platform",
            ))
            while j < len(words) and words[j].startswith("-"):
                if words[j] in uv_value_options and j + 1 < len(words):
                    j += 2
                else:
                    j += 1
            if j < len(words) and _is_python(words[j]):
                j += 1
        else:
            continue

        script: Optional[str] = None
        while j < len(words):
            current = words[j]
            lower = current.lower()
            if lower in ("-c", "-m"):
                break
            if lower in ("-x", "-w", "--check-hash-based-pycs") and j + 1 < len(words):
                j += 2
                continue
            if lower.endswith(".py"):
                script = current
                j += 1
                break
            if current.startswith("-"):
                j += 1
                continue
            break
        if script and any(word.lower() in _HELP_FLAGS for word in words[j:]):
            targets.append((script, approval))
    return targets


def _resolve_script(raw: str, cwd: str) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not path.is_absolute():
        path = Path(cwd or os.getcwd()) / path
    return path.resolve(strict=False)


def _sha256(path: Path) -> Optional[str]:
    """Hash exact local bytes immediately before authorization."""
    try:
        if not path.is_file() or path.stat().st_size > 16_000_000:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(256 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return None


def _help_guard(payload: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Deny local script help until approval matches the exact current bytes."""
    if str(payload.get("hook_event_name") or "") != "PreToolUse":
        return None
    cwd = str(payload.get("cwd") or os.getcwd())
    for command in _command_candidates(payload):
        for raw, approval in _help_targets(command):
            path = _resolve_script(raw, cwd)
            digest = _sha256(path)
            if digest and approval == digest:
                continue
            reason = (
                "[help-discovery-guard] Stopped before execution: the local Python entry point "
                f"{path} has not been approved at its exact current bytes, so --help may fall through "
                "and run normal work. Inspect imports, __main__, and argv control flow. If safe, rerun "
                f"calculate the entry point's SHA-256 after inspection and use an exact command-bound "
                f"approval: PowerShell `$env:{_BYPASS}='<sha256>'; python ... --help` or Bash "
                f"`{_BYPASS}=<sha256> python ... --help`. "
                "A comment or free-form marker does not bypass the guard."
            )
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason[:1400],
            }}
    return None


# The trailing space is load-bearing: without it "[ack-gate]forged..." passes the check, so the
# marker tests only that a string starts with those characters, not that one of our gates wrote it.
_HEED_MARKERS = ("[heed-gate] ", "[ack-gate] ")


def _heed_deny(hso: Mapping[str, Any]) -> Optional[str]:
    """The reason of a HEED-lane deny, or None for every other decision.

    Generic deny/allow from the Claude hook stays discarded: a Codex session must not inherit
    decisions made for a different runtime's tool surface. The two heed gates are the deliberate
    exception, and they are recognizable, bounded, and recoverable by construction - each denies
    at most once per (session, reflex), caps per session, claims its stop BEFORE issuing it so a
    retry can never be blocked twice, and fails open on any internal error. Without this, a Codex
    agent sees the scar text and nothing stops it acting - the exact read-past failure the gates
    exist for, which is why the marker is matched rather than the whole decision passed.
    """
    if str(hso.get("permissionDecision") or "") != "deny":
        return None
    reason = hso.get("permissionDecisionReason")
    if not isinstance(reason, str) or not reason.startswith(_HEED_MARKERS):
        return None
    return reason[:1400]


def _advisory(source: Any, event: str) -> Optional[dict[str, Any]]:
    """Keep model-visible context and heed-lane denies; discard every other decision."""
    if not isinstance(source, Mapping):
        return None
    hso = source.get("hookSpecificOutput")
    if not isinstance(hso, Mapping):
        return None
    context = hso.get("additionalContext")
    heed = _heed_deny(hso) if event == "PreToolUse" else None
    if heed is None and (not isinstance(context, str) or not context.strip()):
        return None
    out: dict[str, Any] = {"hookEventName": event}
    if isinstance(context, str) and context.strip():
        out["additionalContext"] = context[:6000]
    if heed is not None:
        out["permissionDecision"] = "deny"
        out["permissionDecisionReason"] = heed
    return {"hookSpecificOutput": out}


def process(payload: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    event = str(payload.get("hook_event_name") or "")
    if event not in SUPPORTED:
        return None
    session = str(payload.get("session_id") or "")
    try:
        guarded = _help_guard(payload)
        if guarded:
            return guarded
        sys.path.insert(0, HERE)
        import brain_switch
        if brain_switch.is_offline(session):
            return None
        import instinct_prime
    except Exception:
        return None

    # A silent reflex engine is the COMMON case, not a failure: most prompts trip no instinct.
    # Returning here meant the association lane below never ran, so a Codex agent naming a topic
    # got no recall at all unless a reflex happened to fire first. The two lanes are independent.
    child = _capture(instinct_prime, payload)
    out = _advisory(child, event) if child is not None else None
    # The association/topic lane is a SEPARATE hook for Claude sessions, so bridging only the
    # reflex engine left Codex agents without it - measured: a topic-naming Codex prompt drew
    # no context at all. They work the same corpus and hit the same scars, so they get the same
    # recall, including the owner-vocabulary 3x lane. Fail-open and additive: any error, or no
    # association to offer, leaves the reflex result exactly as it was.
    try:
        import spread_recall
        assoc = _capture(spread_recall, payload)
        extra = ""
        if isinstance(assoc, Mapping):
            hso = assoc.get("hookSpecificOutput")
            if isinstance(hso, Mapping):
                candidate = hso.get("additionalContext")
                if isinstance(candidate, str):
                    extra = candidate.strip()
        if extra:
            if out is None:
                out = {"hookSpecificOutput": {"hookEventName": event,
                                              "additionalContext": extra[:6000]}}
            else:
                merged = out["hookSpecificOutput"].get("additionalContext") or ""
                merged = (merged + "\n" + extra).strip() if merged else extra
                out["hookSpecificOutput"]["additionalContext"] = merged[:6000]
    except Exception:
        pass
    return out


def _capture(module: Any, payload: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Run a hook module's main() on this payload and parse its stdout. None on any failure."""
    old_in, old_out = sys.stdin, sys.stdout
    captured = io.StringIO()
    try:
        sys.stdin = io.StringIO(json.dumps(dict(payload), ensure_ascii=False))
        sys.stdout = captured
        module.main()
    except Exception:
        return None
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    try:
        return json.loads(captured.getvalue())
    except Exception:
        return None


def main() -> int:
    started = time.perf_counter()
    event = ""
    mode = "advisory"
    status = "ok"
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        raw = sys.stdin.read(512 * 1024 + 1)
        if len(raw.encode("utf-8", "replace")) > 512 * 1024:
            status = "oversize"
            return 0
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            status = "error"
            return 0
        event = str(payload.get("hook_event_name") or "")
        output = process(payload)
        if event == "PreToolUse":
            try:
                import owner_context_delivery
                import owner_prompts_hook
            except ImportError:
                pass
            else:
                if owner_context_delivery.emit(owner_prompts_hook, payload, "codex", sys.stdout,
                                               base_output=output, budget=9000):
                    output = None  # already emitted together, under one measured budget
        if output:
            try:
                if output.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
                    # MetricsStore's schema accepts block, not a private guard_deny
                    # label.  Reuse the categorical block bucket so successful
                    # guard decisions remain visible in liveness telemetry.
                    mode = "block"
            except Exception:
                pass
            sys.stdout.write(json.dumps(output, ensure_ascii=True))
    except Exception:
        status = "error"
    finally:
        try:
            import codex_hook_telemetry
            codex_hook_telemetry.record(event, mode, status, started)
        except Exception:
            pass
    return 0


def _selftest() -> int:
    sample = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "must not escape",
        "additionalContext": "visible warning",
        "updatedInput": {"command": "must not escape"},
    }}
    out = _advisory(sample, "PreToolUse")
    ok = bool(out and out["hookSpecificOutput"] == {
        "hookEventName": "PreToolUse",
        "additionalContext": "visible warning",
    })
    ok = ok and _advisory({}, "PreToolUse") is None
    # a HEED-lane deny is the one decision that must survive the strip, and only on an act event
    heed = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "[ack-gate] answer the scar before this runs",
        "additionalContext": "visible warning",
    }}
    passed = _advisory(heed, "PreToolUse")
    ok = ok and bool(passed and passed["hookSpecificOutput"].get("permissionDecision") == "deny"
                     and passed["hookSpecificOutput"].get("additionalContext") == "visible warning")
    ok = ok and _advisory(heed, "UserPromptSubmit")["hookSpecificOutput"].get(
        "permissionDecision") is None
    spoof = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": "generic block that must not escape",
        "additionalContext": "visible warning"}}
    ok = ok and _advisory(spoof, "PreToolUse")["hookSpecificOutput"].get(
        "permissionDecision") is None
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        unsafe = root / "unsafe.py"
        unsafe.write_text("print('normal work ran')\n", encoding="utf-8")
        def sample(command: str) -> dict[str, Any]:
            return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": td,
                    "tool_input": {"command": command}}
        denied = _help_guard(sample(f'python "{unsafe}" --help'))
        ok = ok and bool(denied and denied["hookSpecificOutput"].get("permissionDecision") == "deny")
        digest = _sha256(unsafe)
        ok = ok and bool(digest)
        ok = ok and _help_guard(sample(
            f"$env:{_BYPASS}='{digest}'; python \"{unsafe}\" --help"
        )) is None
        ok = ok and _help_guard(sample(f'Get-Content "{unsafe}"')) is None
        ok = ok and _help_guard(sample('python -m pytest --help')) is None
        ok = ok and bool(_help_guard(sample(
            f'python "{unsafe}" --help # {_BYPASS}={digest}'
        )))
        nested = ('const c = `python "' + str(unsafe) + '" --help`; '
                  'await tools.shell_command({command: c});')
        nested_payload = {"hook_event_name": "PreToolUse", "tool_name": "exec", "cwd": td,
                          "tool_input": nested}
        ok = ok and _help_guard(nested_payload) is None
    print("codex_reflex_hook selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    raise SystemExit(main())
