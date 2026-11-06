#!/usr/bin/env python3
"""instinct_prime.py - the priming hook: makes the gut fire on its own.

Registered on UserPromptSubmit (fires once when the user sets a task) and PreToolUse for
Bash/Edit/Write (fires the instant a session is about to DO a dangerous thing). It reads what
the session is about to do, matches it against the compiled reflexes in instincts.tsv, and, on
a hit, injects the matching instinct + the general danger-sense as context - so the session
"just knows" it's in scarred territory instead of having to remember to go read a memory. On no
match it prints nothing and exits 0: silent, no nag, no cost.

Fail-safe by construction: ANY error (missing tsv, bad json, bad regex) -> exit 0 with no
output, so a broken hook can never block or slow a session's work. This is the path every tool
call passes through; it must never be the thing that breaks.

Reads stdin (the hook JSON), writes the injection JSON to stdout. Run as:
  python instinct_prime.py
"""
import sys, os, io, json, re, time, hashlib
import atexit

_T0 = time.time()
# One hook call's identity (29 Sep, account02/B04 r76, B04-R27-F76-REFILL-FIRE-CLOCKS-NOT-COMPARABLE): the fire log and
# the refill A/B's assignment log carry it and _T0, formatted alike, so a refill joins its fire exactly. The two logs
# used to stamp int(now) and '%.0f' of a later now, 1000 against 1001 at 1000.75, and two calls in one second collided.
# Twelve hex digits of os.urandom: the 48 random bits uuid.uuid4().hex[:12] gave, without importing uuid (R24, 13 Oct).
_CALL_ID = os.urandom(6).hex()
_T0_S = "%.0f" % _T0


def opportunity_id(call_id, rid):
    """A refill opportunity: this call's id and the reflex. The same string in the refill log's column 5 and, rebuilt from
    the fire log's call id (column 7) and reflex (column 3), for the fire it became."""
    return "%s:%s" % (call_id, rid)


def _log_slow_call():
    """Slow-tail record (2026-09-02): one TSV line per call that took longer than INSTINCT_SLOW_MS (default
    2000) - ts, seconds, event, session - so a hook creeping toward its registered timeout shows up somewhere.
    A call killed by the runner at the timeout never reaches this handler; a call that came close does. Found
    the need when one control call hung for 60 s during a dream's post-steps and nothing had recorded it."""
    try:
        if _SLOW_EVENT.get("done"):
            return
        _SLOW_EVENT["done"] = True
        dt = time.time() - _T0
        if dt * 1000.0 < float(os.environ.get("INSTINCT_SLOW_MS", "2000")):
            return
        with io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "instinct_slow_calls.tsv"), "a",
                     encoding="utf-8") as f:
            f.write("%.0f\t%.2f\t%s\t%s\n" % (_T0, dt, _SLOW_EVENT.get("event", "?"), _SLOW_EVENT.get("session", "?")))
    except Exception:
        pass


_SLOW_EVENT = {}
atexit.register(_log_slow_call)

try:
    from append_journal import append_text as _append_text
except Exception:
    _append_text = None

HERE = os.path.dirname(os.path.abspath(__file__))
TSV = os.path.join(HERE, "instincts.tsv")
STATE = os.path.join(HERE, ".instinct_fired.json")
FIRELOG = os.path.join(HERE, "instinct_fires.tsv")   # durable: ts, event, act-proxy, reflex, snippet, session
GF_DIR = os.path.join(HERE, "graph_fire")            # the graph-derived firing engine (built 2026-07-11)
GF_ENABLED = os.path.join(GF_DIR, "ENABLED")         # present => graph fires augment live; absent => shadow only
# Carry reflexes are normally wake-only because their broad wording would nag on every turn. This one has
# a narrow, owner-correction trigger and belongs at task intake; completion/timed-work carries are handled at
# Stop, where the assistant's outgoing text actually exists.
AUTO_CARRY = frozenset(("restate_the_ask",))
# read-only / audit command shapes (a grep/cat/ls that MENTIONS scarred vocabulary is not an ACT on the
# guarded target). The frontier the 2026-07-09 dream named: a warning that fires on MENTIONS teaches its
# reader to click past it; this proxy lets a later pass measure the mention-vs-act false-fire rate that
# neither reflex_audit nor the dedup cache could see (both said the precision needs a durable fire-log).
_READONLY = re.compile(r"^\s*(grep|rg|cat|less|head|tail|ls|find|wc|git (log|status|diff|show|blame)|"
                       r"python[0-9.]* (-c )?[\w./\\]*(recall|status|audit|eval|check|_test|probe)|echo|"
                       r"type|Get-Content|Select-String|Get-ChildItem)\b", re.I)


def _act_proxy(event, data):
    """Coarse: is this a real ACT on a target, or a MENTION (a prompt, or a read-only/audit command)?"""
    if event == "UserPromptSubmit":
        return "prompt"                      # discussion, not yet an act - the act comes on a later tool call
    if event == "PreToolUse":
        tn = data.get("tool_name", "")
        if tn in ("Edit", "Write", "NotebookEdit"):
            return "write"                   # a real mutation
        cmd = ((data.get("tool_input", {}) or {}).get("command", "")) or ""
        return "read" if _READONLY.match(cmd) else "act"
    return "?"


def _log_fires(event, data, fired):
    """Append one row per fired reflex: what fired, on what surface, act-or-mention. Secret-masked,
    fully fail-safe (this is the every-tool-call path; a log write must never break it). Real uuid
    sessions only - a test fixture piping payloads at the hook must not pollute the measurement (the
    ern_reliability fixture-count lesson, 2026-07-09).

    Returns False when rows were due and were not written (29 Sep, scheduled audit account04/B04 r5,
    B04-R43-POST-EMIT-STATE-LOG-SPLIT): append_text reports a failed append by returning False, and that was
    dropped, so a fire that reached the session could vanish from every count without a trace."""
    try:
        if not re.match(r"^[0-9a-f]{8}-", data.get("session_id", "") or ""):
            return True
        proxy = _act_proxy(event, data)
        session = data.get("session_id", "") or ""
        _, txt = _text_from(data)
        # Sanitize BEFORE writing a TSV row: strip TAB (column delim), LF and CR (row delims). Missing the CR
        # was 01619e69's find - a Windows CRLF snippet (the run_dream.ps1 prompt header) left a bare 0x0D that
        # split 67 records under universal-newline readers. Strip all three so one fire is always one line.
        snip = re.sub(r"sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|rpa_[A-Za-z0-9]{20,}|"
                      r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----",
                      "<masked>", txt).replace("\t", " ").replace("\r", " ").replace("\n", " ")
        # 2026-09-22 (campaign_2026-09-22_recall F30): at 120 characters a session scratchpad path was ~95 of
        # them, so 11.7 pct of fires recorded a temp directory and nothing else and 23 pct of a random sample
        # could not be judged at all - the log, not the judge, was the limit on measuring this layer. Collapse
        # the scratchpad prefix and keep 300: still one line per fire, and the judge can see the action.
        snip = re.sub(r"[A-Za-z]:[\\/]Users[\\/][^\\/\s]+[\\/]AppData[\\/]Local[\\/]Temp[\\/]claude[\\/]"
                      r"[^\\/\s]+[\\/][^\\/\s]+[\\/]scratchpad", "<scratchpad>", snip)[:300]
        # One locked batch: rows from concurrent Claude/Codex hooks cannot interleave into fragments.
        rows = []
        for it in fired:
            # session_id appended LAST (backward-compat: old readers keep cols 0-4; it is the missing key
            # that lets the heeding instrument group fires by session). snip is sanitized -> delim-safe.
            # col 6 (07-16): firing MECHANISM - 'regex' | 'graph' | 'ff' (forward-feed). The
            # full-text audit found most ib_marks fires carry no regex match (graph-derived);
            # per-layer precision is unmeasurable without this attribution. Readers are
            # positional over cols 0-5 and tolerate the extra column.
            # col 7 (29 Sep): the call id, and col 0 is the call's start (_T0), as in the refill log (see _CALL_ID)
            rows.append("%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" %
                        (_T0_S, event, proxy, it["id"], snip, session,
                         it.get("_via", "regex"), _CALL_ID))
        if rows:
            return _append_text is not None and _append_text(FIRELOG, "".join(rows)) is not False
        return True
    except Exception:
        return False


EMIT_ERRORS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instinct_emit_errors.tsv")


def _note_emit_failure(parts, session, ids):
    """One row per hook call whose context went out but whose bookkeeping did not all land: which writes failed
    (fire_log, backoff, refills), for which session and reflexes. Read beside instinct_fires.tsv: a row here means
    that call's fires may be missing from the fire log or may re-fire before their window. Fail-safe."""
    try:
        with io.open(EMIT_ERRORS, "a", encoding="utf-8") as fh:
            fh.write("%s\t%s\t%s\t%s\t%s\n" % (_T0_S, session or "", ",".join(parts), ",".join(ids), _CALL_ID))
    except Exception:
        pass
DEDUP_SECS = 600   # a reflex fires once when you ENTER its territory, not on every step within
# ...but a FLAT 600s window means re-entering that territory all evening re-fires the same scar every
# ten minutes forever. Measured 2026-07-26 over 25 sessions and 4,522 tool calls: 8,077 bullet
# impressions carrying only 190 distinct scars - 42.5x repetition, a block on 88% of all tool calls,
# the loudest single scar shown 292 times across 23 sessions. That is not priming, it is wallpaper:
# the twentieth showing of a scar teaches nothing the first did and costs the attention the first
# one earned. So the window GROWS with how many times this session has already seen it.
#
#   fire 1 immediately | 2 after 10m | 3 after 40m | 4 after ~2.7h | then capped at DEDUP_MAX
#
# Nothing is removed from the corpus and no FIRST fire is ever delayed - only the tail is thinned,
# which is where the repetition lives (the top 20 of 190 scars are half of all impressions). The
# mindware-override escalation in _render gets sharper as a side effect: reaching count 3 now means
# three genuinely separated encounters, not three ticks of the same ten-minute clock.
DEDUP_BACKOFF = 4.0     # window multiplier per prior fire, this session
DEDUP_MAX = 8 * 3600    # ceiling, so a long session still re-primes occasionally
# 2026-09-02 (design goal: fire less, and be more confident when it fires): measured over the
# previous 24h of interactive sessions, 7,036 fires over 1,456 (session, reflex) pairs - 4.8 per pair, 79%
# of all fires a repeat of a reflex that session had already seen. Two causes. (1) The backoff state was ONE
# shared JSON truncated and re-read by 21 concurrent sessions: a reader hitting a half-written file got {},
# wrote back its own key, and the whole fleet's counts collapsed (57 entries on disk against 1,456 live
# pairs), so most reflexes never got past count 1-2 and re-fired on the flat 10-minute clock. The state is
# now per session (.instinct_fired/<session>.json, atomic replace) so no session can erase another's
# memory of what it has seen. (2) The first window was 10 minutes; it is now 30 (fire 1 immediately |
# 2 after 30m | 3 after 2h | then every 8h). Replayed on that day's log the new windows keep 43% of the
# fires the old ones would have; the first fire on entering a territory is still never delayed.
DEDUP_SECS = 1800
STATE_DIR = os.path.join(HERE, ".instinct_fired")


def _window(cnt):
    """Seconds a reflex stays quiet after it has already fired `cnt` times this session."""
    if cnt <= 0:
        return 0.0
    try:
        return min(DEDUP_SECS * (DEDUP_BACKOFF ** (cnt - 1)), float(DEDUP_MAX))
    except Exception:
        return float(DEDUP_SECS)
DANGER = ("Something here resembles a place we've been burned, even if you can't name it. Treat that "
          "as data: slow down, distrust the confident read, and check the brain (grep the memory dir "
          "or read the linked scar) and the lived numbers before you commit.")
MAX_FIRE = 2   # never dump the whole list; the top two most-relevant reflexes only (owner, 2026-09-02 09:1x:
               # "make the brain fire less, and be more confident when it fires" - was 3)
