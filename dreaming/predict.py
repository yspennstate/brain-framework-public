#!/usr/bin/env python3
"""predict.py - score the brain's predictions, so it learns from surprise.

The brain is a prediction machine (predictive processing: Rao-Ballard 1999; Friston; Clark 2013).
It should forecast and then learn from the gap. The learning signal is prediction ERROR / surprise,
not the outcome itself (Rescorla-Wagner 1972; Schultz 1997 - dopamine encodes reward-prediction-error).

This reads the append-only ledger memory/PREDICTIONS.md, scores every RESOLVED bet with two proper
measures, and writes:
  - predictions.tsv       (id, scope, p, outcome, brier, surprise_bits, claim)  for dream.py
  - <brain>/PREDICTIONS.md (open bets + a calibration read)                      for the wake ritual

Scores (Brier 1950; self-information):
  brier    = (p - o)^2                         lower is better; a proper scoring rule
  surprise = -log2( p if hit else 1-p )  bits  a CONFIDENT miss is maximally surprising; a hedged one barely
Calibration per scope: mean Brier, and mean-p vs hit-rate (over/under-confidence). An un-timestamped
prediction is worthless (hindsight rewrites "I knew it"), so only dated, resolved bets are scored.

Pure read-only over the memory dir. Usage: python predict.py [memory_dir]
"""
import argparse, os, re, io, math, sys
from collections import defaultdict


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


# Claude Code names a project directory after the home path, with ':' and the
# separators replaced by '-'. Deriving it keeps this tree free of one machine's
# username and correct on any other - and unlike a template token it is a real
# value at import time.
_BRAIN_SLUG = _brain_slug_resolve()
MEM_DEFAULT = os.path.join(os.path.expanduser("~"), ".claude", "projects", _BRAIN_SLUG, "memory")
BRAIN_DEFAULT = os.path.expanduser(r"~/brain-repo/01_ai_consciousness/brain")
DREAMING = os.path.dirname(os.path.abspath(__file__))

LINE = re.compile(r"^- \[( |x)\]\s*(.+)$")


def _field(parts, key):
    for p in parts:
        p = p.strip()
        if p.lower().startswith(key + ":"):
            return p.split(":", 1)[1].strip()
    return None


def parse(mem_dir=MEM_DEFAULT):
    path = os.path.join(mem_dir, "PREDICTIONS.md")
    bets = []
    try:
        text = io.open(path, "r", encoding="utf-8", errors="replace").read()
    except Exception:
        return bets
    for ln in text.splitlines():
        m = LINE.match(ln.strip())
        if not m:
            continue
        parts = m.group(2).split("|")
        bid = (parts[0].strip().split() or ["?"])[0]
        p = _field(parts, "p")
        try:
            p = float(p)
        except Exception:
            p = None
        claim = ""
        # the claim is the longest bare (key-less) segment
        bare = [s.strip() for s in parts[1:] if ":" not in s.split(" ")[0]]
        if bare:
            claim = max(bare, key=len)
        bets.append(dict(id=bid, done=(m.group(1) == "x"), scope=_field(parts, "scope") or "?",
                         p=p, by=_field(parts, "by"), outcome=(_field(parts, "outcome") or "").lower(),
                         claim=claim))
    return bets


def score(bets):
    for b in bets:
        b["brier"] = None
        b["surprise"] = None
        if b["outcome"] in ("hit", "miss") and b["p"] is not None:
            o = 1.0 if b["outcome"] == "hit" else 0.0
            b["brier"] = (b["p"] - o) ** 2
            pc = b["p"] if o == 1.0 else (1.0 - b["p"])
            pc = min(max(pc, 1e-6), 1 - 1e-9)
            b["surprise"] = -math.log2(pc)
    return bets


