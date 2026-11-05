#!/usr/bin/env python3
"""pipeline_audit.py - end-to-end health check of the mistake-firing pipeline.

A deployed pipeline fails SILENTLY (a log stops writing, the calibration goes stale, a hook stops firing)
without looking broken - the green-not-trusted scar. This traces a mistake through every stage and confirms
each works with REAL current data, so a silent break is visible. Read-only, stdlib, fail-safe. Run it, or
let the dream run it. Exit 0 always; health is in the printed OK/STALE/BROKEN per stage.
"""
import os, io, json, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
DREAM = os.path.dirname(HERE)
SETTINGS = os.path.join(os.path.dirname(DREAM), "settings.json")


def _age_h(path):
    try:
        return (time.time() - os.path.getmtime(path)) / 3600.0
    except Exception:
        return 1e9


def _lines(path, n=0):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            ls = f.readlines()
        return ls[-n:] if n else ls
    except Exception:
        return []


def check(name, ok, detail=""):
    tag = "OK    " if ok == True else ("STALE " if ok == "stale" else "BROKEN")
    print("  [%s] %-34s %s" % (tag, name, detail))
    return ok == True


def main():
    print("=== mistake-firing pipeline health ===")
    fails = []

    # 1. subconscious firing log
    p = os.path.join(DREAM, "instinct_fires.tsv")
    age, n = _age_h(p), len(_lines(p))
    check("subconscious fire-log (instinct_fires)", (n > 0 and age < 24) or ("stale" if n > 0 else False),
          "%d fires, last %.1fh ago" % (n, age)) or fails.append("fire-log")

    # 2. graph shadow log
    p = os.path.join(HERE, "gf_shadow_fires.tsv")
    age, n = _age_h(p), len(_lines(p))
    check("graph shadow-log (gf_shadow_fires)", (n > 0 and age < 24) or ("stale" if n > 0 else False),
          "%d rows, last %.1fh ago" % (n, age))

    # 3. forward-feed can read outcomes
    try:
        sys.path.insert(0, HERE)
        import forward_feed as F
        rf = F._recent_failures()
        oc = os.path.join(DREAM, "outcomes.jsonl")
        check("forward-feed reads outcomes.jsonl", os.path.exists(oc),
              "%d recent felt-errors in the feed window" % len(rf))
    except Exception as e:
        check("forward-feed reads outcomes.jsonl", False, str(e)[:40]); fails.append("forward-feed")

    # 4. calibration freshness (reflex set matches current instincts.tsv)
    try:
        tsv = [l.rstrip("\n").split("\t") for l in _lines(os.path.join(DREAM, "instincts.tsv"))]
        cur = set(x[0] for x in tsv[1:] if len(x) >= 7 and x[1] in ("fire", "guide", "carry"))
        cal = set(json.load(io.open(os.path.join(HERE, "reflex_anchors_cal.json"), encoding="utf-8"))["reflexes"])
        missing = cur - cal
        ok = True if not missing else "stale"
        check("calibration fresh vs instincts.tsv", ok,
              "%d reflexes; %d un-anchored%s" % (len(cur), len(missing),
                                                 (" (run recalibrate.py): " + ",".join(sorted(missing)[:3])) if missing else ""))
        if missing:
            fails.append("stale-calibration")
    except Exception as e:
        check("calibration fresh vs instincts.tsv", False, str(e)[:40]); fails.append("calibration")

    # 5. the bridge buffer (deposit->read roundtrip)
    try:
        import activation_buffer as ab
        s = "zztest-audit-rt"
        pth = ab._buf_path(s)
        if os.path.exists(pth):
            os.remove(pth)
        ab.deposit(s, {"scar_check_the_brain_before_you_simulate_2026_07_08": 1.0}, danger_bump=0.5)
        r = ab.read(s)
        ok = bool(r["nodes"]) and r["danger"] >= 0.4
        check("conscious<->subconscious buffer", ok, "deposit->read danger=%.2f nodes=%d" % (r["danger"], len(r["nodes"])))
        if os.path.exists(pth):
            os.remove(pth)
        if not ok:
            fails.append("buffer")
    except Exception as e:
        check("conscious<->subconscious buffer", False, str(e)[:40]); fails.append("buffer")

    # 6. hooks registered in settings.json
    try:
        d = json.load(io.open(SETTINGS, encoding="utf-8"))
        blob = json.dumps(d.get("hooks", {}))
        for hk in ("instinct_prime.py", "pre_report_check.py", "spread_recall.py"):
            check("hook registered: " + hk, hk in blob) or fails.append(hk)
    except Exception as e:
        check("hooks registered", False, str(e)[:40]); fails.append("settings")

    # 7. live gate + model in use
    en = os.path.exists(os.path.join(HERE, "ENABLED"))
    check("ENABLED (live vs shadow)", True, "LIVE" if en else "SHADOW (log-only)")
    calfile = "reflex_anchors_cal.json" if os.path.exists(os.path.join(HERE, "reflex_anchors_cal.json")) else "reflex_anchors.json (uncalibrated!)"
    check("gfcore model in use", "cal" in calfile, calfile)

    # 8. bridge LIVE-active. Roundtrip-OK (check 5) is NOT live-active: the danger sub-channel passes the
    #    roundtrip yet is inert (its trigger ~never fires - audited 07-12), which the roundtrip can't see.
    #    So confirm REAL session buffers actually carry deposits; if buffers exist but none do, the bridge
    #    silently stopped. danger>0 count is reported, not failed, since that leg is a known-inert vestige.
    try:
        import glob
        TESTPRE = ("selftest", "zztest", "deadbeef", "abcdef", "zzz", "_")
        bufs = []
        for b in glob.glob(os.path.join(HERE, ".abuf_*.json")):
            sid = os.path.basename(b)[len(".abuf_"):-len(".json")]
            if not sid.startswith(TESTPRE):
                bufs.append(b)
        def _bget(b, k, d):
            try:
                return json.load(io.open(b, encoding="utf-8")).get(k, d)
            except Exception:
                return d
        with_nodes = sum(1 for b in bufs if _bget(b, "nodes", {}))
        with_danger = sum(1 for b in bufs if (_bget(b, "danger", 0) or 0) > 0)
        ok = True if (not bufs or with_nodes > 0) else False
        check("bridge priming live-active", ok,
              "%d/%d real buffers carry scar deposits; danger>0 in %d (danger leg is a known-inert vestige)"
              % (with_nodes, len(bufs), with_danger))
        if not ok:
            fails.append("bridge-dormant")
    except Exception as e:
        check("bridge priming live-active", False, str(e)[:40])

    print("=== %s ===" % ("ALL FLOWS HEALTHY" if not fails else ("ISSUES: " + ", ".join(fails))))


if __name__ == "__main__":
    main()