PRECISION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reflex_precision.json")
ESC_MIN_P = 0.6     # the REPEATED escalation is earned by a judged record at or above this (see _escalation_earned)
GUIDE_MIN_P = 0.25   # a recognition cue (mode guide) judged on-domain below this, at >= 4 judged fires, stays silent


_PREC_MEMO = {}


def _load_precision():
    """{reflex_id: {"n", "on_domain", "p", "judged"}} from the nightly reflex_precision.py build over
    judged_nightly.jsonl (the target stratum since 07-16, the firehose stratum since 09-02). Fail-open: no
    file, or a corrupt one, means every reflex is treated as unjudged and fires as before.

    Read once per hook call (29 Sep, scheduled audit account04/B16 r11, B16-R14-REFILL-COST-AND-MISSINGNESS-001):
    the refill asked for it once per ranked candidate, re-parsing the 60 KB table each time. Kept by the file's
    path, mtime and size, so a table replaced during the call, or a test's fixture, is read again."""
    try:
        st = os.stat(PRECISION_PATH)
        key = (PRECISION_PATH, st.st_mtime_ns, st.st_size)
        if key not in _PREC_MEMO:
            with io.open(PRECISION_PATH, encoding="utf-8") as f:
                d = json.load(f)
            _PREC_MEMO.clear()
            why = _precision_invalid(d)
            _PREC_MEMO[key] = {} if why else (d.get("reflexes") or {})
            if why:
                _note_precision_gate(why)
        return _PREC_MEMO[key]
    except Exception:
        return {}


PRECISION_MAX_AGE_S = 7 * 86400.0
PRECISION_GATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reflex_precision.gate.json")
_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _stamp_seconds(s):
    """time.mktime(time.strptime(s, "%Y-%m-%dT%H:%M:%S")) without importing _strptime, which costs 15-19 ms in a fresh
    process and was paid on every hook call for the table's build stamp (R24, 13 Oct 2026). Only a stamp of exactly that
    form, ASCII digits and a real calendar date with seconds below 60, is converted directly; anything else (a lowercase
    t, a short or non-ASCII stamp, seconds 60 or 61, an impossible date) goes through strptime as before, so every
    result and every error is the one strptime gave."""
    if len(s) == 19 and s[4] == "-" and s[7] == "-" and s[10] == "T" and s[13] == ":" and s[16] == ":":
        dg = s[0:4] + s[5:7] + s[8:10] + s[11:13] + s[14:16] + s[17:19]
        if dg.isascii() and dg.isdigit():
            y, mo, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
            h, mi, se = int(s[11:13]), int(s[14:16]), int(s[17:19])
            if y >= 1 and 1 <= mo <= 12 and h <= 23 and mi <= 59 and se <= 59:
                leap = mo == 2 and y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
                if 1 <= d <= _DAYS_IN_MONTH[mo - 1] + leap:
                    return time.mktime((y, mo, d, h, mi, se, 0, 1, -1))
    return time.mktime(time.strptime(s, "%Y-%m-%dT%H:%M:%S"))


def _precision_invalid(d, now=None):
    """Why the precision table may not steer ranking, or None (29 Sep, account04/B04 r7,
    B04-R43-FRESHNESS-WATCH-NOT-GATE: freshness was only a liveness warning, and a stale table kept steering). A table
    with no readable build stamp, one stamped in the future, or one built more than seven days ago is ignored: every
    reflex is then unjudged and fires as it would with no table. Between 36 hours (when hook_liveness warns) and seven
    days the table still steers: most of what it does is keep low-precision cues silent, and his standing order is to
    fire less and surer, so one missed nightly judge must not turn every reflex back to full charge."""
    try:
        t = _stamp_seconds(str(d.get("built"))[:19])
    except Exception:
        return "no readable build stamp (%r)" % (d.get("built"),)
    now = time.time() if now is None else now
    if t > now + 300:
        return "built in the future (%s)" % d.get("built")
    if now - t > PRECISION_MAX_AGE_S:
        return "built %s, %.1f days ago" % (d.get("built"), (now - t) / 86400.0)
    return None


def _note_precision_gate(why):
    """The reason, machine-readable, beside the table; rewritten at most hourly. Fail-safe."""
    try:
        if os.path.exists(PRECISION_GATE) and time.time() - os.path.getmtime(PRECISION_GATE) < 3600:
            return
        tmp = PRECISION_GATE + ".%d.tmp" % os.getpid()
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "ignored": True, "reason": why}, fh)
        os.replace(tmp, PRECISION_GATE)
    except Exception:
        pass


def _precision_factor(rid, prec):
    """Charge multiplier from judged precision: 0.6 + 0.8 * p (p 0.25 -> 0.8, p 0.5 -> 1.0, p 0.75 -> 1.2), so
    a reflex that was on-domain when judged wins the spotlight over one that mostly was not. Unjudged (fewer
    than 4 judged fires) and the never-damped safety scars keep their charge."""
    if rid in _NEVER_DAMP:
        return 1.0
    v = prec.get(rid)
    if not v or not v.get("judged"):
        return 1.0
    return 0.6 + 0.8 * float(v.get("p", 0.5))


_ROUTE_WORDS = {"graph": "through the graph", "ff": "through the forward feed (a command like one that just failed)"}


def _hint(rid, prec, via=None):
    v = prec.get(rid)
    # F159 (scheduled audit account01/B04, 27 Sep): a graph or forward-feed fire quotes ITS route's judged record
    # when there is one, and names the route; the pooled record is mostly regex fires and says little about it.
    r = ((v or {}).get("paths") or {}).get(via) if via in _ROUTE_WORDS else None
    if r and r.get("judged"):
        return "hint: judged on-domain %d of %d times it fired %s - a fire is a question, not a verdict" % (
            int(r.get("on_domain", 0)), int(r.get("n", 0)), _ROUTE_WORDS[via])
    if v and v.get("judged"):
        return "hint: judged on-domain %d of %d times it fired - a fire is a question, not a verdict" % (
            int(v.get("on_domain", 0)), int(v.get("n", 0)))
    return "hint: unjudged - a fire is a question, not a verdict; this may or may not apply here"
MATCH_CAP = 16384  # hot-path: cap the matchable text before the regexes + graph tokenization run on it. A
                   # command-surface mistake (backslash path, dangerous cmd, sim-flatter wording) is front-
                   # loaded; the tail of a huge Write/heredoc adds ~no detection but cost is LINEAR (measured:
                   # 5MB tool_input -> 14s on the every-tool-call path). 32KB kept latency ~0.19s when the corpus
                   # was 58 regexes; at 285 (2026-09-02), several with bounded lookaheads, a 32KB Write cost 3.0s
                   # and a 32KB Edit 4.5s on the live hook against an 8s runner timeout - two triggers with an
                   # unbounded scan had hung it outright and were bounded the same day. 16KB halves the cost.


def _ranking_doc():
    """IDF ranking (reflex_ranking.json, built by 01619e69, refreshed nightly) that breaks the
    charge-saturation tie: charge is 1.00 for 37/45 reflexes, so it can't rank co-firing reflexes -
    surface the RARE/specific one (high weight) over the always-on generic (low). Measured: 25% fewer
    generics in the top-3 on real actions, no named-case regression. Since 2026-07-16 the same file
    also carries context_damp: {id: charge-damp} for DOMAIN-INVERTED trading-topical reflexes -
    measured to fire 2.2-2.7x over exposure on the brain's own surfaces (whose files quote their
    trigger vocabulary) and at/below exposure on the trading domain they exist for. The damp applies
    ONLY when the acted-on surface is self-referential (fire_surface.py): damping, never deletion -
    the reflex still fires everywhere else at full strength, and still fires here when it wins top-3
    among what matched. Fail-safe: missing/bad file -> {} for both (exact prior behaviour)."""
    try:
        d = json.load(io.open(os.path.join(HERE, "reflex_ranking.json"), encoding="utf-8"))
        return d.get("surface_weight", {}) or {}, d.get("context_damp", {}) or {}
    except Exception:
        return {}, {}


SURFACE_WT, CONTEXT_DAMP = _ranking_doc()
# belt-and-braces mirror of the builder's NEVER_DAMP: even a hand-edited ranking file must
# Command-hazard ('act') scars that must fire even on a mention, a read-only command or a prose write:
# the catastrophic ones, plus the archive lister whose read IS the billed act (owner law 2026-08-25), plus the
# backslash-path check, which since 2026-09-25 fires only where the path breaks THIS command: a read-only `ls`,
# `test -f` or python analysis heredoc fails just the same (of the week's fires this filter dropped, 99 of 100 were
# on commands that then failed).
_MENTION_NEVER = frozenset(("no_local_privileged_stack", "secrets_in_git", "preserve_before_destroy",
                            "pinned_file_two_tripwires", "his_never_is_machinery", "gate_tool_needs_env",
                            "his_desktop_is_not_an_automation_surface", "healer_first_do_no_harm",
                            "google_upload_is_not_free", "windows_backslash_path"))
# Prose targets: a Write/Edit here cannot run a command, so act-surface scars stay silent (2026-09-02).
_PROSE_EXT = frozenset((".md", ".markdown", ".txt", ".tex", ".rst", ".bib", ".html", ".htm"))
# not damp a safety scar (the builder already excludes these at build time)
_NEVER_DAMP = frozenset(("no_local_privileged_stack", "secrets_in_git", "preserve_before_destroy",
                         "pinned_file_two_tripwires", "his_never_is_machinery",
                         "gate_tool_needs_env", "his_desktop_is_not_an_automation_surface",
                         "healer_first_do_no_harm", "windowless", "lookahead"))
# "windowless" is the compiled id of the flashing-window law's reflex (source no_flashing_terminal_windows_2026_06_07).
# Until 30 Sep this set named "no_flashing_windows", which no reflex has ever carried, so that law's reflex was never
# protected: precision ranked it down (0.90) and the cap could drop it like any other (the 1000-page report's D03-T05).


def _damp_for(event, data, text):
    """{id: damp} when this tool call lands on a self-referential surface, else {}.
    Prompts and non-PreToolUse events never damp (a user prompt ABOUT sims is in-domain)."""
    try:
        if not CONTEXT_DAMP or event != "PreToolUse":
            return {}
        import fire_surface
        proxy = "write" if data.get("tool_name", "") in ("Edit", "Write", "NotebookEdit") else "act"
        if fire_surface.is_self_referential(proxy, text[:fire_surface.HEAD]):
            return {k: v for k, v in CONTEXT_DAMP.items() if k not in _NEVER_DAMP}
        return {}
    except Exception:
        return {}


# Reflexes about one Windows machine (its GPU, its drivers). Anywhere else they only nag: on the Mac mini,
# heavy_compute_remote fired 30 times in the day to 30 Oct 07:50 and was judged off-domain on 14 of 14. Left out at
# load, so neither the regex nor the graph path can fire them. bare_open_reads_cp1252 stays on the Mac on purpose:
# code written here for brain-framework runs on Windows too.
WINDOWS_ONLY = frozenset(("heavy_compute_remote",))
# Code files whose code also runs on Windows; off Windows, bare_open_reads_cp1252 fires only on a call that names one.
# A bare repo name was not enough (30 Oct 12:5x): of 8 Mac fires it let through, 6 only read data there (the checklist
# state.json, a gh api call, the ~/.brain-framework state folder) and none wrote code that runs on Windows.
_SHIPS_TO_WINDOWS = re.compile(r"(?:brain-framework|ai-memories-and-functionality|brain-repo|vanilla-brain)/[^\s'\"]*"
                               r"\.(?:py|ps1|psm1|vbs)\b")
