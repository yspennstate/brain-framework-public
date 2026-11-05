# -*- coding: utf-8 -*-
"""verify_gate.py - Stop hook: a claim needs a check that could have failed on it.

The design goal (30 Sep 2026): Lean checking is part of the math brain, and agents connected to it are
prompted by hooks to check proofs, ideas and code with Lean, to audit their work, and to plan how to verify it
in more than one way. verify_sense.py asks for the checks when the work
starts; this is the other end. It reads the message about to reach him and the turn's own tool calls, and blocks once
when the message claims something that nothing in the turn could have refuted:

  R1 stale check   the message says fixed / tests pass / verified / works, the turn edited code, and no check ran
                   after the last code edit (a check that ran before an edit says nothing about the edit)
  R2 Lean claim    the message says Lean-checked / kernel-verified / compiles in Lean / sorry-free, and no Lean ran
                   in the turn, or the last Lean run did not support it: an error or a sorry, a failure or unrecorded
                   exit, or a lean_check receipt short of the claim (a kernel-checked or formally verified claim needs
                   FORMAL_TARGET_VERIFIED; "compiles in Lean" needs only a compile)
  R3 proof claim   the message says proved / QED / the proof is complete / holds for all n, no computation, test or
                   Lean ran in the turn, and the message does not say what rung the claim reached (sketch, conjecture,
                   numerical evidence, unverified)

Each claim is judged with the hedges and record references of its own sentence (and of the next one when that one
refers back, "This is only a sketch"): an unverified side remark elsewhere in the message does not excuse a claim, and
the noun "conjecture" is not a hedge ("the FEI conjecture is solved" is a proof claim). A command is a check only when
it ran something and returned: reading, echoing, listing or searching a checker's file is not running it (the dot's
audit of these hooks, PR 184, 30 Sep).

Reads Claude transcripts and Codex rollouts. It blocks whether or not the stop is a continuation (in a /goal session
every stop is one, and a gate that stood down there would never fire where most of his work happens). Two limits keep
it out of a loop and out of the way: the stop right after one of its blocks, in the same turn, is never blocked (that
stop is where the agent's answer to the block is logged), and a rule blocks once per turn - the user prompt the work
answers - and again in that turn only after REARM_S and for a different claim. Until 30 Sep 03:0x it was once per rule
per session, so a session reused for a second task never met the gate again (1000-page report D12-T02); until 30 Sep
12:4x the stop after a block was spared even when it answered a new prompt. Every decision is logged to
verify_gate_log.jsonl with the evidence it saw, and the next stop after a block logs what the agent did about it (checks
added, claim kept or softened). A block lands after the claim is written, so its follow-ups measure correction, not
prevention (report D11-T02, D16-T03); verify_sense.py and verify_ops.py are the before-the-step channels.
verify_gate_SHADOW beside this file makes it log without blocking. Fail-safe: any error -> silent.
"""
import hashlib
import io
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REARM_S = 45 * 60   # a rule that blocked in a turn may block again in that turn after this long, for a different claim
# VERIFY_HOOKS_STATE_DIR keeps a test's state and log out of the live ones the measurement reads
_DIR = os.environ.get("VERIFY_HOOKS_STATE_DIR") or HERE
STATE = os.path.join(_DIR, ".verify_gate_state.json")
LOG = os.path.join(_DIR, "verify_gate_log.jsonl")
SHADOW = os.path.join(HERE, "verify_gate_SHADOW")
LEAN_CHECK = ("python " if os.name == "nt" else "python3 ") + os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "lean_check.py").replace(chr(92), "/")

CODE_EXT = re.compile(r"\.(?:py|pyw|lean|js|mjs|cjs|ts|tsx|jsx|rs|go|java|kt|c|cc|cpp|h|hpp|cs|rb|php|ps1|psm1|sh|bash|"
                      r"jl|r|m|swift|scala|sql|vbs|bat|cmd)$", re.I)

# a command that executes something that could fail on the claim: a test, a compiler or checker, or code being run.
# Reading, listing, grepping, git and copying are not checks.
CHECK_CMD = re.compile(
    r"\bpytest\b|\bunittest\b|\btest_[\w.-]*\.py\b|\b[\w-]+_test\.py\b|--selftest\b|\bselftest\b|"
    r"\b(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:test|build|lint|check)\b|\bcargo\s+(?:test|check|build|run)\b|"
    r"\bgo\s+(?:test|vet|build|run)\b|\bmvn\b|\bgradle\b|\bmake\b|\bctest\b|\bdotnet\s+(?:test|build|run)\b|"
    r"\blake(?:\.exe)?\s+(?:build|env|exe|test|lean)\b|\blean(?:\.exe)?\s+[^\s|;&]*\.lean\b|\blean(?:\.exe)?\s+-|lean_check|"
    r"\btsc\b|\bmypy\b|\bpyright\b|\bruff\b|\bflake8\b|\bpy_compile\b|\bcompileall\b|\bnode\s+(?:--check\s+)?\S+\.m?js\b|"
    r"\[(?:System\.Management\.Automation\.Language\.)?Parser\]::Parse|"
    r"\b(?:python|python3|py|pythonw|sage|julia|Rscript|ruby|perl|node|deno|bun)(?:\d(?:\.\d+)?)?(?:\.exe)?[\"']?\s+"
    r"(?:-[A-Za-z]+(?:\s+\S+)?\s+)*(?:-c\b|-m\b|-\s|-$|<<|[^\s|;&<>]+\.(?:py|jl|r|R|rb|pl|js|mjs|ts))|"
    r"\b(?:bash|sh|pwsh|powershell)(?:\.exe)?\s+(?:-\S+\s+)*[^\s|;&]+\.(?:sh|ps1)\b|-File\s+\S+\.ps1\b",
    re.I | re.M)
