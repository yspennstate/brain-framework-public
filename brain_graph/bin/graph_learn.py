"""graph_learn.py - the live brain graph learning from what its lines did (26 Sep, claude-825b1305).

Runs after each hourly capture (run_graph.py capture calls it; also by hand). Reads the membrane's evaluations
(live/fires.jsonl), the capture's newly appended events and brain.py recall's log (live/recall_log.jsonl), and for every
evaluation whose 30-minute outcome window the capture has fully seen:

  judged   a served memory is taken up when the session reads, recalls or edits it within 30 minutes; an error line is
           borne out when the error follows (a warning that prevented it reads as a miss, so its fix memory's uptake is
           the cleaner number); a place line when the session first touches that folder or project
  learned  every served line moves the edges that lit it: taken up -> pos + 1 on (item -> target), ignored -> neg + 1.
           The membrane multiplies those edges by ((pos + p0 m) / (pos + neg + m)) / p0, so edges that keep bringing in
           what agents use grow, and edges that keep being ignored fade (learned.sqlite; survives store rebuilds)
  tired    a memory served four or more times in 24 hours and never taken up is left out for three days
  bar      the serving threshold moves toward the target precision: up 15 per cent when the last 40 judged memory
           lines fall below it, down 10 per cent when they are well above it and the lane fires on under 10 per cent
           of evaluations (bounds 10..5000)
  recall   brain.py recall's graph weight moves one step (0.05) toward the weight under which the first memory an agent
           opened within ten minutes of a recall ranked higher (mean reciprocal rank over the last 200 such recalls from
           agents at work - interactive sessions and scripts other than test workers; a headless worker whose first prompt
           starts '[headless-worker]' is a test bank and is counted but not learned from, 27 Sep); it never goes below
           0.05, his order of 26 Sep 15:5x ("a weight of not being zero"), nor above 0.4
Writes live/precision.json (what the hook's hint lines quote), live/LIVE_STATUS.json and a line of live/LEARNING_LOG.md.

    python graph_learn.py [--dry]
"""
import argparse
import collections
import hashlib
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import session_kinds  # noqa: E402
HOME = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph")
LIVE = os.path.join(HOME, "live")
EVENTS = os.path.join(HOME, "events_touch.jsonl")
FIRES = os.path.join(LIVE, "fires.jsonl")
RECALL_LOG = os.path.join(LIVE, "recall_log.jsonl")
LEARNED = os.path.join(LIVE, "learned.sqlite")
POLICY = os.path.join(LIVE, "policy.json")
PRECISION = os.path.join(LIVE, "precision.json")
STATUS = os.path.join(LIVE, "LIVE_STATUS.json")
STATE = os.path.join(LIVE, "learn_state.json")
LOGMD = os.path.join(LIVE, "LEARNING_LOG.md")
EMITTED = os.path.join(LIVE, "emitted.jsonl")
DIAL = os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "brain_graph_dial.json")
HORIZON = 1800.0
# the diffuse-line trial (owner, 6 Oct 2026): memory and place lines shown or held half and half from this moment
# (13:54 his clock; 13:38-13:54 ran a one-in-four hold and is left out so the two arms share one split)
TRIAL_START = 1791284081.0


TRIAL_COHORT_STATUS = [""]


def _trial_cohort(judged, write=True):
    """The diffuse-line trial's cohort: the FIRST exposure (shown or held) of each (session, channel, key) since
    TRIAL_START, persisted in live/trial_cohort.json across runs (dot D7CB6565's reviews, 6 Oct 2026). Each record keeps
    its evaluation id (eid): an unknown outcome is filled in only by the same evaluation, never by another one that
    shares a rounded timestamp. An earlier exposure that arrives in a later batch replaces a later first. A cohort file
    that exists but cannot be read is left untouched and the trial counts are withheld (TRIAL_COHORT_STATUS
    'unreadable'), never rebuilt from the rolling judged window. write=False (graph_learn --dry) persists nothing."""
    path = os.path.join(LIVE, "trial_cohort.json")
    TRIAL_COHORT_STATUS[0] = "ok"
    c = {"since": TRIAL_START, "first": {}}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                c = json.load(f)
            if not isinstance(c, dict) or not isinstance(c.get("first"), dict):
                raise ValueError("cohort file has no 'first' map")
            if c.get("since") != TRIAL_START:
                c = {"since": TRIAL_START, "first": {}}   # a new trial start begins a new cohort
        except Exception:
            TRIAL_COHORT_STATUS[0] = "unreadable"
            return {}
    first = c["first"]
    for j in sorted(judged, key=lambda x: x.get("t") or 0):
        t = j.get("t") or 0
        if t < TRIAL_START:
            continue
        for ln in j.get("lines") or []:
            if ln[0] not in ("mem", "held_mem", "place", "held_place"):
                continue
            k = str(ln[1]).lower()
            k = k.split(":", 1)[1] if k.startswith(("mem:", "place:")) else k
            uid = "%s\t%s\t%s" % (j.get("sid"), "mem" if ln[0] in ("mem", "held_mem") else "place", k)
            rec = {"t": t, "eid": j.get("eid"), "kind": ln[0], "sid": j.get("sid"), "ok": ln[2],
                   "identity": "eid" if j.get("eid") is not None else "none"}
            have = first.get(uid)
            if have is None or t < (have.get("t") or 0):
                first[uid] = rec
                continue
            same = have.get("eid") is not None and have.get("eid") == j.get("eid")
            if not same and have.get("eid") is None and have.get("t") == t and have.get("kind") == ln[0]:
                # a record without an evaluation id, met again at the same second: the match rests on the timestamp
                # alone, so it is filled but never counted as identity-verified (round 6 review); an eid-bearing
                # judgment hands the record its id, still marked as migrated by timestamp
                same = True
                if j.get("eid") is not None:
                    have["eid"], have["identity"] = j.get("eid"), "migrated_by_timestamp"
                else:
                    have["identity"] = "timestamp"
            if same and have.get("ok") is None and ln[2] is not None and have.get("kind") == ln[0]:
                have["ok"] = ln[2]
    if write:
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(c, f)
            os.replace(tmp, path)
        except Exception:
            TRIAL_COHORT_STATUS[0] = "write_failed"
    return first
