#!/usr/bin/env python3
"""miss_budget.py - per-reflex MISS budgets on live analogous wordings.

The gut's calibration so far controls one error direction only: calibrate.py bounds
each reflex's FALSE-ALARM rate on real typical actions (budget ~1.5-2%). Nothing
measures the other direction - the probability that a reflex STAYS SILENT on a live
occurrence of its own family, worded the way trouble actually arrives. That miss side
is exactly the score-error term in the pathwise stability bound the diffusion program
imported (the Girsanov-KL bound: the deployed
session's behavior drifts from the guarded ideal by at most a quantity driven by the
integrated squared score shortfall along the realized prompt path, and zero loss on
the probe wordings alone controls nothing off them - the same bound). This script
measures that shortfall on the best live corpus we have: the owner's own corrections.

POSITIVES. MISTAKES.md correction bullets that name their reflex(es). The scored text
is the SITUATION as it presented - the header, his verbatim words, and the "wrong:"
clause - deliberately EXCLUDING the "lesson:"/"reflex:" clauses, which restate the
reflex's own vocabulary and would flatter the match. For each (correction, named
reflex): the production scorer (gfcore token dot-product, MIN_MATCH, per-reflex
calibrated threshold) either fires or misses; the margin is score - thr.

CONTROLS. Negative: the same scorer over typical_actions.jsonl must reproduce the
calibrated false-alarm budget (validates this replication against calibrate.py).
Positive: heldout_cases.json positives (recent real mistakes labeled with the reflex
that should fire) - an independent set built by another session.

BUDGET. Per reflex: n, miss rate at the command surface (thr_mult 1.0), miss rate at
a 0.75 sensitivity multiplier (the readier prose surface direction; not claimed to be
the production report multiplier), median margin, and the mean-square shortfall
mean(max(0, thr - score)^2) - the epsilon^2 entry of the KL-form budget, in
calibrated token-score units. The ranking and the rates are the operational content;
the KL form gives them their meaning: a reflex with a large shortfall on live
wordings is a large term in the bound on how far unguarded behavior can drift.

SCOPE - read before quoting. This measures the GRAPH-TOKEN channel of the gut alone.
The production system is a union: the per-reflex regex path (instinct_prime), the
report-surface structural checks against lived anchors (pre_report), and
conscious-priming, none of which are scored here - so a 100% miss row means "the
graph channel alone would stay silent on that historical wording at its calibrated
threshold", not "the gut is blind to the family". Second scoping fact: most
corrections PREDATE their reflex's compilation (the reflex was often compiled in
response to that very correction), so rows measure whether the CURRENT model would
catch the historical wording if it arrived again - the analogous-wording recall
question - not whether a deployed reflex failed on the day.

Read-only over the corpora; writes MISS_BUDGETS.md next to ERN_LEARNING.md.
Usage: python miss_budget.py [--write]
"""
import io, json, os, re, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DR = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, DR)
import gfcore

MEM = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
MISTAKES = os.path.join(MEM, "MISTAKES.md")
TYPICAL = os.path.join(HERE, "typical_actions.jsonl")
HELDOUT = os.path.join(HERE, "heldout_cases.json")
OUT = os.path.join(DR, "MISS_BUDGETS.md")

BULLET = re.compile(r"^- \*\*(?P<hdr>.+?)\*\*\s*(?P<rest>.*)$")
REFLEX_ID = re.compile(r"[a-z][a-z0-9_]{2,}")
# ids named after "reflex:" plus compiled ids appearing after "->" in that clause
NON_IDS = set("none yet reflex extend refine both and or the a to of covered compiled candidate "
              "name same class dream sws live gut from this was is are new heed on first fire "
              "fires firing failed pre report top list add it now".split())


def parse_corrections():
    """[(situation_text, [reflex_ids])] from MISTAKES.md bullets that name a reflex."""
    try:
        body = io.open(MISTAKES, encoding="utf-8", errors="replace").read()
    except Exception:
        return []
    # collect bullets with their continuation lines
    bullets = []
    cur = None
    for ln in body.splitlines():
        if BULLET.match(ln):
            if cur:
                bullets.append(cur)
            cur = ln
        elif cur is not None and ln.startswith(("  ", "\t")) and not ln.lstrip().startswith("#"):
            cur += " " + ln.strip()
        elif cur is not None and (ln.startswith("#") or not ln.strip()):
            bullets.append(cur); cur = None
    if cur:
        bullets.append(cur)
    out = []
    for b in bullets:
        low = b.lower()
        rpos = low.rfind("reflex:")
        if rpos < 0:
            continue
        reflex_clause = b[rpos + 7:]
        # ids live outside parentheticals/brackets; the notes inside them name probes,
        # dream sessions, and OTHER memories - stripping the spans keeps ids like
        # "a + b (notes) + c" complete while dropping the prose (spot-checked 2026-07-23)
        reflex_clause = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", reflex_clause)
        ids = [t for t in REFLEX_ID.findall(reflex_clause.lower()) if t not in NON_IDS]
        if not ids:
            continue
        lpos = low.find("lesson:")
        situation = b[:lpos if lpos > 0 else rpos]
        situation = re.sub(r"\[\[[^\]]*\]\]", " ", situation)
        out.append((situation, ids))
    return out