_SHIP_GATED = ("bare_open_reads_cp1252", "python_rewrite_of_a_text_file_without_newline")
# Naming a shipped code file is not writing one (31 Oct, 1000-ways D5 #79): `python3 ~/brain/brain-framework/mesh/
# agent_mesh.py ...` names one, and nearly every mesh command does. Replayed over all 111 Mac fires of the two reflexes:
# naming kept 19, judged on-domain 8 of 8 and off-domain 11 of 90; writing (an Edit/Write target, or the file after a
# redirect, sed -i, a cp/mv/tee destination, or open(..., 'w'/'a') / write_text in inline python) kept 7, on-domain
# 8 of 8, off-domain 0 of 90. All 8 on-domain fires were harness probes writing zz_probe.py; no real Mac write of
# shipped code with a bare open() was judged yet. ~/brain/porting/w08_checks/d5_79_shipgate.py.
_SHIPS_WRITE = re.compile(
    r"(?:>>?\s*['\"]?\S*%(p)s)|(?:sed\s+-i[^\n|;&]*?%(p)s)"
    r"|(?:\b(?:cp|mv|tee|install|rsync)\b[^\n|;&]*?%(p)s\s*(?:$|[;&|\n]))"
    r"|(?:open\([^)]*%(p)s[^)]*,\s*['\"][wa])|(?:%(p)s[^\n]{0,80}\.write_text)" % {"p": _SHIPS_TO_WINDOWS.pattern}, re.S)


def _writes_shipped_code(tool_input):
    """True when this call writes a code file that also runs on Windows (see _SHIPS_WRITE). Fail-open: True on error."""
    try:
        fp = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if fp:
            return bool(_SHIPS_TO_WINDOWS.search(fp))
        return bool(_SHIPS_WRITE.search(str(tool_input.get("command") or "")))
    except Exception:
        return True
# Reflexes about how to answer HIS question: shown only on UserPromptSubmit. duration_question_needs_a_rate fired on
# tool calls in 29 of 32 Mac fires (28-30 Oct: an agent's own "still running"/"keep alive" in a command), judged
# on-domain 0 of 18 there; its compiled controls are all his sentences.
_PROMPT_ONLY = frozenset(("duration_question_needs_a_rate",))


def _load():
    out = []
    with io.open(TSV, "r", encoding="utf-8") as f:
        next(f, None)
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if os.name != "nt" and p[0] in WINDOWS_ONLY:
                continue
            if len(p) >= 7 and (p[1] in ("fire", "guide") or p[0] in AUTO_CARRY):
                try:
                    charge = float(p[7]) if len(p) >= 8 else 0.6   # graded gut; older 7-col tsv -> mid
                except Exception:
                    charge = 0.6
                mode = "fire" if p[0] in AUTO_CARRY else p[1]
                out.append({"id": p[0], "mode": mode, "validity": p[2], "trigger": p[3],
                            "gut": p[4], "sting": p[5], "source": p[6], "charge": charge})
    return out


def _text_from(data):
    """What is the session about to do? Pull the matchable text for this event."""
    ev = data.get("hook_event_name", "")
    if ev == "UserPromptSubmit":
        return ev, data.get("prompt", "") or ""
    if ev == "PreToolUse":
        ti = data.get("tool_input", {}) or {}
        # serialise the whole tool input: catches Bash `command`, Edit/Write `file_path`+content, etc.
        try:
            return ev, json.dumps(ti, ensure_ascii=False)
        except Exception:
            return ev, str(ti)
    # unknown event: match nothing
    return ev, ""


# 6 Nov 2026: exact_clauses.render is a pure function of the prompt text, but importing it compiles 38 regexes, 14 of
# this hook's ~35 ms on a cold prompt. Loop prompts repeat, so the rendered line is kept per sha256(prompt), with the
# stat of exact_clauses.py as the cache's identity (an edit there drops it). Stored only for a uuid session.
# EXACT_CLAUSES_CACHE=0 turns it off (or names another file).
_EC_CACHE = os.environ.get("EXACT_CLAUSES_CACHE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "exact_clauses_render_cache.json")
_EC_KEEP = 400
_EC_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _ec_ident():
    try:
        st = os.stat(os.path.join(os.path.dirname(os.path.abspath(__file__)), "exact_clauses.py"))
        return [st.st_mtime_ns, st.st_size, st.st_ino]
    except OSError:
        return None


def _exact_clause_context(event, text):
    """Intake-only exactness advisory, kept outside `fired` so no owner clause reaches FIRELOG."""
    if event != "UserPromptSubmit":
        return ""
    use = _EC_CACHE != "0"
    ident = _ec_ident() if use else None
    key = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest() if ident else None
    data = None
    if key:
        try:
            with io.open(_EC_CACHE, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("ident") != ident:
                data = None
            elif key in data.get("v", {}):
                return data["v"][key]
        except Exception:
            data = None
    try:
        import exact_clauses
        out = exact_clauses.render(text)
    except Exception:
        return ""
    if key and _EC_UUID.match(str(_SLOW_EVENT.get("session") or "")) and _ec_ident() == ident:
        try:
            v = dict((data or {}).get("v", {}))
            v[key] = out
            if len(v) > _EC_KEEP:
                v = dict(list(v.items())[-_EC_KEEP:])
            tmp = "%s.%d.tmp" % (_EC_CACHE, os.getpid())
            with io.open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"ident": ident, "v": v}, fh, separators=(",", ":"))
            os.replace(tmp, _EC_CACHE)
        except Exception:
            pass
    return out


def _firelog_data(event, data, exact_ctx):
    """Hide owner prose when an exact-clause prompt co-fires an ordinary reflex."""
    if event != "UserPromptSubmit" or not exact_ctx:
        return data
    safe = dict(data)
    safe["prompt"] = "<exact-owner-clause-prompt-redacted>"
    return safe


def _score_gate():
    """graph_fire/SCORE_GATE.json -> (threshold, penalty, anchor_tokens) or None.
    DORMANT BY VERDICT (07-16 replay): on the regex lane this gate targets, the anchor
    score is CONFOUNDED - self-referential text that quotes trigger vocabulary out-scores
    genuinely justified fires (unjustified 0.63-6.94 vs justified 1.20-3.31 on the 13
    replayable labeled cases; a sim memo hit 6.94). The graph-lane separation (AUC 0.69,
    5/5 kept at 0.5) does NOT transfer. Do not create the gate file until a per-reflex
    calibration on n>=50 labeled cases (accruing nightly via judged_nightly + the via
    column) separates embodiment from vocabulary. Fail-safe: no file -> None -> exact
    prior behaviour."""
    try:
        g = json.load(io.open(os.path.join(GF_DIR, "SCORE_GATE.json"), encoding="utf-8"))
        if not g.get("enabled"):
            return None
        cal = json.load(io.open(os.path.join(GF_DIR, "reflex_anchors_cal.json"), encoding="utf-8"))
        toks = {rid: (m.get("tokens") or {}) for rid, m in (cal.get("reflexes") or {}).items()}
        return float(g.get("threshold", 0.5)), float(g.get("penalty", 0.25)), toks
    except Exception:
        return None


_SCORE_GATE = _score_gate()
_WORD_RX = re.compile(r"[a-z][a-z0-9_]{2,}")


SUPPRESSED_LOG = os.path.join(HERE, "instinct_suppressed.tsv")
_SUPPRESSED = []   # (reflex id, reason) dropped on THIS call by a gate - logged so the judge can audit the gates
_LAST_RANKED = []  # every regex hit of THIS call in rank order (before the cap) - main() refills from it after
                   # the mention/prose filters, so a filtered act-scar cannot cost a claim reflex its slot


def _suppress(rid, reason):
    """Record a reflex a gate silenced on this call (precision / cap / mention / prose / backoff). The
    2026-09-02 order made the hook fire less; this log is the falsifier - a nightly sample of suppressed
    fires judged on-domain says whether a gate is eating fires it should not. Never affects behaviour."""
    try:
        _SUPPRESSED.append((str(rid), str(reason)))
    except Exception:
        pass


def _log_suppressed(event, data):
    """One row per suppressed reflex: ts, event, proxy, reflex, reason, session. Real uuid sessions only
    (like _log_fires); fail-safe on the every-tool-call path. This file is the show-or-hold trial's outcome log,
    so a write that fails or that the journal refuses (append_text returns False) is recorded in EMIT_ERRORS as
    'suppressed_log': without that row a lost suppression would read as no repeat (dot D7CB6565, round 6, path 1)."""
    try:
        if not _SUPPRESSED:
            return
        session = data.get("session_id", "") or ""
        if not re.match(r"^[0-9a-f]{8}-", session):
            return
    except Exception:
        return
    try:
        proxy = _act_proxy(event, data)
        now = "%.0f" % time.time()
        payload = "".join("\t".join((now, event, proxy, rid, reason, session)) + "\n" for rid, reason in _SUPPRESSED)
        # through the same per-file lock the fire log uses, so the rotation (rotate_logs.py) can hold it
        # while it rewrites this file too; the plain append stays as the fallback if the journal is absent
        if _append_text is not None:
            if _append_text(SUPPRESSED_LOG, payload) is False:
                raise IOError("the journal refused the append")
        else:
            with io.open(SUPPRESSED_LOG, "a", encoding="utf-8") as f:
                f.write(payload)
    except Exception:
        try:
            ids = [str(x[0]) for x in _SUPPRESSED]
        except Exception:
            ids = []
        _note_emit_failure(["suppressed_log"], session, ids)


def _gated_by_precision(rec, prec=None):
    """True when a recognition cue (mode guide) was judged off-domain on most of its fires (>= MIN_N judged
    rows, smoothed precision below GUIDE_MIN_P): it stays silent on every path - regex and graph alike.
    Danger scars (mode fire) are ranked down by precision, never silenced. Fail-open: any error -> False."""
    try:
        prec = _load_precision() if prec is None else prec
        v = prec.get(rec.get("id"))
        return bool(rec.get("mode") == "guide" and v and v.get("judged")
                    and float(v.get("p", 1.0)) < GUIDE_MIN_P)
    except Exception:
        return False


# THE GRAPH LANE'S OWN RECORD (2026-09-23, round 6; REFLEX_GRAPH_LANE_PRIOR, default on). _graph_gated below judges a
# graph-path fire by its reflex's OWN graph record, which exists only at 4 or more judged graph fires - and almost no
# reflex has that many. Measured over the 7 days to 09-23 14:3x: 1,105 delivered graph fires, 995 of them from reflexes
# with no judged graph record, so the gate never saw them; the lane as a whole was judged on-domain 7 times in 70 (the
# nightly judge plus the 3-judge random sample of 09-22), against 130 of 248 on the regex path. So a reflex without its
# own graph record is judged by the lane's pooled record, smoothed (on+1)/(n+2), against the same GUIDE_MIN_P - and
# one in GRAPH_LANE_EXPLORE of those fires still surfaces, drawn by hash of reflex, session and hour, so the lane keeps
# earning judged evidence and a reflex can win its graph path back with a record of its own. The never-damped safety
# scars are exempt; the regex path is untouched.
GRAPH_LANE_PRIOR = os.environ.get("REFLEX_GRAPH_LANE_PRIOR", "1").strip() not in ("0", "off", "false", "")
GRAPH_LANE_MIN_N = 30
GRAPH_LANE_EXPLORE = 8


def _graph_lane(prec):
    """(judged n, on-domain) pooled over every reflex's graph-path record."""
    n = on = 0
    for v in (prec or {}).values():
        g = ((v or {}).get("paths") or {}).get("graph") or {}
        n += int(g.get("n", 0) or 0)
        on += int(g.get("on_domain", 0) or 0)
    return n, on


def _graph_lane_gated(rid, prec=None, explore_key=""):
    """True when a graph-path fire of a reflex WITHOUT its own judged graph record should stay silent because the
    lane's pooled record is below GUIDE_MIN_P - except one draw in GRAPH_LANE_EXPLORE. Fail-open: any error -> False."""
    try:
        if not GRAPH_LANE_PRIOR or rid in _NEVER_DAMP:
            return False
        prec = _load_precision() if prec is None else prec
        own = ((prec.get(rid) or {}).get("paths") or {}).get("graph")
        if own and own.get("judged"):
            return False   # its own record decides (_graph_gated)
        n, on = _graph_lane(prec)
        if n < GRAPH_LANE_MIN_N or (on + 1.0) / (n + 2.0) >= GUIDE_MIN_P:
            return False
        import zlib
        return zlib.crc32(("%s|%s" % (rid, explore_key)).encode("utf-8", "replace")) % GRAPH_LANE_EXPLORE != 0
    except Exception:
        return False


