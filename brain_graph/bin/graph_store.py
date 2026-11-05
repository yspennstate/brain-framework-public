"""graph_store.py - the brain graph built from use, for the dream to rebuild and the membrane to read.

His 26 Sep voice note: "Parts of dreams should be algorithms building the edges and building the connections and
activation paths and all that, just automatically through mathematics." This is that algorithm. It reads the touch
events (capture/extract_touch_events.py: every tool, command, file, folder, repository, host, service, store, dial,
memory read or written, note served, reflex fired, error, word and verdict, per session and second), and writes a
compact store:

  nodes     every node ever named, with its class (the registry: a node exists before any edge reaches it)
  uses      when each node was needed (the last 50 times, plus a count and first time of older ones) - node size
  edges     context -> target counts: whenever a target was needed (a memory read, a failed call, a tool, the first
            touch of a file, folder, project, repository or host, his verdict), every distinct item the same session
            touched in the W seconds before it adds one to (item -> target), and an error also adds to
            (item -> err:ANY); hubs (items in more than HUB of all sessions) carry no signal and are left out
  co        item <-> item counts between non-word items within 8 events and W seconds of each other (second hop)
  sums      each item's total outgoing weight, each target's total incoming weight, the grand total - what
            ln(P(i | j) / P(i)) needs, so a row read in part still gives the right association
  times     each edge's first and last moment (27 Sep: "the graph should also be temporal") - edges.npz p_first and
            p_last, float32 seconds after t_base; GraphStore.times(k) gives epoch seconds

The membrane (brain_graph/membrane.py) takes a small in-memory Graph; `GraphStore.subgraph(seed, now)`
builds one holding only what the seed can reach (its rows, a bounded second hop, the uses of every node involved and the
true row sums), so a hook call reads kilobytes, not the whole store.

    python graph_store.py build --events EVENTS --out STORE_DIR [--until EPOCH]
    python graph_store.py query --store STORE_DIR --seed "tool:Bash:ssh" "host:build-box" [--k 5]
    python graph_store.py selftest
"""
import argparse
import collections
import contextlib
import itertools
import json
import math
import os
import re
import sys
import time
import zlib

import numpy as np

TARGET_KINDS = ("mem", "err", "tool", "file", "dir", "proj", "repo", "host", "verdict")
FIRST_TOUCH = ("file", "dir", "proj", "repo", "host")
CO_KINDS = ("mem", "file", "tool", "err", "reflex", "served", "recalled", "cmd", "host", "svc", "store", "dial", "repo")
JUNK = re.compile(r"[$%{}*?<>|`\\]")
ERR_ANY = "err:ANY"
KEEP_USES = 50
# Bank work (the recall banks' questions, the key quarantine, the harnesses that score them) is left out of a store
# built with exclude_rx=BANK_RX, so a question a grader wrote out cannot lead straight to its answer when a ranker reading
# the store is tuned on a bank. Rule of capture/exclude_bank_sessions.py, which drops whole sessions; here only the hour
# either side of each bank-named touch goes (26 Sep capture: whole sessions 7.7 pct of events, 15 sessions, some of
# them film or simulation work that listed a bank folder twice; the hour either side 2.3 pct).
BANK_RX = (r"(?i)bank_recall_keys_quarantine|recall4|bank100|bank_rep300|rep300|bank_h2|bank_h1|recall_four_arms|"
           r"mq_bench|rewrite_probe|cloud_credit_2026|memory_questions|recall_bank|bank_recall")
EXCLUDE_KINDS = ("file", "dir", "cmd", "store", "proj", "repo")
EXCLUDE_WINDOW = 3600.0

# NODE_CLASSES.md names classes by role; the capture names them by source. The membrane reads these.
LAW_RX = re.compile(r"^(owner_law_|owner_rule_|owner_order_)")
MISTAKE_RX = re.compile(r"(^scar_|mistake|^mistakes$)")
DOC_RX = re.compile(r"/(readme[^/]*|agents\.md|claude\.md|protocol\.md|design\.md)$")
CODE_RX = re.compile(r"\.(py|ps1|sh|js|ts|vbs|bat|cmd|lean|tex)$")


