"""Extract episode patterns: for each prompt event, the IDF-weighted sparse set of
nodes active in the PRECEDING windows (never the prompt's own window - the pattern
must be knowable before the prompt exists, or retrieval is a lookahead).

Usage:
  python episodes.py --db net.sqlite3 --events events.jsonl [--lookback 3]
"""
import argparse, sys

from netstore import NetStore
import build_graph as bg


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--events", nargs="+", required=True)
    ap.add_argument("--lookback", type=int, default=3,
                    help="windows of context preceding the prompt")
    ap.add_argument("--min-nodes", type=int, default=5)
    a = ap.parse_args(argv)

    st = NetStore(a.db)
    known = {r[0] for r in st.db.execute("SELECT id FROM nodes")}
    evs = bg.load_events(a.events)
    wins = bg.windows_of(evs)
    win_nodes = [bg.nodes_in_window(w) & known for w in wins]
    win_sids = [w[0].get("session", "") for w in wins]

    n_ep = 0
    for i, w in enumerate(wins):
        for e in w:
            if e.get("kind") != "prompt" or not e.get("id"):
                continue
            ctx = set()
            for j in range(max(0, i - a.lookback), i):
                if win_sids[j] != win_sids[i]:
                    continue  # a session boundary is a hard event boundary
                ctx |= win_nodes[j]
            # the prompt's own window is context ONLY through its non-prompt events:
            # the pattern must be knowable before the prompt's words exist (no lookahead)
            same_events = [ev for ev in w if ev.get("kind") != "prompt"]
            ctx |= bg.nodes_in_window(same_events) & known
            if len(ctx) < a.min_nodes:
                continue
            idf = st.idf_map(ctx)
            pattern = {nid: idf.get(nid, 1.0) for nid in ctx}
            ts = float(e.get("ts", 0))
            st.add_episode(f"ep:{e['id']}", ts, f"prompt:{e['id']}", pattern)
            n_ep += 1
    st.commit()
    print(f"episodes={n_ep}")
    st.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
