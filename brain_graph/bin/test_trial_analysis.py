"""Controls for trial_analysis.py, rounds 7 and 8 (dot D7CB6565's round-6 and round-7 dispositions), on synthetic logs
and transcripts in a temporary directory. Every restriction is checked both ways, so a PASS means the check could have
failed. Transcript fixtures follow the real format: an Agent or Workflow tool_use with an id, an async-launch
acknowledgment as its tool result, and a queue-operation task notification carrying the tool-use id and a status.
Run: python test_trial_analysis.py"""
import datetime
import importlib.util
import json
import os
import random
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("trial_analysis_test", os.path.join(HERE, "trial_analysis.py"))
ta = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ta)
D = tempfile.mkdtemp()
for name, fn in (("PRIMARY", "p.tsv"), ("SUPPRESSED", "s.tsv"), ("FIRES", "f.tsv"), ("REFILLS", "r.tsv"),
                 ("EMIT_ERRORS", "e.tsv"), ("EXACT", "x.tsv"), ("COHORT", "c.json"), ("STATUS", "st.json"),
                 ("CACHE", "cache.json"), ("OUT", "o.json")):
    setattr(ta, name, os.path.join(D, fn))
ta.PROJECTS = os.path.join(D, "projects")
ta.SALTS = ta.BOOT = 50
V = ta.T1_VALID
C = V + 1000
ta.T1_COVERAGE = C
W = ta.WINDOW
T0 = C + 100
NOW = T0 + W + 5000
fails = 0


def check(cond, what):
    global fails
    print(("PASS  " if cond else "FAIL  ") + what)
    fails += 0 if cond else 1


def call(n):
    return "%012x" % n


def opp(n, rid):
    return call(n) + ":" + rid


def put(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join("\t".join(map(str, r)) + "\n" for r in rows))


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def launch_lines(launches, tag):
    """launches: dicts with t, end (or None), bg (default True), status (default completed), tool (default Agent),
    by ('tool': the notice names the tool-use id and the task id; 'task': the task id only), stop (time of a TaskStop
    naming the task id, or None)."""
    out = []
    for i, L in enumerate(launches):
        lid = "toolu_%s%d" % (tag, i)
        bg, tool = L.get("bg", True), L.get("tool", "Agent")
        tid = ("w" if tool == "Workflow" else "a") + "%s%d" % (tag.lower(), i)
        out.append((L["t"], {"type": "assistant", "timestamp": iso(L["t"]), "message": {"content": [
            {"type": "tool_use", "id": lid, "name": tool, "input": {"run_in_background": bg}}]}}))
        if L.get("error"):
            out.append((L["t"] + 1, {"type": "user", "timestamp": iso(L["t"] + 1), "message": {"content": [
                {"type": "tool_result", "tool_use_id": lid, "is_error": True,
                 "content": "Concurrent subagent limit reached. Do not retry."}]}}))
            continue
        if bg:
            ack = ("Async agent launched successfully.\nagentId: %s (internal ID)" % tid if tool != "Workflow"
                   else "Workflow launched in background. Task ID: %s\nSummary: x" % tid)
            out.append((L["t"] + 1, {"type": "user", "timestamp": iso(L["t"] + 1), "message": {"content": [
                {"type": "tool_result", "tool_use_id": lid, "content": [{"type": "text", "text": ack}]}]}}))
            if L.get("end") is not None:
                ids = "<task-id>%s</task-id>\n" % tid + (
                    "<tool-use-id>%s</tool-use-id>\n" % lid if L.get("by", "tool") == "tool" else "")
                out.append((L["end"], {"type": "queue-operation", "operation": "enqueue", "timestamp": iso(L["end"]),
                                       "content": "<task-notification>\n%s<status>%s</status>\n</task-notification>" % (
                                           ids, L.get("status", "completed"))}))
            if L.get("stop") is not None:
                sid = "toolu_S%s%d" % (tag, i)
                out.append((L["stop"] - 1, {"type": "assistant", "timestamp": iso(L["stop"] - 1), "message": {"content": [
                    {"type": "tool_use", "id": sid, "name": "TaskStop", "input": {"task_id": tid}}]}}))
                out.append((L["stop"], {"type": "user", "timestamp": iso(L["stop"]), "message": {"content": [
                    {"type": "tool_result", "tool_use_id": sid,
                     "content": "Successfully stopped task: %s (the job)" % tid}]}}))
        elif L.get("end") is not None:
            out.append((L["end"], {"type": "user", "timestamp": iso(L["end"]), "message": {"content": [
                {"type": "tool_result", "tool_use_id": lid, "content": "the agent's final report"}]}}))
    return out


