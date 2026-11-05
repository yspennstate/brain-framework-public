"""Analysis of the show-or-hold trials (owner, 6 Oct 2026 13:33; ANALYSIS_SPEC version 3 with the round-7 restrictions
below). Reads the logs, writes live/trial_analysis.json, prints a short report. It switches nothing: changing
policy.json stays a human step, and decision_prerequisites_met stays false while the guardrails are uncomputed.

Trial 1, reflex lines. Unit: (receiver, reflex) at its FIRST assignment row at or after T1_VALID (the round-4 swap,
15:22:25 his clock). A pair with any assignment row before T1_VALID is left out, whatever order the rows come in (two
passes, round 6 path 2). Outcome: the same reflex suppressed by the backoff in the same parent session within 30 minutes
after t0. A unit enters the primary contrast only when all of these hold, and each failure is counted by arm:
  - its window has closed;
  - t0 >= T1_COVERAGE, the moment the outcome log's own write failures began to be recorded (before it a lost
    suppression row left no trace);
  - coverage: the hook wrote a row for the same parent session at or after t0 + 30 min (fire, suppression, assignment
    or refill log), and no emit failure was recorded for that session inside the window. The latest row of the whole
    file is not evidence for any one session (round 6 path 1);
  - fresh: the reflex never reached that session at or before t0 in any call but the unit's own, by any route (fire
    log, refill log). The unit's own call is the call id on its assignment row; there is no time gap, so an exposure
    seconds earlier in another call counts (round 7 review, B: a trigger revision resets the backoff, so a nearby
    exposure is not proof of the same call). A unit whose row carries no call id is call_identity_missing. The fire
    log's history must cover the session from its first transcript line, with no recorded fire-log failure before t0;
  - child-free in the window: every Agent, Task or Workflow launch made up to the window's end, in the parent or
    inside one of its subagents, has evidence that it ended before t0 (a task notification naming its tool-use id with
    a terminal status, or the tool result of a call that did not run in the background); no subagent transcript has
    lines inside the window; no child receiver row of that parent falls in it. A launch with no evidence of its end
    leaves the unit child_status_unverifiable: a quiet child is not a finished one (round 7 review, A). A session with
    no transcript at all (a Codex session, a lost file) is left out as no_transcript.
Subagent receivers are reported apart. Effect: risk difference shown minus held; p by re-randomization under other
salts of the same rule; interval: a bootstrap over parent sessions.

Trial 2, memory and place lines. Units: live/trial_cohort.json (first exposure per session, channel and key). The file
is read as missing, unreadable, wrong_phase (its 'since' is not T2_START) or ok; only ok is analysed, so an unreadable
file is never an empty cohort. Records whose identity is not an evaluation id (legacy, matched by timestamp, migrated)
are counted apart and left out of the contrast. The learner's own cohort_status is carried through.

Trial 3, exact-name cards: exposures by arm only; its outcome is not wired."""
import datetime
import glob
import hashlib
import json
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict

DREAM = os.path.expanduser("~/.claude/dreaming")
LIVE = os.path.expanduser("~/.claude/brain_graph/live")
PROJECTS = os.path.expanduser("~/.claude/projects")
PRIMARY = os.path.join(DREAM, "instinct_primary_ab.tsv")
SUPPRESSED = os.path.join(DREAM, "instinct_suppressed.tsv")
FIRES = os.path.join(DREAM, "instinct_fires.tsv")
REFILLS = os.path.join(DREAM, "instinct_refills.tsv")
EMIT_ERRORS = os.path.join(DREAM, "instinct_emit_errors.tsv")
EXACT = os.path.join(DREAM, "exact_name_ab.tsv")
COHORT = os.path.join(LIVE, "trial_cohort.json")
STATUS = os.path.join(LIVE, "LIVE_STATUS.json")
CACHE = os.path.join(LIVE, "trial_transcript_cache.json")
OUT = os.path.join(LIVE, "trial_analysis.json")
T1_VALID = 1791289345
T1_COVERAGE = 1791293078    # the round-7 swap: the first second the outcome log's write failures were recorded
T2_START = 1791284081.0
WINDOW = 1800
TERMINAL = ("completed", "failed", "killed", "stopped", "cancelled", "canceled", "error", "timeout", "timed_out")
CALL_RX = re.compile(r"^[0-9a-f]{12}$")
RESULT_RX = re.compile(rb'"tool_use_id"\s*:\s*"(toolu_[A-Za-z0-9_]+)"')
NOTE_RX = re.compile(r"\s*<task-notification>(.*?)</task-notification>", re.S)   # matched at the start of a record only
NOTE_USE_RX = re.compile(r"<tool-use-id>(toolu_[A-Za-z0-9_]+)</tool-use-id>")
NOTE_TASK_RX = re.compile(r"<task-id>([A-Za-z0-9_-]+)</task-id>")
NOTE_ST_RX = re.compile(r"<status>([A-Za-z_]+)</status>")
ACK_ID_RX = re.compile(r"(?:Task ID|agentId)\s*:\s*([A-Za-z0-9_-]+)")
STOP_RX = re.compile(r"Successfully stopped task: ([A-Za-z0-9_-]+)")
SALTS = 1000
BOOT = 1000
LAUNCH_TOOLS = ("Agent", "Task", "Workflow")
LAST_UNITS = []             # (receiver, reflex, arm, y) of the units the last trial1 run admitted, for the controls


