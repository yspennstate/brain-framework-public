#!/usr/bin/env python3
"""build_reflex_ranking.py - refresh the IDF surface weights the priming hook ranks with.

reflex_ranking.json (surface_weight) breaks the charge-saturation tie in instinct_prime._fire:
charge is 1.00 for most reflexes, so among co-firing equals the RARE/specific reflex should
surface over the always-on generic. The original file was built once (01619e69, 2026-07-12)
and never refreshed - but the fire distribution is non-stationary (new reflexes compile
weekly, chronic firers drift), so a static snapshot slowly inverts its own purpose: a reflex
that BECAME chronic keeps its old "rare" weight and buries genuinely rare scars. This
builder recomputes the same normalized-IDF-over-the-fire-log semantics from a rolling
window, so the weights track the distribution they rank. (The 07-15 dev-loop turn measured
the burying qualitatively; the numbers memory is subconscious_firing_degrades_surprise_weight_2026_07_15.)

Semantics (kept identical to the original file's _note):
  weight = normalized IDF over fire EVENTS: idf(r) = log(N_events / (1 + n_events_with_r)),
  min-max normalized to [0,1]. Never-fired reflexes get 1.0 (maximally surprising).
  high = surface first, low = demote. Consumer default for a missing id is 0.5.

Safety floor: reflexes whose displacement could cost real money/damage keep weight >= 0.90
regardless of how often they fire - a chronic safety scar must never lose a tier-2 tie to
vocabulary luck. (Charge strictly dominates the sort anyway; the floor is belt-and-braces.)

Default is a DRY RUN that prints the drift and an eval replaying real co-fire events
(old top-3 vs new top-3, MAX_FIRE=3, key=(charge, weight) on the fired set - match-count
is unavailable in the log, an acceptable approximation for a tiebreak refresh).
--write backs up reflex_ranking.json to .bak-<stamp> and replaces surface_weight.

Wired into the run_dream.ps1 persist block (nonfatal, like ern_reliability) so the weights
refresh nightly. Fail-safe: any error prints and exits 0 under --quiet-tolerant callers;
the consumer already tolerates a missing/bad file by defaulting all weights to 0.5.
"""
import argparse
import io
import json
import math
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FIRELOG = os.path.join(HERE, "instinct_fires.tsv")
TSV = os.path.join(HERE, "instincts.tsv")
RANKING = os.path.join(HERE, "reflex_ranking.json")
MAX_FIRE = 3   # keep in lockstep with instinct_prime.MAX_FIRE

# must never lose a tier-2 tie (see docstring); superset of habituation's _NEVER where ranked
SAFETY_FLOOR_IDS = {
    "no_local_privileged_stack", "secrets_in_git", "preserve_before_destroy",
    "pinned_file_two_tripwires", "his_never_is_machinery", "gate_tool_needs_env",
    "his_desktop_is_not_an_automation_surface", "healer_first_do_no_harm",
    "no_flashing_windows", "lookahead",
}
FLOOR = 0.90

# ---- context damp (2026-07-16, from the fire-precision measurement) --------------------
# A reflex whose trigger vocabulary is MARKET-topical can be domain-inverted: the brain's
# own files quote that vocabulary, so it over-fires on brain-system surfaces (sim_flatters:
# 56% of act/write fires there vs 21% exposure) while failing to concentrate on the market
# domain it exists for (8% vs 8% exposure). Emit a context_damp map {id: amount} that
# instinct_prime subtracts from charge ONLY when the acted-on surface is self-referential
# (fire_surface.is_self_referential) - damping, never deletion: the reflex still fires
# whenever it wins top-3 among what matched, and fires at full strength everywhere else.
# Candidates are found by the trigger itself (the trigger defines the domain); safety-
# floored / never-suppressed reflexes are excluded no matter what the data says.
DAMP_AMOUNT = 0.15
DAMP_MIN_FIRES = 40          # below this the shares are noise
DAMP_SELF_RATIO = 1.8        # fires-on-brain-surface share >= 1.8x exposure share
DAMP_DOMAIN_RATIO = 1.2      # AND market-surface share <= 1.2x exposure share
_TRADING_PROBE = ("backtest sharpe drawdown sim simulate pnl equity curve cagr walk-forward "
                  "leverage 20x risk of ruin worst-case blow up IB mark fair value mid-nlv "
                  "valuation regime lookahead in-sample feature predictor forward-fill "
                  "stale mark carried mark final nlv vxn term structure tenor slope long iv")
NEVER_DAMP = SAFETY_FLOOR_IDS | {
    "his_never_is_machinery", "secrets_in_git", "no_local_privileged_stack",
    "preserve_before_destroy", "pinned_file_two_tripwires", "gate_tool_needs_env",
    "his_desktop_is_not_an_automation_surface", "healer_first_do_no_harm",
}


