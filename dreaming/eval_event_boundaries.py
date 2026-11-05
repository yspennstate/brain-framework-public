#!/usr/bin/env python3
"""Chronological predictive gate for prompt-event-bounded recall edges.

The deployed coactivation mechanism forms an undirected, focused all-pairs clique from a whole
session. This evaluator asks a narrower predictive question: does respecting prompt boundaries help
predict a later session's next memory read? It is evaluation-only. It writes no edge file and changes
no ranker, salience score, hook, or live graph.

The design keeps the hypotheses separate instead of bundling them into one flattering challenger:

* session co-read proxy: focused, undirected whole-session pairs (an offline proxy, not the full live
  graph with decay, pruning, wiki links, or semantic re-ranking);
* session directed: unweighted consecutive reads across the whole session;
* prompt undirected: focused all-pairs inside each prompt event;
* prompt directed: unweighted consecutive reads inside each prompt event;
* prompt timed: prompt-directed reads with a fixed temporal half-life;
* reverse timed: separately trained reversed prompt transitions, as a direction control.

Splits use absolute time and discard sessions spanning the cutoff. Retrieval order comes from the
millisecond-resolved ISO field, never the rounded integer epoch. Metrics use expected reciprocal rank
and expected Hit@10 under score ties, catalog negatives selected without consulting test targets, a
session-clustered bootstrap, leave-one-session-out stability, and 5/10/30-minute half-life sensitivity.
Even a structural pass remains LIVE HOLD until a full-stack replay proves <=1 percentage-point recall
precision loss.

Usage: python eval_event_boundaries.py
"""

from __future__ import print_function

import csv
import io
import math
import os
import random
import tempfile
from collections import Counter, defaultdict
from itertools import combinations

import eligibility_credit as ec
import sys


PRIMARY_HALF_LIFE_SECONDS = 10 * 60.0
HALF_LIFE_GRID = (5 * 60.0, 10 * 60.0, 30 * 60.0)
MAX_DT_SECONDS = 30 * 60.0
FOCUS_K = 6
FOLDS = (0.60, 0.70, 0.80)
MIN_TEST_ROWS = 20
MIN_SCORED_SESSIONS = 10
BOOTSTRAPS = 2000
SEED = 20260709
BASELINE = "session_coread"
PRIMARY = "prompt_timed"


def load_precise_retrievals(path):
    """Load retrievals using ISO sub-second time; integer epoch is fallback-only.

    ``build_retrieval_trace.py`` historically rounded epoch to a whole second. Directed reads inside
    the same second were therefore alphabetized by slug downstream, fabricating roughly half their
    directions. The ISO column already preserves the real order, so use it as the authority.
    """
    rows = []
    stats = Counter()
    try:
        fh = io.open(path, encoding="utf-8", errors="replace")
    except Exception:
        stats["missing_file"] += 1
        return rows, stats
    with fh:
        for order, row in enumerate(csv.DictReader(fh, delimiter="\t")):
            stats["rows"] += 1
            ts = ec._parse_prompt_ts(row.get("iso"))
            if ts is not None:
                stats["iso_time"] += 1
            else:
                try:
                    ts = float(row.get("epoch"))
                    stats["epoch_fallback"] += 1
                except Exception:
                    stats["bad_ts"] += 1
                    continue
            session = str(row.get("session") or "")
            slug = str(row.get("slug") or "").strip()
            if not ec.UUID_RE.fullmatch(session) or not slug:
                stats["bad_shape"] += 1
                continue
            rows.append({"ts": float(ts), "session": session, "slug": slug[:180], "order": order})
    rows.sort(key=lambda r: (r["session"], r["ts"], r["order"], r["slug"]))
    stats["usable"] = len(rows)
    return rows, stats


def _ordered(rows):
    return sorted(rows, key=lambda r: (r["ts"], r.get("order", 0), r["slug"]))


