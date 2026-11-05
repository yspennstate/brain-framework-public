#!/usr/bin/env python3
"""error_monitor.py - PostToolUse hook: the felt 'that went wrong' (error-related negativity).

Gehring et al. 1993: the ERN is an anterior-cingulate signal that fires shortly after an error,
before conscious awareness, and drives post-error slowing. The instinct layer fires reflexes BEFORE
acting; this is the missing post-action half. It sniffs a tool's response for error signatures the
brain already knows are bad - a traceback, a non-zero exit, mojibake, a git conflict/lock, a leaked
secret - and injects a short 'that just felt wrong, slow down and check' before the next step, so a
broken result isn't silently built upon.

Precise signatures only (no bare 'error' - that over-fires), fail-safe silent, per-session+signature
dedup so a stream of the same failure doesn't nag. Reads stdin (PostToolUse JSON), writes injection JSON.
"""
import sys, os, io, json, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, ".ern_fired.json")
DEDUP = 120   # seconds; don't re-fire the same signature for this session that fast

# The ERN sniffs the tool RESPONSE, so it also fires on benign output that merely mentions a failure
# shape: an `echo` about a traceback, a `grep` FOR a pattern, a `cat` of a file with the word "corrupt".
# Those are false alarms - and a noisy alarm gets tuned out (and poisons outcomes.jsonl, which the
# conscience and ern_learn read as real). Suppress a hit when the command only DISPLAYS/searches text,
# or when the command itself contains the very trigger it "matched" (self-referential).
# ASYMMETRIC EXCEPTION: a possible-secret hit is NEVER suppressed - a missed leak is catastrophic, a
# false secret alarm is one cheap message. Better to cry wolf on secrets than ever miss one.
DISPLAY_CMD = re.compile(r"^\s*(sudo\s+)?(echo|printf|cat|type|head|tail|ls|dir|grep|rg|egrep|fgrep|"
                         r"findstr|select-string|awk|sed\s+-n|Get-Content|Write-Output|Write-Host|less|more)\b", re.I)
# The brain's own introspection output states signature words BY DESIGN (ERN_LEARNING.md's header,
# ern_learn's summary line, this hook's own injection quoted back in a transcript). Matching those
# echoes re-logs the REPORT of a failure as a new failure - a feedback loop caught live 2026-07-08:
# running ern_learn.py planted a fresh "corruption" outcome in the ledger it had just measured.
# Recognize our own machinery's phrasing and stay silent on everything but secrets.
SELF_ECHO = re.compile(r"ERN -> gut|that just felt wrong|ern_learn: \d+ signatures|false alarms \(the ERN")

