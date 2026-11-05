#!/usr/bin/env python3
"""heed_weekly_report.py - warning-interaction telemetry, delivered on a cadence.

The 07-28 finding behind this: every heeding metric already existed (ack_log, heed_log,
heed_trend, the report-gate shadow rows) and none of them reached the owner - "a fire-rate
dashboard can look healthy while every fire gets skimmed" cuts both ways: a heed record
nobody reads changes nothing. This writes a short dated report with mechanically
defensible layers: unique deny keys, matching token-registration receipts,
action-class associations in the small scored Claude subset, report-gate blocks,
durable-ledger entries, and retrieval-spine freshness. A token receipt is not
proof of comprehension, execution, safe outcome, or causal heeding; missing
receipt is outcome unknown.
Sections fail soft - a broken input becomes a line saying so, never a dead report.

Scheduled weekly (BrainHeedReport, Sunday 17:05, VBS SW_HIDE). Manual: python heed_weekly_report.py
"""
import glob
import io
import json
import os
import re
import subprocess
import sys
import time


def _brain_slug_resolve():
    """The memory corpus this install belongs to.

    Order matters. $BRAIN_SLUG wins so a caller can point one script at another corpus.
    Then BRAIN_SLUG beside this file, which bootstrap/install.py writes with the slug of the
    --project-dir it was given; without it an install whose project directory is not the home
    directory builds a corpus nothing ever reads. The home-derived value stays last so a tree
    that works today keeps working untouched.
    """
    env = os.environ.get("BRAIN_SLUG", "").strip()
    if env:
        return env
    try:
        marker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "BRAIN_SLUG")
        with open(marker, encoding="utf-8") as fh:
            named = fh.read().strip()
        if named:
            return named
    except OSError:
        pass
    home = os.path.expanduser("~")
    return home.replace(":", "-").replace(chr(92), "-").replace("/", "-")


D = os.path.dirname(os.path.abspath(__file__))
GF = os.path.join(D, "graph_fire")
# Claude Code names a project directory after the home path, with ':' and the
# separators replaced by '-'. Deriving it keeps this tree free of one machine's
# username and correct on any other - and unlike a template token it is a real
# value at import time.
_BRAIN_SLUG = _brain_slug_resolve()
MEM = os.path.join(os.path.expanduser("~"), ".claude", "projects", _BRAIN_SLUG, "memory")
OUT_DIR = os.path.expanduser(r"~/Downloads")
WINDOW_S = 7 * 86400


def _rows(path):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return [ln.rstrip("\n").split("\t") for ln in f if ln.strip()]


def _ts(row):
    try:
        return float(row[0])
    except Exception:
        return 0.0


def section_deny_gates(now):
    try:
        ack_source = _rows(os.path.join(GF, "ack_log.tsv"))
    except Exception as e:
        return [
            "Ack-gate interaction receipts (7d): DATA INCOMPLETE; ack_log.tsv unreadable (%s)."
            % type(e).__name__
        ]
    ack = [r for r in ack_source if len(r) > 4 and now - _ts(r) < WINDOW_S]
    heed_error = None
    try:
        heed_source = _rows(os.path.join(GF, "heed_log.tsv"))
    except Exception as e:
        heed_source = []
        heed_error = type(e).__name__
    heed = [r for r in heed_source if len(r) > 4 and now - _ts(r) < WINDOW_S]
    denies = [r for r in ack if r[4] == "deny" and r[3] == "live"]
    acks = [r for r in ack if r[4] == "ack" and r[3] == "live"]
    deny_keys = {(r[1], r[2]) for r in denies}
    ack_keys = {(r[1], r[2]) for r in acks}
    matched = deny_keys & ack_keys
    missing = deny_keys - ack_keys
    orphan_acks = ack_keys - deny_keys
    per = {}
    for _session, reflex in deny_keys:
        per[reflex] = per.get(reflex, 0) + 1
    top = ", ".join("%s x%d" % (k, v) for k, v in sorted(per.items(), key=lambda t: -t[1])[:5]) or "none"
    hd = [r for r in heed if r[5] == "deny"] if heed and len(heed[0]) > 5 else []
    escalation = (
        str(len(hd)) if heed_error is None
        else "DATA INCOMPLETE (heed_log.tsv unreadable: %s)" % heed_error
    )
    lines = [
        "Ack-gate interaction receipts (7d): unique live denied keys %d; matching live"
        % len(deny_keys),
        "token-registration receipts %d; no matching token receipt %d; live receipt keys"
        % (len(matched), len(missing)),
        "without a same-window deny %d. Recorded heed-gate escalation denies: %s."
        % (len(orphan_acks), escalation),
        "A token receipt proves mechanical registration only. Missing receipt is OUTCOME UNKNOWN:",
        "work may have been abandoned, blocked elsewhere, retried without a registered token, or lost",
        "from the best-effort log. Execution, rationale quality, and outcome correctness are not measured.",
        "Top unique denied scars: %s." % top,
    ]
    return lines


