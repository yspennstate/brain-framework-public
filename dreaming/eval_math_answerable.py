"""Score the math brain on hand-labelled REALISTIC questions - the benchmark that isn't self-flattering.

WHY THIS EXISTS. The other math harness, eval_math_selfretrieval.py, queries a claim with its own
text. That is a necessary floor but a lexical task by construction, and it flatters any lexical-leaning
ranker. Measured 2026-08-22: self-retrieval says hit@1 58.6%, while realistic conversational questions
say 3.3%. Almost all apparent competence was the store recognising a quotation.

THE SET (state/math_answerable_set.jsonl, hand-built, 90 items):
  POS    60 conversational questions the store CAN answer, each with its gold claim id, tagged with a
         phrasing style (plain / terse / verbose / typo / command)
  NEG_A  30 questions about mathematics VERIFIED ABSENT from every claim in the store, written in the
         same register at matched length -> only answerability differs, so POS vs NEG_A is the
         controlled abstention test that register-mismatched comparisons cannot make

TWO TRAPS THIS HARNESS EXISTS TO AVOID, both of which caught the author:
  1. A question that NAMES its answer ("what is Sylow's theorem") tests keyword lookup, not retrieval.
  2. A question that dodges the title but near-quotes the BODY inflates a SEMANTIC ranker specifically.
     Body overlap is therefore reported per item and results are broken out by overlap band. The
     aggregate hit@6 was inflated ~14 points by 14 near-quotation items before this was added; always
     read the low-overlap row.

  3. A SINGLE GOLD ID IS WRONG IN A STORE WITH DUPLICATES, and this one has them badly: 1,131 claims
     collapse to 901 distinct normalised titles, 230 redundant copies, 35.8% of claims involved.
     Measured 2026-08-22 - the harness recorded a MISS on a question whose top three returns were
     "Baire Category Theorem", "Baire category theorem" and "Baire category theorem (complete metric
     spaces)", because the gold id was a fourth copy. That understated every number by 7-21 points.
     Gold is therefore a SET: any claim sharing the gold's normalised title counts as correct.
     Both columns are printed, because normalised-title equality can merge two genuinely different
     results with the same name (Lagrange's theorem is the group-theory one AND the mean-value one),
     so the dup-aware column is an UPPER bound and the truth is between them.

Reports production by default. --arms adds offline re-rankings (they never touch the live scorer).
Read-only.
"""
import sys, os, io, json, re, argparse, statistics
from collections import defaultdict

sys.path.insert(0, "~/math-brain-build/src")
from math_brain.retrieval import retrieve
from math_brain.store import ResearchStore

SET = os.path.expanduser("~/.claude/dreaming/state/math_answerable_set.jsonl")
STORE = "~/math-brain-build/local/population/brain.sqlite3"
GENERIC = {"theorem", "lemma", "formula", "identity", "process", "space", "spaces", "inequality",
           "theorems", "number", "numbers", "system", "linear", "real", "finite", "graph", "matrix",
           "map", "maps", "sequence", "conditions", "convergence", "point", "points", "what", "does",
           "with", "have", "that", "this", "from", "your", "which", "there", "them", "they", "want",
           "need", "give", "tell", "about", "like", "just", "keep", "into", "over", "some", "when"}
STOP = set("the of is a an and or to in for on it as at be by we i n x y f k m t that this with from".split())
VAR = re.compile(r"^v\d+$")
TERM = re.compile(r"matched normalized term '([^']+)'")
HASH = re.compile(r"^(sha256:|[0-9a-f]{32,})")


def _content(t):
    if t.startswith(("punct:", "op:", "num:")):
        return False
    if VAR.match(t) or t.isdigit() or HASH.match(t):
        return False
    return not (len(t) <= 2 or t in STOP)


def content_lex(h):
    return sum(tr.amount for tr in h.trace
               if str(getattr(tr.channel, "value", tr.channel)) == "lexical"
               and (lambda m: m and _content(m.group(1)))(TERM.search(getattr(tr, "detail", "") or "")))