def _graph_gated(rid, prec=None):
    """A graph-path fire says 'this is graph-near a scar' - an association, not the scar's own trigger -
    so it is a cue whatever the reflex's mode, and it is gated by ITS OWN judged precision
    (reflex_precision.json 'paths': {'graph': {...}}, judged at n >= 4): below GUIDE_MIN_P it stays
    silent. The regex path (the trigger, the referent) is untouched by this gate. Measured 2026-09-02:
    fixed_sides_only fired 7 times in a morning, every one through the graph, none on-domain."""
    try:
        prec = _load_precision() if prec is None else prec
        v = ((prec.get(rid) or {}).get("paths") or {}).get("graph")
        return bool(v and v.get("judged") and float(v.get("p", 1.0)) < GUIDE_MIN_P)
    except Exception:
        return False


def _escalation_earned(rid, prec):
    """May a reflex that has fired 3+ times this session be escalated ('do NOT click past this one')?
    Only when its judged record earns it, or it is a never-damped safety scar. Measured 2026-09-23 on 429
    judged fires placed by their order within the session: the third-and-later fires of the charge >= 0.95
    reflexes (the escalated set) were on-domain 23.4 pct (n=141) against 33.0 pct for first fires (n=91),
    off-domain 54.6 against 41.8 - a repeat is more often a trigger matching the session's own vocabulary
    than a real signal read past. Leave-one-out, requiring p >= ESC_MIN_P on >= 4 judged fires kept 17 of 141
    escalations at 41.2 pct on-domain against 21.0 pct for the 124 it drops. The fire itself is untouched;
    only the escalation line is withheld. INSTINCT_ESC_GATE=0 restores the old rule. Fail-open to the old
    rule on any error."""
    try:
        if os.environ.get("INSTINCT_ESC_GATE", "1") == "0" or rid in _NEVER_DAMP:
            return True
        v = (prec or {}).get(rid) or {}
        return bool(v.get("judged")) and float(v.get("p", 0.0)) >= ESC_MIN_P
    except Exception:
        return True


def _anchor_table():
    """sha1(trigger) -> required-literal any-of list, from instinct_anchors.json; {} = no pre-filter (fail-open).
    Measured 2026-09-14 02:2x: 0.78 of a 0.85 s hook call was re.compile of the ~310 triggers in a fresh process;
    a trigger whose required literal is absent from the text cannot match and is not compiled at all.
    INSTINCT_ANCHORS=0 disables the pre-filter for a comparison."""
    if os.environ.get("INSTINCT_ANCHORS", "1") == "0":
        return {}
    try:
        import instinct_anchors
        return instinct_anchors.load()
    except Exception:
        return {}


# SOURCE SUPERSESSION (2026-09-22, brain study F57). A reflex is compiled from a memory once and is never re-read
# when that memory is overtaken: on 2026-09-22 the `heavy_compute_remote` reflex fired "utilisation UNCAPPED since
# the owner's 2026-09-09 order" as a REPEATED x5 high-stakes instinct four days after his 09-18 stop, because its
# source node had acquired `superseded_by` and the reflex text had not moved (Agent E, current-rule bank). So at
# fire time each reflex's source memory is checked for a `superseded_by` field (its own frontmatter, chain followed
# to the terminal memory); a superseded source halves the reflex's charge in the spotlight contest and the render
# says which memory overtook it. Fail-open: any error reads as "not superseded". INSTINCT_SUPERSEDED_CHECK=0 disables.
_SUPERSEDED_CACHE = {}
# BRAIN_MEM_DIR (2026-09-22, round 5 W4): the same corpus seam brain.py and bm25.py honour, so a fixture corpus can
# drive this check; unset, the live corpus as before.
_MEMORY_DIR_FOR_SUPERSESSION = os.environ.get("BRAIN_MEM_DIR") or os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
_PART_CACHE = {}


def _source_supersession(slug):
    """The terminal memory that overtook `slug`, or None when the slug is current or unreadable."""
    if not slug or os.environ.get("INSTINCT_SUPERSEDED_CHECK", "1") == "0":
        return None
    if slug in _SUPERSEDED_CACHE:
        return _SUPERSEDED_CACHE[slug]
    term, cur, hops = None, slug, 0
    try:
        while hops < 5:
            p = os.path.join(_MEMORY_DIR_FOR_SUPERSESSION, cur + ".md")
            if not os.path.isfile(p):
                break
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(4096)
            m = re.search(r"^\s*superseded_by:\s*([A-Za-z0-9_\-]+)", head, re.M)
            if not m or m.group(1) == cur:
                break
            term, cur, hops = m.group(1), m.group(1), hops + 1
    except Exception:
        term = None
    _SUPERSEDED_CACHE[slug] = term
    return term


# PART SUPERSESSION (2026-09-22, round 5 W4). A source memory that declares only one clause of itself overtaken
# (`superseded_in_part: ["<clause> -> <slug>"]`, or the older `partially_superseded_by: <slug> # <part>`) is still
# the source of a live rule for the rest, so its reflex keeps its charge; the render adds one line naming the clause
# that moved and where, and asks the reader to check the gut line is not that clause. Read from the closed
# frontmatter block only, cached per process, fail-open to "not part-superseded". INSTINCT_SUPERSEDED_CHECK=0 or
# BRAIN_PART_SUPERSESSION=0 disables it.
def _source_part_supersession(slug):
    """[(clause, successor)] the reflex's source memory declares partly overtaken; [] when none or unreadable."""
    if (not slug or os.environ.get("INSTINCT_SUPERSEDED_CHECK", "1") == "0"
            or os.environ.get("BRAIN_PART_SUPERSESSION", "1") == "0"):
        return []
    if slug in _PART_CACHE:
        return _PART_CACHE[slug]
    items = []
    try:
        p = os.path.join(_MEMORY_DIR_FOR_SUPERSESSION, slug + ".md")
        if os.path.isfile(p):
            import supersession as _sup
            items = _sup.part_supersessions(_sup.frontmatter_block(p), _MEMORY_DIR_FOR_SUPERSESSION)
    except Exception:
        items = []
    _PART_CACHE[slug] = items
    return items


# Kept trigger programs (R24, 13 Oct 2026). A hook call is a fresh process, so re.findall compiled every trigger that
# passed the anchor prefilter from scratch on every call: 27-47 triggers and 44-77 ms, the largest part of this hook's
# run (profile_prime_1: 0.243 s of a cold 0.352 s, under load). What the regex compiler makes of a trigger - its flags,
# its program as uint32 words, its group count and names - is kept in __pycache__/instinct_rx.<hexversion>.marshal, and
# a later call rebuilds the Pattern from them with _sre.compile, the step re.compile ends with (0.02 ms against 0.75).
# An entry is kept only when the Pattern rebuilt from it equals re.compile's (flags, program and pattern text) and has
# the same groups and group names; each entry carries a CRC of its fields, and a mismatch, another Python's stamp or an
# unreadable file means the trigger is compiled as before. A trigger the compiler refuses raises exactly as it did.
# INSTINCT_RX_CACHE=0 turns this off; INSTINCT_RX_FILE puts the kept file somewhere else (the controls use it).
try:
    import marshal, zlib, _sre
    from array import array as _array
    from re import _compiler as _re_compiler, _parser as _re_parser
    _RX_STAMP = (1, sys.hexversion, _sre.MAGIC, _sre.CODESIZE)
    _RX_ON = _array("I").itemsize == _sre.CODESIZE and os.environ.get("INSTINCT_RX_CACHE", "1") != "0"
except Exception:
    _RX_STAMP, _RX_ON = None, False
_RX_FILE = os.environ.get("INSTINCT_RX_FILE") or os.path.join(HERE, "__pycache__",
                                                              "instinct_rx.%x.marshal" % sys.hexversion)
_RX = {"table": None, "readable": True, "added": {}, "dropped": set(), "dirty": False}


def _rx_read():
    """(table, readable): the kept programs on disk; {} when the file is absent, damaged or another Python's. readable
    is False only when the file is there and could not be read (another call replacing it, say)."""
    try:
        with open(_RX_FILE, "rb") as f:
            blob = f.read()
    except FileNotFoundError:
        return {}, True
    except Exception:
        return {}, False
    try:
        stamp, tab = marshal.loads(blob)
        if stamp == _RX_STAMP and isinstance(tab, dict):
            return tab, True
    except Exception:
        pass
    return {}, True


def _rx_crc(trigger, flags, groups, gindex, igroup, code):
    return zlib.crc32(("%r|%d|%d|%r|%r" % (trigger, flags, groups, sorted(gindex.items()), igroup))
                      .encode("utf-8", "backslashreplace") + code)


def _rx_keep(trigger, ref):
    """Keep what re.compile made of a trigger, once the Pattern rebuilt from it equals ref. The parse and the code
    generation are re._compiler.compile's own steps, with warnings silenced: compiling ref already raised them."""
    import warnings
    flags0 = re.I.value
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        p = _re_parser.parse(trigger, flags0)
        code = _re_compiler._code(p, flags0)
    gindex = dict(p.state.groupdict)
    igroup = [None] * p.state.groups
    for k, i in gindex.items():
        igroup[i] = k
    igroup = tuple(igroup)
    flags, groups = flags0 | p.state.flags, p.state.groups - 1
    got = _sre.compile(trigger, flags, list(code), groups, gindex, igroup)
    if (got == ref and got.flags == ref.flags and got.groups == ref.groups
            and dict(got.groupindex) == dict(ref.groupindex)):
        cb = _array("I", code).tobytes()
        e = (flags, cb, groups, gindex, igroup, _rx_crc(trigger, flags, groups, gindex, igroup, cb))
        _RX["table"][trigger] = _RX["added"][trigger] = e
        _RX["dirty"] = True


def _trigger_rx(trigger):
    """The Pattern re.compile(trigger, re.I) gives, rebuilt from its kept program when there is a good one."""
    if not _RX_ON or not isinstance(trigger, str):
        return re.compile(trigger, re.I)
    if _RX["table"] is None:
        _RX["table"], _RX["readable"] = _rx_read()
    e = _RX["table"].get(trigger)
    if e is not None:
        try:
            flags, code, groups, gindex, igroup, crc = e
            if _rx_crc(trigger, flags, groups, gindex, igroup, code) == crc:
                return _sre.compile(trigger, flags, _array("I", code).tolist(), groups, gindex, igroup)
        except Exception:
            pass
        del _RX["table"][trigger]
        _RX["dropped"].add(trigger)
        _RX["dirty"] = True
    ref = re.compile(trigger, re.I)
    try:
        _rx_keep(trigger, ref)
    except Exception:
        pass
    return ref


def _rx_save(instincts):
    """Write the kept programs back when this call added or dropped one: what the file holds now (another call may have
    added to it since this one read it), less what this call dropped, plus what it added, pruned to the triggers
    instincts.tsv still has; through a private tmp file and os.replace, so a reader sees the old file or the new one.
    Nothing is written when the file was there and could not be read, and any failure (a reader holding it open on
    Windows, a full disk) leaves the old file for the next call that misses."""
    if not _RX["dirty"] or not _RX["readable"]:
        return
    _RX["dirty"] = False
    tmp = None
    try:
        cur, readable = _rx_read()
        if not readable:
            return
        for k in _RX["dropped"]:
            cur.pop(k, None)
        cur.update(_RX["added"])
        live = set(it.get("trigger") for it in instincts)
        tab = dict((k, v) for k, v in cur.items() if k in live)
        os.makedirs(os.path.dirname(_RX_FILE), exist_ok=True)
        tmp = "%s.%d.%s.tmp" % (_RX_FILE, os.getpid(), os.urandom(4).hex())
        with open(tmp, "wb") as f:
            f.write(marshal.dumps((_RX_STAMP, tab)))
        os.replace(tmp, _RX_FILE)
        tmp = None
    except Exception:
        pass
    finally:
        if tmp is not None:
            try:
                os.remove(tmp)
            except Exception:
                pass