def node_class(node):
    kind, nid = node.split(":", 1)
    if kind in ("mem", "memw", "recalled"):
        if LAW_RX.search(nid):
            return "law"
        if MISTAKE_RX.search(nid):
            return "mistake"
        return "mem"
    if kind == "served":
        return "note"
    if kind == "file":
        if DOC_RX.search(nid):
            return "doc"
        if CODE_RX.search(nid):
            return "code"
        return "file"
    if kind == "said":
        return "kw"
    return kind


def _merge(keys, cnt, new_keys):
    if not len(new_keys):
        return keys, cnt
    u, c = np.unique(new_keys, return_counts=True)
    k2, inv = np.unique(np.concatenate((keys, u)), return_inverse=True)
    return k2, np.bincount(inv, weights=np.concatenate((cnt, c.astype(np.float64))))


def _merge_w(keys, weights):
    """Like _merge from empty, with a weight per occurrence (edges fading with age, 26 Sep)."""
    if not len(keys):
        return keys, np.zeros(0)
    u, inv = np.unique(keys, return_inverse=True)
    return u, np.bincount(inv, weights=weights)


def _paths(events):
    """One events file, or several read one after another (26 Sep: the live capture plus the history backfill)."""
    if isinstance(events, (str, os.PathLike)):
        return [events]
    return [p for p in events if os.path.exists(p)]


@contextlib.contextmanager
def _chained(events):
    fhs = [open(p, encoding="utf-8") for p in _paths(events)]
    try:
        yield itertools.chain.from_iterable(fhs)
    finally:
        for fh in fhs:
            fh.close()


def bank_marks(events, rx=BANK_RX):
    """{session: sorted times} of every file, folder, command, store, project or repository node the rule names."""
    rx = re.compile(rx)
    marks = collections.defaultdict(list)
    for path in _paths(events):
        with open(path, encoding="utf-8") as f:
            for ln in f:
                if not rx.search(ln):
                    continue
                try:
                    e = json.loads(ln)
                except Exception:
                    continue
                if e["kind"] in EXCLUDE_KINDS and rx.search(e["id"]):
                    marks[e["session"]].append(float(e["ts"]))
    return {s: np.array(sorted(v)) for s, v in marks.items()}


def near_mark(marks, session, ts, window=EXCLUDE_WINDOW):
    v = marks.get(session)
    if v is None:
        return False
    k = int(np.searchsorted(v, ts))
    return (k < len(v) and v[k] - ts <= window) or (k > 0 and ts - v[k - 1] <= window)


FIRST_TOUCH_KEEP_DAYS = 7.0         # sessions active this recently get their first-touch sets written (first_touch.json)


def keep_tool(s, ts, x, rate=0.1):
    """Whether a tool target is kept: a tenth of them, by a hash of the session key, the time to the millisecond and the
    node name. The fast layer (live_delta.keep_tool) uses this same rule, so a tool target it keeps between two stores
    is the one the next store keeps. Until 27 Sep the store drew from a seeded sequential generator, which no layer that
    starts mid-log can reproduce: the two kept different tenths (account01/B20 R12)."""
    return (zlib.crc32(("%s|%.3f|%s" % (s, ts, x)).encode("utf-8")) % 10000) < rate * 10000