OUT_KINDS = ("mem", "memw", "recalled", "err", "dir", "proj", "file", "verdict")
TARGET_P = 0.25
LAMBDAS = (0.05, 0.1, 0.15, 0.2, 0.3, 0.4)
# the weight moves only on searches from at least this many sessions, each session one vote (30 Sep, the 1000-page
# report's D03-T02: until then the last 200 searches were averaged whatever their sessions, the judged records kept no
# session to show it, and one busy session could move the weight alone - this file's own control did, on forty
# searches from one session)
MIN_SESSIONS = 10


def jload(p, default):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def jsave(p, obj):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1)
    os.replace(tmp, p)


def read_new(path, offset, parse=json.loads):
    """Lines appended since offset (a torn last line is left for the next run)."""
    out = []
    if not os.path.exists(path):
        return out, offset
    size = os.path.getsize(path)
    if size < offset:                      # rotated or truncated: start over
        offset = 0
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read(size - offset)
    end = data.rfind(b"\n")
    if end < 0:
        return out, offset
    for raw in data[:end].split(b"\n"):
        if not raw.strip():
            continue
        try:
            out.append(parse(raw))
        except Exception:
            continue
    return out, offset + end + 1


def learned_db():
    db = sqlite3.connect(LEARNED, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS learned(src TEXT, dst TEXT, pos REAL, neg REAL, updated REAL, "
               "PRIMARY KEY(src, dst))")
    db.execute("CREATE INDEX IF NOT EXISTS learned_src ON learned(src)")
    # every evaluation learned from, written in the same transaction as its counts (see step 3)
    db.execute("CREATE TABLE IF NOT EXISTS processed(obs TEXT PRIMARY KEY, t REAL)")
    return db


def obs_id(j):
    """One membrane evaluation's identity: the eid the membrane gives each record since 29 Sep, else (older records) its
    time, session, event and entry point. Those four made two evaluations one when they shared them: the same session
    within a tenth of a second, or two sessions whose ids begin alike (Codex ids begin with the clock), and the second
    one's lessons were dropped as a replay (account03/B08 auto71, B08-R62-OBS-ID-SAME-SESSION-TENTH-COLLISION-032, -031)."""
    if j.get("eid"):
        return hashlib.sha1(json.dumps(["eid", j["eid"]]).encode("utf-8")).hexdigest()
    return hashlib.sha1(json.dumps([j.get("t"), j.get("sid"), j.get("ev"), j.get("ep", "")]).encode("utf-8")).hexdigest()


def fire_obs(f):
    """obs_id of the judged row a fire record becomes (see the step-2 loop)."""
    return obs_id({"t": f.get("t"), "sid": str(f.get("sid", ""))[:8].lower(), "ev": f.get("ev"), "ep": f.get("ep", ""),
                   "eid": f.get("eid")})


MARK = 1 << 16


def _sampled_mark(path, offset):
    """The mark written before 29 Sep 19:xx: sha256 of the file's first 64 KB and of the 64 KB before offset. Read only to
    carry a cursor written in that form across to the whole-prefix mark, once."""
    try:
        offset = int(offset)
        with open(path, "rb") as fh:
            head = hashlib.sha256(fh.read(min(MARK, offset))).hexdigest()[:16]
            at = max(0, offset - MARK)
            fh.seek(at)
            tail = hashlib.sha256(fh.read(offset - at)).hexdigest()[:16]
        return [head, tail]
    except (OSError, ValueError):
        return None


def log_mark(path, offset):
    """What the bytes under a log offset were: the sha256 of the whole prefix [0, offset), with its length (29 Sep,
    account04/B15 r8, B15-R73-MIDDLE-REWRITE-ESCAPES-CURSOR-BINDING-20260929-01: the first and last 64 KB let a same-size
    rewrite of the middle through, and the rows it changed were skipped). Hashing the whole 590 MB event log takes about
    0.45 s (29 Sep 19:0x), and the learner runs once an hour. None when the file is shorter than offset or unreadable."""
    try:
        offset = int(offset)
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            left = offset
            while left > 0:
                b = fh.read(min(1 << 22, left))
                if not b:
                    return None
                h.update(b)
                left -= len(b)
        return {"sha256": h.hexdigest(), "n": offset}
    except (OSError, ValueError):
        return None


def read_bound(st, key, path, back=None, parse=json.loads):
    """read_new from st[key + '_offset'], trusted only while the bytes under it are the ones it was taken on (29 Sep,
    account01/B07 r77, B07-R77-R62-READ-NEW-OFFSET-NOT-GENERATION-BOUND: a file replaced by one of the same size gave
    nothing, and a larger one lost its first rows, with no trace; account04/B15 r8: the check sampled the first and last
    64 KB, so a rewrite of the middle passed). The mark is the digest of the whole consumed prefix. A file whose prefix
    changed, or that is now shorter than the offset (truncated, replaced, rotated), is read again from the start, or from
    `back` bytes before its end for the event log; the caller drops what it has already seen, and the reason is kept in
    st['reset_reasons'][key]. Returns (rows, reset)."""
    off = st.get(key + "_offset", 0) or 0
    want = st.get(key + "_mark")
    reset, why = False, None
    if off and os.path.exists(path):
        size = os.path.getsize(path)
        if size < off:
            reset, why = True, "shorter than the offset (%d < %d): truncated, replaced or rotated" % (size, off)
        elif want is not None:
            now = _sampled_mark(path, off) if isinstance(want, list) else log_mark(path, off)
            if now != want:
                reset, why = True, "the bytes under the offset changed"
        if reset:
            off = max(0, size - back) if back else 0
    rows, off = read_new(path, off, parse)
    st[key + "_offset"] = off
    st[key + "_mark"] = log_mark(path, off) if off else None
    if reset:
        st.setdefault("reset_reasons", {})[key] = {"t": round(time.time(), 1), "why": why}
    return rows, reset


def emitted_eids():
    """The evaluations whose text the hook printed (live_membrane.mark_emitted)."""
    out = set()
    try:
        with open(EMITTED, encoding="utf-8") as fh:
            for ln in fh:
                try:
                    out.add(json.loads(ln)["eid"])
                except Exception:
                    continue
    except OSError:
        pass
    return out


LANE_PATH = os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "graph_lane.py")
_LANE = {}
CUT = 30                                   # items of each list the recall log keeps (graph_lane.log_recall)


def _lane():
    """graph_lane, the module recall fuses with, loaded from its file (the learner runs from another folder)."""
    if _LANE.get("path") != LANE_PATH:
        import importlib.util
        spec = importlib.util.spec_from_file_location("graph_lane_for_learner", LANE_PATH)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _LANE.update(path=LANE_PATH, m=m)
    return _LANE["m"]


