#!/usr/bin/env python3
"""brain_status.py - a one-command observer of the brain's own state (the owner's "build useful observers,
audit that data is flowing"). Reads everything, writes nothing, wires to nothing - safe to run any time.

Ties the mistake-mitigation / humility / memory layer into a single view: how big the corpus is, which
hooks are live, how many reflexes, what the guards have caught, where confidence is (mis)calibrated, and
what work is still open. A session (or the owner) runs it to see how the brain is holding up.

  python brain_status.py
"""
import os, io, json, re, time
from collections import Counter

try:
    from mistake_graph_status import summarize_path as _summarize_mistake_watch
except Exception:
    _summarize_mistake_watch = None

HOME = os.path.expanduser("~")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
DR = os.path.join(HOME, ".claude", "dreaming")
BRAIN = os.path.join(HOME, "brain-repo", "01_ai_consciousness", "brain")


def _n_lines(path, pred):
    try:
        return sum(1 for ln in io.open(path, encoding="utf-8") if pred(ln))
    except Exception:
        return 0


def _jsonl_recent(path, hours=24):
    n = 0
    cut = time.time() - hours * 3600
    try:
        for ln in io.open(path, encoding="utf-8"):
            try:
                if json.loads(ln).get("ts", 0) >= cut:
                    n += 1
            except Exception:
                pass
    except Exception:
        pass
    return n


def _print_mistake_watch_status():
    try:
        if _summarize_mistake_watch is None:
            raise RuntimeError("status reader unavailable")
        summary = _summarize_mistake_watch(
            os.path.join(DR, ".mistake_graph_status.json"))
        line = summary.line
    except Exception:
        line = ("MISTAKES  FEED UNVERIFIED - no valid latest-watch status is "
                "available; do not infer that recurrence monitoring is quiet.")
    print("\n" + line)


def graph_health():
    """Structural health of the association graph the spreading-activation recall walks: is it one
    connected fabric, or is a slice of memory unreachable? Reads the live spread_edges.tsv. Reachability
    was taken 95.2%->99.6% single-component on 2026-07-10 (orphan+island bridging); this makes that
    trackable and flags re-accumulation (new orphans added faster than the nightly bridges them)."""
    edges_path = os.path.join(DR, "spread_edges.tsv")
    if not os.path.exists(edges_path):
        return
    adj = {}
    n_edges = 0
    try:
        with io.open(edges_path, encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) < 3 or p[0] == "a":
                    continue
                a, b = p[0], p[1]
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set()).add(a)
                n_edges += 1
    except Exception:
        return
    if not adj:
        return
    seen, comps = set(), []
    for n in adj:
        if n in seen:
            continue
        stack, members = [n], []
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            members.append(x)
            stack.extend(adj[x] - seen)
        comps.append(members)
    comps.sort(key=len, reverse=True)
    nodes = len(adj)
    biggest = len(comps[0]) if comps else 0
    # unreachable = in the graph file but only self-loops / nothing: essentially none after bridging.
    # The real "invisible" count is memories with NO node in adj at all vs the corpus - but adj only holds
    # nodes that appear in an edge, so a corpus memory absent from adj is the true orphan. Compare to corpus.
    _sub = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
            "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES", "CONSCIENCE", "CURIOSITY"}
    # Auto-generated watcher heartbeat logs (build_watchdog_W##_quiet / inspector_W##_quiet, ~206 from one
    # 05-25 session) are checkpoint noise, not recall-memories - like substrate pages and dream journals, they
    # were never meant to be graph nodes. Counting them as "invisible to recall" cried wolf (148 vs the real
    # ~22); a health line that overstates a gap ~7x trains the reader to ignore it - the exact heeding failure
    # this dashboard exists to catch. Categorize them (report the count) rather than conflate them with real
    # orphans, and compute absent by set membership (not corpus-minus-nodes) so an edged auto-log can't skew it.
    import re as _re
    _NOISE = _re.compile(r"_W\d+_quiet_\d{4}_\d{2}_\d{2}$|^build_watchdog_W\d+")
    try:
        _allmem = [f[:-3] for f in os.listdir(MEM)
                   if f.endswith(".md") and f[:-3] not in _sub and not f.startswith("dream_")
                   and not f[:-3].isupper()]   # all-caps = a substrate page (RECONSOLIDATION_QUEUE etc), not a
                                               # recall-memory; the hardcoded _sub above had gone stale and missed some
        auto_logs = sum(1 for m in _allmem if _NOISE.search(m))
        _real = set(m for m in _allmem if not _NOISE.search(m))
        corpus = len(_real)
        absent = sum(1 for m in _real if m not in adj)   # real recall-memories with zero edges - true orphans
    except Exception:
        _real = set(adj); corpus = nodes; absent = max(0, corpus - nodes); auto_logs = 0
    weak = sum(1 for n in adj if len(adj[n]) == 1 and n in _real)
    hubs = sorted(adj, key=lambda n: -len(adj[n]))[:3]
    print("\nGRAPH  %d of %d recall-memories edged, %d edges%s" % (
        corpus - absent, corpus, n_edges,
        ("  (+%d auto-log heartbeats: unreachable by design, not a gap)" % auto_logs) if auto_logs else ""))
    print("  connectivity  %.1f%% of edged nodes in one component  (%d components%s)" % (
        100.0 * biggest / nodes, len(comps),
        "" if len(comps) == 1 else ", " + str(len(comps) - 1) + " off-main"))
    print("  unreachable   %d recall-memories have NO edge (invisible to recall); %d hang by one" % (absent, weak))
    print("  top hubs      " + ", ".join("%s (%d)" % (h, len(adj[h])) for h in hubs))
    if len(comps) > 1:
        off_members = [m for c in comps[1:] for m in c]
        print("  off-main isl  %d memories in %d islands (walk can't reach; the auto-bridge's residue - name them so they can be hand-bridged): %s"
              % (len(off_members), len(comps) - 1, ", ".join(m[:32] for m in off_members[:14])))
    # modularity: connectivity says "reachable", this says "well-ORGANIZED" (topics cluster) vs hairball.
    # Reuses the loaded adj; lazy + fail-open so a missing networkx never breaks the dashboard. Baseline
    # 2026-07-11: Q~0.77 (brain_community_health.py has the full breakdown). A DROP = the graph de-organizing.
    try:
        import networkx as _nx
        _g = _nx.Graph()
        for _a, _nbrs in adj.items():
            for _b in _nbrs:
                _g.add_edge(_a, _b)
        _c = _nx.community.louvain_communities(_g, seed=42)
        _q = _nx.community.modularity(_g, _c)
        print("  modularity    Q=%.2f over %d communities (Q>0.3 = clear topic structure; drop = de-organizing)"
              % (_q, len(_c)))
    except Exception:
        pass