def _fold_sequence(rows):
    seq = []
    for row in _ordered(rows):
        item = (row["ts"], row["slug"])
        if not seq or seq[-1][1] != item[1]:
            seq.append(item)
    return seq


def build_events(reads, prompts):
    """Assign each read to the latest same-session prompt and fold consecutive duplicates."""
    prompts_by = ec._group_by_session(prompts)
    grouped = defaultdict(list)
    unmatched = 0
    for read in sorted(reads, key=lambda r: (r["ts"], r["order"], r["session"], r["slug"])):
        prompt = ec._latest_prompt(prompts_by.get(read["session"][:12], []), read["ts"])
        if prompt is None:
            unmatched += 1
            continue
        grouped[(read["session"], prompt["event_id"])].append(read)

    events = []
    for (session, event_id), rows in grouped.items():
        seq = _fold_sequence(rows)
        if seq:
            events.append({
                "session": session,
                "event_id": event_id,
                "start": seq[0][0],
                "end": seq[-1][0],
                "seq": seq,
            })
    events.sort(key=lambda e: (e["start"], e["session"], e["event_id"]))
    return events, unmatched


def build_session_sequences(reads):
    by = defaultdict(list)
    for row in reads:
        by[row["session"]].append(row)
    events = []
    for session, rows in by.items():
        seq = _fold_sequence(rows)
        if seq:
            events.append({
                "session": session,
                "event_id": "session:" + session,
                "start": seq[0][0],
                "end": seq[-1][0],
                "seq": seq,
            })
    return events


def _normalize(table):
    totals = defaultdict(float)
    for (source, _target), value in table.items():
        totals[source] += value
    return {
        pair: value / totals[pair[0]] for pair, value in table.items()
        if totals[pair[0]] > 0
    }


def train_session_coread_proxy(reads):
    """Offline proxy for current coactivation: focused, undirected, all-pairs per session."""
    by = defaultdict(list)
    for row in reads:
        by[row["session"]].append(row)
    table = defaultdict(float)
    for rows in by.values():
        uniq = list(dict.fromkeys(r["slug"] for r in _ordered(rows)))
        if len(uniq) < 2:
            continue
        focus = min(1.0, FOCUS_K / float(len(uniq)))
        for a, b in combinations(uniq, 2):
            table[(a, b)] += focus
            table[(b, a)] += focus
    return _normalize(table)


def train_event_coread(events):
    """Focused undirected all-pairs co-read edges, reset at prompt events."""
    table = defaultdict(float)
    for event in events:
        uniq = list(dict.fromkeys(slug for _ts, slug in event["seq"]))
        if len(uniq) < 2:
            continue
        focus = min(1.0, FOCUS_K / float(len(uniq)))
        for a, b in combinations(uniq, 2):
            table[(a, b)] += focus
            table[(b, a)] += focus
    return _normalize(table)


def train_directed(events, half_life_seconds=None, reverse=False):
    """Train consecutive directed transitions; reverse is learned from reversed pairs, then normalized."""
    table = defaultdict(float)
    for event in events:
        for (ta, a), (tb, b) in zip(event["seq"], event["seq"][1:]):
            dt = tb - ta
            if a == b or dt < 0 or dt > MAX_DT_SECONDS:
                continue
            source, target = (b, a) if reverse else (a, b)
            weight = 1.0
            if half_life_seconds is not None:
                weight = math.exp(-math.log(2.0) * dt / float(half_life_seconds))
            table[(source, target)] += weight
    return _normalize(table)


def heldout_rows(events, known):
    rows = []
    for event in events:
        for (ta, a), (tb, b) in zip(event["seq"], event["seq"][1:]):
            dt = tb - ta
            if a != b and 0 <= dt <= MAX_DT_SECONDS and a in known and b in known:
                rows.append({
                    "session": event["session"], "event_id": event["event_id"],
                    "source": a, "target": b,
                })
    return rows


