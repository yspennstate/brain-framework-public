#!/usr/bin/env python3
"""habituation_filter.py - DRAFT (not wired live). The habituation-wall fix, as pure functions.

Problem (measured 2026-07-12 on instinct_fires.tsv, 5783 fires / ~30h): detection is broad - a reflex
fires before nearly every mistake - but the outcome is READ PAST, because the generic/act-about reflexes
fire on ~everything and become noise. ~30% of all fires land on a MENTION surface (a grep/cat/python
analysis that merely names scar vocabulary), not an ACT on the guarded target. A warning that fires on a
mention teaches its reader to click past it, and then the real fire is lost in the wall.

Three mechanisms, in order of measured leverage:

 (a) ACT-vs-MENTION suppression. Each reflex is tagged danger_surface = 'act' | 'claim'.
     'act' reflexes are dangerous only at a write/run/deploy (editing a .ps1, committing a secret,
     RUNNING a local bot, iterating a file cross-OS). A read/prompt/analysis that merely names their
     vocabulary is a FALSE fire - and, worse, it spends the 600s dedup window so the reflex is silent
     when the REAL act arrives. We drop an 'act' reflex on a mention surface, BEFORE dedup, so the real
     act (write / non-readonly command) re-fires it fresh. 'claim' reflexes (a sim number, a risk
     figure, a confident conclusion, the measured record's sign) live in the reasoning itself and are NEVER
     suppressed - they SHOULD fire on a read/prompt. This split is the robust specificity signal; it is
     NOT frequency (the coarseness lesson, mention_vs_act_too_coarse_2026_07_12: the domain overrides
     fire on off-domain acts too, so act% != targeting% - never rank them by mention/fire frequency).

 (b) IDF down-weight (bounded). Rank co-firing reflexes with a small IDF penalty on the ranking charge,
     so a broad low-information reflex is demoted WITHIN its charge band and a rarer, more specific
     co-firing reflex leads. BOUNDED so it can never flip a charge tier - a maximal safety/override scar
     (charge >= 0.95) can never be displaced from the top-3 by a lower-charge generic. On real act/write
     surfaces ranking stays charge-first (status quo); the penalty only reorders the display lead.

 (c) Escalation cap. The '[!] REPEATED xN' mindware-override escalation must be attached to at most the
     SINGLE most-read-past reflex across ALL reflexes per render - never one per reflex, which would
     rebuild the very wall the escalation exists to pierce. (This already ships in instinct_prime._render
     via _top_esc; the helper here re-expresses it so the patch-spec is self-contained and testable.)

Fail-safe by construction: every entry point swallows exceptions and returns the INPUT unchanged (more
firing, never less) - this is the every-tool-call path. Stdlib only, forward-slash paths.
"""
import re

