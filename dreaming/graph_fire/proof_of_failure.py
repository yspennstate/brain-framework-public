#!/usr/bin/env python3
"""proof_of_failure.py - deny a Bash command ONLY when the hook mechanically PROVES it must fail.

The heeding frontier (audit 2026-07-12): detection is near-total (59/60 of today's felt-errors had a
reflex fire beforehand) but a PreToolUse *warning* cannot stop the command it fires on - the model sees
it only alongside the tool result, after the act. The ONLY thing that stops a read-past act is a DENY,
and a deny is only safe when failure is CERTAIN. These two predicates carry a PROOF, not a heuristic:

  A) BACKSLASH-ENOENT: a *single simple command* whose unquoted drive-letter backslash path is in
     an interpreter argv position, where the forward-slash form EXISTS and the Bash-mangled form does
     NOT. Adjacent backslashes are excluded because their meaning depends on the executor's parse
     depth. Compound/control-flow commands are excluded: a doomed subcommand followed by `|| true`
     is not a doomed whole command.
  B) HEREDOC-COMPILE-FAIL: a standalone `python - <<'MARK' ... MARK` command whose raw body fails
     compile().  The delimiter must be quoted (no Bash expansion), and the heredoc must be the whole
     command; otherwise the bytes compiled by Python or the final shell status can differ.

Blocking a provably-doomed command has near-zero cost: the deny reason hands back the corrected form,
so the worst case is one retry. Precision is bounded BY CONSTRUCTION (the proof), not by tuning.

Fail-OPEN by design: any parse ambiguity, filesystem error, quoting edge, ssh-remote wrapper, or
exception -> (False, ...) = ALLOW. A gate that is unsure never blocks. stdlib only.
"""
import os, re

BS = chr(92)  # backslash, kept out of string literals to avoid escaping hazards
INTERP = r'(?:python3?(?:\.\d+)?|py|node)'
# Only interpreter path arguments.  `cd C:\Windows` may succeed in MSYS after
# mangling to drive-relative `C:Windows`, a meaning Windows os.path cannot model.
_PATH_LEAD = r'(?:python3?(?:\.\d+)?|py|node)'
# path-lead followed by an UNQUOTED drive-letter backslash path in a whole, simple command,
# optionally wrapped by timeout/nice which do not change the argv path.
_PATA = re.compile(r'^\s*(?:timeout\s+\d+\s+)?(?:nice\s+-n\s*\d+\s+)?' + _PATH_LEAD +
                   r'\s+([A-Za-z]:' + BS + BS + r'[^\s\'"|;&<>]+)')
# Header of a standalone quoted python heredoc. The body is parsed below by
# selecting the FIRST exact delimiter line; a regex anchored at the end can
# backtrack across later heredocs and falsely compile shell text as Python.
_HD_HEAD = re.compile(
    r'^[ \t]*python3?(?:\.\d+)?(?:[ \t]+-(?:u|E|s))*[ \t]+-[ \t]*<<[ \t]*([\'"])(\w+)\1[ \t]*\r?\n')
# a remote wrapper means the python that runs is NOT this interpreter (version/fs skew) -> never prove
_REMOTE = re.compile(r'\bssh\b|\bdocker\s+(exec|run)\b|\bkubectl\b|\bwsl\b', re.I)


def _single_simple_command(cmd):
    """True only when Bash control flow cannot recover from or skip the path failure.

    This deliberately rejects useful-but-ambiguous forms (redirection, pipelines,
    multiple commands, substitutions, functions, conditionals).  Recall is traded
    for a mechanically honest whole-command proof.
    """
    quote = None
    escaped = False
    for ch in cmd:
        if escaped:
            escaped = False
            continue
        if quote:
            if quote == '"' and ch == BS:
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
        elif ch == BS:
            # Backslashes are expected in the target path and are not Bash
            # control operators by themselves.
            continue
        elif ch in "\r\n;&|<>`(){}#":
            return False
    return quote is None and not escaped


