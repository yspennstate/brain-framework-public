"""Incremental growth: fold NEW events into an existing network without a full
rebuild. This is what keeps the graph climbing as agents work, dream, and create
tools, prompts, keywords, and memories.

The directive's rule is honored: nodes are CANONICAL (one per id), and a repeated
node accumulates access intensity (df, edge counts) rather than spawning a
duplicate. Only events newer than the store's high-water mark are folded, so
calling grow.py repeatedly is idempotent and cheap.

  python grow.py --db net.sqlite3 --events new_or_full.jsonl

The store records the last-ingested timestamp in meta('grow_hwm'); only events
with ts > hwm are applied. Point --events at the full stream each time; already-
seen events are skipped by the water mark.
"""
import argparse, sys, time
from collections import Counter

from netstore import NetStore
import build_graph as bg


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--events", nargs="+", required=True)
    ap.add_argument("--prune", type=int, default=50)
    ap.add_argument("--full", action="store_true", help="force the whole-table PPMI / prune / idf pass")
    a = ap.parse_args(argv)

    st = NetStore(a.db)
    row = st.db.execute("SELECT v FROM meta WHERE k='grow_hwm'").fetchone()
    hwm = float(row[0]) if row else 0.0

    evs = [e for e in bg.load_events(a.events) if float(e.get("ts", 0)) > hwm]
    if not evs:
        print(f"grow: nothing new past hwm={hwm}")
        st.close()
        return 0

    wins = bg.windows_of(evs)
    kw_df = Counter()
    for w in wins:
        seen = set()
        for e in w:
            for t in bg.tokenize(e.get("text", "")):
                seen.add(t)
        kw_df.update(seen)
    # keep any keyword already a node, plus new ones clearing the SAME band the
    # full build uses. The upper cap is against the TOTAL corpus window count, not
    # this batch's - otherwise every keyword in a small increment looks ubiquitous
    # and gets dropped.
    known_kw = {r[0][3:] for r in st.db.execute("SELECT id FROM nodes WHERE kind='kw'")}
    row_nw = st.db.execute("SELECT v FROM meta WHERE k='n_windows'").fetchone()
    total_windows = (int(row_nw[0]) if row_nw else 0) + len(wins)
    cap = bg.DF_MAX_FRAC * max(1, total_windows)
    kw_whitelist = known_kw | {t for t, df in kw_df.items()
                               if bg.DF_MIN <= df <= cap}

    win_nodes = []
    node_df = Counter()
    new_nodes = 0
    for w in wins:
        ids = bg.nodes_in_window(w, kw_whitelist)
        if len(ids) > 80:
            ids = set(sorted(ids)[:80])
        win_nodes.append(ids)
        node_df.update(ids)
    existing = {r[0] for r in st.db.execute("SELECT id FROM nodes")}
    for nid, df in node_df.items():
        if nid not in existing:
            new_nodes += 1
        st.upsert_node(nid, nid.split(":", 1)[0], df_inc=df)

    now = time.time()
    co = Counter()
    for ids in win_nodes:
        s = sorted(ids)
        for i in range(len(s)):
            for j in range(i + 1, len(s)):
                co[(s[i], s[j])] += 1
    st.db.executemany(
        "INSERT INTO edges(src, dst, kind, w, n, updated) VALUES(?,?,?,0,?,?) "
        "ON CONFLICT(src, dst, kind) DO UPDATE SET n = n + excluded.n",
        [(x, y, "co", n, now) for (x, y), n in co.items()]
        + [(y, x, "co", n, now) for (x, y), n in co.items()])

    seq = Counter()
    for i in range(len(win_nodes)):
        for lag in range(1, bg.LAG + 1):
            if i + lag >= len(win_nodes):
                break
            disc = 1.0 / lag
            for aid in win_nodes[i]:
                for bid in win_nodes[i + lag]:
                    if aid != bid:
                        seq[(aid, bid)] += disc
    st.db.executemany(
        "INSERT INTO edges(src, dst, kind, w, n, updated) VALUES(?,?,?,?,1,?) "
        "ON CONFLICT(src, dst, kind) DO UPDATE SET w = w + excluded.w",
        [(x, y, "seq", v, now) for (x, y), v in seq.items()])

    # refresh PPMI + IDF over the whole store, re-prune, advance the water mark,
    # and mint episodes for the new prompts
    total = int(st.db.execute("SELECT COALESCE(v,'0') FROM meta WHERE k='n_windows'")
                .fetchone()[0]) if st.db.execute(
                    "SELECT 1 FROM meta WHERE k='n_windows'").fetchone() else 0
    total += len(wins)
    # 2026-09-11: the three whole-table passes below (every co edge re-weighted, every node's idf rewritten,
    # a window function over every edge) ran for hours on the 1.4 GB live store with almost no CPU - pure
    # journal I/O - and held the dream lock for a day. Nightly runs now touch only what the batch touched;
    # a FULL pass runs when the window count has grown 5 pct since the last full pass (or --full), so the
    # drift of untouched weights stays bounded. First run after this landing is a full pass (no marker).
    row_full = st.db.execute("SELECT v FROM meta WHERE k='ppmi_full_windows'").fetchone()
    last_full = int(float(row_full[0])) if row_full else 0
    full = a.full or last_full <= 0 or total >= 1.05 * last_full
    touched_nodes = set(node_df)
    touched_srcs = {x for pair in co for x in pair} | {x for pair in seq for x in pair}
    if full:
        st.apply_ppmi(total)
        st.prune_out_degree(a.prune)
        st.finalize_idf(total)
        st.db.execute("INSERT OR REPLACE INTO meta VALUES('ppmi_full_windows', ?)", (str(total),))
    else:
        st.apply_ppmi(total, pairs=co.keys())
        st.prune_out_degree(a.prune, srcs=touched_srcs)
        st.finalize_idf(total, only_ids=touched_nodes)

    known = {r[0] for r in st.db.execute("SELECT id FROM nodes")}
    new_ep = 0
    for i, w in enumerate(wins):
        for e in w:
            if e.get("kind") != "prompt" or not e.get("id"):
                continue
            ctx = set()
            for j in range(max(0, i - 3), i):
                ctx |= (bg.nodes_in_window(wins[j]) & known)
            same = [ev for ev in w if ev.get("kind") != "prompt"]
            ctx |= bg.nodes_in_window(same) & known
            if len(ctx) < 5:
                continue
            idf = st.idf_map(ctx)
            st.add_episode(f"ep:{e['id']}", float(e.get("ts", 0)),
                           f"prompt:{e['id']}", {n: idf.get(n, 1.0) for n in ctx})
            new_ep += 1

    hwm_new = max(float(e.get("ts", 0)) for e in evs)
    st.db.execute("INSERT OR REPLACE INTO meta VALUES('grow_hwm', ?)", (str(hwm_new),))
    st.commit()
    n_nodes = st.db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    print(f"grow: {'FULL' if full else 'incremental'} pass ({len(co)} co pairs, {len(touched_nodes)} nodes touched); "
          f"+{len(evs)} events, +{new_nodes} nodes, +{new_ep} episodes; "
          f"total nodes={n_nodes}; hwm {hwm} -> {hwm_new}")
    st.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
