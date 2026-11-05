"""test_fix_candidate_learner.py - the fix memory an error line names is recorded in both arms and under both estimands,
and the learner judges it in both arms and learns from it in neither (28 Sep 2026, claude-825b1305; scheduled audit
account01/B08 R49, B08-R49-TOTAL-ESTIMAND-FIX-ID-048 and B08-R49-ADAPTIVE-LEARNER-INTERFERENCE-047).

    python test_fix_candidate_learner.py [--membrane PATH] [--learner PATH] [--old-membrane PATH] [--old-learner PATH]

The membrane's run() is driven end to end on a temporary home (sessions, served.jsonl, fires.jsonl): the evaluator is
faked to offer one qualifying error line whose fix memory is M, the real decide() runs, and held_back is forced to SHOW
or HOLD. The fire-log rows it writes are then given to graph_learn.main() on a temporary live folder, with the same
receiver behaviour in both arms, and the learner's state is compared. Nothing live is read or written.
 M1 SHOW, focal: the row names M, delivered, spent, and the index of the line that carried it
 M2 HOLD, focal: the row names M, not delivered, spent; held_fix names M
 M3 HOLD, total: the row names M, not delivered, not spent, and no held_fix (the case B08-048 found unrecorded)
 M4 SHOW, total: delivered and spent
 M5 a failed save in HOLD: nothing served, the row names M as withheld, neither delivered nor spent
 M6 the candidate is never kept in the session state on disk
 M7 the record carries the unit the assignment was drawn for (session, line, two-hour block), the same in both arms:
    the trial id a paired analysis joins on (account03/B08 auto61, B08-R49-HELD-FIX-JUDGED-PROVENANCE-019)
 L1 SHOW and focal HOLD with no read of M: the same learned edges (none), the same tired set, the same memory-bar evidence
    and the same fire-rate slots; the fix report counts one shown and one held, neither read
 L2 four SHOWs of M in a day, never read: M is not tired; the control, four memory-channel lines of M never read: tired
 L3 a SHOW read within 30 minutes: no learned edge, the fix report counts it read; the control, a memory-channel line
    read: a positive edge
 L4 a row written before this change (no fix record): its carried memory is still read as a memory line
 L5 a SHOW record whose carried line has been deleted while the error line stays (account04/B20 r14,
    B20-R37-COARSE-COMPLIANCE-WITHOUT-UPTAKE-20260928): counted as shown by assignment and as a missing line, never as a
    delivered candidate; the intact record counts no missing line
With --old-learner the learner before this change must fail L1 and L2 (N1, N2); with --old-membrane the membrane before
this change must leave the total-estimand HOLD row without the candidate (N3).
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import contextlib
import copy
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import types

H = os.path.expanduser("~")
BIN = os.path.join(H, ".claude", "brain_graph", "bin")
sys.path.insert(0, BIN)
ARGS = sys.argv[1:]


def arg(name, default=None):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


MEMB = arg("--membrane", os.path.join(BIN, "live_membrane.py"))
LEARN = arg("--learner", os.path.join(BIN, "graph_learn.py"))
OLD_MEMB, OLD_LEARN = arg("--old-membrane"), arg("--old-learner")
NOW = 1_800_300_000.0
M = "fix_memory_m_2026_09_28"
ERR = [("err:TimeoutExpired", 500.0, None, [(1.0, "file:~/proj/runner.py"), (1.0, "cmd:pytest")])]
fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path, loader=importlib.machinery.SourceFileLoader(name, path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---- the membrane, end to end --------------------------------------------------------------------------------------
X = types.ModuleType("extract_touch_events")
X.seed_from_transcript = lambda tp, now=None, window=None, k=None, tail_bytes=None: [("file:~/proj/runner.py", 1.0)]
X.his_text = lambda d, s: ""
X.words = lambda s, cap=40: []
sys.modules["extract_touch_events"] = X


class FakeLive:
    def __init__(self):
        self.db, self.meta = None, {"built": 0}

    def evaluate(self, items, now, seen_slugs=None, pol=None, base_p=0.2):
        return {"err": copy.deepcopy(ERR), "mem": [], "n_items": len(items)}

    def fix_memory(self, *a, **k):
        return (M, "what agents read after this error")


def membrane_row(path, name, hold, estimand="focal", fail_save=False, sid=None):
    """One run() of a fresh session: returns (text, the fire-log row, the served rows, the state on disk)."""
    lm = load(path, name)
    tmp = tempfile.mkdtemp(prefix="fixrec_")
    try:
        lm.HOME, lm.SESS = tmp, os.path.join(tmp, "sessions")
        lm.SERVED, lm.FIRES = os.path.join(tmp, "served.jsonl"), os.path.join(tmp, "fires.jsonl")
        lm.STOP, lm.SIDECAR, lm.PRECISION = (os.path.join(tmp, "STOP"), os.path.join(tmp, "sidecar.sqlite"),
                                             os.path.join(tmp, "precision.json"))
        os.makedirs(os.path.join(tmp, "logs"))
        open(lm.SIDECAR, "w").close()
        tp = os.path.join(tmp, "transcript.jsonl")
        open(tp, "w").write("{}\n")
        pol = json.load(open(os.path.join(H, ".claude", "brain_graph", "live", "policy.json"), encoding="utf-8"))
        pol.update(max_lines=2, max_fires_per_hour=6, serve_attr=False, serve_mem=False, serve_word=False,
                   serve_verdict=False, serve_orders=False, serve_successor=False, serve_err=True, serve_place=False,
                   err_hebb_min=1.0, err_skip=[], refractory_s=1800, hold_estimand=estimand)
        base = lm.policy()
        base.update(pol)
        lm.policy = lambda: dict(base)
        lm.Live = FakeLive
        lm.held_back = lambda state, key, now, p: hold
        lm.receiver_kind = lambda st, tp: None
        lm.order_query = lambda items, ask: ["q"]
        lm.order_candidates = lambda *a, **k: []
        lm.stale_orders = lambda *a, **k: []
        lm.word_candidates = lambda *a, **k: []
        if fail_save:
            lm.save_state = lambda sid, st: False
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"
        sid = sid or "abcd%04d-fixrec-%s" % (int(hold) * 10 + (estimand == "total") * 100 + fail_save * 1000, name)
        text = lm.run({"session_id": sid, "transcript_path": tp, "hook_event_name": "PostToolUse"}, now=NOW, force=True)
        rows = [json.loads(l) for l in open(lm.FIRES, encoding="utf-8")] if os.path.exists(lm.FIRES) else []
        srv = [json.loads(l) for l in open(lm.SERVED, encoding="utf-8")] if os.path.exists(lm.SERVED) else []
        sp = lm._state_path(sid)
        state = json.load(open(sp, encoding="utf-8")) if os.path.exists(sp) else {}
        return text, (rows[-1] if rows else {}), srv, state
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---- the learner, on a temporary live folder -----------------------------------------------------------------------
def learn(path, name, fires, events):
    """graph_learn.main() once over `fires` with the capture's `events`; returns status, state, policy, learned rows."""
    gl = load(path, name)
    tmp = tempfile.mkdtemp(prefix="fixlearn_")
    try:
        live = os.path.join(tmp, "live")
        os.makedirs(live)
        os.makedirs(os.path.join(tmp, "projects"))
        ev_path = os.path.join(tmp, "events.jsonl")
        open(ev_path, "w").close()
        open(os.path.join(live, "recall_log.jsonl"), "w").close()
        with open(os.path.join(live, "fires.jsonl"), "w", encoding="utf-8") as fh:
            for f in fires:
                fh.write(json.dumps(f) + "\n")
        dial = os.path.join(tmp, "dial.json")
        json.dump({"lambda": 0.2}, open(dial, "w"))
        close = NOW + 4000                                     # the capture has seen past every 30-minute window
        events = list(events) + [[close, "zzzzzzzz", "dir", "x"]]
        st = {"events_offset": 0, "fires_offset": 0, "recall_offset": 0, "pending": [], "pending_recalls": [],
              "recent": events, "judged": [], "recall_judged": [], "served_log": [], "started": NOW - 86400,
              "last_event_ts": max(e[0] for e in events)}
        json.dump(st, open(os.path.join(live, "learn_state.json"), "w"))
        json.dump({"mem_hebb_min": 55.0, "mem_size_min": 1.89}, open(os.path.join(live, "policy.json"), "w"))
        gl.LIVE, gl.EVENTS, gl.FIRES = live, ev_path, os.path.join(live, "fires.jsonl")
        gl.RECALL_LOG, gl.LEARNED = os.path.join(live, "recall_log.jsonl"), os.path.join(live, "learned.sqlite")
        gl.POLICY, gl.PRECISION = os.path.join(live, "policy.json"), os.path.join(live, "precision.json")
        gl.STATUS, gl.STATE, gl.LOGMD = (os.path.join(live, "LIVE_STATUS.json"), os.path.join(live, "learn_state.json"),
                                         os.path.join(live, "LEARNING_LOG.md"))
        gl.DIAL, gl.PROJECTS = dial, os.path.join(tmp, "projects")
        real_time, argv = gl.time.time, sys.argv
        gl.time.time = lambda: close + 100
        sys.argv = ["graph_learn.py"]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                gl.main()
        finally:
            gl.time.time, sys.argv = real_time, argv
        status = json.loads(buf.getvalue())
        state = json.load(open(gl.STATE, encoding="utf-8"))
        pol = json.load(open(gl.POLICY, encoding="utf-8"))
        rows = []
        if os.path.exists(gl.LEARNED):
            db = sqlite3.connect(gl.LEARNED)
            rows = db.execute("SELECT src, dst, pos, neg FROM learned ORDER BY src, dst").fetchall()
            db.close()
        return status, state, pol, rows
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def unit_free(fix):
    """The fix record without its unit (the unit names the session, which differs between the M-arms)."""
    return {k: v for k, v in (fix or {}).items() if k != "unit"}