def write_jsonl(path, items):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(json.dumps(d) + "\n" for _t, d in sorted(items, key=lambda x: x[0])))


def transcript(parent, start, launches=(), kids=(), nested=(), extra=()):
    d = os.path.join(ta.PROJECTS, "proj")
    os.makedirs(os.path.join(d, parent, "subagents"), exist_ok=True)
    write_jsonl(os.path.join(d, parent + ".jsonl"),
                [(start, {"type": "user", "timestamp": iso(start), "message": {"content": "hi"}})] + launch_lines(launches, "L")
                + list(extra))
    for i, (s, e) in enumerate(kids):
        items = [(s, {"type": "user", "timestamp": iso(s)}), (e, {"type": "a", "timestamp": iso(e)})]
        if i == 0 and nested:
            items += launch_lines(nested, "N")
        write_jsonl(os.path.join(d, parent, "subagents", "agent-k%d.jsonl" % i), items)


def reset():
    shutil.rmtree(ta.PROJECTS, ignore_errors=True)
    for p in (ta.PRIMARY, ta.SUPPRESSED, ta.FIRES, ta.REFILLS, ta.EMIT_ERRORS, ta.CACHE):
        if os.path.exists(p):
            os.remove(p)


def run(primary, supp=(), fires=(), refills=(), errs=(), now=NOW):
    put(ta.PRIMARY, primary)
    put(ta.SUPPRESSED, supp)
    put(ta.FIRES, [[1000, "PreToolUse", "act", "zz", "{}", "old-session", "regex", call(999)]] + list(fires))
    put(ta.REFILLS, refills)
    put(ta.EMIT_ERRORS, errs)
    o = ta.trial1(now, random.Random(1))
    return o, {(r, x): (a, y) for r, x, a, y in ta.LAST_UNITS}


def later(p, t=T0 + W + 10):      # a hook row for session p after the window closes
    return [t, "PreToolUse", "act", "other_reflex", "cap", p]


def fire(t, p, rid, c, route="regex"):
    return [t, "PreToolUse", "act", rid, "{}", p, route] + ([c] if c is not None else [])


# ---- baseline ----------------------------------------------------------------------------------------------------------
reset()
transcript("PA", C - 5000)
o, u = run([[T0, "PA", "rA", "shown", opp(1, "rA")], [T0, "PA", "rB", "held", opp(1, "rB")]],
           supp=[[T0 + 600, "PreToolUse", "act", "rA", "backoff", "PA"], later("PA")])
check(u.get(("PA", "rA")) == ("shown", 1) and u.get(("PA", "rB")) == ("held", 0),
      "baseline: both units admitted, the repeat read as 1 and its absence as 0 (%s)" % u)

# ---- path 1, coverage (round 7) ----------------------------------------------------------------------------------------
reset()
transcript("PB", C - 5000)
U = [[T0, "PB", "rA", "shown", opp(2, "rA")]]
o, u = run(U, supp=[[T0 + 5000, "PreToolUse", "act", "zz", "cap", "PZ"]])
check(("PB", "rA") not in u and o["excluded"].get("coverage_unproven") == {"shown": 1},
      "path 1: a later row from an unrelated session does not certify the target session")