def split_fold(events, reads, fraction):
    """Absolute-time split; discard every session with reads on both sides of the cutoff."""
    if len(events) < 2:
        return None
    index = max(1, min(len(events) - 1, int(len(events) * fraction)))
    cutoff = events[index]["start"]
    ranges = {}
    for row in reads:
        lo, hi = ranges.get(row["session"], (row["ts"], row["ts"]))
        ranges[row["session"]] = (min(lo, row["ts"]), max(hi, row["ts"]))
    straddlers = {s for s, (lo, hi) in ranges.items() if lo < cutoff <= hi}
    train_reads = [r for r in reads if r["ts"] < cutoff and r["session"] not in straddlers]
    train_events = [e for e in events if e["end"] < cutoff and e["session"] not in straddlers]
    test_events = [e for e in events if e["start"] >= cutoff and e["session"] not in straddlers]
    return cutoff, train_reads, train_events, test_events, straddlers


def expected_rank_metrics(table, source, target, candidates, k=10):
    """Expected reciprocal rank and Hit@k under uniform random ordering inside a score tie."""
    target_score = table.get((source, target), 0.0)
    scores = [table.get((source, c), 0.0) for c in candidates if c != source]
    greater = sum(1 for value in scores if value > target_score)
    equal = sum(1 for value in scores if value == target_score)  # includes target
    if equal <= 0:
        return 0.0, 0.0, target_score
    reciprocal_rank = sum(1.0 / (greater + place) for place in range(1, equal + 1)) / equal
    if greater >= k:
        hit = 0.0
    elif greater + equal <= k:
        hit = 1.0
    else:
        hit = float(k - greater) / equal
    return reciprocal_rank, hit, target_score


def score_rows(rows, models, known):
    """Score against the train-known catalog; negatives never consult held-out target labels."""
    candidates = sorted(known)
    scored = []
    for row in rows:
        a, b = row["source"], row["target"]
        negatives = [c for c in candidates if c != a and c != b]
        item = {"session": row["session"], "event_id": row["event_id"]}
        for name, table in models.items():
            rr, hit, positive = expected_rank_metrics(table, a, b, candidates)
            wins = 0.0
            for negative in negatives:
                value = table.get((a, negative), 0.0)
                wins += 1.0 if positive > value else 0.5 if positive == value else 0.0
            item[name + "_mrr"] = rr
            item[name + "_hit10"] = hit
            item[name + "_auc"] = wins / len(negatives) if negatives else 0.5
            item[name + "_covered"] = 1.0 if positive > 0 else 0.0
        scored.append(item)
    return scored


def _mean(rows, key):
    return sum(row[key] for row in rows) / len(rows) if rows else 0.0


def clustered_ci(rows, challenger=PRIMARY, baseline=BASELINE, bootstraps=BOOTSTRAPS):
    by = defaultdict(list)
    for row in rows:
        by[row["session"]].append(row[challenger + "_mrr"] - row[baseline + "_mrr"])
    sessions = sorted(by)
    if len(sessions) < 2:
        return (float("nan"), float("nan"))
    cluster_mean = {session: sum(values) / len(values) for session, values in by.items()}
    rng = random.Random(SEED)
    draws = []
    for _ in range(bootstraps):
        sampled = [sessions[rng.randrange(len(sessions))] for _i in sessions]
        draws.append(sum(cluster_mean[session] for session in sampled) / len(sampled))
    draws.sort()
    lo = draws[int(0.025 * (len(draws) - 1))]
    hi = draws[int(0.975 * (len(draws) - 1))]
    return lo, hi


def loso_stability(rows, challenger=PRIMARY, baseline=BASELINE):
    by = defaultdict(list)
    for row in rows:
        by[row["session"]].append(row[challenger + "_mrr"] - row[baseline + "_mrr"])
    means = {session: sum(values) / len(values) for session, values in by.items()}
    sessions = sorted(means)
    if len(sessions) < 3:
        return False, float("nan")
    leave_one_out = []
    for heldout in sessions:
        kept = [means[session] for session in sessions if session != heldout]
        leave_one_out.append(sum(kept) / len(kept))
    return all(value > 0 for value in leave_one_out), min(leave_one_out)