def pred_backslash_enoent(cmd):
    """(proven, corrected_cmd, proof) - A. Fail-open: any doubt -> (False, ...)."""
    try:
        if _REMOTE.search(cmd) or not _single_simple_command(cmd):
            return (False, None, None)
        for m in _PATA.finditer(cmd):
            p = m.group(1)
            if BS not in p or BS + BS in p:
                # One shell parse consumes a single unquoted backslash. Runs of
                # backslashes can survive one or more eval layers, so they are
                # not a proof of ENOENT without executor-specific parsing.
                continue
            slashed = p.replace(BS, '/')
            mangled = p.replace(BS, '')
            try:
                exists_slashed = os.path.exists(slashed)
                exists_mangled = os.path.exists(mangled)
            except Exception:
                return (False, None, None)     # fs error -> fail-open
            if exists_slashed and not exists_mangled:
                corrected = cmd.replace(p, slashed)
                return (True, corrected, 'A:backslash-path-enoent:%s' % slashed[:80])
        return (False, None, None)
    except Exception:
        return (False, None, None)


def pred_heredoc_compile_fail(cmd):
    """(proven, corrected_cmd, proof) - B. Fail-open."""
    try:
        if _REMOTE.search(cmd):
            return (False, None, None)
        if any(ord(ch) < 32 and ch not in "\r\n\t" for ch in cmd):
            return (False, None, None)         # Bash and Python disagree on exotic line controls
        m = _HD_HEAD.match(cmd)
        if not m:
            return (False, None, None)
        marker = m.group(2)
        tail = cmd[m.end():]
        lines = tail.split('\n')               # only LF is a Bash heredoc line boundary
        end = None
        for i, line in enumerate(lines):
            comparable = line[:-1] if line.endswith('\r') else line
            if comparable == marker:
                end = i
                break
        if end is None:
            return (False, None, None)
        if '\n'.join(lines[end + 1:]).strip():
            return (False, None, None)         # first delimiter is not the whole command
        body = '\n'.join(lines[:end])
        try:
            compile(body, '<heredoc>', 'exec')
        except SyntaxError as e:
            return (True, None, 'B:heredoc-syntaxerror:line %s: %s' % (e.lineno, str(e)[:70]))
        except Exception:
            return (False, None, None)         # non-syntax compile issue -> not a clean proof
        return (False, None, None)
    except Exception:
        return (False, None, None)


def prove_doomed(command, cwd=None):
    """Entry point. Returns (proven: bool, corrected_or_None, proof_str_or_None). Fail-open -> (False,..).
    cwd is accepted for future relative-path resolution; drive-letter paths are absolute so unused now."""
    try:
        cmd = command if isinstance(command, str) else ""
        if not cmd or len(cmd) > 20000:
            return (False, None, None)
        for pred in (pred_backslash_enoent, pred_heredoc_compile_fail):
            proven, corrected, proof = pred(cmd)
            if proven:
                return (True, corrected, proof)
        return (False, None, None)
    except Exception:
        return (False, None, None)


def _selftest():
    bs = BS
    ok = True
    # A: a real backslash path that exists forward-slashed (use this repo's dreaming dir)
    real = "C:" + bs + "Users" + bs + "owner" + bs + ".claude" + bs + "dreaming" + bs + "instinct_prime.py"
    cA = "python " + real + " --x"
    pA = prove_doomed(cA)
    print("  A unquoted backslash path (exists slashed) -> proven=%s proof=%s" % (pA[0], pA[2]))
    ok &= pA[0] is True and "corrected" not in str(pA[1] or "")  # corrected present
    ok &= (pA[1] is not None and "/" in pA[1])
    # A negative: QUOTED path must NOT prove (bash does not mangle)
    pAq = prove_doomed('python "' + real + '" --x')
    print("  A quoted path -> proven=%s (want False)" % pAq[0]); ok &= pAq[0] is False
    # A negative: ssh-remote must NOT prove
    pAr = prove_doomed('ssh box "python ' + real + '"')
    print("  A ssh-remote -> proven=%s (want False)" % pAr[0]); ok &= pAr[0] is False
    # B: heredoc with a syntax error
    cB = "python - <<'PY'\ndef f(:\n  pass\nPY"
    pB = prove_doomed(cB)
    print("  B heredoc syntaxerror -> proven=%s proof=%s" % (pB[0], pB[2])); ok &= pB[0] is True
    # B negative: valid heredoc must NOT prove
    cBok = "python - <<'PY'\nimport json\nprint(1)\nPY"
    pBok = prove_doomed(cBok)
    print("  B valid heredoc -> proven=%s (want False)" % pBok[0]); ok &= pBok[0] is False
    # fail-safe junk
    for junk in (None, 123, {"x": 1}, "x" * 30000, ""):
        try:
            prove_doomed(junk)
        except Exception as e:
            print("  FAIL-SAFE BREACH:", e); ok = False
    print("proof_of_failure selftest:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