o, u = run(U, supp=[[T0 + 5000, "PreToolUse", "act", "zz", "cap", "PZ"], later("PB")])
check(u.get(("PB", "rA")) == ("shown", 0), "path 1, control: the session's own later row certifies it")
o, u = run(U, supp=[later("PB")], errs=[[T0 + 100, "PB", "suppressed_log", "rA", call(7)]])
check(("PB", "rA") not in u and o["excluded"].get("emit_failure_in_window") == {"shown": 1},
      "path 1: an emit failure inside the window leaves the unit unknown")
o, u = run(U, supp=[later("PB")], errs=[[T0 + W + 100, "PB", "suppressed_log", "rA", call(7)]])
check(("PB", "rA") in u, "path 1, control: a failure after the window does not")
o, u = run([[C - 100, "PB", "rA", "held", opp(2, "rA")]], supp=[later("PB", C - 100 + W + 10)])
check(("PB", "rA") not in u and o["excluded"].get("pre_coverage") == {"held": 1},
      "path 1: a unit before the outcome log's failures were recorded is not certified")
saved, ta.T1_COVERAGE = ta.T1_COVERAGE, None
o, u = run(U, supp=[later("PB")])
check(not u and o["excluded"].get("pre_coverage") == {"shown": 1}, "path 1: with no coverage start set, nothing is certified")
ta.T1_COVERAGE = saved
o, u = run(U, supp=[later("PB")], now=T0 + 100)
check(not u and o["incomplete"] == 1, "a window that has not closed is incomplete, not a zero")

# ---- path 2, freshness (round 7) ----------------------------------------------------------------------------------------
reset()
transcript("PD", C - 5000)
U = [[T0, "PD", "rA", "shown", opp(3, "rA")]]
o, u = run([[T0, "PD", "rA", "shown", opp(3, "rA")], [V - 100, "PD", "rA", "held", opp(4, "rA")],
            [T0, "PD", "rB", "shown", opp(3, "rB")]],
           supp=[[T0 + 600, "PreToolUse", "act", "rA", "backoff", "PD"], later("PD")])
check(("PD", "rA") not in u and o["pre_swap_pairs_excluded"] == 1 and ("PD", "rB") in u,
      "path 2: a post-swap row filed BEFORE its pre-swap row contributes nothing (%s)" % sorted(u))
check(o["shown"] == 1 and o["held"] == 0, "path 2: the excluded pair is absent from the arm counts, not just counted")
o, u = run(U, supp=[later("PD")], refills=[[T0 - 3000, "PD", "rA", "", "open", opp(5, "rA")]])
check(("PD", "rA") not in u and o["excluded"].get("prior_exposure") == {"shown": 1},
      "path 2: a refill exposure with no assignment row before t0 excludes the pair")
o, u = run(U, supp=[later("PD")], refills=[[T0 - 3000, "PD", "rA", "", "held", opp(5, "rA")]])
check(("PD", "rA") in u, "path 2, control: a held refill (never shown) is not an exposure")
o, u = run(U, supp=[later("PD")], fires=[fire(T0 - 2000, "PD", "rA", call(6), "graph")])
check(("PD", "rA") not in u and o["excluded"].get("prior_exposure") == {"shown": 1},
      "path 2: a fire by a route outside the trial (graph) before t0 excludes the pair")
o, u = run(U, supp=[later("PD")], fires=[fire(T0, "PD", "rA", call(3))])
check(("PD", "rA") in u, "path 2, control: the unit's own fire, in its own call, is not a prior exposure")
o, u = run(U, supp=[later("PD")], errs=[[T0 - 4000, "PD", "fire_log", "rA", call(8)]])
check(("PD", "rA") not in u and o["excluded"].get("history_incomplete") == {"shown": 1},
      "path 2: a recorded fire-log failure before t0 makes the history incomplete")
reset()
transcript("PD", 500)
o, u = run(U, supp=[later("PD")])
check(("PD", "rA") not in u and o["excluded"].get("history_incomplete") == {"shown": 1},
      "path 2: a session older than the exposure history is not certified fresh")

