#!/usr/bin/env python3
"""prompt_archive.py - every prompt the owner gives an agent, saved in perpetuity, classified.

Owner directive (2026-07-09): "every single prompt that I give an agent is saved in a prompt file
in perpetuity, and is classified well - prompt for mistake, security, prove something, think about
something, improve something (whatever that prompt did)."

Two faces:
  hook mode (no argv)  : UserPromptSubmit -> append one JSONL line to memory/prompts/PROMPTS.jsonl.
                         Silent, fail-open, never blocks a prompt, never prints context.
  argv status          : one-line ledger read (count per class) for wake/status use.

The ledger is append-only and lives under the memory dir, so the nightly repo push preserves it
forever. Classification here is a cheap keyword PROVISIONAL tag only (a fast pattern cannot make a
content call - measured ~50% false-positive on content regexes, 2026-07-08); the real class is
written later by a model pass (backfill_prompts.py --classify writes class_model) and the nightly
dream classifies the previous day's new lines. Consumers should trust class_model, then fall back
to class_provisional.

Secrets: memory pushes to GitHub nightly (10 bot tokens shipped in plaintext once - 2026-07-08
scar), so obvious credentials are redacted BEFORE the line is written. The raw prompt is not kept
anywhere else by this hook.

Test seam: PROMPT_ARCHIVE_DIR points the ledger somewhere else so fixtures never touch the live
file (the ledgers-polluted-by-own-gate scar).
"""
import sys, os, io, json, re, time

MEM_DEFAULT = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
ARCHIVE_DIR = os.environ.get("PROMPT_ARCHIVE_DIR", os.path.join(MEM_DEFAULT, "prompts"))
LEDGER = os.path.join(ARCHIVE_DIR, "PROMPTS.jsonl")

# --- taxonomy (owner's five verbs first; keep small, multi-intent resolved by priority order) ---
# mistake  : reporting/correcting an error, a scar, "you did X wrong", post-mortem
# security : audit/harden/permissions/secrets/danger
# prove    : prove/verify/certify/check a claim is true (math, code, data)
# improve  : build/add/refactor/optimize/make-better (most /goal build work)
# think    : analyze/consider/design/research/answer - thinking as the deliverable
# run      : operate/monitor/keep-working/status directives (fleet, observers, clocks)
# remember : save/recall/organize memory itself
# admin    : config, models, environment, tooling setup
# other    : none of the above matched
TAXONOMY = ["mistake", "security", "prove", "improve", "think", "run", "remember", "admin", "other"]

_RULES = [
    ("mistake",  re.compile(r"\b(mistake|wrong|incorrect|you (broke|missed|forgot)|mess(ed)? up|"
                            r"error you|bug you|shouldn'?t have|why did you|correct(ion)?s?\b|fix what)", re.I)),
    ("security", re.compile(r"\b(secur|audit|harden|vulnerab|secret|token|credential|permission|"
                            r"danger|firewall|attack|leak)", re.I)),
    ("prove",    re.compile(r"\b(prove|proof|verify|verification|certif|confirm (that|it)|"
                            r"check (that|whether|if)|validate|rigorous)", re.I)),
    ("improve",  re.compile(r"\b(improve|build|implement|add|create|refactor|optimi[sz]e|upgrade|"
                            r"make (it|this|the)? ?(better|stronger|smarter|faster)|extend|enhance|develop)", re.I)),
    ("think",    re.compile(r"\b(think|consider|analy[sz]e|research|read about|what do you|how (would|should|does)|"
                            r"why (is|does|do)|design|explore|look into|study|understand|explain)", re.I)),
    ("run",      re.compile(r"\b(keep work|keep going|monitor|observ|watch|status|run(ning)? (smooth|the)|"
                            r"work (on this|for)|continue|carry on|hours?\b)", re.I)),
    ("remember", re.compile(r"\b(remember|memor(y|ies|ize)|save (this|that)|recall|write.{0,12}brain|"
                            r"note (this|that) down)", re.I)),
    ("admin",    re.compile(r"^/(model|config|permissions|status|help)|\b(settings?\.json|api key|"
                            r"install|configure|setup|set up)\b", re.I)),
]

