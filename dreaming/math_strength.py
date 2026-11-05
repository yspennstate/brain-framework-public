#!/usr/bin/env python3
"""Link strengths in the math store that grow from use (claude-6bb7036b-s03, 12 Oct 2026; his 06:17 order: "edges should get
stronger as nodes that are similar are used at similar times, that strength should become automatic and agents should be
able to see the strength between nodes and use that to find information").

    python ~/.claude/dreaming/math_strength.py <card id> <card id>     how strongly two cards are tied, and by what
    python ~/.claude/dreaming/math_strength.py --card <card id>        the cards most used together with this one
    python ~/.claude/dreaming/math_strength.py --top [N]               the strongest pairs in the store
    python ~/.claude/dreaming/math_strength.py --stats                 what the log holds
    python ~/.claude/dreaming/math_strength.py --explain               this text

What counts as use. A card is USED when an agent opens it on purpose: math_recall.py --id <card>, math_walk.py <card>, or
math_note.py <card>. A card is SEEN when it is printed as a search hit (math_recall.py "<words>", math_walk.py --words).
Two cards are USED TOGETHER when, in one session, one is used and the other was used or seen in the 30 minutes before;
when the earlier card was only seen, the two must be linked and the later card must not have been a hit itself (the
agent followed the link from a hit). Two hits of the same search are never a pair: the words put them side by side,
not the agent. A pair counts once per session per day.

The strength of a pair, between 0 and 1, from the session-days in which it was used together:
    mass     = similarity x sum over those session-days of (1 + days since)^-0.5
               (power-law forgetting: a co-use today counts 1, a week ago 0.35, a month ago 0.18)
    strength = (session-days together / sqrt(session-days using the first x session-days using the second))
               x mass / (mass + 1)
The first factor keeps a card used everywhere (a hub) from tying itself to everything. similarity runs from 0.25 to 1
with the meaning of the two cards' texts (the store's bge-small claim vectors): cosine at or below 0.67, the median of
random pairs, gives 0.25; 0.83 (the upper quartile of linked pairs) or more gives 1; a card with no vector (a source,
an evidence card) gives 0.5. So similar cards used together grow fast, unrelated ones slowly. Fresh and used only
together: one session-day gives at most 0.5, three 0.75, fourteen 0.93.
Two cards with no link of their own that two or more separate sessions used together, at strength 0.25 or more, get
a LEARNED link: printed as "used together", never as a mathematical relation. Separate sessions, not separate days:
the subagents of one Claude session share its session id, and parallel subagents on unrelated topics interleave their
uses, so one session, however long and however many days it spans, is one piece of evidence for a learned link.

A printed lead earns nothing. When a tool prints a learned partner ("used together") and the agent then opens it, that
step adds nothing to the pair for the rest of that session-day: the printout suggested it, so it is not new evidence.
Without this rule a lead, once printed, would feed itself. (Links of their own grow from followed steps as usual.)

What a strength is not. It says agents found the two cards useful side by side. It does not say one proves, uses or
implies the other, and it never changes a link's kind or sign: a strong link into a conjecture does not make the
conjecture settled. Read the cards.

Automatic. math_recall.py, math_walk.py and math_note.py append the ids they print or open to
~/.claude/brain_graph/live/math_couse.jsonl (card ids, session id, time; never the words of a query). Every reader
folds whatever the log gained since the last reader into the state (math_couse_state.json beside it), so a strength
moves on the next call after the use. The log is brain data and is never deleted.

Never logged: a call whose working folder is under pd_work, primary_directive or a frozen copy of the brain, or has a
folder named test*/bench*; a call made with CLAUDE_SESSION_ID set (evaluations label their runs with it); a call on
any store but the live one; a call with no session id (CLAUDE_CODE_SESSION_ID or CODEX_THREAD_ID); a session the
shared classifier has settled as a test worker; anything run with MATH_STRENGTH_NOLOG=1. Subagents of one Claude
session share its session id, so their uses pool as one session.
MATH_STRENGTH_OFF=1 makes the tools ignore strengths (printout and spreading exactly as before).
MATH_BRAIN_HOW_IT_WORKS.md beside this file explains the store, its links, spreading and strengths in one page.
"""
import hashlib
import json
import math
import os
import re
import sys
import time

