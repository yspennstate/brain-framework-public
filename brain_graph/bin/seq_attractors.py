"""seq_attractors.py - mistakes stored as ordered chains and cued by what a session just did (26 Sep, claude-825b1305).

His voice note of 26 Sep: "if an attractor lights up or a sequence lights up and previously that led to a mistake ...
then that stuff should be given to the agent automatically." The first attractors (attractors.py: Louvain communities on
co-activation) were measured walk-forward that day: a lit community was followed by one of its mistakes 2.7% of the time,
because a community of 400 shell-command nodes lights in every session. The literature the brain agents collected says
why and what to do instead (brain_graph/literature/SYNTHESIS.md): a symmetric pattern that any member completes is a
spurious attractor (Hopfield 1982); a sequence is stored with asymmetric weights, one step to the next (Sompolinsky and
Kanter 1986, Kleinfeld 1986), and recalled by matching its first steps.

So: an ordered pair (a, then b) of specific items - files, folders, projects, repositories, hosts, stores, dials,
programs run, memories - is a chain when it is among the last three specific steps before an error, within five
minutes. Each chain is counted two ways over the same events: how often it came right before that error, and how often
it happened at all. precision = before / at all; a chain is kept when it happened at least `min_total` times, came before
the error at least `min_err` times, and precision is at least `min_prec`. Generic steps (a tool name alone, a shell
keyword, the agent's words) are not chain links: they happen everywhere and would make every chain lit.

    python seq_attractors.py mine [--until EPOCH] [--out FILE]      # writes live/seq_attractors.json
    python seq_attractors.py study --cut EPOCH                      # walk-forward: mined before the cut, scored after
"""
import argparse
import collections
import json
import os
import sys
import time

HOME = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph")
EVENTS = os.path.join(HOME, "events_touch.jsonl")
OUT = os.path.join(HOME, "live", "seq_attractors.json")
SPECIFIC = ("file", "dir", "proj", "repo", "host", "store", "dial", "mem", "memw", "cmd")
GENERIC_CMD = {"cd", "ls", "cat", "head", "tail", "grep", "echo", "for", "do", "done", "then", "fi", "if", "python",
               "bash", "sed", "awk", "wc", "find", "sort", "cut", "tr", "xargs", "sleep", "date", "true", "exit", "set",
               "export", "mkdir", "cp", "mv", "rm", "git", "powershell", "select-object", "where-object",
               "foreach-object", "get-content", "format-table", "get-childitem", "write-output", "timeout", "python3"}
WINDOW = 300.0
K_PREV = 3


def specific(kind, nid):
    if kind not in SPECIFIC:
        return False
    if kind == "cmd" and nid.split()[0].lower() in GENERIC_CMD:
        return False
    if kind in ("dir", "proj") and nid.count("/") <= 1:
        return False
    return True


def events(files, t_from=None, t_to=None):
    """(session, ts, kind, id) in file order; a cheap ts read skips what is outside [t_from, t_to)."""
    for path in files:
        if not os.path.exists(path):
            continue
        with open(path, "rb") as fh:
            for raw in fh:
                try:
                    i = raw.index(b",")
                    ts = float(raw[7:i])
                except (ValueError, IndexError):
                    continue
                if (t_from is not None and ts < t_from) or (t_to is not None and ts >= t_to):
                    continue
                try:
                    o = json.loads(raw)
                except ValueError:
                    continue
                yield o["session"], o["ts"], o["kind"], o["id"]


def by_session(files, t_from=None, t_to=None):
    by = collections.defaultdict(list)
    for s, ts, kd, nid in events(files, t_from, t_to):
        by[s].append((ts, kd, nid))
    for s in by:
        by[s].sort()
    return by


def chains_before(seq, i):
    """The ordered pairs among the last K_PREV distinct specific steps before position i, within WINDOW."""
    t = seq[i][0]
    prev, seen = [], set()
    j = i - 1
    while j >= 0 and len(prev) < K_PREV and t - seq[j][0] <= WINDOW:
        _ts, kd, nid = seq[j]
        nm = "%s:%s" % (kd, nid)
        if specific(kd, nid) and nm not in seen:
            seen.add(nm)
            prev.append(nm)
        j -= 1
    prev.reverse()                                  # oldest first: a then b
    return [(prev[x], prev[y]) for x in range(len(prev)) for y in range(x + 1, len(prev))]