def fuse(body, graph, lam, bounds=None):
    """The order recall would have shown at graph weight lam: graph_lane.fuse_scores, the function recall runs (29 Sep,
    interactive audit G03: this kept its own copy, which ordered equal scores by hash, differently from one process to
    the next). `bounds` (lo, hi, glo, ghi) are the whole lists' minimum and maximum as recall_log.jsonl records them
    since 27 Sep: the log keeps 30 items of each list, and fusion scales by the whole list's range, so without them a
    replay reorders the top 10 of about 2 recalls in 5."""
    if lam <= 0 or not body or not graph:
        return [s for s, _ in body]
    return [s for s, _ in _lane().fuse_scores(body, graph, lam, bounds=bounds)]


def replay_complete(body, graph, lam, bounds, target, n=None):
    """Whether target's replayed rank at weight lam is the rank recall would have given over the whole lists (29 Sep,
    interactive audit G02: a candidate cut from both logged lists can win once lam mixes them, and the replay never sees
    it). A list of CUT items may have been cut, and an item cut from it scored at most its last kept score. So each
    candidate's true score lies between its replayed score and that plus what it could have had in a cut list it is not
    kept in, and a candidate kept in neither list lies between 0 and the sum of both. The rank is exact when every other
    candidate's range lies wholly above or wholly below target's (two exact scores compare exactly, equal ones by name).
    n: the whole lists' lengths, which the log records since 29 Sep; without it a list of CUT or more items counts as cut."""
    body_cut = (int(n[0]) > len(body)) if n else len(body) >= CUT
    graph_cut = (int(n[1]) > len(graph)) if n else len(graph) >= CUT
    if lam <= 0 or not body or not graph:
        return target in {s for s, _ in body} or not body_cut
    if bounds:
        lo, hi, glo, ghi = (float(x) for x in bounds)
    else:
        hi, lo = max(b for _, b in body), min(b for _, b in body)
        ghi, glo = max(v for _, v in graph), min(v for _, v in graph)
    b_in, g_in = {s for s, _ in body}, {s for s, _ in graph}
    hid_b = (1 - lam) * ((min(b for _, b in body) - lo) / (hi - lo) if hi > lo else 1.0) if body_cut else 0.0
    hid_g = lam * ((min(v for _, v in graph) - glo) / (ghi - glo) if ghi > glo else 1.0) if graph_cut else 0.0

    def hidden(s):
        return (hid_b if body_cut and s not in b_in else 0.0) + (hid_g if graph_cut and s not in g_in else 0.0)

    fs = _lane().fuse_scores(body, graph, lam, bounds=bounds)
    t_lo = dict(fs).get(target)
    if t_lo is None:
        return False
    t_hi = t_lo + hidden(target)
    if hid_b + hid_g >= t_lo:              # a candidate kept in neither list could reach it
        return False
    for s, v in fs:
        if s == target or (t_hi == t_lo and hidden(s) == 0.0):
            continue                       # itself, or two exact scores: the replay orders them as recall did
        if v > t_hi or v + hidden(s) < t_lo:
            continue
        return False
    return True


def search_session(r, by_s):
    """The session a logged search belongs to: the one the record names (brain.py logs it since 27 Sep 02:40), else the
    one whose 'recalled' events carry the shown slugs within 90 s (the legacy join, for older records).

    A record that names its session is never given to another one (28 Sep, interactive audit IA-20260928-01): when the
    named session is not among the loaded transcripts its outcome is unobserved and the search is not judged. Until then
    such a record fell through to the overlap join, and a session that recalled the same memory within 90 s took the
    search and its reads. The overlap join is for records that name no session, and a tie between sessions is no
    answer."""
    b = str(r.get("session") or "")[:8].lower() or None
    if b:
        return b if b in by_s else None
    shown = set(r.get("shown") or [])
    best, best_n, tied = None, 0, False
    for s, xs in by_s.items():
        n = sum(1 for ts, kd, nid in xs if kd == "recalled" and abs(ts - r["t"]) < 90 and nid.lower() in shown)
        if n > best_n:
            best, best_n, tied = s, n, False
        elif n and n == best_n:
            tied = True
    return None if tied else best


def judge_searches(recs, by_s):
    """[(record, session or None, memories opened in order)] for logged searches: the session by search_session, the
    window from the search to ten minutes later or the same session's next search, whichever comes first (audit
    account01/B05: two searches in one session must not share one window), each memory once, the first opened first -
    the fixed target. The learner and the offline evaluators both call this, so their labels cannot drift apart again
    (audits account03/B01 R1 and account04/B18 R6, 27 Sep: an evaluator kept the old any-open, shown-overlap rule).
    by_s: {8-character session: [(time, event kind, node id)]}."""
    sess = [search_session(r, by_s) for r in recs]
    next_t = {}
    for s in set(x for x in sess if x):
        ts_s = sorted(r["t"] for r, x in zip(recs, sess) if x == s)
        for a_t, b_t in zip(ts_s, ts_s[1:]):
            next_t[(s, a_t)] = b_t
    out = []
    for r, best in zip(recs, sess):
        opened = []
        if best:
            end = min(r["t"] + 600, next_t.get((best, r["t"]), r["t"] + 600))
            for ts, kd, nid in sorted(by_s[best]):
                if kd == "mem" and r["t"] < ts <= end and nid.lower() not in opened:
                    opened.append(nid.lower())
        out.append((r, best, opened))
    return out


PROJECTS = os.path.join(os.path.expanduser("~"), ".claude", "projects")


def transcript_index(max_age_days=3.0):
    """{first 8 characters of a session id: its transcript} over the Claude project folders touched lately."""
    idx = {}
    cutoff = time.time() - max_age_days * 86400
    try:
        dirs = [os.path.join(PROJECTS, d) for d in os.listdir(PROJECTS)]
    except OSError:
        return idx
    for d in dirs:
        try:
            if not os.path.isdir(d) or os.path.getmtime(d) < cutoff:
                continue
            for name in os.listdir(d):
                if name.endswith(".jsonl"):
                    idx.setdefault(name[:8].lower(), os.path.join(d, name))
        except OSError:
            continue
    return idx


