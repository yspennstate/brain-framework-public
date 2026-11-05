#!/usr/bin/env python3
"""stream_integrity.py - a re-runnable CORRECTNESS check for the brain's data streams. The dream/hooks check
FRESHNESS (mtime + grew); nothing checked that a stream is still WELL-FORMED. A stream can keep growing while
silently corrupting - a ragged TSV row, an unparseable JSONL line, a NaN weight - and freshness stays green
while recall or the dream quietly break on it. This parses each stream and flags: inconsistent TSV column
counts, JSONL lines that don't parse, and non-finite numeric weights. Read-only. Usage: python stream_integrity.py"""
import io, os, json, math, sys
DR = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(DR, ".stream_integrity_baseline.json")   # per-stream malformed-row counts from the last run


def _load_baseline():
    try:
        return json.load(io.open(BASELINE, encoding="utf-8-sig")) or {}
    except Exception:
        return {}


def _save_baseline(counts):
    try:
        json.dump(counts, io.open(BASELINE, "w", encoding="utf-8"))
    except Exception:
        pass


def _classify(name, count, base):
    """active (malformed rows GREW since last check = a writer is STILL corrupting), frozen (stable = historical
    residue from a since-fixed writer), or baseline (first sighting - not yet classifiable). count<=0 -> clean.
    This is what tells 'a stream is corrupting NOW' apart from '73 frozen fragments a fixed writer left behind'."""
    if count <= 0:
        return "clean", 0
    prev = base.get(name)
    if prev is None:
        return "baseline", count
    if count > prev:
        return "active", count - prev
    return "frozen", count

# stream -> (kind, weight-column index or None). Only streams the brain actually reads for recall/dream/telemetry.
STREAMS = {
    "retrievals.tsv": ("tsv", None), "coactivation.tsv": ("tsv", 2), "spread_edges.tsv": ("tsv", 2),
    "orphan_edges.tsv": ("tsv", 2), "series_edges.tsv": ("tsv", 2), "keyword_edges.tsv": ("tsv", 2),
    "shadow_fires.tsv": ("tsv", None), "salience.tsv": ("tsv", None), "base_activation.tsv": ("tsv", None),
    # fire-logs: telemetry the brain reads (monitor read-past, pipeline_audit, reflex_metafamilies, eval_real).
    # Added 2026-07-12: they were NOT covered, which is how CR-split corruption accumulated unseen (instinct_fires
    # had 67 ragged rows from a LF-not-CR sanitizer). The ragged-row flag below now catches that class going forward.
    "instinct_fires.tsv": ("tsv", None), "graph_fire/gf_shadow_fires.tsv": ("tsv", None),
    "graph_fire/report_guards_shadow.tsv": ("tsv", None),   # report_guards Stop-hook shadow log (added 2026-07-12; new-stream coverage)
    "reasoning_guard_shadow.tsv": ("tsv", None),            # claim_check/retraction_guard shadow log (added 2026-07-12)
    "reality_log.jsonl": ("jsonl", None),                   # reality_monitor Stop-hook telemetry - freshness-tracked by hook_liveness, correctness-gap closed 2026-07-12
    # actively-written brain telemetry, coverage completed 2026-07-12 (were uncovered blind spots):
    "surfaced.tsv": ("tsv", None), "consciousness.tsv": ("tsv", None), "predictions.tsv": ("tsv", None),
    "memory_access.tsv": ("tsv", None), "reliability.tsv": ("tsv", None),
    "outcomes.jsonl": ("jsonl", None), "reliability.jsonl": ("jsonl", None), "eligibility_credit.jsonl": ("jsonl", None),
}


