"""SQLite store for the big association network.

Tables:
  nodes(id TEXT PRIMARY KEY, kind TEXT, label TEXT, df INTEGER, idf REAL)
  edges(src TEXT, dst TEXT, kind TEXT, w REAL, n INTEGER, updated REAL,
        PRIMARY KEY (src, dst, kind))         -- kind: co | seq
  episodes(id TEXT PRIMARY KEY, ts REAL, prompt_node TEXT, pattern TEXT,
           norm REAL)                          -- pattern: json {node_id: idf_weight}
  meta(k TEXT PRIMARY KEY, v TEXT)

Node ids are namespaced: tool:Edit, kw:backtest, prompt:<id>, file:<norm-path>,
mem:<slug>, reflex:<name>.
"""
import io, json, math, os, sqlite3, time

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT DEFAULT '',
  df INTEGER DEFAULT 0, idf REAL DEFAULT 0.0);
CREATE TABLE IF NOT EXISTS edges(
  src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL,
  w REAL DEFAULT 0.0, n INTEGER DEFAULT 0, updated REAL DEFAULT 0,
  PRIMARY KEY (src, dst, kind));
CREATE INDEX IF NOT EXISTS edges_src ON edges(src, kind);
CREATE TABLE IF NOT EXISTS episodes(
  id TEXT PRIMARY KEY, ts REAL NOT NULL, prompt_node TEXT NOT NULL,
  pattern TEXT NOT NULL, norm REAL NOT NULL);
