#!/usr/bin/env python3
"""math_brain_regression_gate.py - the nightly gate that would have caught this week's silence in one night.

Between 2026-09-07 and 2026-09-11 three landings stacked on the math lane and the hook went from serving the
right theorem on 84 pct of a fresh bank's questions to 35 pct. Nothing noticed for four days because the two
evaluators that existed were run by hand, after a landing, by whoever remembered - and the in-process one could
not see the gate that did the damage. This runs the four instruments THROUGH THE REAL HOOK PATH every night,
appends one row to state/regression_gate.jsonl, renders state/REGRESSION_GATE.md (the dashboard's panel), and on
a breach sends one mesh message so a live agent sees it.

    python math_brain_regression_gate.py            run everything, exit 0 on PASS, 1 on BREACH, 2 on an instrument error
    python math_brain_regression_gate.py --quick    health check + proof-sense selftest only (about a minute)

Thresholds are the 2026-09-11 post-repair numbers with slack, not aspirations: a breach means "worse than the
day the gate was written", which is the question the gate exists to answer. Raise them deliberately after a
measured improvement; never lower them to make a night green.
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
PY = sys.executable
MESH = os.path.expanduser(r"~/brain-repo/12_cognitive_architecture/agent_mesh/agent_mesh.py")

# floors (fresh bank of 2026-09-03, through the hook): 74.2 / 91.0 served, 14.6 / 4.5 silent on 09-11
THRESH = {
    "healthcheck_rc": 0,
    "proof_selftest_rc": 0,
    "q_content_hit_min": 0.65,     # served-theorem, unnamed question
    "q_hinted_hit_min": 0.85,      # served-theorem, named
    "q_content_silent_max": 0.25,
    "q_hinted_silent_max": 0.12,
    # owner-voice recall bank (09-11 evening, after the slug x3 index and the name-coverage boost: hit@1 26/50,
    # hit@5 38/50; 09-03: 21 / 36). Raised deliberately from 0.60 after a measured improvement, slack of ~4 items.
    "recall_hit5_min": 0.68,
    "recall_hit1_min": 0.44,
    # locate-reach bank (09-11 evening: saw 40/40, openproblem 40/40 reached; campaign constants served 16/20) - the
    # half of the locate banks that needs no solver; each of the day's three retrieval defects showed here first
    "reach_saw_min": 0.90,
    "reach_openproblem_min": 0.90,
    "reach_campaign_served_min": 0.65,
    # absent-target false fires (09-12 03:xx, after the coverage abstention landed: 0.675, was 0.775) - a ceiling with
    # slack, so a landing that quietly undoes the abstention shows up the next morning
    "absent_fire_max": 0.75,
    # owner-voice PROBABILITY bank (09-13 02:xx; 30 questions in his words about the campaign's own results; hit = a served
    # node's title carries "(our id KEY)", the result nodes landed 09-12 22:11): served 24/30 and result nodes 19/30 after
    # the cost-gate anchors landed (12 / 10 before them; 9 / 0 before the result nodes existed). Floors carry ~4 items of
    # slack. The rotated-key negative control (every question scored against its neighbour's key) read 1 of 30 on the
    # same run - a ceiling of 2, so a "hit" that any served block would satisfy cannot pass as a result.
    "ownvoice_served_min": 0.60,
    "ownvoice_result_min": 0.50,
    "ownvoice_rotated_max": 2,
}


def run(cmd, timeout):
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, cwd=HERE, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        return p.returncode, p.stdout, p.stderr, round(time.time() - t0, 1)
    except subprocess.TimeoutExpired:
        return 124, "", "TIMEOUT after %ss" % timeout, round(time.time() - t0, 1)


GATE_LOCK = os.path.join(STATE, "regression_gate.lock")
GATE_LOCK_STALE_S = 3 * 3600          # a gate still holding after three hours is wedged, not working


def _holder_alive(pid):
    """True unless we can PROVE the holder is gone - on doubt we refuse rather than collide."""
    if pid <= 0:
        return False
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259      # STILL_ACTIVE
    except Exception:
        pass
    try:
        os.kill(pid, 0)
        return True
    except OSError as exc:
        import errno as _errno
        return exc.errno != _errno.ESRCH
    except Exception:
        return True


def _acquire_gate_lock():
    """One gate at a time. Returns (True, None) or (False, why).

    2026-09-13: FOUR gate runs in one day returned INCONCLUSIVE / ERROR / INCONCLUSIVE / INCONCLUSIVE and not one
    of them was a verdict about the brain. Two agents were running the gate by hand while the
    BrainMathRegressionGate scheduled task fired as well; concurrent gates starve each other, stages hit their wall
    clocks (rc=124), and the box's own load becomes the measurement - one run recorded "BREACH:
    math_sense_healthcheck rc=1" that was a timeout, with the health check green on a calm box minutes later. A
    false BREACH in this history is worse than no run, because the next reader may revert good work over it. A mesh
    claim cannot fix it: the scheduled task reads no mesh. A lock file is what both a person and a cron respect.

    A REFUSAL IS NEVER A PASS. This returns a distinct exit code (4) and writes no gate row, so nothing downstream
    can mistake "did not run" for "ran and passed".
    """
    try:
        with io.open(GATE_LOCK, encoding="utf-8") as fh:
            held = json.load(fh)
    except Exception:
        held = None
    if held:
        pid = int(held.get("pid", 0) or 0)
        started = float(held.get("started", 0) or 0)
        age = time.time() - started
        if pid == os.getpid():
            return True, None
        if _holder_alive(pid) and age < GATE_LOCK_STALE_S:
            return False, "another gate is running (pid %d, started %s, %.0f min ago)" % (
                pid, time.strftime("%H:%M:%S", time.localtime(started)), age / 60.0)
    tmp = "%s.tmp.%d" % (GATE_LOCK, os.getpid())
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"pid": os.getpid(), "started": time.time()}, fh)
    os.replace(tmp, GATE_LOCK)
    return True, None


def _release_gate_lock():
    try:
        with io.open(GATE_LOCK, encoding="utf-8") as fh:
            if int(json.load(fh).get("pid", 0) or 0) != os.getpid():
                return            # someone else's lock - never remove it
    except Exception:
        return
    try:
        os.remove(GATE_LOCK)
    except OSError:
        pass


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution. Asking this gate what it does used to take the run lock and
        # answer "another gate is running" - a help request that reads as a verdict on the fleet.
        # (script_help_flag_can_execute_work, fifth recurrence.)
        print(__doc__ or os.path.basename(__file__))
        print("\nusage: math_brain_regression_gate.py [--quick] [--full]")
        return 0
    held, why = _acquire_gate_lock()
    if not held:
        print("gate DECLINED TO RUN: %s" % why)
        print("This is NOT a pass and NOT a breach - no run happened. Try again when that one finishes.")
        return 4
    try:
        return _main_locked()
    finally:
        _release_gate_lock()


def _main_locked():
    quick = "--quick" in sys.argv
    stamp = time.strftime("%Y%m%d_%H%M%S")
    row = {"as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "quick": quick, "instruments": {}, "breaches": [], "errors": []}

    rc, out, err, dt = run([PY, os.path.join(HERE, "math_sense_healthcheck.py")], 600)
    row["instruments"]["healthcheck"] = {"rc": rc, "seconds": dt, "tail": (out or err)[-300:]}
    if rc != THRESH["healthcheck_rc"]:
        row["breaches"].append("math_sense_healthcheck rc=%s" % rc)

    rc, out, err, dt = run([PY, os.path.join(HERE, "proof_sense.py"), "--selftest"], 300)
    row["instruments"]["proof_selftest"] = {"rc": rc, "seconds": dt, "tail": (out or err)[-200:]}
    if rc != THRESH["proof_selftest_rc"]:
        row["breaches"].append("proof_sense --selftest rc=%s" % rc)

    if not quick:
        outp = os.path.join(STATE, "hookpath_gate_%s.jsonl" % stamp)
        rc, out, err, dt = run([PY, os.path.join(HERE, "eval_math_hookpath.py"), "--out", outp, "--jobs", "2"], 1800)
        inst = {"rc": rc, "seconds": dt, "rows": outp}
        try:
            s = json.load(open(outp + ".summary.json", encoding="utf-8"))
            for form in ("q_content", "q_hinted"):
                n = s[form]["n"]
                inst[form + "_hit"] = round(s[form]["hit"] / n, 3)
                inst[form + "_rank1"] = round(s[form]["rank1"] / n, 3)
                inst[form + "_silent"] = round(s[form]["silent"] / n, 3)
            inst["absent_fire"] = round(s["unanswerable"]["false_fire"] / s["unanswerable"]["n"], 3)
            # 2026-09-12: the ceiling judges the STRICT rate when the evaluator reports it - a fire that serves the
            # named target is the store answering a question whose target has since entered the store (11 of the 40
            # 09-03 absent targets matched a node title by 09-12), not a false fire. The raw rate is still recorded.
            _un = s["unanswerable"]
            if _un.get("strict_n"):
                inst["absent_fire_strict"] = round(_un["strict_false_fire"] / _un["strict_n"], 3)
                inst["absent_targets_present"] = _un.get("target_served", 0)
            _judged = inst.get("absent_fire_strict", inst["absent_fire"])
            if _judged > THRESH["absent_fire_max"]:
                row["breaches"].append("absent-target fires %.3f > %.2f" % (_judged, THRESH["absent_fire_max"]))
            inst["median_seconds"] = s.get("median_seconds")
            for form in ("q_content", "q_hinted"):
                if inst[form + "_hit"] < THRESH[form + "_hit_min"]:
                    row["breaches"].append("%s served-theorem %.3f < %.2f" % (form, inst[form + "_hit"], THRESH[form + "_hit_min"]))
                if inst[form + "_silent"] > THRESH[form + "_silent_max"]:
                    row["breaches"].append("%s silent %.3f > %.2f" % (form, inst[form + "_silent"], THRESH[form + "_silent_max"]))
        except Exception as e:  # noqa: BLE001
            row["errors"].append("hookpath summary unreadable: %s" % e)
        # PER ITEM AGAINST THE LAST GATE RUN (2026-09-13). Every number above is an aggregate against a floor, and
        # an aggregate cannot see a SWAP: on 09-12 a paired A/B read identical on every summary row while one item
        # had been fixed and another broken (memory an_identical_aggregate_can_hide_a_swap_2026_09_12). So the gate
        # now also diffs this run's rows against the previous gate run's, item by item, and records which items
        # gained and which lost. It is RECORDED, NOT A BREACH, and deliberately: between two gate runs the store
        # grows and other agents land changes, so a loss here is a question ("what moved, and was it meant to?"),
        # not a floor violation. The names are in the row so the answer takes one read instead of a re-run.
        try:
            prior = sorted(f for f in os.listdir(STATE)
                           if f.startswith("hookpath_gate_") and f.endswith(".jsonl") and f != os.path.basename(outp))
            if prior:
                prev = os.path.join(STATE, prior[-1])

                def _rows(p):
                    out = {}
                    for line in io.open(p, encoding="utf-8"):
                        line = line.strip()
                        if line:
                            r = json.loads(line)
                            out[(r.get("kind"), r.get("key"), r.get("form"), r.get("item_id"), r.get("prompt_chars"))] = r
                    return out

                a, b = _rows(prev), _rows(outp)
                shared = set(a) & set(b)
                gained, lost = [], []
                for k in shared:
                    for field in ("hit", "rank1"):
                        if a[k].get(field) != b[k].get(field):
                            name = "%s/%s:%s" % (k[0], k[1] or (k[3] or "")[:24], field)
                            (gained if b[k].get(field) else lost).append(name)
                inst["vs_prev"] = {"prev": os.path.basename(prev), "shared": len(shared),
                                   "gained": sorted(gained)[:12], "lost": sorted(lost)[:12],
                                   "n_gained": len(gained), "n_lost": len(lost)}
        except Exception as e:  # noqa: BLE001
            inst["vs_prev"] = {"error": str(e)[:120]}
        row["instruments"]["hookpath"] = inst

        rc, out, err, dt = run([PY, os.path.join(HERE, "eval_brain_recall_fresh.py"), "--v2"], 1800)
        inst = {"rc": rc, "seconds": dt}
        m = re.search(r"hit@1 (\d+)/(\d+).*?hit@5 (\d+)/(\d+)", out or "")
        if m:
            h1, n1, h5, n5 = map(int, m.groups())
            inst.update({"hit1": round(h1 / n1, 3), "hit5": round(h5 / n5, 3), "n": n1})
            if inst["hit5"] < THRESH["recall_hit5_min"]:
                row["breaches"].append("owner-voice recall hit@5 %.3f < %.2f" % (inst["hit5"], THRESH["recall_hit5_min"]))
            if inst["hit1"] < THRESH["recall_hit1_min"]:
                row["breaches"].append("owner-voice recall hit@1 %.3f < %.2f" % (inst["hit1"], THRESH["recall_hit1_min"]))
        else:
            row["errors"].append("recall summary line not found (rc=%s): %s" % (rc, (err or out)[-200:]))
        row["instruments"]["recall"] = inst

        # REAL PROMPTS THROUGH THE SHIPPED RECALL (2026-09-14). RECORDED, NOT FLOORED - deliberately, because
        # nobody has watched this number across enough nights to know what a normal one looks like, and a floor
        # invented today would breach on noise. The instrument above scores a curated 56-question bank; this one
        # replays what he actually typed against what those sessions actually went on to read, through
        # `brain.py recall` itself. The bank is where a rerank gets tuned, so the bank alone cannot falsify a
        # rerank - and the older eval_prompt_recall_fullstack.py cannot either, because it reimplements the blend
        # and never calls recall(), which is how it returned byte-identical numbers on both arms of a change that
        # moved 12 of 56 bank questions. Watch hit8 and mrr drift; floor them once there is a baseline.
        rc, out, err, dt = run([PY, os.path.join(HERE, "eval_recall_real_prompts.py")], 900)
        inst = {"rc": rc, "seconds": dt}
        m = re.search(r"hit@\d+ (\d+)/(\d+) = ([0-9.]+)\s+MRR ([0-9.]+)", out or "")
        if m:
            inst.update({"hit8": round(float(m.group(3)), 3), "mrr": round(float(m.group(4)), 4),
                         "n": int(m.group(2))})
        else:
            # AN UNPARSED SUMMARY IS AN ERROR EVEN AT rc 0. A parser that quietly records nothing when the
            # wording moves is an instrument that stops measuring without saying so - one wording change
            # blinded four instruments and this gate on 2026-09-13. A row with no hit8 must be loud.
            row["errors"].append("real-prompt recall summary line not found (rc=%s): %s"
                                 % (rc, (err or out)[-160:]))
        row["instruments"]["recall_real_prompts"] = inst

        rc, out, err, dt = run([PY, os.path.join(HERE, "eval_locate_reach.py"), "--jobs", "2"], 1200)
        inst = {"rc": rc, "seconds": dt}
        # families after openproblem (the L3 saw_pair items, 2026-09-11 night) are recorded, not floored yet
        m = re.search(r"REACH saw (\d+)/(\d+) campaign \d+/\d+ openproblem (\d+)/(\d+)(?: [a-z_]+ \d+/\d+)* \| served saw \d+/\d+ campaign (\d+)/(\d+)", out or "")
        mp = re.search(r"saw_pair (\d+)/(\d+) \|", out or "")
        if mp:
            inst["saw_pair_reach"] = round(int(mp.group(1)) / max(int(mp.group(2)), 1), 3)
        if m:
            s_hit, s_n, o_hit, o_n, c_srv, c_n = map(int, m.groups())
            inst.update({"saw_reach": round(s_hit / max(s_n, 1), 3), "openproblem_reach": round(o_hit / max(o_n, 1), 3),
                         "campaign_served": round(c_srv / max(c_n, 1), 3), "n": s_n + o_n + c_n})
            if inst["saw_reach"] < THRESH["reach_saw_min"]:
                row["breaches"].append("locate reach saw %.3f < %.2f" % (inst["saw_reach"], THRESH["reach_saw_min"]))
            if inst["openproblem_reach"] < THRESH["reach_openproblem_min"]:
                row["breaches"].append("locate reach openproblem %.3f < %.2f" % (inst["openproblem_reach"], THRESH["reach_openproblem_min"]))
            if inst["campaign_served"] < THRESH["reach_campaign_served_min"]:
                row["breaches"].append("locate campaign served %.3f < %.2f" % (inst["campaign_served"], THRESH["reach_campaign_served_min"]))
        else:
            row["errors"].append("reach summary line not found (rc=%s): %s" % (rc, (err or out)[-200:]))
        row["instruments"]["reach"] = inst

        # 2026-09-12: value-in-block - did the stored VALUE reach the served block (100 keyed L1+L2 locate items; a number in
        # the block equals the key within its tolerance; the ceiling of any solver on the bank). Recorded, not floored yet:
        # campaign 15/20 and saw 40/40, open-problem 40/40 at landing (03:4x). Keys are read from the quarantine dir.
        _vib_out = os.path.join(HERE, "state", "vib_%s.jsonl" % time.strftime("%Y%m%d_%H%M%S"))
        rc, out, err, dt = run([PY, os.path.join(HERE, "eval_value_in_block.py"), "--out", _vib_out, "--jobs", "2"], 900)
        inst = {"rc": rc, "seconds": dt, "rows": _vib_out}
        mv = re.search(r"l1/campaign n=(\d+) served (\d+) vib (\d+) \| l1/saw n=(\d+) served \d+ vib (\d+) \| l2/openproblem_page n=(\d+) served \d+ vib (\d+) \| ALL n=(\d+) served (\d+) vib (\d+)", out or "")
        if mv:
            c_n, c_srv, c_vib, s_n, s_vib, o_n, o_vib, a_n, a_srv, a_vib = map(int, mv.groups())
            inst.update({"campaign_served": round(c_srv / max(c_n, 1), 3), "campaign_vib": round(c_vib / max(c_n, 1), 3),
                         "saw_vib": round(s_vib / max(s_n, 1), 3), "openproblem_vib": round(o_vib / max(o_n, 1), 3),
                         "all_vib": round(a_vib / max(a_n, 1), 3), "n": a_n})
        else:
            row["errors"].append("value-in-block summary line not found (rc=%s): %s" % (rc, (err or out)[-200:]))
        row["instruments"]["vib"] = inst

        # 2026-09-13: the owner-voice probability bank through the hook (state/bank_ownvoice_prob.json, keys are node
        # NAMES not answers), then the rotated-key negative control on the same recorded run. ~1 min.
        _ov_out = os.path.join(HERE, "state", "ownvoice_%s.jsonl" % time.strftime("%Y%m%d_%H%M%S"))
        _store = os.path.expanduser(r"~/math-brain-build/local/population/brain.sqlite3")
        rc, out, err, dt = run([PY, os.path.join(HERE, "eval_ownvoice.py"), "--store", _store, "--out", _ov_out, "--tag", "gate"], 900)
        inst = {"rc": rc, "seconds": dt, "rows": _ov_out}
        mo = re.search(r"OWNVOICE\[gate\] n=(\d+) served (\d+) hit_result (\d+) hit_any (\d+)", out or "")
        if mo:
            o_n, o_srv, o_res, o_any = map(int, mo.groups())
            inst.update({"n": o_n, "served": round(o_srv / max(o_n, 1), 3), "result": round(o_res / max(o_n, 1), 3), "any": round(o_any / max(o_n, 1), 3)})
            if inst["served"] < THRESH["ownvoice_served_min"]:
                row["breaches"].append("owner-voice probability served %.3f < %.2f" % (inst["served"], THRESH["ownvoice_served_min"]))
            if inst["result"] < THRESH["ownvoice_result_min"]:
                row["breaches"].append("owner-voice probability result nodes %.3f < %.2f" % (inst["result"], THRESH["ownvoice_result_min"]))
            rc2, out2, err2, dt2 = run([PY, os.path.join(HERE, "ownvoice_negative_control.py"), "--store", _store, "--run", _ov_out], 300)
            mn = re.search(r"own-key hits (\d+) \| rotated-key hits (\d+)", out2 or "")
            if mn:
                inst["rotated_key_hits"] = int(mn.group(2))
                if inst["rotated_key_hits"] > THRESH["ownvoice_rotated_max"]:
                    row["breaches"].append("owner-voice negative control: rotated-key hits %d > %d (the instrument is not key-specific)" % (inst["rotated_key_hits"], THRESH["ownvoice_rotated_max"]))
            else:
                row["errors"].append("owner-voice negative control line not found (rc=%s): %s" % (rc2, (err2 or out2)[-200:]))
        else:
            row["errors"].append("owner-voice summary line not found (rc=%s): %s" % (rc, (err or out)[-200:]))
        row["instruments"]["ownvoice"] = inst

    # 2026-09-12: is content still flowing into the store? Count inbox bundles the integration ledger has never seen and
    # the store's newest node. Recorded, never floored: whether a lane's bundles enter the store is a quality decision
    # (found 946 of 1,121 unintegrated on 09-12, the 09-07 corpus lanes, ~157k entries against 7,451 nodes).
    try:
        import glob as _glob
        import sqlite3 as _sqlite3
        _led = os.path.expanduser(r"~/math-brain-build/local/population/integration_ledger.jsonl")
        _inbox = os.path.expanduser(r"~/math-brain-inbox")
        _seen = {json.loads(l).get("draft") for l in io.open(_led, encoding="utf-8") if l.strip()} if os.path.exists(_led) else set()
        _bundles = _glob.glob(os.path.join(_inbox, "*", "bundle_*.json"))
        _never = [p for p in _bundles if os.path.relpath(p, _inbox).replace("\\", "/") not in _seen]
        _con = _sqlite3.connect("file:~/math-brain-build/local/population/brain.sqlite3?mode=ro", uri=True)
        _nodes, _newest = _con.execute("select count(*), max(created_at) from nodes").fetchone()
        row["instruments"]["inflow"] = {"inbox_bundles": len(_bundles), "never_in_ledger": len(_never), "store_nodes": _nodes, "newest_node": _newest}
    except Exception as e:  # noqa: BLE001
        row["instruments"]["inflow"] = {"error": str(e)[:120]}

    # 2026-09-11 evening: a run launched under heavy load (four evaluators and two capture passes on the same
    # machine) read the health check at 212 s instead of 19 s and timed the hook-path pass out; it wrote ERROR
    # and mailed the mesh, and its row landed after a clean PASS. A timed-out instrument or a health check ten
    # times its normal cost is contention, not a regression: the verdict is INCONCLUSIVE, the mesh hears it
    # once with that word, and the panel keeps the last decided verdict beside it.
    timed_out = any(v.get("rc") == 124 for v in row["instruments"].values())
    slow_health = float(row["instruments"]["healthcheck"].get("seconds") or 0) > 120
    if timed_out or slow_health:
        row["verdict"] = "INCONCLUSIVE"
        row["inconclusive_because"] = ("instrument timeout" if timed_out else "") + (" health check %.0f s" % row["instruments"]["healthcheck"]["seconds"] if slow_health else "")
    else:
        row["verdict"] = "ERROR" if row["errors"] else ("BREACH" if row["breaches"] else "PASS")
    os.makedirs(STATE, exist_ok=True)
    with io.open(os.path.join(STATE, "regression_gate.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    hp = row["instruments"].get("hookpath", {})
    rcl = row["instruments"].get("recall", {})
    md = [
        "# Math-brain regression gate", "",
        "Last run: %s  verdict: **%s**%s" % (row["as_of"], row["verdict"], " (quick)" if quick else ""), "",
        "- health check rc %s; proof-sense selftest rc %s" % (row["instruments"]["healthcheck"]["rc"], row["instruments"]["proof_selftest"]["rc"]),
    ]
    if hp:
        md.append("- fresh bank through the hook: unnamed served %s (rank-1 %s, silent %s); named served %s (rank-1 %s, silent %s); absent-target fires %s (strict %s, targets now present %s); median %s s"
                  % (hp.get("q_content_hit"), hp.get("q_content_rank1"), hp.get("q_content_silent"), hp.get("q_hinted_hit"), hp.get("q_hinted_rank1"), hp.get("q_hinted_silent"), hp.get("absent_fire"), hp.get("absent_fire_strict"), hp.get("absent_targets_present"), hp.get("median_seconds")))
    if hp.get("vs_prev") and "error" not in hp["vs_prev"]:
        _vp = hp["vs_prev"]
        md.append("- per item against the previous gate run (%s, %s items shared): %d gained, %d lost%s%s"
                  % (_vp.get("prev"), _vp.get("shared"), _vp.get("n_gained", 0), _vp.get("n_lost", 0),
                     ("; lost: " + ", ".join(_vp["lost"])) if _vp.get("lost") else "",
                     ("; gained: " + ", ".join(_vp["gained"])) if _vp.get("gained") else ""))
    if rcl:
        md.append("- owner-voice recall bank: hit@1 %s, hit@5 %s (n=%s)" % (rcl.get("hit1"), rcl.get("hit5"), rcl.get("n")))
    rch = row["instruments"].get("reach", {})
    if rch:
        md.append("- locate reach (source node served): saw %s, open-problem %s; campaign constants served %s; two-fact pairs both served %s (n=%s)"
                  % (rch.get("saw_reach"), rch.get("openproblem_reach"), rch.get("campaign_served"), rch.get("saw_pair_reach"), rch.get("n")))
    vib = row["instruments"].get("vib", {})
    if vib and "all_vib" in vib:
        md.append("- value in block (the stored number reached the served text): campaign %s (served %s), saw %s, open-problem %s, all %s (n=%s)"
                  % (vib.get("campaign_vib"), vib.get("campaign_served"), vib.get("saw_vib"), vib.get("openproblem_vib"), vib.get("all_vib"), vib.get("n")))
    ov = row["instruments"].get("ownvoice", {})
    if ov and "served" in ov:
        md.append("- owner-voice probability bank (his words, the campaign's own results): served %s, result nodes %s, any %s; rotated-key control %s (n=%s)"
                  % (ov.get("served"), ov.get("result"), ov.get("any"), ov.get("rotated_key_hits"), ov.get("n")))
    inf = row["instruments"].get("inflow", {})
    if inf and "error" not in inf:
        md.append("- inflow: store %s nodes, newest %s; inbox bundles %s, never in the ledger %s"
                  % (inf.get("store_nodes"), str(inf.get("newest_node"))[:16], inf.get("inbox_bundles"), inf.get("never_in_ledger")))
    for b in row["breaches"]:
        md.append("- BREACH: " + b)
    for e in row["errors"]:
        md.append("- ERROR: " + e)
    md += ["", "Floors: " + json.dumps(THRESH), "", "History: state/regression_gate.jsonl (one row per run)."]
    with io.open(os.path.join(STATE, "REGRESSION_GATE.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
    print("\n".join(md))
    if row["verdict"] != "PASS":
        try:
            body = "math-brain regression gate %s: %s" % (row["verdict"], "; ".join(row["breaches"] + row["errors"])[:600])
            subprocess.run([PY, MESH, "hello", "--agent", "brain-mathgate", "--runtime", "brain", "--task", "nightly math-brain regression gate"],
                           capture_output=True, text=True, timeout=60)
            subprocess.run([PY, MESH, "send", "--agent", "brain-mathgate", "--to", "all", "--topic", "math-brain-regression", "--body", body],
                           capture_output=True, text=True, timeout=60)
        except Exception:
            pass
    return 0 if row["verdict"] == "PASS" else (1 if row["verdict"] == "BREACH" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