def build(events, out, until=None, window=600.0, k=40, hub=0.30, tool_rate=0.1, seed=7, exclude_rx=None,
          exclude_window=EXCLUDE_WINDOW, half_life_days=None, verdict_days=40.0, verdict_file=None,
          skip_session=None, collapse_repeats=True):
    """One pass over the touch events -> the store. Deterministic for a given input (tool targets are kept by
    keep_tool, a hash; `seed` is no longer used and stays for callers). The dream calls this nightly; it takes minutes on
    40 days of events. With exclude_rx, every event
    within exclude_window seconds of a touch the rule names, in the same session, is left out (bank_marks). With
    verdict_file, his corrections, repeats and praise come from that file alone (26 Sep: the capture had read hook
    feedback as his words, so the verdict events in the older event files are mostly not his). With skip_session,
    a function of the 8-character session key, that session's events are left out (27 Sep: the benches' headless
    test workers were a quarter to a third of the store's memory reads, recalled and served events; run_graph
    passes session_kinds.kind(s) == 'test'). The events on disk are untouched."""
    paths = _paths(events)
    if verdict_file and os.path.exists(verdict_file):
        vf = os.path.normcase(os.path.abspath(verdict_file))
        paths = [p for p in paths if os.path.normcase(os.path.abspath(p)) != vf] + [verdict_file]
    else:
        vf = None
    marks = bank_marks(paths, exclude_rx) if exclude_rx else {}
    n_out = 0
    skip_cache, n_skip = {}, 0
    nid, names, sid = {}, [], {}
    nlines = 0
    for path in paths:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 24), b""):
                nlines += chunk.count(b"\n") + 1
    S = np.empty(nlines, np.int64)
    TS = np.empty(nlines, np.float64)
    ND = np.empty(nlines, np.int64)
    n = 0
    seen_v = set()
    for path in paths:
        from_vf = vf is not None and os.path.normcase(os.path.abspath(path)) == vf
        with open(path, encoding="utf-8") as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                except Exception:
                    continue
                if vf is not None and (e["kind"] == "verdict") != from_vf:
                    continue                 # his reactions only from the verdict file, and nothing else from it
                if from_vf:
                    kv = (e["session"], round(e["ts"], 2), e["id"])
                    if kv in seen_v:
                        continue             # the re-derivation and the hourly capture can both hold one prompt
                    seen_v.add(kv)
                if JUNK.search(e["id"]) or (until is not None and e["ts"] >= until):
                    continue
                if skip_session is not None:
                    sk = skip_cache.get(e["session"])
                    if sk is None:
                        sk = skip_cache[e["session"]] = bool(skip_session(e["session"]))
                    if sk:
                        n_skip += 1
                        continue
                if marks and near_mark(marks, e["session"], e["ts"], exclude_window):
                    n_out += 1
                    continue
                x = e["kind"] + ":" + e["id"]
                i = nid.get(x)
                if i is None:
                    i = nid[x] = len(names)
                    names.append(x)
                j = sid.get(e["session"])
                if j is None:
                    j = sid[e["session"]] = len(sid)
                S[n], TS[n], ND[n] = j, e["ts"], i
                n += 1
    S, TS, ND = S[:n], TS[:n], ND[:n]
    # An event read twice is one event (28 Sep, account03/B12 R12). A fork subagent's transcript carries the parent's
    # records under the same uuid, and one hook call can be written as two attachments at the same millisecond: 2.5
    # percent of the log's lines repeat another line exactly. A capture interrupted before its state is saved would add
    # its tail again the same way. The same session, time and node count once; which copy stays does not matter.
    n_repeat = 0
    if collapse_repeats and n > 1:
        o = np.lexsort((ND, TS, S))
        keep = np.ones(n, bool)
        keep[1:] = (S[o[1:]] != S[o[:-1]]) | (TS[o[1:]] != TS[o[:-1]]) | (ND[o[1:]] != ND[o[:-1]])
        n_repeat = int(n - int(keep.sum()))
        if n_repeat:
            idx = np.sort(o[keep])
            S, TS, ND = S[idx], TS[idx], ND[idx]
            n = len(S)
    if ERR_ANY not in nid:
        nid[ERR_ANY] = len(names)
        names.append(ERR_ANY)
    ERR = nid[ERR_ANY]
    NN = len(names)
    order = np.lexsort((TS, S))
    S, TS, ND = S[order], TS[order], ND[order]
    bounds = np.flatnonzero(np.diff(S)) + 1
    starts = np.concatenate(([0], bounds)).tolist()
    ends = np.concatenate((bounds, [len(S)])).tolist()
    kind = [x.split(":", 1)[0] for x in names]
    # hubs: items named in more than HUB of all sessions
    ns = np.unique((ND << 32) | S)
    sess_count = np.bincount(ns >> 32, minlength=NN)
    hubs = set(np.flatnonzero(sess_count > hub * max(1, len(starts))).tolist())
    keys, co = [], []
    kw, cw = [], []                  # per-pair weights when edges fade with age (half_life_days)
    kt = []                          # per-pair moments, float32 seconds after t_base (first and last seen, 27 Sep)
    t_base = float(TS.min()) if len(TS) else 0.0
    decay = float(half_life_days) * 86400.0 if half_life_days else 0.0
    t_ref = float(until) if until is not None else (float(TS[:n].max()) if n else 0.0)
    uses = collections.defaultdict(list)
    tkinds = set(TARGET_KINDS)
    ftouch = set(FIRST_TOUCH)
    co_ok = np.array([kd in CO_KINDS for kd in kind])
    sess_names = [None] * len(sid)
    for sname, j in sid.items():
        sess_names[j] = sname
    ft_since = (float(TS.max()) if len(TS) else 0.0) - FIRST_TOUCH_KEEP_DAYS * 86400.0
    first_touch = {}
    for lo, hi in zip(starts, ends):
        seen = set()
        s_key = sess_names[int(S[lo])] if hi > lo else None
        for j in range(lo, hi):
            x = int(ND[j])
            kd = kind[x]
            is_t = kd in tkinds and (kd not in ftouch or x not in seen)
            seen.add(x)
            if not is_t:
                continue
            t = TS[j]
            uses[x].append(float(t))
            if kd == "tool" and not keep_tool(s_key, float(t), names[x], tool_rate):
                continue
            ctx = []
            got = set()
            i = j - 1
            while i >= lo and t - TS[i] <= window and len(ctx) < k:
                y = int(ND[i])
                if TS[i] < t and y not in hubs and y not in got:
                    got.add(y)
                    ctx.append(y)
                i -= 1
            if ctx:
                if decay and kd == "verdict" and verdict_days and t_ref - t > verdict_days * 86400.0:
                    continue                 # his corrections: the last `verdict_days` at full weight, older ones not at all
                c = np.array(ctx, dtype=np.int64)
                keys.append((c << 32) | x)
                kt.append(np.full(len(c), t - t_base, dtype=np.float32))
                if decay:
                    kw.append(np.full(len(c), 1.0 if kd == "verdict" else 0.5 ** (max(0.0, t_ref - t) / decay)))
                if kd == "err":
                    keys.append((c << 32) | ERR)
                    kt.append(kt[-1])
                    if decay:
                        kw.append(kw[-1])
        # what this session has touched of the first-touch kinds, for the fast layer: past the store's last event a
        # target the session touched before is a repeat, however far back (the fast layer reads 8 MiB back only)
        if hi > lo and float(TS[hi - 1]) >= ft_since:
            first_touch[s_key] = sorted(names[y] for y in seen if kind[y] in ftouch)
    if keys:
        allk = np.concatenate(keys)
        del keys
        pk, inv = np.unique(allk, return_inverse=True)
        del allk
        pc = np.bincount(inv, weights=np.concatenate(kw)) if decay else np.bincount(inv).astype(np.float64)
        del kw
        allt = np.concatenate(kt)
        del kt
        p_first = np.full(len(pk), np.inf, np.float32)
        p_last = np.full(len(pk), -np.inf, np.float32)
        np.minimum.at(p_first, inv, allt)
        np.maximum.at(p_last, inv, allt)
        del allt, inv
    else:
        pk, pc = np.zeros(0, np.int64), np.zeros(0)
        p_first = p_last = np.zeros(0, np.float32)
    sel = np.flatnonzero(co_ok[ND])
    for lag in range(1, 9):
        prev = np.maximum(sel - lag, 0)
        ok = (sel - lag >= 0) & (S[prev] == S[sel]) & (TS[sel] - TS[prev] <= window) & co_ok[ND[prev]] & \
             (ND[prev] != ND[sel])
        if ok.any():
            x1, x0 = ND[sel[ok]], ND[prev[ok]]
            co.append((x0 << 32) | x1)
            co.append((x1 << 32) | x0)
            if decay:
                w = 0.5 ** (np.maximum(0.0, t_ref - TS[sel[ok]]) / decay)
                cw.append(w)
                cw.append(w)
    if decay:
        ck, cc = _merge_w(np.concatenate(co) if co else np.zeros(0, np.int64), np.concatenate(cw) if cw else np.zeros(0))
    else:
        ck, cc = _merge(np.zeros(0, np.int64), np.zeros(0), np.concatenate(co) if co else np.zeros(0, np.int64))
    out_sum = np.bincount(pk >> 32, weights=pc, minlength=NN)
    in_w = np.bincount(pk & 0xFFFFFFFF, weights=pc, minlength=NN)
    os.makedirs(out, exist_ok=True)
    np.savez(os.path.join(out, "edges.npz"), keys=pk, cnt=pc, co_keys=ck, co_cnt=cc, out_sum=out_sum, in_w=in_w,
             p_first=p_first, p_last=p_last, t_base=np.array(t_base))
    u_node, u_last, u_old_n, u_old_first = [], [], [], []
    for x, ts in uses.items():
        ts.sort()
        u_node.append(x)
        u_last.append(ts[-KEEP_USES:])
        u_old_n.append(max(0, len(ts) - KEEP_USES))
        u_old_first.append(ts[0])
    meta = {"built": time.time(), "until": until if until is not None else float(TS.max()) if len(TS) else 0.0,
            "events": n, "sessions": len(starts), "window_s": window, "k": k, "hub": hub, "tool_rate": tool_rate,
            "hubs": sorted(names[h] for h in hubs), "pairs": int(len(pk)), "co_pairs": int(len(ck)),
            "total_w": float(pc.sum()), "excluded_sessions": len(marks), "excluded_events": n_out,
            "skipped_sessions": sum(1 for v in skip_cache.values() if v), "skipped_events": n_skip,
            "exclude_rule": exclude_rx or "", "exclude_window_s": exclude_window if exclude_rx else 0,
            "half_life_days": half_life_days, "verdict_file": os.path.basename(verdict_file) if vf else "",
            "verdict_events": len(seen_v), "tool_sampler": "hash", "first_touch_sessions": len(first_touch),
            "repeats_collapsed": n_repeat if collapse_repeats else None}
    with open(os.path.join(out, "first_touch.json"), "w", encoding="utf-8") as f:
        json.dump({"until": meta["until"], "keep_days": FIRST_TOUCH_KEEP_DAYS, "sessions": first_touch}, f)
    with open(os.path.join(out, "nodes.json"), "w", encoding="utf-8") as f:
        json.dump({"names": names, "meta": meta}, f)
    with open(os.path.join(out, "uses.json"), "w", encoding="utf-8") as f:
        json.dump({"node": u_node, "last": u_last, "old_n": u_old_n, "old_first": u_old_first}, f)
    return meta