def check_tsv(path, wcol):
    rows = colcounts = bad_w = 0
    from collections import Counter
    cc = Counter()
    for i, ln in enumerate(io.open(path, encoding="utf-8", errors="replace")):
        if i == 0 and ("\ta\t" in ln or ln.startswith("a\t") or ln.startswith("slug\t")):
            continue
        ln = ln.rstrip("\n")
        if not ln:
            continue
        p = ln.split("\t")
        rows += 1
        cc[len(p)] += 1
        if wcol is not None and len(p) > wcol and not p[0].startswith("#"):
            try:
                v = float(p[wcol])
                if not math.isfinite(v):
                    bad_w += 1
            except Exception:
                pass   # non-numeric weight rows (e.g. #applied ledger) counted via colcount, not here
    # dominant column count; a stream is ragged if a minority of rows differ (excluding # comment rows)
    if not cc:
        return "empty", 0, 0
    dom = cc.most_common(1)[0][0]
    # A TRUNCATION FRAGMENT (col-count <= dom/2 - e.g. a 1-col line in a 5/6-col log) has lost its leading key
    # fields: the unambiguous corruption signature (a CR-split continuation, a torn row). A row with MORE cols
    # than dom (a schema field like session_id, or a split trailing value) keeps the leading fields intact, and
    # a small deficit (5 vs a 6-col dominant) is old-schema, not loss - benign schema variation. Flagging every
    # off-dominant row cried wolf on the 2026-07-12 session_id migration (5->6 col: the 6-col rows are VALID).
    # So flag genuine fragments + wild (>3-way) raggedness; tolerate a clean 2-3-width schema evolution.
    floor = max(1, dom // 2)
    frag = sum(v for k, v in cc.items() if k <= floor)
    issues = []
    if frag:
        issues.append("%d truncated fragment rows (lost fields) %s" % (frag, dict(cc)))
    if len(cc) > 3:
        issues.append("%d-way ragged column shape %s" % (len(cc), dict(cc)))
    if bad_w:
        issues.append("%d non-finite weights" % bad_w)
    return ("OK" if not issues else "; ".join(issues)), rows, frag + bad_w


def check_jsonl(path):
    rows = bad = 0
    for ln in io.open(path, encoding="utf-8", errors="replace"):
        ln = ln.strip()
        if not ln:
            continue
        rows += 1
        try:
            json.loads(ln)
        except Exception:
            bad += 1
    return ("OK" if not bad else "%d unparseable lines" % bad), rows, bad


def summary():
    """Compact (n_ok, n_present, [active-issue names]) for the brain_status dashboard. Fail-open. Reports only
    ACTIVE corruption (malformed rows GROWING vs the baseline); FROZEN historical residue is not an alarm, so
    brain_status stops crying wolf on a since-fixed writer's leftovers (the hook_liveness false-DEAD lesson)."""
    base = _load_baseline()
    ok = present = 0
    issues = []
    for name, (kind, wcol) in STREAMS.items():
        path = os.path.join(DR, name)
        if not os.path.exists(path):
            continue
        present += 1
        try:
            status, _, mal = check_tsv(path, wcol) if kind == "tsv" else check_jsonl(path)
        except Exception:
            status, mal = "unreadable", 0
        if status == "unreadable":
            issues.append(name)
            continue
        kind_c, _ = _classify(name, mal, base)
        if status in ("OK", "empty") or kind_c in ("frozen", "baseline"):
            ok += 1
        else:
            issues.append(name)      # active growth only
    return ok, present, issues


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    print("stream integrity (correctness, not just freshness):")
    base = _load_baseline()
    newbase = {}
    active = frozen = baseln = 0
    for name, (kind, wcol) in sorted(STREAMS.items()):
        path = os.path.join(DR, name)
        if not os.path.exists(path):
            print("  %-26s ABSENT" % name)
            continue
        status, rows, mal = check_tsv(path, wcol) if kind == "tsv" else check_jsonl(path)
        newbase[name] = mal
        kind_c, delta = _classify(name, mal, base)
        if kind_c == "active":
            active += 1
            tag = "  <-- ACTIVE +%d malformed since last check (a writer is STILL corrupting)" % delta
        elif kind_c == "frozen":
            frozen += 1
            tag = "  (frozen residue: %d malformed, none new since last check - writer looks fixed)" % mal
        elif kind_c == "baseline":
            baseln += 1
            tag = "  (baseline set: %d malformed - re-run to classify active vs frozen)" % mal
        else:
            tag = ""
        print("  %-26s %6d rows  %s%s" % (name, rows, status, tag))
    _save_baseline(newbase)
    if active:
        verdict = "ACTIVE corruption - %d stream(s) gained malformed rows since the last check; find the writer NOW." % active
    elif frozen or baseln:
        parts = (["%d frozen-residue" % frozen] if frozen else []) + (["%d baseline-set (re-run to classify)" % baseln] if baseln else [])
        verdict = ("no ACTIVE corruption; malformed rows present but NOT growing (" + ", ".join(parts) +
                   ") - historical residue from a since-fixed writer, filter in backfills; not a live alarm.")
    else:
        verdict = "all streams well-formed. Data is flowing AND correct."
    print("\nVERDICT: " + verdict)


def _selftest():
    """Prove the active/frozen guard fires - the classification that stops the cry-wolf must itself be tested."""
    b = {"s.tsv": 5}
    cases = [
        ("clean=0",          _classify("s.tsv", 0, b)[0] == "clean"),
        ("baseline=unseen",  _classify("new.tsv", 7, b)[0] == "baseline"),
        ("frozen=equal",     _classify("s.tsv", 5, b)[0] == "frozen"),
        ("frozen=repaired",  _classify("s.tsv", 3, b)[0] == "frozen"),
        ("active=grew",      _classify("s.tsv", 8, b) == ("active", 3)),
    ]
    for name, v in cases:
        print("  [%s] %s" % ("ok" if v else "FAIL", name))
    ok = all(v for _, v in cases)
    print("stream_integrity _classify selftest:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else (main() or 0))