LEAN_CMD = re.compile(r"lean_check|\blake(?:\.exe)?\s+(?:build|env|exe|lean)\b|\blean(?:\.exe)?[\"']?\s+(?:-\S+\s+)*[^\s|;&]*\.lean\b|"
                      r"\blean(?:\.exe)?[\"']?\s+--(?:run|stdin|json)\b", re.I)
# edits made through the shell: a redirect, tee, sed -i, cp/mv or Set-Content into a code file
SHELL_EDIT = re.compile(r"(?:(?<![0-9&])>>?\s*|\btee\s+(?:-a\s+)?|\bsed\s+-i\S*\s+(?:'[^']*'|\"[^\"]*\"|\S+)\s+|\b(?:cp|mv|copy|move)\s+\S+\s+|"
                        r"\b(?:Set-Content|Add-Content|Out-File)\s+(?:-(?:Path|LiteralPath|FilePath)\s+)?)"
                        r"[\"']?([^\s\"'|;&<>]+\.(?:py|lean|js|ts|tsx|rs|go|ps1|sh|jl|java|c|cpp|cs|rb))\b", re.I)

# A code claim about this turn's work. "Works now / is running" is left to reality_monitor, whose observations include
# ssh and process reads; a descriptive sentence ("a size check passes a truncated file") is not a claim, hence the
# article guard (the 30 Sep replay, fire #15).
CODE_CLAIM = re.compile(
    r"\b(?:is|are|now|all|been|was|were|it'?s|i'?ve|we'?ve|have|has)\s+(?:now\s+)?(?:fixed|repaired|resolved)\b|"
    r"^\s*(?:fixed|repaired|resolved)\b|"
    r"(?<!\ba\s)(?<!\ban\s)(?<!\bif\s)\b(?:all\s+)?(?:\d+\s+(?:of\s+\d+\s+)?)?(?:tests|checks|controls|cases)\s+(?:now\s+)?"
    r"(?:pass(?:es|ed|ing)?|are\s+green|succeed(?:s|ed)?)\b|"
    r"\bpass(?:es|ed)?\s+(?:all|every|each|the)\s+(?:\d+\s+)?(?:tests?|checks?|controls?)\b|"
    r"\b(?:i|we)\s+(?:have\s+)?(?:verified|confirmed|tested)\b|\bverified\s+(?:that|it|the|both|each|end)\b|\bconfirmed\s+(?:that|it|the)\b",
    re.I | re.M)
# the stop summary his 7 Sep order asks for recaps the whole session; its claims are about earlier turns, which this
# turn's tool calls cannot back or refute (replay fires #5, #6, #8, #20, #21, #23)
RECAP_RE = re.compile(r"WHAT I (?:WAS TOLD TO DO|HAVE DONE SO FAR)", re.I)
# edits to launcher and scheduling scripts are usually written to run later; a claim beside them is rarely about them
LAUNCHER_EXT = re.compile(r"\.(?:sh|bash|ps1|psm1|cmd|bat|vbs)$", re.I)
LEAN_CLAIM = re.compile(
    r"\b(?:lean|kernel)[\s-]+(?:checked|verified|certified|accept(?:s|ed))\b|\bverified\s+(?:in|with|by)\s+(?:the\s+)?lean\b|"
    r"\blean\s+(?:now\s+)?(?:compiles|accepts|accepted|verifies|verified|checks|proves|proved)\b|"
    r"\bcompiles?\s+(?:cleanly\s+)?(?:in|under|with)\s+lean\b|\bsorry[\s-]free\b|\baxiom[\s-]clean\b|"
    r"\bmachine[\s-]checked\b|\bformally\s+(?:verified|checked)\b", re.I)
PROOF_CLAIM = re.compile(
    r"\b(?:i|we)\s+(?:have\s+)?(?:now\s+)?(?:proved|proven|established|shown)\s+(?:that|the|it|this|both|each|every)\b|"
    r"\b(?:the|this|my|our)\s+proof\s+(?:is\s+)?(?:now\s+)?(?:complete|correct|valid|sound|rigorous|goes\s+through|checks\s+out|holds|closes)\b|"
    r"\bq\.e\.d\b|\bqed\b|"
    r"\b(?:theorem|lemma|proposition|corollary|conjecture|claim|inequality|identity|bound|estimate)\s+(?:is\s+)?(?:now\s+)?"
    r"(?:proved|proven|established|settled|resolved)\b|\bthis\s+(?:proves|establishes|settles)\b|"
    r"\bholds\s+for\s+(?:all|every)\b|"
    r"\b(?:conjecture|theorem|lemma|open\s+problem|problem)\s+is\s+(?:now\s+)?(?:solved|settled|resolved)\b", re.I)
# the sentence labels the claim's standing, or hedges it: the gate leaves that claim alone. "Conjecture" the noun names
# a problem ("the FEI conjecture is solved" is a claim, PR 184); only the words that give a claim that standing hedge it.
STATUS_RE = re.compile(
    r"\b(?:sketch|heuristic|conjectur(?:al|ally|ed)|(?:a|as\s+a|still\s+a|only\s+a|just\s+a|remains?\s+a)\s+conjecture|"
    r"(?:i|we)\s+conjecture|unverified|not\s+(?:yet\s+)?(?:verified|checked|tested|proved|proven|run)|"
    r"(?:have\s+not|haven'?t|did\s+not|didn'?t)\s+(?:yet\s+)?(?:verified|checked|tested|run|re-?run|compiled)|"
    r"needs?\s+(?:to\s+be\s+)?(?:checked|verified|tested)|numerical(?:ly)?\s+(?:evidence|supported|checked)|untested|"
    r"if\s+(?:this|it|the\s+argument)\s+(?:is\s+)?(?:correct|holds|right)|appears?\s+to|seems?\s+to|should\s+(?:hold|work|pass)|"
    r"plausib\w+|outline|i\s+(?:think|believe|expect)|probably|likely|pending|unresolved|open\s+question)\b", re.I)