SIGS = [
    ("a traceback (something threw)", r"Traceback \(most recent call last\)|Unhandled exception|\bpanic:"),
    ("a non-zero exit / hard failure", r"command failed|\bfatal:|FATAL|is not recognized as|No such file or directory|cannot find the path"),
    ("mojibake (an encoding got mangled)", r"Ã[©¨¤]|â|�"),
    ("a git conflict / lock / rejected push", r"index\.lock|CONFLICT \(|! \[rejected\]|non-fast-forward|Merge conflict|Automatic merge failed"),
    # RETIRED 2026-07-08: ("corruption", r"\bcorrupt(ed|ion)?\b") - measured against the full ledger:
    # all 12 pure hits were vocabulary echoes (sessions reading the brain's own files, which DISCUSS
    # corruption - this file, instincts gut-text, old ledger entries), zero unique real catches. Real
    # corruption is already covered: mojibake by its byte-pattern sig, git/fs corruption by the
    # co-printed 'fatal:' in the hard-failure sig. The word-sig's only product was a phantom x11
    # "uncovered reflex candidate" that the dream was repeatedly told to distill. Do not re-add a
    # bare word match; if a real gap appears, match the EVENT shape (e.g. 'fatal: .* corrupt').
    ("a possible LEAKED SECRET", r"sk-[A-Za-z0-9]{20}|ghp_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("a PowerShell parse error", r"ParserError|Unexpected token|missing (statement|closing|terminator)"),
    ("a SyntaxError", r"SyntaxError:|IndentationError:"),
    ("an implausible magnitude (numbers like this have flattered a sim before - reconcile against the lived book/tape, don't report it)",
     r"(?i:sharpe\D{0,12}([5-9]\.\d|[1-9]\d[.\s])|\b\d{3,}(\.\d+)?\s*%\s*(std|standard|vol)|hedg\w*\D{0,20}\d{2,}\s*(million|\bmn\b)|\b\d{3,}\s*%\s*(a month|monthly|return))"),
]


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
        resp = data.get("tool_response", "")
        if isinstance(resp, (dict, list)):
            resp = json.dumps(resp, ensure_ascii=False)
        resp = str(resp)
        if not resp:
            return
        cmd = ((data.get("tool_input", {}) or {}).get("command", "") or "")
        display = bool(DISPLAY_CMD.match(cmd)) if cmd else False
        echo = bool(SELF_ECHO.search(resp))
        # PDF text extraction is EXPECTED to yield odd glyphs (ligatures, math unicode) that match
        # the mojibake byte patterns - 3 of the first 4 hand-labeled false alarms in the snippet-era
        # ledger were paper-reading sessions extracting PDFs (measured 2026-07-09 00:15, n=13). A
        # genuinely mangled FILE write presents in the writing command, not the extraction.
        pdf_cmd = bool(re.search(r"pdf|fitz|pdftotext|pymupdf", cmd, re.I))
        # A secret SCANNER prints secret-shaped strings on purpose (its regex, its positive controls,
        # its findings). Measured 2026-09-02 over 14 days: all 5 LEAKED SECRET rows in the ledger were
        # scanners (PAT='...ghp_|AKIA[0-9A-Z]{16}...' commands echoing AKIAIOSFODNN7EXAMPLE and
        # xoxb-1111 controls). The tell is a secret prefix followed by regex construction in the COMMAND.
        scanner_cmd = bool(re.search(r"(ghp_|sk-|AKIA|xox\[?[abpr]|PRIVATE KEY)[^\n]{0,8}(\[|\{|\\|\||\)\|)", cmd))
        hits = []
        for label, pat in SIGS:
            if not re.search(pat, resp):
                continue
            is_secret = "SECRET" in label
            # keep secrets always; drop others when the output is our own machinery's echo, or the
            # command only shows text or names the trigger itself
            if not is_secret and (echo or (cmd and (display or re.search(pat, cmd)))):
                continue
            if is_secret and scanner_cmd:
                continue
            if label.startswith("mojibake") and pdf_cmd:
                continue
            hits.append(label)
        if not hits:
            return
        sess = data.get("session_id") or ""
        now = time.time()
        try:
            st = json.load(io.open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
        except Exception:
            st = {}
        st = {k: v for k, v in st.items() if now - v < 86400}
        fresh = []
        for h in hits:
            key = sess + "|" + h
            if now - st.get(key, 0) >= DEDUP:
                fresh.append(h)
                st[key] = now
        try:
            # atomic replace (2026-09-02): same lost-update race as the reflex hook's shared backoff file
            _tmp = STATE + ".%d.tmp" % os.getpid()
            with io.open(_tmp, "w", encoding="utf-8") as _f:
                json.dump(st, _f)
            os.replace(_tmp, STATE)
        except Exception:
            pass
        if not fresh:
            return
        # capture the outcome (a real failure) so the dream can close the loop: check whether a reflex
        # should have warned (instinct coverage) and feed prediction calibration. The felt error is data.
        # EXCEPT synthetic events from the humility_check gate ("_gate"): the canary must fire for the
        # gate to assert it, but a test trap is not a failure that happened - 21 gate runs had planted
        # 84 phantom entries that ern_learn/conscience then learned from as real (caught 2026-07-08).
        try:
            if not data.get("_gate"):
                # also keep a short RESPONSE snippet: without it the mentions-class (an echo header
                # whose output quotes a traceback) can never be tuned against ground truth - the
                # ledger stored only cmds, so real-vs-mention was unmeasurable (2026-07-08). Secrets
                # are masked before the snippet is at rest; everything else is already local-only.
                snip = re.sub(r"sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
                              r"|\b\d{8,10}:[A-Za-z0-9_-]{35}\b", "<masked>", resp)[:200]
                with io.open(os.path.join(HERE, "outcomes.jsonl"), "a", encoding="utf-8") as of:
                    of.write(json.dumps({"ts": time.time(), "sig": fresh, "tool": data.get("tool_name", ""),
                                         "session": sess,
                                         "cmd": (((data.get("tool_input", {}) or {}).get("command", "")) or "")[:160],
                                         "resp": snip}) + "\n")
        except Exception:
            pass
        ctx = ("[unconscious] that just felt wrong - the last result shows " + "; ".join(fresh) +
               ". Post-error slowing: stop, read what actually happened, and fix it before the next "
               "step - don't build on a broken result. (If a secret leaked, do NOT push it.)")
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                            "additionalContext": ctx}}))
    except Exception:
        return


if __name__ == "__main__":
    main()
