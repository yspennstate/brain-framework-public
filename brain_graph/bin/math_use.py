"""math_use.py - the math brain's use-grown layer: claims that get used grow, claims used together connect (26 Sep).

His order of 26 Sep 15:5x: the brain live, "the edges growing and learning, including for math brain". The math store
(math-brain-build, 8,776 nodes) has typed edges written by its builders (depends_on, cites, supersedes ...) and
math_sense.py ranks claims mostly by words; nothing in it grew from use. This is that layer, from what the math hook
actually served and what agents actually fetched:

  size   each claim's use history - every time the hook served it into a session, and every explicit fetch (weight 2) -
         read as an ACT-R base level, ln sum over uses of age^-0.5 (hours), squashed to 0..1 across the claims
  co     claims served into the same session are one co-activation; NPMI over sessions (positive part) is the edge, so
         a claim used with the ones this session already has lights up (Hebbian, as the voice note asks)
  score  0.5 size + 0.5 max NPMI to a claim this session was already served; math_sense multiplies its own rank score
         by (1 + MATHBRAIN_USE_WEIGHT x score), default weight 0.15: a tilt among close candidates, never an override
Sources: .math_sense_sessions/<shard>.json (served sets per session, since the shards began), mathbrain_fetches.tsv,
and live/math_serves.jsonl, which math_sense.py appends to on every serve from 26 Sep on (log_serve). Rebuilt hourly by
run_graph.py capture (build()); read by the hook through scores().

Only real sessions count (27 Sep): a serve, shard or fetch is use when its session is his (an interactive Claude session
or a Codex cli/vscode thread) or a script (any other headless Claude session or Codex thread). The recall benches' test
workers (first prompt '[headless-worker]') and callers with no session of their own - the math evaluation harnesses,
which give each question a fresh id - are counted in 'skipped' and learned from not at all. On 27 Sep that left one
session's shard of 13,242 and no fetch: the layer is nearly empty because real sessions have rarely been served math.

    python math_use.py build | stats
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import collections
import glob
import json
import math
import os
import time

HOME = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph")
LIVE = os.path.join(HOME, "live")
SERVES = os.path.join(LIVE, "math_serves.jsonl")
STORE = os.path.join(LIVE, "math_use.json")
DREAM = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")
SHARDS = os.path.join(DREAM, ".math_sense_sessions")
FETCHES = os.path.join(DREAM, "mathbrain_fetches.tsv")
ARCHIVE = os.path.join(LIVE, "math_served_archive.jsonl")        # written by math_sense.py before it retires a shard
SNAPSHOT = os.path.join(LIVE, "math_served_snapshot_20260926.jsonl")
REAL = ("his", "script")                        # the session kinds whose serves count as use
_CACHE = {}


def snapshot():
    """Copy every math session shard as it stands now into SNAPSHOT (26 Sep, the day the 48-hour deletion stopped)."""
    n = 0
    with open(SNAPSHOT, "a", encoding="utf-8") as out:
        for p in glob.glob(os.path.join(SHARDS, "*.json")):
            try:
                rec = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            out.write(json.dumps({"shard": os.path.basename(p)[:-5], "archived": round(time.time(), 1), "rec": rec},
                                 separators=(",", ":")) + "\n")
            n += 1
    return n


def log_serve(session_id, ids):
    """Called by math_sense.py when it emits cards. Never raises. The whole session id since 27 Sep; before, its first 8
    characters, which a Codex id (it begins with the clock) shares with every thread started in the same minute."""
    try:
        if not ids:
            return
        os.makedirs(LIVE, exist_ok=True)
        with open(SERVES, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": round(time.time(), 1), "sid": str(session_id or "")[:64],
                                 "ids": list(ids)[:12]}, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _session_kinds():
    """The shared classifier (session_kinds.py): his / test / script / unknown for a whole session id or its first 8
    characters. 27 Sep: 13,164 of this layer's 13,242 shards came from callers with no session of their own - the math
    evaluation harnesses give each question a fresh id ('vib-<uuid>', up to 750 an hour on 25-26 Sep) - and 77 from the
    recall benches' test workers; counted as use, they made the layer's sizes and co-activations the benches' questions.
    A shard is named sha1(session id)[:24] (math_sense._session_shard)."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import session_kinds
    return session_kinds