class GraphStore:
    """The built store, read-only. rows(j) and co(j) are array slices; subgraph() feeds the membrane."""

    def __init__(self, path):
        z = np.load(os.path.join(path, "edges.npz"))
        self.keys, self.cnt = z["keys"], z["cnt"]
        self.co_keys, self.co_cnt = z["co_keys"], z["co_cnt"]
        self.out_sum, self.in_w = z["out_sum"], z["in_w"]
        # each edge's first and last moment (27 Sep); a store built before then has none
        self.p_first = z["p_first"] if "p_first" in z.files else None
        self.p_last = z["p_last"] if "p_last" in z.files else None
        self.t_base = float(z["t_base"]) if "t_base" in z.files else 0.0
        d = json.load(open(os.path.join(path, "nodes.json"), encoding="utf-8"))
        self.names, self.meta = d["names"], d["meta"]
        self.total_w = float(self.meta["total_w"])
        self.id = {n: i for i, n in enumerate(self.names)}
        u = json.load(open(os.path.join(path, "uses.json"), encoding="utf-8"))
        self.uses = {x: (last, on, of) for x, last, on, of in zip(u["node"], u["last"], u["old_n"], u["old_first"])}

    def times(self, k):
        """(first, last) epoch seconds of the edges at positions k (an array), or (None, None) for an older store."""
        if self.p_first is None:
            return None, None
        return self.p_first[k].astype(np.float64) + self.t_base, self.p_last[k].astype(np.float64) + self.t_base

    def _slice(self, keys, j):
        lo = np.searchsorted(keys, np.int64(j) << 32, "left")
        hi = np.searchsorted(keys, (np.int64(j) + 1) << 32, "left")
        return lo, hi

    def rows(self, j, cap=300):
        """Targets of item j with their counts, the strongest `cap` by count x association."""
        lo, hi = self._slice(self.keys, j)
        xs = (self.keys[lo:hi] & 0xFFFFFFFF).astype(np.int64)
        w = self.cnt[lo:hi]
        if len(xs) > cap:
            p_i = self.in_w[xs] / self.total_w
            s = np.log((w / self.out_sum[j]) / p_i) * np.log1p(w)
            top = np.argpartition(-s, cap)[:cap]
            xs, w = xs[top], w[top]
        return xs, w

    def co(self, j, cap=10):
        lo, hi = self._slice(self.co_keys, j)
        xs = (self.co_keys[lo:hi] & 0xFFFFFFFF).astype(np.int64)
        w = self.co_cnt[lo:hi]
        o = np.argsort(-w)[:cap]
        return xs[o], w[o]

    def subgraph(self, seed, membrane_mod, cap=300, hop2=10):
        """A membrane.Graph with the seed's rows, the rows of each seed's `hop2` strongest first-hop targets (the membrane's
        second hop runs from what the first hop lit), true row sums and totals, and node uses. Edge weights are raw
        counts; the membrane takes the association itself."""
        g = membrane_mod.Graph()
        ids = [self.id[s] for s in dict.fromkeys(seed) if s in self.id]
        involved = set(ids)
        second = set()

        def etype(x):
            # context, not seq: every distinct item of the W seconds before a target gets equal credit, with no order
            # or lag in it. NODE_CLASSES.md's seq edge (exp(-lag/tau) from A to B) was measured on 27-28 Sep
            # (study_lag_edges.py, THREE_DAY_RUN 28 Sep): at the membrane's 2 percent bar it lost to equal-credit co-use
            # (38 right of 507 lines on 17 sessions, against 41 of 454 on 23); it found more right lines only when
            # serving four or more times as many lines as the membrane does. So the store keeps equal credit and names it
            # for what it is (scheduled audit account03/B03 r51).
            return "led_to" if self.names[x].startswith("err:") else "context"
        for j in ids:
            xs, w = self.rows(j, cap)
            for x, ww in zip(xs.tolist(), w.tolist()):
                g.add_edge(self.names[j], self.names[x], ww, etype(x))
                involved.add(x)
            if hop2 and len(xs):
                s = np.log((w / self.out_sum[j]) / (self.in_w[xs] / self.total_w)) * np.log1p(w)
                o = np.argsort(-s)[:hop2]
                second.update(int(x) for x, v in zip(xs[o].tolist(), s[o].tolist()) if v > 0)
        for j in second - set(ids):
            xs, w = self.rows(j, 60)
            for x, ww in zip(xs.tolist(), w.tolist()):
                g.add_edge(self.names[j], self.names[x], ww, etype(x))
                involved.add(x)
            involved.add(j)
        # the true statistics, not the partial rows: P(i|j) needs j's whole outgoing weight, P(i) the global in-weight
        g.out_sum = {self.names[j]: float(self.out_sum[j]) for j in involved}
        g.in_w = collections.defaultdict(float, {self.names[x]: float(self.in_w[x]) for x in involved})
        g.total_w = self.total_w
        for x in involved:
            nm = self.names[x]
            u = self.uses.get(x)
            g.add_node(nm, node_class(nm))
            if u:
                last, old_n, old_first = u
                g.uses[nm] = list(last)
                if old_n:
                    g.older[nm] = (old_n, old_first)
        return g


