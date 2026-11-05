#!/usr/bin/env python3
"""Does temporal order add recall signal beyond the undirected co-read graph?

The live association graph remembers which memories were read in the same focused
session, but deliberately discards order.  Human episodic retrieval does not:
temporal-context models predict contiguity, and hippocampal replay preserves ordered
trajectories.  A useful AI analogue would let reading step A prime the memory that
usually follows it at step B, especially for workflow knowledge.

This is a causal, read-only gate over ``retrievals.tsv``.  For each chronological
train/test split it learns only from earlier sessions, then predicts the next
*distinct* memory read in later sessions.  It compares:

* popularity: how often the target was read in training;
* co-read: the current undirected, focus-weighted same-session signal;
* immediate transition: directed A -> B counts;
* successor trace: directed A -> later-B credit over four steps, decayed by 0.5;
* co-read + successor: an equal blend of per-source conditional probabilities.

The metric is conditional pairwise accuracy: for each real A -> B transition, does
the signal score B above memories that never followed A in that held-out fold?
Ties count as 0.5, so an absent/sparse signal honestly scores chance.  Sessions are
kept wholly on one side of the split; giant survey sessions are excluded. Sessions
that cross a chronological cutoff are discarded rather than letting their later
reads leak into training. The three fixed splits are robustness checks, not tuning.

Ship a temporal edge only if the successor signal beats co-read consistently and
the blend adds >= 0.03 on the mean usable chronological folds with useful coverage. Otherwise keep the
result as a measured null and let the trace mature.

Neuroscience basis: Howard & Kahana's temporal-context model; Foster & Wilson's
ordered replay; Stachenfeld et al.'s hippocampus-as-successor-representation view.
Usage: python eval_temporal_context.py [path/to/retrievals.tsv]
"""

from __future__ import print_function

import io
import os
import sys
from collections import Counter, defaultdict


DR = os.path.expanduser(r"~/.claude/dreaming")
TRACE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DR, "retrievals.tsv")
MAX_SESSION_MEMORIES = 25
HORIZON = 4
GAMMA = 0.5
SPLITS = (0.60, 0.70, 0.80)


def load_sessions(path):
    """Return sessions in first-event order, with consecutive duplicate reads folded."""
    by_session = defaultdict(list)
    try:
        rows = io.open(path, encoding="utf-8", errors="replace")
    except OSError:
        return []
    with rows:
        for i, line in enumerate(rows):
            if i == 0:
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) < 4:
                continue
            try:
                epoch = float(p[1])
            except (TypeError, ValueError):
                continue
            by_session[p[3]].append((epoch, p[0]))

    sessions = []
    for sid, events in by_session.items():
        events.sort()
        seq = []
        for _epoch, slug in events:
            if not seq or seq[-1] != slug:
                seq.append(slug)
        unique = len(set(seq))
        if 2 <= unique <= MAX_SESSION_MEMORIES and len(seq) >= 2:
            sessions.append((events[0][0], events[-1][0], sid, seq))
    sessions.sort()
    return sessions


def train_signals(sessions):
    popularity = Counter()
    coread = defaultdict(float)
    immediate = defaultdict(float)
    successor = defaultdict(float)

    for _start, _end, _sid, seq in sessions:
        # Focus-weight broad sessions exactly in the spirit of coactivation_end:
        # one survey should not wire every pair as strongly as a tight session.
        uniq = list(dict.fromkeys(seq))
        focus = 1.0 / max(1, len(uniq) - 1)
        popularity.update(uniq)
        for i, a in enumerate(uniq):
            for b in uniq[i + 1:]:
                coread[(a, b)] += focus
                coread[(b, a)] += focus

        for i, a in enumerate(seq[:-1]):
            b = seq[i + 1]
            if a != b:
                immediate[(a, b)] += 1.0
            seen = set()
            for lag, target in enumerate(seq[i + 1:i + 1 + HORIZON], start=1):
                if target == a or target in seen:
                    continue
                seen.add(target)
                successor[(a, target)] += GAMMA ** (lag - 1)

    def normalize(table):
        totals = defaultdict(float)
        for (source, _target), value in table.items():
            totals[source] += value
        return {pair: value / totals[pair[0]] for pair, value in table.items()
                if totals[pair[0]] > 0}

    pop_total = float(sum(popularity.values())) or 1.0
    return {
        "popularity": {slug: count / pop_total for slug, count in popularity.items()},
        "co-read": normalize(coread),
        "immediate": normalize(immediate),
        "successor": normalize(successor),
    }


def transitions(sessions):
    out = []
    actual_by_source = defaultdict(set)
    for _start, _end, sid, seq in sessions:
        for a, b in zip(seq, seq[1:]):
            if a != b:
                out.append((sid, a, b))
                actual_by_source[a].add(b)
    return out, actual_by_source