def score_text(model, rid, text):
    """(score, nmatch, thr) via the production token dot-product for one reflex."""
    m = model.get(rid)
    if not m:
        return None
    toks = m.get("tokens", {})
    ctoks = set(gfcore._toks(text))
    score = 0.0; nmatch = 0
    for t in ctoks:
        w = toks.get(t)
        if w:
            score += w; nmatch += 1
    thr = float(m.get("thr", gfcore.BASE_THRESHOLD))
    return score, nmatch, thr


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    write = "--write" in sys.argv
    model = gfcore._model()
    if not model:
        print("no reflex model - abort"); return 1
    corr = parse_corrections()
    print("MISTAKES.md corrections with named reflexes: %d" % len(corr))

    per = {}
    unmatched = set()
    for situation, ids in corr:
        for rid in ids:
            if rid not in model:
                unmatched.add(rid); continue
            if model[rid].get("mode") == "carry":
                continue
            s = score_text(model, rid, situation)
            if s is None:
                continue
            score, nmatch, thr = s
            fired = (nmatch >= gfcore.MIN_MATCH) and (score >= thr)
            fired75 = (nmatch >= gfcore.MIN_MATCH) and (score >= 0.75 * thr)
            per.setdefault(rid, []).append((score, nmatch, thr, fired, fired75))
    print("named-but-not-in-model ids (fyi): %s" % ", ".join(sorted(unmatched)[:12]))

    rows = []
    for rid, evs in sorted(per.items(), key=lambda kv: -len(kv[1])):
        n = len(evs)
        misses = sum(1 for *_x, f, _f75 in evs if not f)
        miss75 = sum(1 for *_x, f75 in evs if not f75)
        margins = [sc - th for sc, _nm, th, _f, _f75 in evs]
        shortfall = sum(max(0.0, th - sc) ** 2 for sc, _nm, th, _f, _f75 in evs) / n
        rows.append((rid, n, misses / n, miss75 / n, statistics.median(margins), shortfall))

    print("\nPER-REFLEX MISS BUDGET on the owner's live corrections (command surface):")
    print("  %-34s %3s  %6s  %6s  %8s  %9s" % ("reflex", "n", "miss", "m@.75", "med-mrg", "eps^2"))
    for rid, n, mr, mr75, med, sf in rows:
        print("  %-34s %3d  %5.0f%%  %5.0f%%  %+8.2f  %9.3f" % (rid, n, 100 * mr, 100 * mr75, med, sf))
    tot_n = sum(n for _r, n, *_ in rows)
    tot_miss = sum(n * mr for _r, n, mr, *_ in rows)
    overall = tot_miss / max(1, tot_n)
    print("  overall: n=%d  miss %.0f%%  (weighted by occurrence)" % (tot_n, 100 * overall))

    # ERN extension (iteration 3): labeled real-failure events from ern_labels.tsv.
    # Scoring surface is the COMMAND text that produced the failure (outcomes.jsonl
    # holds cmd, not the output prose), so rows read "would the token channel fire on
    # the failing action's command at its threshold".
    ern_rows = []
    ern_path = os.path.join(HERE, "ern_labels.tsv")
    if os.path.exists(ern_path):
        import ern_labels as EL
        by_ts = {ts: (sig, tool, cmd) for ts, sig, tool, cmd in EL.load()}
        per_ern = {}
        n_scored = 0
        for ln in io.open(ern_path, encoding="utf-8", errors="replace"):
            p = ln.rstrip("\n").split("\t")
            if len(p) < 7 or p[0] == "ts" or not p[2] or p[5] != "True":
                continue
            ts, label = p[0], p[2]
            if ts not in by_ts or label not in model:
                continue
            _sig, _tool, cmd = by_ts[ts]
            s = score_text(model, label, cmd)
            if s is None:
                continue
            score, nmatch, thr = s
            fired = (nmatch >= gfcore.MIN_MATCH) and (score >= thr)
            per_ern.setdefault(label, []).append((score, thr, fired))
            n_scored += 1
        print("\nERN EXTENSION - labeled real failures (scored on the failing command text):")
        print("  %-24s %4s  %6s  %8s  %9s" % ("family", "n", "miss", "med-mrg", "eps^2"))
        for label, evs in sorted(per_ern.items(), key=lambda kv: -len(kv[1])):
            n = len(evs)
            misses = sum(1 for _s, _t, f in evs if not f)
            med = statistics.median(sc - th for sc, th, _f in evs)
            sf = sum(max(0.0, th - sc) ** 2 for sc, th, _f in evs) / n
            ern_rows.append((label, n, misses / n, med, sf))
            print("  %-24s %4d  %5.0f%%  %+8.2f  %9.3f" % (label, n, 100.0 * misses / n, med, sf))
        if n_scored:
            tot_m = sum(n * mr for _l, n, mr, _md, _sf in ern_rows)
            tot_nn = sum(n for _l, n, *_ in ern_rows)
            print("  overall: n=%d  miss %.0f%%  (coverage: 107/612 events labeled, "
                  "33 generic hand-read, remainder generic-unlabeled per ern_labels.py)"
                  % (tot_nn, 100.0 * tot_m / max(1, tot_nn)))

    # negative control - replicate the calibrated false-alarm budget on typical actions
    try:
        acts = [json.loads(l).get("text", "") for l in io.open(TYPICAL, encoding="utf-8") if l.strip()]
    except Exception:
        acts = []
    fa_rates = []
    if acts:
        for rid, m in model.items():
            if m.get("mode") == "carry" or not m.get("tokens"):
                continue
            fires = 0
            for t in acts:
                s = score_text(model, rid, t)
                if s and s[1] >= gfcore.MIN_MATCH and s[0] >= s[2]:
                    fires += 1
            fa_rates.append((fires / len(acts), rid))
        fa_rates.sort(reverse=True)
        mean_fa = sum(r for r, _ in fa_rates) / len(fa_rates)
        print("\nNEGATIVE CONTROL - false-alarm on %d typical actions: mean %.2f%%, worst %s %.2f%%"
              % (len(acts), 100 * mean_fa, fa_rates[0][1], 100 * fa_rates[0][0]))
        print("  (calibrate.py budget was %.1f%% per reflex - replication is sane if the same order)"
              % (100 * float(json.load(io.open(gfcore.ANCH, encoding='utf-8'))
                             .get("_meta", {}).get("calibrated_budget", 0.02))))

    # positive control - independent held-out positives
    pos_res = []
    try:
        held = json.load(io.open(HELDOUT, encoding="utf-8"))
        for case in held.get("positives", []):
            text = case.get("text") or case.get("action") or ""
            want = case.get("reflex") or case.get("expect") or case.get("reflexes")
            wants = want if isinstance(want, list) else ([want] if want else [])
            wants = [w for w in wants if w in model]
            if not text or not wants:
                continue
            for rid in wants:
                s = score_text(model, rid, text)
                if s:
                    pos_res.append((rid, s[1] >= gfcore.MIN_MATCH and s[0] >= s[2]))
    except Exception:
        pass
    if pos_res:
        hit = sum(1 for _r, f in pos_res if f)
        print("\nPOSITIVE CONTROL - held-out labeled positives: %d/%d fire (%.0f%%)"
              % (hit, len(pos_res), 100 * hit / len(pos_res)))

    if write:
        with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
            f.write("# Per-reflex miss budgets - the other half of the gut's calibration\n\n")
            f.write("Generated by graph_fire/miss_budget.py. The false-alarm side is bounded by\n")
            f.write("calibrate.py; this measures the MISS side on the owner's own corrections\n")
            f.write("(MISTAKES.md situation text, lesson/reflex clauses excluded), scored by the\n")
            f.write("production gfcore token model at the calibrated thresholds. The pathwise\n")
            f.write("stability bound (Girsanov form) makes the\n")
            f.write("epsilon^2 column the per-event entry of the budget by which unguarded\n")
            f.write("behavior can drift from guarded behavior along a session; the units are\n")
            f.write("calibrated token-score units, so the rates and the ranking carry the\n")
            f.write("operational meaning. A reflex high in this table with a high family count\n")
            f.write("is the next compilation target; a reflex missing here but recurring in the\n")
            f.write("ERN ledger has no named corrections yet and needs labeling first.\n\n")
            f.write("| reflex | n | miss | miss@0.75thr | median margin | eps^2 |\n|---|---|---|---|---|---|\n")
            for rid, n, mr, mr75, med, sf in rows:
                f.write("| %s | %d | %.0f%% | %.0f%% | %+.2f | %.3f |\n" % (rid, n, 100 * mr, 100 * mr75, med, sf))
            f.write("\nOverall weighted miss %.0f%% over %d named live occurrences.\n" % (100 * overall, tot_n))
            f.write("\nControls: see the script's stdout for the replication of the calibrated\n")
            f.write("false-alarm budget on typical actions and the independent held-out positives.\n")
        print("\nwrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