HOME = os.path.expanduser("~")
LIVE = os.path.join(HOME, ".claude", "brain_graph", "live")
LOG = os.environ.get("MATH_STRENGTH_LOG") or os.path.join(LIVE, "math_couse.jsonl")
STATE = os.environ.get("MATH_STRENGTH_STATE") or (os.path.splitext(LOG)[0] + "_state.json")
FETCHES = os.environ.get("MATH_STRENGTH_FETCHES") or os.path.join(HOME, ".claude", "dreaming", "mathbrain_fetches.tsv")
KINDS_CACHE = os.environ.get("MATH_STRENGTH_KINDS") or os.path.join(LIVE, "session_kinds.json")
LIVE_STORE = os.environ.get("MATHBRAIN_STORE", "")  # deployment-specific: point MATHBRAIN_STORE at your population db
VECS = os.environ.get("MATH_BRAIN_CLAIM_VECS") or os.path.join(os.path.dirname(LIVE_STORE), "claim_embeddings.npz")

WINDOW = 30 * 60.0            # seconds: "used at similar times"
DAY = 86400.0
COS_LO, COS_HI = 0.67, 0.83   # random-pair median, linked-pair upper quartile (12 Oct 2026: 17,010 links, 20,000 random)
SIM_FLOOR = 0.25
LEARN_SESSIONS = 2
LEARN_STRENGTH = 0.25
LEARNED_WEIGHT = 0.5          # a learned link spreads like a vocabulary link of weight 0.5 x strength (a lead, not a relation)
CALL_PAIRS = 12               # cards opened in one call pair with each other up to this many
LEAD_CAP = 60                 # learned leads one call may record as printed
VERSION = 3                   # 3: learned links need separate sessions; printed leads earn nothing
SKIP_PARTS = ("pd_work", "primary_directive", "frozen_math_brain")
# a real session id (a Claude session or a Codex thread) is a UUID; math_note.py writes CLAUDE_SESSION_ID first, which
# only an evaluation sets ("proofbanks-<stamp>-<item>", "ownvoice-..."), so a fetch row whose session is not a UUID is
# an evaluation's and is left out
SESSION_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _now():
    return float(os.environ.get("MATH_STRENGTH_NOW") or time.time())


def _norm(p):
    return os.path.normcase(os.path.abspath(p or "")).replace("/", os.sep)


# ---------------------------------------------------------------- writing (the tools call log_use)

