#!/usr/bin/env python3
"""eval_real.py - measure the firing math against the brain's ACTUAL history, not hand-authored cases.

The 9/10 from shadow_eval.py was 10 cases I wrote myself - circular. This measures against real data:
  POSITIVES  - the labeled corrections in MISTAKES.md (context -> the reflex that should fire). Leaky in
               ABSOLUTE terms (reflexes were written from these corrections), so we use it for RELATIVE
               method comparison, where the leakage cancels: 'does PPR beat BFS on the same fixed data'.
  NEGATIVES  - snippets from instinct_fires.tsv where a reflex fired on a READ-ONLY action (proxy='read'):
               candidate false-fires (a mention, not an act). A better precision method fires on fewer.
  OUTCOMES   - outcomes.jsonl felt-errors (generalization check: the reflex text was NOT written from these).

The scorer is method-agnostic: it reads an anchor-model JSON (reflex_anchors.json or an alternative built
by a different diffusion), so 'compare BFS vs PPR' = point it at two models. Regex is the instinct_prime
baseline. Bootstrap CIs on the deltas. Stdlib only, forward-slash paths, no heavy compute.
"""
import os, io, json, re, sys, math, random

HERE = os.path.dirname(os.path.abspath(__file__))
DREAM = os.path.dirname(HERE)
TSV = os.path.join(DREAM, "instincts.tsv")
FIRES = os.path.join(DREAM, "instinct_fires.tsv")
OUTCOMES = os.path.join(DREAM, "outcomes.jsonl")
MISTAKES = os.path.join(os.path.dirname(os.path.dirname(DREAM)), "projects", "{{BRAIN_SLUG}}", "memory", "MISTAKES.md")
if not os.path.exists(MISTAKES):
    MISTAKES = "~/.claude/projects/{{BRAIN_SLUG}}/memory/MISTAKES.md"

_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9]{2,}")
_STOP = set("""the a an and or of to in on for by is are was were be been being it its this that these those
with without into onto from as at we you he she they them his her their our your my me not no yes do did done
if then else than so but scar gist method feedback project reference dream daydream 2026 2025 new old now day
days before after check make sure look take run use get keep need want first still just also see saw open said
say tell point place change move find what which when where why how who out off down over under about around here
there will would could should can may might must more most less very really thing things one two three""".split())


def toks(s):
    return [w.lower() for w in _WORD.findall(s or "") if w.lower() not in _STOP and not w.isdigit()]


def load_reflexes():
    out = []
    for p in (l.rstrip("\n").split("\t") for l in io.open(TSV, encoding="utf-8")):
        if len(p) >= 7 and p[1] in ("fire", "guide", "carry"):
            out.append({"id": p[0], "mode": p[1], "trigger": p[3], "charge": float(p[7]) if len(p) >= 8 else 0.6})
    return out


REF = load_reflexes()
REF_IDS = set(r["id"] for r in REF)
REF_TRIG = [(r["id"], r["trigger"]) for r in REF if r["mode"] in ("fire", "guide")]


# ---------- data ----------
def positives_mistakes():
    """[(context, {expected reflex ids})] from MISTAKES.md bullet lines that name a known reflex."""
    out = []
    for ln in io.open(MISTAKES, encoding="utf-8"):
        if not re.match(r"\s*-\s+\*\*", ln):
            continue
        m = re.search(r"reflex:\s*(.+)$", ln)
        if not m:
            continue
        exp = set(rid for rid in REF_IDS if re.search(r"\b" + re.escape(rid) + r"\b", m.group(1)))
        if not exp:
            continue
        context = ln[:m.start()]          # drop the 'reflex:' clause to cut the worst leakage
        context = re.sub(r"\s*-\s+\*\*[^*]+\*\*\s*", " ", context)  # drop the date/session marker
        out.append((context, exp))
    return out


def negatives_reads(limit=200):
    """Snippets where a reflex fired on a READ-ONLY action (proxy='read') - candidate false-fires."""
    out = []
    for l in io.open(FIRES, encoding="utf-8"):
        p = l.rstrip("\n").split("\t")
        if len(p) >= 5 and p[2] == "read":
            out.append(p[4])
    return out[:limit]


def positives_outcomes(limit=200):
    """[(cmd, sig)] felt-errors - the reflex text was NOT written from these (cleaner generalization)."""
    out = []
    for l in io.open(OUTCOMES, encoding="utf-8"):
        l = l.strip()
        if not l:
            continue
        try:
            o = json.loads(l)
        except Exception:
            continue
        cmd = o.get("cmd") or ""
        if cmd:
            out.append((cmd, o.get("sig", [])))
    return out[-limit:]


# ---------- firing methods ----------
def fire_regex(text):
    hits = set()
    for rid, trig in REF_TRIG:
        try:
            if re.search(trig, text, re.I):
                hits.add(rid)
        except Exception:
            pass
    return hits