def _summaries(scored, model_names):
    return {
        name: {
            "mrr": _mean(scored, name + "_mrr"),
            "hit10": _mean(scored, name + "_hit10"),
            "auc": _mean(scored, name + "_auc"),
            "coverage": _mean(scored, name + "_covered"),
        }
        for name in model_names
    }


def evaluate_fold(events, reads, fraction):
    split = split_fold(events, reads, fraction)
    if split is None:
        return None
    cutoff, train_reads, train_events, test_events, straddlers = split
    known = set(row["slug"] for row in train_reads)
    session_events = build_session_sequences(train_reads)
    models = {
        BASELINE: train_session_coread_proxy(train_reads),
        "session_directed": train_directed(session_events),
        "prompt_undirected": train_event_coread(train_events),
        "prompt_directed": train_directed(train_events),
        PRIMARY: train_directed(train_events, half_life_seconds=PRIMARY_HALF_LIFE_SECONDS),
        "reverse_timed": train_directed(
            train_events, half_life_seconds=PRIMARY_HALF_LIFE_SECONDS, reverse=True
        ),
    }
    for half_life in HALF_LIFE_GRID:
        models["tau_%dm" % int(half_life / 60)] = train_directed(
            train_events, half_life_seconds=half_life
        )
    test = heldout_rows(test_events, known)
    scored = score_rows(test, models, known)
    summaries = _summaries(scored, models)
    lo, hi = clustered_ci(scored)
    loso_ok, loso_min = loso_stability(scored)
    sessions = len(set(row["session"] for row in scored))
    base, primary = summaries[BASELINE], summaries[PRIMARY]
    return {
        "fraction": fraction, "cutoff": cutoff, "n": len(scored),
        "scored_sessions": sessions,
        "train_events": len(train_events), "test_events": len(test_events),
        "train_sessions": len(set(r["session"] for r in train_reads)),
        "test_sessions": len(set(e["session"] for e in test_events)),
        "straddlers": len(straddlers),
        "models": summaries,
        "delta_mrr": primary["mrr"] - base["mrr"],
        "delta_auc": primary["auc"] - base["auc"],
        "hit_relative": ((primary["hit10"] / base["hit10"]) - 1.0)
        if base["hit10"] > 0 else float("inf") if primary["hit10"] > 0 else 0.0,
        "ci": (lo, hi), "loso_ok": loso_ok, "loso_min": loso_min,
    }


def boundary_pair_audit(events):
    by_session = defaultdict(list)
    for event in events:
        by_session[event["session"]].append(event)
    total = cross = 0
    for session_events in by_session.values():
        all_slugs = set(slug for event in session_events for _ts, slug in event["seq"])
        all_pairs = set(combinations(sorted(all_slugs), 2))
        within = set()
        for event in session_events:
            slugs = sorted(set(slug for _ts, slug in event["seq"]))
            within.update(combinations(slugs, 2))
        total += len(all_pairs)
        cross += len(all_pairs - within)
    return cross, total


def _fixture_prompt(ts, session, event_id, index):
    return {
        "ts": float(ts), "ts_text": "fixture", "session": session[:12],
        "event_id": event_id, "index": index, "class": "test", "origin": "test", "sha1": "x",
    }