def session_id():
    return (os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CODEX_THREAD_ID") or "").strip()


def not_logged_because(store_path=None):
    """None when this call may be logged, else the reason it may not."""
    if os.environ.get("MATH_STRENGTH_NOLOG"):
        return "MATH_STRENGTH_NOLOG"
    if os.environ.get("CLAUDE_SESSION_ID"):
        return "evaluation label CLAUDE_SESSION_ID"
    if store_path and _norm(store_path) != _norm(LIVE_STORE) and not os.environ.get("MATH_STRENGTH_LOG"):
        return "not the live store"
    try:
        cwd = _norm(os.getcwd()).lower()
    except OSError:
        cwd = ""
    parts = [x for x in cwd.split("\\") if x]
    if any(x.startswith(s) for s in SKIP_PARTS for x in parts):
        return "test or work folder"
    if any(x.startswith("test") or x.startswith("bench") for x in parts):
        return "test folder"
    mesh = (os.environ.get("AGENT_MESH_ID") or "").lower()
    if mesh.startswith("codex-br") or "bench" in mesh or "test" in mesh:
        return "test agent"
    if not session_id():
        return "no session id"
    return None


def log_use(tool, use=(), seen=(), store_path=None, lead=()):
    """Append one use record (card ids only). lead: (card, partner) pairs the call printed as learned partners.
    Never raises; returns True when written."""
    try:
        use = list(dict.fromkeys(str(x) for x in use if x))[:40]
        seen = [x for x in dict.fromkeys(str(x) for x in seen if x) if x not in use][:40]
        if not use and not seen:
            return False
        if not_logged_because(store_path):
            return False
        lead = list(dict.fromkeys(_key(str(a), str(b)) for a, b in lead if a and b and a != b))[:LEAD_CAP]
        rec = {"t": round(time.time(), 1), "sid": session_id()[:64], "tool": tool}
        if use:
            rec["use"] = use
        if seen:
            rec["seen"] = seen
        if lead:
            rec["lead"] = lead
        d = os.path.dirname(LOG)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- the model

def similarity_from_cos(c):
    if c is None:
        return 0.5
    x = (c - COS_LO) / (COS_HI - COS_LO)
    return round(SIM_FLOOR + (1.0 - SIM_FLOOR) * min(1.0, max(0.0, x)), 3)


def _key(a, b):
    return (a + "|" + b) if a < b else (b + "|" + a)


def _skey(sid):
    return hashlib.sha1(sid.lower().encode("utf-8", "replace")).hexdigest()[:12]


def _day(t):
    return time.strftime("%Y%m%d", time.localtime(t))


def _empty():
    return {"version": VERSION, "log": LOG, "offset": 0, "fetch_offset": 0, "built": 0.0, "events": 0,
            "skipped": {}, "cards": {}, "pairs": {}, "recent": {}}


def _episode(rec, sk, t):
    """Count (session, day) once in rec; returns True when this is a new session-day for it."""
    d = _day(t)
    if rec.get("day") != d:
        rec["day"], rec["today"] = d, []
    if sk in rec["today"]:
        return False
    rec["today"].append(sk)
    rec["n"] = rec.get("n", 0) + 1
    return True


class Strengths:
    """Read side. Load once per process: Strengths.load(linked=fn) where fn(a, b) says whether two cards share a
    typed link (needed only to judge a seen-then-used step as a followed link)."""

    def __init__(self, st, linked=None):
        self.st = st
        self.linked = linked
        self._by_card = None
        self._smap = None

    @classmethod
    def load(cls, linked=None, save=True):
        st = None
        try:
            with open(STATE, encoding="utf-8") as fh:
                st = json.load(fh)
            if st.get("version") != VERSION or _norm(st.get("log")) != _norm(LOG):
                st = None
        except Exception:
            st = None
        self = cls(st or _empty(), linked)
        try:
            if self._fold() and save:
                self._save()
        except Exception:
            pass
        return self

    def _kinds(self):
        if not hasattr(self, "_kc"):
            try:
                with open(KINDS_CACHE, encoding="utf-8") as fh:
                    self._kc = json.load(fh)
            except Exception:
                self._kc = {}
        return self._kc

    def _tail(self, path, key):
        """Complete new lines of a file since the stored offset (a half-written last line waits for the next reader)."""
        try:
            size = os.path.getsize(path)
        except OSError:
            return []
        off = self.st.get(key, 0)
        if size < off:                      # the file was replaced: read it again from the start
            off = 0
        if size <= off:
            self.st[key] = off
            return []
        with open(path, "rb") as fh:
            fh.seek(off)
            chunk = fh.read(size - off)
        end = chunk.rfind(b"\n")
        if end < 0:
            self.st[key] = off
            return []
        self.st[key] = off + end + 1
        return chunk[:end + 1].decode("utf-8", "replace").splitlines()

    def _fold(self):
        """Fold new log lines (and new fetch rows) into the state. Returns True when anything was read."""
        st = self.st
        try:
            if os.path.getsize(LOG) < st["offset"]:      # the log itself was replaced: start the state again
                self.st = st = _empty()
        except OSError:
            pass
        recs = []
        for line in self._tail(LOG, "offset"):
            try:
                r = json.loads(line)
                recs.append((float(r["t"]), str(r.get("sid") or ""), [str(x) for x in r.get("use") or []],
                             [str(x) for x in r.get("seen") or []], [str(x) for x in r.get("lead") or []]))
            except Exception:
                st["skipped"]["bad line"] = st["skipped"].get("bad line", 0) + 1
        for line in self._tail(FETCHES, "fetch_offset"):   # math_note.py reads: epoch, session, card, outcome
            p = line.split("\t")
            if len(p) >= 4 and p[1].strip() and p[2].strip() and p[3].strip() == "ok":
                if not SESSION_RE.match(p[1].strip()):
                    st["skipped"]["fetch not from a session"] = st["skipped"].get("fetch not from a session", 0) + 1
                    continue
                try:
                    recs.append((float(p[0]), p[1].strip(), [p[2].strip()], [], []))
                except ValueError:
                    pass
        if not recs:
            return False
        recs.sort(key=lambda r: r[0])
        kinds = self._kinds()
        new_pairs = []
        leads_by = st.setdefault("leads", {})
        for t, sid, use, seen, lead in recs:
            if not sid:
                st["skipped"]["no session"] = st["skipped"].get("no session", 0) + 1
                continue
            if kinds.get(sid.lower()) == "test":
                st["skipped"]["test session"] = st["skipped"].get("test session", 0) + 1
                continue
            st["events"] += 1
            sk = _skey(sid)
            day = _day(t)
            ld = leads_by.get(sk)
            if ld is None or ld["day"] != day:
                ld = leads_by[sk] = {"day": day, "keys": []}
            printed = set(ld["keys"])     # learned leads this session was shown today, before this call
            ctx = [e for e in st["recent"].get(sk, []) if e[0] >= t - WINDOW]
            was_hit = {x for (_t0, x, how) in ctx if how == "s"}
            pairs = set()
            for i, y in enumerate(use):
                for (_t0, x, how) in ctx:
                    # a hit x pairs with y only when y was reached along x's link, not picked as a hit itself
                    if x != y and (how == "u" or (y not in was_hit and self.linked is not None and self.linked(x, y))):
                        pairs.add(_key(x, y))
                if i < CALL_PAIRS:
                    for x in use[:i]:
                        if x != y:
                            pairs.add(_key(x, y))
            followed = pairs & printed
            if followed:                                # the printout suggested these steps: no evidence, and no cost
                st["skipped"]["followed a printed lead"] = st["skipped"].get("followed a printed lead", 0) + len(followed)
                pairs -= followed
            # a card opened after a lead named it, with no other pair here, is the lead being followed: its use is not
            # counted either, or every follow would dilute the pair (more uses of each card, none together)
            lead_only = set()
            if printed:
                named = {c for k in printed for c in k.split("|")}
                ends = {c for k in pairs for c in k.split("|")}
                lead_only = {y for y in use if y in named and y not in ends}
            for k in pairs:
                p = st["pairs"].get(k)
                if p is None:
                    p = st["pairs"][k] = {"n": 0, "ts": [], "sim": None, "ss": []}
                    new_pairs.append(k)
                if _episode(p, sk, t):
                    p["ts"].append(round(t, 1))
                elif p["ts"]:
                    p["ts"][-1] = round(t, 1)          # same session-day: keep its latest time, count it once
                ss = p.setdefault("ss", [])
                if sk not in ss and len(ss) < LEARN_SESSIONS:   # separate sessions, up to the number a learned link needs
                    ss.append(sk)
                for c in k.split("|"):
                    self._card_use(c, sk, t)
            for y in use:
                if y not in lead_only:       # a card opened only along a printed lead: its use would only dilute the pair
                    self._card_use(y, sk, t)
            ctx.extend([[t, y, "u"] for y in use] + [[t, x, "s"] for x in seen if x not in use])
            st["recent"][sk] = ctx[-80:]
            if lead:
                ld["keys"] = list(dict.fromkeys(ld["keys"] + lead))[-400:]
        last = recs[-1][0]
        st["recent"] = {k: v for k, v in st["recent"].items() if v and v[-1][0] >= last - WINDOW}
        st["leads"] = {k: v for k, v in leads_by.items() if v["keys"] and v["day"] == _day(last)}
        if new_pairs:
            self._fill_sims(new_pairs)
        st["built"] = time.time()
        self._by_card = None
        self._smap = None
        return True

    def _card_use(self, c, sk, t):
        cd = self.st["cards"].get(c)
        if cd is None:
            cd = self.st["cards"][c] = {"n": 0, "last": 0.0}
        _episode(cd, sk, t)
        cd["last"] = max(cd["last"], round(t, 1))

    def _fill_sims(self, keys):
        try:
            import numpy as np
            want = {c for k in keys for c in k.split("|")}
            with np.load(VECS, allow_pickle=False) as f:
                ids = [str(x) for x in f["slugs"]]
                ix = {s: i for i, s in enumerate(ids) if s in want}
                rows = sorted(set(ix.values()))
                sub = np.asarray(f["vecs"][rows], dtype=np.float32) if rows else None
            pos = {r: j for j, r in enumerate(rows)}
            if sub is not None:
                sub /= np.maximum(np.linalg.norm(sub, axis=1, keepdims=True), 1e-9)
            for k in keys:
                a, b = k.split("|")
                c = float(sub[pos[ix[a]]] @ sub[pos[ix[b]]]) if (a in ix and b in ix) else None
                self.st["pairs"][k]["sim"] = similarity_from_cos(c)
        except Exception:
            for k in keys:
                if self.st["pairs"][k].get("sim") is None:
                    self.st["pairs"][k]["sim"] = similarity_from_cos(None)

    def _save(self):
        lock = STATE + ".lock"
        try:
            if os.path.exists(lock) and time.time() - os.path.getmtime(lock) > 120:
                os.remove(lock)
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError:
            return False
        try:
            os.close(fd)
            tmp = STATE + ".tmp%d" % os.getpid()
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.st, fh, separators=(",", ":"))
            try:
                os.replace(tmp, STATE)
            except OSError:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                return False
            return True
        finally:
            try:
                os.remove(lock)
            except OSError:
                pass

    # ---- reading ----
    def empty(self):
        return not self.st["pairs"]

    def pair(self, a, b, now=None):
        """{"n", "last", "strength", "sim"} for a pair used together at least once, else None."""
        p = self.st["pairs"].get(_key(a, b))
        if not p or not p.get("n"):
            return None
        now = now or _now()
        n = p["n"]
        ca, cb = self.st["cards"].get(a), self.st["cards"].get(b)
        na = ca["n"] if ca else n
        nb = cb["n"] if cb else n
        spec = min(1.0, n / math.sqrt(max(1, na) * max(1, nb)))
        sim = p.get("sim") or 0.5
        mass = sim * sum((1.0 + max(0.0, now - t) / DAY) ** -0.5 for t in p["ts"])
        return {"n": n, "last": max(p["ts"]), "strength": round(spec * mass / (mass + 1.0), 2), "sim": sim,
                "sessions": len(p.get("ss") or ())}

    def strength(self, a, b):
        p = self.pair(a, b)
        return p["strength"] if p else 0.0

    def factor(self, a, b):
        """Multiplier on a typed link's spreading weight: 1 + strength (exactly 1 when never used together)."""
        return 1.0 + self.strength_map().get(a, {}).get(b, 0.0)

    def strength_map(self):
        """{card: {other: strength}} for every pair whose strength is above zero, both ends (built once per load, so
        a tool can scale a million links by touching only the few that agents used)."""
        if self._smap is None:
            m = {}
            for k in self.st["pairs"]:
                a, b = k.split("|")
                s = self.strength(a, b)
                if s > 0:
                    m.setdefault(a, {})[b] = s
                    m.setdefault(b, {})[a] = s
            self._smap = m
        return self._smap

    def partners(self, a, now=None):
        """[(other, pair info)] for every card used together with a, strongest first."""
        if self._by_card is None:
            by = {}
            for k in self.st["pairs"]:
                x, y = k.split("|")
                by.setdefault(x, []).append(y)
                by.setdefault(y, []).append(x)
            self._by_card = by
        out = []
        for o in self._by_card.get(a, ()):
            info = self.pair(a, o, now)
            if info:
                out.append((o, info))
        out.sort(key=lambda x: (-x[1]["strength"], -x[1]["n"], x[0]))
        return out

    def learned(self, a, typed_linked):
        """Partners of a with no typed link to it that pass the bar: two or more separate sessions, strength 0.25 or more."""
        return [(o, i) for o, i in self.partners(a)
                if not typed_linked(a, o) and i["sessions"] >= LEARN_SESSIONS and i["strength"] >= LEARN_STRENGTH]

    def learned_all(self, typed_linked, now=None):
        """{card: [(other, pair info)]} for every learned link, both ends, strongest first."""
        out = {}
        for k, p in self.st["pairs"].items():
            if len(p.get("ss") or ()) < LEARN_SESSIONS:
                continue
            a, b = k.split("|")
            if typed_linked(a, b):
                continue
            info = self.pair(a, b, now)
            if info and info["strength"] >= LEARN_STRENGTH:
                out.setdefault(a, []).append((b, info))
                out.setdefault(b, []).append((a, info))
        for v in out.values():
            v.sort(key=lambda x: (-x[1]["strength"], -x[1]["n"], x[0]))
        return out

    def card_use(self, a):
        cd = self.st["cards"].get(a)
        return {"sessions": cd["n"], "last": cd["last"]} if cd else None

    def stats(self):
        st = self.st
        return {"log": LOG, "state": STATE, "events": st["events"], "cards_used": len(st["cards"]),
                "pairs": len(st["pairs"]),
                "pairs_on_2plus_session_days": sum(1 for p in st["pairs"].values() if p.get("n", 0) >= LEARN_SESSIONS),
                "pairs_from_2plus_sessions": sum(1 for p in st["pairs"].values() if len(p.get("ss") or ()) >= LEARN_SESSIONS),
                "skipped": st["skipped"],
                "built": time.strftime("%Y-%m-%d %H:%M", time.localtime(st["built"])) if st["built"] else None}