def selftest():
    """The dataflow, end to end, on a synthetic corpus: events -> store -> subgraph -> activation."""
    import tempfile
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(here))   # brain_graph/membrane.py
    sys.path.insert(0, here)
    fails = []

    def check(ok, what):
        print(("ok    " if ok else "WRONG ") + what)
        if not ok:
            fails.append(what)
    tmp = tempfile.mkdtemp(prefix="graph_store_selftest_")
    ev = os.path.join(tmp, "events.jsonl")
    t0 = 1.79e9
    lines = []
    # 30 sessions: 10 walk folder A, run a Bash ssh to box 3, and fail; 10 touch folder B and read memory m_b; 10 noise
    for s in range(30):
        sid = "%08x" % (0xabc00000 + s)
        t = t0 + s * 5000
        def emit(dt, kind, x):
            lines.append(json.dumps({"ts": t + dt, "session": sid, "kind": kind, "id": x}))
        emit(0, "kw", "hello")
        if s < 10:
            emit(10, "dir", "~/proj_a/src")
            emit(11, "file", "~/proj_a/src/run.py")
            emit(20, "tool", "Bash:ssh")
            emit(20, "host", "build-box")
            emit(25, "err", "Bash:ssh:exit255")
        elif s < 20:
            emit(10, "dir", "~/proj_b/notes")
            emit(30, "kw", "thumbnail")
            emit(40, "mem", "m_b_2026_09_26")
        else:
            emit(10, "dir", "~/noise_%d" % s)
            emit(20, "tool", "Read")
    # a two-hop chain: the word "alpha" leads to memory m_x in ten sessions, m_x leads to memory m_y in ten others, and
    # "alpha" never meets m_y; a word has no co-activation neighbours, so only the first hop's own rows can reach m_y
    for s in range(20):
        sid = "%08x" % (0xabd00000 + s)
        t = t0 + 200000 + s * 5000
        def emit2(dt, kind, x):
            lines.append(json.dumps({"ts": t + dt, "session": sid, "kind": kind, "id": x}))
        if s < 10:
            emit2(0, "kw", "alpha")
            emit2(30, "mem", "m_x_2026_09_26")
        else:
            emit2(0, "mem", "m_x_2026_09_26")
            emit2(30, "mem", "m_y_2026_09_26")
    open(ev, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    meta = build(ev, os.path.join(tmp, "store"), window=600, k=40, hub=0.9, tool_rate=1.0)
    check(meta["pairs"] > 0 and meta["sessions"] == 50, "build: %d pairs from %d sessions" % (meta["pairs"], meta["sessions"]))
    # bank work: a session opens a bank file, reads a memory two minutes later and another three hours later
    ev_b = os.path.join(tmp, "events_bank.jsonl")
    tb = t0 + 900000
    with open(ev_b, "w", encoding="utf-8") as f:
        f.write(open(ev, encoding="utf-8").read())
        for dt, kind, x in ((0, "file", "~/brain/bank100/questions.jsonl"), (120, "mem", "m_bankans_2026_09_26"),
                            (10800, "mem", "m_later_2026_09_26")):
            f.write(json.dumps({"ts": tb + dt, "session": "bbbb0001", "kind": kind, "id": x}) + "\n")
    meta_b = build(ev_b, os.path.join(tmp, "store_b"), window=600, k=40, hub=0.9, tool_rate=1.0, exclude_rx=BANK_RX)
    st_b = GraphStore(os.path.join(tmp, "store_b"))
    check("mem:m_bankans_2026_09_26" not in st_b.id and "file:~/brain/bank100/questions.jsonl" not in st_b.id
          and "mem:m_later_2026_09_26" in st_b.id and meta_b["excluded_events"] == 2,
          "bank rule: the bank file and the read two minutes after it are left out, the read three hours later kept "
          "(%d events left out)" % meta_b["excluded_events"])
    st = GraphStore(os.path.join(tmp, "store"))
    a_dir = st.id["dir:~/proj_a/src"]
    xs, w = st.rows(a_dir)
    tg = {st.names[x]: ww for x, ww in zip(xs.tolist(), w.tolist())}
    check(tg.get("err:Bash:ssh:exit255") == 10 and tg.get(ERR_ANY) == 10,
          "folder A -> the ssh failure, 10 times, and -> err:ANY 10 times (%s)" % tg.get("err:Bash:ssh:exit255"))
    check("mem:m_b_2026_09_26" not in tg, "folder A never leads to memory m_b")
    try:
        import membrane
    except ImportError:
        membrane = None
    if membrane is None:
        check(False, "membrane.py importable beside graph_store.py")
    else:
        g = st.subgraph(["dir:~/proj_a/src", "tool:Bash:ssh"], membrane)
        m = membrane.Membrane(g, k=2, threshold=-50.0, actionable=frozenset({"err", "mem"}))
        items = m.step(["dir:~/proj_a/src", "tool:Bash:ssh"], t0 + 200000)
        check(bool(items) and items[0]["node"] in ("err:Bash:ssh:exit255", ERR_ANY),
              "seed folder A + ssh -> the membrane raises the ssh failure first (%s)" % [i["node"] for i in items])
        g2 = st.subgraph(["kw:thumbnail", "dir:~/proj_b/notes"], membrane)
        m2 = membrane.Membrane(g2, k=2, threshold=-50.0, actionable=frozenset({"err", "mem"}))
        items2 = m2.step(["kw:thumbnail", "dir:~/proj_b/notes"], t0 + 200000)
        check(bool(items2) and items2[0]["node"] == "mem:m_b_2026_09_26",
              "seed 'thumbnail' + folder B -> memory m_b (%s)" % [i["node"] for i in items2])
        g3 = st.subgraph(["dir:~/noise_25"], membrane)
        m3 = membrane.Membrane(g3, k=2, threshold=0.5, actionable=frozenset({"err", "mem"}))
        check(not m3.step(["dir:~/noise_25"], t0 + 200000), "a seed with no history raises nothing")
        g4 = st.subgraph(["kw:alpha"], membrane)
        m4 = membrane.Membrane(g4, k=2, threshold=-50.0, actionable=frozenset({"mem"}), hop2=0.5)
        act4, why4 = m4.activation(["kw:alpha"], t0 + 400000)
        via = [j for _c, j, _e in why4.get("mem:m_y_2026_09_26", [])]
        check("mem:m_y_2026_09_26" in act4 and "mem:m_x_2026_09_26" in via,
              "two hops: 'alpha' -> m_x -> m_y reaches m_y through m_x (%s via %s)" % (
                  round(act4.get("mem:m_y_2026_09_26", 0.0), 3), via))
        m5 = membrane.Membrane(g4, k=2, threshold=-50.0, actionable=frozenset({"mem"}), hop2=0.0)
        act5, _w5 = m5.activation(["kw:alpha"], t0 + 400000)
        check("mem:m_y_2026_09_26" not in act5, "with the second hop off, 'alpha' does not reach m_y")
    print("PASS" if not fails else "FAIL - %d wrong" % len(fails))
    return not fails


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build")
    b.add_argument("--events", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--until", type=float, default=None)
    b.add_argument("--exclude-banks", action="store_true",
                   help="leave out the hour either side of every touch of the recall banks or their harnesses")
    b.add_argument("--include-test-workers", action="store_true",
                   help="keep the sessions session_kinds calls 'test' (the benches' own workers); by default "
                        "they are left out, as run_graph.py leaves them out of the scheduled builds")
    q = sub.add_parser("query")
    q.add_argument("--store", required=True)
    q.add_argument("--seed", nargs="+", required=True)
    q.add_argument("--k", type=int, default=5)
    sub.add_parser("selftest")
    a = ap.parse_args()
    if a.cmd == "build":
        # the scheduled builds' population rule (27 Sep, audit account03/B15 R9): this command used to keep the test
        # workers run_graph.py leaves out, so one event log gave two different stores
        skip, sk = None, None
        if not a.include_test_workers:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import session_kinds as sk
            skip = lambda s: sk.kind(s) == "test"
        meta = build(a.events, a.out, a.until, exclude_rx=BANK_RX if a.exclude_banks else None, skip_session=skip)
        if sk is not None:
            sk.save()
        print(json.dumps({k: v for k, v in meta.items() if k != "hubs"}))
    elif a.cmd == "query":
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path[:0] = [here, os.path.dirname(here)]
        import membrane
        st = GraphStore(a.store)
        g = st.subgraph(a.seed, membrane)
        m = membrane.Membrane(g, k=a.k, threshold=-50.0)
        for it in m.step(a.seed, time.time()):
            print("%-60s %-8s %7.3f  %s" % (it["node"][:60], it["class"], it["activation"], it["hint"][:120]))
    elif a.cmd == "selftest":
        sys.exit(0 if selftest() else 1)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