# ---- freshness by exact call identity (round 8, qualification B) ----------------------------------------------------------
reset()
transcript("PD", C - 5000)
o, u = run(U, supp=[later("PD")], fires=[fire(T0 - 30, "PD", "rA", call(9))])
check(("PD", "rA") not in u and o["excluded"].get("prior_exposure") == {"shown": 1},
      "B: the same reflex shown 30 s before t0 in another call is a prior exposure (the old 60 s gap admitted it)")
o, u = run(U, supp=[later("PD")], fires=[fire(T0 - 30, "PD", "rA", None)])
check(("PD", "rA") not in u and o["excluded"].get("prior_exposure") == {"shown": 1},
      "B: an earlier fire with no call id cannot be the unit's own call and counts as prior")
o, u = run(U, supp=[later("PD")], fires=[fire(T0 + 30, "PD", "rA", call(9))])
check(("PD", "rA") in u, "B, control: an exposure after t0 in another call is not prior")
o, u = run(U, supp=[later("PD")], refills=[[T0, "PD", "rA", "", "open", opp(3, "rA")]])
check(("PD", "rA") in u, "B, control: a refill row in the unit's own call is not prior")
o, u = run([[T0, "PD", "rA", "shown", "x"]], supp=[later("PD")])
check(("PD", "rA") not in u and o["excluded"].get("call_identity_missing") == {"shown": 1},
      "B: a unit whose assignment row carries no call id is not certified fresh")

# ---- path 3, children (round 7) -----------------------------------------------------------------------------------------
reset()
P3 = [[V - 100, "PF~c", "rX", "shown", opp(10, "rX")], [T0, "PF", "rA", "shown", opp(11, "rA")]]
o, u = run(P3, supp=[later("PF")])
check(("PF", "rA") not in u and o["excluded"].get("no_transcript") == {"shown": 1},
      "path 3: a child filtered out for pre-swap history does not make its parent certified child-free")
transcript("PF", C - 5000, launches=[{"t": T0 - 200, "end": T0 + 400}], kids=[(T0 - 150, T0 + 400)])
o, u = run(P3, supp=[later("PF")])
check(("PF", "rA") not in u and o["excluded"].get("child_active_in_window") == {"shown": 1},
      "path 3: a subagent running inside the window excludes the unit")
reset()
transcript("PF", C - 5000, launches=[{"t": C - 3000, "end": C - 2000}], kids=[(C - 2990, C - 2000)])
o, u = run(P3, supp=[later("PF")])
check(u.get(("PF", "rA")) == ("shown", 0), "path 3, control: a child whose end is recorded before the window leaves it child-free")
reset()
transcript("PF", C - 5000, launches=[{"t": C - 3000, "end": C - 2000}, {"t": T0 + 100, "end": None}],
           kids=[(C - 2990, C - 2000)])
o, u = run(P3, supp=[later("PF")])
check(("PF", "rA") not in u and o["excluded"].get("child_status_unverifiable") == {"shown": 1},
      "path 3: a launch in the window with no recorded end is unverifiable")
reset()
transcript("PF", C - 5000)
o, u = run(P3 + [[T0 + 300, "PF~d", "rY", "held", opp(12, "rY")]], supp=[later("PF")])
check(("PF", "rA") not in u and o["excluded"].get("child_active_in_window", {}).get("shown") == 1,
      "path 3: a child receiver's row inside the window excludes the parent's unit")

# ---- child lifecycle (round 8, qualification A) ---------------------------------------------------------------------------
reset()
transcript("PF", C - 5000, launches=[{"t": T0 - 500, "end": None}], kids=[(T0 - 490, T0 - 400)])
o, u = run(P3, supp=[later("PF")])
check(("PF", "rA") not in u and o["excluded"].get("child_status_unverifiable") == {"shown": 1},
      "A: a child whose transcript went quiet before t0 but has no recorded end is unverifiable, not child-free")