def rows(path):
    """Tab rows split on LF only: a bare CR inside a field (a pasted command) must not tear a row in two."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="replace", newline="\n") as f:
        return [l.rstrip("\n").rstrip("\r").split("\t") for l in f if l.strip()]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _parent(rec):
    return (rec or "").split("~")[0]


def rd(units):
    s = [u["y"] for u in units if u["arm"] == "shown"]
    h = [u["y"] for u in units if u["arm"] == "held"]
    if not s or not h:
        return None, len(s), len(h), sum(s), sum(h)
    return sum(s) / len(s) - sum(h) / len(h), len(s), len(h), sum(s), sum(h)


def boot(units, key, rng):
    by = defaultdict(list)
    for u in units:
        by[u[key]].append(u)
    ks = list(by)
    vals = []
    for _ in range(BOOT):
        sample = [u for k in (rng.choice(ks) for _ in ks) for u in by[k]]
        d = rd(sample)[0]
        if d is not None:
            vals.append(d)
    vals.sort()
    return [round(vals[int(0.025 * len(vals))], 4), round(vals[int(0.975 * len(vals)) - 1], 4)] if len(vals) > 20 else None


# ---- transcripts: session start, subagent activity, launches ------------------------------------------------------

def _ts_of(obj):
    try:
        return datetime.datetime.fromisoformat(str(obj.get("timestamp")).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _first_last(path):
    """First and last top-level timestamp of a JSONL transcript, from its head and its last 256 KB."""
    first = last = None
    try:
        with open(path, "rb") as f:
            for _ in range(200):
                line = f.readline()
                if not line:
                    break
                try:
                    first = _ts_of(json.loads(line))
                except Exception:
                    continue
                if first is not None:
                    break
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 262144))
            for line in reversed(f.read().split(b"\n")):
                try:
                    last = _ts_of(json.loads(line))
                except Exception:
                    continue
                if last is not None:
                    break
    except OSError:
        pass
    return first, last


def _call(opp):
    """The 12-hex call id that opens an opportunity id (call:reflex), or None."""
    x = (opp or "").split(":")[0]
    return x if CALL_RX.match(x) else None


def _lifecycle(path, cache):
    """id -> [launch_ts, tool, end_ts, background, task_id] for every Agent, Task or Workflow call in a transcript, with
    the earliest TYPED evidence that it ended. Marker text quoted in a message or in another tool's output is content,
    never evidence (round 8 review). Accepted:
      - a queue-operation record whose own content opens with a task notification (that leading block only; 41,938
        real records held one block each, 113 with harness text after it) with a terminal status naming the launch's
        tool-use id, or its task id (the id the launch's own acknowledgment gave: 'agentId: ...' for an agent,
        'Task ID: ...' for a workflow);
      - the result of a TaskStop invocation, bound to it by tool-use id and not an error, saying
        'Successfully stopped task: <id>' for the very task id the invocation named;
      - an error result for the launch itself (it never ran: 'Concurrent subagent limit reached', a refused call);
      - for a call that did not run in the background, its own tool result.
    An async-launch acknowledgment is not an ending, and neither is silence. Read incrementally up to the last complete
    line (transcripts only grow)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return {}
    c = cache.get(path)
    if not isinstance(c, dict) or c.get("v") != 4 or c.get("off", 0) > size:
        c = {"v": 4, "off": 0, "launch": {}, "by_task": {}, "stops": {}, "early_use": {}, "early_task": {}}
    off, launch, by_task, stops = c["off"], c["launch"], c["by_task"], c["stops"]
    early_use, early_task = c["early_use"], c["early_task"]

    def end(lid, ts):
        rec = launch.get(lid)
        if rec is not None and ts is not None and (rec[2] is None or ts < rec[2]):
            rec[2] = ts

    def end_task(tid, ts):
        if tid in by_task:
            end(by_task[tid], ts)
        elif ts is not None and (tid not in early_task or ts < early_task[tid]):
            early_task[tid] = ts

    try:
        with open(path, "rb") as f:
            f.seek(off)
            while True:
                line = f.readline()
                if not line or not line.endswith(b"\n"):
                    break
                off += len(line)
                use = b'"tool_use"' in line and any(('"%s"' % t).encode() in line for t in LAUNCH_TOOLS + ("TaskStop",))
                res = b'"tool_result"' in line and any(m.group(1).decode() in launch or m.group(1).decode() in stops
                                                       for m in RESULT_RX.finditer(line))
                note = b'"queue-operation"' in line and b"task-notification" in line
                if not (use or res or note):
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                ts = _ts_of(d)
                for part in ((d.get("message") or {}).get("content") or []) if (use or res) else []:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "tool_use" and part.get("name") in LAUNCH_TOOLS:
                        lid = part.get("id")
                        bg = part.get("name") == "Workflow" or bool((part.get("input") or {}).get("run_in_background"))
                        launch[lid] = [ts, part.get("name"), early_use.pop(lid, None), bg, None]
                    elif part.get("type") == "tool_use" and part.get("name") == "TaskStop":
                        stops[part.get("id")] = str((part.get("input") or {}).get("task_id") or "")
                    elif part.get("type") == "tool_result" and part.get("tool_use_id") in launch:
                        lid = part["tool_use_id"]
                        rec = launch[lid]
                        text = part.get("content")
                        text = text if isinstance(text, str) else json.dumps(text)
                        m = ACK_ID_RX.search(text[:2000])
                        if m and rec[4] is None:
                            rec[4] = m.group(1)
                            by_task[m.group(1)] = lid
                            end(lid, early_task.pop(m.group(1), None))
                        if part.get("is_error") or (not rec[3] and "Async agent launched" not in text[:300]):
                            end(lid, ts)    # a launch refused with an error never ran; a foreground one ends here
                    elif part.get("type") == "tool_result" and part.get("tool_use_id") in stops:
                        target = stops.pop(part["tool_use_id"])
                        text = part.get("content")
                        text = text if isinstance(text, str) else json.dumps(text)
                        m = STOP_RX.search(text[:500])
                        if target and not part.get("is_error") and m and m.group(1) == target:
                            end_task(target, ts)
                content = d.get("content") if note and d.get("type") == "queue-operation" else None
                head = NOTE_RX.match(content) if isinstance(content, str) and ts is not None else None
                if head:    # the payload is the record's leading block alone; text after it (a quoted block too) is content
                    for block in (head.group(1),):
                        st = NOTE_ST_RX.search(block)
                        if not st or st.group(1).lower() not in TERMINAL:
                            continue
                        u, t = NOTE_USE_RX.search(block), NOTE_TASK_RX.search(block)
                        lid = (u.group(1) if u and u.group(1) in launch else None) or (
                            by_task.get(t.group(1)) if t else None)
                        if lid:
                            end(lid, ts)
                        else:
                            if u and (u.group(1) not in early_use or ts < early_use[u.group(1)]):
                                early_use[u.group(1)] = ts
                            if t:
                                end_task(t.group(1), ts)
    except OSError:
        pass
    c["off"] = off
    cache[path] = c
    return launch