def lint(bets):
    """Ledger-status lies (the p119/p120 class): a flip whose fields contradict its checkbox.

    A `- [x]` bet with no outcome: field is resolved-in-form but invisible to scoring -
    it vanishes from the learning loop (neither open nor scored). The mirror ([ ] carrying
    a resolution) and a resolved hit/miss without a parseable p are the same lie from
    other sides. Caught by hand twice in three days (07-30 SWS, 07-31 nap) and staked by
    the OPEN_QUESTIONS flip-lint entry; this makes the catch mechanical. Read-only:
    reports, never rewrites. Legitimate outcomes are hit/miss (scored) and void (unscored).
    """
    problems = []
    for b in bets:
        if b["done"] and not b["outcome"]:
            problems.append("%s: [x] but no outcome: field" % b["id"])
        elif not b["done"] and b["outcome"] in ("hit", "miss", "void"):
            problems.append("%s: outcome:%s but checkbox still [ ]" % (b["id"], b["outcome"]))
        elif b["done"] and b["outcome"] in ("hit", "miss") and b["p"] is None:
            problems.append("%s: resolved %s but p missing/unparseable - unscoreable" % (b["id"], b["outcome"]))
        if not re.match(r"^[a-z][a-z0-9_-]{1,15}$", b["scope"] or ""):
            problems.append("%s: malformed scope: field (%r...) - swallows the claim and garbles calibration"
                            % (b["id"], (b["scope"] or "?")[:40]))
    return problems


def calibration(bets):
    """Per scope: n resolved, mean Brier, mean-p, and a bias verdict a single sample can't swing.

    The naive verdict (mean-p vs raw hit-rate) declares "underconfident" off ONE resolved bet - which
    is how PREDICTIONS.md ended up asserting a verdict from n=1. Fix: shrink each scope's hit-rate toward
    the GLOBAL hit-rate with a Beta-Binomial prior of strength KAPPA, so a thin scope stays near the base
    rate until it has earned a verdict, and label anything under 3 resolved as provisional. (Metacognition
    scout / Moore-Healy base-rate anchoring.)"""
    KAPPA = 4.0
    resolved = [b for b in bets if b["brier"] is not None]
    g = (sum(1 for b in resolved if b["outcome"] == "hit") / len(resolved)) if resolved else 0.5
    by = defaultdict(list)
    for b in resolved:
        by[b["scope"]].append(b)
    out = {}
    for scope, bs in by.items():
        n = len(bs)
        mp = sum(b["p"] for b in bs) / n
        hits = sum(1 for b in bs if b["outcome"] == "hit")
        hr = hits / n
        hr_s = (hits + KAPPA * g) / (n + KAPPA)   # shrunk toward the global base rate
        mb = sum(b["brier"] for b in bs) / n
        gap = hr_s - mp
        if n < 3:
            bias = "provisional (too few)"
        else:
            bias = "overconfident" if gap < -0.12 else ("underconfident" if gap > 0.12 else "calibrated")
        out[scope] = dict(n=n, mean_p=mp, hit_rate=hr, hit_rate_shrunk=hr_s, mean_brier=mb, bias=bias, base_rate=g)
    return out