# ---------------------------------------------------------------------------
# (a) danger_surface classification.
#   act   -> dangerous only at a write/run/deploy; a read/prompt/analysis MENTION is a false fire, and
#            the real act re-fires it. Suppress on mention surfaces.
#   claim -> the danger is a number / conclusion / reasoning that legitimately appears in a read or a
#            prompt (a sim result, a risk figure, a confident 'done', the measured record's sign). NEVER suppress.
#            Domain overrides (sim_flatters, risk_vs_measured_record, leveraged_etf_is_not_linear, ...) are 'claim'
#            deliberately - the coarseness memory proved their act% is not targeting; do not touch them here.
# Anything unlisted defaults to 'claim' (conservative: an unknown reflex is never suppressed).
# ---------------------------------------------------------------------------
DANGER_SURFACE = {
    # --- act-about (suppress on a mention surface) ---
    "ps1_hook_edit": "act",                       # editing a .ps1 / hook / settings.json
    "utf8_text_surgery": "act",                   # PowerShell -Encoding write on a memory file
    "heredoc_verbatim_surgery": "act",            # verbatim string surgery WRITE
    "crlf_cross_os_parse": "act",                 # iterating/hashing a file cross-OS
    "windows_backslash_path": "act",              # a backslash path baked into code being written
    "hasty_pattern_match": "act",                 # archive/delete/move a target
    "secrets_in_git": "act",                      # git add/commit/push a secret
    # 2026-09-13: this one was MISSING and therefore defaulted to "claim", so the ack gate - which covers
    # act-surface hazards only - could never reach it. Measured that day: the reflex fired FIVE times on act
    # surfaces while every one of 37 pushes was written `scan | tail -1 && push`, testing tail's exit status
    # instead of the scanner's, and neither gate denied once (heed_log had zero rows all day, because heed_gate
    # also needs the command linked to a REAL recent failure and a mistake that fails OPEN never writes one).
    # It belongs here on its own terms: the fault changes what RUNS - a push proceeds when its guard did not
    # pass - and every sibling in this act block is the same command-surface family (CRLF, heredoc, backslash
    # paths, UTF-8 surgery). Charge is already 1.00, the same as those siblings, so this entry is the only
    # thing that was keeping the gate out. Cost is bounded by the gate's own caps: one deny per session.
    "a_pipe_reports_the_filters_verdict": "act",   # a guard's verdict sitting upstream of a filter in an acting line
    "windowless": "act",                          # launching a background process/task
    "concurrent_races": "act",                    # push/force/robocopy shared state
    "premortem": "act",                           # rm -rf / force-push / deploy / migrate
    "heavy_compute_remote": "act",                 # running a heavy/GPU fit locally
    "no_local_privileged_stack": "act",                    # RUNNING a local privileged/approval process (edit is safe)
    "preserve_before_destroy": "act",             # redeploy / wipe / rotate persistent state
    "figure_filename_clobber": "act",             # savefig onto a claimed generic path
    "pinned_file_two_tripwires": "act",           # editing/swapping a pinned guarded file
    "gate_tool_needs_env": "act",                 # running a gate/broker tool by hand on the box
    "his_desktop_is_not_an_automation_surface": "act",  # SendKeys / focus-steal into his session
    "healer_first_do_no_harm": "act",             # deploying/running a watcher that acts
    "stamp_only_on_delivery": "act",              # a sender that stamps dedup state (code write)
    # --- claim (never suppress; danger lives in the reasoning/number) ---
    "sim_flatters": "claim", "risk_vs_measured_record": "claim", "sim_sign_vs_lived_nav": "claim",
    "leveraged_etf_is_not_linear": "claim", "vxn_long_tenor_state": "claim", "lookahead": "claim",
    "stale_mark_banks": "claim", "ib_marks": "claim", "broker_truth": "claim",
    "his_hand_in_the_tape": "claim", "conventions_are_read_not_guessed": "claim",
    "fixed_sides_only": "claim", "green_not_trusted": "claim", "done_means_verified": "claim",
    "he_will_look": "claim", "work_the_clock": "claim", "keep_working": "claim",
    "high_exact_answer_gate": "claim", "first_wall_is_not_impossibility": "claim",
    "voice_human": "claim", "verify_adversarially": "claim", "steelman_opposite": "claim",
    "disconfirm": "claim", "outside_view": "claim", "false_independence": "claim",
    "reach_across_domains": "claim", "dont_extrapolate_past_range": "claim",
    "measure_one_before_promising_n": "claim", "stat_before_you_stake": "claim",
    "confirm_the_target": "claim", "restate_the_ask": "claim", "the_default_is_not_his": "claim",
    "his_never_is_machinery": "claim", "his_call_is_final": "claim", "compute_distress": "claim",
    "heavy_compute_owner_first": "claim", "check_the_brain_first": "claim",
    "verify_worker_model": "claim", "audit_mode_restraint": "claim", "ib_model_mark": "claim",
    "build_on_his_harness": "claim", "verify_the_correction_not_just_the_claim": "claim",
}

# The broad, domain-agnostic METHOD generics that fire on confident/hasty LANGUAGE (not domain vocab).
# These are the ones the escalation and the top-3 crowding are mostly made of; (b) demotes them so a
# co-firing specific leads. They are 'claim' (never suppressed) - only down-weighted in ranking.
GENERIC_LANGUAGE = frozenset({
    "verify_adversarially", "steelman_opposite", "disconfirm", "outside_view",
    "false_independence", "reach_across_domains",
})


def danger_surface(reflex_id):
    return DANGER_SURFACE.get(reflex_id, "claim")


