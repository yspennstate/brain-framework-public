"""Cascade rule mining over the firing log: do stable reflex cascades exist, where
seeing a SUBSET of them reliably predicts another reflex firing?

This is the owner's "if 5 fire in a certain way... the correct node fires" made
measurable. It is association-rule mining (Agrawal 1993) over the co-firing sets in the
shadow log: frequent reflex itemsets, then rules {A,B,C} -> D scored by support (how often
the antecedent fires) and confidence (how often D fires given the antecedent), lifted over
D's base rate so a ubiquitous consequent doesn't look predictive. A high-confidence,
high-lift rule is a real cascade: the antecedent firing is genuine evidence the consequent
should fire, which is exactly a cascade that can complete to the missing node.

Read-only over the firing log. `python cascade_rules.py [shadow-fires.tsv]`.
"""
import io, math, os, re, sys
from collections import Counter
from itertools import combinations

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


NAMESET = re.compile(r"^[a-z][a-z0-9_]+(,[a-z][a-z0-9_]+)*$")


def _reflex_field(cols):
    for c in cols:
        c = c.strip()
        if NAMESET.match(c) and "_" in c and len(c) > 3:
            return c
    return None


def load_firing_sets(path):
    sets = []
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            cols = ln.rstrip("\n").split("\t")
            if len(cols) < 2:
                continue
            rf = _reflex_field(cols[1:])
            if rf:
                s = frozenset(x for x in rf.split(",") if x)
                if len(s) >= 2:
                    sets.append(s)
    return sets


def mine_rules(sets, min_support=30, min_confidence=0.6, max_ante=3):
    n = len(sets)
    item = Counter()
    for s in sets:
        item.update(s)
    frequent = {i for i, c in item.items() if c >= min_support}

    # count co-occurrence of small antecedents and antecedent+consequent
    ante_count = Counter()
    pair_count = Counter()   # (antecedent_frozenset, consequent) -> count
    for s in sets:
        fs = sorted(s & frequent)
        for k in range(1, min(max_ante, len(fs) - 1) + 1):
            for ante in combinations(fs, k):
                a = frozenset(ante)
                ante_count[a] += 1
                for cons in fs:
                    if cons not in a:
                        pair_count[(a, cons)] += 1

    rules = []
    for (a, cons), c in pair_count.items():
        ac = ante_count[a]
        if ac < min_support:
            continue
        conf = c / ac
        if conf < min_confidence:
            continue
        base = item[cons] / n
        lift = conf / base if base > 0 else 0.0
        if lift < 1.2:
            continue
        rules.append((conf, lift, ac, tuple(sorted(a)), cons))
    rules.sort(key=lambda r: (-r[0], -r[1]))
    return rules, n


class CascadeCompleter:
    """Realizes the cascade-completion vision: given the reflexes that have fired in the
    current context, surface the consequent that the mined rules predict. "If a cascade of
    nodes fires in a certain way, the correct node fires and you get its information."

    Built from the mined rules (mine_rules output). A rule fires a completion when its full
    antecedent is present in the active set AND the consequent has not already fired. Only
    rules whose consequent is RARE (low base rate -> high lift) are worth surfacing - a
    ubiquitous consequent carries no information even at high confidence - so completions are
    ranked by lift, and a lift floor filters the base-rate rules.
    """

    def __init__(self, rules, min_lift=3.0):
        # index rules by antecedent for O(active choose k) lookup
        self.by_ante = {}
        for conf, lift, ac, ante, cons in rules:
            if lift >= min_lift:
                self.by_ante.setdefault(frozenset(ante), []).append((conf, lift, cons))

    def complete(self, active_reflexes, top=3):
        """Return [(consequent, confidence, lift), ...] the active set predicts but that
        have not fired, ranked by lift (most informative first), deduped by consequent."""
        active = set(active_reflexes)
        best = {}
        for ante, cons_list in self.by_ante.items():
            if ante <= active:                       # antecedent fully present
                for conf, lift, cons in cons_list:
                    if cons in active:               # already fired - nothing to surface
                        continue
                    if cons not in best or lift > best[cons][1]:
                        best[cons] = (conf, lift)
        out = [(cons, conf, lift) for cons, (conf, lift) in best.items()]
        out.sort(key=lambda x: -x[2])
        return out[:top]


