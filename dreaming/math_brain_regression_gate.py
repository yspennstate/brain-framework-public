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
sys.path.insert(0, HERE)
from math_block_parse import delivery_problem  # noqa: E402
MESH = os.path.expanduser("~/brain-repo/12_cognitive_architecture/agent_mesh/agent_mesh.py")

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
    # 2026-09-25: a result node the block names on its "same result on record" line counts as reached. The hook's
    # same-result rule (landed 25 Sep 02:46) serves our memory-born card of a result pair and names the ledger card
    # (the one carrying "(our id KEY)") on that line; counted on served cards alone the 25 Sep run read 11/30 against
    # 22/30 the day before with every block still carrying the result. The served-cards count stays in the row as
    # result_strict. The floor did not move.
    "ownvoice_served_min": 0.60,
    "ownvoice_result_min": 0.50,
    "ownvoice_rotated_max": 2,
    # REAL PROOFS (2026-09-25, the landing that made a pasted proof an argument - arm cand46 of campaign mb10d): the results a
    # pasted proof cites by name, reached in a served card's title. Held-out bank of other papers' proofs (FEI, QSO, paper23,
    # Benjamini-Schramm): 20 of 32 after the landing, 1 of 32 before; the quadratic-form papers' proofs: 21 of 57 after, 4
    # before. Floors carry about five items of slack; a landing that stops reading proofs as arguments falls to the old numbers.
    # raised 2026-09-25 after cand65 landed (on the live hook: other papers 0.72, quadratic-form papers 0.91, held-out 0.45)
    "proofbank_named_min": 0.60,
    "proofbank_qform_min": 0.75,
    "proofbank_heldout_min": 0.33,
    # the same keys scored against shuffled prompts: a hit there means the instrument is not key-specific (0 at landing)
    "proofbank_shuffled_max": 2,
}


def _invalid_to_row(row, inst, out, name):
    """Read an evaluator line's '| invalid N (reason: k; ...)' tail into the row. A hook call that exited nonzero, failed
    to launch or printed something other than its JSON delivers nothing in a session, so any such call is a breach; a
    call that timed out makes the row inconclusive (an error), not a breach. A line with no invalid count comes from an
    evaluator that does not check its calls, and is an error too (account03/B01, 30 Sep 2026)."""
    m = re.search(r"\| invalid (\d+)(?: \(([^)]*)\))?", out or "")
    if not m:
        row["errors"].append("%s: the summary carries no invalid-call count" % name)
        return
    total, why = int(m.group(1)), m.group(2) or ""
    t = re.search(r"(?:^|; )timeout: (\d+)", why)
    timeouts = int(t.group(1)) if t else 0
    inst["invalid_rows"], inst["timed_out_rows"] = total - timeouts, timeouts
    if total - timeouts:
        row["breaches"].append("%s: %d hook calls did not finish cleanly (%s)" % (name, total - timeouts, why))
    if timeouts:
        row["errors"].append("%s: %d hook calls timed out, so the row is inconclusive" % (name, timeouts))


# 2026-10-04: the whole run keeps inside GATE_BUDGET_S. regression_gate_launcher.vbs runs the gate under run_bounded.py
# with a 6600 s cap that kills the tree; the stage caps below add to about 11,000 s, so on a loaded box the 4 Oct run
# was killed at 07:01:32 with no row, no verdict and its lock left behind, and every stage printed only at the end.
# Each stage now gets at most the time left (less ROW_RESERVE_S for the row); a stage with none left is skipped and
# named, so the run ends INCONCLUSIVE with a row. The budget leaves 600 s of the cap for the unbudgeted calls (the
# 300 s production hook call and the mesh notice).
GATE_BUDGET_S = 6000
ROW_RESERVE_S = 120
_GATE = {"t0": None, "skipped": []}