def session_index(parent, cache):
    """None when no transcript of this session exists (child status and session start cannot be established)."""
    mains = sorted(set(glob.glob(os.path.join(PROJECTS, "*", parent + ".jsonl"))))
    if not parent or not mains:
        return None
    dirs = set(p for p in glob.glob(os.path.join(PROJECTS, "*", parent)) if os.path.isdir(p))
    dirs |= set(p for p in glob.glob(os.path.join(PROJECTS, "*%s*" % parent)) if os.path.isdir(p))
    kids, kid_paths, mainset = [], [], set(os.path.abspath(m) for m in mains)
    for d in sorted(dirs):
        for root, _dirs, files in os.walk(d):
            for fn in files:
                p = os.path.abspath(os.path.join(root, fn))
                if not fn.endswith(".jsonl") or p in mainset:
                    continue
                s, e = _first_last(p)
                try:
                    s = s if s is not None else os.path.getctime(p)
                    e = e if e is not None else os.path.getmtime(p)
                except OSError:
                    continue
                kids.append((s, max(s, e)))
                kid_paths.append(p)
    starts = [x for x in (_first_last(m)[0] for m in mains) if x is not None]
    launches = [v for p in list(mains) + kid_paths for v in _lifecycle(p, cache).values()]
    return {"start": min(starts) if starts else None, "children": kids, "launches": launches}