def section_advisory(now):
    try:
        last = None
        with io.open(os.path.join(D, "heed_trend.jsonl"), encoding="utf-8", errors="replace") as f:
            for ln in f:
                if ln.strip():
                    last = json.loads(ln)
        if not last:
            return ["Advisory action-class association: no heed_trend data."]
        bits = []
        scored = 0
        for k, v in (last.get("stats") or {}).items():
            fired = int(v.get("fired", 0))
            hit = int(v.get("heeded", 0))
            ctrl_n = int(v.get("ctrl_n", 0))
            ctrl_hit = int(v.get("ctrl_hit", 0))
            scored += fired
            bits.append("%s after %d/%d (%.0f%%), control %d/%d (%.0f%%)"
                        % (k, hit, fired, 100.0 * hit / max(1, fired),
                           ctrl_hit, ctrl_n, 100.0 * ctrl_hit / max(1, ctrl_n)))
        eligible = int(last.get("n_fires", scored))
        return [
            "Advisory action-class association (Claude transcripts; four reflexes; not causal heeding):",
            "eligible fires %d; scored fires %d; unscored/excluded %d; last row %s."
            % (eligible, scored, max(0, eligible - scored), last.get("ran_at", "?")),
            "; ".join(bits) + ".",
        ]
    except Exception as e:
        return ["Advisory action-class association: unreadable (%s)." % e]


def section_report_gate(now):
    try:
        source = _rows(os.path.join(GF, "gf_shadow_fires.tsv"))
    except Exception as e:
        return [
            "Report gate (7d): DATA INCOMPLETE; gf_shadow_fires.tsv unreadable (%s)."
            % type(e).__name__
        ]
    rows = [r for r in source if len(r) > 2 and r[1] == "STOP" and now - _ts(r) < WINDOW_S]
    blocks = [r for r in rows if r[2] == "block"]
    would = [r for r in rows if r[2] == "would-block"]
    out = ["Report gate (7d): %d stop-checks, %d real blocks, %d would-blocks." % (len(rows), len(blocks), len(would))]
    for r in blocks[-3:]:
        out.append("  blocked: " + (r[4] if len(r) > 4 else "")[:90])
    return out


def section_corrections(now):
    try:
        t = io.open(os.path.join(MEM, "MISTAKES.md"), encoding="utf-8", errors="replace").read()
        cutoff = time.localtime(now - WINDOW_S)
        recent = 0
        for m in re.finditer(r"\*\*(2026-\d\d-\d\d)", t):
            if time.strptime(m.group(1), "%Y-%m-%d") >= cutoff:
                recent += 1
        lines = [
            "Durable MISTAKES.md entries dated in the last 7d: %d (ledger count only;" % recent,
            "correction-ingestion and runtime coverage are not established by this number).",
        ]
        pj = os.path.join(MEM, "prompts", "PROMPTS.jsonl")
        if os.path.exists(pj):
            age_h = (now - os.path.getmtime(pj)) / 3600
            if age_h > 24:
                lines.append("  CAVEAT: the prompt archiver is %.0fh stale, so correction harvesting" % age_h)
                lines.append("  undercounts - fix mesh #289 before trusting a low number here.")
        return lines
    except Exception as e:
        return ["Durable correction ledger: unreadable (%s)." % e]


def section_retrieval():
    try:
        p = subprocess.run([sys.executable, os.path.join(D, "embedding_coverage.py")],
                           capture_output=True, text=True, timeout=120)
        txt = (p.stdout or "") + (p.stderr or "")
        first = next((l for l in txt.splitlines() if "embedding_coverage" in l), "?")
        verdict = next((l.strip() for l in txt.splitlines() if "verdict" in l), "?")
        return ["Retrieval spine: %s | %s" % (first.strip()[:90], verdict)]
    except Exception as e:
        return ["Retrieval spine: check failed (%s)." % e]


def conclusion_lines():
    return [
        "What this can support: token-answer coverage and a limited action-class association.",
        "It does not establish that a warning was understood, that the intended action executed,",
        "that the outcome was correct, or that the warning caused it. Those require a joined",
        "generation -> delivery -> response -> tool result -> independently verified outcome receipt.",
    ]


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    now = time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    parts = ["# Brain warning-interaction report, week ending %s" % day, ""]
    parts += section_deny_gates(now) + [""]
    parts += section_advisory(now) + [""]
    parts += section_report_gate(now) + [""]
    parts += section_corrections(now) + [""]
    parts += section_retrieval() + [""]
    parts += conclusion_lines()
    out = os.path.join(OUT_DIR, "brain_heed_report_%s.md" % day)
    with io.open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(parts) + "\n")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