# a claim about earlier work that names its record is not a claim about this turn
PRIOR_RE = re.compile(r"\b(?:receipt|commit\s+[0-9a-f]{7,}|previously|earlier\s+(?:today|this\s+week|run|session)|yesterday|last\s+week|"
                      r"on\s+\d{1,2}\s+(?:Sep|Oct|Aug)|on\s+20\d\d-\d\d-\d\d|in\s+(?:the|our)\s+(?:repo|paper|record)|published)\b", re.I)
# Lean's own failure lines only: a Lean message is "<file>.lean:<line>:<col>: error", and lean_check prints its verdict.
# A bare "error:" matched an argparse message from an unrelated command in the same shell call (replay fire #22).
ERROR_OUT = re.compile(r"\.lean:\d+:\d+:\s*error|unsolved goals|declaration uses 'sorry'|linarith failed|"
                       r"\bCOMPILE_FAILED\b|\bADMITTED_TARGET\b|\bSETUP_UNVERIFIED\b|\bRUNTIME_UNVERIFIED\b")
# lean_check's receipt statuses (proof_receipt.classify, plus SETUP_UNVERIFIED and the older COMPILED). The last one a
# Lean run printed decides what it supports: FORMAL_TARGET_VERIFIED is the only one that certifies a named theorem, and a
# compile-level claim is supported by any status that means the source compiled. Read from the output, not the exit code:
# a compound command ending in an echo hides the checker's exit 3 (PR 184, finding 3).
RECEIPT_STATUS = re.compile(r"\b(FORMAL_TARGET_VERIFIED|COMPILED_NO_NAMED_TARGET|COMPILED_SOURCE_UNREVIEWED|"
                            r"SOURCE_REVIEW_MISMATCH|TARGET_IDENTITY_MISSING_OR_MISMATCH|TARGET_IDENTITY_MALFORMED|"
                            r"TARGET_KIND_MISSING_OR_AMBIGUOUS|TARGET_NOT_THEOREM|TARGET_AXIOMS_MISSING_OR_AMBIGUOUS|"
                            r"MALFORMED_AXIOM_RECEIPT|ADMITTED_TARGET|UNAPPROVED_AXIOMS|EVIDENCE_DRIFT_OR_MISSING|"
                            r"RUNTIME_UNVERIFIED|COMPILE_FAILED|SETUP_UNVERIFIED|COMPILED)\b")
NOT_COMPILED = {"COMPILE_FAILED", "RUNTIME_UNVERIFIED", "SETUP_UNVERIFIED", "EVIDENCE_DRIFT_OR_MISSING", "ADMITTED_TARGET"}
# the claims a clean compile supports; every other Lean claim needs a verified target when a receipt is printed
COMPILE_CLAIM = re.compile(r"\bcompiles?\b|\bsorry[\s-]free\b|\blean\s+(?:now\s+)?(?:accepts|accepted)\b", re.I)

# a sentence ends at . ! or ? and a space, or at a line break; not after an abbreviation, an initial or a list number
_END = re.compile(r"(?<=[.!?])[\"')\]]*[ \t]+|[ \t]*\n\s*")
_ABBREV = re.compile(r"(?:\b(?:e\.g|i\.e|cf|vs|etc|al|Fig|Eqs?|Thm|Lem|Prop|Cor|Sec|Ch|No|pp?|approx|resp)\.|\b[A-Z]\.|"
                     r"^\s*\d+\.)$")
# a following sentence that refers back to the claim carries its hedge ("The theorem is proved. This is only a sketch.")
_ANAPHOR = re.compile(r"^\s*[\"'(]?\s*(?:this|that|it|these|those|both|but|however|though|although|caveat|note|"
                      r"the\s+(?:proof|argument|claim|result|sketch|derivation|computation|statement|check|lemma|theorem|"
                      r"bound|fix|change)s?)\b", re.I)
# ... and so does a next sentence in which the agent says it did not check ("Fixed the parser. I have not run the tests
# yet."); a first-person remark about anything else ("I also read an earlier receipt") does not
_SELF_UNCHECKED = re.compile(r"^\s*(?:i|we)\s+(?:have\s+not|haven'?t|did\s+not|didn'?t|could\s+not|couldn'?t|was\s+not|"
                             r"wasn'?t)\b.{0,40}?\b(?:verif|check|test|run\b|re-?run|compil|prov|confirm)", re.I)


def _sentences(text):
    """(start, end) of each sentence of the message: the unit a hedge or a record reference qualifies."""
    spans, start = [], 0
    for m in _END.finditer(text):
        if "\n" not in m.group(0) and _ABBREV.search(text[start:m.start()]):
            continue
        if text[start:m.start()].strip():
            spans.append((start, m.start()))
        start = m.end()
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans or [(0, len(text))]


def _qualified(text, spans, pos, rx):
    """Does rx qualify the claim at pos: in its own sentence, or in the next one when that one refers back to it?"""
    for i, (s, e) in enumerate(spans):
        if s <= pos < e or (pos >= e and (i + 1 == len(spans) or pos < spans[i + 1][0])):
            if rx.search(text[s:e]):
                return True
            if i + 1 < len(spans):
                nxt = text[spans[i + 1][0]:spans[i + 1][1]]
                if rx is STATUS_RE and _SELF_UNCHECKED.match(nxt):
                    return True
                return bool(_ANAPHOR.match(nxt) and rx.search(nxt))
            return False
    return bool(rx.search(text))