reset()
transcript("PF", C - 5000, launches=[{"t": T0 - 500, "end": T0 - 300}], kids=[(T0 - 490, T0 - 400)])
o, u = run(P3, supp=[later("PF")])
check(u.get(("PF", "rA")) == ("shown", 0), "A, control: the same child with a terminal notification before t0 is finished")
reset()
transcript("PF", C - 5000, launches=[{"t": T0 - 500, "end": T0 + 200}], kids=[(T0 - 490, T0 - 400)])
o, u = run(P3, supp=[later("PF")])
check(("PF", "rA") not in u and o["excluded"].get("child_active_in_window") == {"shown": 1},
      "A: a child whose recorded end falls inside the window was active in it")
reset()
transcript("PF", C - 5000, launches=[{"t": T0 - 500, "end": T0 - 300, "status": "running"}])
o, u = run(P3, supp=[later("PF")])
check(("PF", "rA") not in u and o["excluded"].get("child_status_unverifiable") == {"shown": 1},
      "A: a notification with a non-terminal status is not an end")
reset()
transcript("PF", C - 5000, launches=[{"t": T0 - 500, "end": None}])
o, u = run(P3, supp=[later("PF")])
check(("PF", "rA") not in u and o["excluded"].get("child_status_unverifiable") == {"shown": 1},
      "A: an async-launch acknowledgment alone is not an end")
reset()
transcript("PF", C - 5000, launches=[{"t": T0 - 500, "end": T0 - 300, "bg": False}])
o, u = run(P3, supp=[later("PF")])
check(u.get(("PF", "rA")) == ("shown", 0), "A, control: a foreground launch ends with its own tool result")
reset()
transcript("PF", C - 5000, launches=[{"t": C - 3000, "end": T0 - 300}], kids=[(C - 2990, T0 - 300)],
           nested=[{"t": C - 2500, "end": None}])
o, u = run(P3, supp=[later("PF")])
check(("PF", "rA") not in u and o["excluded"].get("child_status_unverifiable") == {"shown": 1},
      "A: a launch made inside a subagent with no recorded end is unverifiable")
reset()
transcript("PF", C - 5000, launches=[{"t": T0 - 500, "end": None, "tool": "Workflow"}])
o, u = run(P3, supp=[later("PF")])
check(("PF", "rA") not in u and o["excluded"].get("child_status_unverifiable") == {"shown": 1},
      "A: a Workflow's immediate tool result is not its end")
reset()
transcript("PF", C - 5000, launches=[{"t": T0 - 500, "end": T0 - 300, "tool": "Workflow"}])
o, u = run(P3, supp=[later("PF")])
check(u.get(("PF", "rA")) == ("shown", 0), "A, control: a Workflow with a terminal notification before t0 is finished")

reset()
transcript("PF", C - 5000, launches=[{"t": T0 - 500, "end": T0 - 300, "tool": "Workflow", "by": "task"}])
o, u = run(P3, supp=[later("PF")])
check(u.get(("PF", "rA")) == ("shown", 0), "A: a notice naming only the task id ends the launch whose acknowledgment gave it")
reset()
transcript("PF", C - 5000, launches=[{"t": T0 - 500, "end": None, "tool": "Workflow", "stop": T0 - 200}])
o, u = run(P3, supp=[later("PF")])
check(u.get(("PF", "rA")) == ("shown", 0), "A: a TaskStop before t0 naming the task id ends the launch")
reset()
transcript("PF", C - 5000, launches=[{"t": T0 - 500, "end": None, "stop": T0 + 200}])
o, u = run(P3, supp=[later("PF")])
check(("PF", "rA") not in u and o["excluded"].get("child_active_in_window") == {"shown": 1},
      "A: an agent stopped inside the window was running in it")