# --- redaction: obvious credentials never reach a pushed file ---
# each entry: (compiled pattern, replacement). A live rpa_ RunPod key rode into the pushed ledger
# past the named-prefix list (2026-07-09), so a generic keyword-near-random-blob rule backs it up.
_SECRETS = [
    # Natural language can put a short credential after its purpose rather than
    # directly after the label. Bound both lookaheads to the SAME value token;
    # never borrow a digit from a later sentence or redact ordinary prose.
    (re.compile(r"((?i:password|passwd|pwd|passphrase)\b[ \t]+"
                r"(?i:to[ \t]+(?:connect|log[ \t]*in|login|sign[ \t]*in|authenticate)|"
                r"for[ \t]+(?:login|authentication|connecting))"
                r"(?:[ \t]+(?i:is|was))?[ \t]*[:=]?[ \t]+[\"'`]?)"
                r"(?!\[redacted)(?=[^\s\"'`,;/\\]{0,63}\d)"
                r"(?=[^\s\"'`,;/\\]{0,63}[A-Za-z])"
                r"[^\s\"'`,;/\\]{6,64}(?![^\s\"'`,;/\\])"),
     r"\1[redacted-password]"),
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,60}\b"), "[redacted-secret]"),   # telegram bot token
    (re.compile(r"\b(?:sk-ant-|sk-|sk_|ghp_|gho_|github_pat_|xoxb-|xoxp-|AKIA|rpa_|rpk_|hf_|gsk_)[A-Za-z0-9_\-]{16,}\b"),
     "[redacted-secret]"),
    # long high-entropy standalone token ("use this API, UYYDt3hSnLJ8froKR4gGEyq...") - a 40+ char
    # mixed-case+digit run on its own is an API key, not prose (a Chartmetric key rode in, 2026-07-09)
    (re.compile(r"(?<![/\w.])(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{40,}(?![\w.])"),
     "[redacted-secret]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "[redacted-private-key]"),
    # base64 secret bodies. Every rule above uses an alphanumeric character class, so a token
    # containing "/" or "+" - which most AWS secret access keys do - breaks the run below the
    # length floor and survives. Measured 2026-08-22: the ID was redacted and the secret was not,
    # from 2026-07-09 onward. Both rules below fire exactly twice across 18,751 real ledger rows
    # (the two known secrets) and never on a Windows path.
    (re.compile(r"((?i:secret\s+access\s+key)\b[\s\S]{0,60}?)(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"),
     r"\1[redacted-secret]"),
    (re.compile(r"(?<![A-Za-z0-9/+=\\])(?=[A-Za-z0-9/+=]*[a-z])(?=[A-Za-z0-9/+=]*[A-Z])"
                r"(?=[A-Za-z0-9/+=]*\d)(?=[A-Za-z0-9+=]*[/+])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"),
     "[redacted-secret]"),

    # Label-adjacent credentials, taken as a WHOLE token. This runs before the generic rule below
    # because that rule LEAVES A PREFIX OF THE VALUE BEHIND: its `.{0,40}?` matches anything, so on
    # an Azure service-principal secret (which carries a tilde, outside the value class) the lazy
    # prefix expands past the tilde, swallows `": "RQ18Q~` into group 1, and the \1 backreference
    # writes those six characters back out. Four rows on disk read `"password": "RQ18Q~[redacted-`.
    # `[^\w\[\n]{0,8}` cannot match a letter or digit, so the value must begin at the real token
    # boundary; excluding the newline stops the label reaching across a blank line into the NEXT
    # command (without it this redacted an AWS security-group id inside a shell line - a real
    # over-redaction caught by inspecting the blast radius rather than trusting the count).
    # Both lookaheads are BOUNDED to the value's own class: an unbounded (?=\S*\d) reaches across
    # compact JSON, where a menu record would satisfy "has a digit" from hundreds of chars away.
    # Controls: 3/3 positives fully clean, 0/8 negatives, 5 of 18,888 real rows.
    # See brainideas/redactor_prefix_controls.py.
    (re.compile(r"((?i:password|passwd|secret|client[_ -]?secret|api[_ -]?key|access[_ -]?key|"
                r"token|credential|passphrase)\b[^\w\[\n]{0,8})"
                r"(?!\[redacted)"
                r"(?=[^\s\"',;/\\]{0,79}\d)"
                r"(?=[^\s\"',;/\\]{0,79}[A-Za-z])"
                r"[^\s\"',;/\\]{20,80}"),
     r"\1[redacted-secret]"),

    (re.compile(r"((?i:key|token|secret|password|credential)\b.{0,40}?)\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[a-zA-Z])[A-Za-z0-9_\-]{24,}\b"),
     r"\1[redacted-secret]"),
    # short human passwords: "password: X" / "pwd X" with a digit-bearing 6-24 char value, and the
    # "username : name, <pass>" pair shape - two live brokerage passwords rode under the 24-char
    # floor of the generic rule above (caught during inline classification, 2026-07-09)
    (re.compile(r"((?i:password|passwd|pwd)\b(?:[^\S\n]+(?:is|was))?[^\S\n]*[:=]?[^\S\n]+)(?=\S*\d)[A-Za-z0-9!@#$%^&*_\-]{6,24}\b"),
     r"\1[redacted-password]"),
    # long generated passwords with bracket/symbol characters - a 32-char RDP Administrator password
    # sat unredacted from 2026-06-08 because it failed the rule above TWICE OVER: longer than the
    # 24-char cap, and it contains parentheses, which are not in that character class. Added rather
    # than widening the rule above so nothing already caught can regress. The three lookaheads
    # (digit AND letter AND a non-alphanumeric) are what separate a generated credential from prose
    # following the word "password"; measured 2026-08-22 to fire on exactly 1 of 18,880 ledger rows
    # and on 0 of 6 prose negative controls.
    (re.compile(r"((?i:password|passwd|pwd)\b(?:[^\S\n]+(?:is|was))?[^\S\n]*[:=]?[^\S\n]+)"
                r"(?=\S*\d)(?=\S*[A-Za-z])(?=\S*[^A-Za-z0-9\s])[^\s,;]{8,64}"),
     r"\1[redacted-password]"),
    # the "<host> MIDDOT <account> MIDDOT <value>" shape a console prints when it hands over a login.
    # The rule above missed the SECOND copy of the same leaked RDP credential because that copy
    # carries no "password" keyword at all - fixing the keyword rule alone left the secret in place,
    # and only counting OCCURRENCES rather than ROWS caught it. Anchors on the account name instead.
    # Path separators are excluded from the value: without that, "root: /srv/aiwork/qqq_backtests/..."
    # was redacted as a password (a real negative-control failure, 2026-08-22 - a path has digits,
    # letters and punctuation too). Controls: 3/3 positives, 0/11 negatives, and exactly 1 of 18,884
    # real ledger rows. See brainideas/redactor_triple_controls.py.
    (re.compile(r"((?i:administrator|root|admin|ubuntu|ec2-user)\s*[·|:]\s*)"
                r"(?=\S*\d)(?=\S*[A-Za-z])(?=\S*[^A-Za-z0-9\s])[^\s,;/\\]{12,64}"),
     r"\1[redacted-password]"),
    (re.compile(r"((?i:username|user name|login)\b[^,\n]{0,30}?,\s*)(?=\S*\d)(?![0-9]+\b)[A-Za-z0-9!@#$%^&*_\-]{8,24}\b"),
     r"\1[redacted-password]"),
    # 2FA / login one-time codes ("account N code is NNNNNN") - 6-8 digit, single-use and stale by
    # archive time but code-shaped, so redact on principle
    (re.compile(r"((?i:\bcode\b(?:\s+(?:is|was))?)\s*[:=]?\s*)\d{6,8}\b"),
     r"\1[redacted-code]"),
    # SSN, spelled or 9-digit ("ssn is NNNNNNNNN", "NNN-NN-NNNN") - real PII in the archive
    (re.compile(r"((?i:\bssn\b|social security(?:\s+number)?)(?:\s+(?:is|was|#))?\s*[:=]?\s*)\d{3}-?\d{2}-?\d{4}\b"),
     r"\1[redacted-ssn]"),
]