def score_fold(train, test):
    sig = train_signals(train)
    known = set(sig["popularity"])
    rows, actual = transitions(test)
    eligible = [(sid, a, b) for sid, a, b in rows if a in known and b in known]
    names = ("popularity", "co-read", "immediate", "successor", "blend")
    wins = {name: 0.0 for name in names}
    pairs = {name: 0 for name in names}
    covered = {name: 0 for name in names}

    def value(name, a, b):
        if name == "popularity":
            return sig[name].get(b, 0.0)
        if name == "blend":
            return 0.5 * sig["co-read"].get((a, b), 0.0) + 0.5 * sig["successor"].get((a, b), 0.0)
        return sig[name].get((a, b), 0.0)

    # Compare each true next-read with every held-out-safe negative.  Excluding all
    # targets that really followed A in the test fold avoids labelling another valid
    # continuation as a negative merely because it occurred in a different session.
    candidates = sorted(known)
    for _sid, a, b in eligible:
        negs = [c for c in candidates if c != a and c != b and c not in actual.get(a, set())]
        if not negs:
            continue
        for name in names:
            pos = value(name, a, b)
            if pos > 0:
                covered[name] += 1
            for c in negs:
                neg = value(name, a, c)
                wins[name] += 1.0 if pos > neg else 0.5 if pos == neg else 0.0
                pairs[name] += 1

    accuracy = {name: wins[name] / pairs[name] if pairs[name] else 0.5 for name in names}
    coverage = {name: covered[name] / len(eligible) if eligible else 0.0 for name in names}
    return accuracy, coverage, len(eligible), len(rows), len(known)


def main():
    sessions = load_sessions(TRACE)
    if len(sessions) < 20:
        print("eval_temporal_context: only %d usable sessions; need >=20." % len(sessions))
        return 1

    print("eval_temporal_context: %d focused sessions; ordered next-read prediction" % len(sessions))
    print("  %-5s %5s %5s %5s  %7s %7s %9s %9s %7s" %
          ("split", "train", "test", "n", "popular", "co-read", "immediate", "successor", "blend"))
    fold_results = []
    for fraction in SPLITS:
        nominal_cut = max(1, min(len(sessions) - 1, int(len(sessions) * fraction)))
        cutoff = sessions[nominal_cut][0]
        train = [s for s in sessions if s[1] < cutoff]
        test = [s for s in sessions if s[0] >= cutoff]
        straddlers = len(sessions) - len(train) - len(test)
        acc, cov, n, raw_n, known_n = score_fold(train, test)
        fold_results.append((fraction, acc, cov, n))
        print("  %3d%%  %5d %5d %5d  %7.3f %7.3f %9.3f %9.3f %7.3f" %
              (int(100 * fraction), len(train), len(test), n,
               acc["popularity"], acc["co-read"], acc["immediate"],
               acc["successor"], acc["blend"]))
        print("        coverage of true transitions (co-read / immediate / successor / blend): "
              "%.2f / %.2f / %.2f / %.2f  [raw transitions=%d, known memories=%d, straddlers=%d]" %
              (cov["co-read"], cov["immediate"], cov["successor"], cov["blend"],
               raw_n, known_n, straddlers))

    usable = [r for r in fold_results if r[3] >= 20]
    if len(usable) < 2:
        print("\nVERDICT: DATA-GATED/HOLD - fewer than two chronological folds have >=20 eligible "
              "transitions. Do not add sequence edges; let the trace mature.")
        return 0
    mean = {name: sum(r[1][name] for r in usable) / len(usable)
            for name in ("co-read", "immediate", "successor", "blend")}
    consistent = all(r[1]["successor"] > r[1]["co-read"] for r in usable)
    adds = mean["blend"] - mean["co-read"]
    cov = sum(r[2]["successor"] for r in usable) / len(usable)
    print("\n  mean over usable folds: co-read %.3f | immediate %.3f | successor %.3f | blend %.3f" %
          (mean["co-read"], mean["immediate"], mean["successor"], mean["blend"]))
    print("  successor coverage %.2f; blend delta over current co-read signal %+0.3f" % (cov, adds))
    if consistent and adds >= 0.03 and cov >= 0.20:
        print("VERDICT: SHIP-CANDIDATE - temporal context adds robust signal; next step is an isolated "
              "edge-file replay through spread_recall before touching the live graph.")
    elif cov < 0.20:
        print("VERDICT: DATA-GATED - ordered transitions are too sparse to alter live recall; re-run as "
              "the retrieval trace matures.")
    else:
        print("VERDICT: HOLD/NULL - temporal order does not add robustly over the undirected co-read "
              "signal on real future sessions. Do not add sequence edges from this trace.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