def _fire(text, instincts, damp=None):
    hits = []
    table = _anchor_table()
    low = text.lower() if table else ""
    could = None
    if table:
        try:
            import instinct_anchors
            could = instinct_anchors.could_match
        except Exception:
            table = {}
    for it in instincts:
        try:
            if table and could is not None and not could(it["trigger"], low, table):
                continue
            m = _trigger_rx(it["trigger"]).findall(text)
        except Exception:
            continue
        if m:
            hits.append((len(m), it))
    _rx_save(instincts)
    # graph-score gate (07-16): on a self-referential surface (signalled by damp being
    # non-empty), a regex fire whose anchor-token score against THIS text is below
    # threshold is demoted like a damped reflex - vocabulary-proximity fires lose top-3
    # contention to embodiment-relevant scars. Demotion, never deletion; safety scars
    # (_NEVER_DAMP) exempt; no gate file -> zero behaviour change.
    gate_pen = {}
    if _SCORE_GATE and damp:
        th, pen, anchor = _SCORE_GATE
        seen = set(_WORD_RX.findall(text[:32768].lower()))
        for _, it in hits:
            rid = it["id"]
            toks = anchor.get(rid)
            if not toks or rid in _NEVER_DAMP:
                continue
            if sum(w for t, w in toks.items() if t in seen) < th:
                gate_pen[rid] = pen
    # graded gut: the sharpest scar takes the working-memory spotlight. Charge STRICTLY dominates (a burn
    # outranks a paper-cut); THEN IDF surface-weight (surface the rare/specific reflex over the always-on
    # generic, since charge is saturated at 1.00 for 37/45 reflexes and can't rank them); trigger-count is
    # the final tiebreak. Lexicographic, so a safety scar (no_local_privileged_stack, secrets_in_git) can never be
    # displaced from the top-3 by a broad low-charge trigger that merely matched more times (the old additive
    # key did that on 0.12% of actions; the surface-weight tier also cuts generics in the top-3 by 25%).
    # context damp (see _ranking_doc): on a self-referential surface a domain-inverted reflex competes at
    # charge-damp, so it loses spotlight contention to surface-relevant scars but still fires when it's
    # what matched - damping, never deletion.
    damp = damp or {}
    # judged precision (owner order 2026-09-02): a recognition cue that was judged off-domain on most of its
    # fires stays silent (outside_view 0/4 the night the order came); every other reflex's charge is scaled by
    # its judged precision so a fire that tends to be right wins the spotlight; unjudged and safety scars are
    # untouched (see _precision_factor). Fail-open through _load_precision.
    prec = _load_precision()
    if prec:
        kept = []
        for h in hits:
            if _gated_by_precision(h[1], prec):
                _suppress(h[1].get("id"), "precision")
                continue
            kept.append(h)
        hits = kept
    for h in hits:
        _term = _source_supersession(h[1].get("source"))
        if _term:
            h[1]["_superseded_by"] = _term
        else:
            _parts = _source_part_supersession(h[1].get("source"))
            if _parts:
                h[1]["_part_superseded"] = _parts     # a hint line in the render; the charge is untouched
    # SAFETY SCARS FIRST (30 Sep 2026, the 1000-page report's D03-T05: learned precision and mandatory rules have
    # different authority). The key above ranked a never-damped safety scar on the same scale as everything else, so two
    # well-judged method reflexes on the same action took both MAX_FIRE slots: from 2 to 30 Sep the ten safety scars
    # lost 1,175 of 3,668 fresh fires (32 pct) to the cap - pinned_file_two_tripwires 73 pct, lookahead 40,
    # secrets_in_git 28, no_local_privileged_stack 24 (instinct_suppressed.tsv reason "cap" against instinct_fires.tsv). A safety
    # scar now takes a slot before any other reflex; the cap, the backoff and every other gate are unchanged, and the
    # order within each tier is the key it always was.
    hits.sort(key=lambda h: (h[1]["id"] in _NEVER_DAMP,
                             h[1].get("charge", 0.6) * _precision_factor(h[1]["id"], prec)
                             * (0.5 if h[1].get("_superseded_by") else 1.0)
                             - damp.get(h[1]["id"], 0.0) - gate_pen.get(h[1]["id"], 0.0),
                             SURFACE_WT.get(h[1]["id"], 0.5), h[0]), reverse=True)
    _LAST_RANKED[:] = [it for _, it in hits]   # main() refills from this after its filters and logs the cap
    return [it for _, it in hits[:MAX_FIRE]]


RENDER_MAX_GUT = 600     # a note is a hint, not a wall: the longest guts run past 2,500 characters (measured 09-02)
RENDER_MAX_STING = 320


def _clip(text, n):
    """Cut a note at a sentence end near n characters and say where the rest lives. A reader who needs the
    whole scar recalls it by name; a two-thousand-character note under every action is the opposite of the
    owner's 'fire less, be more confident'."""
    try:
        text = text or ""
        if len(text) <= n:
            return text
        cut = text.rfind(". ", int(n * 0.6), n)
        cut = cut + 1 if cut > 0 else n
        return text[:cut].rstrip() + " ... (%d more characters; recall the memory for the rest)" % (len(text) - cut)
    except Exception:
        return text


def _render(fired):
    danger = [it for it in fired if it.get("mode", "fire") == "fire"]
    guide = [it for it in fired if it.get("mode") == "guide"]
    lines = []
    prec = _load_precision()
    if danger:
        # owner order 2026-09-02 09:1x: a surfaced note can point right or wrong. Every fire below carries a
        # hint with its own judged precision, and the header says what a fire is: a question to weigh
        # against what you can see, never an instruction to obey.
        lines.append("[unconscious] instinct surfacing - a note can point right or wrong; weigh each against what "
                     "you see, do not obey it. You may be in territory that has burned us before:")
        # healer-first (measured 07-12): in a long session ~18 reflexes cross count>=3, so escalating EVERY one
        # of them habituates the very signal the escalation exists to make un-ignorable. Escalate at most the
        # SINGLE most read-past reflex per render - one focused wake-up beats a wall of diluted ones.
        _esc = [it for it in danger if it.get("_count", 1) >= 3 and it.get("charge", 0) >= 0.95
                and _escalation_earned(it.get("id"), prec)]
        _top_esc = max(_esc, key=lambda it: it.get("_count", 1)) if _esc else None
        for it in danger:
            lv = ("  (low-validity domain: this is a place intuition is known to lie - flag it and verify "
                  "against the lived book, do NOT act on the hunch)") if it.get("validity") == "low" else ""
            lines.append(f"  - {_clip(it['gut'], RENDER_MAX_GUT)}{lv}")
            lines.append("    (" + _hint(it.get("id", ""), prec, it.get("_via")) + ")")
            # mindware-override escalation: the ONE high-stakes instinct you've read past most this session -
            # today's failure was reading past a fired signal, not missing it (referee afec84b0). Framing only.
            if it is _top_esc:
                lines.append("    [!] REPEATED x%d THIS SESSION - you've surfaced this exact high-stakes "
                             "instinct %d times and are still here. The failure mode is reading past a fired "
                             "signal (mindware-override), not missing it. Do NOT click past this one: stop and "
                             "act on it now, or say plainly why it does not apply." % (it["_count"], it["_count"]))
            if it["sting"]:
                lines.append(f"    (scar: {_clip(it['sting'], RENDER_MAX_STING)} -> [[{it['source']}]])")
            if it.get("_superseded_by"):
                lines.append(f"    (SOURCE SUPERSEDED: the memory behind this reflex was overtaken by "
                             f"[[{it['_superseded_by']}]] - the rule above may be stale; read that memory before "
                             f"acting on this line)")
            elif it.get("_part_superseded"):
                _moved = "; ".join((f"{c} moved to [[{s}]]" if s else f"{c} was corrected in its own body")
                                   for c, s in it["_part_superseded"][:2])
                lines.append(f"    (SOURCE PARTLY SUPERSEDED: {_moved} - the rest of the memory behind this reflex "
                             f"stands; check that the line above is not the part that moved)")
        # the gradation lives in the ORDER above (charge-weighted: the sharpest scar leads, mild ones
        # sink) - honest at this corpus, where the load-bearing scars are nearly all maximal burns.
        lines.append("Danger-sense: " + DANGER)
    if guide:
        lines.append("[unconscious] recognition - a move that has worked here before; a hint, not an order:")
        for it in guide:
            lines.append(f"  - {_clip(it['gut'], RENDER_MAX_GUT)}")
            lines.append("    (" + _hint(it.get("id", ""), prec, it.get("_via")) + ")")
    return "\n".join(lines)


def _revision(it):
    """What a reflex activates on and asks for, as a short fingerprint of its trigger and its gut. The backoff keeps it
    beside the reflex's id, so a revision under the same id (a narrower cue, a changed intervention) reaches a session
    that saw the old text instead of waiting out the window the old text earned. D11-T05 of the 1000-page report, 30 Sep:
    on the live hook before this change the revision was held in every case (d11_t05_revision_backoff.py in the audit
    folder r1000_d_tests/d11). A change to the sting (the scar's story) alone does not re-arm it. An entry written before
    this change carries no fingerprint and keeps holding the current text, so the change re-fires nothing by itself."""
    trig, gut = it.get("trigger") or "", it.get("gut") or ""
    if not (trig or gut):
        return ""
    return hashlib.sha256((trig + "\x1f" + gut).encode("utf-8")).hexdigest()[:12]


def _dedup(session, fired):
    """Drop reflexes already surfaced for THIS session inside their current backoff window, so a
    reflex fires when you enter its territory, not on every step - and so re-entering that territory
    all evening does not re-fire it every ten minutes (see _window). Per-session (each session gets
    its own priming). Fail-safe: any state error -> fire normally (more firing, never less)."""
    if not session or not fired:
        return fired
    now = time.time()
    # per-session state (2026-09-02): one small file per session, replaced atomically - the shared file was
    # a lost-update race across concurrent sessions (see the DEDUP_SECS note). The old shared file is left
    # alone; a session id that cannot be a filename falls back to it.
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session)[:80]
    state_path = os.path.join(STATE_DIR, safe + ".json") if safe else STATE
    try:
        st = json.load(io.open(state_path, encoding="utf-8")) if os.path.exists(state_path) else {}
    except Exception:
        st = {}
    # value is [last_ts, count]; tolerate the old bare-ts format. count = how many times this reflex has
    # actually FIRED this session (a read-past signal for the mindware-override escalation in _render).
    def _tsval(v):
        try:
            return float(v[0] if isinstance(v, list) else v)   # tolerate any type; bad -> 0.0 (gets pruned)
        except Exception:
            return 0.0
    st = {k: v for k, v in st.items() if now - _tsval(v) < 86400}   # prune >1 day (drops corrupt entries)
    keep = []
    updates = {}
    for it in fired:
        key = session + "|" + it["id"]
        prev = st.get(key)
        # defensive: tolerate ANY corrupt value (string, short list, null) - coerce to numbers, and on a
        # bad value RESET to fire (never let a corrupt state silently suppress a reflex forever).
        if isinstance(prev, list) and len(prev) >= 2:
            last, cnt = prev[0], prev[1]
        elif isinstance(prev, (int, float)):
            last, cnt = prev, 1
        else:
            last, cnt = 0, 0
        try:
            last, cnt = float(last), int(cnt)
        except Exception:
            last, cnt = 0.0, 0
        rev = _revision(it)
        seen = prev[2] if isinstance(prev, list) and len(prev) >= 3 and isinstance(prev[2], str) else ""
        if rev and seen and rev != seen:
            last, cnt = 0.0, 0          # a revision of this reflex: new to this session (see _revision)
        if now - last >= _window(cnt):
            cnt += 1
            it["_count"] = cnt          # surfaced this many times this session; _render escalates high-stakes repeats
            keep.append(it)
            st[key] = [now, cnt, rev] if rev else [now, cnt]   # fresh fires refresh the timer + bump count;
            updates[key] = st[key]                             # suppressed keep their clock
        else:
            _suppress(it.get("id"), "backoff")
    if _DEFER_BACKOFF[0]:
        if updates:
            _BACKOFF_PENDING.setdefault(state_path, {}).update(updates)
        return keep
    _write_backoff(state_path, st, now)
    return keep