def dream_health():
    """Surface the last nightly dream's REM (consolidation) outcome + any component crashes - both were
    SILENT before (found 2026-07-11 by hand: a REM 'ConnectionRefused' lost a night's consolidation with
    no alert; a wired eval crashed every dream unnoticed). The dream IS the brain's consolidation, so a
    failing one degrades it quietly. Strictly read-only: reads the newest dream_*.out.json/.log in logs/,
    reports, never acts (no restart, no medicine)."""
    logs = os.path.join(DR, "logs")
    if not os.path.isdir(logs):
        return
    # stamp is date_time (20260710_193002); a REM retry adds a .retryN suffix but shares the base .log
    pat = re.compile(r"dream_(\d{8}_\d{6})(?:\.retry\d+)?\.out\.json$")
    cands = [(f, pat.match(f).group(1)) for f in os.listdir(logs) if pat.match(f)]
    if not cands:
        return
    latest, stamp = max(cands, key=lambda fs: os.path.getmtime(os.path.join(logs, fs[0])))
    rem = "unknown"
    try:
        raw = io.open(os.path.join(logs, latest), encoding="utf-8", errors="replace").read()
        i = raw.find("{")
        if i >= 0:
            j = json.loads(raw[i:])
            if j.get("is_error"):
                rem = "FAILED (%s)" % str(j.get("result") or "")[:48].replace("\n", " ")
            else:
                rem = "ok"
    except Exception:
        pass
    ncrash = 0
    try:
        txt = io.open(os.path.join(logs, "dream_%s.log" % stamp), encoding="utf-8", errors="replace").read()
        ncrash = txt.count("Traceback (most recent call last)")
    except Exception:
        pass
    mt = os.path.getmtime(os.path.join(logs, latest))
    when = time.strftime("%m-%d %H:%M", time.localtime(mt))
    flag = "  <- consolidation lost; check the REM retry" if rem not in ("ok", "unknown") else (
        "  <- a wired dream component threw" if ncrash else "")
    print("\nDREAM  last run %s: REM %s, %d component crash(es)%s" % (when, rem, ncrash, flag))
    # a STOPPED scheduler is the highest-impact silent failure - the whole brain stops refreshing. Cadence is
    # ~8-24h (nightly + naps), so >30h means the BrainDream task likely died; flag it (was itself unobserved).
    age_h = (time.time() - mt) / 3600.0
    if age_h > 30:
        print("  ** STALE: no dream in %.0fh - the BrainDream scheduler may be STOPPED; if so the brain "
              "silently stops refreshing indexes/salience/instincts/gists. Check the scheduled task." % age_h)