# A command is split into the parts a shell runs (outside quotes, at ; && || | and line breaks), after unwrapping the
# shell a Codex rollout records around it (powershell.exe -Command ..., bash -lc ...). A part whose program only reads,
# prints, lists, searches or moves files is never a check, however it names a checker: cat lean_check.py, echo 'lake env
# lean X.lean' and grep pytest run nothing (PR 184, finding 2).
_WRAP = re.compile(r"^\s*(?:&\s*)?[\"']?(?:[^\s\"']*[\\/])?(?:powershell|pwsh|bash|sh|zsh|dash|cmd)(?:\.exe)?[\"']?"
                   r"(?:\s+(?:-NoProfile|-NonInteractive|-NoLogo|-ExecutionPolicy\s+\S+|-l|-i|-e|-x|/d|/s|/q))*"
                   r"\s+(?:-Command|-c|-lc|-ic|/c|/k)\s+", re.I)
_READER = re.compile(r"^(?:[A-Za-z_]\w*=\S*\s+)*(?:&\s*)?[\"']?(?:[^\s\"']*[\\/])?(?:cat|type|more|less|head|tail|echo|printf|"
                     r"grep|egrep|fgrep|rg|findstr|awk|sed|wc|ls|dir|find|git|diff|cp|mv|copy|move|rm|del|touch|mkdir|"
                     r"stat|file|which|where|get-content|gc|select-string|sls|write-output|write-host|out-file|set-content|"
                     r"add-content|get-childitem|gci|test-path|cd|set-location|pushd|popd|export|set|sleep|start-sleep|"
                     r"curl|wget|start|explorer)(?:\.exe)?[\"']?(?=\s|$)", re.I)


def _unwrap(cmd):
    for _ in range(3):
        m = _WRAP.match(cmd)
        if not m:
            break
        cmd = cmd[m.end():].strip()
        if len(cmd) >= 2 and cmd[0] == cmd[-1] and cmd[0] in "\"'":
            cmd = cmd[1:-1]
    return cmd


# a quoted run (to its close, or to the end if never closed), a separator (; newline | && ||), plain text, or a lone &
_TOK = re.compile(r'"[^"]*"?|\'[^\']*\'?|&&|\|\||[;\n|]|[^"\';\n|&]+|&')


def _parts(cmd):
    """The command split at ; newline | && || outside quotes. A regex walk, not a char loop: same parts on 20,652
    transcript commands, about 6x faster (31 Oct)."""
    out, cur = [], []
    for m in _TOK.finditer(_unwrap(cmd or "")):
        t = m.group(0)
        if t in (";", "\n", "|", "&&", "||"):
            out.append("".join(cur))
            cur = []
        else:
            cur.append(t)
    out.append("".join(cur))
    return [p.strip() for p in out if p.strip()]


def _runs(cmd, rx):
    """Does some part of the command run a program that rx recognises as a check (or as Lean)?"""
    return any(rx.search(p) for p in _parts(cmd) if not _READER.match(p))


def _base(path):
    """The file name of a Windows or POSIX path on either platform (os.path.basename keeps a backslash path whole on
    Linux, PR 184's baseline note)."""
    return re.split(r"[\\/]", path or "")[-1]


def _read_tail(path, first=2_000_000, cap=48_000_000):
    """The file's tail, widened until it holds `need` - bounded, because a transcript can be a gigabyte."""
    size = os.path.getsize(path)
    span = first
    while True:
        with open(path, "rb") as fh:
            fh.seek(max(0, size - span))
            raw = fh.read()
        # split on \n only: splitlines() also cuts at a raw U+2028/U+2029 inside a JSON string, losing that record
        yield raw.decode("utf-8", "replace").split("\n")[(1 if span < size else 0):]
        if span >= size or span >= cap:
            return
        span *= 4


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    out.append(b.get("text", ""))
                elif b.get("type") == "tool_result":
                    out.append(_text_of(b.get("content")))
        return "\n".join(out)
    return ""


def _line_key(line):
    return hashlib.sha1(line[:600].encode("utf-8", "replace")).hexdigest()[:16]


# a user-shaped record the harness or a hook wrote: not a prompt of his, so never the start of a turn. A Codex Stop hook's
# continuation is a <hook_prompt>, a goal's resume a <goal_context>; a /goal or a prompt that opens with a paste is his.
HARNESS = re.compile(r"^\s*(?:<(?:hook_prompt|goal_context|codex_internal_context|system-reminder|task-notification|"
                     r"cross-session-message|agent-message|agent-notification|subagent_notification|turn_aborted|"
                     r"local-command-stdout|local-command-caveat|environment_context|user_instructions|"
                     r"recommended_plugins|INSTRUCTIONS)\b|# AGENTS\.md|Stop hook feedback|Caveat: |\[Request interrupted)", re.I)