def write_outputs(bets, cal, problems=None, dreaming=DREAMING, brain=BRAIN_DEFAULT):
    tsv = os.path.join(dreaming, "predictions.tsv")
    with io.open(tsv, "w", encoding="utf-8") as f:
        f.write("id\tscope\tp\toutcome\tbrier\tsurprise_bits\tclaim\n")
        for b in bets:
            if b["brier"] is None:
                continue
            f.write(f"{b['id']}\t{b['scope']}\t{b['p']}\t{b['outcome']}\t"
                    f"{b['brier']:.3f}\t{b['surprise']:.2f}\t{b['claim'][:90]}\n")
    if brain and os.path.isdir(brain):
        openb = [b for b in bets if not b["done"]]
        resolved = [b for b in bets if b["brier"] is not None]
        with io.open(os.path.join(brain, "PREDICTIONS.md"), "w", encoding="utf-8") as f:
            f.write("# The brain's forecasts (predictive processing)\n\n")
            f.write("What the brain currently expects, and how well its past bets scored. Surprise (bits) "
                    "is the learning signal - a confident miss teaches most. Regenerated by `predict.py`.\n\n")
            f.write(f"## The brain expects ({len(openb)} open bets)\n\n")
            for b in sorted(openb, key=lambda b: -(b["p"] or 0))[:8]:
                f.write(f"- p={b['p']} [{b['scope']}] {b['claim'][:110]}\n")
            if cal:
                f.write("\n## Calibration (how much to trust our own confidence)\n\n")
                for scope, c in sorted(cal.items()):
                    f.write(f"- **{scope}**: {c['n']} resolved, mean-p {c['mean_p']:.2f} vs hit-rate "
                            f"{c['hit_rate']:.2f} (shrunk {c['hit_rate_shrunk']:.2f}) -> **{c['bias']}** "
                            f"(Brier {c['mean_brier']:.2f})\n")
            if resolved:
                f.write("\n## Biggest surprises (what to sit with)\n\n")
                for b in sorted(resolved, key=lambda b: -(b["surprise"] or 0))[:6]:
                    f.write(f"- {b['surprise']:.1f} bits ({b['outcome']}, p={b['p']}) {b['claim'][:100]}\n")
            if problems:
                f.write("\n## Ledger lint (status lies to repair in memory/PREDICTIONS.md)\n\n")
                for issue in problems[:10]:
                    f.write("- %s\n" % issue)
                if len(problems) > 10:
                    f.write("- ... and %d more (run predict.py for the full list)\n" % (len(problems) - 10))
    return tsv


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    # A REAL PARSER, added 2026-08-22 after the SIXTH recurrence of the same defect.
    # `sys.argv[1]` swallowed ANY token as the memory directory, so `predict.py --help`
    # looked for "--help/PREDICTIONS.md", found no bets, and REWROTE both derived
    # outputs with zero rows - destroying 136 scored bets in predictions.tsv and the
    # brain's PREDICTIONS.md view. Recoverable only because memory/PREDICTIONS.md is
    # the append-only source. instincts.py had the identical bug and the identical fix
    # on 2026-07-20; see script_help_flag_can_execute_work_2026_07_14.
    # Controls that must hold: --help exits 0 and writes nothing; an unknown flag exits 2
    # and writes nothing; no args behaves exactly as before.
    ap = argparse.ArgumentParser(
        prog="predict.py",
        description="Score the append-only prediction ledger and regenerate the derived views. "
                    "READ-ONLY over the memory dir; WRITES dreaming/predictions.tsv and "
                    "brain/PREDICTIONS.md.")
    ap.add_argument("memory_dir", nargs="?", default=MEM_DEFAULT,
                    help="directory containing PREDICTIONS.md (default: %(default)s)")
    ap.add_argument("--dry-run", action="store_true",
                    help="score and report, but write no derived files")
    a = ap.parse_args()
    if not os.path.isdir(a.memory_dir):
        ap.error("memory_dir '%s' is not a directory - refusing to run. A missing ledger "
                 "would score zero bets and overwrite the derived views with nothing."
                 % a.memory_dir)
    mem = a.memory_dir
    bets = score(parse(mem))
    cal = calibration(bets)
    problems = lint(bets)
    if a.dry_run:
        print("dry-run: %d bets parsed from %s; no files written" % (len(bets), mem))
        return
    write_outputs(bets, cal, problems)
    openb = sum(1 for b in bets if not b["done"])
    res = sum(1 for b in bets if b["brier"] is not None)
    calstr = ", ".join(f"{k}={v['bias']}" for k, v in cal.items()) or "no resolved bets yet"
    print(f"predict: {len(bets)} bets ({openb} open, {res} resolved+scored). calibration: {calstr}")
    if problems:
        head = "; ".join(problems[:6])
        more = " ..." if len(problems) > 6 else ""
        print("lint: %d status lies in PREDICTIONS.md: %s%s" % (len(problems), head, more))
    else:
        print("lint: ledger clean")


if __name__ == "__main__":
    main()