def child_status(info, a, b, log_child_times):
    """child_active when a subagent was active inside [a, b]: a launch ended at or after a, a subagent transcript has
    lines spanning into the window, or a child receiver's row falls in it. unverifiable when a launch made up to b has
    no evidence that it ended. child_free otherwise."""
    if info is None:
        return "unverifiable"
    active = unknown = False
    for t, _tool, end, _bg, _tid in info["launches"]:
        if t is not None and t > b:
            continue
        if end is None:
            unknown = True
        elif end >= a:
            active = True
    if any(s <= b and e >= a for s, e in info["children"]) or any(a <= t <= b for t in log_child_times):
        active = True
    return "child_active" if active else ("unverifiable" if unknown else "child_free")


# ---- trial 1 --------------------------------------------------------------------------------------------------------

def trial1(now, rng):
    parsed = []
    for r in rows(PRIMARY):
        if len(r) >= 4 and not r[1].startswith("0000ab") and r[3] in ("shown", "held") and _f(r[0]) is not None:
            parsed.append((_f(r[0]), r[1], r[2], r[3], _call(r[4]) if len(r) > 4 else None))
    pre = {(rec, rid) for t, rec, rid, _a, _c in parsed if t < T1_VALID}          # pass 1: the whole pre-swap history
    first, dropped_pre = {}, set()
    for t, rec, rid, arm, call in sorted(parsed, key=lambda x: x[0]):         # pass 2: first post-swap assignment
        if t < T1_VALID:
            continue
        if (rec, rid) in pre:
            dropped_pre.add((rec, rid))
            continue
        first.setdefault((rec, rid), {"t0": t, "arm": arm, "receiver": rec, "reflex": rid, "parent": _parent(rec),
                                      "call": call})

    seen = defaultdict(float)            # parent -> latest hook-written row of that session, any log
    exposed = defaultdict(list)          # (parent, reflex) -> (time, call id) each time the reflex reached that session
    log_child = defaultdict(list)        # parent -> times of rows written for one of its subagent receivers
    hist_start = None
    for r in rows(FIRES):
        t = _f(r[0]) if r else None
        if t is None or len(r) < 6:
            continue
        hist_start = t if hist_start is None else min(hist_start, t)
        seen[r[5]] = max(seen[r[5]], t)
        exposed[(r[5], r[3])].append((t, _call(r[7]) if len(r) > 7 else None))
    supp = defaultdict(list)
    for r in rows(SUPPRESSED):
        t = _f(r[0]) if r else None
        if t is None or len(r) < 6:
            continue
        seen[r[5]] = max(seen[r[5]], t)
        if r[4] == "backoff":
            supp[(r[5], r[3])].append(t)
    for t, rec, rid, _a, _c in parsed:
        seen[_parent(rec)] = max(seen[_parent(rec)], t)
        if "~" in rec:
            log_child[_parent(rec)].append(t)
    for r in rows(REFILLS):
        t = _f(r[0]) if r else None
        if t is None or len(r) < 5:
            continue
        p = _parent(r[1])
        seen[p] = max(seen[p], t)
        if "~" in r[1]:
            log_child[p].append(t)
        for rid in ([r[2]] if r[4] != "held" else []) + [x for x in r[3].split(",") if x]:
            exposed[(p, rid)].append((t, _call(r[5]) if len(r) > 5 else None))
    errs = defaultdict(list)             # parent -> [(t, parts)] of recorded emit failures
    for r in rows(EMIT_ERRORS):
        t = _f(r[0]) if r else None
        if t is not None and len(r) >= 3:
            errs[_parent(r[1])].append((t, r[2]))

    try:
        cache = json.load(open(CACHE, encoding="utf-8"))
    except Exception:
        cache = {}
    idx = {}
    excluded = defaultdict(Counter)      # reason -> Counter(arm)
    units, sub_units, incomplete = [], [], 0
    exposures_by_rec = defaultdict(list)
    for (rec, rid), u in first.items():
        exposures_by_rec[rec].append((u["t0"], rid, u["arm"]))
    for (rec, rid), u in sorted(first.items(), key=lambda kv: kv[1]["t0"]):
        t0, p, a, b = u["t0"], u["parent"], u["t0"], u["t0"] + WINDOW
        if now < b:
            incomplete += 1
            continue
        reason = None
        if T1_COVERAGE is None or t0 < T1_COVERAGE:
            reason = "pre_coverage"
        elif seen.get(p, 0) < b:
            reason = "coverage_unproven"
        elif any(a - 5 <= te <= b for te, _parts in errs.get(p, ())):
            reason = "emit_failure_in_window"
        if reason is None:
            if p not in idx:
                idx[p] = session_index(p, cache)
            info = idx[p]
            if info is None:
                reason = "no_transcript"     # neither the session's start nor its subagents can be established
            elif info["start"] is None or hist_start is None or hist_start > info["start"] or any(
                    "fire_log" in parts and te < t0 for te, parts in errs.get(p, ())):
                reason = "history_incomplete"
            elif u["call"] is None:
                reason = "call_identity_missing"
            elif any(te <= t0 and ce != u["call"] for te, ce in exposed.get((p, rid), ())):
                reason = "prior_exposure"
            elif "~" not in rec:
                cs = child_status(info, a, b, log_child.get(p, ()))
                reason = None if cs == "child_free" else ("child_active_in_window" if cs == "child_active"
                                                          else "child_status_unverifiable")
        if reason is not None:
            excluded[reason][u["arm"]] += 1
            continue
        u["y"] = int(any(t0 < t <= b for t in supp.get((p, rid), [])))
        prior = [x for (t, r2, x) in exposures_by_rec[rec] if t < t0 and r2 != rid]
        u["ctx_shown"], u["ctx_held"] = prior.count("shown"), prior.count("held")
        (sub_units if "~" in rec else units).append(u)
    LAST_UNITS[:] = [(u["receiver"], u["reflex"], u["arm"], u["y"]) for u in units + sub_units]
    try:
        tmp = CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        os.replace(tmp, CACHE)
    except Exception:
        pass
    out = {"valid_from": T1_VALID, "coverage_from": T1_COVERAGE, "units_main": len(units),
           "units_subagent": len(sub_units), "incomplete": incomplete,
           "excluded": {k: dict(v) for k, v in sorted(excluded.items())},
           "pre_swap_pairs_excluded": len(dropped_pre), "sessions": len(set(u["parent"] for u in units)),
           "guardrails": "not computed (tool errors and his corrections after t0 are not wired)",
           "decision_prerequisites_met": False}
    d, ns, nh, ys, yh = rd(units)
    out.update({"shown": ns, "held": nh, "shown_rate": round(ys / ns, 4) if ns else None,
                "held_rate": round(yh / nh, 4) if nh else None, "risk_difference": None if d is None else round(d, 4)})
    if d is not None:
        ge = 0
        for k in range(1, SALTS + 1):
            alt = [dict(u, arm="held" if int.from_bytes(hashlib.sha256(("primary%d:%s:%s" % (k, u["receiver"], u["reflex"]))
                                                                         .encode("utf-8")).digest()[:4], "big") % 4 == 0 else "shown")
                   for u in units]
            dk = rd(alt)[0]
            ge += dk is not None and abs(dk) >= abs(d)
        out["p_rerandomization"] = round((1 + ge) / (1 + SALTS), 4)
        out["ci95_session_bootstrap"] = boot(units, "parent", rng)
    for arm in ("shown", "held"):
        g = [u for u in units if u["arm"] == arm]
        out["context_" + arm] = {"mean_other_shown": round(sum(u["ctx_shown"] for u in g) / len(g), 3) if g else None,
                                 "mean_other_held": round(sum(u["ctx_held"] for u in g) / len(g), 3) if g else None}
    return out


