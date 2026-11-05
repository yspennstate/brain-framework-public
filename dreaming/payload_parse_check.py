#!/usr/bin/env python3
"""REFUTED 2026-08-17 - DO NOT WIRE THIS. Kept for the negative result, not for use.

MEASURED against 1,004 untruncated Bash commands from session transcripts: 361 carried an
extractable Python payload, 40 were flagged as not compiling, and only 5 of those 40 actually
failed. 87.5 percent false positives.

WHY IT CANNOT WORK, which is the useful part. The flagged commands are overwhelmingly of the form
    ssh box 'python3 -c "..."'   or   cd X && python - <<PY ... PY
where what I extract from the command STRING is not what Python eventually receives. A shell layer
rewrites quotes and backslashes on the way, and THAT REWRITE IS THE BUG THE CHECK WAS HUNTING. A
static pre-execution parse check reads the text before the corrupting transformation happens, so it
is structurally blind to the failure it exists for and noisy about everything else.

WHAT THIS LEAVES. The reflex's own existing advice is the complete fix and needs no detector:
"when a payload needs quotes-inside-quotes or newlines, stop nesting: Write it to a temp .py and
run the file - one quoting layer, zero escapes." That removes the shell transformation instead of
trying to predict it. A check that could work would have to sit where Python receives the bytes
(an audit hook inside the interpreter), not where the shell command is composed.

Original docstring follows.
"""

from __future__ import annotations
import os

import io
import json
import re
import sys

# python ... << 'EOF' ... EOF   (quoted delimiter only: an unquoted one is shell-expanded before
# python ever sees it, so what is on disk is not what would run and checking it would mislead)
HEREDOC = re.compile(
    r"python[0-9.]*\s+(?:-[A-Za-z]+\s+)*-?\s*<<\s*(['\"])(?P<tag>[A-Za-z_][A-Za-z0-9_]*)\1"
    r"(?P<body>.*?)^(?P=tag)\s*$",
    re.S | re.M)

# python -c '...' or python -c "..."
INLINE = re.compile(
    r"python[0-9.]*\s+(?:-[A-Za-z]+\s+)*-c\s+(['\"])(?P<body>.*?)(?<!\\)\1",
    re.S)


def extract_payloads(cmd: str):
    """Yield (kind, payload) for each Python payload embedded in a shell command."""
    for m in HEREDOC.finditer(cmd):
        yield ("heredoc", m.group("body"))
    for m in INLINE.finditer(cmd):
        yield ("inline-c", m.group("body"))


def check(cmd: str):
    """Return a list of (kind, SyntaxError) for payloads that will not compile. Empty = nothing found wrong."""
    bad = []
    for kind, body in extract_payloads(cmd):
        if not body.strip():
            continue
        try:
            compile(body, "<payload>", "exec")
        except SyntaxError as exc:
            bad.append((kind, exc))
        except Exception:
            # a non-SyntaxError here is not this checker's business
            pass
    return bad


def _score(outcomes_path: str, typical_path: str):
    FAMILY = re.compile(
        r"syntaxerror|unterminated|unicodeescape|invalid escape|not found verbatim|"
        r"eol while scanning|invalid syntax", re.I)

    deaths, others = [], []
    with io.open(outcomes_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            cmd = d.get("cmd") or ""
            sig = " ".join(d.get("sig") or [])
            (deaths if FAMILY.search(sig + " " + cmd) else others).append(cmd)

    typical = []
    with io.open(typical_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                typical.append(d.get("cmd") or d.get("command") or d.get("text") or line)
            except Exception:
                typical.append(line)

    def rate(rows):
        found = sum(1 for c in rows if extract_any(c))
        flagged = sum(1 for c in rows if check(c))
        return len(rows), found, flagged

    def extract_any(c):
        for _ in extract_payloads(c):
            return True
        return False

    print("PAYLOAD PARSE-CHECK, scored on real corpora")
    print()
    for name, rows in (("syntax-family deaths", deaths),
                       ("other outcome rows", others),
                       ("typical actions", typical)):
        n, found, flagged = rate(rows)
        print("  %-22s n=%-5d payload extractable in %-5d  FLAGGED as not compiling: %-5d %s"
              % (name, n, found, flagged,
                 ("= %.1f%% of n" % (100.0 * flagged / n)) if n else ""))
    print()
    print("  A flag on a 'typical action' is not a false alarm in the reflex sense - it means that")
    print("  action's payload genuinely does not compile. Read it as a defect found, then judge.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--score":
        _score(os.path.expanduser(r"~/.claude/dreaming/outcomes.jsonl"),
               os.path.expanduser(r"~/.claude/dreaming/graph_fire/typical_actions.jsonl"))
    else:
        print(__doc__)