def redact(text):
    hit = False
    for rx, repl in _SECRETS:
        text, n = rx.subn(repl, text)
        hit = hit or n > 0
    return text, hit


def redact_tree(value):
    """Filter legacy JSON-derived views too; keep their structure and identifiers.

    This heuristic is defense in depth, not a guarantee that arbitrary private
    prose contains no secret. Call before truncation/chunking and at emission.
    """
    if isinstance(value, str):
        return redact(value)[0]
    if isinstance(value, list):
        return [redact_tree(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_tree(item) for key, item in value.items()}
    return value


# --- harness noise: task notifications / injected reminders are NOT owner prompts ---
# (live bug caught 2026-07-09: a background-task notification containing "7-19h old" restarted
# the fleet work clock at 19h; the same payload landed in this ledger classified "mistake".)
_NOISE_MARKERS = ("[SYSTEM NOTIFICATION - NOT USER INPUT]", "<task-notification>")
_SYSREM = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_CMD_STDOUT = re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.S)
_CMD_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_CMD_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)


def human_part(prompt):
    """Strip harness-injected blocks; return the owner's actual words ('' if none)."""
    # run_dream.ps1 prepends a mechanical time-budget header to dream sessions; that whole
    # line is machine-authored, not an owner prompt, so it never belongs in the ledger.
    if "mechanical header, injected by run_dream" in prompt:
        return ""
    for mk in _NOISE_MARKERS:
        if mk in prompt:
            # keep only text after the notification block, if any (arrives separately in practice)
            tail = prompt.split("</task-notification>")[-1] if "</task-notification>" in prompt else ""
            prompt = tail
    if "<command-name>" in prompt:
        name = _CMD_NAME.search(prompt)
        args = _CMD_ARGS.search(prompt)
        prompt = ((name.group(1).strip() if name else "") + " " +
                  (args.group(1).strip() if args else "")).strip()
    prompt = _SYSREM.sub("", prompt)
    prompt = _CMD_STDOUT.sub("", prompt)
    return prompt.strip()