# ---- trial 2 --------------------------------------------------------------------------------------------------------

def _read_cohort():
    if not os.path.exists(COHORT):
        return "missing", {}
    try:
        with open(COHORT, encoding="utf-8") as f:
            c = json.load(f)
    except Exception:
        return "unreadable", {}
    if not isinstance(c, dict) or not isinstance(c.get("first"), dict):
        return "unreadable", {}
    if c.get("since") != T2_START:
        return "wrong_phase", {}
    return "ok", c["first"]


def _learner_status():
    try:
        st = json.load(open(STATUS, encoding="utf-8"))
    except Exception:
        return None
    todo = [st]
    while todo:
        o = todo.pop()
        if isinstance(o, dict):
            if isinstance(o.get("trial_mem"), dict):
                return o["trial_mem"].get("cohort_status")
            todo.extend(v for v in o.values() if isinstance(v, (dict, list)))
        elif isinstance(o, list):
            todo.extend(o)
    return None


def _identity(v):
    return v.get("identity") or ("eid" if v.get("eid") is not None else "none")


def trial2(rng):
    status, cohort = _read_cohort()
    learner = _learner_status()
    out = {"input_status": status, "learner_cohort_status": learner, "since": T2_START}
    if status != "ok":
        out["note"] = ("no analysis: the cohort file is %s, and a missing, unreadable or wrong-phase file is never read "
                       "as an empty cohort" % status)
        return out
    for ch, (sk, hk) in (("mem", ("mem", "held_mem")), ("place", ("place", "held_place"))):
        allu = [dict(v, arm="shown" if v.get("kind") == sk else "held") for v in cohort.values() if v.get("kind") in (sk, hk)]
        verified = [u for u in allu if _identity(u) == "eid"]
        known = [dict(u, y=int(bool(u["ok"]))) for u in verified if u.get("ok") is not None]
        d, ns, nh, ys, yh = rd(known)
        o = {"units": len(allu), "identity_unverified": len(allu) - len(verified),
             "unknown": len(verified) - len(known), "shown": ns, "held": nh, "shown_taken": ys, "held_taken": yh,
             "risk_difference": None if d is None else round(d, 4), "screen_reached": ns >= 100 and nh >= 100,
             "certified": learner == "ok",
             "guardrails": "not computed (tool errors and his corrections after the line are not wired)",
             "decision_prerequisites_met": False}
        if d is not None:
            ge = sum(1 for _ in range(SALTS)
                     if (lambda dk: dk is not None and abs(dk) >= abs(d))(rd([dict(u, arm=rng.choice(("shown", "held")))
                                                                              for u in known])[0]))
            o["p_rerandomization_fair_coin"] = round((1 + ge) / (1 + SALTS), 4)
            o["ci95_session_bootstrap"] = boot(known, "sid", rng)
        out[ch] = o
    return out


def trial3():
    r = [x for x in rows(EXACT) if len(x) >= 5 and not x[1].startswith("0000ab")]
    return {"exposures": len(r), "arms": dict(Counter(x[4] for x in r)), "sessions": len(set(x[1] for x in r)),
            "outcome": "not wired yet (a tool error within five calls on a command naming the same file)"}


def main():
    rng = random.Random(20261006)
    now = time.time()
    res = {"at": now, "trial1_reflex_lines": trial1(now, rng), "trial2_diffuse_lines": trial2(rng),
           "trial3_exact_name": trial3()}
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    os.replace(tmp, OUT)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