CREATE INDEX IF NOT EXISTS episodes_ts ON episodes(ts);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS node_content(id TEXT PRIMARY KEY, content TEXT);
"""


class NetStore:
    def __init__(self, path):
        self.path = path
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        self.db = sqlite3.connect(path)
        # 2026-09-11: the nightly grow step ran 3.5 h with 16 s of CPU and 170k write operations - every
        # co-edge UPDATE and every node idf UPDATE went through a rollback journal on a 1.4 GB file.
        # WAL mode turns those page copies into sequential appends; synchronous=NORMAL keeps durability
        # at the checkpoint. Both are best-effort: a database another connection holds open in rollback
        # mode refuses the switch, and the store then runs exactly as before.
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            pass
        self.db.executescript(SCHEMA)

    # ---- nodes ----
    def upsert_node(self, nid, kind, label="", df_inc=0):
        self.db.execute(
            "INSERT INTO nodes(id, kind, label, df) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET df = df + ?",
            (nid, kind, label, df_inc, df_inc))

    def set_content(self, nid, content):
        """Store the retrievable payload of a node (a prompt's text, a memory's
        first paragraph) so a completed cascade can return the INFORMATION, not
        just the node id."""
        if content:
            self.db.execute(
                "INSERT OR REPLACE INTO node_content(id, content) VALUES(?,?)",
                (nid, content[:4000]))

    def content(self, nid):
        row = self.db.execute(
            "SELECT content FROM node_content WHERE id=?", (nid,)).fetchone()
        return row[0] if row else None

    def finalize_idf(self, n_windows, only_ids=None):
        """idf = log((1+N)/(1+df)) + 1 over event windows.

        only_ids: restrict the rewrite to these nodes (the ones whose df moved this run). idf also
        depends on N, so every node drifts a little between full passes; grow.py schedules a full
        pass whenever N has grown enough since the last one (2026-09-11)."""
        if only_ids is None:
            rows = self.db.execute("SELECT id, df FROM nodes").fetchall()
        else:
            ids = list(only_ids)
            rows = []
            for i in range(0, len(ids), 900):
                chunk = ids[i:i + 900]
                rows.extend(self.db.execute(
                    "SELECT id, df FROM nodes WHERE id IN (%s)" % ",".join("?" * len(chunk)), chunk).fetchall())
        updates = [(math.log((1.0 + n_windows) / (1.0 + max(0, df))) + 1.0, nid) for nid, df in rows]
        self.db.executemany("UPDATE nodes SET idf=? WHERE id=?", updates)
        self.db.execute("INSERT OR REPLACE INTO meta VALUES('n_windows', ?)",
                        (str(n_windows),))
        self.db.commit()

    def idf_map(self, ids):
        q = ",".join("?" * len(ids))
        return dict(self.db.execute(
            f"SELECT id, idf FROM nodes WHERE id IN ({q})", list(ids)))

    # ---- edges ----
    def bump_edge(self, src, dst, kind, inc=1.0):
        now = time.time()
        self.db.execute(
            "INSERT INTO edges(src, dst, kind, w, n, updated) VALUES(?,?,?,?,1,?) "
            "ON CONFLICT(src, dst, kind) DO UPDATE SET n = n + 1, updated = ?",
            (src, dst, kind, inc, now, now))

    def neighbors(self, nid, kind=None, limit=50):
        if kind:
            cur = self.db.execute(
                "SELECT dst, w FROM edges WHERE src=? AND kind=? ORDER BY w DESC LIMIT ?",
                (nid, kind, limit))
        else:
            cur = self.db.execute(
                "SELECT dst, w FROM edges WHERE src=? ORDER BY w DESC LIMIT ?",
                (nid, limit))
        return cur.fetchall()

    def prune_out_degree(self, top_n=50, srcs=None):
        """Synaptic scaling: keep only each node's top-N outgoing edges per kind.

        srcs: restrict to these source nodes (the ones that gained edges this run); the whole-table
        window function over millions of rows was the second of the two hour-scale steps (2026-09-11)."""
        if srcs is None:
            self.db.execute("""
                DELETE FROM edges WHERE rowid IN (
                  SELECT rowid FROM (
                    SELECT rowid, ROW_NUMBER() OVER (
                      PARTITION BY src, kind ORDER BY w DESC) AS rk
                    FROM edges) WHERE rk > ?)""", (top_n,))
        else:
            ids = list(srcs)
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                self.db.execute("""
                    DELETE FROM edges WHERE rowid IN (
                      SELECT rowid FROM (
                        SELECT rowid, ROW_NUMBER() OVER (
                          PARTITION BY src, kind ORDER BY w DESC) AS rk
                        FROM edges WHERE src IN (%s)) WHERE rk > ?)""" % ",".join("?" * len(chunk)),
                                (*chunk, top_n))
        self.db.commit()

    def apply_ppmi(self, total_windows, pairs=None):
        """Re-weight co edges to positive PMI from raw counts (n) and node df.

        pairs: restrict to these (src, dst) pairs (both directions are rewritten) - the pairs this run
        touched. PMI of an untouched edge also drifts with N and with df of its endpoints, so grow.py
        runs a full pass when N has grown enough since the last one; between full passes the untouched
        edges keep last night's weight, which the recall paths read as a ranking, never as a truth."""
        df = dict(self.db.execute("SELECT id, df FROM nodes"))
        t = float(max(1, total_windows))
        if pairs is None:
            rows = self.db.execute("SELECT src, dst, n FROM edges WHERE kind='co'").fetchall()
        else:
            want = set()
            for a, b in pairs:
                want.add((a, b)); want.add((b, a))
            rows = []
            for a, b in want:
                r = self.db.execute("SELECT src, dst, n FROM edges WHERE src=? AND dst=? AND kind='co'", (a, b)).fetchone()
                if r:
                    rows.append(r)
        updates = []
        for src, dst, n in rows:
            pa = max(1, df.get(src, 1)) / t
            pb = max(1, df.get(dst, 1)) / t
            pab = n / t
            w = max(0.0, math.log(max(pab, 1e-12) / (pa * pb)))
            updates.append((w, src, dst))
        self.db.executemany(
            "UPDATE edges SET w=? WHERE src=? AND dst=? AND kind='co'", updates)
        self.db.commit()

    # ---- episodes ----
    def add_episode(self, eid, ts, prompt_node, pattern):
        norm = math.sqrt(sum(v * v for v in pattern.values())) or 1.0
        self.db.execute(
            "INSERT OR REPLACE INTO episodes VALUES(?,?,?,?,?)",
            (eid, ts, prompt_node, json.dumps(pattern, sort_keys=True), norm))

    def episodes_before(self, ts=None):
        if ts is None:
            cur = self.db.execute("SELECT id, ts, prompt_node, pattern, norm FROM episodes")
        else:
            cur = self.db.execute(
                "SELECT id, ts, prompt_node, pattern, norm FROM episodes WHERE ts < ?", (ts,))
        for eid, ets, pn, pat, norm in cur.fetchall():
            yield eid, ets, pn, json.loads(pat), norm

    def commit(self):
        self.db.commit()

    def close(self):
        self.db.commit()
        self.db.close()
