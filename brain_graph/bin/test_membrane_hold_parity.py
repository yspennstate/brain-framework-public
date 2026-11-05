"""test_membrane_hold_parity.py - a line held back by design costs what a shown line costs (28 Sep, claude-825b1305;
scheduled audit account03/B08 R12, B08-R12-HOLD-PARITY-010).

    python test_membrane_hold_parity.py [LIVE_MEMBRANE.py] [OLD_LIVE_MEMBRANE.py] [--old-fixmem OLD_LIVE_MEMBRANE.py]
                                        [--old-quota OLD_LIVE_MEMBRANE.py]

Two arms start from the same state, the same candidate is assigned SHOW in one and HOLD in the other (held_back forced),
and the membrane's decide() is called as the hook calls it. B08's acceptance: after the focal line, the budget, the
refractory keys and what a second eligible candidate gets must match between the arms; only the focal delivery differs.
 P1 one fire below the hourly cap: the focal evaluation spends the last fire in both arms, so a second candidate a
    minute later is refused in both
 P2 one line per evaluation: a held focal line takes the slot, so a second channel in the same evaluation stays silent
    in both arms
 P3 the refractory keys and the fire times match between the arms after the focal line
 P4 the focal line is shown in SHOW and not in HOLD, and HOLD logs it as held
With OLD_LIVE_MEMBRANE, the same steps on the membrane before this change must differ (the check can fail).
 P5 (28 Sep 15:xx, B08 R12 B08-R12-FIXMEM-CARRYOVER-014) an error line whose fix memory is M: both arms spend the same
    budget, only SHOW prints, and M offered a minute later or after the refractory is served in neither arm (focal
    isolation); the HOLD record names M. P6: hold_estimand "total" keeps the mediated exposure, as labelled.
    With --old-fixmem, the membrane before that change must serve M in HOLD and not in SHOW (N2).
 P7 (28 Sep 16:xx, account01/B08 R49, B08-R49-CROSSSESSION-QUOTA-INTERFERENCE-045) the two-session quota, on real
    temporary logs with the module's own served_elsewhere (not stubbed, B08-R49-PARITY-TEST-MASKS-GLOBAL-STATE-046): M
    served in session S0; in S1 an error line whose fix is M, SHOW or HOLD, written to the logs as the hook writes them;
    M offered in S2. S2 must be served the same, and the quota must count the same sessions, in both arms.
 P8 the same evaluation: a held fix memory stops the memory channel from serving a second memory, as the shown line's
    named memory does. P9 the word channel does not name a memory the evaluation already named or held.
    With --old-quota, the membrane before this change must fail P7, P8 and P9 (N3, N4, N5).
 P7 and P8 also pin the state the rule should leave, not only that the arms agree (28 Sep 18:xx, account03/B08 auto61,
    B08-R49-PARITY-ABSOLUTE-CONTRACT-020: two arms can be identically wrong): in P7 M is refused in S2 in both arms, the
    quota lists [M, 2] and counts S0 and S1; in P8 M2 is refused in both arms. N6: a membrane whose quota counts no
    session, in both arms, must fail P7; N7: one whose memory channel ignores the memory the evaluation named or held,
    in both arms, must fail P8 (the checks can fail on a symmetric regression).
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import copy
import importlib.util
import json
import os
import shutil
import sys
import tempfile

H = os.path.expanduser("~")
BIN = os.path.join(H, ".claude", "brain_graph", "bin")
NEW = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BIN, "live_membrane.py")
OLD = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else None
OLD_FIX = sys.argv[sys.argv.index("--old-fixmem") + 1] if "--old-fixmem" in sys.argv else None
OLD_Q = sys.argv[sys.argv.index("--old-quota") + 1] if "--old-quota" in sys.argv else None
sys.path.insert(0, BIN)
fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def load(path, name, stub_quota=True):
    import importlib.machinery                      # any file name, a backup's too
    spec = importlib.util.spec_from_file_location(name, path, loader=importlib.machinery.SourceFileLoader(name, path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    if stub_quota:
        m.served_elsewhere = lambda now: {}         # no other sessions: the arms differ only by the assignment
    m.precision_line = lambda kind: "test"
    return m


NOW = 1_800_200_000.0
ERR = [("err:TimeoutExpired", 500.0, None, [(1.0, "file:~/proj/runner.py"), (1.0, "cmd:pytest")])]
PLACE = [("dir:~/proj/results", 500.0, None, [(1.0, "file:~/proj/report.py"), (1.0, "cmd:python")])]


def policy(m, max_lines):
    pol = json.load(open(os.path.join(os.path.dirname(BIN), "live", "policy.json"), encoding="utf-8"))
    pol.update(max_lines=max_lines, max_fires_per_hour=6, serve_attr=False, serve_mem=False, serve_word=False,
               serve_verdict=False, serve_orders=False, serve_successor=False, serve_err=True, serve_place=True,
               err_hebb_min=1.0, place_hebb_min=1.0, err_skip=[], refractory_s=1800)
    return pol


def arms(m, hold):
    m.held_back = lambda state, key, now, pol: hold
    return m


def run(m, hold, max_lines, fires_before):
    pol = policy(m, max_lines)
    m = arms(m, hold)
    st = {"sid": "parity01", "served": {}, "seen": [], "fire_times": [NOW - 600 - 60 * i for i in range(fires_before)]}
    # the focal evaluation carries the focal err line and, when max_lines allows it, a place line in the same call
    ev1 = {"ep": "tool", "err": copy.deepcopy(ERR), "place": copy.deepcopy(PLACE) if max_lines == 1 else []}
    lines1, served1 = m.decide(ev1, NOW, st, pol)
    after1 = {"fires": len([x for x in st.get("fire_times", []) if NOW - x < 3600]),
              "refractory": sorted(st.get("served", {}))}
    ev2 = {"ep": "tool", "place": copy.deepcopy(PLACE)}
    lines2, served2 = m.decide(ev2, NOW + 60, st, pol)
    return {"lines1": lines1, "served1": served1, "after1": after1, "lines2": lines2, "served2": served2}


def compare(m, tag):
    out = {}
    s1, h1 = run(m, False, 2, 5), run(m, True, 2, 5)
    out["P1"] = (bool(s1["lines2"]) == bool(h1["lines2"]),
                 "%s P1 one fire below the cap, a second candidate a minute later: SHOW arm %s, HOLD arm %s" % (
                     tag, "served" if s1["lines2"] else "refused", "served" if h1["lines2"] else "refused"))
    out["P3"] = (s1["after1"] == h1["after1"],
                 "%s P3 after the focal line: SHOW %s, HOLD %s" % (tag, s1["after1"], h1["after1"]))
    out["P4"] = (bool(s1["lines1"]) and not h1["lines1"] and any(x[0] == "held" for x in h1["served1"]),
                 "%s P4 the focal line: shown %d line(s) in SHOW, %d in HOLD, held logged %s" % (
                     tag, len(s1["lines1"]), len(h1["lines1"]), any(x[0] == "held" for x in h1["served1"])))
    s2, h2 = run(m, False, 1, 0), run(m, True, 1, 0)
    same_slot = [x[0] for x in s2["served1"] if x[0] != "err"] == [x[0] for x in h2["served1"] if x[0] not in ("held",)]
    out["P2"] = (same_slot and not any(x[0] == "place" for x in h2["served1"]),
                 "%s P2 one line per evaluation, focal err plus a place line in the same call: SHOW served %s, HOLD "
                 "served %s" % (tag, [x[0] for x in s2["served1"]], [x[0] for x in h2["served1"]]))
    return out


FIX = ("fix_memory_m_2026_09_28", "what agents read after this error")


def spend(m, seen, served, st):
    """The hook entry's bookkeeping after decide(): the module's own spend_served, or the loop it replaced."""
    if hasattr(m, "spend_served"):
        return m.spend_served(seen, served, st)
    for kind, key, _by in served:
        if kind in ("mem", "word"):
            seen.add(key)
    return []


def fixmem_run(m, hold, estimand=None):
    """B08's acceptance fixture: one qualifying error whose fix memory is M, then M offered a minute later and again
    after the refractory, as evaluate() offers memories (never one the session has seen)."""
    pol = policy(m, 2)
    pol.update(serve_mem=True, mem_size_min=1.0, mem_hebb_min=1e9, mem_reach_min=1e9, mem_max_sessions=2)
    if estimand:
        pol["hold_estimand"] = estimand
    m = arms(m, hold)
    st = {"sid": "parity02", "served": {}, "seen": [], "fire_times": []}
    lines1, served1 = m.decide({"ep": "tool", "err": copy.deepcopy(ERR), "_fix": FIX}, NOW, st, pol)
    seen = set(st["seen"])
    held_fix = spend(m, seen, served1, st)
    st["seen"] = sorted(seen)
    out = {"text": bool(lines1), "held": any(x[0] == "held" for x in served1), "held_fix": held_fix,
           "fires": len(st.get("fire_times", [])), "later": []}
    for dt in (60, pol["refractory_s"] + 60):
        offer = [{"slug": FIX[0], "size": 5.0, "hebb": 0.0, "reach": 0.0, "why": ["file:~/proj/runner.py"],
                  "descr": FIX[1]}] if FIX[0] not in set(st["seen"]) else []
        lines, served = m.decide({"ep": "tool", "mem_all": offer}, NOW + dt, st, pol)
        seen = set(st["seen"])
        spend(m, seen, served, st)
        st["seen"] = sorted(seen)
        out["later"].append(any(x[0] == "mem" and x[1] == FIX[0] for x in served))
    return out


def fixmem_checks(m, tag):
    s, h = fixmem_run(m, False), fixmem_run(m, True)
    return {
        "P5a": (s["fires"] == h["fires"] and s["text"] and not h["text"] and h["held"],
                "%s P5a the focal error line with fix memory M: fires SHOW %d / HOLD %d, text only in SHOW %s, HOLD "
                "logged held %s" % (tag, s["fires"], h["fires"], s["text"] and not h["text"], h["held"])),
        "P5b": (s["later"] == h["later"] == [False, False],
                "%s P5b M offered a minute later and after the refractory: served in SHOW %s, in HOLD %s (focal "
                "isolation: never in either)" % (tag, s["later"], h["later"])),
        "P5c": (h["held_fix"] == [FIX[0]],
                "%s P5c the HOLD evaluation records the held line's fix slug for the analysis: %s" % (tag, h["held_fix"])),
    }, s, h


M2 = {"slug": "other_memory_m2_2026_09_28", "size": 5.0, "hebb": 0.0, "reach": 0.0, "why": ["file:~/proj/runner.py"],
      "descr": "another memory the session connects to"}


def offer(slug, descr):
    return [{"slug": slug, "size": 5.0, "hebb": 0.0, "reach": 0.0, "why": ["file:~/proj/runner.py"], "descr": descr}]


def mem_policy(m, max_lines=2):
    pol = policy(m, max_lines)
    pol.update(serve_mem=True, mem_size_min=1.0, mem_hebb_min=1e9, mem_reach_min=1e9, mem_max_sessions=2)
    return pol


def log_rows(m, now, sid, served, held_fix):
    """The two rows the hook writes for one evaluation: the served-lines row (the module's own served_row when it has
    one; before 28 Sep 16:xx the hook wrote these five fields) and the fire-log record's quota fields."""
    if served:
        row = m.served_row(now, sid, "tool", "his", served, held_fix) if hasattr(m, "served_row") else \
            {"t": round(now, 1), "sid": sid[:40], "ep": "tool", "kind": "his", "served": [list(x) for x in served]}
        with open(m.SERVED, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    rec = {"t": round(now, 1), "sid": sid[:40], "kind": "his", "served": [list(x) for x in served]}
    if held_fix:
        rec["held_fix"] = held_fix
    with open(m.FIRES, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def quota_run(path, name, hold, no_quota=False):
    """P7: S0 was served M; S1 draws SHOW or HOLD on an error line whose fix is M; then M is offered in S2. no_quota: the
    N6 regression, a quota that counts no session in either arm."""
    m = load(path, name, stub_quota=False)
    if no_quota:
        m.served_elsewhere = lambda now, *a, **k: {}
    tmp = tempfile.mkdtemp(prefix="quota_parity_")
    try:
        m.SERVED, m.FIRES = os.path.join(tmp, "served.jsonl"), os.path.join(tmp, "fires.jsonl")
        log_rows(m, NOW - 1800, "s0aaaaaa-quota", [("mem", FIX[0], [])], [])
        pol = mem_policy(m)
        m = arms(m, hold)
        st1 = {"sid": "s1bbbbbb-quota", "served": {}, "seen": [], "fire_times": []}
        lines1, served1 = m.decide({"ep": "tool", "err": copy.deepcopy(ERR), "_fix": FIX}, NOW - 900, st1, pol)
        seen = set(st1["seen"])
        held_fix = spend(m, seen, served1, st1)
        log_rows(m, NOW - 900, st1["sid"], served1, held_fix)
        st2 = {"sid": "s2cccccc-quota", "served": {}, "seen": [], "fire_times": []}
        ev2 = {"ep": "tool", "mem_all": offer(FIX[0], FIX[1])}
        lines2, served2 = m.decide(ev2, NOW, st2, pol)
        return {"shown1": bool(lines1), "m_in_s2": any(x[0] == "mem" and x[1] == FIX[0] for x in served2),
                "qb": ev2.get("qb"), "others": sorted(m.served_elsewhere(NOW).get(FIX[0], set()))}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def gate_run(m, hold, with_err=True):
    """P8: the focal error line (fix M) and a second memory M2 the memory channel would serve, in one evaluation."""
    pol = mem_policy(m)
    m = arms(m, hold)
    st = {"sid": "parity03", "served": {}, "seen": [], "fire_times": []}
    ev = {"ep": "tool", "mem_all": [dict(M2)]}
    if with_err:
        ev.update(err=copy.deepcopy(ERR), _fix=FIX)
    lines, served = m.decide(ev, NOW, st, pol)
    return {"m2": any(x[0] == "mem" and x[1] == M2["slug"] for x in served), "n_lines": len(lines)}


def word_run(m, hold, slug):
    """P9: his prompt's words match `slug` in the same evaluation as the focal error line whose fix is M."""
    pol = policy(m, 2)
    pol.update(serve_word=True, word_min=1.0, mem_max_sessions=2)
    m = arms(m, hold)
    st = {"sid": "parity04", "served": {}, "seen": [], "fire_times": []}
    ev = {"ep": "cli", "err": copy.deepcopy(ERR), "_fix": FIX, "word": [{"slug": slug, "score": 99.0, "descr": "d"}]}
    lines, served = m.decide(ev, NOW, st, pol)
    return {"word": [x[1] for x in served if x[0] == "word"], "n_lines": len(lines)}


def quota_checks(path, tag, no_quota=False):
    s, h = quota_run(path, tag + "_qs", False, no_quota), quota_run(path, tag + "_qh", True, no_quota)
    gs, gh, g0 = None, None, None
    m = load(path, tag + "_g")
    gs, gh, g0 = gate_run(m, False), gate_run(m, True), gate_run(m, False, with_err=False)
    ws, wh = word_run(m, False, FIX[0]), word_run(m, True, FIX[0])
    w3s, w3h = word_run(m, False, "word_memory_m3_2026_09_28"), word_run(m, True, "word_memory_m3_2026_09_28")
    return {
        "P7": (s["shown1"] and s["m_in_s2"] is h["m_in_s2"] is False and s["qb"] == h["qb"] == [[FIX[0], 2]]
               and s["others"] == h["others"] == ["s0aaaaaa", "s1bbbbbb"],
               "%s P7 M offered in S2 after S0 had it and S1 drew SHOW or HOLD on its error line: served SHOW %s / HOLD "
               "%s, quota held back SHOW %s / HOLD %s, sessions counted SHOW %s / HOLD %s" % (
                   tag, s["m_in_s2"], h["m_in_s2"], s["qb"], h["qb"], s["others"], h["others"])),
        "P8": (g0["m2"] and gs["m2"] is gh["m2"] is False,
               "%s P8 a second memory M2 in the focal evaluation: served alone %s; with the focal line SHOW %s / HOLD %s" % (
                   tag, g0["m2"], gs["m2"], gh["m2"])),
        "P9": (ws["word"] == wh["word"] == [] and w3s["word"] == w3h["word"] == ["word_memory_m3_2026_09_28"],
               "%s P9 his words match the fix memory M: word line SHOW %s / HOLD %s (never twice, never only in one "
               "arm); match another memory M3: SHOW %s / HOLD %s" % (tag, ws["word"], wh["word"], w3s["word"],
                                                                     w3h["word"])),
    }


def main():
    new = load(NEW, "lm_new")
    for key, (ok, what) in sorted(compare(new, "new").items()):
        check(ok, what)
    res5, _s, _h = fixmem_checks(new, "new")
    for key, (ok, what) in sorted(res5.items()):
        check(ok, what)
    tot = fixmem_run(new, True, estimand="total")
    check(tot["later"][0] and not tot["held_fix"],
          "new P6 hold_estimand 'total' keeps the mediated exposure on purpose: HOLD serves M a minute later %s, no "
          "held fix recorded %s" % (tot["later"][0], not tot["held_fix"]))
    for key, (ok, what) in sorted(quota_checks(NEW, "new").items()):
        check(ok, what)
    res6 = quota_checks(NEW, "N6", no_quota=True)
    check(not res6["P7"][0], "N6 a quota that counts no session, in both arms, fails P7 (the check can fail on a symmetric "
                             "regression): %s" % res6["P7"][1].split(": ", 1)[1])
    src = open(NEW, encoding="utf-8").read()
    guard = ' and not any(s[0] in ("mem", "word") for s in served) and not held_mems:'
    if src.count(guard) == 1:
        tmp = tempfile.mkdtemp(prefix="parity_n7_")
        try:
            mut = os.path.join(tmp, "live_membrane_no_mem_guard.py")
            open(mut, "w", encoding="utf-8").write(src.replace(guard, ":"))
            res7 = quota_checks(mut, "N7")
            check(not res7["P8"][0], "N7 a memory channel that ignores the named and held memory, in both arms, fails P8 "
                                     "(the check can fail on a symmetric regression): %s" % res7["P8"][1].split(": ", 1)[1])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        check(False, "N7 the memory channel's guard was not found once in %s (the mutation needs updating)" % NEW)
    if OLD_Q:
        res = quota_checks(OLD_Q, "before 28 Sep 16:xx")
        for key, n in (("P7", "N3"), ("P8", "N4"), ("P9", "N5")):
            check(not res[key][0], "%s the membrane before this change fails %s (the check can fail): %s" % (
                n, key, res[key][1].split(": ", 1)[1]))
    if OLD_FIX:
        old5 = load(OLD_FIX, "lm_old_fix")
        _r, s0, h0 = fixmem_checks(old5, "before 28 Sep 15:xx")
        check(h0["later"][0] and not s0["later"][0],
              "N2 the membrane before this change serves M a minute later in HOLD and not in SHOW (the defect B08 "
              "found; the check can fail): SHOW %s, HOLD %s" % (s0["later"], h0["later"]))
    if OLD:
        old = load(OLD, "lm_old")
        res = compare(old, "before 28 Sep")
        differs = not res["P1"][0] or not res["P2"][0]
        check(differs, "N1 the membrane before this change fails P1 or P2 (the check can fail): P1 %s, P2 %s" % (
            res["P1"][1].split(": ", 1)[1], res["P2"][1].split(": ", 1)[1]))
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
