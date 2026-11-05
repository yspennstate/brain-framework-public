#!/usr/bin/env python3
"""ern_reliability.py - solder the ERN -> prediction edge: turn the felt-error stream into a scored,
calibrated reliability signal instead of a pile of logged failures.

The gap (named in gist_neuroscience_faculties_2026_07_08, and independently by three 1000-ways scouts):
error_monitor.py logs a real felt-error to outcomes.jsonl, but nothing writes a PREDICTION OUTCOME from
it - so the after-the-fact ERN signal never reaches predict.py's calibration. The forecast loop is drawn,
not soldered. This closes it: the brain holds a standing implicit prediction "my work runs clean," and
each felt-error is a resolved MISS of it. Bucketed by day, that's a Bernoulli series this scores with the
same Brier + Beta-Binomial-shrunk machinery predict.py uses, and appends to reliability.jsonl (append-only,
the scored series that accumulates going forward). Rescorla-Wagner / Schultz: the error is the learning
signal; a felt-error in a stretch the brain thought reliable carries the most bits.

HONEST ON PURPOSE. The stream is young and noisy, and this says so rather than shipping a flattering rate:
  - it excludes SYNTHETIC gate events (already excluded upstream) and best-effort filters obvious
    intentional/probe failures (a `python -c "import x"` that throws to TEST for absence is not a bug);
  - it reports the felt-error RATE (errors per active day) and its trend - honestly computable from
    timestamps alone - and only a HEAVILY-CAVEATED clean-day estimate, never a precise "success rate"
    from a denominator (session ids in the stream don't cleanly align to transcript files - measured 10/15);
  - it labels everything provisional until the series is old enough to trust (>= MIN_DAYS distinct days).

The real prerequisite it flags for later: QUALITY-GATE outcomes.jsonl (separate a real bug from an
intentional/expected failure) before the reliability number earns trust. Built, measured, caveated - not
over-claimed. Read-mostly (only appends to its own reliability.jsonl). Usage: python ern_reliability.py
"""
import os, io, json, time, math, sys, re
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
OUTCOMES = os.path.join(HERE, "outcomes.jsonl")
SERIES = os.path.join(HERE, "reliability.jsonl")   # the scored daily clean/dirty series (append-only)
TSV = os.path.join(HERE, "reliability.tsv")
DAY = 86400.0
KAPPA = 4.0          # Beta-Binomial shrink strength toward the neutral prior (predict.py's convention)
PRIOR_CLEAN = 0.7    # neutral-ish prior for P(clean day) before the series has spoken
MIN_DAYS = 5         # fewer distinct covered days than this -> the estimate is provisional, don't trust it

# best-effort marks of an intentional / expected failure the brain deliberately provoked (probing for a
# module's absence, a deliberate "does this fail" test). Not a real reliability miss. Conservative - only
# unambiguous probe shapes, so a real bug is never silently excluded.
PROBE = ("import sentence_transformers", "-c \"import", "-c 'import", "expected-negative", "probe")

# Reuse ern_learn's false-alarm classifier so this reliability count MATCHES ern_learn's "real" count.
# The ERN fires on benign output that merely MENTIONS a signature (an echo about a traceback, a grep FOR
# "leaked secret") and on empty/display-only commands. Before 2026-07-10 this file filtered only probes +
# fixtures, so it reported ~130 "real" felt-errors vs ern_learn's 106 - the quality-gate this file's own
# header names as the prerequisite for trust. Fail-open: a broken import falls back to the old (looser)
# behavior rather than crash the dream that runs this.
try:
    from ern_learn import is_false_alarm as _ern_false_alarm
except Exception:
    def _ern_false_alarm(cmd, sigs):
        return False


