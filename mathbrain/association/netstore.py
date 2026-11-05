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

    def finalize_idf(self, n_windows):
        """idf = log((1+N)/(1+df)) + 1 over event windows."""
        cur = self.db.execute("SELECT id, df FROM nodes")
        rows = cur.fetchall()
        for nid, df in rows:
            idf = math.log((1.0 + n_windows) / (1.0 + max(0, df))) + 1.0
            self.db.execute("UPDATE nodes SET idf=? WHERE id=?", (idf, nid))
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

    def prune_out_degree(self, top_n=50):
        """Synaptic scaling: keep only each node's top-N outgoing edges per kind."""
        self.db.execute("""
            DELETE FROM edges WHERE rowid IN (
              SELECT rowid FROM (
                SELECT rowid, ROW_NUMBER() OVER (
                  PARTITION BY src, kind ORDER BY w DESC) AS rk
                FROM edges) WHERE rk > ?)""", (top_n,))
        self.db.commit()

    def apply_ppmi(self, total_windows):
        """Re-weight co edges to positive PMI from raw counts (n) and node df."""
        df = dict(self.db.execute("SELECT id, df FROM nodes"))
        cur = self.db.execute("SELECT src, dst, n FROM edges WHERE kind='co'")
        updates = []
        t = float(max(1, total_windows))
        for src, dst, n in cur.fetchall():
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