def load_triggers():
    out = {}
    try:
        with io.open(TSV, encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 7:
                    out[p[0]] = p[3]
    except Exception:
        pass
    return out


def build_context_damp(days, quiet):
    """Measure fire-surface concentration vs exposure; return ({id: amount}, meta)."""
    try:
        import fire_surface
        from precision_audit import exposure_sample, fire_counts
    except Exception as e:
        if not quiet:
            print("context_damp: modules unavailable (%s) - emitting empty map" % e)
        return {}, {}
    # n~2200: the 5-seed spread of the resulting ratios is ~+/-0.1 (measured 07-16), far
    # from both thresholds; the n~800 sample flapped a borderline reflex across the line
    base = exposure_sample(n_files=60, keep=0.25)
    tot = sum(base.values())
    if tot < 1200:
        if not quiet:
            print("context_damp: exposure sample too thin (%d) - emitting empty map" % tot)
        return {}, {}
    # POPULATION MATCH (audit 010 finding 1, confirmed by matched rerun 2026-07-16): the
    # fire log carries Claude AND Codex hook fires, but the exposure control can only be
    # sampled from Claude transcripts - so the fire side must be restricted to
    # Claude-transcript-backed sessions or the ratio compares different worlds (the
    # unmatched version shipped a damp that did not survive matched replay: sim_flatters
    # x2.7/x1.0 unmatched became x1.97/x1.83 matched - domain-concentrated, gate FAILED).
    base_self = sum(base[k] for k in fire_surface.SELF_REFERENTIAL) / tot
    base_dom = base["market"] / tot
    if base_self <= 0 or base_dom <= 0:
        return {}, {}
    per = fire_counts(days, claude_backed_only=True)
    triggers = load_triggers()
    damp, meta = {}, {}
    for rid, trig in triggers.items():
        if rid in NEVER_DAMP:
            continue
        try:
            if not re.search(trig, _TRADING_PROBE, re.I):
                continue   # trigger vocabulary is not market-topical; inversion test meaningless
        except Exception:
            continue
        c = per.get(rid)
        n = sum(c.values()) if c else 0
        if n < DAMP_MIN_FIRES:
            continue
        self_share = sum(c[k] for k in fire_surface.SELF_REFERENTIAL) / n
        dom_share = c["market"] / n
        r_self, r_dom = self_share / base_self, dom_share / base_dom
        if r_self >= DAMP_SELF_RATIO and r_dom <= DAMP_DOMAIN_RATIO:
            damp[rid] = DAMP_AMOUNT
            meta[rid] = {"n": n, "self_ratio": round(r_self, 2), "domain_ratio": round(r_dom, 2)}
    if not quiet and damp:
        print("context_damp: %d domain-inverted reflexes (of %d market-topical candidates)"
              % (len(damp), sum(1 for t in triggers.values()
                                if _safe_probe_match(t))))
        for rid, m in sorted(meta.items()):
            print("  %-26s n=%d self x%.1f domain x%.1f -> damp %.2f"
                  % (rid, m["n"], m["self_ratio"], m["domain_ratio"], DAMP_AMOUNT))
    return damp, meta


def _safe_probe_match(trig):
    try:
        return bool(re.search(trig, _TRADING_PROBE, re.I))
    except Exception:
        return False


def load_instincts():
    """id -> charge (col 8, default 0.6), for every compiled reflex (fire/guide/carry)."""
    out = {}
    try:
        with io.open(TSV, encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 7:
                    try:
                        out[p[0]] = float(p[7]) if len(p) >= 8 else 0.6
                    except Exception:
                        out[p[0]] = 0.6
    except Exception:
        pass
    return out


def load_events(days):
    """Fire events from the log: one event per (ts,session) write batch -> set of reflex ids."""
    events = {}
    cutoff = time.time() - days * 86400
    with io.open(FIRELOG, encoding="utf-8", errors="replace") as f:
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if len(p) < 4:
                continue
            try:
                ts = float(p[0])
            except Exception:
                continue
            if ts < cutoff:
                continue
            key = (p[0], p[5] if len(p) > 5 else "")
            events.setdefault(key, set()).add(p[3])
    return events


def build_weights(events, all_ids):
    n_events = max(1, len(events))
    n_with = {}
    for rids in events.values():
        for r in rids:
            n_with[r] = n_with.get(r, 0) + 1
    ids = set(all_ids) | set(n_with)
    idf = {r: math.log(n_events / (1.0 + n_with.get(r, 0))) for r in ids}
    lo, hi = min(idf.values()), max(idf.values())
    span = (hi - lo) or 1.0
    w = {r: round((v - lo) / span, 4) for r, v in idf.items()}
    for r in SAFETY_FLOOR_IDS & ids:
        w[r] = max(w[r], FLOOR)
    return w, n_with, n_events


def top3(fired_ids, charges, weights):
    key = lambda r: (charges.get(r, 0.6), weights.get(r, 0.5), r)  # id as stable last tiebreak
    return sorted(sorted(fired_ids), key=key, reverse=True)[:MAX_FIRE]


def replay_eval(events, charges, old_w, new_w, quiet):
    co = {k: v for k, v in events.items() if len(v) > MAX_FIRE}
    changed, enter, exit_ = 0, {}, {}
    safety_fail = 0
    for rids in co.values():
        a, b = top3(rids, charges, old_w), top3(rids, charges, new_w)
        if a != b:
            changed += 1
            for r in set(b) - set(a):
                enter[r] = enter.get(r, 0) + 1
            for r in set(a) - set(b):
                exit_[r] = exit_.get(r, 0) + 1
        for r in SAFETY_FLOOR_IDS & rids:
            if r in a and r not in b:
                safety_fail += 1
    if not quiet:
        print("eval: %d co-fire events (>%d reflexes), top-3 changed in %d (%.1f%%)"
              % (len(co), MAX_FIRE, changed, 100.0 * changed / max(1, len(co))))
        fmt = lambda d: ", ".join("%s x%d" % kv for kv in sorted(d.items(), key=lambda x: -x[1])[:6])
        print("  entering top-3 most: %s" % (fmt(enter) or "-"))
        print("  exiting  top-3 most: %s" % (fmt(exit_) or "-"))
        print("  safety-floor displaced: %d %s" % (safety_fail, "FAIL" if safety_fail else "(PASS)"))
    return safety_fail == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    charges = load_instincts()
    events = load_events(a.days)
    if len(events) < 500:   # a thin window would swing the weights on noise; keep the old file
        print("build_reflex_ranking: only %d fire events in %dd - too thin, not touching the ranking"
              % (len(events), a.days))
        return 0
    new_w, n_with, n_events = build_weights(events, charges.keys())

    old = {}
    try:
        old = json.load(io.open(RANKING, encoding="utf-8")).get("surface_weight", {}) or {}
    except Exception:
        pass

    if not a.quiet:
        print("build_reflex_ranking: %d events / %d fires span %dd; %d reflexes (%d never fired)"
              % (n_events, sum(n_with.values()), a.days, len(new_w), len(new_w) - len(n_with)))
        movers = sorted(new_w, key=lambda r: abs(new_w[r] - old.get(r, 0.5)), reverse=True)[:8]
        for r in movers:
            print("  %-36s old=%.3f new=%.3f fires=%d" % (r, old.get(r, 0.5), new_w[r], n_with.get(r, 0)))

    ok = replay_eval(events, charges, old, new_w, a.quiet)
    if not ok:
        print("build_reflex_ranking: safety-floor eval FAILED - not writing")
        return 1

    damp, damp_meta = build_context_damp(a.days, a.quiet)

    if a.write:
        try:
            if os.path.exists(RANKING):
                bak = RANKING + ".bak-" + time.strftime("%Y%m%d_%H%M%S")
                with io.open(RANKING, encoding="utf-8") as f, io.open(bak, "w", encoding="utf-8") as g:
                    g.write(f.read())
            doc = {
                "_note": ("surface_weight = normalized IDF over the fire-log; ranks co-firing "
                          "reflexes since charge is saturated (89f84043). high=surface first, "
                          "low=demote/collapse to meta-line. context_damp = {id: charge-damp} "
                          "applied by instinct_prime ONLY on self-referential surfaces "
                          "(fire_surface.py) - domain-inverted market-topical reflexes, "
                          "measured nightly vs exposure; damping never deletion."),
                "_built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "_window_days": a.days,
                "_n_events": n_events,
                "_builder": "build_reflex_ranking.py (nightly, run_dream persist block)",
                "_damp_meta": damp_meta,
                "context_damp": damp,
                "surface_weight": new_w,
            }
            tmp = RANKING + ".tmp"
            with io.open(tmp, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=1, sort_keys=True)
            os.replace(tmp, RANKING)
            if not a.quiet:
                print("wrote %s (%d weights; backup kept)" % (RANKING, len(new_w)))
        except Exception as e:
            print("build_reflex_ranking: write failed: %s" % e)
            return 1
    elif not a.quiet:
        print("(dry run - pass --write to update reflex_ranking.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
