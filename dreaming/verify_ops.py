# -*- coding: utf-8 -*-
"""verify_ops.py - PostToolUse on Edit/Write: when an agent writes mathematics, name the obligation a risky step opens
(30 Sep 2026, his 01:10 order: "hooks telling them to check proofs, ideas, code and so on with lean algorithms as well,
to audit their work").

The 30 Sep design review (Proof_Aware_Audit_Hooks_Design) asked that a check fire on an identified operation, not on a
theorem-related word, and that it arrive before the step is built on. So this reads only the text just written to a
.tex, .lean or mathematical .md file and looks for five operations whose side condition is the usual silent failure:

  divide    dividing, cancelling or multiplying both sides by a quantity: it is nonzero (positive, for an inequality)
            on the whole declared range, boundary included
  exchange  a limit, sum, derivative or integral moved past another: the licensing theorem and its hypothesis (a
            dominating integrable bound, monotonicity, uniform or absolute convergence) - pointwise convergence alone
            does not do it (f_n = n on (0,1/n): each integral is 1, the limit is 0)
  witness   'there exists ... for all': the witness is fixed before the for-all, so it may not depend on its variable
  finite    a claim checked on finitely many cases stated for all: numerical evidence, not the general case
  sorry     a .lean file with sorry, admit or a new axiom: not a proof until the kernel accepts it without them

A step whose justification is written beside it (the report's "clean near-miss") stays silent. One card per operation
per file per session, at most two per tool call. Patch hunks and MultiEdit entries are evaluated independently.
Logged to verify_ops_log.jsonl. Fail-safe silent.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)
import hashlib
import io
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_DIR = os.environ.get("VERIFY_HOOKS_STATE_DIR") or HERE   # a test's state stays out of the live log
STATE = os.path.join(_DIR, ".verify_ops_state.json")
LOG = os.path.join(_DIR, "verify_ops_log.jsonl")
LEAN = "python " + os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "lean_check.py").replace(chr(92), "/")
MATH_MD = re.compile(r"\$[^$\n]{2,}\$|\\\(|\\\[|\\begin\{|\\frac|\\sum|\\int|\\lim|\\forall|\\exists|[∀∃∫∑≤≥]")
WINDOW = 320   # characters either side of the operation searched for its justification

# Tuned on 11,009 writes to .tex/.lean/.md files in his sessions over the 30 days to 30 Sep: bare "cancel" was mostly
# prose ("the crossings cancel"), a limit near an expectation was mostly a limsup beside an E, and "there exists ... for
# all" is how careful theorems are stated, so each operation now needs its operative form.
OPS = [
    ("divide",
     re.compile(r"\b(?:divid(?:e|ing))\s+(?:both\s+sides\s+|through\s+|each\s+side\s+)?by\b|"
                r"\bmultiply(?:ing)?\s+both\s+sides\s+by\b|\bcancel(?:l?ing)?\s+(?:the\s+|a\s+|common\s+)+(?:factor|term|denominator)s?\b", re.I),
     # any stated inequation beside the step counts: the side condition is usually written at the root ("x \neq 1"
     # before dividing by x - 1), not as "\neq 0"
     re.compile(r"\\neq?\b|\\neq|≠|>\s*0|\bnon-?zero\b|\bpositive\b|\bstrictly\b|\bis\s+not\s+zero\b|\bnever\s+(?:vanishes|zero)\b", re.I),
     "you divide or cancel by a quantity here: show it is nonzero on the whole declared range (positive, if you divide "
     "an inequality), boundary cases included, or split the case where it vanishes. One nonzero example does not show it."),
    ("exchange",
     # an unnamed interchange: naming Fubini or Tonelli is itself the justification, and "interchange" alone was mostly
     # algebra ("the interchange of the two Bott classes") in the replay
     re.compile(r"\b(?:interchang|exchang|swap)\w*\s+(?:of\s+)?(?:the\s+)?(?:limits?|sums?|integrals?|expectations?|derivatives?|"
                r"order\s+of\s+(?:summation|integration|limits|the\s+limits))\b|"
                r"\bpass\w*\s+(?:the\s+)?limit\s+(?:inside|under|through|into)|\bdifferentiat\w*\s+under\s+the\s+integral|"
                r"\\lim\S*\s*(?:\\int|\\sum|\\mathbb\{E\})[^=]{0,90}=\s*(?:\\int|\\sum|\\mathbb\{E\})[^=]{0,90}\\lim", re.I),
     re.compile(r"\bdominated\s+convergence\b|\bmonotone\s+convergence\b|\bbounded\s+convergence\b|\bdominated\s+by\b|"
                r"\bintegrable\s+(?:majorant|bound|envelope)\b|\buniform(?:ly)?\s+(?:convergen\w*|integrab\w*|bounded)\b|"
                r"\babsolutely\s+(?:summable|convergent|integrable)\b|\bVitali\b|\bFatou\b|\bnon-?negative\b|\\le\s*g\b|\bL\^?1\b|"
                r"\bcompact(?:ly)?\s+support\w*|\blegitimate\b|\bjustified\b|\blicen[sc]ed\b|\bmeasurable\b|\bsigma-finite\b", re.I),
     "a limit, sum, derivative or integral is moved past another here: name the theorem that licenses it and show its "
     "hypothesis - an integrable dominating bound, monotonicity, uniform or absolute convergence. Pointwise convergence "
     "alone does not (f_n = n on (0,1/n) has integral 1 for every n and limit 0)."),
    ("finite",
     re.compile(r"\b(?:checked|verified|tested|computed|confirmed|holds)\s+(?:numerically\s+)?(?:for|on|up\s+to|through)\s+"
                r"(?:all\s+)?(?:\$|\\\()?\s*[a-zA-Z]{1,3}\s*(?:≤|<=|\\leq?|<|up\s+to)\s*\d|"
                r"\b(?:for|on)\s+the\s+first\s+\d+\s+(?:values|cases|terms)\b", re.I),
     re.compile(r"\bnumerical\s+evidence\b|\bconjectur\w*\b|\bonly\s+for\b|\bfinite\s+range\b|\bin\s+this\s+range\b", re.I),
     "a finite check stands here beside a general claim: it is numerical evidence for the checked range only. Prove the "
     "general case, or state the claim for the range you checked."),
]
# the finite check matters only when a general claim stands beside it ("for each pseudoinverse" in a list of
# computations is not one - replay)
GENERAL = re.compile(r"\bfor\s+(?:all|every)\b|\\forall|∀", re.I)
# narration scripts and memory notes use the same words for other things ("dividing by two pi counts the turns",
# "returns cancel the factor"): only research writing is read
NOT_RESEARCH = re.compile(r"movie_projects|history_docs|[\\/]films?[\\/]|narrat|[\\/]memory[\\/]|[\\/]mem_[^\\/]*$|"
                          r"[\\/]sec_\d+\.md$|script", re.I)
# a quantifier swap made by an Edit: "for every x ... there exists y" in the old text, "there exists y ... for every x" in
# the new (the design's quantifier-drift case) - the witness may now have to work for every x at once
FORALL_EXISTS = re.compile(r"\bfor\s+(?:all|every|each)\b[^.;]{0,120}?\bthere\s+(?:exists|is|are)\b|∀[^.;]{0,60}∃|"
                           r"\\forall[^.;]{0,80}\\exists", re.I)
EXISTS_FORALL = re.compile(r"\bthere\s+(?:exists|is|are)\b[^.;]{0,120}?\bfor\s+(?:all|every|each)\b|∃[^.;]{0,60}∀|"
                           r"\\exists[^.;]{0,80}\\forall", re.I)
WITNESS_CARD = ("this edit turns a 'for every ... there exists' statement into 'there exists ... for every': the witness "
                "is now chosen before the for-all and must work for all of it at once. 'For every n there is m > n' does "
                "not give one m larger than every n. Prove the uniform statement or keep the original order.")
SORRY = re.compile(r"(?<![\w.])(?:sorry|admit)\b|^\s*axiom\s+\w+|\bnative_decide\b", re.M)
LEAN_COMMENT = re.compile(r"/-.*?-/|--[^\n]*|\"(?:\\.|[^\"\\])*\"", re.S)


def _written_chunks(tool, inp):
    """Yield (path, written text, replaced text), never joining separate files or edits.

    For apply_patch, only +/- lines in one Add/Update hunk are paired. Unchanged
    context becomes same-length NUL markers: it keeps distances without being
    read as a new operation or justification. Delete blocks and text outside the
    patch are ignored.
    """
    path = str(inp.get("file_path") or inp.get("notebook_path") or inp.get("path") or "")
    if tool in ("Write",):
        yield path, str(inp.get("content") or ""), ""
        return
    if tool in ("Edit",):
        yield path, str(inp.get("new_string") or ""), str(inp.get("old_string") or "")
        return
    if tool in ("MultiEdit",):
        for edit in inp.get("edits") or []:
            if isinstance(edit, dict):
                yield path, str(edit.get("new_string") or ""), str(edit.get("old_string") or "")
        return
    patch = str(inp.get("patch") or inp.get("input") or inp.get("command") or "")
    active = False
    path = ""
    added, removed = [], []
    for line in patch.splitlines():
        if line == "*** Begin Patch":
            active = True
            path, added, removed = "", [], []
            continue
        if not active:
            continue
        header = re.match(r"^\*\*\* (Add|Update|Delete) File: (.+)$", line)
        hunk = line == "@@" or line.startswith("@@ ")
        end = line == "*** End Patch"
        if header or hunk or end:
            if path and (added or removed):
                yield path, "\n".join(added), "\n".join(removed)
            added, removed = [], []
            if header:
                path = header.group(2).strip() if header.group(1) != "Delete" else ""
            if end:
                return
        elif line.startswith("*** Move to: "):
            # Move metadata precedes an Update's first hunk in apply_patch.
            if path and not (added or removed):
                path = line[len("*** Move to: "):].strip()
        elif path and line.startswith("+"):
            added.append(line[1:])
        elif path and line.startswith("-"):
            removed.append(line[1:])
        elif path and (line.startswith(" ") or not line):
            context = "\0" * len(line[1:])
            added.append(context)
            removed.append(context)
    if path and (added or removed):
        yield path, "\n".join(added), "\n".join(removed)


def _written_matches(rx, text):
    """Matches inside written spans, with offsets into the context-padded text."""
    for span in re.finditer(r"[^\x00]+", text):
        for match in rx.finditer(span.group(0)):
            yield match, span.start()


def obligations(path, text, old=""):
    """[(op, card)] for the operations in text without their justification nearby, in OPS order."""
    ext = os.path.splitext(path.lower())[1]
    if ext not in (".tex", ".lean", ".md", ".txt") or not text.strip():
        return []
    out = []
    if ext == ".lean":
        # a comment that says "no sorry" is not a sorry (sampled: Solution.lean's docstring)
        m = SORRY.search(LEAN_COMMENT.sub(" ", text))
        if m:
            out.append(("sorry", "this Lean file contains '%s': it is not a proof until the kernel accepts it without "
                        "sorry, admit, native_decide or a new axiom. Compile it: %s --file %s" % (m.group(0).strip(), LEAN, path)))
        return out
    if ext in (".md", ".txt") and (not MATH_MD.search(text) or NOT_RESEARCH.search(path)):
        return []
    if (old and any(_written_matches(FORALL_EXISTS, old)) and any(_written_matches(EXISTS_FORALL, text))
            and not any(_written_matches(FORALL_EXISTS, text))):
        out.append(("witness", WITNESS_CARD))
    for op, rx, just, card in OPS:
        for m, offset in _written_matches(rx, text):
            lo, hi = max(0, offset + m.start() - WINDOW), min(len(text), offset + m.end() + WINDOW)
            if just.search(text[lo:hi]):
                continue
            if op == "finite" and not GENERAL.search(text[lo:hi]):
                continue
            out.append((op, card))
            break
    return out[:2]


def _state():
    try:
        with io.open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def decide(data):
    tool = str(data.get("tool_name") or data.get("tool") or "")
    inp = data.get("tool_input") or data.get("input") or {}
    if not isinstance(inp, dict):
        inp = {"input": str(inp)}
    chunks = list(_written_chunks(tool, inp))
    sess = str(data.get("session_id") or data.get("thread_id") or "")
    exts = sorted({os.path.splitext(p)[1].lower()[:8] for p, _text, _old in chunks if p})
    if exts:
        # one row per tool call that wrote a file, beside the log: the denominator of the card rate, and the proof the
        # hook runs ("exts" when one patch writes several kinds of file)
        try:
            row = {"ts": int(time.time()), "hook": "ops", "s": sess[:12], "ext": exts[0]}
            if len(exts) > 1:
                row["exts"] = exts
            with io.open(os.path.join(os.path.dirname(LOG), "verify_hooks_seen.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except Exception:
            pass
    found = [(path, op, card) for path, text, old in chunks for op, card in obligations(path, text, old)]
    if not found:
        return ""
    st = _state()
    now = time.time()
    seen = st.get(sess) or {}
    fresh = []
    for path, op, card in found:
        key = hashlib.sha1((os.path.normcase(path) + "|" + op).encode("utf-8")).hexdigest()[:16]
        if key in seen:
            continue
        seen[key] = now
        fresh.append((path, op, card))
        if len(fresh) == 2:
            break   # Do not consume cooldowns for cards omitted by the call-wide cap.
    if not fresh:
        return ""
    st[sess] = seen
    st = {k: v for k, v in st.items() if isinstance(v, dict) and any(now - t < 86400 for t in v.values())}
    try:
        tmp = STATE + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        os.replace(tmp, STATE)
        with io.open(LOG, "a", encoding="utf-8") as fh:
            by_file = {}
            for path, op, _ in fresh:
                by_file.setdefault(path, []).append(op)
            files = [{"file": os.path.basename(path), "ops": ops} for path, ops in by_file.items()]
            row = {"ts": now, "session": sess, "tool": tool,
                   "file": files[0]["file"] if len(files) == 1 else None,
                   "ops": [op for _, op, _ in fresh]}
            if len(files) > 1:
                row["files"] = files
            # Keep one row per emitting call, so multi-file edits do not inflate call counts.
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass
    return "\n".join("[verify-op %s] %s" % (op, card) for _, op, card in fresh)


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    try:
        block = decide(data)
    except Exception:
        return
    if block:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": block}}))


if __name__ == "__main__":
    main()
