"""Causal replay evaluation: when the engine surfaces a historical prompt, is that
prompt USEFUL - textually close to what the owner actually writes next?

Every archived prompt is a unique node, so exact next-node identity is the wrong
score (a new prompt is always a new node). The production question is retrieval
usefulness: at each held-out prompt, compare the engine's retrieved prompt TEXTS
against the truth text by token Jaccard. Baseline: the most recent prompt's text
(what "just suggest the last thing he said" would achieve).

Chronological discipline: only train-era (< --split-ts) episodes are loaded; the
buffer is warmed through the train era and replayed causally through the test era.

  python eval_replay.py --db net.sqlite3 --events all.jsonl --split-ts <epoch>
"""
import argparse, sys

from netstore import NetStore
import build_graph as bg
from fire import FireEngine


def jaccard(a, b):
    ta, tb = set(bg.tokenize(a)), set(bg.tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--events", nargs="+", required=True)
    ap.add_argument("--split-ts", type=float, required=True)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--threshold", type=float, default=None,
                    help="override the engine's fire threshold")
    a = ap.parse_args(argv)

    st = NetStore(a.db)
    kw = {}
    if a.threshold is not None:
        kw["threshold"] = a.threshold
    eng = FireEngine(st, **kw)
    eng._load_episodes(before_ts=a.split_ts)   # causal: only train-era episodes

    evs = bg.load_events(a.events)
    wins = bg.windows_of(evs)
    text_of = {f"prompt:{e['id']}": e.get("text", "")
               for w in wins for e in w if e.get("kind") == "prompt" and e.get("id")}

    last_prompt = None
    n_test = fired = 0
    sim_sum = base_sum_at_fire = base_sum_all = 0.0
    for w in wins:
        ids = bg.nodes_in_window(w)
        prompt_evs = [e for e in w if e.get("kind") == "prompt" and e.get("id")]
        ts = float(w[0].get("ts", 0))
        in_test = ts >= a.split_ts
        for e in prompt_evs:
            truth_text = e.get("text", "")
            if in_test:
                n_test += 1
                if last_prompt is not None:
                    base_sum_all += jaccard(text_of.get(last_prompt, ""), truth_text)
                hits = eng.complete(top=a.top)
                if hits:
                    fired += 1
                    best = max(jaccard(text_of.get(p, ""), truth_text)
                               for p, _, _ in hits)
                    sim_sum += best
                    if last_prompt is not None:
                        base_sum_at_fire += jaccard(text_of.get(last_prompt, ""),
                                                    truth_text)
            last_prompt = f"prompt:{e['id']}"
        eng.observe([i for i in ids if not i.startswith("prompt:")])

    print(f"test prompts: {n_test}")
    if fired:
        print(f"engine fired on {100.0*fired/n_test:.1f}% "
              f"({fired}/{n_test}); mean best-retrieved similarity {sim_sum/fired:.3f} "
              f"vs recency baseline {base_sum_at_fire/fired:.3f} on the same prompts")
    else:
        print("engine never fired (raise --threshold sensitivity or check calibration)")
    if n_test:
        print(f"recency baseline over all test prompts: {base_sum_all/n_test:.3f}")
    st.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
