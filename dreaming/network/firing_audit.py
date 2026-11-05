"""Firing-sequence audit: read the reflex-firing shadow log and report how the
subconscious is actually behaving - which reflexes fire, which fire TOGETHER (the
cascade structure), how the rate trends, and where habituation may be needed.

This is one of the three signals the development loop watches ("how firing sequences
are going"). It is read-only and cheap; run it any time, and once per dream so the
cascade structure is tracked over the program.

Input: a shadow-fire TSV whose lines are
    <epoch> \t <session-or-verdict> \t <comma-separated reflex names> \t <context text>
(the exact columns vary by logger; the reflex-name column is auto-detected as the
first column that looks like a comma-joined set of snake_case names).

  python firing_audit.py [path-to-shadow-fires.tsv]
"""
import io, math, os, re, sys
from collections import Counter
from itertools import combinations

NAMESET = re.compile(r"^[a-z][a-z0-9_]+(,[a-z][a-z0-9_]+)*$")


def reflex_field(cols):
    for c in cols:
        c = c.strip()
        if "," in c and NAMESET.match(c):
            return c
        if NAMESET.match(c) and len(c) > 3 and "_" in c:
            return c
    return None


def _selftest():
    """Prove the reflex-field detection and the surprise ranking on a synthetic log:
    a reflex firing on 8/10 events must rank as lowest-information; one firing on 1/10
    as highest. A failure here means the audit would mislead the development loop."""
    import tempfile
    rows = []
    ts = 1_700_000_000
    for i in range(10):
        names = ["ubiquitous_reflex"]              # fires every event
        if i % 5 == 0:
            names.append("rare_reflex")            # fires 2/10
        if i < 8:
            names.append("common_reflex")          # fires 8/10
        rows.append(f"{ts+i}\ts{i}\t{','.join(names)}\tcontext text here")
    fd, p = tempfile.mkstemp(suffix=".tsv")
    with io.open(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(rows) + "\n")
    # reparse with the same logic and assert the surprise ordering
    per = Counter()
    n = 0
    with io.open(p, encoding="utf-8") as fh:
        for ln in fh:
            cols = ln.rstrip("\n").split("\t")
            rf = reflex_field(cols[1:])
            assert rf, f"reflex field not detected in {cols!r}"
            per.update(rf.split(","))
            n += 1
    os.remove(p)
    bits = {name: -math.log2(c / n) for name, c in per.items()}
    assert bits["ubiquitous_reflex"] < bits["common_reflex"] < bits["rare_reflex"], bits
    assert bits["ubiquitous_reflex"] == 0.0, bits
    print("firing_audit selftest OK:", {k: round(v, 2) for k, v in bits.items()})
    return 0


def main(argv=None):
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    args = argv if argv is not None else sys.argv[1:]
    if args and args[0] == "--selftest":
        return _selftest()
    path = (args or [os.path.expanduser(
        r"~/.claude/dreaming/graph_fire/gf_shadow_fires.tsv")])[0]
    if not os.path.exists(path):
        print(f"no shadow log at {path}")
        return 1

    n = 0
    first_ts = last_ts = None
    per_reflex = Counter()
    cofire = Counter()
    fires_per_reflex_events = 0
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            cols = ln.rstrip("\n").split("\t")
            if len(cols) < 2:
                continue
            try:
                ts = float(cols[0])
                first_ts = ts if first_ts is None else min(first_ts, ts)
                last_ts = ts if last_ts is None else max(last_ts, ts)
            except ValueError:
                pass
            rf = reflex_field(cols[1:])
            if not rf:
                continue
            names = [x for x in rf.split(",") if x]
            if not names:
                continue
            n += 1
            per_reflex.update(names)
            fires_per_reflex_events += 1
            for a, b in combinations(sorted(set(names)), 2):
                cofire[(a, b)] += 1

    span_days = ((last_ts - first_ts) / 86400.0) if (first_ts and last_ts) else 0.0
    print(f"firing events (with a reflex set): {n:,}")
    print(f"span: {span_days:.1f} days   rate: {n / span_days:.0f}/day"
          if span_days > 0 else f"span: n/a")
    print(f"distinct reflexes fired: {len(per_reflex)}")

    print("\ntop reflexes by fire count (habituation candidates at the top):")
    for name, c in per_reflex.most_common(15):
        print(f"  {c:>7,}  {name}")

    print("\ntop co-firing pairs (the cascade edges - reflexes that travel together):")
    for (a, b), c in cofire.most_common(15):
        # lift: how much more they co-fire than chance
        pa, pb = per_reflex[a] / n, per_reflex[b] / n
        lift = (c / n) / (pa * pb) if pa and pb else 0.0
        print(f"  {c:>7,}  lift {lift:>5.1f}x  {a} + {b}")

    # surprise weighting: a reflex's information content is -log2(p_fire). A reflex that
    # fires on most actions carries almost no information and, by drowning the rare ones,
    # trains the reader to ignore ALL banners. Firing-priority should be surprise-weighted,
    # not count-weighted. Below ~2 bits (fires on >25% of events) is a habituation candidate.
    print("\nsurprise weighting (-log2 p_fire = bits of information per fire):")
    surprises = []
    for name, c in per_reflex.items():
        p = c / n
        bits = -math.log2(p) if p > 0 else float("inf")
        surprises.append((bits, name, c))
    surprises.sort()
    print("  lowest-information (deprioritize surfacing - habituation candidates):")
    for bits, name, c in surprises[:6]:
        flag = "  <-- fires on >25% of events" if bits < 2.0 else ""
        print(f"    {bits:>4.1f} bits  {name} ({100*c/n:.0f}% of fires){flag}")
    print("  highest-information (rare, high-signal - must not be buried):")
    for bits, name, c in surprises[-6:]:
        print(f"    {bits:>4.1f} bits  {name} ({c} fires)")

    # a concrete finding: reflexes that ONLY ever fire inside a pair (never alone-ish)
    print("\nreflexes whose fires are >90% inside their top co-fire (fused, not independent):")
    fused = 0
    for name, c in per_reflex.most_common(40):
        partners = [(cofire[tuple(sorted((name, o)))], o)
                    for o in per_reflex if o != name]
        if not partners:
            continue
        top_c, top_o = max(partners)
        if c >= 50 and top_c / c > 0.9:
            print(f"  {name}  ({c:,} fires, {100*top_c/c:.0f}% with {top_o})")
            fused += 1
    if not fused:
        print("  (none - reflexes fire independently enough)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