def claude_turn(path):
    """(events, final_text, turn_started, turn_key) for the turn in a Claude transcript. events are dicts in order:
    {"k": "cmd", "cmd", "id"} / {"k": "edit", "path"} / {"k": "out", "id", "text", "err"}. turn_key names the user
    prompt the turn answers (a Stop hook's feedback is not a prompt, so a continuation stays in its turn)."""
    for lines in _read_tail(path):
        start, key = None, ""
        for i in range(len(lines) - 1, -1, -1):
            ln = lines[i]
            if '"type":"user"' not in ln and '"type": "user"' not in ln:
                continue
            try:
                o = json.loads(ln)
            except Exception:
                continue
            if o.get("type") != "user" or o.get("isMeta") or o.get("isCompactSummary") or o.get("toolUseResult"):
                continue
            m = o.get("message") if isinstance(o.get("message"), dict) else {}
            c = m.get("content")
            if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
                continue
            txt = _text_of(c).strip()
            if not txt or HARNESS.match(txt):
                continue
            start, key = i, str(o.get("uuid") or o.get("timestamp") or "") or _line_key(ln)
            break
        if start is None:
            continue
        events, final = [], ""
        for ln in lines[start + 1:]:
            try:
                o = json.loads(ln)
            except Exception:
                continue
            m = o.get("message") if isinstance(o.get("message"), dict) else {}
            c = m.get("content")
            if o.get("type") == "assistant" and isinstance(c, list):
                texts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
                if any(t.strip() for t in texts):
                    final = "\n".join(texts)
                for b in c:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    name, inp = b.get("name") or "", b.get("input") or {}
                    if name in ("Bash", "PowerShell"):
                        cmd = str(inp.get("command") or "")
                        events.append({"k": "cmd", "cmd": cmd, "id": b.get("id")})
                        for p in SHELL_EDIT.findall(cmd):
                            events.append({"k": "edit", "path": p, "via": "shell"})
                    elif name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                        events.append({"k": "edit", "path": str(inp.get("file_path") or inp.get("notebook_path") or "")})
            elif o.get("type") == "user" and isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        events.append({"k": "out", "id": b.get("tool_use_id"), "text": _text_of(b.get("content"))[:6000],
                                       "err": bool(b.get("is_error")), "exit": 1 if b.get("is_error") else 0})
        return events, final, True, key
    return [], "", False, ""


# claude_turn re-reads up to 128 MB and re-parses the whole turn at every stop; a session kept going by Stop-hook
# continuations stays in one turn for days (1 Nov: 0.4-3.4 s a stop on the eight newest transcripts, 85-233 MB). The
# cache keeps the turn's start offset and its events per transcript and parses only the bytes appended since. Same
# result as claude_turn, except that tool output text is kept only for Lean commands: evaluate reads no other.
TURN_CACHE = os.path.join(_DIR, ".verify_gate_turns")
_FIRST, _CAP = 2_000_000, 48_000_000
_MAX_SPAN = _FIRST
while _MAX_SPAN < _CAP:
    _MAX_SPAN *= 4                      # the widest window _read_tail reaches (128 MB)


def _prompt_key(ln):
    """claude_turn's test for a prompt of his: the turn key, or None."""
    if '"type":"user"' not in ln and '"type": "user"' not in ln:
        return None
    try:
        o = json.loads(ln)
    except Exception:
        return None
    if o.get("type") != "user" or o.get("isMeta") or o.get("isCompactSummary") or o.get("toolUseResult"):
        return None
    m = o.get("message") if isinstance(o.get("message"), dict) else {}
    c = m.get("content")
    if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
        return None
    txt = _text_of(c).strip()
    if not txt or HARNESS.match(txt):
        return None
    return str(o.get("uuid") or o.get("timestamp") or "") or _line_key(ln)


def _feed(ln, events, lean_ids, final):
    """claude_turn's forward pass over one line; returns the turn's final text so far."""
    try:
        o = json.loads(ln)
    except Exception:
        return final
    m = o.get("message") if isinstance(o.get("message"), dict) else {}
    c = m.get("content")
    if o.get("type") == "assistant" and isinstance(c, list):
        texts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
        if any(t.strip() for t in texts):
            final = "\n".join(texts)
        for b in c:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            name, inp = b.get("name") or "", b.get("input") or {}
            if name in ("Bash", "PowerShell"):
                cmd = str(inp.get("command") or "")
                lean = _runs(cmd, LEAN_CMD)
                events.append({"k": "cmd", "cmd": cmd, "id": b.get("id"), "chk": _runs(cmd, CHECK_CMD), "lean": lean})
                if lean:
                    lean_ids.add(b.get("id"))
                for p in SHELL_EDIT.findall(cmd):
                    events.append({"k": "edit", "path": p, "via": "shell"})
            elif name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                events.append({"k": "edit", "path": str(inp.get("file_path") or inp.get("notebook_path") or "")})
    elif o.get("type") == "user" and isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                tid = b.get("tool_use_id")
                events.append({"k": "out", "id": tid, "text": _text_of(b.get("content"))[:6000] if tid in lean_ids else "",
                               "err": bool(b.get("is_error")), "exit": 1 if b.get("is_error") else 0})
    return final


def _lines_at(raw, base):
    """(offset, text) for each complete line of raw, which starts at file offset base, and the offset after the last."""
    out, o = [], base
    parts = raw.split(b"\n")
    for p in parts[:-1]:
        out.append((o, p.decode("utf-8", "replace")))
        o += len(p) + 1
    return out, o, parts[-1]


def _cold_start(path, size):
    """claude_turn's backward search with byte offsets: (offset, key) of the newest prompt in reach, or (None, "")."""
    span = _FIRST
    while True:
        lo = max(0, size - span)
        with open(path, "rb") as fh:
            fh.seek(lo)
            raw = fh.read(size - lo)
        lines, _, tail = _lines_at(raw, lo)
        if tail:
            lines.append((size - len(tail), tail.decode("utf-8", "replace")))
        for off, ln in reversed(lines[1:] if lo > 0 else lines):
            key = _prompt_key(ln)
            if key:
                return off, key
        if span >= size or span >= _CAP:
            return None, ""
        span *= 4