# ---------------------------------------------------------------- printing

def ago(t, now=None):
    d = max(0.0, (now or _now()) - t)
    if d < 3600:
        return "%d min ago" % max(1, round(d / 60))
    if d < 48 * 3600:
        return "%d h ago" % round(d / 3600)
    return "%d days ago" % round(d / DAY)


def tag(info, now=None):
    """The words a tool prints beside a link: '[strength 0.62: used together 3 times, last 2 h ago]'."""
    if not info:
        return ""
    return "[strength %.2f: used together %s, last %s]" % (
        info["strength"], "once" if info["n"] == 1 else "%d times" % info["n"], ago(info["last"], now))


def disabled():
    return bool(os.environ.get("MATH_STRENGTH_OFF"))


def for_tools(linked=None):
    """What the tools call: None when strengths are off or empty (so their printout stays exactly as before)."""
    if disabled():
        return None
    try:
        s = Strengths.load(linked=linked)
        return None if s.empty() else s
    except Exception:
        return None


# ---------------------------------------------------------------- CLI

def _store_maps(store_path):
    """Titles, a typed-link test and the kinds between pairs, from the store (read-only)."""
    sys.path.insert(0, os.environ.get("MATHBRAIN_BUILD_SRC", ""))
    from math_brain.store import ResearchStore
    with ResearchStore.open_read_only(store_path) as st:
        nodes = list(st.iter_nodes())
        edges = list(st.iter_edges(skip_unknown=True))
    title = {n.id: " ".join((n.title or "").split()) for n in nodes}
    kinds = {}
    for e in edges:
        k = str(getattr(e.kind, "value", e.kind))
        if k != "supersedes":
            kinds.setdefault(_key(e.source_id, e.target_id), []).append((e.source_id, k, e.target_id))
    return title, (lambda a, b: _key(a, b) in kinds), kinds


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import argparse
    ap = argparse.ArgumentParser(description="Link strengths grown from use in the math store (the store is read-only).")
    ap.add_argument("ids", nargs="*", help="two card ids: how strongly they are tied")
    ap.add_argument("--card", default=None, help="the cards most used together with this one")
    ap.add_argument("--top", type=int, nargs="?", const=20, default=None, help="the strongest pairs")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--explain", action="store_true", help="how strengths are made and what they mean")
    ap.add_argument("--store", default=os.environ.get("MATHBRAIN_STORE", LIVE_STORE))
    a = ap.parse_args(argv)
    if a.explain:
        print(__doc__)
        return 0
    title, linked, kinds = _store_maps(a.store)
    S = Strengths.load(linked=linked)

    def name(i):
        return '"%s" (%s)' % ((title.get(i) or i)[:80], i)

    def resolve(w):
        w = w.strip().strip(",;.()[]")
        if w in title:
            return w
        hits = [i for i in title if i.startswith(w)] if len(w) >= 6 else []
        return hits[0] if len(hits) == 1 else None

    def kinds_of(x, y):
        return ", ".join(sorted({k for (_s, k, _t) in kinds.get(_key(x, y), [])}))

    def learned_word(info):
        return "a learned link" if info["sessions"] >= LEARN_SESSIONS and info["strength"] >= LEARN_STRENGTH else "no link"

    if a.stats or not (a.ids or a.card or a.top is not None):
        print(json.dumps(S.stats(), indent=1))
        return 0
    if a.top is not None:
        rows = []
        for k in S.st["pairs"]:
            x, y = k.split("|")
            info = S.pair(x, y)
            if info:
                rows.append((info["strength"], x, y, info))
        rows.sort(key=lambda r: (-r[0], r[1], r[2]))
        print("strongest pairs, from use (a strength is not a mathematical relation): %d pairs in all" % len(rows))
        for s, x, y, info in rows[:a.top]:
            print("  %s  <->  %s\n      %s; %s" % (name(x), name(y), tag(info), kinds_of(x, y) or learned_word(info)))
        return 0
    if a.card:
        c = resolve(a.card)
        if not c:
            print("no card with id %s" % a.card)
            return 1
        cu = S.card_use(c)
        print("%s: %s" % (name(c), ("used on %d session-day(s), last %s" % (cu["sessions"], ago(cu["last"]))) if cu
                          else "no recorded use yet"))
        rows = S.partners(c)
        if not rows:
            print("  nothing used together with it yet")
        for o, info in rows[:30]:
            print("  %s %s; %s" % (tag(info), name(o), kinds_of(c, o) or learned_word(info)))
        return 0
    if len(a.ids) != 2:
        ap.error("give two card ids, or --card, --top, --stats, --explain")
    x, y = resolve(a.ids[0]), resolve(a.ids[1])
    if not x or not y:
        print("no card with id %s" % (a.ids[0] if not x else a.ids[1]))
        return 1
    print("%s\n%s" % (name(x), name(y)))
    ty = kinds.get(_key(x, y), [])
    for s_, k, t_ in ty:
        print("  typed link: %s %s %s" % ("first" if s_ == x else "second", k, "second" if t_ == y else "first"))
    if not ty:
        print("  no typed link between them")
    info = S.pair(x, y)
    if not info:
        print("  never used together in a logged session")
        return 0
    print("  %s; similarity weight %.2f (the meaning of the two texts)" % (tag(info), info["sim"]))
    print("  %s" % ("spreading along the typed link is multiplied by %.2f" % (1 + info["strength"]) if ty else
                    learned_word(info) + (" (two or more separate sessions and strength 0.25 make one; %d so far)" % info["sessions"]
                                          if learned_word(info) == "no link" else "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