def learner_view(res):
    """What the arms must agree on: learned edges, the tired set, the memory-bar evidence and the fire-rate slots."""
    status, state, pol, rows = res
    judged = state.get("judged", [])
    return {"edges": rows, "tired": sorted(pol.get("tired_until", {})),
            "mem_bar_lines": sum(1 for j in judged for ln in j["lines"] if ln[0] == "mem"),
            "bar_slots": sum(1 for j in judged if j["fired"] or j.get("held")),
            "bar": status.get("bar")}


def at(row, t, sid):
    r = copy.deepcopy(row)
    r["t"], r["sid"] = t, sid
    return r


def mem_channel_row(t, sid):
    return {"t": t, "sid": sid, "ev": "post", "ep": "cli", "mem": [[M, 9.0]], "served": [["mem", M, ["file:~/proj/runner.py"]]]}


def main():
    # the membrane
    s_txt, s_row, s_srv, s_state = membrane_row(MEMB, "lm_s", False)
    h_txt, h_row, h_srv, h_state = membrane_row(MEMB, "lm_h", True)
    ht_txt, ht_row, _srv, _st = membrane_row(MEMB, "lm_ht", True, "total")
    st_txt, st_row, _srv, _st = membrane_row(MEMB, "lm_st", False, "total")
    w_txt, w_row, w_srv, _st = membrane_row(MEMB, "lm_w", True, fail_save=True)
    fx = s_row.get("fix") or {}
    carried = (s_row.get("served") or [None])[fx.get("at", -1)] if isinstance(fx.get("at"), int) else None
    check(bool(s_txt) and unit_free(fx) == {"candidate": M, "delivered": True, "spent": True, "estimand": "focal", "at": 0}
          and carried is not None and carried[:2] == ["mem", M],
          "M1 SHOW, focal: fix %s, the line at that index %s" % (fx, carried[:2] if carried else None))
    check(not h_txt and unit_free(h_row.get("fix")) == {"candidate": M, "delivered": False, "spent": True, "estimand": "focal"}
          and h_row.get("held_fix") == [M],
          "M2 HOLD, focal: fix %s, held_fix %s" % (h_row.get("fix"), h_row.get("held_fix")))
    check(not ht_txt and unit_free(ht_row.get("fix")) == {"candidate": M, "delivered": False, "spent": False, "estimand": "total"}
          and not ht_row.get("held_fix"),
          "M3 HOLD, total: fix %s, held_fix %s" % (ht_row.get("fix"), ht_row.get("held_fix")))
    check(bool(st_txt) and (st_row.get("fix") or {}).get("delivered") is True and st_row["fix"].get("spent") is True
          and st_row["fix"].get("estimand") == "total", "M4 SHOW, total: fix %s" % st_row.get("fix"))
    check(not w_txt and not w_srv and unit_free(w_row.get("fix")) == {"candidate": M, "delivered": False, "spent": False,
                                                                     "estimand": "focal", "withheld": True}
          and not w_row.get("held_fix") and w_row.get("withheld"),
          "M5 a failed save in HOLD: served rows %d, fix %s, withheld %s" % (len(w_srv), w_row.get("fix"),
                                                                           bool(w_row.get("withheld"))))
    check("fix_rec" not in s_state and "fix_rec" not in h_state and s_state and h_state,
          "M6 the candidate is not kept in the session state (SHOW keys %d, HOLD keys %d)" % (len(s_state), len(h_state)))
    _t1, ps_row, _s1, _st1 = membrane_row(MEMB, "lm_ps", False, sid="abcd7777-paired")
    _t2, ph_row, _s2, _st2 = membrane_row(MEMB, "lm_ph", True, sid="abcd7777-paired")
    u_s, u_h = (ps_row.get("fix") or {}).get("unit"), (ph_row.get("fix") or {}).get("unit")
    check(u_s and u_s == u_h and u_s == "abcd7777-paired|err:TimeoutExpired|%d" % int(NOW // 7200),
          "M7 one session, line and block drawn SHOW in one run and HOLD in the other: unit %s / %s" % (u_s, u_h))

    # the learner, on the rows the membrane wrote
    def arms_view(path, tag):
        s = learner_view(learn(path, tag + "s", [s_row], []))
        h = learner_view(learn(path, tag + "h", [h_row], []))
        return s, h
    s, h = arms_view(LEARN, "gl_l1")
    res_s = learn(LEARN, "gl_l1r", [s_row, at(h_row, NOW + 10, "abcd9999-fixrec-other")], [])
    fxr = res_s[0]["precision"].get("fix", {})
    units = sorted((j.get("fix") or {}).get("unit") or "" for j in res_s[1]["judged"])
    check(s == h and fxr.get("shown") == 1 and fxr.get("held") == 1 and fxr.get("shown_read") == 0
          and fxr.get("held_read") == 0 and fxr.get("held_spent") == 1 and all(units) and len(units) == 2,
          "L1 SHOW and focal HOLD, no read: SHOW %s | HOLD %s | fix report %s" % (
              s, h, {k: v for k, v in fxr.items() if k != "note"}))
    four = [at(s_row, NOW + 600 * i, "abcd%04d-four" % i) for i in range(4)]
    tired_fix = learner_view(learn(LEARN, "gl_l2", four, []))["tired"]
    tired_mem = learner_view(learn(LEARN, "gl_l2c", [mem_channel_row(NOW + 600 * i, "abce%04d-four" % i)
                                                     for i in range(4)], []))["tired"]
    check(tired_fix == [] and tired_mem == [M],
          "L2 four SHOWs of M never read: tired %s; the control, four memory-channel lines: tired %s" % (
              tired_fix, tired_mem))
    sid8 = s_row["sid"][:8].lower()
    read = [[NOW + 60, sid8, "mem", M]]
    r = learn(LEARN, "gl_l3", [s_row], read)
    rc = learn(LEARN, "gl_l3c", [mem_channel_row(NOW, "abcf0000-read")], [[NOW + 60, "abcf0000", "mem", M]])
    check(r[3] == [] and r[0]["precision"]["fix"]["shown_read"] == 1 and rc[3] == [("file:~/proj/runner.py", "mem:" + M,
                                                                                     1.0, 0.0)],
          "L3 a SHOW read within 30 min: edges %s, fix read %s; the control, a memory-channel line read: edges %s" % (
              r[3], r[0]["precision"]["fix"]["shown_read"], rc[3]))
    old_row = {k: v for k, v in s_row.items() if k != "fix"}
    o = learn(LEARN, "gl_l4", [old_row], [])
    check(sorted(x[1] for x in o[3]) == ["mem:" + M, "mem:" + M] and all(x[3] == 1.0 for x in o[3]),
          "L4 a row from before this change (no fix record) is read as before: edges %s" % (o[3],))

    gone = copy.deepcopy(s_row)
    gone["served"] = [x for x in gone["served"] if x[0] != "mem"]
    lg = learn(LEARN, "gl_l5", [gone], [])[0]["precision"].get("fix", {})
    li = learn(LEARN, "gl_l5c", [s_row], [])[0]["precision"].get("fix", {})
    check(lg.get("shown") == 1 and lg.get("shown_line_missing") == 1 and li.get("shown_line_missing") == 0,
          "L5 the carried line deleted, the error line kept: shown %s, missing %s; intact record missing %s" % (
              lg.get("shown"), lg.get("shown_line_missing"), li.get("shown_line_missing")))
    if OLD_LEARN:
        so, ho = arms_view(OLD_LEARN, "gl_old")
        check(so != ho, "N1 the learner before this change leaves the arms different (the check can fail): SHOW %s | "
                        "HOLD %s" % (so, ho))
        t_old = learner_view(learn(OLD_LEARN, "gl_old2", four, []))["tired"]
        check(t_old == [M], "N2 the learner before this change tires M after four SHOWs: %s" % t_old)
    if OLD_MEMB:
        _t, o_row, _s, _st = membrane_row(OLD_MEMB, "lm_old_ht", True, "total")
        check("fix" not in o_row and not o_row.get("held_fix"),
              "N3 the membrane before this change leaves the total-estimand HOLD row without the candidate: fix %s, "
              "held_fix %s" % (o_row.get("fix"), o_row.get("held_fix")))
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