# THE BACKOFF, THE FIRE LOG AND A SHOWN REFILL ARE WRITTEN ONLY AFTER THE CONTEXT IS (28 Sep, scheduled audit account04/B04
# r4). Until then _dedup wrote the per-session backoff and main logged the fire before rendering and output, so a failure
# in between left a fire no session received, held that reflex for 30 minutes, and entered every study that counts
# fires. main turns deferral on; _dedup and the refill record what they would write, and main commits it after its
# output is written and flushed. The refill A/B's rows are not deferred - see _refill_after_backoff.
_DEFER_BACKOFF = [False]
_BACKOFF_PENDING = {}     # state path -> {session|reflex: [ts, count]}


def _commit_backoff():
    """Write what _dedup and the refill deferred. Called once the context is out; fail-safe. Returns ['backoff'] when a
    state write failed, for main to record."""
    _DEFER_BACKOFF[0] = False
    now = time.time()
    failed = []
    for state_path, updates in list(_BACKOFF_PENDING.items()):
        try:
            st = json.load(io.open(state_path, encoding="utf-8")) if os.path.exists(state_path) else {}
        except Exception:
            st = {}
        if not isinstance(st, dict):
            st = {}
        st.update(updates)
        if not _write_backoff(state_path, st, now) and "backoff" not in failed:
            failed.append("backoff")
    _BACKOFF_PENDING.clear()
    return failed


def _discard_backoff():
    _DEFER_BACKOFF[0] = False
    _BACKOFF_PENDING.clear()


def _write_backoff(state_path, st, now):
    """Replace the session's backoff state atomically; True when it was written."""
    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        tmp = state_path + ".%d.tmp" % os.getpid()
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, state_path)
        # prune other sessions' files older than a day, now and then: 1 in 50, the same 53-bit draw random.random() <
        # 0.02 made, from os.urandom, so that a hook call need not import random (12 ms in a fresh process; R24)
        if (int.from_bytes(os.urandom(7), "big") >> 3) < 0.02 * 2.0 ** 53:
            for fn in os.listdir(STATE_DIR):
                p = os.path.join(STATE_DIR, fn)
                try:
                    if now - os.path.getmtime(p) > 86400:
                        os.remove(p)
                except Exception:
                    pass
        return True
    except Exception:
        return False



REFILL_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instinct_refills.tsv")

# The refill A/B, registered 28 Sep 03:39 in brain_graph/study/THREE_DAY_RUN_2026-09-27.md: does a shown reflex change
# what the agent does next? For four reflexes whose trigger is itself the act they warn against, each (session, reflex)
# is shown or held by the low bit of sha256("session:reflex"), fixed for the session; a held refill is not shown, not
# entered in the backoff, and logged. Every other reflex refills as before ("open"). INSTINCT_REFILL_AB=0 shows all.
REFILL_AB = ("mesh_claim_never_chained_with_mutate", "a_refused_mesh_claim_did_not_stop_the_write",
             "bare_open_reads_cp1252", "edit_tool_for_verbatim_surgery")


# The primary A/B (6 Oct 2026: a show-or-hold trial for the reflex lines, because only a
# randomized trial can say whether a line helps): does a shown reflex change what happens next? Every reflex outside the refill trial above and outside
# the never-hold words is, for each (session, reflex), shown or held by sha256("primary:session:reflex"), one in
# PRIMARY_HOLD_EVERY held, fixed for the session. A held reflex has passed _dedup (it entered the backoff as a shown one
# does), keeps its slot through the refill (no other reflex takes its place), is not rendered, and is written with the
# shown ones to instinct_primary_ab.tsv before anything is sent: ts, session, reflex, arm, opportunity. Registered
# outcome, per (session, reflex) at its first row: the same reflex matching again in that session within 30 minutes (the
# act it warns against recurring), a tool error within the next five calls, and his correction in the session.
# INSTINCT_PRIMARY_AB=0 shows all. A held reflex enters the backoff exactly as a shown one does: _dedup records every
# reflex it keeps and main commits that record after the output, so a repeat inside the window is suppressed 'backoff'
# in both arms and instinct_suppressed.tsv measures it the same way for both. Registered exclusion: sessions whose id
# starts 0000ab are the refill tests' own (nine such rows reached the live file on 6 Oct before the log followed
# REFILL_LOG; brain logs are never deleted, so they stay and the analysis drops them).
PRIMARY_AB_LOG = None   # None: instinct_primary_ab.tsv beside REFILL_LOG, so a test that sandboxes one sandboxes both
PRIMARY_HOLD_EVERY = 4
_NEVER_HOLD_WORDS = ("law", "trading", "secret", "credential", "windowless", "never", "delete", "money", "credit",
                     "github", "push", "box", "google", "kill", "flash", "danger")


PRIMARY_ELIGIBLE = os.path.join(HERE, "primary_ab_eligible_v1.txt")
_ELIGIBLE = [None]


def _eligible():
    """The frozen allowlist (version 1): only a reflex listed there can be held; a missing or unreadable list holds
    nothing. Dot D7CB6565's review, 6 Oct: words in an id are not a safety classification."""
    if _ELIGIBLE[0] is None:
        try:
            with open(PRIMARY_ELIGIBLE, encoding="utf-8") as f:
                _ELIGIBLE[0] = set(l.strip() for l in f if l.strip() and not l.startswith("#"))
        except Exception:
            _ELIGIBLE[0] = set()
    return _ELIGIBLE[0]


def _primary_arm(session, rid):
    if os.environ.get("INSTINCT_PRIMARY_AB", "1") == "0" or not session or not rid or rid in REFILL_AB:
        return "open"
    if rid not in _eligible():
        return "open"
    if any(w in rid.lower() for w in _NEVER_HOLD_WORDS):
        return "open"
    import hashlib
    h = hashlib.sha256(("primary:%s:%s" % (session, rid)).encode("utf-8")).digest()
    return "held" if int.from_bytes(h[:4], "big") % PRIMARY_HOLD_EVERY == 0 else "shown"


def _primary_ab(session, fired):
    """Split `fired` into (shown, held) by _primary_arm and write one row per reflex in both arms. Fail-open: any error
    returns (fired, [])."""
    try:
        shown, held, rows = [], [], []
        for it in fired:
            rid = it.get("id") or ""
            arm = _primary_arm(session, rid) if _primary_item_eligible(it) else "open"
            (held if arm == "held" else shown).append(it)
            if arm != "open":
                rows.append("%s\t%s\t%s\t%s\t%s\n" % (_T0_S, session, rid, arm, opportunity_id(_CALL_ID, rid)))
        if rows:
            with open(PRIMARY_AB_LOG or os.path.join(os.path.dirname(REFILL_LOG), "instinct_primary_ab.tsv"), "a",
                      encoding="utf-8") as f:
                f.write("".join(rows))
        return shown, held
    except Exception:
        return fired, []


def _primary_item_eligible(it):
    """The item-level half of the gate (dot D7CB6565's source review, 6 Oct): a listed id is held only while its LIVE
    charge is still at most 0.83 and it fired by its own trigger; the same id arriving by the graph or forward-feed
    route, or with its charge raised since the list was frozen, is shown."""
    if it.get("_via") in ("graph", "ff"):
        return False
    try:
        return float(it.get("charge") if it.get("charge") not in (None, "") else 1.0) <= 0.83
    except (TypeError, ValueError):
        return False


def _split_and_render(receiver, fired, drop_act):
    """Dedup, refill, then ONE primary split for every route a reflex can be rendered by. Returns (shown, held).
    Before 6 Oct the split ran between the dedup and the refill, so a reflex the refill added was never assigned and
    could be shown although its arm was held (dot D7CB6565's source review, P1-2). Splitting after the refill keeps a
    held reflex's slot, since the refill already counted it, and a refilled one cannot bypass its arm."""
    fired = _dedup(receiver, fired)
    fired = _refill_after_backoff(receiver, fired, drop_act)
    return _primary_ab(receiver, fired)


def _settle_backoff_when_silent(held):
    """Nothing is rendered on this call. A held reflex still enters the backoff exactly as a shown one does; before
    6 Oct this branch discarded every pending entry, so a held-only call left no backoff and the two arms' repeat
    outcomes differed mechanically (dot D7CB6565's source review, P1-1). Only the held reflexes' entries are committed
    (the key is receiver|reflex); anything else pending is discarded as before."""
    ids = set(it.get("id") for it in (held or []) if it.get("id"))
    if not ids:
        _discard_backoff()
        return []
    for state_path in list(_BACKOFF_PENDING):
        _BACKOFF_PENDING[state_path] = {k: v for k, v in _BACKOFF_PENDING[state_path].items()
                                        if "|" in str(k) and str(k).split("|", 1)[1] in ids}
    return _commit_backoff()


def _refill_arm(session, rid):
    if rid not in REFILL_AB or os.environ.get("INSTINCT_REFILL_AB", "1") == "0":
        return "open"
    import hashlib
    return "held" if hashlib.sha256(("%s:%s" % (session, rid)).encode("utf-8")).digest()[-1] & 1 else "shown"


# THE RECEIVER, NOT THE SESSION (30 Sep 2026, claude-825b1305; the 1000-page report's D12-T01). A subagent's tool step
# carries its PARENT's session_id and its own agent_id (owner_context_delivery.py measured it on 25 Sep; a subagent
# transcript records the parent's sessionId beside its agentId). The backoff was kept per session id, so a reflex the
# parent had just seen was held back from its child, a child's warning silenced the parent, and a second child got
# nothing after the first: 3 of 5 checks failed on the live hook (r1000_d_tests/d12/d12_t01_live_before.json). The
# backoff now belongs to the receiver: the session for its own steps, session~agent for a subagent's. The fire log and
# the graph lane keep the session id as before.
_AGENT_TRANSCRIPT = re.compile(r"/subagents/agent-([A-Za-z0-9]+)\.jsonl$")


def _receiver(data):
    """The session id, or session~agent when the step is a subagent's (agent_id, or a subagents/ transcript path)."""
    session = str(data.get("session_id", "") or "")
    agent = str(data.get("agent_id") or "")
    if not agent:
        path = str(data.get("agent_transcript_path") or data.get("transcript_path") or "").replace("\\", "/")
        m = _AGENT_TRANSCRIPT.search(path)
        agent = m.group(1) if m else ""
    agent = re.sub(r"[^A-Za-z0-9]", "", agent)[:24]
    return session + "~" + agent if session and agent else session