def session_kind(sid, idx=None, cache=None):
    """'his' for an interactive session, 'test' for a headless test worker (its first prompt starts '[headless-worker]',
    the recall benches' workers), 'script' for any other headless run, 'unknown' when no record decides it. Before 27
    Sep the recall log was taken for agents' own searches; 198 of the 201 judged ones of 26-27 Sep were test workers.

    session_kinds.kind, the rule the store and the fast layer use (29 Sep, account03/B01 auto73,
    B01-R9-GRAPH-LEARN-PREFIX-KIND-ORDER-DEPENDENT-20260929). This used to keep its own map of 8-character keys, where the
    first transcript listed won, and a cache in the learner's state that never looked again: under a key two sessions of
    different kinds share it answered 'his' or 'test' by directory order where the store answers 'unknown'. idx and
    cache are ignored, kept so callers that pass them still work."""
    return session_kinds.kind(sid)


def update_mem_bars(pol, judged, fire_rate):
    """The memory bars (step 6), moved by at most one step per batch of 20 memory lines judged since the last move: the
    move records the newest line's time in pol['mem_bar_evidence_until'], and a line older than that never counts again.
    Returns the note, '' when nothing moved. Replaying unchanged evidence moves nothing."""
    since = float(pol.get("mem_bar_evidence_until", 0.0))
    fresh = [(j["t"], ln) for j in sorted(judged, key=lambda j: j["t"]) if j["t"] > since
             for ln in j["lines"] if ln[0] == "mem"]
    recent = fresh[-40:]
    if len(recent) < 20:
        return ""
    p = sum(1 for _t, ln in recent if ln[2]) / len(recent)
    cur = float(pol.get("mem_hebb_min", 55.0))
    cur_s = float(pol.get("mem_size_min", 1.89))
    note = ""
    if p < TARGET_P:                       # fire less: both memory bars up
        pol["mem_hebb_min"] = round(min(5000.0, cur * 1.15), 2)
        pol["mem_size_min"] = round(min(4.0, cur_s + 0.02), 3)
        note = "memory bars up: edge %.1f -> %.1f, size %.3f -> %.3f (%d new lines taken up %.0f%%, target %.0f%%)" % (
            cur, pol["mem_hebb_min"], cur_s, pol["mem_size_min"], len(recent), 100 * p, 100 * TARGET_P)
    elif p > 1.6 * TARGET_P and fire_rate < 0.10:
        pol["mem_hebb_min"] = round(max(10.0, cur * 0.9), 2)
        pol["mem_size_min"] = round(max(0.5, cur_s - 0.02), 3)
        note = "memory bars down: edge %.1f -> %.1f, size %.3f -> %.3f (%d new lines taken up %.0f%%, firing on %.0f%%)" % (
            cur, pol["mem_hebb_min"], cur_s, pol["mem_size_min"], len(recent), 100 * p, 100 * fire_rate)
    if note:
        pol["mem_bar_evidence_until"] = recent[-1][0]
    return note


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    now = time.time()
    os.makedirs(LIVE, exist_ok=True)
    st = jload(STATE, {})
    first = not st
    if first:                              # the lane starts now: only events and fires from here on matter
        st = {"events_offset": os.path.getsize(EVENTS) if os.path.exists(EVENTS) else 0, "fires_offset": 0,
              "recall_offset": 0, "pending": [], "pending_recalls": [], "recent": [], "judged": [], "recall_judged": [],
              "served_log": [], "started": now}
    # 1. the capture's new events, kept 6 hours for the outcome windows
    evs, reset_ev = read_bound(st, "events", EVENTS, back=64 << 20)
    resets = ["events"] if reset_ev else []
    recent = st.get("recent", [])
    newest_ev = max([e["ts"] for e in evs] + [0.0])
    for e in evs:
        if e.get("kind") in OUT_KINDS:
            recent.append([e["ts"], e["session"], e["kind"], e["id"]])
    # an event read twice (a replaced log read again) is one event
    recent = [list(r) for r in dict.fromkeys(tuple(r) for r in recent if r[0] > now - 6 * 3600)]
    st["recent"] = recent
    st["last_event_ts"] = max(st.get("last_event_ts", 0.0), newest_ev)
    by_s = collections.defaultdict(list)
    for ts, s, kd, nid in recent:
        by_s[s].append((ts, kd, nid))
    # which sessions are test workers (27 Sep): the benches' headless workers are judged and reported, never learned from
    st.pop("session_kinds", None)          # the learner's own kinds cache, retired 29 Sep (session_kind)

    def kind_of(sid, ep=""):
        return "his" if ep == "cli" else session_kind(sid)

    # 2. the membrane's new evaluations
    fires, reset_fires = read_bound(st, "fires", FIRES)
    if reset_fires:                        # read again from the start: keep only evaluations not already taken in
        resets.append("fires")
        seen = {fire_obs(f) for f in st["pending"]} | {obs_id(j) for j in st.get("judged", [])}
        if os.path.exists(LEARNED):
            db = sqlite3.connect(LEARNED, timeout=30)
            try:
                seen |= {r[0] for r in db.execute("SELECT obs FROM processed")}
            except sqlite3.Error:
                pass
            db.close()
        fires = [f for f in fires if fire_obs(f) not in seen]
    for f in fires:
        st["pending"].append(f)
    # a window is closed when the capture has seen past its end (the capture lags the transcripts by up to an hour)
    horizon_end = st["last_event_ts"] - 60.0
    emitted = emitted_eids() if any(f.get("eid") for f in st["pending"]) else set()
    n_unemitted = 0
    n_censored = 0
    # ONE OUTCOME, ONE CREDIT (30 Sep, the 1000-page report's D03-T01: the learner must not reward itself for speaking).
    # Each evaluation is judged on its own window, so a line served ten times before the session read it once took ten
    # successes from that one read (a fixture: pos 10). A read now credits only the last serve of the same line before
    # it in that session; an earlier serve of it has no outcome of its own and teaches nothing, neither way. Measured the
    # same day on the live week: no line had been served twice to one session, so no count changed.
    serves = collections.defaultdict(list)
    for g in st["pending"]:
        for sv in g.get("served") or []:
            if len(sv) > 1 and sv[0] in ("mem", "word", "place"):
                serves[(str(g.get("sid", ""))[:8].lower(), sv[0], str(sv[1]).lower())].append(g["t"])
    still, judged_now = [], []
    for f in st["pending"]:
        if f["t"] + HORIZON > horizon_end:
            if now - f["t"] < 3 * 86400:
                still.append(f)
            continue
        s = str(f.get("sid", ""))[:8].lower()
        out = [x for x in by_s.get(s, []) if f["t"] < x[0] <= f["t"] + HORIZON]
        # CENSORED, NOT IGNORED (30 Sep, the 1000-page report's D16-T05). A line the session did not take counts as
        # ignored only when the session was seen after the window closed; a session that ended inside the window (or
        # has not been seen since) could not have read it, and until now each of its lines drew a negative. Such a
        # line now has no outcome and teaches nothing either way. A line it did take is a success as before.
        seen_after = any(x[0] > f["t"] + HORIZON for x in by_s.get(s, []))
        read = {nid.lower() for _ts, kd, nid in out if kd in ("mem", "recalled", "memw")}
        errs = {"err:" + nid for _ts, kd, nid in out if kd == "err"}
        corrected = any(kd == "verdict" and nid in ("correction", "repeat") for _ts, kd, nid in out)
        places = {"%s:%s" % (kd, nid) for _ts, kd, nid in out if kd in ("dir", "proj")}
        # fired = a line was shown; a line held back by design is its own state (28 Sep, account03/B08 R12,
        # B08-R12-HOLD-MEASUREMENT-011: until then an evaluation that only held a line counted as fired)
        sv_all = f.get("served") or []
        res = {"t": f["t"], "sid": s, "ev": f.get("ev"), "ep": f.get("ep", ""), "eid": f.get("eid"),
               "fired": any(sv[0] != "held" for sv in sv_all), "held": any(sv[0] == "held" for sv in sv_all),
               "top1": (f.get("mem") or [[None]])[0][0], "top1_read": False, "top8_read": False, "lines": [],
               "kind": kind_of(s, f.get("ep", ""))}
        cands = [m[0] for m in (f.get("mem") or [])]
        res["top1_read"] = bool(cands and cands[0] in read)
        res["top8_read"] = bool(set(cands) & read)
        # shadow channels, judged as predictions: the top error and place. A channel whose line was SHOWN in this
        # evaluation is marked, and its prediction score leaves it out (27 Sep: a shown warning that prevented the error
        # read as a miss, so a channel that worked would have switched itself off)
        shown = {sv[0] for sv in f.get("served") or []}
        if f.get("err"):
            res["err_top"], res["err_score"] = f["err"][0][0], f["err"][0][1]
            res["err_got"] = f["err"][0][0] in errs
            res["err_shown"] = "err" in shown
        if f.get("place"):
            res["place_score"] = f["place"][0][1]
            res["place_got"] = f["place"][0][0] in places
        vd = [v for v in (f.get("verdict") or []) if v[0] in ("verdict:correction", "verdict:repeat")]
        if vd:                               # the warning before his corrections, as a prediction (shown ones marked)
            res["verdict_score"] = vd[0][1]
            res["verdict_got"] = corrected
            res["verdict_shown"] = "verdict" in shown
        if f.get("word"):                    # his prompt's best word match, judged whether or not it was shown
            res["word_score"] = f["word"][0][1]
            res["word_got"] = str(f["word"][0][0]).lower() in read
        # The fix memory an error line names (or would have named, when the line was held back) is judged in both arms
        # and learned from in neither: no learned edge, no tiredness, no memory-bar evidence (28 Sep, account01/B08 R49,
        # B08-R49-ADAPTIVE-LEARNER-INTERFERENCE-047). Until then a shown one was a memory line - a negative edge on a
        # no-read, a count toward tired, a line for the memory bars - and a held one was nothing, so which arm a line
        # drew changed the learner's state even when the session did the same thing.
        fix = f.get("fix") if isinstance(f.get("fix"), dict) and f["fix"].get("candidate") else None
        if fix:
            res["fix"] = {"candidate": fix["candidate"], "delivered": bool(fix.get("delivered")),
                          "spent": bool(fix.get("spent")), "estimand": fix.get("estimand"),
                          "withheld": bool(fix.get("withheld")), "unit": fix.get("unit"),
                          "read": str(fix["candidate"]).lower() in read}
        def answered_later(kind, key):
            """True when the first outcome for this line in the window came after a later serve of the same line."""
            k = key.lower()
            if kind == "place":
                hits = [ts for ts, kd, nid in out if kd in ("dir", "proj") and "%s:%s" % (kd, nid) == key.split(":", 1)[1]]
            else:
                hits = [ts for ts, kd, nid in out if kd in ("mem", "recalled", "memw") and nid.lower() == k]
            if not hits:
                return False
            first = min(hits)
            return any(f["t"] < t < first for t in serves.get((s, kind, k), ()))

        for i, sv in enumerate(f.get("served") or []):
            kind, key = sv[0], sv[1]
            by = sv[2] if len(sv) > 2 else []
            if kind in ("mem", "word"):
                ok = key.lower() in read
                if ok and answered_later(kind, key):
                    ok = None                        # the read answers a later serve of the same line
                if kind == "mem" and fix and fix.get("delivered") and fix.get("at") == i \
                        and key.lower() == str(fix["candidate"]).lower():
                    kind = "fix"                     # the error line's fix memory: judged here, learned from nowhere
                    res["fix"]["line_found"] = True
            elif kind == "err":
                ok = None                            # shown: its outcome is not its prediction (see above)
            elif kind == "place":
                ok = key.split(":", 1)[1] in places
                if ok and answered_later(kind, key):
                    ok = None
            elif kind == "verdict":
                ok = None                            # shown: a warning that prevented his correction is not a miss
            elif kind == "held" and str(key).startswith("mem:"):
                # the diffuse-line trial (owner, 6 Oct 2026): the held arm of a memory line, judged as the shown arm is,
                # learned from nowhere; shown against held take-up is the effect of showing it
                kind = "held_mem"
                ok = key[4:].lower() in read
                if ok and answered_later("mem", key[4:]):
                    ok = None
            elif kind == "held" and str(key).startswith("place:"):
                kind = "held_place"
                ok = key.split(":", 1)[1] in places
                if ok and answered_later("place", key):
                    ok = None
            else:
                ok = None                    # an attractor line is judged through the memory it carried
            if ok is False and not seen_after:
                ok = None
                n_censored += 1
            res["lines"].append([kind, key, ok, by])
        if fix and res["fix"]["delivered"] and not res["fix"].get("line_found"):
            # a SHOW record whose carried line is not where the record says (28 Sep, account04/B20 r14,
            # B20-R37-COARSE-COMPLIANCE-WITHOUT-UPTAKE-20260928): not a delivery of the candidate, whatever else was shown
            res["fix"]["line_found"] = False
        if res["fired"] and res.get("eid") and res["eid"] not in emitted:
            # no receipt from the hook: whether its lines reached the session is unknown, so they teach nothing, neither
            # way (29 Sep, account01/B15 r84: a line on record that never printed read as shown and not taken up)
            res["emission"] = "unknown"
            n_unemitted += 1
            for ln in res["lines"]:
                ln[2] = None
        judged_now.append(res)
    st["pending"] = still
    judged = (st.get("judged", []) + judged_now)
    judged = [j for j in judged if now - j["t"] < 7 * 86400]
    for j in judged:                      # evaluations judged before 27 Sep carry no kind: read it from the transcript
        if "kind" not in j:
            j["kind"] = kind_of(j.get("sid", ""), j.get("ep", ""))
    st["judged"] = judged
    tests = [j for j in judged if j.get("kind") == "test"]
    judged = [j for j in judged if j.get("kind") != "test"]     # what the edges, the bars and the precision learn from
    # 3. learning on the edges that lit each judged line. Each evaluation's identity goes into the processed table in the
    # same transaction as its counts, and one already there adds nothing (28 Sep, interactive audit IA-20260928-06): the
    # state file with the consumed offsets is written in step 8, after this commit, so a run that died in between left
    # the counts in and the evaluations pending, and the next run judged and counted them again.
    n_learn, n_dup = 0, 0
    if judged_now and not a.dry:
        db = learned_db()
        for j in judged_now:
            if j.get("kind") == "test":
                continue
            if db.execute("INSERT OR IGNORE INTO processed(obs, t) VALUES (?, ?)", (obs_id(j), j["t"])).rowcount == 0:
                n_dup += 1
                continue
            for kind, key, ok, by in j["lines"]:
                if ok is None or not by or kind in ("fix", "held_mem", "held_place"):
                    continue
                dst = ("mem:" + key) if kind == "mem" else (key if kind == "err" else ("verdict:correction" if kind == "verdict" else key.split(":", 1)[1]))
                for src in by:
                    db.execute("INSERT INTO learned VALUES (?,?,?,?,?) ON CONFLICT(src, dst) DO UPDATE SET "
                               "pos = pos + excluded.pos, neg = neg + excluded.neg, updated = excluded.updated",
                               (src, dst, 1.0 if ok else 0.0, 0.0 if ok else 1.0, now))
                    n_learn += 1
        db.execute("DELETE FROM processed WHERE t < ?", (now - 14 * 86400,))   # a fire is re-judged within 3 days at most
        db.commit()
        db.close()
    # 4. precision per class over the week, and the eval-level hit rates
    prec = {}
    for cls in ("mem", "word", "err", "place", "verdict"):
        ls = [ln for j in judged for ln in j["lines"] if ln[0] == cls and ln[2] is not None]
        taken = sum(1 for ln in ls if ln[2])
        prec[cls] = {"judged": len(ls), "taken": taken, "rate": round(taken / len(ls), 4) if ls else None}
    cohort = _trial_cohort(judged, write=not a.dry)
    for shown, held in (("mem", "held_mem"), ("place", "held_place")):
        # the diffuse-line trial on the persistent first-exposure cohort (see _trial_cohort): an outcome still unknown
        # is counted as unknown, never skipped and never replaced by a later exposure in the other arm
        rows = [v for v in cohort.values() if v.get("kind") in (shown, held)]
        sl = [v for v in rows if v["kind"] == shown]
        hl = [v for v in rows if v["kind"] == held]
        prec["trial_" + shown] = {"since": TRIAL_START, "unit": "first exposure per (session, key), persistent",
                                  "shown": sum(1 for v in sl if v.get("ok") is not None),
                                  "shown_taken": sum(1 for v in sl if v.get("ok")),
                                  "shown_unknown": sum(1 for v in sl if v.get("ok") is None),
                                  "held": sum(1 for v in hl if v.get("ok") is not None),
                                  "held_taken": sum(1 for v in hl if v.get("ok")),
                                  "held_unknown": sum(1 for v in hl if v.get("ok") is None),
                                  "sessions": len(set(v.get("sid") for v in rows)),
                                  "identity_unverified": sum(1 for v in rows if (v.get("identity") or (
                                      "eid" if v.get("eid") is not None else "none")) != "eid"),
                                  "cohort_status": TRIAL_COHORT_STATUS[0]}
    ev_all = len(judged)
    fx = [j["fix"] for j in judged if j.get("fix")]
    prec["fix"] = {"shown": sum(1 for x in fx if x["delivered"]),
                   "shown_read": sum(1 for x in fx if x["delivered"] and x["read"]),
                   "held": sum(1 for x in fx if not x["delivered"] and not x["withheld"]),
                   "held_read": sum(1 for x in fx if not x["delivered"] and not x["withheld"] and x["read"]),
                   "held_spent": sum(1 for x in fx if not x["delivered"] and x["spent"]),
                   "shown_line_missing": sum(1 for x in fx if x["delivered"] and x.get("line_found") is False),
                   "shown_line_found_read": sum(1 for x in fx if x["delivered"] and x.get("line_found") and x["read"]),
                   "note": "the fix memory an error line names, SHOW against HOLD: read within 30 min; judged in both arms, "
                           "learned from in neither (28 Sep, account01/B08 R49). shown/held are by assignment (intention "
                           "to treat); shown_line_missing counts SHOW records whose carried line is absent, "
                           "shown_line_found_read the reads among the rest (account04/B20 r14)"}
    prec["evaluations"] = {"judged": ev_all, "fired": sum(1 for j in judged if j["fired"]),
                           "held_by_design": sum(1 for j in judged if j.get("held")),
                           "top1_read_30min": round(sum(j["top1_read"] for j in judged) / ev_all, 4) if ev_all else None,
                           "top8_any_read_30min": round(sum(j["top8_read"] for j in judged) / ev_all, 4) if ev_all else None}
    tm = [ln for j in tests for ln in j["lines"] if ln[0] == "mem" and ln[2] is not None]
    prec["test_workers"] = {"evaluations": len(tests), "fired": sum(1 for j in tests if j["fired"]),
                            "mem_lines": len(tm), "mem_taken": sum(1 for ln in tm if ln[2]),
                            "note": "the benches' headless workers: judged and reported, not learned from (27 Sep)"}
    prec["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    # 5. tired memories: served 4+ times in 24 h, never taken up
    served24 = collections.Counter()
    taken24 = collections.Counter()
    for j in judged:
        if now - j["t"] < 86400:
            for kind, key, ok, _by in j["lines"]:
                if kind in ("mem", "word"):
                    served24[key] += 1
                    taken24[key] += bool(ok)
    pol = jload(POLICY, {})
    tired_until = pol.get("tired_until", {})
    for k, n in served24.items():
        # 26 Sep 19:55: was "never taken up"; the qform memory was served to 25 sessions in 18 minutes and read by one or
        # two that were reading it anyway, so it never tired and stayed the size channel's top pick for hours
        if n >= 4 and taken24[k] < 0.15 * n:
            tired_until[k] = now + 3 * 86400
    tired_until = {k: v for k, v in tired_until.items() if v > now}
    pol["tired_until"] = tired_until
    pol["tired"] = sorted(tired_until)
    # 6. the bar: moved once per batch of new evidence (update_mem_bars; the audit of 27 Sep found it re-moved on old lines)
    # a line held back by design is a fire for the bar's rate, as it is for decide()'s hourly budget (B08 R49): the
    # rate the bar reads must not depend on which arm the lines drew
    slots = sum(1 for j in judged if j["fired"] or j.get("held"))
    bar_note = update_mem_bars(pol, judged, slots / max(1, ev_all))
    # the shadow error channel: shown only once its own top line, at the bar, is right often enough
    sh = [j for j in judged if "err_score" in j and not j.get("err_shown")
          and j["err_score"] >= float(pol.get("err_hebb_min", 47.5))]
    if len(sh) >= 30:
        pe = sum(1 for j in sh if j["err_got"]) / len(sh)
        prec["err_shadow"] = {"judged": len(sh), "right": sum(1 for j in sh if j["err_got"]), "rate": round(pe, 4)}
        was = bool(pol.get("serve_err", False))
        pol["serve_err"] = pe >= 0.2 if not was else pe >= 0.12
        if pol["serve_err"] != was:
            bar_note += "; error lines %s (shadow precision %.0f%% on %d)" % ("ON" if pol["serve_err"] else "OFF",
                                                                           100 * pe, len(sh))
    # the warning before his corrections: shown while its own line, at the bar, beats his base rate by enough. His
    # corrections follow few moments (re-derived 26 Sep: about 8 a day, most of them 'again'), so the bar is relative.
    # headless sessions (ep sdk-cli: a script's prompt, a test worker) never draw his corrections; judged before 26 Sep 19:44
    # carry no ep and stay in
    allv = [j for j in judged if "verdict_score" in j and j.get("ep", "") in ("", "cli") and not j.get("verdict_shown")]
    shv = [j for j in allv if j["verdict_score"] >= float(pol.get("verdict_hebb_min", 76.0))]
    if allv:
        base_v = sum(1 for j in allv if j["verdict_got"]) / len(allv)
        prec["verdict_shadow"] = {"judged": len(shv), "right": sum(1 for j in shv if j["verdict_got"]),
                                  "rate": round(sum(1 for j in shv if j["verdict_got"]) / len(shv), 4) if shv else None,
                                  "base_rate": round(base_v, 4), "moments": len(allv)}
        if len(shv) >= 30:
            pv = sum(1 for j in shv if j["verdict_got"]) / len(shv)
            was = bool(pol.get("serve_verdict", True))      # the membrane's own default
            pol["serve_verdict"] = pv >= max(0.10, 3 * base_v) if not was else pv >= max(0.06, 2 * base_v)
            if pol["serve_verdict"] != was:
                bar_note += "; correction warnings %s (shadow precision %.0f%% on %d, base %.1f%%)" % (
                    "ON" if pol["serve_verdict"] else "OFF", 100 * pv, len(shv), 100 * base_v)
    shp = [j for j in judged if "place_score" in j and j["place_score"] >= float(pol.get("place_hebb_min", 40.0))]
    if shp:
        prec["place_shadow"] = {"judged": len(shp), "right": sum(1 for j in shp if j["place_got"]),
                                "rate": round(sum(1 for j in shp if j["place_got"]) / len(shp), 4)}
    # his prompt's best word match (live_membrane.word_candidates), judged on every prompt, his (interactive) apart from the
    # headless ones. The replay (26 Sep): headless 20 of 41 above the bar, his 0 of 43 at any score - so it is shown only
    # when HIS prompts above the bar read the match 25% of the time on 30 lines, and off again under 15%.
    allw = [j for j in judged if "word_score" in j]
    if allw:
        wmin = float(pol.get("word_min", 61.4))
        ws = {}
        for kind, pick in (("his", lambda j: j.get("ep") == "cli"), ("headless", lambda j: j.get("ep") == "sdk-cli")):
            aw = [j for j in allw if pick(j)]
            sw = [j for j in aw if j["word_score"] >= wmin]
            ws[kind] = {"prompts": len(aw), "right_any": sum(1 for j in aw if j["word_got"]), "judged": len(sw),
                        "right": sum(1 for j in sw if j["word_got"]),
                        "rate": round(sum(1 for j in sw if j["word_got"]) / len(sw), 4) if sw else None}
        prec["word_shadow"] = dict(ws, bar=wmin)
        if ws["his"]["judged"] >= 30:
            pw = ws["his"]["right"] / ws["his"]["judged"]
            was = bool(pol.get("serve_word", False))
            pol["serve_word"] = pw >= TARGET_P if not was else pw >= 0.15
            if pol["serve_word"] != was:
                bar_note += "; word lines %s (his prompts above the bar read %.0f%% on %d)" % (
                    "ON" if pol["serve_word"] else "OFF", 100 * pw, ws["his"]["judged"])
    # 7. the recall weight, from brain.py recall's own log. Since 27 Sep: the session comes from the record when it names
    # one (brain.py logs it since 02:40), the target is the FIRST memory opened after the search (the best-ranked opened
    # memory favoured whichever weight ranked any of several opens high), and the weight learns only from agents at work:
    # a test worker's search is judged and counted by kind but not learned from.
    recs, reset_rec = read_bound(st, "recall", RECALL_LOG)
    if reset_rec:                          # read again from the start: a search is known by its time
        resets.append("recall")
        seen_t = {r["t"] for r in st.get("pending_recalls", [])} | {x["t"] for x in st.get("recall_judged", [])}
        recs = [r for r in recs if r.get("t") not in seen_t]
    st["pending_recalls"] = [r for r in st.get("pending_recalls", []) + recs if now - r["t"] < 86400]
    lam_note = ""
    rj = st.get("recall_judged", [])
    keep = []

    for r, best, opened in judge_searches(st["pending_recalls"], by_s):
        if r["t"] + 600 > horizon_end:
            keep.append(r)
            continue
        if not best or not opened:
            continue
        kind = kind_of(best, r.get("entry", ""))
        body = [tuple(x) for x in r.get("body") or []]
        graph = [tuple(x) for x in r.get("graph") or []]
        rr, rr_any = {}, {}
        complete = True
        for lam in (0.0,) + LAMBDAS:
            order = fuse(body, graph, lam, r.get("bounds"))
            rr[str(lam)] = 1.0 / (order.index(opened[0]) + 1) if opened[0] in order else 0.0
            ranks = [order.index(o) + 1 for o in opened if o in order]
            rr_any[str(lam)] = 1.0 / min(ranks) if ranks else 0.0
            complete = complete and replay_complete(body, graph, lam, r.get("bounds"), opened[0], r.get("n"))
        rj.append({"t": r["t"], "rr": rr, "rr_any": rr_any, "kind": kind, "complete": complete, "session": best})
    st["pending_recalls"] = keep
    rj = [x for x in rj if now - x["t"] < 14 * 86400][-1000:]
    st["recall_judged"] = rj
    # the weight learns only from replays that are exact at every weight it compares (29 Sep, audit G02); a search judged
    # before that has no such record and is not used
    work = [x for x in rj if x.get("kind") in ("his", "script") and x.get("complete") is True][-200:]
    status_incomplete = sum(1 for x in rj if x.get("kind") in ("his", "script") and x.get("complete") is not True)
    by_kind = collections.Counter(x.get("kind", "before 27 Sep") for x in rj)
    kinds_note = ", ".join("%s %d" % kv for kv in sorted(by_kind.items()))
    # each session one vote: a session's searches are averaged first, then the sessions (searches judged before 30 Sep
    # carry no session and wait out their fourteen days without a vote)
    by_sess = collections.defaultdict(list)
    for x in work:
        if x.get("session"):
            by_sess[x["session"]].append(x)
    voted = sum(len(v) for v in by_sess.values())
    top_share = (max(len(v) for v in by_sess.values()) / float(voted)) if voted else 0.0
    sess_note = "%d of them from %d sessions, the largest %d pct" % (voted, len(by_sess), round(100 * top_share))
    if voted >= 30 and len(by_sess) >= MIN_SESSIONS:
        mrr = {lam: sum(sum(x["rr"].get(str(lam), 0.0) for x in v) / len(v) for v in by_sess.values()) / len(by_sess)
               for lam in (0.0,) + LAMBDAS}
        dial = jload(DIAL, {})
        cur = float(dial.get("lambda", 0.1))
        best_lam = max(LAMBDAS, key=lambda l: (round(mrr[l], 4), -abs(l - cur)))
        step = 0.05 if best_lam > cur else -0.05
        new = cur if abs(best_lam - cur) < 1e-9 else round(min(0.4, max(0.05, cur + step)), 2)
        lam_note = "recall weight %.2f -> %.2f (MRR of the first memory opened, each session one vote, %d judged " \
                   "searches by agents at work, %s: %s; judged by kind: %s)" % (
                       cur, new, len(work), sess_note, ", ".join("%g %.3f" % (l, mrr[l]) for l in (0.0,) + LAMBDAS),
                       kinds_note)
        # one move per batch of new evidence, as the bars (step 6): the move records the newest judged search it was made
        # on, and replaying the same searches - a run that died before step 8 saved the state - does not move it again
        newest = max(x["t"] for x in work)
        if new != cur and not a.dry:
            if newest > float(dial.get("_moved_on_until", 0.0)):
                dial["lambda"] = new
                dial["_learned"] = lam_note
                dial["_moved_on_until"] = newest
                jsave(DIAL, dial)
            else:
                lam_note += "; not moved: no judged search newer than the last move's"
    else:
        lam_note = ("recall weight unchanged: %d judged searches by agents at work, %s; 30 needed from %d sessions or more "
                    "(judged by kind: %s; %d not exact at every weight, left out)" % (
                        len(work), sess_note, MIN_SESSIONS, kinds_note, status_incomplete))
    # 8. write it all
    if not a.dry:
        jsave(PRECISION, prec)
        pol["version"] = int(pol.get("version", 1)) + (1 if bar_note else 0)
        jsave(POLICY, pol)
    status = {"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "pending": len(st["pending"]), "judged_now": len(judged_now),
              "edges_learned_now": n_learn, "evaluations_already_learned": n_dup, "precision": prec, "bar": bar_note or "unchanged", "recall": lam_note,
              "recall_judged_by_kind": dict(by_kind), "recall_not_exact_left_out": status_incomplete,
              "recall_sessions": len(by_sess), "recall_largest_session_share": round(top_share, 3),
              "shown_without_receipt": n_unemitted, "censored_not_ignored": n_censored, "logs_read_again": resets,
              "tired": len(tired_until),
              "new_fires": len(fires),
              "new_events": len(evs), "recall_logged": len(recs)}
    if os.path.exists(LEARNED):
        try:
            db = sqlite3.connect(LEARNED)
            status["learned_edges"] = db.execute("SELECT COUNT(*), SUM(pos), SUM(neg) FROM learned").fetchone()
            db.close()
        except Exception:
            pass
    if not a.dry:
        jsave(STATE, st)
        jsave(STATUS, status)
        session_kinds.save()               # the kinds this run settled, into the cache every consumer shares
        with open(LOGMD, "a", encoding="utf-8") as fh:
            fh.write("- %s: %d new evaluations, %d judged (%d fired lines); memory lines taken up %s of %s; top-1 read "
                     "in 30 min %s; %s; %s\n" % (
                         status["updated"], len(fires), len(judged_now), sum(len(j["lines"]) for j in judged_now),
                         prec["mem"]["taken"], prec["mem"]["judged"], prec["evaluations"]["top1_read_30min"],
                         status["bar"], status["recall"]))
    print(json.dumps(status, indent=1))


if __name__ == "__main__":
    main()