def self_test():
    session_a = "11111111-2222-3333-4444-555555555555"
    session_b = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    # Two reads share the legacy rounded epoch but ISO proves z precedes a. Alphabetical order would
    # reverse them, which was the live directed-edge bug this evaluator must never reintroduce.
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "retrievals.tsv")
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("slug\tepoch\tiso\tsession\n")
            fh.write("z_first\t100\t1970-01-01T00:01:40.100Z\t%s\n" % session_a)
            fh.write("a_second\t100\t1970-01-01T00:01:40.900Z\t%s\n" % session_a)
        precise, stats = load_precise_retrievals(path)
        assert [row["slug"] for row in precise] == ["z_first", "a_second"]
        assert precise[0]["ts"] < precise[1]["ts"] and stats["iso_time"] == 2

    prompts = [
        _fixture_prompt(100.0, session_a, "a1", 0),
        _fixture_prompt(200.0, session_a, "a2", 1),
        _fixture_prompt(300.0, session_b, "b1", 0),
    ]
    reads = [
        {"ts": 110.0, "session": session_a, "slug": "x", "order": 0},
        {"ts": 120.0, "session": session_a, "slug": "y", "order": 1},
        {"ts": 210.0, "session": session_a, "slug": "z", "order": 2},
        {"ts": 310.0, "session": session_b, "slug": "x", "order": 3},
        {"ts": 320.0, "session": session_b, "slug": "z", "order": 4},
    ]
    events, unmatched = build_events(reads, prompts)
    assert unmatched == 0 and len(events) == 3
    assert [slug for _ts, slug in events[0]["seq"]] == ["x", "y"]
    prompt_directed = train_directed(events)
    session_directed = train_directed(build_session_sequences(reads))
    reverse = train_directed(events, half_life_seconds=600.0, reverse=True)
    assert prompt_directed.get(("x", "y"), 0) > 0 and prompt_directed.get(("y", "z"), 0) == 0
    assert session_directed.get(("y", "z"), 0) > 0
    assert reverse.get(("y", "x"), 0) > 0 and reverse.get(("x", "y"), 0) == 0
    split = split_fold(events, reads, 0.5)
    assert split is not None and session_a in split[-1]

    rr, hit, score = expected_rank_metrics({}, "x", "z", {"x", "y", "z"}, k=1)
    assert abs(rr - 0.75) < 1e-12 and abs(hit - 0.5) < 1e-12 and score == 0.0

    # The other real held-out target remains a catalog negative for this row; scoring never peeks at
    # the full test target set to remove inconvenient negatives.
    fixture_models = {"m": {("x", "y"): 1.0, ("x", "z"): 2.0}}
    fixture_rows = [
        {"session": session_a, "event_id": "a1", "source": "x", "target": "y"},
        {"session": session_a, "event_id": "a1", "source": "x", "target": "z"},
    ]
    scored = score_rows(fixture_rows, fixture_models, {"x", "y", "z"})
    assert scored[0]["m_auc"] == 0.0