def mine(files=(EVENTS,), until=None, min_err=3, min_total=5, min_prec=0.15):
    t0 = time.time()
    by = by_session(files, t_to=until)
    before = collections.defaultdict(collections.Counter)      # (a, b) -> {err: n}
    for s, seq in by.items():
        for i, (ts, kd, nid) in enumerate(seq):
            if kd == "err":
                for ch in set(chains_before(seq, i)):
                    before[ch]["err:" + nid] += 1
    cand = {ch for ch, c in before.items() if max(c.values()) >= min_err}
    total = collections.Counter()
    # a chain 'happens' each time its second step occurs with its first among the K_PREV specific steps before it
    for s, seq in by.items():
        spec = [(ts, "%s:%s" % (kd, nid)) for ts, kd, nid in seq if specific(kd, nid)]
        for i, (t, b) in enumerate(spec):
            seen = set()
            for j in range(i - 1, max(-1, i - 1 - K_PREV), -1):
                if t - spec[j][0] > WINDOW:
                    break
                a = spec[j][1]
                if a != b and (a, b) not in seen and (a, b) in cand:
                    seen.add((a, b))
                    total[(a, b)] += 1
    rows = []
    for ch in cand:
        n_all = total.get(ch, 0)
        if n_all < min_total:
            continue
        err, n = before[ch].most_common(1)[0]
        prec = n / n_all
        if n >= min_err and prec >= min_prec:
            rows.append({"a": ch[0], "b": ch[1], "err": err, "before": n, "total": n_all, "prec": round(prec, 4)})
    rows.sort(key=lambda r: (-r["prec"], -r["before"]))
    meta = {"built": time.time(), "until": until, "sessions": len(by), "candidates": len(cand), "chains": len(rows),
            "window_s": WINDOW, "k_prev": K_PREV, "min_err": min_err, "min_total": min_total, "min_prec": min_prec,
            "seconds": round(time.time() - t0)}
    return {"meta": meta, "chains": rows}


def lit(chains, recent):
    """recent: the session's specific steps, oldest first. Chains whose a came before b among the last K_PREV + 2."""
    idx = {}
    for r in chains:
        idx.setdefault(r["b"], []).append(r)
    tail = recent[-(K_PREV + 2):]
    out = []
    for j, b in enumerate(tail):
        for r in idx.get(b, ()):
            if r["a"] in tail[:j]:
                out.append(r)
    out.sort(key=lambda r: -r["prec"])
    return out


def study(cut, days=3.0, horizon=1800.0, files=(EVENTS,)):
    m = mine(files, until=cut)
    chains = m["chains"]
    by = by_session(files, t_from=cut - 700, t_to=cut + days * 86400)
    fired = right = moments = 0
    base_err = 0
    per = collections.Counter()
    for s, seq in by.items():
        spec_hist = []
        n_tool = 0
        for i, (ts, kd, nid) in enumerate(seq):
            if ts >= cut and kd == "tool":
                n_tool += 1
                if n_tool % 4 == 0:
                    moments += 1
                    recent = [x for t2, x in spec_hist if ts - t2 <= WINDOW]
                    hits = lit(chains, recent)
                    errs = {"err:" + n2 for t2, k2, n2 in seq[i:] if k2 == "err" and ts < t2 <= ts + horizon}
                    base_err += bool(errs)
                    if hits:
                        fired += 1
                        ok = hits[0]["err"] in errs
                        right += ok
                        per[hits[0]["err"]] += 1
            if specific(kd, nid):
                spec_hist.append((ts, "%s:%s" % (kd, nid)))
    return {"cut": time.strftime("%Y-%m-%d %H:%M", time.localtime(cut)), "chains": len(chains), "mine": m["meta"],
            "moments": moments, "any_error_follows": round(base_err / max(1, moments), 4), "fired": fired,
            "fire_rate": round(fired / max(1, moments), 4), "right": right,
            "precision": round(right / max(1, fired), 4), "top_errors": per.most_common(5)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("mine", "study"))
    ap.add_argument("--until", type=float, default=None)
    ap.add_argument("--cut", type=float, default=None)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    if a.mode == "mine":
        m = mine(until=a.until)
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        tmp = a.out + ".tmp"
        json.dump(m, open(tmp, "w", encoding="utf-8"))
        os.replace(tmp, a.out)
        print(json.dumps(m["meta"]))
        for r in m["chains"][:8]:
            print("  %.2f  %s -> %s -> %s (%d of %d)" % (r["prec"], r["a"][:40], r["b"][:40], r["err"][:40], r["before"],
                                                      r["total"]))
    else:
        print(json.dumps(study(a.cut), indent=1))


if __name__ == "__main__":
    main()