def _in_backoff(session, rid, rev=""):
    """Read-only twin of _dedup's test: is this reflex inside its backoff window for this session? A held refill is
    counted only when a shown one would have been shown, so the two arms are cut the same way. A revision the session
    has not seen is outside the window, as in _dedup."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session)[:80]
    path = os.path.join(STATE_DIR, safe + ".json") if safe else STATE
    try:
        prev = (json.load(io.open(path, encoding="utf-8")) if os.path.exists(path) else {}).get(session + "|" + rid)
        if isinstance(prev, list) and len(prev) >= 2:
            if rev and len(prev) >= 3 and isinstance(prev[2], str) and prev[2] and prev[2] != rev:
                return False
            last, cnt = float(prev[0]), int(prev[1])
        elif isinstance(prev, (int, float)):
            last, cnt = float(prev), 1
        else:
            return False
        return time.time() - last < _window(cnt)
    except Exception:
        return False


def _refill_after_backoff(session, fired, drop_act=False):
    """Fill the slots the session backoff emptied (28 Sep, claude-825b1305). The cap takes the top MAX_FIRE ranked
    hits BEFORE _dedup runs, so a reflex the session's backoff then silences still held a slot, and the next ranked
    hits stayed marked 'cap'. Measured over the 7 days to 28 Sep: 14,573 calls had a reflex held by the cap while fewer
    than MAX_FIRE were shown (10,848 showed none). At 02:26:54 on 28 Sep secrets_in_git held slot two and was backed
    off, while the two reflexes made for that command (a mesh claim chained to writes with ';') were held; the command
    then pushed on a refused claim. So the next ranked hits pass the same gates - the mention/prose filter when it
    ran, the precision gate - and the same backoff, one at a time, until MAX_FIRE are shown or none is left. Replayed
    on the same week it would add 7,532 fires to 23,364 (+32 pct), at the same median judged precision as the fires
    shown (0.64). Each refill is logged to instinct_refills.tsv so the nightly judge can score them apart.
    INSTINCT_REFILL_BACKOFF=0 turns it off. Fail-open: any error returns `fired` unchanged."""
    if os.environ.get("INSTINCT_REFILL_BACKOFF", "1") == "0":
        return fired
    try:
        if not session or len(fired) >= MAX_FIRE:
            return fired
        have = set(it.get("id") for it in fired)
        tried = set(r for r, why in _SUPPRESSED if why in ("backoff", "precision", "mention", "prose", "platform", "event"))
        hf = None
        if drop_act:
            sys.path.insert(0, GF_DIR)
            import habituation_filter as hf
        out, added = list(fired), []
        for it in list(_LAST_RANKED):
            if len(out) >= MAX_FIRE:
                break
            rid = it.get("id")
            if not rid or rid in have or rid in tried:
                continue
            if hf is not None and hf.danger_surface(rid) == "act" and rid not in _MENTION_NEVER:
                continue
            if _gated_by_precision(it):
                continue
            have.add(rid)
            arm = _refill_arm(session, rid)
            if arm == "held":
                if _in_backoff(session, rid, _revision(it)):
                    # not an A/B opportunity: the backoff refuses it in either arm, so it is 'backoff', never 'ab_held'
                    # (29 Sep, account01/B04, B04-F203: it was logged under both)
                    _suppress(rid, "backoff")
                    _SUPPRESSED[:] = [(r, why) for r, why in _SUPPRESSED if not (r == rid and why == "cap")]
                else:
                    added.append((rid, "held"))
                    _SUPPRESSED[:] = [(r, "ab_held" if (r == rid and why == "cap") else why) for r, why in _SUPPRESSED]
                continue
            got = _dedup(session, [it])        # the same backoff: a reflex in its window is suppressed 'backoff'
            _SUPPRESSED[:] = [(r, why) for r, why in _SUPPRESSED if not (r == rid and why == "cap")]
            if got:
                out.extend(got)
                added.append((rid, arm))
        if added:
            # THE REFILL LOG IS THE A/B'S ASSIGNMENT LOG (29 Sep, scheduled audit account04/B04 r6,
            # B04-R43-REFILL-AB-LOGGING-BOUNDARY-ASYMMETRY). One row per refill opportunity, both arms, written here,
            # when the arm is decided and before anything is rendered or sent. From 28 Sep a shown row waited for the
            # context to go out while a held row was written at once, so a call that failed afterwards kept its held
            # row and lost its shown one. Whether a shown refill reached the session is in instinct_fires.tsv, which
            # is written only after the context is out: join the two on session and reflex. A held refill does not
            # enter the backoff (as registered), so it can be offered again on the next call where a shown one waits
            # out its window: count each (session, reflex) at its first row in each arm.
            rows = "".join("%s\t%s\t%s\t%s\t%s\t%s\n" % (_T0_S, session, rid, ",".join(it.get("id") for it in fired),
                                                         arm, opportunity_id(_CALL_ID, rid)) for rid, arm in added)
            try:
                with io.open(REFILL_LOG, "a", encoding="utf-8") as fh:
                    fh.write(rows)
            except Exception:
                _note_emit_failure(["refills"], session, [rid for rid, _arm in added])
        return out
    except Exception:
        return fired

GRAPH_PRIME_MIN = 0.45   # a graph fire surfaces on priming only when a CONSCIOUS deposit is still this hot (09-02)


def _ff_gated(rid, prec=None):
    """A forward-feed fire says 'a command like this one just failed'. It is gated by ITS OWN judged record on
    that route (reflex_precision.json 'paths': {'ff': {...}}, judged at n >= 4): below GUIDE_MIN_P it stays
    silent, like a graph fire under _graph_gated. The never-damped safety scars are exempt and the regex path of
    the same reflex is untouched. Scheduled audit account01/B04 F158, 27 Sep: the ff precision was built nightly
    and nothing read it. Fail-open: any error -> False."""
    try:
        if rid in _NEVER_DAMP:
            return False
        prec = _load_precision() if prec is None else prec
        v = ((prec.get(rid) or {}).get("paths") or {}).get("ff")
        return bool(v and v.get("judged") and float(v.get("p", 1.0)) < GUIDE_MIN_P)
    except Exception:
        return False


def _augment_graph(text, data, instincts, fired):
    """Union the GRAPH-derived fires (gfcore) + forward-feed cautions into the regex fires - so a
    situation graph-near a scar fires the reflex even when its own words don't match ('heredoc' wakes
    'CRLF'), and a just-made failure arrests the next one (the backslash-path bug). Gated by
    graph_fire/ENABLED: absent -> shadow (log only, return `fired` UNCHANGED, zero behaviour change).
    Fail-safe by construction: ANY error -> return `fired` unchanged, so the graph path can never break
    the regex path this hook has always run."""
    try:
        sys.path.insert(0, GF_DIR)
        import gfcore, forward_feed
        session = data.get("session_id", "") or ""
        tn = data.get("tool_name", "") or ""
        gfires = gfcore.graph_fire(text, session) or []
        ffires = forward_feed.forward_feed(text, tn, session) or []
        try:
            gfcore.shadow_log(session, text, list(gfires) + [{"id": f["id"]} for f in ffires])
        except Exception:
            pass
        if not os.path.exists(GF_ENABLED):
            return fired  # shadow: no behaviour change until deliberately enabled
        have = set(it["id"] for it in fired)
        byid = {it["id"]: it for it in instincts}
        add = []
        for f in gfires:
            rid = f.get("id")
            if not rid or rid in have:
                continue
            # The calibrated broad graph adds ~0 recall over regex but +nag on representative data
            # (measured on 2500 real actions). So only SURFACE a graph fire that was PRIMED (a consciously-
            # recalled scar lowered its bar - the valuable bridge case) or is a very strong match; this keeps
            # the command surface at the regex baseline normally. (note_fires still deposits ALL fires for
            # the bridge, so the subconscious->conscious signal is unaffected.)
            # 2026-09-02, 40 graph fires judged by hand: 2 on-domain, and 39 of the 40 would NOT have fired
            # unprimed (recomputed with an empty buffer) - they came through priming, and any nonzero prime
            # counted, including the hook's OWN deposits from note_fires (a fire it computed but never showed
            # primed itself for the next 25 minutes). "Primed" now means a CONSCIOUS deposit that is still hot:
            # spread_recall deposits 1.0 (prime 0.6, 0.45 after ten minutes); the gut's self-deposits give at most
            # ~0.36 on one fire and are no longer made for unshown fires (see note_fires below).
            if not (float(f.get("primed") or 0.0) >= GRAPH_PRIME_MIN or f.get("score", 0) >= 2.6):
                continue
            rec = byid.get(rid)
            if rec and _gated_by_precision(rec):
                _suppress(rid, "precision")
                continue   # the graph path honours the same judged-precision gate as the regex path (09-02)
            if rec and _graph_gated(rid):
                _suppress(rid, "precision_graph")
                continue   # a graph-path fire is an association, a cue - gated by ITS OWN judged precision
            if rec and _graph_lane_gated(rid, explore_key="%s|%d" % (session, int(time.time() // 3600))):
                _suppress(rid, "graph_lane_prior")
                continue   # no graph record of its own: the lane's pooled record decides (see _graph_lane_gated)
            if rec:
                rec = dict(rec, _via="graph")   # mechanism attribution for the fire log (07-16)
                add.append(rec)
                have.add(rid)
        for f in ffires:
            rid = f.get("id")
            if not rid or rid in have:
                continue
            if _ff_gated(rid):
                _suppress(rid, "precision_ff")
                continue   # its forward-feed route was judged off-domain on most of its fires (F158)
            add.append({"id": rid, "mode": "fire", "validity": "high", "gut": f.get("gut", ""),
                        "sting": f.get("why", ""), "source": "MISTAKES", "charge": f.get("charge", 0.9),
                        "_via": "ff"})
            have.add(rid)
        # bound the total shown at MAX_FIRE (the graph path used to lift it to 4 - the owner's 2026-09-02
        # "fire less" order applies here too); a forward-feed fire about a JUST-FAILED command may ride one
        # past the cap, since that is the one signal the regex path cannot see. Regex fires keep priority.
        ff_first = [a for a in add if a.get("_via") == "ff"][:1]
        rest = [a for a in add if a is not (ff_first[0] if ff_first else None)]
        merged = fired + ff_first + rest[:max(0, MAX_FIRE - len(fired) - len(ff_first))]
        recent_fail = any(f.get("id") == "recent_repeat" for f in ffires)  # a command resembling a just-failed one
        try:
            # The gut deposits NOTHING into the buffer it reads for priming - only the danger bump on a REAL
            # failure. Until 2026-09-02 13:41 every graph fire the model computed was deposited (shown or not)
            # and read back as priming for the same reflex; from 13:41 only the shown fires were deposited, and
            # the next nine graph fires still all came through priming (none cleared the raw score) - a shown
            # fire re-primes itself just as well. Priming now comes from the CONSCIOUS layer alone
            # (spread_recall deposits the memory it surfaced), which is what the bridge was built to carry.
            gfcore.note_fires(session, [], extra_danger=0.6 if recent_fail else 0.0)
        except Exception:
            pass
        return merged
    except Exception:
        return fired


# A picture-maker work order is a fully specified image job for a headless Codex worker. Over the week to 2026-09-24,
# 1,080 reflex notes landed on these prompts from 58 reflexes, two of them about pictures; the rest were prose-voice,
# recall, verification and trading scars. Codex runs this engine twice on a prompt (through codex_reflex_hook.py and
# directly, the 09-09 reflex trio), so the skip lives here, where both paths meet. Since 09-23 17:27 the briefs also
# open "You are THE picture maker", which the first form missed (10 fires in the log by 09-24 19:40).
_IMAGE_WORK_ORDER = re.compile(r"^\s*(?:\[headless-worker\]\s*)?You are (?:a|the) picture maker\b", re.I)


_EXACT_NAME = [None]


def _exact_name_line(data, text):
    """The exact-name trigger (exact_name.py; owner, 6 Oct 2026 13:3x his clock): at most one card per call, computed
    once per call and cached here; fail-open to ''. EXACT_NAME_OFF=1 turns it off."""
    if _EXACT_NAME[0] is None:
        try:
            if HERE not in sys.path:
                sys.path.insert(0, HERE)
            import exact_name as _en
            _EXACT_NAME[0] = _en.line(data, text) or ""
        except Exception:
            _EXACT_NAME[0] = ""
    return _EXACT_NAME[0]


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return  # silent
    # name the surface first, so a slow call that dies before the text builder still says where it was
    # (a 158 s row on 2026-09-02 13:33 read "?" for both fields and could not be traced)
    _SLOW_EVENT.update(event=str(data.get("hook_event_name") or "?"), session=str(data.get("session_id") or ""))
    try:
        ev, text = _text_from(data)
        _SLOW_EVENT.update(event=ev, session=data.get("session_id", "") or "")
        if not text:
            return
        if ev == "UserPromptSubmit" and _IMAGE_WORK_ORDER.match(text):
            return
        exact_ctx = _exact_clause_context(ev, text)
        if len(text) > MATCH_CAP:
            text = text[:MATCH_CAP]   # keep the hot path fast on huge tool_inputs (see MATCH_CAP note)
        try:
            instincts = _load()
            fired = _fire(text, instincts, _damp_for(ev, data, text))
            fired = _augment_graph(text, data, instincts, fired)   # + graph-derived + forward-feed fires
        except Exception:
            # Exact owner constraints are an independent intake channel. A missing/corrupt TSV or any
            # ordinary-reflex failure must not erase an exact advisory that was already rendered.
            fired = []
        # habituation fix (2026-07-12, verified): drop a LOW-STAKES 'act'-about reflex (ps1_hook_edit,
        # utf8_text_surgery, heredoc, crlf, windows_backslash_path, hasty_pattern_match, ...) on a TRUE
        # read/prompt MENTION, so it re-fires fresh on the REAL act (a mention spends its 600s dedup window,
        # silencing the reflex when the act arrives). TWO recall guards, learned from the hole this caused:
        # (1) is_readonly_command(cmd) rejects a compound 'grep x && python bot.py' (proxy='read' but it RUNS);
        # (2) catastrophic/irreversible reflexes are NEVER suppressed. claim reflexes + any real write/act are
        # untouched. Fail-safe: any error -> fired unchanged (this is the every-tool-call path; never less).
        _drop_act = False   # set when the mention/prose filter removed act-scars; the refill below honours it
        try:
            _proxy = _act_proxy(ev, data)
            # 2026-07-28: proxy 'act' joins the candidates - a command that _READONLY's narrow prefix list
            # misses (a `python -c` analysis one-liner, a Get-Date chain) still gets the STRONG per-segment
            # is_readonly_command() validation below, which fails toward firing. This is the filter's own
            # expand_act=True reading (mention_surface), previously left unwired; measured 30% of all fires
            # were mention-surface, and each one spent the ack-gate deny budget on a non-act.
            if _proxy in ("read", "prompt", "act"):
                sys.path.insert(0, GF_DIR)
                import habituation_filter as _hf
                _cmd = ((data.get("tool_input", {}) or {}).get("command", "")) or ""
                if _proxy == "prompt" or _hf.is_readonly_command(_cmd):
                    _drop_act = True
                    for it in fired:
                        if _hf.danger_surface(it.get("id")) == "act" and it.get("id") not in _MENTION_NEVER:
                            _suppress(it.get("id"), "mention")
                    fired = [it for it in fired
                             if _hf.danger_surface(it.get("id")) != "act" or it.get("id") in _MENTION_NEVER]
            elif _proxy == "write":
                # 2026-09-02 (owner: fire less, be more confident): a command-hazard scar (surface 'act' -
                # pgrep/kill matching, heredoc surgery, CRLF iteration, backslash paths...) cannot apply to
                # WRITING PROSE - a memory, a report, a slide, a note - yet it fires whenever the prose
                # mentions its vocabulary. 32 hand-judged fires from one day: on Write/Edit calls 2 of 12
                # were on-domain (both on code), against 9 of 20 on commands; the three act-scar fires on
                # prose targets (an .md edit, an .md write, a .tex write) were all off-domain. A write of
                # code or a script keeps every reflex: the script may run the hazard. Catastrophic scars
                # stay regardless (secrets_in_git guards a memory write that embeds a token).
                sys.path.insert(0, GF_DIR)
                import habituation_filter as _hf
                _ti = data.get("tool_input", {}) or {}
                _path = str(_ti.get("file_path") or _ti.get("notebook_path") or "")
                if os.path.splitext(_path)[1].lower() in _PROSE_EXT:
                    _drop_act = True
                    for it in fired:
                        if _hf.danger_surface(it.get("id")) == "act" and it.get("id") not in _MENTION_NEVER:
                            _suppress(it.get("id"), "prose")
                    fired = [it for it in fired
                             if _hf.danger_surface(it.get("id")) != "act" or it.get("id") in _MENTION_NEVER]
        except Exception:
            pass
        # bare_open_reads_cp1252 off Windows (30 Oct): Python here decodes UTF-8, so the hazard is code that will also
        # run on Windows - the brain repos. Of 49 Mac fires to 11:0x none touched them (42 inline one-liners, 6 writes
        # elsewhere, 1 command that only ran a tool from brain-framework); judged on-domain 0 of 39.
        # Ranked hits count too: one held below the cap came back through the backoff refill at 11:42 (a9b28e2d, a
        # scratch .py write, instinct_refills.tsv "shown"); a 'platform' suppression is what the refill skips.
        # python_rewrite_of_a_text_file_without_newline joins it (30 Oct 21:2x, 1000-ways D7 #20): its hazard is text
        # mode writing CRLF on Windows. 39 Mac fires in 45 h, 4 naming a file that ships there; judged on-domain 0 of 33.
        try:
            for _rid in _SHIP_GATED:
                if os.name != "nt" and any(it.get("id") == _rid for it in list(fired) + list(_LAST_RANKED)):
                    if not _writes_shipped_code(data.get("tool_input", {}) or {}):
                        _suppress(_rid, "platform")
                        fired = [it for it in fired if it.get("id") != _rid]
        except Exception:
            pass
        try:
            if ev != "UserPromptSubmit":
                for rid in _PROMPT_ONLY:
                    if any(it.get("id") == rid for it in list(fired) + list(_LAST_RANKED)):
                        _suppress(rid, "event")
                        fired = [it for it in fired if it.get("id") != rid]
        except Exception:
            pass
        # Refill after the mention/prose filters (2026-09-02): _fire capped the ranked hits at MAX_FIRE before
        # those filters ran, so an act-scar could take a slot and then be removed, leaving a claim reflex that
        # deserved the slot unshown (seen on a prose write: one fire where two were due). Pull the next
        # ranked hits that pass the same filter and the precision gate; then log the cap ONCE, here.
        try:
            if _drop_act and len(fired) < MAX_FIRE:
                sys.path.insert(0, GF_DIR)
                import habituation_filter as _hf2
                have = set(it.get("id") for it in fired) | set(r for r, why in _SUPPRESSED if why in ("platform", "event"))
                for it in _LAST_RANKED:
                    if len(fired) >= MAX_FIRE:
                        break
                    rid = it.get("id")
                    if rid in have:
                        continue
                    if _hf2.danger_surface(rid) == "act" and rid not in _MENTION_NEVER:
                        continue
                    if _gated_by_precision(it):
                        continue
                    fired.append(it)
                    have.add(rid)
            shown = set(it.get("id") for it in fired)
            already = set(r for r, _ in _SUPPRESSED)
            for it in _LAST_RANKED:
                if it.get("id") not in shown and it.get("id") not in already:
                    _suppress(it.get("id"), "cap")
        except Exception:
            pass
        _held_primary = []
        try:
            _DEFER_BACKOFF[0] = True
            fired, _held_primary = _split_and_render(_receiver(data), fired, _drop_act)
            _log_suppressed(ev, data)   # what the gates silenced on this call (audit trail for the judge)
            ordinary_ctx = _render(fired)
            # The cap of two costs something on a report-writing action: a money write-up matched ten judged
            # scars and showed two (hand-judged 2026-09-02: 8 of 11 cap-held fires were on-domain). Name what
            # the cap held, names only, so the reader can recall any of them by name - the hint stays one line.
            held = [r for r, reason in _SUPPRESSED if reason == "cap"]
            if ordinary_ctx and held:
                more = (" +%d more" % (len(held) - 5)) if len(held) > 5 else ""
                ordinary_ctx += ("\n  (also matched, held by the cap of %d - names only, recall any by name: %s%s)"
                                 % (MAX_FIRE, ", ".join(held[:5]), more))
        except Exception:
            fired, ordinary_ctx = [], ""
            _discard_backoff()
        if not ordinary_ctx:
            fired = []
            _sf = _settle_backoff_when_silent(_held_primary)
            if _sf:   # a held-only call's backoff write failed: record it as the shown path does (dot review, C)
                _note_emit_failure(_sf, data.get("session_id", ""), [it.get("id", "") for it in _held_primary])
        if not ordinary_ctx and not exact_ctx:
            if not _exact_name_line(data, text):
                return  # silent - no new ordinary match, no exact owner-clause advisory and no exact-name card
        ordinary_ctx = chr(10).join(p for p in (ordinary_ctx, _exact_name_line(data, text)) if p)
        ctx = "\n".join(part for part in (exact_ctx, ordinary_ctx) if part)
        if ev == "PreToolUse":
            # "allow" here is SAFE alongside the deny hooks that run in the same chain (danger_mode.py,
            # humility_check.py): Claude Code combines PreToolUse decisions by "most restrictive wins,
            # order deny > defer > ask > allow", so an "allow" can never defeat or short-circuit a later
            # "deny" (verified against the hooks docs 2026-07-08). Keep the field - omitting it is
            # undocumented territory; this priming hook explicitly assents and lets the guards tighten.
            # HEED GATE (graph_fire/heed_gate.py): converts the ONE right fire into a one-time deny speed
            # bump instead of another ignorable line - ONLY when a real ACT (never a read/mention) fires
            # the top charge>=0.95 ACT-HAZARD scar already read past >=2x this session AND the command is
            # evidence-linked (E>=0.60) to a real recent outcomes.jsonl failure. Caps: once per
            # (session,reflex), 2/session, 20min fleet gap; the claim persists BEFORE the deny so a re-run
            # is never blocked twice. SHADOW (log-only, evaluate()->None) unless graph_fire/HEED_ENABLED
            # exists -> in shadow this dict is byte-identical to before. Fail-safe: any error -> allow.
            _decision, _reason = "allow", None
            try:
                sys.path.insert(0, GF_DIR)
                import heed_gate as _hg
                _h = _hg.evaluate(ev, data, fired)
                if _h and _h.get("decision") == "deny" and _h.get("reason"):
                    _decision, _reason = "deny", str(_h["reason"])[:1200]
            except Exception:
                _decision, _reason = "allow", None
            if _decision == "allow":
                # ACK GATE (2026-07-25): one-time acknowledgement-token gate intended to prompt
                # explicit engagement. A valid retry prevents this gate from denying the key again;
                # other hooks, tool execution, and outcome verification remain separate. ack_log.tsv
                # is best-effort registration telemetry, not semantic heeding proof. SHADOW unless
                # graph_fire/ACK_ENABLED exists; claim-before-deny; fail-safe: any error -> allow.
                try:
                    import ack_gate as _ag
                    _a = _ag.evaluate(ev, data, fired)
                    if _a and _a.get("decision") == "deny" and _a.get("reason"):
                        _decision, _reason = "deny", str(_a["reason"])[:1200]
                except Exception:
                    pass
            _hso = {"hookEventName": "PreToolUse", "permissionDecision": _decision,
                    "additionalContext": ctx}
            if _reason:
                _hso["permissionDecisionReason"] = _reason
            out = {"hookSpecificOutput": _hso}
        else:
            out = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                          "additionalContext": ctx}}
        sys.stdout.write(json.dumps(out))
        sys.stdout.flush()
        # The context is out: now the fire log, then the backoff and the refill rows (see _DEFER_BACKOFF). An ordinary
        # reflex may co-fire with an exact owner-clause advisory; _firelog_data redacts the prompt so the fire log
        # does not keep its first 120 characters. The fire log goes first (29 Sep, account04/B04 r5): if the process
        # dies between the two, the fire is on record and may repeat before its window, which shows in the log,
        # rather than being held off with no record of having fired. A write that fails is named in
        # instinct_emit_errors.tsv instead of vanishing.
        failed = []
        if fired and not _log_fires(ev, _firelog_data(ev, data, exact_ctx), fired):
            failed.append("fire_log")
        failed += _commit_backoff()
        if failed:
            _note_emit_failure(failed, data.get("session_id", ""), [it.get("id", "") for it in fired])
    except Exception:
        _discard_backoff()
        return  # any failure -> silent, never block a session


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print("usage: instinct_prime.py   (hook: reads the event JSON on stdin; help is inert)")
        raise SystemExit(0)
    try:
        import os as _o, sys as _s
        _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
        import brain_switch as _bsw
        _bsw.gate()
    except SystemExit:
        raise
    except Exception:
        pass
    try:
        main()
    finally:
        _log_slow_call()   # explicit as well as atexit: a direct run was found not to reach the atexit handler