def _usable(result):
    return (
        result["n"] >= MIN_TEST_ROWS
        and result["scored_sessions"] >= MIN_SCORED_SESSIONS
    )


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    self_test()
    reads, read_stats = load_precise_retrievals(ec.DEFAULT_RETRIEVALS)
    prompts, _ = ec.load_prompts(ec.DEFAULT_PROMPTS)
    events, unmatched = build_events(reads, prompts)
    cross, total = boundary_pair_audit(events)
    usable_events = sum(1 for event in events if len(event["seq"]) >= 2)
    print(
        "eval_event_boundaries: %d precise-ISO reads mapped to %d prompt events "
        "(%d with >=2 reads; %d unmatched; %d epoch fallbacks)"
        % (
            len(reads) - unmatched, len(events), usable_events, unmatched,
            read_stats.get("epoch_fallback", 0),
        )
    )
    print(
        "  mapped-read clique audit: %d/%d unique within-session pairs (%.1f%%) exist only "
        "across prompt boundaries"
        % (cross, total, 100.0 * cross / total if total else 0.0)
    )
    print(
        "  baseline = offline session co-read proxy only (no live decay/pruning/wiki/semantic layers)"
    )
    print(
        "  split  train/test events sessions strad rows/sess   MRR proxy->timed "
        "[delta, 95% CI]  Hit10 proxy->timed  AUC proxy->timed"
    )
    results = []
    for fraction in FOLDS:
        result = evaluate_fold(events, reads, fraction)
        if result is None:
            continue
        results.append(result)
        models = result["models"]
        base, primary = models[BASELINE], models[PRIMARY]
        lo, hi = result["ci"]
        print(
            "  %3d%%  %4d/%-4d %3d/%-3d %5d %3d/%-3d   %.3f->%.3f "
            "[%+.3f, %+.3f,%+.3f]   %.3f->%.3f   %.3f->%.3f"
            % (
                int(100 * fraction), result["train_events"], result["test_events"],
                result["train_sessions"], result["test_sessions"], result["straddlers"],
                result["n"], result["scored_sessions"],
                base["mrr"], primary["mrr"], result["delta_mrr"], lo, hi,
                base["hit10"], primary["hit10"], base["auc"], primary["auc"],
            )
        )
        print(
            "       ablate MRR: sess-dir %.3f | prompt-undir %.3f | prompt-dir %.3f | "
            "prompt-timed %.3f | reverse %.3f"
            % (
                models["session_directed"]["mrr"], models["prompt_undirected"]["mrr"],
                models["prompt_directed"]["mrr"], primary["mrr"],
                models["reverse_timed"]["mrr"],
            )
        )
        print(
            "       tau MRR 5/10/30m: %.3f / %.3f / %.3f; coverage %.2f->%.2f; "
            "LOSO %s (min %+.3f)"
            % (
                models["tau_5m"]["mrr"], models["tau_10m"]["mrr"],
                models["tau_30m"]["mrr"], base["coverage"], primary["coverage"],
                "stable" if result["loso_ok"] else "not-stable", result["loso_min"],
            )
        )

    usable = [result for result in results if _usable(result)]
    main70 = next((r for r in results if abs(r["fraction"] - 0.70) < 1e-9), None)
    main_usable = bool(main70 and _usable(main70))
    tau_stable = bool(main70 and all(
        main70["models"]["tau_%dm" % int(half_life / 60)]["mrr"]
        > main70["models"][BASELINE]["mrr"]
        and main70["models"]["tau_%dm" % int(half_life / 60)]["auc"]
        >= main70["models"][BASELINE]["auc"]
        for half_life in HALF_LIFE_GRID
    ))
    boundary_ablation = bool(
        main70
        and main70["models"]["prompt_undirected"]["mrr"]
        > main70["models"][BASELINE]["mrr"]
        and main70["models"]["prompt_directed"]["mrr"]
        > main70["models"]["session_directed"]["mrr"]
    )
    reverse_control = bool(
        main70
        and main70["models"][PRIMARY]["mrr"] > main70["models"]["reverse_timed"]["mrr"]
        and main70["models"][PRIMARY]["auc"] > main70["models"]["reverse_timed"]["auc"]
    )
    structural_pass = bool(
        len(usable) >= 2 and main_usable
        and all(result["delta_mrr"] > 0 for result in usable)
        and main70["ci"][0] > 0
        and main70["hit_relative"] >= 0.10
        and main70["delta_auc"] >= 0.05
        and main70["loso_ok"] and tau_stable and boundary_ablation and reverse_control
    )
    if structural_pass:
        print(
            "VERDICT: STRUCTURAL PASS / LIVE HOLD - prompt-event edges clear this chronological "
            "offline gate, but no consumer may ship until full-stack replay proves precision loss <=1pp."
        )
    elif not main_usable or len(usable) < 2:
        print(
            "VERDICT: DATA-GATED/HOLD - too few scored rows and independent sessions in at least "
            "two chronological folds; do not add event edges."
        )
    else:
        print(
            "VERDICT: HOLD/NULL - prompt-event edge models do not beat the session co-read proxy "
            "robustly across ablations, controls, and sensitivity checks; do not add live edges."
        )
    if results and all(result["delta_mrr"] <= 0 for result in results):
        print("  current direction is adverse in every fold; hold unless future independent data overturn it")
    print("  self-test: PASS; evaluator writes nothing and has no consumer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