def load_model(path):
    try:
        return json.load(io.open(path, encoding="utf-8")).get("reflexes", {})
    except Exception:
        return {}


def fire_graph_cal(text, model):
    """Fire using each reflex's OWN calibrated threshold m['thr'] (from calibrate.py)."""
    ct = set(toks(text))
    if not ct:
        return set()
    hits = set()
    for rid, m in model.items():
        if m.get("mode") == "carry":
            continue
        tk = m.get("tokens", {})
        thr = m.get("thr", 1.05)
        s = 0.0
        n = 0
        for t in ct:
            w = tk.get(t)
            if w:
                s += w
                n += 1
        if n >= 2 and s >= thr:
            hits.add(rid)
    return hits


def cal(cal_path):
    """Held-out comparison: regex vs uniform-threshold graph vs per-reflex-calibrated graph. Nag is
    measured on the ODD half of typical actions (calibrate.py used the EVEN half) - no peeking."""
    model = load_model(cal_path)
    uni = load_model(os.path.join(HERE, "reflex_anchors.json"))
    posM = positives_mistakes()
    posO_act = [(c, s) for c, s in positives_outcomes() if not _is_read(c)]
    typ_test = load_typical()[1::2]
    typ_test_act = [r for r in typ_test if not r["is_read"]]
    methods = [("regex", lambda t: fire_regex(t)),
               ("uniform1.05", lambda t: fire_graph(t, uni, 1.05)),
               ("calibrated", lambda t: fire_graph_cal(t, model)),
               ("union-cal", lambda t: fire_regex(t) | fire_graph_cal(t, model))]
    print("HELD-OUT eval (mistakes=%d, outcome-acts=%d, typical-test-act=%d)"
          % (len(posM), len(posO_act), len(typ_test_act)))
    print("  %-12s | mistake-recall | outcome-act | typical-nag(act)   (want recall hi, nag lo)" % "method")
    for name, fire in methods:
        pc = sum(1 for ctx, exp in posM if exp & fire(ctx))
        oa = sum(1 for c, s in posO_act if fire(c))
        tn = sum(1 for r in typ_test_act if fire(r["text"]))
        print("  %-12s |   %2d/%-2d %.2f   | %3d/%-3d %.2f | %3d/%-3d %.2f"
              % (name, pc, len(posM), pc / max(1, len(posM)), oa, len(posO_act), oa / max(1, len(posO_act)),
                 tn, len(typ_test_act), tn / max(1, len(typ_test_act))))


def fire_graph(text, model, thr):
    """Uncapped token-dot-product firing from an anchor model (matches gfcore's scoring, no MAX_FIRE cap)."""
    ct = set(toks(text))
    if not ct:
        return set()
    hits = set()
    for rid, m in model.items():
        if m.get("mode") == "carry":
            continue
        tk = m.get("tokens", {})
        score = 0.0
        nm = 0
        for t in ct:
            w = tk.get(t)
            if w:
                score += w
                nm += 1
        if nm >= 2 and score >= thr:
            hits.add(rid)
    return hits


# ---------- metrics ----------
def _wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def evaluate(model_path, thr):
    model = load_model(model_path)
    posM = positives_mistakes()
    negR = negatives_reads()
    posO = positives_outcomes()
    res = {}
    for name, fire in (("regex", lambda t: fire_regex(t)),
                       ("graph", lambda t: fire_graph(t, model, thr)),
                       ("union", lambda t: fire_regex(t) | fire_graph(t, model, thr))):
        # recall on labeled mistakes: labeled reflex among fired
        pc = sum(1 for ctx, exp in posM if exp & fire(ctx))
        # false-fire on read-only actions: any reflex fired
        ff = sum(1 for s in negR if fire(s))
        # outcomes: fired anything (coarse generalization recall)
        oc = sum(1 for cmd, sig in posO if fire(cmd))
        res[name] = {"mistake_recall": _wilson(pc, len(posM)), "recall_k": (pc, len(posM)),
                     "read_falsefire": _wilson(ff, len(negR)), "ff_k": (ff, len(negR)),
                     "outcome_fire": _wilson(oc, len(posO)), "oc_k": (oc, len(posO))}
    return res, len(posM), len(negR), len(posO)


_READONLY = re.compile(r"^\s*(grep|rg|cat|less|head|tail|ls|find|wc|git (log|status|diff|show|blame)|"
                       r"python[0-9.]* (-c )?[\w./\\]*(recall|status|audit|eval|check|_test|probe)|echo|"
                       r"type|Get-Content|Select-String|Get-ChildItem)\b", re.I)


def _is_read(cmd):
    c = cmd or ""
    m = re.search(r'"command"\s*:\s*"([^"]*)"', c)
    if m:
        c = m.group(1)
    return bool(_READONLY.match(c))


