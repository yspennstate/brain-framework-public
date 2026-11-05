#!/usr/bin/env python3
"""Dev-loop turn: cluster retrieval (pool the top-M episodes) vs single-episode completion.

Where this comes from: the 07-15 morning turn measured single-episode prompt->prompt
completion firing ~0% held-out (network_retrieval_reframe_2026_07_15), and
INTEGRATION_NOTES prescribes the next step exactly: "If it also fires ~0%, the sparsity is
fundamental and the next step is cluster retrieval (pool the top-M episodes), not a scorer
swap."

HYPOTHESIS (stated before running): pooling the top-M partial-scoring episodes - union
pattern, pooled direct-anchor gate (same k>=5 above-median-idf directly-observed
discipline; spread never testifies) - lifts fire-rate to >=5% of held-out prompt events
AND the pooled retrieval's usefulness (best token-Jaccard of pooled prompt texts vs the
owner's actual next prompt) beats the recency baseline ON THE SAME fired events (paired).

KILL CRITERIA: fire-rate still <1%, or paired usefulness <= the recency baseline. A third
independent kill in this lane closes the completion question: the network's retrieval
value is recall_memories (topic seeding), and next-prompt completion is not where the
signal lives. That result gets recorded as plainly as a win.

Protocol fidelity: the replay loop below is eval_replay.py's, verbatim in structure - same
windows, same causal split (train-era episodes only, buffer warmed through train), same
Jaccard usefulness, same recency baseline - so arm A (single complete()) doubles as the
calibration check: it must reproduce ~0% fire under this split or the harness diverged.

Measurement only - nothing is promoted live by this script (the program's discipline).
"""
import argparse
import math
import sys
import time

from netstore import NetStore
import build_graph as bg
from fire import FireEngine, K_WINNERS


def jaccard(a, b):
    ta, tb = set(bg.tokenize(a)), set(bg.tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


class ClusterArm(FireEngine):
    """Adds pooled top-M retrieval on the same buffer/episode state. No cooldown (the
    eval wants every opportunity scored; live wiring would re-add habituation)."""

    def complete_cluster(self, m=8, k_min_pool=None, threshold_pool=None):
        if self._episodes is None:
            raise RuntimeError("call _load_episodes(before_ts) first (causal split)")
        k_min_pool = k_min_pool if k_min_pool is not None else self.k_min
        threshold_pool = threshold_pool if threshold_pool is not None else self.threshold
        active = dict(sorted(self.act.items(), key=lambda kv: -kv[1])[:K_WINNERS])
        if not active:
            return []
        a_norm = math.sqrt(sum(v * v for v in active.values())) or 1.0
        scored = []
        for eid, ets, pn, pat, norm, cutoff in self._episodes:
            dot = 0.0
            touched = False
            for nid, w in pat.items():
                a = active.get(nid)
                if a is None:
                    continue
                dot += a * w
                touched = True
            if touched and dot > 0.0:
                scored.append((dot / (a_norm * norm), pn, pat, cutoff))
        if not scored:
            return []
        scored.sort(key=lambda s: -s[0])
        top_m = scored[:m]
        # pooled pattern: weighted max over the union; pooled anchors: distinct
        # directly-observed above-that-episode's-median-idf nodes across the union
        pooled = {}
        anchors = set()
        for _, pn, pat, cutoff in top_m:
            for nid, w in pat.items():
                if w > pooled.get(nid, 0.0):
                    pooled[nid] = w
                if w >= cutoff and self.direct.get(nid, 0.0) > 0.2:
                    anchors.add(nid)
        if len(anchors) < k_min_pool:
            return []
        p_norm = math.sqrt(sum(w * w for w in pooled.values())) or 1.0
        dot = sum(active.get(nid, 0.0) * w for nid, w in pooled.items())
        score = dot / (a_norm * p_norm)
        if score < threshold_pool:
            return []
        return [(pn, round(score, 4), len(anchors)) for _, pn, _, _ in top_m]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="net.sqlite3")
    ap.add_argument("--events", nargs="+", default=["events_full.jsonl"])
    ap.add_argument("--split", default="2026-07-13 00:00",
                    help="local time; test era starts here")
    ap.add_argument("--m", type=int, default=8)
    ap.add_argument("--top", type=int, default=3)
    a = ap.parse_args(argv)

    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(
            ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)  # BelowNormal
    except Exception:
        pass

    split_ts = time.mktime(time.strptime(a.split, "%Y-%m-%d %H:%M"))
    st = NetStore(a.db)
    single = FireEngine(st)
    single._load_episodes(before_ts=split_ts)
    cluster = ClusterArm(st)
    cluster._episodes = single._episodes          # identical train-era episodes
    cluster._nbr_cache = single._nbr_cache        # share the neighbor cache too

    evs = bg.load_events(a.events)
    wins = bg.windows_of(evs)
    text_of = {f"prompt:{e['id']}": e.get("text", "")
               for w in wins for e in w if e.get("kind") == "prompt" and e.get("id")}

    import random as _random
    _rng = _random.Random(20260715)   # fixed seed: the control must be reproducible
    last_prompt = None
    n_test = 0
    sA = {"fired": 0, "sim": 0.0, "base": 0.0}
    sB = {"fired": 0, "sim": 0.0, "base": 0.0, "rand": 0.0}
    base_all = 0.0
    for w in wins:
        ids = bg.nodes_in_window(w)
        prompt_evs = [e for e in w if e.get("kind") == "prompt" and e.get("id")]
        ts = float(w[0].get("ts", 0))
        in_test = ts >= split_ts
        for e in prompt_evs:
            truth = e.get("text", "")
            if in_test:
                n_test += 1
                base_j = jaccard(text_of.get(last_prompt, ""), truth) if last_prompt else 0.0
                base_all += base_j
                hitsA = single.complete(top=a.top)
                if hitsA:
                    sA["fired"] += 1
                    sA["sim"] += max(jaccard(text_of.get(p, ""), truth) for p, _, _ in hitsA)
                    sA["base"] += base_j
                hitsB = cluster.complete_cluster(m=a.m)
                if hitsB:
                    sB["fired"] += 1
                    sB["sim"] += max(jaccard(text_of.get(p, ""), truth) for p, _, _ in hitsB)
                    sB["base"] += base_j
                    # control: same-size RANDOM retrieval on the same event - if random
                    # episodes score like the pool, the pool adds nothing beyond
                    # "any old prompt text" and the win is an artifact
                    rnd = _rng.sample(single._episodes, min(a.m, len(single._episodes)))
                    sB["rand"] += max(jaccard(text_of.get(pn, ""), truth)
                                      for _, _, pn, _, _, _ in rnd)
            last_prompt = f"prompt:{e['id']}"
        obs = [i for i in ids if not i.startswith("prompt:")]
        single.observe(obs)
        cluster.observe(obs)

    print("split: %s (epoch %.0f); train episodes: %d; test prompts: %d"
          % (a.split, split_ts, len(single._episodes), n_test))
    print("recency baseline over all test prompts: %.3f" % (base_all / n_test if n_test else 0.0))
    for name, s in (("A single complete()", sA), ("B cluster pool m=%d" % a.m, sB)):
        if s["fired"]:
            extra = ""
            if "rand" in s:
                extra = "; random-M control %.3f" % (s["rand"] / s["fired"])
            print("%s: fired %.1f%% (%d/%d); usefulness %.3f vs paired baseline %.3f%s"
                  % (name, 100.0 * s["fired"] / n_test, s["fired"], n_test,
                     s["sim"] / s["fired"], s["base"] / s["fired"], extra))
        else:
            print("%s: never fired" % name)
    st.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