def _load_events():
    """Real felt-errors from outcomes.jsonl: (ts, session, sigs, tool, cmd). Skips blank/bad lines."""
    ev = []
    try:
        for ln in io.open(OUTCOMES, encoding="utf-8", errors="replace"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except Exception:
                continue
            if not isinstance(o, dict) or "ts" not in o:
                continue
            ev.append(o)
    except Exception:
        pass
    return ev


def _is_probe(cmd):
    c = (cmd or "").lower()
    return any(p in c for p in PROBE)


def _is_fixture_session(sess):
    """A real session id is uuid-shaped; empty means the pre-stamping era (2026-07-08, real errors).
    Anything else (ernt_*, selftest, short test sids) is a test fixture piping payloads at the hook -
    5 such events sat inside the 'real' count when this series started (flagged 2026-07-09). Same
    uuid-shape convention fok_probe uses for surfaced.tsv."""
    return bool(sess) and not re.match(r"^[0-9a-f]{8}-", sess)


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    now = time.time()
    ev = _load_events()
    if not ev:
        print("ern_reliability: outcomes.jsonl empty - no felt-errors logged yet. Loop is wired; nothing to score.")
        return

    # attribute each excluded event to ONE reason (priority probe > fixture > false-alarm) so the counts
    # sum to len(ev) with no double-count, and a real bug is never silently dropped.
    def _reason(e):
        if _is_probe(e.get("cmd", "")):
            return "probe"
        if _is_fixture_session(e.get("session", "")):
            return "fixture"
        if _ern_false_alarm(e.get("cmd", ""), e.get("sig", [])):
            return "false_alarm"
        return "real"
    reasons = Counter(_reason(e) for e in ev)
    real = [e for e in ev if _reason(e) == "real"]
    n_probe = reasons["probe"]; n_fixture = reasons["fixture"]; n_false = reasons["false_alarm"]
    t0 = min(e["ts"] for e in ev)
    span_days = max((now - t0) / DAY, 1e-9)

    # bucket felt-errors by calendar-ish day index from t0; a day with >=1 real felt-error is "dirty"
    def day_idx(ts):
        return int((ts - t0) // DAY)
    dirty_days = set(day_idx(e["ts"]) for e in real)
    covered_days = day_idx(now) + 1                      # days elapsed since the stream began (>=1)
    dirty = len(dirty_days)
    clean = max(covered_days - dirty, 0)

    # P(clean day), Beta-Binomial shrunk toward the neutral prior so a thin series can't assert a rate
    p_clean = (clean + KAPPA * PRIOR_CLEAN) / (covered_days + KAPPA)
    # surprise of the dirty days against the belief (bits) - the learning signal
    p_dirty_belief = min(max(1.0 - p_clean, 1e-6), 1 - 1e-6)
    bits_per_dirty = -math.log2(p_dirty_belief)

    by_sig = Counter()
    for e in real:
        for s in e.get("sig", []):
            by_sig[s] += 1
    by_tool = Counter(e.get("tool", "?") for e in real)
    rate = len(real) / span_days

    # append resolved verdicts to the scored series (idempotent per day-index). PREQUENTIAL / CAUSAL:
    # each past day is scored against the belief formed from the days BEFORE it only - never the final
    # rate. A one-step-ahead score can't peek at data the moment wouldn't have had (the lookahead scar:
    # the May regime-VIX leak, 31 backlinks of fallout). Only days fully in the past are resolved, so a
    # still-open "clean" day is never claimed prematurely.
    today = day_idx(now)
    already = set()
    try:
        for ln in io.open(SERIES, encoding="utf-8", errors="replace"):
            try:
                already.add(json.loads(ln).get("day"))
            except Exception:
                pass
    except Exception:
        pass
    c_prior = d_prior = 0                     # clean/dirty counts from days strictly before the one scored
    for d in range(covered_days):
        is_dirty = d in dirty_days
        if d < today and d not in already:    # a complete, past, unscored day
            tot_prior = c_prior + d_prior
            p_belief = (c_prior + KAPPA * PRIOR_CLEAN) / (tot_prior + KAPPA)   # belief BEFORE seeing day d
            o = 0.0 if is_dirty else 1.0
            brier = (p_belief - o) ** 2
            pc = min(max(p_belief if o == 1.0 else 1.0 - p_belief, 1e-6), 1 - 1e-6)
            rec = {"day": d, "ts": t0 + (d + 0.5) * DAY, "outcome": ("hit" if o == 1.0 else "miss"),
                   "p": round(p_belief, 3), "brier": round(brier, 3), "surprise_bits": round(-math.log2(pc), 2),
                   "n_errors": sum(1 for e in real if day_idx(e["ts"]) == d)}
            try:
                with io.open(SERIES, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")
            except Exception:
                pass
        # update running counts AFTER scoring, so day d never informs its own belief
        if d < today:
            d_prior += 1 if is_dirty else 0
            c_prior += 0 if is_dirty else 1

    # scored series so far (for the calibration read)
    ser = []
    try:
        for ln in io.open(SERIES, encoding="utf-8", errors="replace"):
            try:
                ser.append(json.loads(ln))
            except Exception:
                pass
    except Exception:
        pass
    scored_days = len(ser)
    mean_brier = (sum(s.get("brier", 0) for s in ser) / scored_days) if scored_days else None

    try:
        with io.open(TSV, "w", encoding="utf-8") as f:
            f.write("day\toutcome\tp\tbrier\tsurprise_bits\tn_errors\n")
            for s in ser:
                f.write(f"{s.get('day')}\t{s.get('outcome')}\t{s.get('p')}\t{s.get('brier')}\t"
                        f"{s.get('surprise_bits')}\t{s.get('n_errors')}\n")
    except Exception:
        pass

    trust = "PROVISIONAL - too young to trust" if covered_days < MIN_DAYS else "maturing"
    print("=" * 72)
    print(" ERN -> PREDICTION reliability (the felt-error stream, scored)")
    print("=" * 72)
    print(f"  window: {span_days:.1f} days, {len(real)} real felt-errors"
          + (f" (+{n_probe} intentional/probe excluded)" if n_probe else "")
          + (f" (+{n_fixture} fixture-session excluded)" if n_fixture else "")
          + (f" (+{n_false} false-alarm excluded: echo/grep ABOUT a signature)" if n_false else "")
          + f", rate {rate:.1f}/day")
    print(f"  by signature: " + "; ".join(f"{k[:38]} x{v}" for k, v in by_sig.most_common(4)))
    print(f"  by tool: {dict(by_tool)}")
    print(f"  covered days {covered_days}: {clean} clean / {dirty} dirty -> P(clean day) {p_clean:.2f} "
          f"(shrunk, prior {PRIOR_CLEAN}); a dirty day = {bits_per_dirty:.1f} bits of surprise")
    if scored_days:
        print(f"  scored series: {scored_days} resolved day-bets, mean Brier {mean_brier:.3f} "
              f"(-> reliability.jsonl; feeds calibration as it grows)")
    print(f"  TRUST: {trust}.  Intentional/probe, fixture, AND false-alarm (echo/grep ABOUT a signature)")
    print(f"         events are now gated out via ern_learn.is_false_alarm, so this count matches ern_learn's.")
    print(f"         Remaining caveat: the clean-day denominator (session<->transcript alignment, measured 10/15).")
    # say exactly what was (not) written - the unconditional "loop is soldered" line got quoted by a
    # dream as "reliability.jsonl exists now" while no file existed (caught 2026-07-09)
    if scored_days:
        print(f"         Scored series on disk: reliability.jsonl ({scored_days} day-bets).")
    else:
        print(f"         No series file yet - the first FULL past day scores on tomorrow's run;")
        print(f"         nothing has been written to disk so far.")
    # ran-stamp for hook_liveness: a missing output file is ambiguous between producer-never-ran
    # and output-not-yet-due (it cried wolf on exactly that, 2026-07-09) - the stamp disambiguates
    try:
        # stamp name = "." + stream-stem + "_ran.json" (hook_liveness derives it from STREAMS keys)
        json.dump({"ts": time.time()}, io.open(os.path.join(HERE, ".reliability_ran.json"),
                                               "w", encoding="utf-8"))
    except Exception:
        pass


if __name__ == "__main__":
    main()
