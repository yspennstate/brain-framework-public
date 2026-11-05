#!/usr/bin/env python3
"""fire_telemetry.py - the READER for the reflex-quality series (built 2026-07-16).

heed_trend.jsonl (nightly heed_audit --jsonl) and reflex_ranking.json's context_damp /
_damp_meta existed as of this morning with no consumer - a series nothing reads is
documentation, not telemetry. This renders a compact agenda section so every dream sees
the trend and can act on a regression (the same law that got dream_grow and prompt
classification their owners on 07-15).

Standalone test: python fire_telemetry.py
"""
import io, json, os, time

HERE = os.path.dirname(os.path.abspath(__file__))


def _heed_tail(n=4):
    rows = []
    try:
        with io.open(os.path.join(HERE, "heed_trend.jsonl"), encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    rows.append(json.loads(ln))
    except Exception:
        return []
    return rows[-n:]


def _firehose_line():
    """Total-fire-VOLUME trend from instinct_fires.tsv - the wall SIZE, complementing heed_trend's
    quality view. Bounded tail read (last ~3MB) so cost stays O(1) as the log grows. Never raises."""
    try:
        p = os.path.join(HERE, "instinct_fires.tsv")
        sz = os.path.getsize(p)
        with io.open(p, "rb") as f:
            if sz > 3_000_000:
                f.seek(sz - 3_000_000)
                f.readline()  # drop the partial first line after the seek
            blob = f.read().decode("utf-8", "replace")
        parsed, last_ts = [], 0
        for ln in blob.splitlines():
            parts = ln.split("\t")
            if len(parts) < 4:
                continue
            try:
                ts = int(parts[0])
            except ValueError:
                continue
            if not (1700000000 < ts < 1830000000):
                continue
            parsed.append((ts, parts[3]))
            if ts > last_ts:
                last_ts = ts
        if not parsed:
            return ""
        c24, by = 0, {}
        for ts, rid in parsed:
            if ts > last_ts - 86400:
                c24 += 1
                by[rid] = by.get(rid, 0) + 1
        top = sorted(by.items(), key=lambda kv: -kv[1])[:3]
        topstr = "; ".join("%s %d" % (r.replace("_", " ")[:20], n) for r, n in top) or "-"
        trend = "ABOVE" if c24 > 3527 else "at/below"
        return ("## F. Reflex firehose SIZE - is the wall growing? (reader added 07-24)\n\n"
                "Last 24h: ~%d reflex fires, %s the 07-12 baseline (~3527/day). A rising firehose "
                "buries the rare load-bearing signal (the heeding wall; the diffusion notes, "
                "reflex_firehose_remeasured_2026_07_24). If it keeps climbing the lever is tightening "
                "the top over-firers toward their referent (mesh #265), not more detectors. "
                "Top 24h: %s.\n" % (c24, trend, topstr))
    except Exception:
        return ""


def agenda_section():
    """Markdown section or '' - never raises (agenda generation must not break)."""
    try:
        out = []
        rows = _heed_tail()
        if rows:
            out.append("## T. Reflex telemetry - is the gut getting MORE load-bearing? (reader added 07-16)")
            out.append("")
            out.append("Daily fired-and-heeded vs control (heed_trend.jsonl; lift = heed/control). "
                       "Read the TREND: a reflex whose lift decays toward 1.0 is becoming decoration - "
                       "that is a finding to consolidate, and if it persists 3+ days, machinery "
                       "(a guard at the act site) beats more exhortation, per ps1_guard's precedent.")
            out.append("")
            for r in rows:
                day = time.strftime("%m-%d", time.localtime(r.get("until", 0)))
                cells = []
                for rid, s in sorted((r.get("stats") or {}).items()):
                    f_ = s.get("fired", 0)
                    if not f_:
                        continue
                    hr = 100.0 * s.get("heeded", 0) / f_
                    cn = s.get("ctrl_n") or 1
                    cr = 100.0 * s.get("ctrl_hit", 0) / cn
                    # sample-size gate (audit 010 finding 5): a x19 lift from n=3 is noise
                    # dressed as signal - below n=10 show the counts, never a multiplier
                    if f_ < 10:
                        cells.append("%s n=%d (too thin for a lift)" % (rid.replace("_", " ")[:18], f_))
                        continue
                    lift = hr / cr if cr else float("inf")
                    cells.append("%s n=%d %d%%/%d%% x%.1f" % (rid.replace("_", " ")[:18], f_, hr, cr, lift))
                out.append("- %s: %s" % (day, "; ".join(cells) if cells else "no qualifying fires"))
        # the nightly judge verdicts (section J's output) get their reader here on day
        # one - a series nothing reads is documentation (the heed_trend lesson, applied
        # to the instrument born the same day). n-gated like everything else.
        try:
            jrows = []
            with io.open(os.path.join(HERE, "judged_nightly.jsonl"), encoding="utf-8") as jf:
                for jl in jf:
                    jl = jl.strip()
                    if jl:
                        try:
                            jrows.append(json.loads(jl))
                        except Exception:
                            pass
            if jrows:
                dom = sum(1 for r in jrows if r.get("domain_at_play") == "yes")
                fol = sum(1 for r in jrows if r.get("domain_at_play") == "yes"
                          and r.get("followed") == "yes")
                out.append("")
                if len(jrows) < 12:
                    out.append("Nightly judge verdicts accruing: %d cases so far "
                               "(%d domain-at-stake, %d of those followed) - too thin for "
                               "rates; read individual verdicts in judged_nightly.jsonl."
                               % (len(jrows), dom, fol))
                else:
                    out.append("Nightly judge series (n=%d): domain-at-stake %d/%d; "
                               "followed-when-at-stake %d/%d. A falling followed-rate is "
                               "the alarm this series exists for."
                               % (len(jrows), dom, len(jrows), fol, dom))
        except Exception:
            pass
        try:
            rk = json.load(io.open(os.path.join(HERE, "reflex_ranking.json"), encoding="utf-8"))
            damp = rk.get("context_damp") or {}
            meta = rk.get("_damp_meta") or {}
            if damp:
                out.append("")
                out.append("Context-damped on self-referential surfaces (nightly, measured vs exposure): " +
                           "; ".join("%s (n=%s, self x%s, domain x%s)" %
                                     (rid, meta.get(rid, {}).get("n", "?"),
                                      meta.get(rid, {}).get("self_ratio", "?"),
                                      meta.get(rid, {}).get("domain_ratio", "?"))
                                     for rid in sorted(damp)))
                out.append("Check: did the damp SET change vs yesterday (a reflex entering/leaving is a "
                           "finding)? Did any section-M correction land where a damped reflex would have "
                           "fired (the steelman in judgment_reflex_domain_inversion_damped_2026_07_16)?")
        except Exception:
            pass
        fl = _firehose_line()
        if fl:
            out.append(fl)
        return ("\n".join(out) + "\n") if out else ""
    except Exception:
        return ""


if __name__ == "__main__":
    s = agenda_section()
    print(s if s else "(empty section)")