def main():
    print("=" * 64)
    print(" BRAIN STATUS  -  " + time.strftime("%Y-%m-%d %H:%M"))
    print("=" * 64)

    # corpus
    try:
        files = [f for f in os.listdir(MEM) if f.endswith(".md")]
        dates = sorted(re.search(r"_(\d{4})_(\d{2})_(\d{2})", f).group(0)[1:]
                       for f in files if re.search(r"_(\d{4})_(\d{2})_(\d{2})", f))
        print("\nMEMORY  %d files" % len(files) + (
            "  (%s -> %s)" % (dates[0], dates[-1]) if dates else ""))
        for idx, label in (("MEMORY.md", "live index"), ("INDEX_ARCHIVE.md", "archived")):
            k = _n_lines(os.path.join(MEM, idx), lambda l: l.startswith("- ["))
            if k:
                print("        %d %s lines" % (k, label))
    except Exception:
        pass

    _print_mistake_watch_status()

    # association-graph health (reachability of the corpus by spreading activation)
    try:
        graph_health()
    except Exception:
        pass

    # dream (consolidation) health - surface a silently-failed REM or a crashed dream component
    try:
        dream_health()
    except Exception:
        pass

    # stream integrity - streams well-formed, not just fresh (a corrupt stream breaks recall/dream silently)
    try:
        import stream_integrity as _si
        ok, present, issues = _si.summary()
        line = "\nSTREAMS  %d/%d data streams well-formed" % (ok, present)
        if issues:
            line += "  <-- ISSUE: " + ", ".join(issues[:4])
        print(line)
    except Exception:
        pass

    # The report-surface invariant must use durable owner-confirmed signs, never retired P&L magnitudes.
    # The earlier dashboard canary used old dollar anchors and therefore cried wolf even while the real guard
    # was correct.  Keep retract/discussion vocabulary in the cases: it is the adversarial shape that once
    # suppressed a fresh contradiction, but the underlying assertions here remain timeless and testable.
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(DR, "graph_fire"))
        import pre_report as _pr
        _durable_discuss = [
            "Setting the withdrawn anchor aside, his current strategy loses money every year.",
            "Per the old memory, he should sell puts and collect premium.",
            "Ignore the retracted note; <ticker> is linear -3x and its carry is just a negligible expense ratio.",
        ]
        _retired_rates = [
            "The grid backtest earns $180k per year.",
            "The grid backtest makes $1.9M per year.",
            "Account7 makes $40k per month.",
        ]
        _blocked = sum(1 for t in _durable_discuss if _pr.check(t).get("should_block"))
        _stale_blocks = sum(1 for t in _retired_rates if _pr.check(t).get("should_block"))
        if _blocked == len(_durable_discuss) and _stale_blocks == 0:
            print("\nSAFETY   durable contradictions survive discuss-vocab (%d/%d); retired rates stay advisory (0/%d blocked)" %
                  (_blocked, len(_durable_discuss), len(_retired_rates)))
        else:
            print("\nSAFETY   <-- REGRESSED: durable-discuss blocked %d/%d; retired-rate false blocks %d/%d - run referee_suite.py" %
                  (_blocked, len(_durable_discuss), _stale_blocks, len(_retired_rates)))
    except Exception:
        pass

    # hooks
    try:
        d = json.load(io.open(os.path.expanduser("~/.claude/settings.json"), encoding="utf-8"))
        rows = []
        for ev, gs in d.get("hooks", {}).items():
            names = [os.path.basename(a) for g in gs for h in g.get("hooks", []) for a in h.get("args", []) if a.endswith((".py", ".ps1"))]
            rows.append("  %-16s %s" % (ev, ", ".join(names)))
        print("\nHOOKS (%d events)" % len(rows))
        for r in rows:
            print(r)
    except Exception:
        pass

    # reflexes
    try:
        modes = Counter()
        with io.open(os.path.join(DR, "instincts.tsv"), encoding="utf-8") as f:
            next(f, None)
            for ln in f:
                p = ln.split("\t")
                if len(p) >= 2:
                    modes[p[1]] += 1
        if modes:
            print("\nGUT  %d reflexes  (%s)" % (sum(modes.values()),
                  ", ".join("%d %s" % (v, k) for k, v in modes.most_common())))
    except Exception:
        pass

    # what the guards have caught (recent + total)
    print("\nGUARDS")
    for path, label in ((os.path.join(DR, "outcomes.jsonl"), "felt-errors (ERN)"),
                        (os.path.join(DR, "reality_log.jsonl"), "runtime claims logged")):
        tot = _n_lines(path, lambda l: l.strip().startswith("{"))
        rec = _jsonl_recent(path)
        if tot:
            print("  %-24s %d total, %d in last 24h" % (label, tot, rec))
    # ungrounded-claim rate from reality_log
    try:
        rl = [json.loads(l) for l in io.open(os.path.join(DR, "reality_log.jsonl"), encoding="utf-8") if l.strip().startswith("{")]
        if rl:
            ung = sum(1 for r in rl if not r.get("grounded"))
            print("  %-24s %d/%d runtime claims had no in-turn observation" % ("(reality-monitor)", ung, len(rl)))
    except Exception:
        pass

    # the prompt archive (owner directive 2026-07-09: every prompt saved + classified, forever)
    try:
        pl = os.path.join(MEM, "prompts", "PROMPTS.jsonl")
        if os.path.exists(pl):
            tot = unc = 0
            for l in io.open(pl, encoding="utf-8", errors="replace"):
                if l.strip().startswith("{"):
                    tot += 1
                    if '"class_model"' not in l:
                        unc += 1
            print("  %-24s %d prompts archived, %d awaiting model classification" % ("prompt archive", tot, unc))
    except Exception:
        pass

    # the newer faculties, at a glance: what it's curious about, whether the conscience is carrying a
    # debt, and how the gut is doing against real failures (with the ERN's own false-alarm rate).
    print("\nFACULTIES")
    try:
        cur = io.open(os.path.join(BRAIN, "CURIOSITY.md"), encoding="utf-8").read()
        gaps = [l for l in cur.splitlines() if l.startswith("- **[")]
        if gaps:
            top = re.sub(r"\s*_\(pull [0-9.]+\)_\s*$", "", gaps[0]).replace("**", "").lstrip("- ")
            print("  curiosity     %d live gaps; sharpest: %s" % (len(gaps), top[:88]))
    except Exception:
        pass
    try:
        con = io.open(os.path.join(MEM, "CONSCIENCE.md"), encoding="utf-8").read()
        pat = [l for l in con.splitlines() if l.startswith("- PATTERN:")]
        debt = [l for l in con.splitlines() if l.startswith("- DEBT")]
        if pat:
            print("  conscience    standing PATTERN: " + re.sub(r"^- PATTERN:\s*", "", pat[0])[:82])
        elif debt:
            print("  conscience    recent debt: " + re.sub(r"^- DEBT(\s*\[[^\]]+\])?:\s*", "", debt[0])[:78])
        else:
            print("  conscience    ledger clean (no standing debt)")
    except Exception:
        print("  conscience    ledger clean (no standing debt)")
    try:
        el = io.open(os.path.join(DR, "ERN_LEARNING.md"), encoding="utf-8").read()
        m = re.search(r"\*\*(\d+)\*\* look like false alarms.*?leaving \*\*(\d+)\*\*.*?before \*\*(\d+)\*\* of the real ones \(\*\*(\d+)% coverage", el, re.S)
        if m:
            print("  ern-learn     %s%% gut coverage of %s real failures; %s of %s signatures were false alarms"
                  % (m.group(4), m.group(2), m.group(1), int(m.group(1)) + int(m.group(2))))
    except Exception:
        pass
    try:
        import importlib, sys as _sys
        if DR not in _sys.path:
            _sys.path.insert(0, DR)
        _ga = importlib.import_module("guard_audit")
        w = _ga.weak_guards()
        print("  guards        %s" % ("all have a liveness anchor; none can die silently"
                                       if not w else "COULD DIE UNNOTICED: " + ", ".join(w)))
    except Exception:
        pass

    # calibration (from the wake view)
    try:
        t = io.open(os.path.join(BRAIN, "PREDICTIONS.md"), encoding="utf-8").read()
        cal = re.search(r"## Calibration[^\n]*\n+(.*?)(\n## |\Z)", t, re.S)
        if cal:
            lines = [l.strip() for l in cal.group(1).splitlines() if l.strip().startswith("- ")]
            if lines:
                print("\nCALIBRATION")
                for l in lines[:6]:
                    print("  " + re.sub(r"\*\*", "", l))
    except Exception:
        pass

    # open work
    print("\nOPEN WORK")
    for path, label in ((os.path.join(MEM, "OPEN_QUESTIONS.md"), "open questions"),
                        (os.path.join(DR, "INTENTIONS.md"), "deferred intentions"),
                        (os.path.join(MEM, "RECONSOLIDATION_QUEUE.md"), "beliefs awaiting reconsolidation")):
        k = _n_lines(path, lambda l: l.lstrip().startswith("- [ ]"))
        print("  %-34s %d" % (label, k))
    print()


if __name__ == "__main__":
    main()
