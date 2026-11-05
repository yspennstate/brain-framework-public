"""live_membrane.py - the subconscious graph feeding the conscious context, live (26 Sep).

His voice note of 26 Sep 00:26: "If a certain sequence of files are used and then a tool is called and another tool and
then a keyword is thought of, all of those stuff should be activations in the brain ... if an attractor lights up or a
sequence lights up and previously that led to a mistake or that required a certain tool or required a certain way of
thinking, then that stuff should be given to the agent automatically. It should prompt into the agent." And at 15:5x the
same day: live, with a weight that is not zero, edges growing and learning, so it can be studied.

What happens on a prompt or (every few) tool calls of a session:

  activation   the nodes the session touched in the last ten minutes (the capture's own reading of the transcript tail:
               files, folders, tools, commands, hosts, errors, memories read, notes served, his words, the agent's
               words), each weighted 0.5 ** (age / 150 s) - the context of the walk-forward study
  spreading    one learned hop through the sidecar (live_sidecar.py): hebb(x) = sum of weight x PMI x ln(1 + count),
               times what the learner has learned about that edge from earlier fires (learned.sqlite); the edges
               learned since the store was built come from the fast layer (live_delta.py, rebuilt every five minutes)
               and are put on one clock with the store's: both faded by the store's half-life to the moment asked,
               PMI and count taken over the two together (27 Sep: "the data should be live ... the graph should also
               be temporal"); plus the
               memories whose text names what is active (content edges) and the memories linked to the ones read
               (link edges); node size (ACT-R base level) as the prior, fused by reciprocal rank as in the study
  attractors   a co-activation community with three or more members active is lit; what it led to before (errors at
               several times their base rate, the memories agents then read) is a candidate
  ignition     at most two lines, only above a threshold, only once per item per session in two hours, at most six
               fires an hour: his 2 Sep rule, fire less and be more confident when it fires (global workspace
               ignition). Every line says what lit it and how often lines like it were taken up
  learning     every evaluation, fired or not, is one line of live/fires.jsonl; graph_learn.py judges them against what
               the session did next and moves the edge factors, the thresholds and the recall weight

Kill switches: the file live/STOP silences it at once; deleting live/HOOK_ENABLED removes the hook from the dispatcher.
"""
import collections
import json
import math
import os
import sqlite3
import sys
import time
import uuid
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph")
LIVE = os.path.join(HOME, "live")
SIDECAR = os.path.join(LIVE, "sidecar.sqlite")
LEARNED = os.path.join(LIVE, "learned.sqlite")
POLICY = os.path.join(LIVE, "policy.json")
FIRES = os.path.join(LIVE, "fires.jsonl")
SESS = os.path.join(LIVE, "sessions")
STOP = os.path.join(LIVE, "STOP")
PRECISION = os.path.join(LIVE, "precision.json")
KINDS_CACHE = os.path.join(LIVE, "session_kinds.json")    # session_kinds.py's settled kinds (his/test/script)

DEFAULT_POLICY = {
    "version": 1,
    "window_s": 600.0, "half_life_s": 150.0, "k_items": 40, "tail_bytes": 1500000,
    "post_every_calls": 4, "post_every_s": 120.0, "prompt_min_gap_s": 10.0,
    "max_lines": 2, "max_fires_per_hour": 6, "refractory_s": 7200.0,
    "rrf_k": 60.0, "w_hebb": 1.0, "w_size": 0.5, "w_reach": 0.7,
    "mem_size_min": 1.89, "mem_hebb_min": 55.0, "mem_reach_min": 1e9, "err_hebb_min": 47.5, "place_hebb_min": 40.0,
    "attr_min_members": 3, "attr_min_lift": 20.0, "attr_min_count": 15, "attr_max_size": 80, "attr_min_frac": 0.08,
    "err_skip": ["-v:", ":other:", ":exit1", ":error"],
    "serve_mem": True, "serve_err": False, "serve_attr": False, "serve_place": True,
    "serve_word": False, "word_min": 61.4,
    "use_delta": True,
    "hold_every": 4,
    "serve_successor": True,
    "serve_orders": False, "orders_min": 30.0, "orders_stable": 2,
    "learned_prior_n": 4.0, "learned_min": 0.3, "learned_max": 3.0,
    "tired": [],
}
SKIP_SLUGS = {"memory", "mistakes", "index_archive", "predictions", "state_of_mind", "open_questions", "autobiography",
              "model_of_partner", "curiosity", "conscience", "intentions"}
# First live hour (26 Sep 16:07-16:50, 19 sessions): one very big memory went to seven unrelated sessions, lit only by
# generic items (a command word, the agent's own word 'script'), and place lines named folders every agent is already
# in. A line now needs a specific item behind it, a memory reaches at most two sessions in six hours until the learner
# has judged it, and obvious places are not named.
GENERIC = ("tool:", "cmd:", "said:", "kw:", "hook:", "state:", "verdict:", "ask:", "site:", "svc:")
TRIVIAL_PLACE = __import__("re").compile(r"^(dir|proj):~/(\.claude/projects(/c--users-[^/]+(/memory)?)?|[^/]+|\.claude/[^/]+)$",
                                         __import__("re").I)


def specific(why):
    return any(not str(x).startswith(GENERIC) and not TRIVIAL_PLACE.match(str(x).split(" (")[0]) for x in (why or []))


SERVED = os.path.join(LIVE, "served.jsonl")      # only the evaluations that served a line: small, covers days
EMITTED = os.path.join(LIVE, "emitted.jsonl")    # the hook's receipt for each evaluation whose text it printed (29 Sep)
LAST_EID = [None]                                # this process's last evaluation that returned text, for mark_emitted
_KINDS = {}


def cached_kind(sid):
    """A receiver's kind from session_kinds' settled cache, or 'unknown'. Never session_kinds.kind(): that lists every
    transcript on the machine (1.1 s on 27 Sep) and this runs inside a hook. Only rows written before 27 Sep 09:xx need
    it; newer rows carry the kind the membrane read when it served them."""
    if "d" not in _KINDS:
        try:
            _KINDS["d"] = json.load(open(KINDS_CACHE, encoding="utf-8"))
        except Exception:
            _KINDS["d"] = {}
    return _KINDS["d"].get(str(sid or "").lower(), "unknown")


def receiver_kind(st, tp):
    """his / test / script / unknown for the session being evaluated, read once from the head of its own transcript and
    kept in its state; 'unknown' (a transcript too new to show its entrypoint) is read again next time."""
    k = st.get("kind")
    if k in (None, "unknown"):
        try:
            import session_kinds
            k = session_kinds.kind_from_transcript(tp)
        except Exception:
            k = "unknown"
        st["kind"] = k
    return k


def served_elsewhere(now, hours=6.0, tail=400000):
    """{slug: set of sessions} served in the last `hours`. Read from the served-lines log (26 Sep 19:55: the fire log's
    400 KB tail covered about 40 minutes at 300 evaluations an hour, not six hours, and the over-used qform memory
    reached four more sessions after the two-session rule), plus the fire log's tail for anything the log missed.
    A test worker's row does not count (27 Sep, account03/B15 R9): the benches drew 39 of the first 46 memory lines,
    and two of them holding a memory would keep it from his next session. His sessions, scripts and unknown receivers
    still count, so the rule against one memory going everywhere stands.
    The fix memory of an error line held back by design counts as served (28 Sep, account01/B08 R49,
    B08-R49-CROSSSESSION-QUOTA-INTERFERENCE-045): under the focal estimand the two arms may differ only in what was
    printed, and until then a hold left the memory's quota free, so it could reach a later session that the shown line
    would have kept it from."""
    out = {}
    for path, n in ((SERVED, 4 * tail), (FIRES, tail)):
        try:
            with open(path, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - n))
                data = fh.read()
        except OSError:
            continue
        for raw in data.split(b"\n")[1:] if size > n else data.split(b"\n"):
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            if now - r.get("t", 0) > hours * 3600:
                continue
            got = [s[1] for s in r.get("served") or [] if s and s[0] in ("mem", "word")]
            got += [x for x in r.get("held_fix") or [] if x]
            if not got or (r.get("kind") or cached_kind(r.get("sid"))) == "test":
                continue
            for sl in got:
                out.setdefault(sl, set()).add(r.get("sid", "")[:8])
    return out