def build():
    t0 = time.time()
    # One co-activation per SESSION (29 Sep; scheduled audit account01/B06 r24,
    # B06-R70-MATH-USE-DOUBLE-COUNTS-SESSION-COACTIVATION-20260929). A session's shard (live, archived or
    # snapshotted) and its serve-log lines used to enter as separate sets, so one served session met the two-session
    # floor of an edge by itself. They are merged under one key: the whole session id when it is known, else the
    # shard's own name. An old 8-character serve id that matches no single known session cannot be joined to its
    # shard, so it is left out of co-activation; its uses still count.
    merged = {}                                 # session key -> [last time, set of claim ids, first source]
    uses = collections.defaultdict(list)        # claim -> [(time, weight)]
    seen_shards = set()                         # (shard, time): a shard read live, archived or snapshotted counts once
    import hashlib
    sk = _session_kinds()
    known = sk.known_ids()
    by_hash = {hashlib.sha1(sid.encode("utf-8", "replace")).hexdigest()[:24]: sid for sid in known}
    by_prefix = collections.defaultdict(list)
    for sid in known:
        by_prefix[sid[:8]].append(sid)
    kept, skipped = collections.Counter(), collections.Counter()
    excluded_legacy = 0

    def add(key, ts, ids, src):
        m = merged.get(key)
        if m is None:
            merged[key] = [ts, set(ids), src]
        else:
            m[0] = max(m[0], ts)
            m[1].update(ids)

    def keep(sid, what):
        kind = sk.kind(sid) if sid else "no session"
        if kind in REAL:
            kept[what + " " + kind] += 1
            return True
        skipped[what + " " + kind] += 1
        return False
    for p in glob.glob(os.path.join(SHARDS, "*.json")):
        try:
            rec = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        ids = [x for x in rec.get("ids") or [] if isinstance(x, str)]
        ts = float(rec.get("ts") or os.path.getmtime(p))
        key = (os.path.basename(p)[:-5], round(ts, 1))
        if ids and key not in seen_shards:
            seen_shards.add(key)
            shard = os.path.basename(p)[:-5]
            if keep(by_hash.get(shard), "shard"):
                add(by_hash.get(shard) or ("shard", shard), ts, ids, "shard:" + shard[:12])
    # shards the math hook retired after 48 hours (archived first, by his 26 Sep law), and the one-time snapshot
    for arch in (ARCHIVE, SNAPSHOT):
        if not os.path.exists(arch):
            continue
        for line in open(arch, encoding="utf-8"):
            try:
                a = json.loads(line)
                rec = a.get("rec") or {}
                ids = [x for x in rec.get("ids") or [] if isinstance(x, str)]
                ts = float(rec.get("ts") or a.get("archived") or 0)
            except Exception:
                continue
            key = (str(a.get("shard")), round(ts, 1))
            if ids and ts and key not in seen_shards:
                seen_shards.add(key)
                shard = str(a.get("shard"))
                if keep(by_hash.get(shard), "shard"):
                    add(by_hash.get(shard) or ("shard", shard), ts, ids, "shard:" + shard[:12])
    by_sid = collections.defaultdict(lambda: [0.0, set()])
    if os.path.exists(SERVES):
        for line in open(SERVES, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            sid = str(r.get("sid") or "").lower()   # lines written before 27 Sep carry the first 8 characters only
            if not keep(sid or None, "serve"):
                continue
            for cid in r.get("ids") or []:
                uses[cid].append((r["t"], 1.0))
            b = by_sid[sid]
            b[0] = max(b[0], r["t"])
            b[1].update(r.get("ids") or [])
    logged = {k for k in by_sid}
    for sid, (ts, ids) in by_sid.items():
        if len(sid) > 8:
            key = sid
        else:
            full = by_prefix.get(sid, [])
            if len(full) != 1:
                excluded_legacy += 1
                continue
            key = full[0]
        add(key, ts, ids, "log:" + sid[:8])
    sets = [(ts, frozenset(ids), src) for ts, ids, src in merged.values()]
    # a shard's ids carry only the shard's last time; use it once per id when the serve log has nothing on that id
    for ts, ids, src in sets:
        if src.startswith("shard:"):
            for cid in ids:
                if cid not in uses:
                    uses[cid].append((ts, 1.0))
    if os.path.exists(FETCHES):
        for line in open(FETCHES, encoding="utf-8", errors="replace"):
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3 and parts[2].strip():
                if not keep(parts[1].strip() or None, "fetch"):       # before 27 Sep the column was always empty
                    continue
                if len(parts) >= 4 and parts[3].strip() != "ok":      # a refused read is not a use (B19-R79, 30 Sep)
                    continue
                try:
                    uses[parts[2].strip()].append((float(parts[0]), 2.0))
                except ValueError:
                    continue
    n_sets = max(1, len(sets))
    single = collections.Counter()
    pair = collections.Counter()
    for _ts, ids, _src in sets:
        ids = sorted(ids)[:60]
        single.update(ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pair[(ids[i], ids[j])] += 1
    co = collections.defaultdict(dict)
    for (a, b), n in pair.items():
        if n < 2:
            continue
        pab = n / n_sets
        pa, pb = single[a] / n_sets, single[b] / n_sets
        npmi = math.log(pab / (pa * pb)) / -math.log(pab) if pab < 1 else 0.0
        if npmi > 0:
            co[a][b] = round(npmi, 4)
            co[b][a] = round(npmi, 4)
    sk.save()
    out = {"built": time.time(), "sets": n_sets, "logged_sessions": len(logged), "kept": dict(kept),
           "skipped": dict(skipped), "legacy_ids_left_out": excluded_legacy,
           "uses": {k: sorted(v)[-60:] for k, v in uses.items()}, "co": co}
    os.makedirs(LIVE, exist_ok=True)
    tmp = STORE + ".tmp"
    json.dump(out, open(tmp, "w", encoding="utf-8"))
    os.replace(tmp, STORE)
    return {"sets": n_sets, "claims_with_uses": len(uses), "claims_with_edges": len(co),
            "edges": sum(len(v) for v in co.values()) // 2, "kept": dict(kept), "skipped": dict(skipped),
            "seconds": round(time.time() - t0, 1)}


def _store():
    try:
        mt = os.path.getmtime(STORE)
    except OSError:
        return None
    if _CACHE.get("mt") != mt:
        try:
            _CACHE["s"] = json.load(open(STORE, encoding="utf-8"))
            _CACHE["mt"] = mt
        except Exception:
            return None
    return _CACHE["s"]


def _size(us, now):
    s = sum(w * max((now - t) / 3600.0, 1.0 / 60.0) ** -0.5 for t, w in us if t < now)
    return math.log(s) if s > 0 else None


def scores(session_id, claim_ids, served_ids=(), now=None):
    """{claim id: 0..1} for the candidates: half node size (squashed ACT-R level), half the strongest NPMI edge to a claim
    this session was already served. Unknown claims score 0. Never raises."""
    try:
        st = _store()
        if not st:
            return {}
        now = now or time.time()
        uses, co = st.get("uses", {}), st.get("co", {})
        served = set(served_ids or ())
        out = {}
        for cid in claim_ids:
            b = _size(uses.get(cid, ()), now)
            size = 1.0 / (1.0 + math.exp(-(b - 1.0))) if b is not None else 0.0     # level 1 (a few uses today) -> 0.5
            edge = max((co.get(cid, {}).get(s, 0.0) for s in served), default=0.0)
            v = 0.5 * size + 0.5 * edge
            if v > 0:
                out[cid] = round(v, 4)
        return out
    except Exception:
        return {}


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["build"]:
        print(json.dumps(build(), indent=1))
    else:
        s = _store() or {}
        print({k: (v if not isinstance(v, dict) else len(v)) for k, v in s.items()})