def run(cmd, timeout):
    t0 = time.time()
    name = os.path.basename(cmd[1]) if len(cmd) > 1 else str(cmd[0])
    if _GATE["t0"] is not None:
        left = GATE_BUDGET_S - (t0 - _GATE["t0"]) - ROW_RESERVE_S
        if left < 5:
            _GATE["skipped"].append(name)
            print("[gate %s] %s skipped: the run is out of time" % (time.strftime("%H:%M:%S"), name), flush=True)
            return 124, "", "SKIPPED: the gate ran out of time before this stage", 0.0
        timeout = min(timeout, int(left))
    print("[gate %s] %s started (cap %ss)" % (time.strftime("%H:%M:%S"), name, timeout), flush=True)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, cwd=HERE, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        result = p.returncode, p.stdout, p.stderr, round(time.time() - t0, 1)
    except subprocess.TimeoutExpired:
        result = 124, "", "TIMEOUT after %ss" % timeout, round(time.time() - t0, 1)
    print("[gate %s] %s rc %s in %ss" % (time.strftime("%H:%M:%S"), name, result[0], result[3]), flush=True)
    return result


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
        print("\nusage: math_brain_regression_gate.py [--quick | --selftest] [--full]")
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


def production_math_block(pout):
    """(served, problem) for the dispatcher's stdout on the production-flags probe. Until 30 Sep the probe passed on the
    literal marker alone, so a block whose cards had lost their id and trust lines read as served (account04/B07,
    B07-R79-INCOMPLETE-CARD-PASSES-REGRESSION-GATES). The math section of the dispatcher's context - the marker to the
    next hook's tag line; the block's own '[math-brain: N note(s) omitted ...]' line belongs to it - must hold at least
    one card and every card whole. The dispatcher's cut, when the hooks overflow its cap, works at line boundaries and
    can leave a header without its id line; that is a delivery failure too."""
    try:
        ctx = json.loads(pout)["hookSpecificOutput"]["additionalContext"] if (pout or "").strip() else ""
    except Exception:
        return False, "dispatcher output is not the hook JSON"
    at = ctx.find("[math-brain]")
    if at < 0:
        return False, "no [math-brain] block"
    section = ctx[at:]
    nxt = re.search(r"\n\[(?!math-brain)", section)
    section = section[:nxt.start()] if nxt else section
    problem = delivery_problem(section)
    return not problem, problem


