"""
filmbrain store - the movie-making part of the brain.

A node/edge graph with measurements attached, so the film pipeline can ask
"what do we know about X" before it renders, and write back what it found out.

Design notes that matter:
  - Every node carries a SOURCE. Harvested-from-a-tool, authored-by-us and
    measured-on-this-machine are different kinds of truth and must stay separable.
  - Every claim that came from a measurement points at the run that produced it,
    so a number can always be traced back to the render that made it.
  - Nothing is deleted. A superseded node keeps its edges and gains a
    superseded_by edge, the same convention the main brain uses.
"""
import json, os, re, sqlite3, time, uuid, hashlib
from datetime import datetime, timezone

# The film brain lives beside this file. FILMBRAIN_ROOT moves it (a deployment that keeps its
# store on a larger disk, or a second brain for a second project); the store database itself is
# never vendored - it is harvested from the toolchain on the machine that will use it, so a copy
# from somewhere else would describe binaries that are not installed here. `ingest/` rebuilds it.
ROOT = os.environ.get("FILMBRAIN_ROOT", os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("FILMBRAIN_DB", os.path.join(ROOT, "store", "filmbrain.db"))

NODE_KINDS = {
    "craft",      # a filmmaking concept: shot, move, cut, light, sound idea
    "principle",  # a rule we hold, with a reason
    "failure",    # a named way this goes wrong (a scar)
    "tool",       # Blender, ffmpeg, kdenlive, kokoro ...
    "param",      # one parameter / filter / option of a tool
    "preset",     # a bundle of parameters known to work
    "engine",     # a generative engine (LTX, kokoro, chatterbox ...)
    "voice",      # one TTS voice
    "metric",     # something measurable
    "asset",      # font, LUT, sound, model file, reference clip
    "work",       # a film / series / lecture we point at
    "person",     # a director, DP, editor, animator
    "project",    # one of our own films
    "run",        # one experiment we ran
    "format",     # container, codec, pixel format, aspect ratio
    "reference",  # external documentation
    "term",       # vocabulary
}

RELS = {
    "is_a", "part_of", "requires", "produces", "alternative_to", "conflicts_with",
    "measured_by", "implemented_by", "parameter_of", "option_of", "used_in",
    "causes", "prevents", "evidence_for", "contradicts", "see_also",
    "preferred_over", "derived_from", "example_of", "applies_to", "learned_from",
    "superseded_by", "tuned_by", "input_of", "output_of", "depends_on",
    "same_as", "opposite_of", "step_of", "fixes", "breaks",
}

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS node(
  id       TEXT PRIMARY KEY,
  kind     TEXT NOT NULL,
  name     TEXT NOT NULL,
  title    TEXT,
  body     TEXT,
  source   TEXT,
  status   TEXT DEFAULT 'active',
  tags     TEXT,
  meta     TEXT,
  created  TEXT,
  updated  TEXT
);
CREATE INDEX IF NOT EXISTS node_kind ON node(kind);
CREATE INDEX IF NOT EXISTS node_name ON node(name);
CREATE INDEX IF NOT EXISTS node_src  ON node(source);

CREATE TABLE IF NOT EXISTS edge(
  src     TEXT NOT NULL,
  rel     TEXT NOT NULL,
  dst     TEXT NOT NULL,
  weight  REAL DEFAULT 1.0,
  note    TEXT,
  source  TEXT,
  created TEXT,
  PRIMARY KEY (src, rel, dst)
);
CREATE INDEX IF NOT EXISTS edge_src ON edge(src);
CREATE INDEX IF NOT EXISTS edge_dst ON edge(dst);
CREATE INDEX IF NOT EXISTS edge_rel ON edge(rel);

CREATE TABLE IF NOT EXISTS run(
  id       TEXT PRIMARY KEY,
  kind     TEXT,
  tool     TEXT,
  started  TEXT,
  ended    TEXT,
  seconds  REAL,
  params   TEXT,
  outcome  TEXT,
  metrics  TEXT,
  artifact TEXT,
  notes    TEXT,
  session  TEXT
);
CREATE INDEX IF NOT EXISTS run_kind ON run(kind);
CREATE INDEX IF NOT EXISTS run_tool ON run(tool);

CREATE TABLE IF NOT EXISTS obs(
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  node_id  TEXT,
  metric   TEXT,
  num      REAL,
  text     TEXT,
  unit     TEXT,
  run_id   TEXT,
  created  TEXT,
  source   TEXT
);
CREATE INDEX IF NOT EXISTS obs_node ON obs(node_id);
CREATE INDEX IF NOT EXISTS obs_metric ON obs(metric);

CREATE TABLE IF NOT EXISTS lesson(
  id         TEXT PRIMARY KEY,
  node_id    TEXT,
  text       TEXT NOT NULL,
  evidence   TEXT,
  confidence TEXT DEFAULT 'provisional',
  created    TEXT,
  session    TEXT,
  promoted   INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS lesson_node ON lesson(node_id);

CREATE VIRTUAL TABLE IF NOT EXISTS node_fts USING fts5(
  id UNINDEXED, name, title, body, tags, tokenize='porter unicode61'
);
CREATE VIRTUAL TABLE IF NOT EXISTS lesson_fts USING fts5(
  id UNINDEXED, text, evidence, tokenize='porter unicode61'
);
"""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(s):
    out = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return out[:120] or hashlib.sha1(s.encode()).hexdigest()[:12]


class Store:
    def __init__(self, path=DB):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path, timeout=120)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---------- writes ----------
    def add_node(self, node_id, kind, name, title=None, body=None, source="authored",
                 tags=None, meta=None, status="active", commit=True):
        if kind not in NODE_KINDS:
            raise ValueError("unknown node kind " + str(kind))
        t = now()
        tags = " ".join(tags) if isinstance(tags, (list, tuple, set)) else (tags or "")
        meta_j = json.dumps(meta, ensure_ascii=False) if meta else None
        cur = self.db.execute("SELECT id FROM node WHERE id=?", (node_id,))
        if cur.fetchone():
            self.db.execute(
                "UPDATE node SET kind=?,name=?,title=?,body=?,source=?,tags=?,meta=?,status=?,updated=? WHERE id=?",
                (kind, name, title, body, source, tags, meta_j, status, t, node_id))
            self.db.execute("DELETE FROM node_fts WHERE id=?", (node_id,))
        else:
            self.db.execute(
                "INSERT INTO node(id,kind,name,title,body,source,status,tags,meta,created,updated)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (node_id, kind, name, title, body, source, status, tags, meta_j, t, t))
        self.db.execute("INSERT INTO node_fts(id,name,title,body,tags) VALUES(?,?,?,?,?)",
                        (node_id, name, title or "", body or "", tags))
        if commit:
            self.db.commit()
        return node_id

    def add_edge(self, src, rel, dst, weight=1.0, note=None, source="authored", commit=True):
        if rel not in RELS:
            raise ValueError("unknown relation " + str(rel))
        self.db.execute(
            "INSERT OR REPLACE INTO edge(src,rel,dst,weight,note,source,created) VALUES(?,?,?,?,?,?,?)",
            (src, rel, dst, weight, note, source, now()))
        if commit:
            self.db.commit()

    def add_run(self, kind, tool, params=None, outcome=None, metrics=None,
                artifact=None, notes=None, seconds=None, started=None, session=None):
        rid = "run_" + time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.db.execute(
            "INSERT INTO run(id,kind,tool,started,ended,seconds,params,outcome,metrics,artifact,notes,session)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, kind, tool, started or now(), now(), seconds,
             json.dumps(params or {}, ensure_ascii=False), outcome,
             json.dumps(metrics or {}, ensure_ascii=False), artifact, notes, session))
        self.db.commit()
        return rid

    def add_obs(self, node_id, metric, num=None, text=None, unit=None, run_id=None,
                source="measured", commit=True):
        self.db.execute(
            "INSERT INTO obs(node_id,metric,num,text,unit,run_id,created,source) VALUES(?,?,?,?,?,?,?,?)",
            (node_id, metric, num, text, unit, run_id, now(), source))
        if commit:
            self.db.commit()

    def add_lesson(self, text, node_id=None, evidence=None, confidence="provisional", session=None):
        lid = "les_" + hashlib.sha1((text + (node_id or "")).encode()).hexdigest()[:16]
        self.db.execute(
            "INSERT OR REPLACE INTO lesson(id,node_id,text,evidence,confidence,created,session)"
            " VALUES(?,?,?,?,?,?,?)", (lid, node_id, text, evidence, confidence, now(), session))
        self.db.execute("DELETE FROM lesson_fts WHERE id=?", (lid,))
        self.db.execute("INSERT INTO lesson_fts(id,text,evidence) VALUES(?,?,?)",
                        (lid, text, evidence or ""))
        self.db.commit()
        return lid

    def commit(self):
        self.db.commit()

    # ---------- reads ----------
    def get(self, node_id):
        r = self.db.execute("SELECT * FROM node WHERE id=?", (node_id,)).fetchone()
        return dict(r) if r else None

    def neighbors(self, node_id, depth=1):
        seen, frontier, out = {node_id}, [node_id], []
        for _ in range(depth):
            nxt = []
            q = ",".join("?" * len(frontier))
            rows = self.db.execute(
                "SELECT src,rel,dst,note FROM edge WHERE src IN (" + q + ") OR dst IN (" + q + ")",
                frontier + frontier).fetchall()
            for r in rows:
                out.append(dict(r))
                for side in (r["src"], r["dst"]):
                    if side not in seen:
                        seen.add(side)
                        nxt.append(side)
            frontier = nxt
            if not frontier:
                break
        return out

    def search(self, q, limit=20, kind=None):
        fq = _fts_query(q)
        if not fq:
            return []
        sql = ("SELECT n.*, bm25(node_fts) AS score FROM node_fts "
               "JOIN node n ON n.id = node_fts.id WHERE node_fts MATCH ? ")
        # Default search is actionable knowledge. Historical nodes remain
        # accessible through get()/the explicit node command.
        sql += ("AND COALESCE(n.status, 'active')='active' "
                "AND NOT EXISTS (SELECT 1 FROM edge retired "
                "WHERE retired.src=n.id AND retired.rel='superseded_by') ")
        args = [fq]
        if kind:
            sql += "AND n.kind=? "
            args.append(kind)
        sql += "ORDER BY score LIMIT ?"
        args.append(limit)
        try:
            return [dict(r) for r in self.db.execute(sql, args)]
        except sqlite3.OperationalError:
            return []

    def search_lessons(self, q, limit=15):
        fq = _fts_query(q)
        if not fq:
            return []
        try:
            return [dict(r) for r in self.db.execute(
                "SELECT l.*, bm25(lesson_fts) AS score FROM lesson_fts "
                "JOIN lesson l ON l.id = lesson_fts.id WHERE lesson_fts MATCH ? "
                "AND NOT EXISTS (SELECT 1 FROM node parent WHERE parent.id=l.node_id "
                "AND COALESCE(parent.status, 'active')<>'active') "
                "AND NOT EXISTS (SELECT 1 FROM edge retired WHERE retired.src=l.node_id "
                "AND retired.rel='superseded_by') "
                "ORDER BY score LIMIT ?", (fq, limit))]
        except sqlite3.OperationalError:
            return []

    def stats(self):
        d = {}
        d["nodes"] = self.db.execute("SELECT COUNT(*) c FROM node").fetchone()["c"]
        d["edges"] = self.db.execute("SELECT COUNT(*) c FROM edge").fetchone()["c"]
        d["runs"] = self.db.execute("SELECT COUNT(*) c FROM run").fetchone()["c"]
        d["obs"] = self.db.execute("SELECT COUNT(*) c FROM obs").fetchone()["c"]
        d["lessons"] = self.db.execute("SELECT COUNT(*) c FROM lesson").fetchone()["c"]
        d["by_kind"] = {r["kind"]: r["c"] for r in self.db.execute(
            "SELECT kind, COUNT(*) c FROM node GROUP BY kind ORDER BY c DESC")}
        d["by_rel"] = {r["rel"]: r["c"] for r in self.db.execute(
            "SELECT rel, COUNT(*) c FROM edge GROUP BY rel ORDER BY c DESC")}
        d["by_source"] = {r["source"]: r["c"] for r in self.db.execute(
            "SELECT source, COUNT(*) c FROM node GROUP BY source ORDER BY c DESC")}
        return d


_FTS_BAD = re.compile(r"[^\w\s]")


def _fts_query(q):
    """FTS5 chokes on stray punctuation. Turn a human question into a safe OR query."""
    toks = [t for t in _FTS_BAD.sub(" ", q).split() if len(t) > 1]
    if not toks:
        return ""
    return " OR ".join(toks)