def _marks(path, end):
    """Hashes of the file's first 4 KB and of the 4 KB before end: a transcript rewritten in place fails them."""
    with open(path, "rb") as fh:
        head = fh.read(4096)
        fh.seek(max(0, end - 4096))
        before = fh.read(min(end, 4096))
    return hashlib.sha1(head).hexdigest()[:16], hashlib.sha1(before).hexdigest()[:16]


def _prune_turn_cache(days=7):
    try:
        cut = time.time() - days * 86400
        for e in os.scandir(TURN_CACHE):
            if e.name.endswith(".json") and e.stat().st_mtime < cut:
                os.remove(e.path)
    except OSError:
        pass


def claude_turn_cached(path):
    """claude_turn's (events, final, found, key), reading only what the transcript gained since the last stop."""
    st = os.stat(path)
    cpath = os.path.join(TURN_CACHE, hashlib.sha1(os.path.abspath(path).encode("utf-8")).hexdigest()[:20] + ".json")
    c = None
    try:
        with io.open(cpath, encoding="utf-8") as fh:
            c = json.load(fh)
        if (c.get("v") != 2 or c.get("ino") != st.st_ino or c.get("dev") != st.st_dev or st.st_size < c.get("end", 0)
                or list(_marks(path, c["end"])) != c.get("marks")):
            c = None
    except Exception:
        c = None
    if c is None:
        _prune_turn_cache()
        start, key = _cold_start(path, st.st_size)
        c = {"v": 2, "ino": st.st_ino, "dev": st.st_dev, "start": start, "key": key,
             "end": start if start is not None else st.st_size, "events": [], "lean": [], "final": "", "marks": None}
    with open(path, "rb") as fh:
        fh.seek(c["end"])
        raw = fh.read(st.st_size - c["end"])
    lines, end, tail = _lines_at(raw, c["end"])
    # a prompt of his among the new lines starts a new turn there (the newest one wins, as in claude_turn)
    for i in range(len(lines) - 1, -1, -1):
        key = _prompt_key(lines[i][1])
        if key:
            c.update(start=lines[i][0], key=key, events=[], lean=[], final="")
            lines = lines[i + 1:]
            break
    else:
        if c["start"] is not None and c["start"] == c["end"]:
            lines = lines[1:]           # a cold start: the first new line is the prompt itself
    events, lean_ids, final = c["events"], set(c["lean"]), c["final"]
    for _, ln in lines:
        final = _feed(ln, events, lean_ids, final)
    if end != c["end"] or c["final"] != final or c.get("marks") is None:
        c.update(end=end, final=final, lean=sorted(x for x in lean_ids if isinstance(x, str)),
                 marks=list(_marks(path, end)))
        try:
            os.makedirs(TURN_CACHE, exist_ok=True)
            tmp = "%s.%d.tmp" % (cpath, os.getpid())
            with io.open(tmp, "w", encoding="utf-8") as fh:
                json.dump(c, fh, ensure_ascii=False)
            os.replace(tmp, cpath)
        except Exception:
            pass
    if c["start"] is None or (st.st_size > _MAX_SPAN and c["start"] <= st.st_size - _MAX_SPAN):
        return [], "", False, ""        # out of claude_turn's reach: it would find no turn either
    if tail:                            # a last line with no newline yet: read now, cached next time it is complete
        events = list(events)
        final = _feed(tail.decode("utf-8", "replace"), events, set(lean_ids), final)
    return events, final, True, c["key"]


def _codex_prompt_key(lines):
    """The turn key of a Codex rollout: the last UserMessage he typed. A Stop hook's continuation (a user-role
    <hook_prompt> message) and a goal's resume are the harness's, so a turn keeps its key through them."""
    for i in range(len(lines) - 1, -1, -1):
        if '"UserMessage"' not in lines[i]:
            continue
        try:
            it = ((json.loads(lines[i]).get("payload") or {}).get("item") or {})
        except Exception:
            continue
        txt = "".join(b.get("text", "") for b in (it.get("content") or []) if isinstance(b, dict)).strip()
        if txt and not HARNESS.match(txt):
            return str(it.get("id") or "") or _line_key(lines[i])
    return ""


def codex_turn(path):
    """As claude_turn, for a Codex rollout. The events are those since the last task start or typed message; the key is
    his last typed prompt (_codex_prompt_key), so a hook's continuation stays in the turn it continues."""
    for lines in _read_tail(path):
        start = None
        for i in range(len(lines) - 1, -1, -1):
            if '"task_started"' in lines[i] or '"UserMessage"' in lines[i]:
                start = i
                break
        if start is None:
            continue
        events, final = [], ""
        for ln in lines[start + 1:]:
            try:
                o = json.loads(ln)
            except Exception:
                continue
            p = o.get("payload") or {}
            if p.get("type") != "item_completed":
                continue
            it = p.get("item") or {}
            t = it.get("type")
            if t == "CommandExecution":
                cmd = it.get("command")
                cmd = " ".join(cmd) if isinstance(cmd, list) else str(cmd or "")
                events.append({"k": "cmd", "cmd": cmd, "id": it.get("id")})
                for pth in SHELL_EDIT.findall(cmd):
                    events.append({"k": "edit", "path": pth, "via": "shell"})
                # an exit the rollout did not record stays unknown: it is neither a failure nor a success (PR 184)
                code = it.get("exit_code")
                try:
                    code = None if code is None or str(code).strip().lower() in ("", "none", "null") else int(code)
                except (TypeError, ValueError):
                    code = None
                events.append({"k": "out", "id": it.get("id"), "text": str(it.get("aggregated_output") or "")[:6000],
                               "err": code is not None and code != 0, "exit": code})
            elif t == "FileChange":
                for pth in (it.get("changes") or {}):
                    events.append({"k": "edit", "path": str(pth)})
            elif t == "AgentMessage":
                txt = " ".join(b.get("text", "") for b in (it.get("content") or []) if isinstance(b, dict))
                if txt.strip():
                    final = txt
        return events, final, True, _codex_prompt_key(lines) or _line_key(lines[start])
    return [], "", False, ""