def served_row(now, sid, ep, kind, served, held_fix=None):
    """The served-lines log row (live/served.jsonl) that served_elsewhere() reads for the two-session quota. A held error
    line's fix memory rides along as held_fix, so the quota spends it as it would a shown one (account01/B08 R49)."""
    row = {"t": round(now, 1), "sid": sid[:40], "ep": ep, "kind": kind, "served": [list(x) for x in served]}
    if held_fix:
        row["held_fix"] = list(held_fix)
    return row
MEM_KINDS = ("mem", "memw", "recalled", "served")


# THE WORD CHANNEL (26 Sep; the goal: keep the part that tested well and combine it with the
# graph). SHADOW ONLY. Walk-forward replay of the live configuration (results/LIVE_MEMBRANE_STUDY_2026-09-26_1929_G_tight_lex.json,
# cut 23 Sep 15:57, 120 sessions): over all 131 prompt moments the recall stack's BM25 top memory, among memories that existed
# and the session had not seen, was read within 30 minutes after 27, and above a score of 61.4 after 20 of 41. Split by
# session kind, every one of those came from the 88 headless sessions (a script's prompt names a problem, the solver reads
# the memory about it); on the 43 prompts he typed in 25 interactive sessions it was read after 0. So it is computed and
# logged on every prompt, graph_learn.py judges it on his prompts apart from the headless ones, and it is shown only when
# policy serve_word is on (graph_learn turns it on when his prompts above the bar read the match 25% of the time on 30
# lines) and only in an interactive session. Multiplying BM25 by node size changed nothing (lexg) and keeping only hits
# the graph also reached made it worse (agree, 6 of 71).
_BM25 = {}


def _bm25_conn(bm):
    """The live generation's postings, or the newest finished one while the refresher rebuilds (about a minute in 15)."""
    got = bm._postings_conn(bm.INDEX)
    if got is not None:
        return got
    import glob
    cands = [p for p in glob.glob(bm.INDEX + ".postings.*.sqlite3") if os.path.isfile(p)]
    for p in sorted(cands, key=os.path.getmtime, reverse=True)[:2]:
        try:
            con = sqlite3.connect("file:%s?mode=ro" % p.replace("?", "%3f"), uri=True)
            h = dict(con.execute("select k,v from hdr"))
            if h.get("schema") == bm.POSTINGS_SCHEMA:
                return con, float(h["n"]), float(h["avgdl"])
            con.close()
        except Exception:
            continue
    return None


# THE STANDING-ORDERS CHANNEL (27 Sep; the goal: find whom the channel
# is not helping, and combine the word index, which does help, with the graph). Of his 73 corrections and repeats since
# 1 Sep, 22 restated an order the brain already held (the GPU nine times, pictures six, the voice four). Nothing put the
# order in front of the agent: his ask's own words rarely name it, and the graph's memory lines are read 0.4% of the time.
# Here the word index runs on his last ask plus the names and words of what the session touched in the last ten minutes,
# over his orders only; the graph's part is the query - where the agent is - not a second ranking.
ORDER_NOISE = {"users", "owner", "appdata", "local", "temp", "claude", "scratchpad", "projects", "com", "www", "http",
               "https", "exe", "bin", "tmp", "the", "and", "for", "with", "src", "lib", "home", "jsonl", "json", "txt",
               "html", "memory", "c--users-me", "python", "bash", "powershell", "tool", "cmd", "file", "dir", "proj",
               "said", "kw", "hook"}
_ORDER_TOKEN = __import__("re").compile(r"[a-z][a-z0-9]{2,}")


def order_query(items, ask_words, cap=120):
    """His last ask's words, then the words in the names of what the session touched (newest first)."""
    ws = list(ask_words or [])
    for nm, _age in items:
        _kd, _, nid = nm.partition(":")
        ws += [w for w in _ORDER_TOKEN.findall(str(nid).lower()) if w not in ORDER_NOISE]
    return " ".join(ws[:cap])


def order_candidates(query, seen, db, topn=3, topk=600):
    """His stored orders the query matches, best first: [{slug, score, descr, via}]. A superseded order hands over to
    the one that replaced it."""
    if not query.strip():
        return []
    try:
        if "m" not in _BM25:
            d = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")
            if d not in sys.path:
                sys.path.insert(0, d)
            import bm25
            _BM25["m"] = bm25
        bm = _BM25["m"]
        got = _bm25_conn(bm)
        if got is None:
            return []
        try:
            hits = bm.score_postings(query, topk=topk, conn=got) or []
        finally:
            try:
                got[0].close()
            except Exception:
                pass
    except Exception:
        return []
    out, taken = [], set()
    for sl, sc in hits:
        sl = str(sl).lower()
        row = db.execute("SELECT descr, status, superseded_by FROM memory WHERE slug=?", (sl,)).fetchone()
        if not row or not (sl.startswith("owner_") or "owner-stated" in (row[1] or "").lower()):
            continue
        via, hops = "", 0
        while row[2] and hops < 8:                  # follow the chain to the order that stands today
            nxt = db.execute("SELECT descr, status, superseded_by FROM memory WHERE slug=?", (row[2],)).fetchone()
            if not nxt or row[2] == sl:
                row = None
                break
            via, sl, row, hops = via or sl, row[2], nxt, hops + 1
        if row is None or row[2]:
            continue
        if sl in seen or sl in taken:
            continue
        taken.add(sl)
        out.append({"slug": sl, "score": round(float(sc), 2), "descr": row[0] or "", "via": via})
        if len(out) >= topn:
            break
    return out