def _main_locked():
    _GATE["t0"], _GATE["skipped"] = time.time(), []
    # --selftest is what work_finders' W86 and the referee pass to every gate; this script never knew it, so each of
    # those checks ran the FULL gate (bank recall, 500+ s at load 260) - 29 Oct, several agents every 12 min. It means
    # the quick self-check: health check + proof-sense selftest.
    quick = "--quick" in sys.argv or "--selftest" in sys.argv
    stamp = time.strftime("%Y%m%d_%H%M%S")
    row = {"as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "quick": quick, "instruments": {}, "breaches": [], "errors": []}

    rc, out, err, dt = run([PY, os.path.join(HERE, "math_sense_healthcheck.py")], 600)
    row["instruments"]["healthcheck"] = {"rc": rc, "seconds": dt, "tail": (out or err)[-300:]}
    # rc 3 (2026-10-03): the hook did not answer a row in time, twice. That is the box's load, not a verdict on the
    # brain, so it is an instrument error (the run reads INCONCLUSIVE), never a BREACH and never a PASS.
    if rc == 3:
        row["errors"].append("math_sense_healthcheck could not judge (rc=3): a row timed out twice")
    elif rc != THRESH["healthcheck_rc"]:
        row["breaches"].append("math_sense_healthcheck rc=%s" % rc)

    rc, out, err, dt = run([PY, os.path.join(HERE, "proof_sense.py"), "--selftest"], 300)
    row["instruments"]["proof_selftest"] = {"rc": rc, "seconds": dt, "tail": (out or err)[-200:]}
    if rc != THRESH["proof_selftest_rc"]:
        row["breaches"].append("proof_sense --selftest rc=%s" % rc)

    # PRODUCTION FLAGS (2026-09-22). Every instrument above runs the hook under plain `python`; production runs it
    # through `python -s hook_dispatch.py` (settings.json), and from 2026-09-15 to 2026-09-22 that path died on
    # import (numpy is user-site only; -s drops the user site) while this gate stayed green for a week. This
    # instrument asks one question the store answers, through the real dispatcher with the real flags, with a fresh
    # session id so the per-session dedup cannot hide a card, and breaches when no [math-brain] block comes back.
    try:
        payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "regression-gate-%s" % stamp,
                              "prompt": "what is the best known constant in the read-once formula version of the "
                                        "Fourier entropy influence conjecture"})
        t0 = time.time()
        p = subprocess.run([PY, "-s", os.path.join(HERE, "hook_dispatch.py")], input=payload.encode("utf-8"),
                           capture_output=True, timeout=300)
        pout = (p.stdout or b"").decode("utf-8", "replace")
        served, problem = production_math_block(pout)
        row["instruments"]["production_flags"] = {"rc": p.returncode, "seconds": round(time.time() - t0, 1),
                                                  "served": served, "problem": problem,
                                                  "stderr_tail": (p.stderr or b"").decode("utf-8", "replace")[-200:]}
        if not served:
            row["breaches"].append("production flags: `python -s hook_dispatch.py` served no whole math-brain block "
                                   "(%s; rc=%s)" % (problem, p.returncode))
    except Exception as exc:
        row["errors"].append("production flags instrument: %s" % repr(exc)[:160])

    if not quick:
        outp = os.path.join(STATE, "hookpath_gate_%s.jsonl" % stamp)
        rc, out, err, dt = run([PY, os.path.join(HERE, "eval_math_hookpath.py"), "--out", outp, "--jobs", "2"], 1800)
        inst = {"rc": rc, "seconds": dt, "rows": outp}
        try:
            s = json.load(open(outp + ".summary.json", encoding="utf-8"))
            for form in ("q_content", "q_hinted"):
                n = s[form]["n"]
                inst[form + "_hit"] = round(s[form]["hit"] / n, 3) if n else 0.0
                inst[form + "_rank1"] = round(s[form]["rank1"] / n, 3) if n else 0.0
                inst[form + "_silent"] = round(s[form]["silent"] / n, 3) if n else 0.0
                inst[form + "_not_scored"] = s[form].get("not_scored", 0)
            _un_n = s["unanswerable"]["n"]
            inst["absent_fire"] = round(s["unanswerable"]["false_fire"] / _un_n, 3) if _un_n else 0.0
            # 2026-09-30 (account02/B07, B07-R79-MATH-HOOKPATH-EVALUATOR-IGNORES-CHILD-EXIT-056): the evaluator scores a
            # block only from a hook that exited 0 and counts the calls that exited nonzero, failed to launch or printed
            # something other than the hook's JSON. Such a call delivers nothing in a session, so it is a breach.
            # Since 30 Sep (account04/B07-R79) a block with an incomplete card or no readable card is counted the same way:
            # the session received it broken.
            inst["invalid_rows"] = s.get("invalid", 0)
            if inst["invalid_rows"]:
                row["breaches"].append("hook path: %d hook calls not delivered whole (%s)" % (
                    inst["invalid_rows"], "; ".join("%s: %s" % kv for kv in (s.get("invalid_reasons") or {}).items())))
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
            # 2026-09-25: the evaluator asks a timed-out hook call once more and counts the calls that time out twice;
            # the verdict below reads those as contention, like an instrument timeout
            inst["retried_rows"] = s.get("retried", 0)
            inst["timed_out_rows"] = s.get("timed_out", 0)
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
                # a row the evaluator did not score (a timeout or a call that did not finish cleanly) is no outcome to
                # gain or lose
                shared = {k for k in set(a) & set(b) if not (a[k].get("invalid") or a[k].get("timed_out")
                                                           or b[k].get("invalid") or b[k].get("timed_out"))}
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
        _invalid_to_row(row, inst, out, "value-in-block")
        row["instruments"]["vib"] = inst

        # 2026-09-13: the owner-voice probability bank through the hook (state/bank_ownvoice_prob.json, keys are node
        # NAMES not answers), then the rotated-key negative control on the same recorded run. ~1 min.
        _ov_out = os.path.join(HERE, "state", "ownvoice_%s.jsonl" % time.strftime("%Y%m%d_%H%M%S"))
        _store = os.path.expanduser("~/math-brain-build/local/population/brain.sqlite3")
        rc, out, err, dt = run([PY, os.path.join(HERE, "eval_ownvoice.py"), "--store", _store, "--out", _ov_out, "--tag", "gate"], 900)
        inst = {"rc": rc, "seconds": dt, "rows": _ov_out}
        mo = re.search(r"OWNVOICE\[gate\] n=(\d+) served (\d+) hit_result (\d+) hit_any (\d+)(?: strict (\d+))?", out or "")
        if mo:
            o_n, o_srv, o_res, o_any = map(int, mo.groups()[:4])
            inst.update({"n": o_n, "served": round(o_srv / max(o_n, 1), 3), "result": round(o_res / max(o_n, 1), 3), "any": round(o_any / max(o_n, 1), 3)})
            if mo.group(5) is not None:
                inst["result_strict"] = round(int(mo.group(5)) / max(o_n, 1), 3)
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

        # 2026-09-25: real proofs through the hook (state/bank_named_proofs_v1.json, bank_qform_proofs_v1.json). ~2 min.
        rc, out, err, dt = run([PY, os.path.join(HERE, "eval_proof_banks.py"), "--jobs", "2"], 900)
        inst = {"rc": rc, "seconds": dt}
        mp = re.search(r"PROOFBANKS named n=(\d+) served (\d+) title (\d+)/(\d+) items (\d+) \| qform n=(\d+) served (\d+) "
                       r"title (\d+)/(\d+) items (\d+)(?: \| heldout n=(\d+) served (\d+) title (\d+)/(\d+) items (\d+))? "
                       r"\| shuffled (\d+)", out or "")
        if mp:
            g = mp.groups()
            nn, ns, nt, nk, ni, qn, qs, qt, qk, qi = map(int, g[:10])
            sh = int(g[15])
            if g[10] is not None:
                hn, hs, ht, hk, hi = map(int, g[10:15])
                inst.update({"heldout_title": round(ht / max(hk, 1), 3), "heldout_items": hi, "heldout_n": hn, "heldout_served": hs})
                if inst["heldout_title"] < THRESH["proofbank_heldout_min"]:
                    row["breaches"].append("real-proof bank (held-out papers) title reach %.3f < %.2f" % (inst["heldout_title"], THRESH["proofbank_heldout_min"]))
            inst.update({"named_title": round(nt / max(nk, 1), 3), "named_items": ni, "named_n": nn, "named_served": ns,
                         "qform_title": round(qt / max(qk, 1), 3), "qform_items": qi, "qform_n": qn, "qform_served": qs,
                         "shuffled": sh})
            if inst["named_title"] < THRESH["proofbank_named_min"]:
                row["breaches"].append("real-proof bank (other papers) title reach %.3f < %.2f" % (inst["named_title"], THRESH["proofbank_named_min"]))
            if inst["qform_title"] < THRESH["proofbank_qform_min"]:
                row["breaches"].append("real-proof bank (quadratic-form papers) title reach %.3f < %.2f" % (inst["qform_title"], THRESH["proofbank_qform_min"]))
            if sh > THRESH["proofbank_shuffled_max"]:
                row["breaches"].append("real-proof bank shuffled control: %d hits > %d (the instrument is not key-specific)" % (sh, THRESH["proofbank_shuffled_max"]))
        else:
            row["errors"].append("proof-bank summary line not found (rc=%s): %s" % (rc, (err or out)[-200:]))
        _invalid_to_row(row, inst, out, "real-proof bank")
        row["instruments"]["proofbanks"] = inst

        # 2026-09-25: the ledger questions in other words (state/bank_locate_l2_paraphrase_v1.json), stored wording beside
        # them. Recorded, NOT floored: the first reading (cand65) is stored 40 of 40 against paraphrase 11 of 40 answer nodes
        # served, and a floor under a number that low would only freeze it; it is here so a change that moves recall in
        # other words - either way - shows in the nightly row. ~3 min.
        # 2026-10-04: the same 40 descriptions also go in two natural framings (eval_paraphrase_reach.NATURAL): the template
        # alone halves recall - 14 of 40 against 27 for "where is the open problem about X posed" - and "what is known about
        # X" (12) shows the entry-gate weakness. Twice the calls, hence 1500 s. Also recorded, never floored.
        rc, out, err, dt = run([PY, os.path.join(HERE, "eval_paraphrase_reach.py"), "--jobs", "2"], 1500)
        inst = {"rc": rc, "seconds": dt}
        mp = re.search(r"PARAPHRASE stored n=(\d+) served (\d+) node (\d+) value (\d+) \| paraphrase n=(\d+) served (\d+) "
                       r"node (\d+) value (\d+)", out or "")
        if mp:
            sn, ss, sno, sv, pn, psv, pno, pv = map(int, mp.groups())
            inst.update({"stored_n": sn, "stored_node": sno, "stored_value": sv, "stored_served": ss,
                         "para_n": pn, "para_node": pno, "para_value": pv, "para_served": psv})
            for _nm in ("natural_where", "natural_known"):
                _mn = re.search(r"\| %s n=(\d+) served (\d+) node (\d+) value (\d+)" % _nm, out or "")
                if _mn:
                    _n, _s, _no, _v = map(int, _mn.groups())
                    inst.update({_nm + "_n": _n, _nm + "_served": _s, _nm + "_node": _no, _nm + "_value": _v})
        else:
            row["errors"].append("paraphrase summary line not found (rc=%s): %s" % (rc, (err or out)[-200:]))
        if mp:
            _invalid_to_row(row, inst, out, "paraphrase")
        row["instruments"]["paraphrase"] = inst

    # 2026-09-12: is content still flowing into the store? Count inbox bundles the integration ledger has never seen and
    # the store's newest node. Recorded, never floored: whether a lane's bundles enter the store is a quality decision
    # (found 946 of 1,121 unintegrated on 09-12, the 09-07 corpus lanes, ~157k entries against 7,451 nodes).
    try:
        import glob as _glob
        import sqlite3 as _sqlite3
        _led = os.path.expanduser("~/math-brain-build/local/population/integration_ledger.jsonl")
        _inbox = os.path.expanduser("~/math-brain-inbox")
        _seen = {json.loads(l).get("draft") for l in io.open(_led, encoding="utf-8") if l.strip()} if os.path.exists(_led) else set()
        _bundles = _glob.glob(os.path.join(_inbox, "*", "bundle_*.json"))
        _never = [p for p in _bundles if os.path.relpath(p, _inbox).replace("\\", "/") not in _seen]
        # 2026-09-23: the 946 deliberate exclusions hid four bundles of 09-13..09-15 that nobody had integrated (three with
        # a license string off the allowlist, one never run); a bundle written after the 09-12 count is not part of that
        # decision, so it is counted on its own and named - one stuck bundle must be visible, not one in 950.
        _cut = time.mktime((2026, 9, 12, 12, 0, 0, 0, 0, -1))
        _new = sorted(os.path.relpath(p, _inbox).replace("\\", "/") for p in _never if os.path.getmtime(p) >= _cut)
        _con = _sqlite3.connect("file:" + os.path.expanduser("~/math-brain-build/local/population/brain.sqlite3") + "?mode=ro", uri=True)
        _nodes, _newest = _con.execute("select count(*), max(created_at) from nodes").fetchone()
        row["instruments"]["inflow"] = {"inbox_bundles": len(_bundles), "never_in_ledger": len(_never),
                                        "new_pending": len(_new), "new_pending_names": _new[:10],
                                        "store_nodes": _nodes, "newest_node": _newest}
    except Exception as e:  # noqa: BLE001
        row["instruments"]["inflow"] = {"error": str(e)[:120]}

    # 2026-09-11 evening: a run launched under heavy load (four evaluators and two capture passes on the same
    # machine) read the health check at 212 s instead of 19 s and timed the hook-path pass out; it wrote ERROR
    # and mailed the mesh, and its row landed after a clean PASS. A timed-out instrument or a health check ten
    # times its normal cost is contention, not a regression: the verdict is INCONCLUSIVE, the mesh hears it
    # once with that word, and the panel keeps the last decided verdict beside it.
    timed_out = any(v.get("rc") == 124 for v in row["instruments"].values())
    slow_health = float(row["instruments"]["healthcheck"].get("seconds") or 0) > 120
    # 2026-09-25: hook calls that timed out twice inside the hook-path pass are the same contention at row level: the
    # pass exits 0 and scores those questions silent, which read as a served-theorem breach before the evaluator counted them
    rows_timed_out = int((row["instruments"].get("hookpath") or {}).get("timed_out_rows") or 0)
    health_unjudged = row["instruments"]["healthcheck"].get("rc") == 3
    if timed_out or slow_health or rows_timed_out or health_unjudged:
        row["verdict"] = "INCONCLUSIVE"
        row["inconclusive_because"] = (("instrument timeout" if timed_out else "")
                                       + (" health check %.0f s" % row["instruments"]["healthcheck"]["seconds"] if slow_health else "")
                                       + (" health check could not judge a row (timed out twice)" if health_unjudged else "")
                                       + (" %d hook-path calls timed out twice" % rows_timed_out if rows_timed_out else "")
                                       + (" the run was out of time before %s" % ", ".join(_GATE["skipped"])
                                          if _GATE["skipped"] else "")).strip()
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
    pb = row["instruments"].get("proofbanks", {})
    if pb and "named_title" in pb:
        md.append("- real proofs (the results a pasted proof cites, in a served title): other papers %s (%s of %s proofs), quadratic-form papers %s (%s of %s), held-out papers %s (%s of %s); shuffled control %s"
                  % (pb.get("named_title"), pb.get("named_items"), pb.get("named_n"), pb.get("qform_title"), pb.get("qform_items"), pb.get("qform_n"),
                     pb.get("heldout_title"), pb.get("heldout_items"), pb.get("heldout_n"), pb.get("shuffled")))
    pp = row["instruments"].get("paraphrase", {})
    if pp and "para_node" in pp:
        md.append("- the ledger questions in other words (answer node served, recorded not floored): paraphrase %s of %s (served %s, "
                  "value %s), stored wording %s of %s" % (pp.get("para_node"), pp.get("para_n"), pp.get("para_served"),
                                                          pp.get("para_value"), pp.get("stored_node"), pp.get("stored_n")))
        if "natural_where_node" in pp or "natural_known_node" in pp:
            md.append("- the same descriptions in natural questions (template above): 'where is the open problem about X posed' "
                      "%s of %s, 'what is known about X' %s of %s" % (pp.get("natural_where_node"), pp.get("natural_where_n"),
                                                                     pp.get("natural_known_node"), pp.get("natural_known_n")))
    ov = row["instruments"].get("ownvoice", {})
    if ov and "served" in ov:
        md.append("- owner-voice probability bank (his words, the campaign's own results): served %s, result nodes %s (served cards alone %s), any %s; rotated-key control %s (n=%s)"
                  % (ov.get("served"), ov.get("result"), ov.get("result_strict"), ov.get("any"), ov.get("rotated_key_hits"), ov.get("n")))
    inf = row["instruments"].get("inflow", {})
    if inf and "error" not in inf:
        md.append("- inflow: store %s nodes, newest %s; inbox bundles %s, never in the ledger %s (%s since 09-12, the "
                  "rest the deliberate corpus exclusion%s)"
                  % (inf.get("store_nodes"), str(inf.get("newest_node"))[:16], inf.get("inbox_bundles"), inf.get("never_in_ledger"),
                     inf.get("new_pending"), (": " + ", ".join(inf.get("new_pending_names") or [])) if inf.get("new_pending") else ""))
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