def sweep(model_path):
    """Precision/recall of the GRAPH model across firing thresholds - the operating curve. Also splits the
    outcome positives into act vs read so we can see whether raising the bar costs real-act recall."""
    model = load_model(model_path)
    posM = positives_mistakes()
    negR = negatives_reads()
    posO = positives_outcomes()
    posO_act = [(c, s) for c, s in posO if not _is_read(c)]
    typ = load_typical()
    typ_act = [r for r in typ if not r["is_read"]]
    print("threshold sweep (graph model=%s)   mistakes=%d outcome-acts=%d typical=%d(act=%d)"
          % (os.path.basename(model_path), len(posM), len(posO_act), len(typ), len(typ_act)))
    print("  thr  | mistake-recall | outcome-ACT | TYPICAL-nag-all | TYPICAL-nag-act  (want recall hi, nag lo)")
    for thr in (0.8, 1.2, 1.6, 2.0, 2.4, 2.8, 3.2, 3.6):
        pc = sum(1 for ctx, exp in posM if exp & fire_graph(ctx, model, thr))
        oa = sum(1 for c, s in posO_act if fire_graph(c, model, thr))
        tn = sum(1 for r in typ if fire_graph(r["text"], model, thr))
        ta = sum(1 for r in typ_act if fire_graph(r["text"], model, thr))
        print("  %.1f  |   %2d/%-2d %.2f   | %3d/%-3d %.2f | %3d/%-3d %.2f    | %3d/%-3d %.2f"
              % (thr, pc, len(posM), pc / max(1, len(posM)), oa, len(posO_act), oa / max(1, len(posO_act)),
                 tn, len(typ), tn / max(1, len(typ)), ta, len(typ_act), ta / max(1, len(typ_act))))


TYPICAL = os.path.join(HERE, "typical_actions.jsonl")


def load_typical():
    out = []
    try:
        for l in io.open(TYPICAL, encoding="utf-8"):
            l = l.strip()
            if l:
                out.append(json.loads(l))
    except Exception:
        pass
    return out


def nag(model_path, thr):
    """Fire-rate on a REAL sample of ordinary tool calls = the nagging burden (mostly benign, so ~ the
    false-fire rate; an upper bound). Split by read-only vs act/write - the act column is the honest
    benign-ACT false-fire number the read-snippet negatives couldn't give."""
    model = load_model(model_path)
    typ = load_typical()
    rd = [r for r in typ if r["is_read"]]
    ac = [r for r in typ if not r["is_read"]]
    print("nagging on %d typical actions (read=%d act/write=%d)  model=%s thr=%.2f"
          % (len(typ), len(rd), len(ac), os.path.basename(model_path), thr))
    for name, fire in (("regex", lambda t: fire_regex(t)),
                       ("graph", lambda t: fire_graph(t, model, thr)),
                       ("union", lambda t: fire_regex(t) | fire_graph(t, model, thr))):
        af = sum(1 for r in typ if fire(r["text"]))
        rf = sum(1 for r in rd if fire(r["text"]))
        cf = sum(1 for r in ac if fire(r["text"]))
        print("  %-6s | all %.2f (%3d/%3d) | read %.2f (%2d/%2d) | act %.2f (%3d/%3d)"
              % (name, af / max(1, len(typ)), af, len(typ), rf / max(1, len(rd)), rf, len(rd),
                 cf / max(1, len(ac)), cf, len(ac)))


def main():
    thr = 1.05
    mp = os.path.join(HERE, "reflex_anchors.json")
    for a in sys.argv[1:]:
        if a.startswith("--model="):
            mp = a.split("=", 1)[1]
        if a.startswith("--thr="):
            thr = float(a.split("=", 1)[1])
    if "--sweep" in sys.argv:
        sweep(mp)
        return
    if "--nag" in sys.argv:
        nag(mp, thr)
        return
    if "--cal" in sys.argv:
        cal(os.path.join(HERE, "reflex_anchors_cal.json"))
        return
    res, nP, nN, nO = evaluate(mp, thr)
    print("model=%s  thr=%.2f   (mistakes=%d, read-negs=%d, outcomes=%d)" % (os.path.basename(mp), thr, nP, nN, nO))
    print("  %-6s | mistake-recall (95%% CI) | read false-fire (CI) | outcome-fire (CI)" % "method")
    for name in ("regex", "graph", "union"):
        r = res[name]
        print("  %-6s | %2d/%-2d %.2f [%.2f,%.2f]   | %3d/%-3d %.2f [%.2f,%.2f] | %3d/%-3d %.2f"
              % (name, r["recall_k"][0], r["recall_k"][1], r["mistake_recall"][0], r["mistake_recall"][1], r["mistake_recall"][2],
                 r["ff_k"][0], r["ff_k"][1], r["read_falsefire"][0], r["read_falsefire"][1], r["read_falsefire"][2],
                 r["oc_k"][0], r["oc_k"][1], r["outcome_fire"][0]))
    print("\nRead: union should recall >= regex (graph adds); watch read false-fire doesn't blow up.")


if __name__ == "__main__":
    main()