reset()
transcript("PF", C - 5000, launches=[{"t": T0 - 500, "end": T0 - 300, "status": "failed", "by": "task"}])
o, u = run(P3, supp=[later("PF")])
check(u.get(("PF", "rA")) == ("shown", 0), "A, control: a failed agent has ended (a terminal status)")
reset()
transcript("PF", C - 5000, launches=[{"t": T0 - 500, "error": True}])
o, u = run(P3, supp=[later("PF")])
check(u.get(("PF", "rA")) == ("shown", 0), "A, control: a launch refused with an error never ran")

# ---- typed lifecycle evidence (round 9, the round-8 review): quoted markers are content --------------------------------
NOTE = ("<task-notification>\n<task-id>al0</task-id>\n<tool-use-id>toolu_L0</tool-use-id>\n<status>completed</status>\n"
        "</task-notification>")
STOPPED = "Successfully stopped task: al0 (the job)"


def say(t, text):                       # an ordinary assistant message
    return [(t, {"type": "assistant", "timestamp": iso(t), "message": {"content": [{"type": "text", "text": text}]}})]


def tool_out(t, uid, text):             # an unrelated tool call and its output
    return [(t - 1, {"type": "assistant", "timestamp": iso(t - 1), "message": {"content": [
                {"type": "tool_use", "id": uid, "name": "Bash", "input": {"command": "cat log"}}]}}),
            (t, {"type": "user", "timestamp": iso(t), "message": {"content": [
                {"type": "tool_result", "tool_use_id": uid, "content": text}]}})]


def stop_call(t, uid, target, text, err=False):     # a TaskStop invocation naming target, and its result
    r = {"type": "tool_result", "tool_use_id": uid, "content": text}
    if err:
        r["is_error"] = True
    return [(t - 1, {"type": "assistant", "timestamp": iso(t - 1), "message": {"content": [
                {"type": "tool_use", "id": uid, "name": "TaskStop", "input": {"task_id": target}}]}}),
            (t, {"type": "user", "timestamp": iso(t), "message": {"content": [r]}})]


def queue(t, content):
    return [(t, {"type": "queue-operation", "operation": "enqueue", "timestamp": iso(t), "content": content})]


def typed_case(extra):
    reset()
    transcript("PF", C - 5000, launches=[{"t": T0 - 500, "end": None}], kids=[(T0 - 490, T0 - 400)], extra=extra)
    return run(P3, supp=[later("PF")])


def still_unverifiable(o, u):
    return ("PF", "rA") not in u and o["excluded"].get("child_status_unverifiable") == {"shown": 1}


o, u = typed_case(say(T0 - 300, "The agent reported:\n" + NOTE))
check(still_unverifiable(o, u), "typed: a completed notification quoted in an assistant message is not an end")
o, u = typed_case(tool_out(T0 - 300, "toolu_B1", "log line\n" + NOTE))
check(still_unverifiable(o, u), "typed: a completed notification inside an unrelated tool's output is not an end")
o, u = typed_case(queue(T0 - 300, "earlier output: " + NOTE))
check(still_unverifiable(o, u), "typed: a queue record whose content is not itself a notification is not an end")
o, u = typed_case(queue(T0 - 300, NOTE))
check(u.get(("PF", "rA")) == ("shown", 0), "typed, control: the same notification as a queue record's own content is an end")
o, u = typed_case(say(T0 - 300, STOPPED))
check(still_unverifiable(o, u), "typed: stop text in an assistant message is not an end")
o, u = typed_case(tool_out(T0 - 300, "toolu_B2", STOPPED))
check(still_unverifiable(o, u), "typed: stop text in an unrelated tool's output is not an end")
o, u = typed_case(stop_call(T0 - 300, "toolu_S9", "aZZ", STOPPED))
check(still_unverifiable(o, u), "typed: a TaskStop aimed at another task, whose result names this one, is not an end")
o, u = typed_case(stop_call(T0 - 300, "toolu_S9", "al0", STOPPED, err=True))
check(still_unverifiable(o, u), "typed: a TaskStop result flagged as an error is not an end")
o, u = typed_case(stop_call(T0 - 300, "toolu_S9", "al0", STOPPED))
check(u.get(("PF", "rA")) == ("shown", 0), "typed, control: a bound TaskStop result for this very task is an end")
NOTE_A = NOTE.replace("al0", "aA1").replace("toolu_L0", "toolu_A1")     # a genuine notice for another task
o, u = typed_case(queue(T0 - 300, NOTE_A + "\nThe other agent wrote:\n" + NOTE))
check(still_unverifiable(o, u), "typed: a completed block after a record's leading notification (another task's) is not an end")
o, u = typed_case(queue(T0 - 300, NOTE + "\n<system-reminder>\nGoal check-in: keep going\n</system-reminder>"))
check(u.get(("PF", "rA")) == ("shown", 0), "typed, control: a leading notification with harness text after it is an end")