_ORDER_MEM = os.path.join(os.path.expanduser("~"), ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")), "memory")


_LV_DB = {}


def lv_descr(slug):
    """A memory's description from the sidecar the hook opened (set in the hook before decide)."""
    db = _LV_DB.get("db")
    row = db.execute("SELECT descr FROM memory WHERE slug=?", (slug,)).fetchone() if db is not None else None
    return (row[0] or "") if row else ""


def order_facts(slug):
    """(created 'YYYY-MM-DD', [parts overtaken]) from the order's own frontmatter; read only when a line is served."""
    created, parts = "", []
    try:
        with open(os.path.join(_ORDER_MEM, slug + ".md"), encoding="utf-8", errors="replace") as fh:
            head = fh.read(12000)
    except OSError:
        return created, parts
    if not head.startswith("---"):
        return created, parts
    fm = head[3:head.find("\n---", 3)] if head.find("\n---", 3) > 0 else head[:4000]
    for ln in fm.splitlines():
        t = ln.strip()
        if t.startswith("created:") and not created:
            created = t.split(":", 1)[1].strip().strip("'\"")[:10]
        elif t.startswith("superseded_in_part:"):
            v = t.split(":", 1)[1].strip()
            try:
                got = json.loads(v)
                parts = [str(x) for x in (got if isinstance(got, list) else [got])]
            except Exception:
                parts = [v.strip("[]'\" ")] if v else []
    return created, parts


def _cut(t, n):
    t = " ".join(str(t).split())
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + " ..."


def order_line(oc):
    """The served text for the leading order: its date, what of it was overtaken, and a newer order on the same ground
    when one ranks within 0.6 of it (his later words win)."""
    c = oc[0]
    created, parts = order_facts(c["slug"])
    newer = ""
    for d in oc[1:3]:
        dc, _ = order_facts(d["slug"])
        if dc and created and dc > created and d["score"] >= 0.6 * c["score"]:
            newer = "; newer on the same ground (%s): `%s`" % (dc, d["slug"])
            break
    over = ("; parts later overtaken: %s" % " | ".join(_cut(x, 140) for x in parts[:3])) if parts else ""
    return "`%s`%s - %s%s%s" % (c["slug"], (" (%s)" % created) if created else "", _cut(c.get("descr") or "", 200),
                                over, newer)


def word_candidates(words, seen, db, topk=40):
    """His prompt's words through the recall stack's BM25 arm: [{slug, score, descr, via}], best first."""
    if not words:
        return []
    try:
        if "m" not in _BM25:
            d = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")
            if d not in sys.path:
                sys.path.insert(0, d)
            import bm25
            _BM25["m"] = bm25
        bm = _BM25["m"]
        got = _bm25_conn(bm)
        if got is None:
            return []
        try:
            hits = bm.score_postings(" ".join(words), topk=topk, conn=got) or []
        finally:
            try:
                got[0].close()
            except Exception:
                pass
    except Exception:
        return []
    out, taken = [], set()
    for sl, sc in hits:
        sl = str(sl)
        row = db.execute("SELECT descr, superseded_by FROM memory WHERE slug=?", (sl,)).fetchone()
        if not row:
            continue                                  # not a memory (an index file, a note elsewhere)
        via = ""
        if row[1]:                                    # a superseded memory hands over to the one that replaced it
            nxt = db.execute("SELECT descr FROM memory WHERE slug=?", (row[1],)).fetchone()
            if not nxt:
                continue
            via, sl, row = sl, row[1], (nxt[0], None)
        if sl.lower() in seen or sl.lower() in SKIP_SLUGS or sl in taken:
            continue
        taken.add(sl)
        out.append({"slug": sl, "score": round(float(sc), 2), "descr": row[0] or "", "via": via})
    return out


def policy():
    p = dict(DEFAULT_POLICY)
    try:
        with open(POLICY, encoding="utf-8") as fh:
            p.update(json.load(fh))
    except Exception:
        pass
    return p


def base_level(uses_json, t):
    """ACT-R base level at t from the store's use record [last uses, older count, first time] (context_rank.size)."""
    if not uses_json:
        return None
    try:
        last, old_n, old_first = json.loads(uses_json)
    except Exception:
        return None
    s = sum(max((t - ts) / 3600.0, 1.0 / 60.0) ** -0.5 for ts in last if ts < t)
    if old_n and old_first and last and old_first < t:
        span = max((min(last) - old_first) / 3600.0, 1.0 / 60.0)
        age = max((t - min(last)) / 3600.0, 1.0 / 60.0)
        s += old_n * ((age + span) ** 0.5 - age ** 0.5) / (0.5 * span)
    return math.log(s) if s > 0 else None


def merge_uses(main_json, delta_json):
    """A node's use record [last uses, older count, first time] from the store, with the fast layer's newer uses added
    (the last 50 kept, the rest counted), so node size (ACT-R base level) sees this afternoon's uses too."""
    try:
        new = json.loads(delta_json) if delta_json else []
    except Exception:
        new = []
    if not new:
        return main_json
    try:
        last, old_n, first = json.loads(main_json) if main_json else ([], 0, None)
    except Exception:
        last, old_n, first = [], 0, None
    allu = sorted({round(float(x), 3) for x in (last or [])} | {round(float(x), 3) for x in new})
    drop = max(0, len(allu) - 50)
    old_n = int(old_n or 0)
    if drop:
        old_n += drop
        first = min(first, allu[0]) if first else allu[0]
        allu = allu[-50:]
    if first is None and old_n:
        first = allu[0]
    return json.dumps([allu, old_n, first])


class Live:
    def __init__(self, sidecar=SIDECAR, learned=LEARNED, delta="auto"):
        """delta: "auto" opens the fast layer (live_delta.py) when it was built on this sidecar's store - a walk-forward
        study's cut sidecar has another store, so it gets none; None leaves it out; a path opens that file."""
        self.db = sqlite3.connect("file:%s?mode=ro" % sidecar.replace("\\", "/"), uri=True, timeout=5)
        meta = {k: json.loads(v) for k, v in self.db.execute("SELECT k, v FROM meta")}
        self.meta = meta
        self.delta, self.dmeta = None, None
        if delta:
            try:
                if HERE not in sys.path:
                    sys.path.insert(0, HERE)
                import live_delta
                if delta == "auto":
                    self.delta, self.dmeta = live_delta.open_delta(meta)
                else:
                    self.delta = sqlite3.connect("file:%s?mode=ro" % str(delta).replace("\\", "/"), uri=True, timeout=2)
                    self.dmeta = {k: json.loads(v) for k, v in self.delta.execute("SELECT k, v FROM meta")}
            except Exception:
                self.delta, self.dmeta = None, None
        self.kinds = meta.get("kinds") or ["dir", "err", "file", "host", "mem", "proj", "repo", "tool", "verdict"]
        self.k_of = {k: i for i, k in enumerate(self.kinds)}
        self.learned = None
        if os.path.exists(learned):
            try:
                self.learned = sqlite3.connect("file:%s?mode=ro" % learned.replace("\\", "/"), uri=True, timeout=2)
            except Exception:
                self.learned = None

    # ---- reads -------------------------------------------------------------------------------------------------
    def node_ids(self, names):
        out = {}
        names = list(dict.fromkeys(names))
        for i in range(0, len(names), 400):
            chunk = names[i:i + 400]
            q = "SELECT id, name FROM node WHERE name IN (%s)" % ",".join("?" * len(chunk))
            for nid, nm in self.db.execute(q, chunk):
                out[nm] = nid
        return out

    def names_of(self, ids):
        ids = list(dict.fromkeys(int(x) for x in ids))
        out = {}
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            q = "SELECT id, name, uses FROM node WHERE id IN (%s)" % ",".join("?" * len(chunk))
            for nid, nm, u in self.db.execute(q, chunk):
                out[nid] = (nm, u)
        return out

    def learned_factor(self, pairs, pol, base_p):
        """Edge factor from the learner's outcomes: ((pos + p0 m) / (pos + neg + m)) / p0, clipped; 1.0 unjudged."""
        if not self.learned or not pairs:
            return {}
        srcs = list({s for s, _d in pairs})
        out = {}
        m = pol["learned_prior_n"]
        try:
            for i in range(0, len(srcs), 300):
                chunk = srcs[i:i + 300]
                q = "SELECT src, dst, pos, neg FROM learned WHERE src IN (%s)" % ",".join("?" * len(chunk))
                for s, d, pos, neg in self.learned.execute(q, chunk):
                    if (s, d) in pairs:
                        p0 = max(0.02, base_p)
                        f = ((pos + p0 * m) / (pos + neg + m)) / p0
                        out[(s, d)] = min(pol["learned_max"], max(pol["learned_min"], f))
        except Exception:
            return {}
        return out

    def _merge_delta(self, rows, dst_names, kind_of_dst, by_id, w_of, t):
        """The fast layer's edges from the active items, merged into the store's rows on one clock. The store's counts
        were faded to its own last event and the fast layer's to its build; both are faded on to t by the store's
        half-life, and a pair's PMI is taken over the two together: count, the item's out-weight, the target's
        in-weight and the grand total, each the store's plus the fast layer's. A pair both hold gets the combined score;
        a pair only the fast layer holds is added, with a new negative id when its target is new to the store. Returns
        the rows, the name and kind tables grown, and counts for the log."""
        d, dm = self.delta, self.dmeta
        hl = float(dm.get("half_life_s") or 0.0)
        fm = 0.5 ** (max(0.0, t - float(self.meta.get("store_until") or t)) / hl) if hl else 1.0
        fd = 0.5 ** (max(0.0, t - float(dm.get("ref_time") or t)) / hl) if hl else 1.0
        tot = float(self.meta.get("total_w") or 0.0) * fm + float(dm.get("total_w") or 0.0) * fd
        names = list(w_of)
        dsrc = {}
        for i in range(0, len(names), 400):
            chunk = names[i:i + 400]
            q = "SELECT id, name, out_w FROM dnode WHERE name IN (%s)" % ",".join("?" * len(chunk))
            for nid, nm, ow in d.execute(q, chunk):
                dsrc[nid] = (nm, float(ow or 0.0))
        info = {"age_s": round(t - float(dm.get("ref_time") or t)), "src": len(dsrc), "pairs": 0, "new": 0, "updated": 0,
                "new_nodes": 0}
        if not dsrc:
            return rows, dst_names, kind_of_dst, by_id, info
        raw = []
        il = list(dsrc)
        for i in range(0, len(il), 300):
            chunk = il[i:i + 300]
            q = "SELECT src, dst, k, cnt FROM dedge WHERE src IN (%s)" % ",".join("?" * len(chunk))
            raw += d.execute(q, chunk).fetchall()
        info["pairs"] = len(raw)
        ddst = {}
        dl = list({r[1] for r in raw})
        for i in range(0, len(dl), 400):
            chunk = dl[i:i + 400]
            q = "SELECT id, name, in_w, uses FROM dnode WHERE id IN (%s)" % ",".join("?" * len(chunk))
            for nid, nm, iw, u in d.execute(q, chunk):
                ddst[nid] = (nm, float(iw or 0.0), u)
        involved = list({v[0] for v in dsrc.values()} | {v[0] for v in ddst.values()})
        main = {}
        for i in range(0, len(involved), 400):
            chunk = involved[i:i + 400]
            q = "SELECT id, name, in_w, out_sum, uses FROM node WHERE name IN (%s)" % ",".join("?" * len(chunk))
            for nid, nm, iw, osum, u in self.db.execute(q, chunk):
                main[nm] = (nid, float(iw or 0.0), float(osum or 0.0), u)
        idx = {}
        for j, (src, dst, _k, _s, _c) in enumerate(rows):
            dn = dst_names.get(dst, (None,))[0]
            if dn is not None:
                idx[(by_id[src], dn)] = j
        rows = list(rows)
        synth = {}

        def key_of(name):
            m = main.get(name)
            if m:
                return m[0]
            if name not in synth:
                synth[name] = -(len(synth) + 1)
            return synth[name]
        for s_id, d_id, k, cnt_d in raw:
            if s_id not in dsrc or d_id not in ddst:
                continue
            sn, ow_d = dsrc[s_id]
            dn, iw_d, u_d = ddst[d_id]
            ms, md = main.get(sn), main.get(dn)
            j = idx.get((sn, dn))
            cnt_m = float(rows[j][4] or 0.0) if j is not None else 0.0
            out_c = (ms[2] if ms else 0.0) * fm + ow_d * fd
            in_c = (md[1] if md else 0.0) * fm + iw_d * fd
            cnt_c = cnt_m * fm + float(cnt_d or 0.0) * fd
            if out_c <= 0 or in_c <= 0 or tot <= 0 or cnt_c <= 0:
                continue
            pmi = math.log((cnt_c / out_c) / (in_c / tot))
            s_c = round(pmi * math.log1p(cnt_c), 4) if pmi > 0 else 0.0
            if j is not None:
                src, dst, kk, _s_old, c_old = rows[j]
                rows[j] = (src, dst, kk, s_c, c_old)
                info["updated"] += 1
                continue
            if s_c <= 0:
                continue
            sk, dk = key_of(sn), key_of(dn)
            by_id.setdefault(sk, sn)
            if dk not in dst_names:
                dst_names[dk] = (dn, md[3] if md else None)
            kind_of_dst[dk] = k
            rows.append((sk, dk, k, s_c, cnt_c))
            info["new"] += 1
        info["new_nodes"] = len(synth)
        # this afternoon's uses on every target named, for node size
        got = {v[0]: v[2] for v in ddst.values() if v[2]}
        for dk, (dn, u) in list(dst_names.items()):
            if dn in got:
                dst_names[dk] = (dn, merge_uses(u, got[dn]))
        return rows, dst_names, kind_of_dst, by_id, info

    # ---- the activation ----------------------------------------------------------------------------------------
    def evaluate(self, items, t, seen_slugs=(), served_recent=(), pol=None, base_p=0.2, mem_asof=None):
        """items: [(node name, age s)] newest first. Returns a dict of ranked candidates per channel, without deciding.
        mem_asof (a replay's moment): memories whose file was last written after it did not exist yet and are left out."""
        pol = pol or policy()
        hl = pol["half_life_s"]
        w_of = {}
        for nm, age in items:
            w = 0.5 ** (max(0.0, age) / hl)
            if w > w_of.get(nm, 0.0):
                w_of[nm] = w
        ids = self.node_ids(w_of)
        by_id = {nid: nm for nm, nid in ids.items()}
        hebb = collections.defaultdict(float)
        why = collections.defaultdict(list)
        kind_of_dst = {}
        rows = []
        idl = list(by_id)
        for i in range(0, len(idl), 300):
            chunk = idl[i:i + 300]
            q = "SELECT src, dst, k, s, cnt FROM edge WHERE src IN (%s)" % ",".join("?" * len(chunk))
            rows += self.db.execute(q, chunk).fetchall()
        # learned factors for the (item, target) pairs we are about to use; names are resolved for mem/err/place only
        for src, dst, k, s, _c in rows:
            kind_of_dst[dst] = k
        need = {dst for dst, k in kind_of_dst.items()}
        dst_names = self.names_of(need) if need else {}
        delta_info = None
        if self.delta is not None and pol.get("use_delta", True):
            try:
                rows, dst_names, kind_of_dst, by_id, delta_info = self._merge_delta(rows, dst_names, kind_of_dst, by_id,
                                                                                    w_of, t)
            except Exception as e:           # the fast layer is an addition; the store's rows stand without it
                delta_info = {"error": type(e).__name__}
        pairs = {(by_id[src], dst_names[dst][0]) for src, dst, k, s, _c in rows if dst in dst_names}
        lf = self.learned_factor(pairs, pol, base_p)
        for src, dst, k, s, _c in rows:
            w = w_of[by_id[src]]
            dn = dst_names.get(dst, (None,))[0]
            f = lf.get((by_id[src], dn), 1.0) if dn else 1.0
            c = w * s * f
            hebb[dst] += c
            why[dst].append((c, by_id[src]))
        out = {"n_items": len(w_of), "n_known": len(ids) + sum(1 for x in by_id if x < 0)}
        if delta_info is not None:
            out["delta"] = delta_info
        seen = set(seen_slugs)

        def ranked(kind, n):
            ki = self.k_of.get(kind, -99)
            xs = [(v, d) for d, v in hebb.items() if kind_of_dst.get(d) == ki]
            xs.sort(reverse=True)
            return [(dst_names[d][0], round(v, 2), dst_names[d][1], sorted(why[d], reverse=True)[:3]) for v, d in xs[:n]
                    if d in dst_names]
        mem_h = ranked("mem", 60)
        err_h = ranked("err", 8)
        places = ranked("dir", 8) + ranked("proj", 5)
        # memories reached by content (their text names what is active) and by link (linked to memories touched now)
        reach = collections.defaultdict(float)
        rwhy = collections.defaultdict(list)
        names = list(w_of)
        for i in range(0, len(names), 300):
            chunk = names[i:i + 300]
            q = "SELECT node, slug, w FROM content WHERE node IN (%s)" % ",".join("?" * len(chunk))
            for nd, slug, w in self.db.execute(q, chunk):
                reach[slug] += w_of[nd] * w
                rwhy[slug].append((w_of[nd] * w, nd, "names"))
        touched = {}
        for nm, w in w_of.items():
            kd, _, sl = nm.partition(":")
            if kd in MEM_KINDS and sl:
                touched[sl] = max(touched.get(sl, 0.0), w)
        tl = list(touched)
        for i in range(0, len(tl), 300):
            chunk = tl[i:i + 300]
            q = "SELECT src, dst, etype, w FROM link WHERE src IN (%s)" % ",".join("?" * len(chunk))
            for s_, d, et, w in self.db.execute(q, chunk):
                reach[d] += 3.0 * touched[s_] * w
                rwhy[d].append((3.0 * touched[s_] * w, "mem:" + s_, et))
        cand = {}
        for nm, v, u, wy in mem_h:
            sl = nm.split(":", 1)[1]
            cand[sl] = {"slug": sl, "hebb": v, "uses": u, "why": [j for _c, j in wy]}
        for sl, v in sorted(reach.items(), key=lambda kv: -kv[1])[:40]:
            c = cand.setdefault(sl, {"slug": sl, "hebb": 0.0, "uses": None, "why": []})
            c["reach"] = round(v, 2)
            c["why"] += ["%s (%s)" % (nd, et) for _c, nd, et in sorted(rwhy[sl], reverse=True)[:2]]
        # memory facts: existence, description, supersession; the successor replaces a superseded memory
        sl_list = list(cand)
        facts = {}
        for i in range(0, len(sl_list), 400):
            chunk = sl_list[i:i + 400]
            q = "SELECT slug, descr, status, superseded_by, mtime FROM memory WHERE slug IN (%s)" % ",".join("?" * len(chunk))
            for sl, de, stt, sup, mt in self.db.execute(q, chunk):
                if mem_asof is None or (mt or 0) <= mem_asof:
                    facts[sl] = (de, stt, sup)
        final = {}
        for sl, c in cand.items():
            if sl not in facts or sl in SKIP_SLUGS:
                continue
            de, stt, sup = facts[sl]
            if sup:
                if sup in facts or self.db.execute("SELECT 1 FROM memory WHERE slug=?", (sup,)).fetchone():
                    c = dict(c, via=sl, slug=sup)
                    sl = sup
                    row = self.db.execute("SELECT descr FROM memory WHERE slug=?", (sup,)).fetchone()
                    de = row[0] if row else de
                else:
                    continue
            if sl in seen or sl in final:
                continue
            c["descr"] = de
            c["size"] = base_level(c.get("uses"), t) if c.get("uses") else None
            final[sl] = c
        cl = list(final.values())
        k = pol["rrf_k"]
        for lst, key, wgt in ((sorted([c for c in cl if c["hebb"] > 0], key=lambda c: -c["hebb"]), "hebb", pol["w_hebb"]),
                              (sorted([c for c in cl if c.get("size") is not None], key=lambda c: -c["size"]), "size",
                               pol["w_size"]),
                              (sorted([c for c in cl if c.get("reach", 0) > 0], key=lambda c: -c["reach"]), "reach",
                               pol["w_reach"])):
            for r, c in enumerate(lst):
                c["fused"] = c.get("fused", 0.0) + wgt / (k + r)
        cl.sort(key=lambda c: -c.get("fused", 0.0))
        out["mem"] = cl[:10]
        out["mem_all"] = cl
        out["err"] = err_h
        out["verdict"] = ranked("verdict", 3)          # his corrections and repeats: shadow, measured before shown
        out["place"] = [p for p in places if p[0] not in w_of][:6]
        # attractors lit by the active items
        attr = []
        if idl:
            q = ("SELECT m.attractor, COUNT(*) FROM member m WHERE m.node IN (%s) GROUP BY m.attractor HAVING COUNT(*) >= ?"
                 % ",".join("?" * min(len(idl), 900)))
            for aid, n in self.db.execute(q, idl[:900] + [pol["attr_min_members"]]).fetchall():
                row = self.db.execute("SELECT size, cohesion, members, leads, mistakes FROM attractor WHERE id=?",
                                      (aid,)).fetchone()
                if row:
                    qa = "SELECT node FROM member WHERE attractor=? AND node IN (%s)" % ",".join("?" * min(len(idl), 900))
                    act = [by_id[x] for (x,) in self.db.execute(qa, [aid] + idl[:900]) if x in by_id]
                    act.sort(key=lambda nm: -w_of.get(nm, 0.0))
                    attr.append({"id": aid, "active": n, "size": row[0], "cohesion": row[1],
                                 "members": act[:3] or json.loads(row[2])[:5], "active_names": act[:6],
                                 "leads": json.loads(row[3]), "mistakes": json.loads(row[4])})
        attr.sort(key=lambda a: -(a["active"] / max(1.0, a["size"]) ** 0.5))
        out["attr"] = attr[:3]
        out["items_top"] = [(nm, round(w, 3)) for nm, w in sorted(w_of.items(), key=lambda kv: -kv[1])[:12]]
        return out

    def fix_memory(self, err_name, seen):
        """The memory agents read most specifically after this error (the err node's own row to memories)."""
        row = self.db.execute("SELECT id FROM node WHERE name=?", (err_name,)).fetchone()
        if not row:
            return None
        mk = self.k_of.get("mem", -1)
        for dst, s in self.db.execute("SELECT dst, s FROM edge WHERE src=? AND k=? ORDER BY s DESC LIMIT 8", (row[0], mk)):
            nm = self.db.execute("SELECT name FROM node WHERE id=?", (dst,)).fetchone()
            if not nm:
                continue
            sl = nm[0].split(":", 1)[1]
            if sl in seen or sl in SKIP_SLUGS:
                continue
            f = self.db.execute("SELECT descr, superseded_by FROM memory WHERE slug=?", (sl,)).fetchone()
            if not f:
                continue
            if f[1]:
                g = self.db.execute("SELECT descr FROM memory WHERE slug=?", (f[1],)).fetchone()
                if g and f[1] not in seen:
                    return f[1], g[0]
                continue
            return sl, f[0]
        return None


# ---- the decision -------------------------------------------------------------------------------------------------
def short(nm, n=46):
    kd, _, rest = nm.partition(":")
    rest = rest.replace(os.path.expanduser("~").replace(chr(92), "/"), "~")
    if len(rest) > n:
        rest = "..." + rest[-(n - 3):]
    return "%s:%s" % (kd, rest)


def precision_line(cls):
    try:
        p = json.load(open(PRECISION, encoding="utf-8")).get(cls)
    except Exception:
        p = None
    if not p or not p.get("judged"):
        return "unjudged - a lead, not a verdict"
    return "taken up %d of %d judged times - a lead, not a verdict" % (p.get("taken", 0), p["judged"])


def stale_orders(items, db, window=600.0):
    """His orders (owner_* or owner-stated) the session opened or recalled in the window that have been superseded:
    [(old slug, slug in force, chain)], newest first."""
    out, taken = [], set()
    for nm, age in items:
        kd, _, sl = nm.partition(":")
        sl = sl.lower()
        if kd not in ("mem", "recalled") or not sl or age > window or sl in taken:
            continue
        try:
            row = db.execute("SELECT status, superseded_by FROM memory WHERE slug=?", (sl,)).fetchone()
        except Exception:
            row = None
        if not row or not row[1] or not (sl.startswith("owner_") or "owner-stated" in (row[0] or "").lower()):
            continue
        chain, cur = [sl], row[1]
        while cur and cur not in chain and len(chain) < 9:
            chain.append(cur)
            nxt = db.execute("SELECT superseded_by FROM memory WHERE slug=?", (cur,)).fetchone()
            cur = nxt[0] if nxt and nxt[0] else None
        if len(chain) > 1:
            taken.add(sl)
            out.append((sl, chain[-1], chain))
    return out


def diffuse_pol(pol):
    """The diffuse-line trial's own split (rule: no diffuse line is served until a trial shows it changes an outcome): memory and place lines are held in one session-block in hold_every_diffuse (2 =
    half), not one in hold_every as the error and fix lines are."""
    return dict(pol, hold_every=max(2, int(pol.get("hold_every_diffuse", 2))))


def held_back(state, key, now, pol):
    """True for one qualifying line in pol['hold_every']: fixed per session, key and two-hour block, so the same line is not
    shown on the next evaluation of that block. His laws are never held."""
    k = int(pol.get("hold_every", 4) or 0)
    if k <= 1 or str(key).split(":", 1)[-1].startswith("owner_law_"):
        return False
    return zlib.crc32(("%s|%s|%d" % (state.get("sid", ""), key, int(now // 7200))).encode("utf-8")) % k == 0


def decide(ev, now, state, pol):
    """At most max_lines lines, above threshold, outside refractory and the hourly budget. Returns (lines, served)."""
    served_at = state.setdefault("served", {})
    hour = [x for x in state.get("fire_times", []) if now - x < 3600]
    if len(hour) >= pol["max_fires_per_hour"]:
        return [], []
    tired = set(pol.get("tired") or [])
    lines, served = [], []
    held_n = [0]           # a line held back by design takes the slot and the hourly budget a shown line would
                           # (28 Sep, account03/B08 R12, B08-R12-HOLD-PARITY-010: until then a HOLD left room that a
                           # SHOW used, so the arm a line drew changed what the next lines could be)
    held_mems = []         # the fix memory of an error line held back (focal): named for the rest of this evaluation as
                           # the shown line names it (28 Sep, account01/B08 R49: until then a HOLD let the memory channel
                           # serve a second memory in the same evaluation, which the SHOW arm's named memory stopped)

    def room():
        return len(lines) + held_n[0] < pol["max_lines"]

    def fresh(key):
        t0 = served_at.get(key)
        return t0 is None or now - t0 > pol["refractory_s"]
    seen = set(state.get("seen", []))
    # 1. an attractor that led to a mistake, or an error this state has led to before
    if pol["serve_attr"]:
        for a in ev.get("attr", []):
            key = "attr:%d" % a["id"]
            if not fresh(key) or a["size"] > pol["attr_max_size"] or a["active"] / max(1, a["size"]) < pol["attr_min_frac"]:
                continue                        # a big generic community (every shell session lights it) says nothing
            mis = [m for m in a.get("mistakes", []) if m[1] >= pol["attr_min_lift"] and m[2] >= pol["attr_min_count"]
                   and not any(x in m[0] for x in pol["err_skip"])]
            if not mis:
                continue
            fm = None
            for nm, lf, n in a.get("leads", []):
                if nm.startswith("mem:") and nm[4:] not in seen and nm[4:] not in tired:
                    fm = nm[4:]
                    break
            txt = "[subconscious] attractor %d is lit (%d of its members active now: %s); before, this led to `%s` at %.0fx its base rate (%d times)" % (
                a["id"], a["active"], ", ".join(short(m, 30) for m in a["members"][:3]), short(mis[0][0], 60), mis[0][1], mis[0][2])
            if fm:
                txt += " - this reminds me of `%s`" % fm
                served.append(("mem", fm, a.get("active_names", [])[:4]))
            txt += " (hint: %s)" % precision_line("attr")
            lines.append(txt)
            served.append(("attr", key, a.get("active_names", [])[:4]))
            break
    if pol["serve_err"] and room() and ev.get("err"):
        nm, v, _u, wy = ev["err"][0]
        key = nm                                   # already "err:<class>"
        if v >= pol["err_hebb_min"] and fresh(key) and not any(s[1] == key for s in served) and \
                not any(x in nm for x in pol["err_skip"]):
            fm = ev.get("_fix")
            by = [j for _c, j in wy[:3]]
            estimand = pol.get("hold_estimand", "focal")
            # the unit the assignment is drawn for (held_back's own key), the same in both arms: the trial id of the record
            unit = "%s|%s|%d" % (state.get("sid", ""), key, int(now // 7200))
            if held_back(state, key, now, pol):
                served.append(("held", key, by))           # held back: logged, judged as a prediction, not shown
                held_n[0] += 1
                if fm:
                    # the fix memory this line would have named, logged in both arms and under both estimands, so a later
                    # exposure of it can be joined to this evaluation (28 Sep, account01/B08 R49,
                    # B08-R49-TOTAL-ESTIMAND-FIX-ID-048: under "total" nothing recorded it)
                    state["fix_rec"] = {"candidate": fm[0], "delivered": False, "spent": estimand == "focal",
                                        "estimand": estimand, "unit": unit}
                if fm and estimand == "focal":
                    # The fix memory a shown line would have named is spent here too (refractory now, seen by the hook
                    # entry), so the two arms differ only in what was printed; until 28 Sep it stayed free in HOLD and a
                    # later channel could serve it there and not in SHOW (account03/B08 R12, B08-R12-FIXMEM-CARRYOVER-014).
                    # hold_estimand "total" keeps the old behaviour, for a study that labels the later exposure mediated.
                    served_at["mem:" + fm[0]] = now
                    state.setdefault("held_fix", []).append(fm[0])
                    held_mems.append(fm[0])
            else:
                txt = "[subconscious] what is active now (%s) has come before `%s` in past sessions (graph weight %.0f)" % (
                    ", ".join(short(j, 30) for j in by[:2]), short(nm, 60), v)
                if fm:
                    txt += " - this reminds me of `%s` - %s" % (fm[0], (fm[1] or "")[:140])
                    served.append(("mem", fm[0], by))
                    state["fix_rec"] = {"candidate": fm[0], "delivered": True, "spent": True, "estimand": estimand,
                                        "unit": unit, "at": len(served) - 1}
                txt += " (hint: %s)" % precision_line("err")
                lines.append(txt)
                served.append(("err", key, by))
    # his corrections: the mistakes that matter are the ones he corrects (walk-forward 26 Sep, 2,125 moments: a correction or
    # repeat of his follows 3.1% of moments; the graph's warning at the 2% bar is right 16.7%, 5x the base rate)
    vd = [v for v in ev.get("verdict", []) if v[0] in ("verdict:correction", "verdict:repeat")]
    if pol.get("serve_verdict", True) and vd and room():
        nm, v, _u, wy = vd[0]
        by = [j for _c, j in wy[:3]]
        spec = [j for j in by if specific([j])]
        if v >= pol.get("verdict_hebb_min", 76.0) and fresh("verdict") and spec and held_back(state, "verdict", now, pol):
            served.append(("held", "verdict", by))
            held_n[0] += 1
        elif v >= pol.get("verdict_hebb_min", 76.0) and fresh("verdict") and spec:
            area = spec[0].split(":", 1)[1].rstrip("/").rsplit("/", 1)[-1][:40]
            lines.append("[subconscious] sessions in this state (lit by %s) have drawn his corrections before (graph weight "
                         "%.0f); before you report, check the scars for it: python ~/.claude/dreaming/brain.py recall "
                         "\"%s mistakes\" (hint: %s)" % (", ".join(short(j, 34) for j in by[:2]), v, area,
                                                          precision_line("verdict")))
            served.append(("verdict", "verdict", by))
    others = served_elsewhere(now)
    me = str(state.get("sid", ""))[:8]
    # on his prompt, his words first (see word_candidates): the top match only, the channel the replay measured; never into
    # a headless session (a script's prompt, a test worker)
    if pol.get("serve_word", False) and ev.get("ep") == "cli" and room() and ev.get("word"):
        c = ev["word"][0]
        sl = c["slug"]
        if c["score"] >= pol.get("word_min", 61.4) and sl not in tired and fresh("mem:" + sl) \
                and len(others.get(sl, set()) - {me}) < pol.get("mem_max_sessions", 2) and sl not in held_mems \
                and not any(s[0] in ("mem", "word") and s[1] == sl for s in served):
            lines.append("[subconscious] this reminds me of `%s` - %s (the prompt's own words match it, score %.0f%s; "
                         "hint: %s)" % (sl, (c.get("descr") or "")[:160], c["score"],
                                        "; via superseded %s" % c["via"] if c.get("via") else "", precision_line("word")))
            served.append(("word", sl, []))
    if pol["serve_mem"] and room() and not any(s[0] in ("mem", "word") for s in served) and not held_mems:
        # walk-forward study of 26 Sep (cut 09-23, 120 sessions, 2,369 moments): among the memories the moment connects
        # to, the biggest node (ACT-R base level: used most, most recently) is read within 30 minutes 12-17% of the time
        # above its bar, the strongest learned edge 6%, the blend of both less than either. So: size first, then edges.
        pool = [c for c in ev.get("mem_all", ev.get("mem", [])) if c["slug"] not in tired and fresh("mem:" + c["slug"])
                and not any(s[0] == "mem" and s[1] == c["slug"] for s in served)
                and specific(c.get("why")) and len(others.get(c["slug"], set()) - {me}) < pol.get("mem_max_sessions", 2)]
        # what the quota held back (eligible otherwise), with how many other sessions had each: the cost of the rule
        ev["qb"] = [[c["slug"], len(others.get(c["slug"], set()) - {me})] for c in ev.get("mem_all", ev.get("mem", []))
                    if c["slug"] not in tired and fresh("mem:" + c["slug"]) and specific(c.get("why"))
                    and len(others.get(c["slug"], set()) - {me}) >= pol.get("mem_max_sessions", 2)][:3]
        by_size = sorted((c for c in pool if c.get("size") is not None), key=lambda c: -c["size"])
        by_hebb = sorted(pool, key=lambda c: -c.get("hebb", 0))
        pick, why_pick = None, ""
        if by_size and by_size[0]["size"] >= pol["mem_size_min"]:
            pick, why_pick = by_size[0], "a big node right now"
        elif by_hebb and by_hebb[0].get("hebb", 0) >= pol["mem_hebb_min"]:
            pick, why_pick = by_hebb[0], "a strong learned edge"
        elif pool and max(c.get("reach", 0) for c in pool) >= pol["mem_reach_min"]:
            pick = max(pool, key=lambda c: c.get("reach", 0))
            why_pick = "its text names what is active"
        for c in ([pick] if pick else []):
            sl = c["slug"]
            if pol.get("hold_mem", True) and held_back(state, "mem:" + sl, now, diffuse_pol(pol)):
                # the diffuse-line trial: a memory line is shown or held by the same hash as the error
                # lines, so whether showing it changes what the session reads can be measured; held: logged, judged, not shown
                served.append(("held", "mem:" + sl, [x.split(" (")[0] for x in (c.get("why") or [])[:4]]))
                held_n[0] += 1
                break
            lit = ", ".join(short(x, 34) for x in (c.get("why") or [])[:2])
            txt = "[subconscious] this reminds me of `%s` - %s (%s, lit by %s%s; hint: %s)" % (
                sl, (c.get("descr") or "")[:160], why_pick, lit or "the session's recent work",
                "; via superseded %s" % c["via"] if c.get("via") else "", precision_line("mem"))
            lines.append(txt)
            served.append(("mem", sl, [x.split(" (")[0] for x in (c.get("why") or [])[:4]]))
            break
    # the order in force: the session just opened or recalled one of his orders that has been superseded (stale_orders)
    if pol.get("serve_successor", True) and ev.get("ep") == "cli" and ev.get("stale") and room():
        for old, new, chain in ev["stale"]:
            if not fresh("successor:" + old):
                continue
            oc_, _ = order_facts(old)
            nc, nparts = order_facts(new)
            try:
                nd = lv_descr(new)
            except Exception:
                nd = ""
            over = ("; parts of it later overtaken: %s" % " | ".join(_cut(x, 120) for x in nparts[:2])) if nparts else ""
            lines.append("[his standing order, in force] you just read `%s`%s, which he replaced: the order in force is "
                         "`%s`%s - %s%s%s (his later words win)" % (
                             old, (" (%s)" % oc_) if oc_ else "", new, (" (%s)" % nc) if nc else "", _cut(nd, 200), over,
                             ("; the chain: %s" % " -> ".join(chain)) if len(chain) > 2 else ""))
            served.append(("successor", "successor:" + old, [new]))
            break
    # his standing orders (see order_candidates): the leader must lead two evaluations running and clear the bar
    oc = ev.get("orders") or []
    lead = oc[0]["slug"] if oc else None
    runs = state.get("orders_run", [None, 0])
    runs = [lead, runs[1] + 1 if lead and runs[0] == lead else (1 if lead else 0)]
    state["orders_run"] = runs
    if pol.get("serve_orders", False) and ev.get("ep") == "cli" and oc and room():
        c = oc[0]
        if c["score"] >= pol.get("orders_min", 30.0) and runs[1] >= pol.get("orders_stable", 2) \
                and fresh("order:" + c["slug"]) and c["slug"] not in tired and held_back(state, "order:" + c["slug"], now, pol):
            served.append(("held", "order:" + c["slug"], []))
            held_n[0] += 1
        elif c["score"] >= pol.get("orders_min", 30.0) and runs[1] >= pol.get("orders_stable", 2) \
                and fresh("order:" + c["slug"]) and c["slug"] not in tired:
            lines.append("[his standing order] %s (the ground you are on matches it; he has had to repeat orders "
                         "like this one when agents missed them; his later words win; hint: %s)" % (
                             order_line(oc), precision_line("order")))
            served.append(("order", "order:" + c["slug"], []))
    places = [p for p in ev.get("place", []) if not TRIVIAL_PLACE.match(p[0])]
    if pol["serve_place"] and room() and places:
        nm, v, _u, wy = places[0]
        if v >= pol["place_hebb_min"] and fresh("place:" + nm) and specific([j for _c, j in wy]):
            by = [j for _c, j in wy[:3]]
            if pol.get("hold_place", True) and held_back(state, "place:" + nm, now, diffuse_pol(pol)):
                served.append(("held", "place:" + nm, by))     # the diffuse-line trial, as for memory lines above
                held_n[0] += 1
            else:
                lines.append("[subconscious] sessions doing what this one is doing (%s) usually go next to `%s` (hint: %s)" % (
                    ", ".join(short(j, 30) for j in by[:2]), short(nm, 70), precision_line("place")))
                served.append(("place", "place:" + nm, by))
    for kind, key, _by in served:
        served_at[key if kind not in ("mem", "word") else "mem:" + key] = now
    if lines or held_n[0]:
        state.setdefault("fire_times", []).append(now)
        state["fire_times"] = [x for x in state["fire_times"] if now - x < 3600]
    return lines[:pol["max_lines"]], served


def spend_served(seen, served, st):
    """Add to the session's seen set what the evaluation used: the memories its lines named, and the fix memory of an
    error line held back by design (decide() leaves those in st['held_fix']). Returns the held fix slugs for the record."""
    held_fix = st.pop("held_fix", None) or []
    for kind, key, _by in served:
        if kind in ("mem", "word"):
            seen.add(key)
        elif kind == "held" and str(key).startswith("mem:"):
            seen.add(key[4:])                    # the diffuse-line trial: held, spent as if shown
    seen.update(held_fix)
    return held_fix


# ---- the hook entry -----------------------------------------------------------------------------------------------
def _state_path(sid):
    return os.path.join(SESS, "%s.json" % "".join(ch for ch in sid if ch.isalnum() or ch in "-_")[:80])


def load_state(sid):
    try:
        with open(_state_path(sid), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"t0": time.time(), "calls": 0, "last_eval": 0.0, "seen": [], "served": {}, "fire_times": []}


def save_state(sid, st):
    """Write the session state; True when it reached disk. Never raises. On Windows the rename fails with Access denied
    while another hook call of the same session has the file open, so it is retried for about 0.4 s (17 hook errors
    26-27 Sep, each on an evaluation that served nothing: they lost state updates, not lines). Every other failure -
    making the folder, opening or writing the temporary file on a full disk, any other error at the rename - is caught
    too and returns False: the first repair covered only the rename, and a failure elsewhere could still abort the hook
    after its line had been recorded as served (account01/B20 R17, 27 Sep). run() withholds a line whose state did not
    commit."""
    p = _state_path(sid)
    tmp = p + ".%d.tmp" % os.getpid()
    err = None
    try:
        os.makedirs(SESS, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        for i in range(6):
            try:
                os.replace(tmp, p)
                return True
            except PermissionError as e:
                err = e
                time.sleep(0.02 * (i + 1))
    except Exception as e:
        err = e
    try:
        os.remove(tmp)
    except OSError:
        pass
    try:
        with open(os.path.join(HOME, "logs", "live.log"), "a", encoding="utf-8") as fh:
            fh.write("%s state write failed (the hook went on, its line withheld): %s: %s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), type(err).__name__, str(err)[:200]))
    except Exception:
        pass
    return False


_HELD = {}          # lock path -> the open descriptor whose OS lock this process holds


def _os_lock(fd, take):
    """Take (non-blocking) or release the OS lock on the first byte of an open file; raises OSError when it is held."""
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, 0)
        msvcrt.locking(fd, msvcrt.LK_NBLCK if take else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, (fcntl.LOCK_EX | fcntl.LOCK_NB) if take else fcntl.LOCK_UN)


def lock_session(sid, wait=0.5, stale=None):
    """Hold one session for one hook call from load_state to save_state: the lock's path, or None. Parallel tool calls
    of a session each loaded the same state, each found a line fresh and each served it, so the refractory and the fire
    budget held only for calls made in sequence (account01/B15 R17, 27 Sep). A call that cannot take the lock within
    `wait` seconds leaves the moment to the call holding it and serves nothing.

    The lock is the operating system's, held on an open file for as long as this process holds it, and released by the
    system when the process ends however it ends (28 Sep, interactive audit IA-20260928-03). Until then it was a file
    created exclusively and taken over when older than 60 seconds, whoever held it, and removed at unlock whoever it
    belonged to by then: a live call slower than 60 s (the worst of 8,577 evaluations took 85 s) lost its lock to a
    second call, and its unlock then removed the second call's lock. `stale` is kept for callers and is not used: a dead
    holder's lock is released by the system, a live holder's is never taken."""
    p = _state_path(sid) + ".lock"
    try:
        os.makedirs(SESS, exist_ok=True)
        fd = os.open(p, os.O_RDWR | os.O_CREAT)
    except OSError:
        return None
    end = time.time() + wait
    while True:
        try:
            _os_lock(fd, True)
            _HELD[p] = fd
            return p
        except OSError:
            pass
        if time.time() >= end:
            os.close(fd)
            return None
        time.sleep(0.05)


def unlock_session(p):
    """Release the lock this process holds at p; a path it does not hold is left alone."""
    fd = _HELD.pop(p, None) if p else None
    if fd is None:
        return
    try:
        _os_lock(fd, False)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass
    if os.name == "nt":
        # tidy the file away when nobody else has it open (Windows refuses to delete an open file, so a waiting call keeps
        # it); elsewhere it stays, since unlinking a file another process has locked would let a third lock a new one
        try:
            os.remove(p)
        except OSError:
            pass


def log_eval(rec):
    try:
        with open(FIRES, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except Exception:
        pass


def mark_emitted():
    """The hook wrapper calls this after the text run() returned is written and flushed: one row naming the evaluation.
    The state and the evaluation's row are committed before the text leaves (so a line is never shown twice), which left
    a hook that died between the two with a line on record that never reached the session; the learner now learns from a
    shown line only when this receipt exists (29 Sep, account01/B15 r84, B15-R20-MEMBRANE-COMMIT-BEFORE-EMIT-20260929).
    Fail-safe: a lost receipt costs that evaluation's lessons, never a wrong one."""
    eid, LAST_EID[0] = LAST_EID[0], None
    if not eid:
        return
    try:
        with open(EMITTED, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"eid": eid, "t": round(time.time(), 1)}, separators=(",", ":")) + "\n")
    except Exception:
        pass


def run(payload, now=None, force=False):
    """The hook: returns the text to add to the context ('' for nothing). Never raises."""
    t0 = time.time()
    now = now or t0
    lock = None
    LAST_EID[0] = None
    try:
        if os.path.exists(STOP) or not os.path.exists(SIDECAR):
            return ""
        ev_name = payload.get("hook_event_name") or payload.get("hookEventName") or ""
        sid = str(payload.get("session_id") or "")
        tp = payload.get("transcript_path") or ""
        if not sid or not tp or not os.path.exists(tp):
            return ""
        lock = lock_session(sid)
        if lock is None:                     # a parallel call of this session holds its state: that call decides
            return ""
        pol = policy()
        st = load_state(sid)
        st["sid"] = sid[:40]
        st["calls"] = st.get("calls", 0) + 1
        is_prompt = ev_name == "UserPromptSubmit"
        since = now - st.get("last_eval", 0.0)
        due = force or (is_prompt and since >= pol["prompt_min_gap_s"]) or (
            not is_prompt and (st["calls"] - st.get("calls_at_eval", 0) >= pol["post_every_calls"] or since >= pol["post_every_s"]))
        if not due:
            save_state(sid, st)
            return ""
        sys.path.insert(0, HERE)
        receiver_kind(st, tp)
        import extract_touch_events as X
        items = X.seed_from_transcript(tp, now=now, window=pol["window_s"], k=pol["k_items"], tail_bytes=pol["tail_bytes"])
        his = X.his_text({}, str(payload.get("prompt") or "").strip()) if is_prompt else ""
        if his:                              # a task notification or hook text is not his prompt (26 Sep)
            items = [("kw:" + w, 0.0) for w in X.words(his, cap=40)] + items
        seen = set(st.get("seen", []))
        for nm, _a in items:
            kd, _, sl = nm.partition(":")
            if kd in MEM_KINDS and sl:
                seen.add(sl)
        st["seen"] = sorted(seen)[-3000:]
        st["last_eval"] = now
        st["calls_at_eval"] = st["calls"]
        try:
            base_p = json.load(open(PRECISION, encoding="utf-8")).get("mem", {}).get("rate", 0.2)
        except Exception:
            base_p = 0.2
        lv = Live()
        ev = lv.evaluate(items, now, seen_slugs=seen, pol=pol, base_p=base_p)
        ev["_names"] = {}                    # 'lit by' entries already carry node names
        ev["ep"] = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")    # cli = interactive; sdk-cli = headless (claude -p)
        if his:                              # judged on every prompt (graph_learn word_shadow), shown above word_min
            ev["word"] = word_candidates(X.words(his, cap=40), {s.lower() for s in seen}, lv.db)
            st["ask_words"] = X.words(his, cap=40)
        # his standing orders: his last ask and what the session is on, through the word index (see order_candidates)
        oq = order_query(items, st.get("ask_words") or [])
        if st.get("orders_q") == oq and isinstance(st.get("orders_oc"), list):
            ev["orders"] = [c for c in st["orders_oc"] if c.get("slug", "").lower() not in {x.lower() for x in seen}]
        else:
            ev["orders"] = order_candidates(oq, {s.lower() for s in seen}, lv.db, topk=300)
            st["orders_q"], st["orders_oc"] = oq, ev["orders"][:3]
        ev["stale"] = stale_orders(items, lv.db, window=pol["window_s"])    # a superseded order just read: see decide()
        _LV_DB["db"] = lv.db
        if ev.get("err"):
            ev["_fix"] = lv.fix_memory(ev["err"][0][0], seen)
        lines, served = decide(ev, now, st, pol)
        fix_rec = st.pop("fix_rec", None)       # the error line's fix candidate, for the record only (never saved in state)
        held_fix = spend_served(seen, served, st)
        st["seen"] = sorted(seen)[-3000:]
        # A line is served only once the state that keeps it from repeating is on disk: a line shown on a failed save
        # could be shown again on the next call inside its refractory, and before 27 Sep a failure after the served row
        # was written could leave a row for a line that never printed (account01/B15 and B20, R17). Such a line is withheld,
        # logged under 'withheld' in the evaluation, and may be served on a later call.
        withheld = []
        if not save_state(sid, st) and served:
            withheld, served, lines = [list(s) for s in served], [], []
            held_fix = []                       # the state that spends it was not saved: nothing was spent
            if fix_rec:
                fix_rec.update(delivered=False, spent=False, withheld=True)
                fix_rec.pop("at", None)
        try:
            rec = {"t": round(now, 1), "sid": sid[:40], "ev": "prompt" if is_prompt else "post", "pv": pol.get("version"),
                   "ep": ev.get("ep", ""), "kind": st.get("kind", "unknown"), "qb": ev.get("qb", []),
                   "sc": lv.meta.get("built"), "n": ev.get("n_items"), "known": ev.get("n_known"),
                   "items": ev.get("items_top"),
                   "mem": [[c["slug"], c.get("hebb", 0), c.get("reach", 0), round(c["size"], 3) if c.get("size") is not None
                            else None, round(c.get("fused", 0), 5)] for c in ev.get("mem", [])[:8]],
                   "err": [[e[0], e[1]] for e in ev.get("err", [])[:3]],
                   "verdict": [[e[0], e[1]] for e in ev.get("verdict", [])[:2]],
                   "place": [[p[0], p[1]] for p in ev.get("place", [])[:3]],
                   "word": [[c["slug"], c["score"]] for c in ev.get("word", [])[:3]],
                   "attr": [[a["id"], a["active"], a["size"]] for a in ev.get("attr", [])],
                   "orders": [[c["slug"], c["score"]] for c in ev.get("orders", [])[:3]],
                   "stale": [[o, n] for o, n, _c in ev.get("stale", [])[:3]],
                   "dl": ev.get("delta")}
        except Exception as e:                   # a malformed candidate must not cost the line its record
            rec = {"t": round(now, 1), "sid": sid[:40], "ev": "prompt" if is_prompt else "post",
                   "rec_error": "%s: %s" % (type(e).__name__, str(e)[:120])}
        rec["served"] = [list(s) for s in served]
        # the evaluation's own identity (29 Sep, account03/B08 auto71, B08-R62-OBS-ID-SAME-SESSION-TENTH-COLLISION-032):
        # the learner keyed an evaluation by its tenth-second, 8-character session, event and entry point, and two of
        # them sharing those were one; a retry of the same row keeps its eid, so it is still counted once
        rec["eid"] = uuid.uuid4().hex[:20]
        if held_fix:
            rec["held_fix"] = held_fix          # the fix slug of each held line, for the A/B's later-exposure analysis
        if fix_rec:
            rec["fix"] = fix_rec                # candidate, delivered, spent, estimand (and 'at', its index in served)
        if withheld:
            rec["withheld"] = withheld
        text = "\n".join(lines)
        if served:
            try:
                with open(SERVED, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(served_row(now, sid, ev.get("ep", ""), st.get("kind", "unknown"), served,
                                                   held_fix), separators=(",", ":")) + "\n")
            except OSError as e:
                try:
                    with open(os.path.join(HOME, "logs", "live.log"), "a", encoding="utf-8") as fh:
                        fh.write("%s served row not written (the line was shown): %s: %s\n" % (
                            time.strftime("%Y-%m-%d %H:%M:%S"), type(e).__name__, str(e)[:200]))
                except Exception:
                    pass
        rec["ms"] = round((time.time() - t0) * 1000)
        log_eval(rec)
        LAST_EID[0] = rec["eid"] if text else None
        return text
    except Exception as e:
        try:
            with open(os.path.join(HOME, "logs", "live.log"), "a", encoding="utf-8") as fh:
                fh.write("%s hook error: %s: %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), type(e).__name__, str(e)[:300]))
        except Exception:
            pass
        return ""
    finally:
        unlock_session(lock)