def _codex_rollout(session_id):
    if not session_id:
        return ""
    root = os.path.join(os.path.expanduser("~"), ".codex", "sessions")
    best = ""
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(session_id + ".jsonl"):
                p = os.path.join(dirpath, f)
                if not best or os.path.getmtime(p) > os.path.getmtime(best):
                    best = p
    return best


def _stem(path):
    return os.path.splitext(_base(path))[0].lower()


def evaluate(events, final):
    """The rule that fires on this turn, if any: (rule, claim, detail) or None; and the evidence it read.
    R1w is the weak form of R1 (a source file edited after the last check, but no check in the turn had ever run it):
    logged for measurement, never blocked - in the 30 Sep replay most such edits were not what the claim was about."""
    outs = {e["id"]: e for e in events if e["k"] == "out" and e.get("id")}
    last_edit, last_check, last_lean, n_checks, n_lean = -1, -1, -1, 0, 0
    edited, checks, stale_retested, stale_other = [], [], [], []
    for i, e in enumerate(events):
        if e["k"] == "edit" and CODE_EXT.search(e.get("path") or "") and not LAUNCHER_EXT.search(e.get("path") or ""):
            last_edit = i
            edited.append(e["path"])
        elif e["k"] == "cmd" and e.get("id") in outs:   # a command with no result never finished: it checked nothing
            # "chk"/"lean": the same two tests, made once when claude_turn_cached first read the command
            if e["chk"] if "chk" in e else _runs(e["cmd"], CHECK_CMD):
                last_check, n_checks = i, n_checks + 1
                checks.append(e["cmd"].lower())
            if e["lean"] if "lean" in e else _runs(e["cmd"], LEAN_CMD):
                last_lean, n_lean = i, n_lean + 1
    if last_edit > last_check >= 0:
        # which files changed after the last check, and had an earlier check run them?
        earlier = "\n".join(checks)
        for e in events[last_check + 1:]:
            if e["k"] == "edit" and CODE_EXT.search(e.get("path") or "") and not LAUNCHER_EXT.search(e.get("path") or ""):
                st = _stem(e["path"])
                if st and (st in earlier or st.replace("test_", "") in earlier):
                    stale_retested.append(e["path"])
                else:
                    stale_other.append(e["path"])
    ev = {"n_events": len(events), "n_checks": n_checks, "n_lean": n_lean, "n_code_edits": len(edited),
          "stale": bool(last_edit >= 0 and last_check < last_edit), "stale_retested": len(stale_retested)}
    text = final or ""
    if not text.strip() or RECAP_RE.search(text):
        return None, ev
    spans = _sentences(text)

    def fresh(rx, *qualifiers):
        """The claims rx finds that no qualifier of their own sentence covers, in order."""
        return [m for m in rx.finditer(text) if not any(_qualified(text, spans, m.start(), q) for q in qualifiers)]

    for m in fresh(LEAN_CLAIM, STATUS_RE):
        if n_lean == 0:
            if _qualified(text, spans, m.start(), PRIOR_RE):
                continue
            return ("R2", m.group(0), "no Lean command ran in this turn"), ev
        why = _lean_verdict(outs.get(events[last_lean].get("id")), m.group(0))
        if why:
            return ("R2", m.group(0), why), ev
    if last_edit >= 0 and last_check < last_edit:
        for m in fresh(CODE_CLAIM, STATUS_RE)[:1]:
            if last_check < 0:
                where = _base(events[last_edit].get("path"))
                return ("R1", m.group(0), "code was edited (%s) and nothing was run after it" % where), ev
            if stale_retested:
                return ("R1", m.group(0), "%s was checked earlier in this turn, then edited again, and no check ran "
                                          "after the edit" % _base(stale_retested[-1])), ev
            return ("R1w", m.group(0), "a source edit (%s) came after the last check" %
                    _base(stale_other[-1] if stale_other else "")), ev
    if n_checks == 0:
        for m in fresh(PROOF_CLAIM, STATUS_RE, PRIOR_RE)[:1]:
            return ("R3", m.group(0), "no computation, test or Lean ran in this turn"), ev
    return None, ev


def _lean_verdict(out, claim):
    """Why the turn's last Lean run does not support this Lean claim; '' when it does."""
    if not out:
        return "the last Lean command has no recorded result"
    text = out.get("text") or ""
    said = next((ln.strip() for ln in text.splitlines() if ERROR_OUT.search(ln)), "")
    if said:
        return "the last Lean run printed \"%s\"" % said[:140]
    statuses = RECEIPT_STATUS.findall(text)
    if statuses:
        st = statuses[-1]
        if st == "FORMAL_TARGET_VERIFIED" or (COMPILE_CLAIM.search(claim) and st not in NOT_COMPILED):
            return ""
        if st in NOT_COMPILED:
            return "the last Lean run's receipt says %s" % st
        return ("the last Lean run's receipt says %s, which certifies no named theorem (lean_check --target NAME "
                "--reviewed-sha256 SHA gives FORMAL_TARGET_VERIFIED; or say only that it compiles)" % st)
    if out.get("err"):
        return "the last Lean run exited with a failure"
    if "exit" in out and out["exit"] is None:
        return "the last Lean run's exit status was not recorded"
    return ""