# ---------------------------------------------------------------------------
# Mention-surface detection.
# A write is never a mention. read/prompt are mentions. An 'act' (bash command) is a mention ONLY when it
# is confirmed read-only: it carries NO write signature AND affirmatively looks like a read/analysis shape.
# Two-sided and conservative - when unsure, treat as an ACT (fire), never suppress. This is the recall
# guard the coarseness memory demands.
# ---------------------------------------------------------------------------
# Any of these in the command body means it can mutate state -> NOT read-only (fail toward firing).
_WRITE_SIG = re.compile(
    r"open\([^)]*,\s*['\"][^'\")]*[wax+]|\.write\(|\.writelines|\.truncate\(|os\.remove|os\.unlink|os\.rename|"
    r"os\.replace|os\.mkdir|shutil\.|subprocess|Popen|os\.system|os\.exec|savefig|\.to_csv|\.to_parquet|"
    r"\.to_pickle|\.dump\(|json\.dump\b|pickle\.dump|\.savez|np\.save|system\(|\.run\(|\.call\(|popen|"
    r"__import__|\bexec\(|\beval\(|\.unlink\(|\.rmdir\(|\.mkdir\(|getattr\(|>>|(?<![-0-9<>=])>(?![=>])|\btee\b|"
    r"Set-Content|Out-File|Add-Content|Remove-Item|New-Item|Register-|Start-Process|Stop-Process|"
    r"\bgit\s+(add|commit|push|reset|checkout|rm|merge|rebase|tag|branch\s+-|clean)|\brm\s+-|\bmv\b|\bcp\b|"
    r"\bmkdir\b|\btouch\b|\bpip\s+install|\bnpm\s+(i|install|run)|dos2unix|\bchmod\b|\bchown\b|\bcurl\b|"
    r"\bwget\b|\bdd\b|\bsed\s+-i|robocopy|/MIR|--force|-Recurse|brain\.py\s+remember|agent_mesh\.py\s+"
    r"(claim|hello|handoff|bye|pulse)|\.remember\(",
    re.I)
# Note the `-` added to the redirect lookbehind: it excludes the arrow `->` (common in analysis prints /
# comments) from looking like a shell redirect `> file`, while a real `echo x > file` (space before `>`)
# still trips it. A false positive here only costs REDUCTION, never recall (a read wrongly called a write
# just keeps firing) - so WRITE_SIG stays deliberately greedy.

# Affirmative NON-python read/analysis shapes (after an optional leading `cd ... &&`).
_READ_SHAPE = re.compile(
    r"^\s*(grep|rg|cat|less|head|tail|ls|find|wc|awk|sed\s+-n|sort|uniq|column|jq|stat|file|diff|nl|cut|"
    r"tr\b|comm|paste|xxd|od|echo|printf|type|Get-Content|Select-String|Get-ChildItem|Get-Date|Get-Item\b|"
    r"Measure-Object|Where-Object|Select-Object|Format-|Import-Csv|Test-Path|git\s+(log|status|diff|show|"
    r"blame|ls-files|rev-parse|config\s+--get|for-each-ref))\b",
    re.I)
# python is read-only ONLY when its body is VISIBLE (`-c` inline, or `- <<HEREDOC` stdin) so WRITE_SIG
# could actually scan it, OR when it is a narrow read-only tool name. A bare `python foo.py` / `python -m X`
# is OPAQUE - it can start a bot, launch a fit, mutate anything - so it is treated as a real ACT (fires).
# This is the recall guard: `python approval_broker.py` must NEVER be classified a mention.
_PY_HEAD = re.compile(r"^\s*(python[0-9.]*|py)\b(.*)$", re.I | re.S)
_PY_VISIBLE_BODY = re.compile(r"^\s*(-c\b|-u\b\s*-c\b|-\s*<<|<<)", re.I)
_PY_READONLY_NAME = re.compile(r"(recall|status|audit|eval|check|_test|probe|report|inspect|analy[sz])", re.I)
_LEAD_CD = re.compile(r"^\s*cd\s+\S+\s*(&&|;)\s*", re.I)


_CD_ONLY = re.compile(r"^\s*cd\s+\S+\s*$", re.I)
_SEG_SPLIT = re.compile(r"&&|\|\||;|\|")


def _py_readonly(rest):
    """Given the text AFTER `python`, is this a VISIBLE-body inline/heredoc run or a narrow read-only
    tool? (WRITE_SIG has already been scanned on the whole command by the caller.)"""
    return bool(_PY_VISIBLE_BODY.match(rest) or _PY_READONLY_NAME.search(rest))


def _segment_readonly(seg):
    """One shell segment (already WRITE_SIG-clean at the whole-command level): a bare `cd`, a read/analysis
    command, or a VISIBLE-body python. An opaque `python foo.py` / `python -m X` is NOT read-only."""
    seg = seg.strip()
    if not seg or _CD_ONLY.match(seg):
        return True                          # empty or a bare navigation is benign
    mpy = _PY_HEAD.match(seg)
    if mpy:
        return _py_readonly(mpy.group(2))    # opaque `python foo.py` -> False (fires)
    return bool(_READ_SHAPE.match(seg))