# ---- reading --------------------------------------------------------------------------------------------------------------
with open(ta.FIRES, "w", encoding="utf-8", newline="") as f:
    f.write('1700000000\tPreToolUse\tact\trX\t{"command": "a\rb"}\tPG\tregex\t%s\n' % call(1))
r = ta.rows(ta.FIRES)
check(len(r) == 1 and r[0][5] == "PG", "a CR inside the command field keeps the row whole (%d rows)" % len(r))

# ---- trial 2: input status and identity -------------------------------------------------------------------------------------
rng = random.Random(2)
if os.path.exists(ta.COHORT):
    os.remove(ta.COHORT)
o2 = ta.trial2(rng)
check(o2["input_status"] == "missing" and "mem" not in o2, "trial 2: a missing cohort is reported as missing, no numbers")
open(ta.COHORT, "w").write("{not json")
o2 = ta.trial2(rng)
check(o2["input_status"] == "unreadable" and "mem" not in o2, "trial 2: an unreadable cohort is never an empty one")
json.dump({"since": ta.T2_START + 1, "first": {}}, open(ta.COHORT, "w"))
o2 = ta.trial2(rng)
check(o2["input_status"] == "wrong_phase" and "mem" not in o2, "trial 2: a cohort from another trial start is wrong_phase")
json.dump({"since": ta.T2_START, "first": {}}, open(ta.COHORT, "w"))
o2 = ta.trial2(rng)
check(o2["input_status"] == "ok" and o2["mem"]["units"] == 0, "trial 2: a valid empty cohort is ok with zero units")
first = {"a\tmem\tk1": {"t": 1, "kind": "mem", "sid": "a", "ok": True, "eid": "e1", "identity": "eid"},
         "b\tmem\tk1": {"t": 1, "kind": "held_mem", "sid": "b", "ok": False, "eid": "e2"},
         "c\tmem\tk1": {"t": 1, "kind": "mem", "sid": "c", "ok": True, "eid": None},
         "d\tmem\tk1": {"t": 1, "kind": "held_mem", "sid": "d", "ok": True, "eid": "e9", "identity": "migrated_by_timestamp"},
         "e\tmem\tk1": {"t": 1, "kind": "mem", "sid": "e", "ok": True, "eid": None, "identity": "timestamp"}}
json.dump({"since": ta.T2_START, "first": first}, open(ta.COHORT, "w"))
json.dump({"x": {"trial_mem": {"cohort_status": "write_failed"}}}, open(ta.STATUS, "w"))
o2 = ta.trial2(rng)
m = o2["mem"]
check(m["identity_unverified"] == 3 and m["shown"] == 1 and m["held"] == 1,
      "trial 2: legacy, timestamp-matched and migrated records are counted apart, not in the contrast")
check(o2["learner_cohort_status"] == "write_failed" and m["certified"] is False,
      "trial 2: the learner's write failure is carried through and the result is not certified")
check(m["decision_prerequisites_met"] is False and o["decision_prerequisites_met"] is False,
      "no decision while the guardrails are uncomputed")
print(("PASS" if not fails else "FAIL") + " - trial analysis controls, %d failed" % fails)
sys.exit(1 if fails else 0)