ADVICE = {
    "R1": ("Rerun the check against the code as it stands now and report what it printed. The strongest check is one "
           "that fails on the old code and passes on the new."),
    "R2": ("Run it: " + LEAN_CHECK + " --file <file.lean> (or --stmt \"example ... := by ...\"; --core for Nat/Int/List "
           "statements), and quote the verdict. Lean certifies the formal statement only: restate it in words and compare "
           "it with the claim you mean."),
    "R3": ("Do one check that could fail on it before you call it proved: compute small and boundary cases, search for a "
           "counterexample to each hypothesis you did not use, or state the key step in Lean (" + LEAN_CHECK +
           " --stmt \"...\"). Or say what rung it reached: sketch, conjecture, numerical evidence, or proof unchecked."),
}


def _state():
    try:
        with io.open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_state(st):
    now = time.time()
    st = {k: v for k, v in st.items() if isinstance(v, dict) and now - v.get("t", 0) < 3 * 86400}
    try:
        tmp = STATE + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        os.replace(tmp, STATE)
    except Exception:
        pass


def _log(rec):
    try:
        with io.open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _seen(sess, runtime):
    """One row per stop evaluated, beside the log: the denominator of the fire rate, and the proof the gate runs."""
    try:
        with io.open(os.path.join(os.path.dirname(LOG), "verify_hooks_seen.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": int(time.time()), "hook": "gate", "s": sess[:12], "rt": runtime}) + "\n")
    except Exception:
        pass


def decide(data, runtime):
    """The Stop hook's decision for one payload: a dict to print, or None."""
    if (data.get("hook_event_name") or data.get("event_name") or "Stop") not in ("Stop", "SubagentStop"):
        return None
    sess = str(data.get("session_id") or data.get("thread_id") or "")
    tp = data.get("transcript_path") or ""
    final_payload = str(data.get("last_assistant_message") or "")
    if runtime == "codex":
        tp = tp if tp and os.path.exists(tp) else _codex_rollout(sess)
        events, final, ok, turn = codex_turn(tp) if tp and os.path.exists(tp) else ([], "", False, "")
    elif tp and os.path.exists(tp):
        try:
            events, final, ok, turn = claude_turn_cached(tp)
        except Exception:
            events, final, ok, turn = claude_turn(tp)
    else:
        events, final, ok, turn = [], "", False, ""
    final = final_payload or final
    if not final.strip():
        return None
    _seen(sess, runtime)
    hit, ev = evaluate(events, final)
    st = _state()
    mine = st.get(sess) or {"t": time.time(), "rules": {}}
    now = time.time()
    # the stop after one of our blocks: what did the agent do about it? In a Codex rollout the continuation starts a new
    # turn, so the counts are the new turn's own; in a Claude transcript it stays in the blocked turn
    pend = mine.get("pending")
    # the stop right after a block is spared only in the blocked turn; a new prompt of his meets the gate at once (PR 184,
    # finding 5: the pending record used to spare the first stop of whatever prompt came next)
    spared = bool(pend) and (not turn or not pend.get("turn") or turn == pend.get("turn"))
    if pend:
        same = bool(turn) and turn == pend.get("turn")
        _log({"ts": now, "kind": "followup", "session": sess, "runtime": runtime, "rule": pend["rule"],
              "checks_added": ev["n_checks"] - (pend["n_checks"] if same else 0),
              "lean_added": ev["n_lean"] - (pend["n_lean"] if same else 0),
              "claim_still_made": bool(hit and hit[0] == pend["rule"]),
              "status_labelled": bool(STATUS_RE.search(final)), "blocked_at": pend["t"], "same_turn": same})
        mine.pop("pending", None)
    if not hit:
        st[sess] = mine
        _save_state(st)
        return None
    rule, claim, detail = hit
    shadow = os.path.exists(SHADOW)
    prev = mine["rules"].get(rule)
    if isinstance(prev, (int, float)):   # state written before 30 Sep 03:0x: a bare time, no turn
        prev = {"t": float(prev), "turn": "", "claim": ""}
    # once per turn, and again in that turn only after REARM_S and for a new claim; a hook's continuation keeps its turn
    same_claim = bool(prev) and prev.get("claim") == claim.strip()[:160]
    recent = bool(prev) and now - prev.get("t", 0) < REARM_S
    already = bool(prev) and prev.get("turn") == turn and (recent or same_claim)
    block = not shadow and not already and not spared and bool(sess) and rule != "R1w"
    _log({"ts": now, "kind": "fire", "session": sess, "runtime": runtime, "rule": rule, "claim": claim[:80],
          "detail": detail, "evidence": ev, "blocked": block, "shadow": shadow, "repeat": already,
          "after_own_block": spared, "continuation": bool(data.get("stop_hook_active"))})
    if block:
        mine["rules"][rule] = {"t": now, "turn": turn, "claim": claim.strip()[:160]}
        mine["pending"] = {"rule": rule, "t": now, "n_checks": ev["n_checks"], "n_lean": ev["n_lean"], "turn": turn}
    mine["t"] = time.time()
    st[sess] = mine
    _save_state(st)
    if not block:
        return None
    reason = ("[verify-gate %s] Your report says \"%s\" but %s, so nothing in this turn could have shown it wrong. %s "
              "If a check did run that this gate cannot see, name it and its result in one line. (the standing rule: audit "
              "your work, verify it a couple of ways, use Lean where it applies)") % (rule, claim.strip(), detail, ADVICE[rule])
    return {"decision": "block", "reason": reason}


def main():
    runtime = "codex" if "--runtime" in sys.argv and sys.argv[sys.argv.index("--runtime") + 1:][:1] == ["codex"] else "claude"
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    try:
        out = decide(data, runtime)
    except Exception:
        return
    if out:
        sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        import test_verify_hooks
        raise SystemExit(test_verify_hooks.main(["gate"]))
    main()