ARMS = {
    "production": lambda h: h.lexical_score + h.symbol_score + h.formula_score + h.graph_score,
    "content-lex+0.25graph": lambda h: content_lex(h) + h.symbol_score + h.formula_score + 0.25 * h.graph_score,
    "content-lex-alone": lambda h: content_lex(h),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=SET)
    ap.add_argument("--store", default=STORE)
    ap.add_argument("--slate", type=int, default=6)
    ap.add_argument("--arms", action="store_true", help="also score offline re-rankings")
    a = ap.parse_args()

    st = ResearchStore(a.store)
    nodes = list(st.iter_nodes())
    edges = list(st.iter_edges())
    byid = {n.id: n for n in nodes}
    def _norm_title(t):
        t = (t or "").lower()
        t = re.sub(r"\(.*?\)", " ", t)          # drop parenthetical qualifiers
        t = re.sub(r"[^a-z0-9 ]", " ", t)
        t = re.sub(r"\b(the|of|a|an|for|and|in|on|to)\b", " ", t)
        return " ".join(t.split())

    bytitle = defaultdict(set)
    for n in nodes:
        bytitle[_norm_title(n.title)].add(n.id)

    rows = [json.loads(l) for l in io.open(a.set, encoding="utf-8")]
    POS = [r for r in rows if r["cls"] == "POS" and r.get("gold") in byid]
    NEG = [r for r in rows if r["cls"] == "NEG_A"]
    print("set %s: %d answerable questions, %d verified-unanswerable" % (os.path.basename(a.set), len(POS), len(NEG)))
    print("store: %d claims, %d edges\n" % (len(nodes), len(edges)))

    for r in POS:
        g = byid[r["gold"]]
        qw = set(w for w in re.findall(r"[a-z]{4,}", r["q"].lower()) if w not in GENERIC)
        bw = set(re.findall(r"[a-z]{4,}", ((g.body or "") + " " + (g.title or "")).lower()))
        r["_ov"] = len(qw & bw) / max(1, len(qw))

    use = ["production"] + ([k for k in ARMS if k != "production"] if a.arms else [])
    res = {k: defaultdict(lambda: {"h1": 0, "h6": 0, "d1": 0, "d6": 0, "n": 0}) for k in use}
    for r in POS:
        rr = retrieve(r["q"], nodes=nodes, edges=edges, limit=2000,
                      min_support_score=0.0, min_margin=0.0)
        if not rr.hits:
            continue
        bands = ["all"]
        bands.append("low<0.60" if r["_ov"] < 0.60 else "high>=0.60")
        if r["_ov"] < 0.45:
            bands.append("strict<0.45")
        bands.append("style:" + r.get("style", "plain"))
        for k in use:
            f = ARMS[k]
            ids = [i for _, i in sorted(((f(h), h.node.id) for h in rr.hits), key=lambda t: (-t[0], t[1]))][:a.slate]
            goldset = bytitle[_norm_title(byid[r["gold"]].title)]
            for b in bands:
                d = res[k][b]
                d["n"] += 1
                d["h1"] += (ids[0] == r["gold"]) if ids else 0
                d["h6"] += r["gold"] in ids
                d["d1"] += (ids[0] in goldset) if ids else 0
                d["d6"] += bool(goldset & set(ids))

    order = ["all", "low<0.60", "strict<0.45", "high>=0.60"]
    order += sorted(b for b in res[use[0]] if b.startswith("style:"))
    for k in use:
        print("ARM: %s   (strict single-gold -> duplicate-aware)" % k)
        for b in order:
            d = res[k].get(b)
            if not d or not d["n"]:
                continue
            note = "   <- READ THIS ONE" if b == "low<0.60" else ("   (inflated: near-quotations)" if b.startswith("high") else "")
            print("   %-14s n=%3d   hit@1 %5.1f%% -> %5.1f%%   hit@%d %5.1f%% -> %5.1f%%%s"
                  % (b, d["n"], 100.0 * d["h1"] / d["n"], 100.0 * d["d1"] / d["n"],
                     a.slate, 100.0 * d["h6"] / d["n"], 100.0 * d["d6"] / d["n"], note))
        print()
    ov = [r["_ov"] for r in POS]
    print("body overlap: median %.2f, p25 %.2f, p75 %.2f  (high overlap flatters semantic rankers)"
          % (statistics.median(ov), sorted(ov)[len(ov) // 4], sorted(ov)[3 * len(ov) // 4]))


if __name__ == "__main__":
    main()
