"""Build the big network from a chronological event stream.

Input: one or more JSONL files of events, each line
  {"ts": <epoch>, "session": "<sid>", "kind": "tool|prompt|file|mem|reflex|text",
   "id": "<node id without namespace>", "text": "<optional free text>"}

`text` events (and prompt events' text) are tokenized into keyword nodes here, with a
document-frequency band filter so stopwords and one-offs never become nodes.

Windows: events in the same session within WINDOW_S seconds share a window (event
segmentation); co-activation edges connect nodes in one window; sequence edges connect
window t to windows t+1..t+LAG.

Usage:
  python build_graph.py --db net.sqlite3 --events events.jsonl [more.jsonl ...]
"""
import argparse, io, json, re, sys
from collections import Counter, defaultdict

from netstore import NetStore

WINDOW_S = 120.0
LAG = 3
TOKEN = re.compile(r"[a-z][a-z0-9_]{2,}")
STOP = set("""the a an and or of to in for on with is are was were be this that it its as
at by from into over per not no new one two use used using via etc when then than but our
we you your all any each more most some such only same now file files line lines run runs
running make made just also can could should would will dont doesnt didnt have has had do
does did done get got if else while true false none null let der die und""".split())
import os

DF_MIN = int(os.environ.get("NET_DF_MIN", "3"))          # keyword min document frequency
DF_MAX_FRAC = float(os.environ.get("NET_DF_MAX_FRAC", "0.30"))  # ...and max fraction


def tokenize(text):
    return [t for t in TOKEN.findall(text.lower()) if t not in STOP][:64]


def load_events(paths):
    evs = []
    for p in paths:
        with io.open(p, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    evs.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
    evs.sort(key=lambda e: (e.get("session", ""), float(e.get("ts", 0))))
    return evs


def windows_of(evs):
    """Yield lists of events sharing (session, <=WINDOW_S gap) windows, in time order."""
    cur, cur_sid, cur_t = [], None, None
    out = []
    for e in evs:
        sid, t = e.get("session", ""), float(e.get("ts", 0))
        if cur and (sid != cur_sid or t - cur_t > WINDOW_S):
            out.append(cur)
            cur = []
        cur.append(e)
        cur_sid, cur_t = sid, t
    if cur:
        out.append(cur)
    out.sort(key=lambda w: float(w[0].get("ts", 0)))
    return out


def nodes_in_window(win, kw_whitelist=None):
    ids = set()
    for e in win:
        kind = e.get("kind", "")
        if kind in ("tool", "file", "mem", "reflex", "prompt") and e.get("id"):
            ids.add(f"{kind}:{e['id']}")
        text = e.get("text", "")
        if text:
            for t in tokenize(text):
                if kw_whitelist is None or t in kw_whitelist:
                    ids.add(f"kw:{t}")
    return ids


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--events", nargs="+", required=True)
    ap.add_argument("--prune", type=int, default=50)
    a = ap.parse_args(argv)

    evs = load_events(a.events)
    wins = windows_of(evs)
    n_win = len(wins)

    # pass 1: keyword document frequency -> the df band decides the node universe
    kw_df = Counter()
    for w in wins:
        seen = set()
        for e in w:
            for t in tokenize(e.get("text", "")):
                seen.add(t)
        kw_df.update(seen)
    kw_whitelist = {t for t, df in kw_df.items()
                    if df >= DF_MIN and df <= DF_MAX_FRAC * max(1, n_win)}

    st = NetStore(a.db)
    win_nodes = []
    node_df = Counter()
    for w in wins:
        ids = nodes_in_window(w, kw_whitelist)
        # cap pathological windows so the pair loop stays bounded
        if len(ids) > 80:
            ids = set(sorted(ids)[:80])
        win_nodes.append(ids)
        node_df.update(ids)
    for nid, df in node_df.items():
        st.upsert_node(nid, nid.split(":", 1)[0], df_inc=df)
    # retrievable content: a prompt/mem node's own text, so a completed cascade can
    # return the INFORMATION (his directive: surface the exact prompt or memory)
    for w in wins:
        for e in w:
            k, i = e.get("kind"), e.get("id")
            if k in ("prompt", "mem") and i and e.get("text"):
                st.set_content(f"{k}:{i}", e["text"])

    # co-activation + sequence counts, flushed in chunks so memory stays bounded
    # at corpus scale (an unflushed pair Counter over 10^4-10^5 windows is gigabytes)
    now = __import__("time").time()
    FLUSH_EVERY = 2000

    def flush_co(co):
        st.db.executemany(
            "INSERT INTO edges(src, dst, kind, w, n, updated) VALUES(?,?,?,0,?,?) "
            "ON CONFLICT(src, dst, kind) DO UPDATE SET n = n + excluded.n",
            [(x, y, "co", c, now) for (x, y), c in co.items()]
            + [(y, x, "co", c, now) for (x, y), c in co.items()])
        st.db.commit()

    def flush_seq(seq):
        st.db.executemany(
            "INSERT INTO edges(src, dst, kind, w, n, updated) VALUES(?,?,?,?,1,?) "
            "ON CONFLICT(src, dst, kind) DO UPDATE SET w = w + excluded.w",
            [(x, y, "seq", v, now) for (x, y), v in seq.items()])
        st.db.commit()

    co, seq = Counter(), Counter()
    for i, ids in enumerate(win_nodes):
        s = sorted(ids)
        for x in range(len(s)):
            for j in range(x + 1, len(s)):
                co[(s[x], s[j])] += 1
        for lag in range(1, LAG + 1):
            if i + lag >= len(win_nodes):
                break
            disc = 1.0 / lag
            for aid in ids:
                for bid in win_nodes[i + lag]:
                    if aid != bid:
                        seq[(aid, bid)] += disc
        if (i + 1) % FLUSH_EVERY == 0:
            flush_co(co)
            flush_seq(seq)
            co, seq = Counter(), Counter()
    flush_co(co)
    flush_seq(seq)

    # weights: PPMI for co; discounted counts already in w for seq
    st.apply_ppmi(n_win)
    st.prune_out_degree(a.prune)
    st.finalize_idf(n_win)
    # water mark so grow.py only ever folds genuinely newer events
    max_ts = max((float(e.get("ts", 0)) for w in wins for e in w), default=0.0)
    st.db.execute("INSERT OR REPLACE INTO meta VALUES('grow_hwm', ?)", (str(max_ts),))
    st.commit()
    n_nodes = st.db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    n_edges = st.db.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    print(f"windows={n_win} nodes={n_nodes} edges={n_edges} kw={len(kw_whitelist)}")
    st.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