def _selftest():
    # plant a known cascade: whenever {a,b,c} fire, d fires; noise sets never include d.
    sets = []
    for _ in range(100):
        sets.append(frozenset({"reflex_a", "reflex_b", "reflex_c", "reflex_d"}))
    for _ in range(100):
        sets.append(frozenset({"noise_x", "noise_y", "noise_z"}))
    for _ in range(50):
        sets.append(frozenset({"reflex_a", "reflex_b"}))   # antecedent without full set
    rules, n = mine_rules(sets, min_support=30, min_confidence=0.6)
    # the rule {reflex_a,reflex_b,reflex_c} -> reflex_d must be found with high confidence
    found = [r for r in rules if r[4] == "reflex_d" and set(r[3]) >= {"reflex_a", "reflex_b", "reflex_c"}]
    assert found, "planted cascade not recovered"
    assert found[0][0] >= 0.99, f"confidence too low: {found[0][0]}"
    # a rule from the noise cluster must NOT predict reflex_d
    assert not any(r[4] == "reflex_d" and "noise_x" in r[3] for r in rules), "spurious noise->d rule"
    # the completer must surface reflex_d when {a,b,c} are active, and NOT when the noise
    # set is active (do no harm), and NOT when d has already fired.
    comp = CascadeCompleter(rules, min_lift=1.5)
    got = comp.complete({"reflex_a", "reflex_b", "reflex_c"})
    assert any(c == "reflex_d" for c, _, _ in got), f"completer missed the cascade: {got}"
    assert not comp.complete({"noise_x", "noise_y", "noise_z"}), "completer fired on noise"
    assert not any(c == "reflex_d" for c, _, _ in
                   comp.complete({"reflex_a", "reflex_b", "reflex_c", "reflex_d"})), \
        "completer surfaced an already-fired consequent"
    # persistence round-trip: save -> load -> completer still surfaces d
    import tempfile
    fp = os.path.join(tempfile.mkdtemp(), "rules.json")
    save_rules(rules, fp)
    reloaded = load_rules(fp)
    assert reloaded == rules, "rule persistence round-trip changed the rules"
    comp2 = CascadeCompleter(reloaded, min_lift=1.5)
    assert any(c == "reflex_d" for c, _, _ in comp2.complete({"reflex_a", "reflex_b", "reflex_c"})), \
        "completer from reloaded rules missed the cascade"
    print(f"cascade_rules selftest OK: recovered {{a,b,c}}->d at conf {found[0][0]:.2f}, "
          f"{len(rules)} rules; completer surfaces d, silent on noise; persistence round-trips")
    return 0


def save_rules(rules, path):
    """Persist mined rules (JSON) so a dream can write them and a live hook can load them
    without re-mining the whole log each fire."""
    import json
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump([[conf, lift, ac, list(ante), cons]
                   for conf, lift, ac, ante, cons in rules], fh)


def load_rules(path):
    import json
    with io.open(path, encoding="utf-8") as fh:
        return [(c, l, ac, tuple(a), cons) for c, l, ac, a, cons in json.load(fh)]


def main(argv=None):
    args = list(argv) if argv is not None else sys.argv[1:]
    if args and args[0] == "--selftest":
        return _selftest()
    save_to = None
    if "--save" in args:
        i = args.index("--save")
        save_to = args[i + 1]
        args = args[:i] + args[i + 2:]
    path = (args or [os.path.expanduser(
        r"~/.claude/dreaming/graph_fire/gf_shadow_fires.tsv")])[0]
    if not os.path.exists(path):
        print(f"no shadow log at {path}")
        return 1
    sets = load_firing_sets(path)
    rules, n = mine_rules(sets)
    if save_to:
        save_rules(rules, save_to)
        print(f"saved {len(rules)} rules -> {save_to}")
    print(f"firing sets: {n:,}   cascade rules (conf>=0.6, lift>=1.2): {len(rules)}")
    print("\ntop cascades (antecedent -> consequent  conf/lift, antecedent support):")
    for conf, lift, ac, ante, cons in rules[:20]:
        print(f"  {conf:.2f} conf  {lift:>4.1f}x  n={ac:<5} {{{', '.join(ante)}}} -> {cons}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