def provisional_class(text):
    for name, rx in _RULES:
        if rx.search(text):
            return name
    return "other"


def append_line(rec):
    try:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        with io.open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # fail-open: archiving must never break a prompt


def status():
    counts = {}
    total = 0
    try:
        with io.open(LEDGER, encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                total += 1
                c = r.get("class_model") or r.get("class_provisional") or "other"
                counts[c] = counts.get(c, 0) + 1
    except Exception:
        pass
    parts = ", ".join("%s %d" % (k, counts[k]) for k in sorted(counts, key=counts.get, reverse=True))
    print("prompt archive: %d prompts (%s)" % (total, parts or "empty"))


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    argv = sys.argv[1:]
    runtime = "claude"
    # 2026-09-20: the Codex side never archived his prompts live - the ledger's last Codex row was a 07-06
    # backfill, so 1,103 of his Codex asks over eleven weeks were missing from the ledger and from every
    # hourly owner-prompts build. Codex's hooks.json wires this script as `prompt_archive.py --runtime codex`
    # (the shape task_checklist_hook.py uses); the row then carries source hook-codex so the views can tell
    # the runtimes apart. Origin (owner / automation) is stamped the same way for both runtimes.
    if len(argv) >= 2 and argv[0] == "--runtime":
        runtime = (argv[1].strip().lower() or "claude")
        argv = argv[2:]
    if argv:
        if argv[0] == "status":
            status()
        return
    if runtime == "claude" and os.environ.get("CODEX_THREAD_ID"):
        runtime = "codex"
    try:
        # brain_switch.gate() replaces sys.stdin after reading it; tolerate a restored stdin
        # with or without .buffer (a StringIO restore killed this hook silently 07-23..07-28 -
        # 5 days of the owner's prompts unarchived; mesh #289).
        buf = getattr(sys.stdin, "buffer", None)
        raw = buf.read() if buf is not None else sys.stdin.read().encode("utf-8", "surrogateescape")
        data = json.loads(raw.decode("utf-8", "strict") or "{}")
    except Exception:
        return
    if data.get("hook_event_name") != "UserPromptSubmit":
        return
    prompt = human_part((data.get("prompt") or "").strip())
    if not prompt:
        return
    # Belt and braces on cp1252 double-encoding - an em-dash arriving as three junk characters.
    # The cause was brain_switch.gate() doing a TEXT read of a BYTE protocol, and it is fixed
    # there (2026-08-22); this is the second line of defence for any other path that hands us
    # already-mangled text. 962 records carried the damage before the fix, and a mangled dash
    # splits one token into three that BM25 then scores against. Repair only when lossless.
    try:
        import mojibake
        prompt = mojibake.repair_deep(prompt)
    except Exception:
        pass
    text, redacted = redact(prompt)
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "session": (data.get("session_id") or data.get("thread_id") or "")[:12],
        "cwd": data.get("cwd") or "",
        "source": "hook" if runtime == "claude" else "hook-" + runtime,
        "class_provisional": provisional_class(text),
        "redacted": redacted,
        "text": text,
    }
    # Who authored it, and is it a re-fire? Without this the ledger cannot tell a prompt he typed
    # from the same prompt re-enqueued by a /loop cron job - and "read my last 100 prompts" was
    # reading about 40 real asks and 60 machine echoes (measured 2026-08-22; see prompt_origin.py).
    try:
        import prompt_origin
        prompt_origin.stamp(rec)
    except Exception:
        pass  # fail-open: archiving must never break a prompt
    append_line(rec)


if __name__ == "__main__":
    try:
        import os as _o, sys as _s
        _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
        import brain_switch as _bsw
        _bsw.gate()
    except SystemExit:
        raise
    except Exception:
        pass
    main()