def is_readonly_command(command):
    """Conservative: does this bash/PowerShell command only READ/analyze (a mention), with no way to
    mutate state? Fail toward NOT read-only (i.e. fire) on ANY doubt. Every segment of a &&/;/| chain must
    be read-only - `cat x && python bot.py` is an ACT (the recall bug the unit test caught). Pure, fail-safe."""
    try:
        if not command:
            return False
        b = command
        if _WRITE_SIG.search(b):
            return False                     # any mutation signature anywhere -> real act (fire)
        if "<<" in b:
            # heredoc: the body was already WRITE_SIG-scanned; only the head before `<<` decides the shape
            # (`python - <<PY ...`, `cat <<EOF`). Do not split on separators inside the heredoc body.
            head = _LEAD_CD.sub("", b.split("<<", 1)[0].strip(), count=1).strip()
            mpy = _PY_HEAD.match(head)
            if mpy:
                tail = mpy.group(2).strip()
                if not re.sub(r"[-u\s]", "", tail):     # only `- / -u / -u -` before `<<` = stdin heredoc
                    return True                          # body feeds stdin, already WRITE_SIG-clean
                return _py_readonly(mpy.group(2))        # `python analyze.py <<X` stays opaque -> fires
            return _segment_readonly(head)
        # no heredoc: require EVERY top-level segment to be read-only
        return all(_segment_readonly(s) for s in _SEG_SPLIT.split(b))
    except Exception:
        return False


def mention_surface(proxy, command="", expand_act=True):
    """Is this fire on a MENTION surface (a place an 'act' reflex must not fire)?
      write            -> False (a real mutation)
      read / prompt    -> True
      act              -> True only if the command is confirmed read-only AND expand_act is on.
    expand_act=False gives the SAFE literal reading (read+prompt only); True catches the mis-proxied
    read-only analysis commands that are the bulk of the wall. The integrator chooses."""
    try:
        if proxy in ("read", "prompt"):
            return True
        if proxy == "act" and expand_act:
            return is_readonly_command(command)
        return False
    except Exception:
        return False


def suppress_on_mention(reflex_id, proxy, command="", expand_act=True):
    """(a) True => drop this fire. An 'act'-about reflex on a mention surface is a false fire; the real
    act will re-fire it. 'claim' reflexes are never suppressed."""
    try:
        return danger_surface(reflex_id) == "act" and mention_surface(proxy, command, expand_act)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# (b) Bounded IDF down-weight. rank_charge = charge - LAMBDA * (1 - idf). LAMBDA is small enough that the
# penalty (<= LAMBDA) can never move a reflex across a charge tier: a charge>=0.95 scar stays above any
# charge<=0.82 reflex for LAMBDA <= 0.13 (0.95 - 0.13 = 0.82). So a maximal safety/override scar can never
# be displaced from the top-3 by a low-charge generic. On a mention surface the generics get an extra
# demotion so the specific claim leads; on act/write, charge still dominates.
# ---------------------------------------------------------------------------
LAMBDA_IDF = 0.12          # bounded: < 0.13, so charge tiers >=0.95 vs <=0.82 never invert
MENTION_GENERIC_EXTRA = 0.20  # extra demotion for GENERIC_LANGUAGE reflexes on a mention surface only


def rank_key(reflex_id, charge, idf, count=1, on_mention=False):
    """Sort key (descending) for the display order. charge-dominant, IDF-adjusted, generic-demoted on
    mentions. Returns a tuple so ties fall back to raw charge then count (stable, never raises)."""
    try:
        c = float(charge)
    except Exception:
        c = 0.6
    try:
        f = float(idf)
    except Exception:
        f = 0.5
    adj = c - LAMBDA_IDF * (1.0 - f)
    if on_mention and reflex_id in GENERIC_LANGUAGE:
        adj -= MENTION_GENERIC_EXTRA
    return (round(adj, 4), round(c, 4), int(count) if count else 1)


# ---------------------------------------------------------------------------
# (c) Escalation cap: at most ONE '[!] REPEATED xN' per render, on the single most-read-past high-stakes
# reflex across ALL fired reflexes. (Ships live in instinct_prime._render as _top_esc; re-expressed for a
# self-contained patch-spec + test.)
# ---------------------------------------------------------------------------
def top_escalation(fired, min_count=3, min_charge=0.95):
    """Return the id of the single reflex to escalate this render, or None. Fail-safe -> None."""
    try:
        elig = [it for it in fired
                if int(it.get("_count", 1) or 1) >= min_count and float(it.get("charge", 0) or 0) >= min_charge]
        if not elig:
            return None
        return max(elig, key=lambda it: int(it.get("_count", 1) or 1)).get("id")
    except Exception:
        return None
