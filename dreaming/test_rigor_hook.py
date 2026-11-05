"""test_rigor_hook.py - push every agent that builds or runs a brain test toward harder, longer, fairer tests.

The owner, 3 Nov 2026 00:3x on the Mac: "some of the more recent test where way to simple which is why everyone did
more or less the same well and got great scores that is not how life is. please make tests much harder, recall harder
agentic testing harder everything should be harder ... make 50 hooks for testing which prompt agents with this and my
other testing prompts, to make tests harder and more diverse and so on, and to read the task manager about this."
Earlier: 2 Nov 23:34 (proof audits, agency, public benchmarks, quantify everything) and his 2 Nov 15:06 EST email
("bigger stronger, more agentic tests that will showcase the strength of the brain").

Fifty cards, each with its own trigger. When a prompt or a tool call looks like test work, the hook shows the matching
card this session has seen least recently, plus his orders the first time. UserPromptSubmit: at most one card every
3 minutes per session. PostToolUse: at most one every 10 minutes. Silent in subagents and when nothing matches.
Fires are logged to state/test_rigor/fires.jsonl so the cards can be judged later. Off switch: test_rigor_runtime/OFF.
Fail-open: any error prints nothing.

  python3 test_rigor_hook.py --list        print the 50 cards
  python3 test_rigor_hook.py --selftest    check triggers and rate limits on sample payloads
claude-48dd2a83, 3 Nov 2026.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import json, os, re, sys, time, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state", "test_rigor")
OFF = os.path.join(HERE, "test_rigor_runtime", "OFF")
GAP = {"UserPromptSubmit": 180, "PostToolUse": 600}
PY = sys.executable or "python3"

ORDERS = (
    "FULL BRAIN FIRST (3 Nov 00:30-00:36, OWNER_ORDERS_FULL_BRAIN_AND_TESTS.md on yspennstate/brain-tasks-board): the brain arm runs on the full graph, 349,065 nodes and 15,473,498 edges (release brain-graph-full-2026-11-01); a smaller graph is a partial-brain result and is labeled so; hard problems only. "
    "His testing orders, newest first. 3 Nov 00:3x: \"make tests much harder, recall harder, agentic testing harder, "
    "everything should be harder ... the current ones are too simple and disadvantage the brain ... test longer "
    "workflows, auditing, much much harder recall ... use Opus 5.5 and gpt-6.1-sol, no Sonnet ... check the task "
    "manager much more often, respond to my messages faster.\" 2 Nov 15:06 EST: \"bigger stronger, more agentic tests "
    "that will showcase the strength of the brain.\" 2 Nov 23:34: recall, proof audits with planted errors and clean "
    "controls, your own brain-vs-no-brain tests, public benchmarks, agency (loops, stalls, stopping early), all "
    "quantified. Task manager: %s ~/.claude/dreaming/task_checklist_hook.py list --all and "
    "%s ~/.claude/dreaming/fleet_board.py check --agent <you>." % (PY, PY))

GATE = re.compile(r"\b(tests?|testing|benchmarks?|a/b|brain[- ]vs|no[- ]brain|arms?|bank|pilot|grad(e|er|ing)|gold|"
                  r"mcnemar|recall_ab\w*|items\.jsonl|key\.jsonl|brief|auditors?|planted|controls?|scor(e|er|ing))\b",
                  re.I)

# (id, trigger, text). A trigger is a regex over the prompt or the tool call; "" means the card is a general one.
CARDS = [
    # Hardness
    (1, r"score|accuracy|%|percent|result", "If both arms score above 85%, or within 3 points of each other, the test "
        "cannot tell them apart. Raise the difficulty until the no-brain arm lands between 30% and 60%."),
    (2, r"pilot|calibrat", "Calibrate before the big run: put 10-20 items to the no-brain arm first and drop or harden "
        "every item it gets right in under a minute."),
    (3, r"question|item|bank", "An item one grep can answer is not a brain test. Each item should need two or three "
        "sources joined: a memory, a result file, and the later correction that changed it."),
    (4, r"distract|supersed|stale|newer|latest", "Plant near-misses: superseded values, retracted results, a memory "
        "that looks right and was corrected a week later. The right answer is the current one."),
    (5, r"budget|command|limit|cap", "Set the command budget low enough that brute search cannot cover the ground. A "
        "brain should win by knowing where to look, not by having time to look everywhere."),
    (6, r"workflow|task|long|hour", "Make tasks long: 30 to 120 minutes of agent work with several stages. Two-minute "
        "tasks measure typing speed, not memory."),
    (7, r"kind|categor|aspect|type", "Use at least six kinds of item and report each kind on its own. One pooled score "
        "hides where the brain helps and where it costs."),
    (8, r"adversar|hard|fail|miss", "Have a second agent write new items after reading the no-brain arm's answers, aimed "
        "at what it got wrong. Keep the items that still separate the arms."),
    (9, r"leak|vocabular|wording|paraphras", "Write items without the answer's vocabulary: no slug words, no file "
        "names, no phrases from the memory. Round 5's memory-note items are the model."),
    (10, r"not.?found|absent|never|fabricat", "Include items whose honest answer is 'we never did that' or 'no such "
        "result'. Score a confident made-up answer as wrong, and report fabrication rate per arm."),
    (11, r"multi|part|all.or.nothing|join", "Ask multi-part questions (value, date, file, who) scored all or nothing. "
        "Partial credit flatters the arm that guesses."),
    (12, r"power|sample|n\s*=|items per|how many", "Plan the size before launch: about 388 tasks per arm are needed to "
        "see a 10-point gap at 80% power. A 40-item pilot is a pilot; say so."),
    # What the brain is for
    (13, r"dead.?route|tried|attempt|proof|route", "The brain should know what we tried and how it failed. Test it: give "
        "a problem with a recorded dead route and score whether the agent avoids it and starts from the live one."),
    (14, r"scar|mistake|trap|lesson", "Test whether the brain stops repeat mistakes: real tasks where one of our recorded "
        "scars is live in the environment, scored by a hidden checker on the finished work."),
    (15, r"the other laptop|laptop|the research box|other computer|cross", "Ask about results that were made on another computer. The "
        "brain is supposed to make the other laptop's and the laptop's work visible here; git on the Mac cannot."),
    (16, r"owner|rule|order|policy|compl", "Test compliance with his standing orders on tasks that tempt a violation "
        "(a paper that wants the old grant line, a push to a CI repo, a Sonnet launch). The brain knows the rules."),
    (17, r"change|correct|retract|update", "Ask for a value that changed two or three times and name the date you want "
        "it at. The brain holds the history; a single file holds only the last version."),
    (18, r"resum|continu|interrupt|next line|status", "Test continuity: stop a task halfway, wait, then give it to a "
        "fresh session. Score what it finishes, and how much of the first session's work it redoes."),
    (19, r"link|associat|neighbo|graph|hop", "Ask questions that need two hops through linked memories: A's lesson "
        "explains B's failure. Report one-hop and two-hop accuracy separately."),
    (20, r"math|theorem|lemma|store", "Math store tests: ask for the route that worked on a known theorem, the link that "
        "was retracted and why, the technique another problem borrowed. Bodies only is the control."),
    (21, r"number|unit|provenance|cite|source", "Require every number with its unit and the file it came from. Score "
        "provenance as well as the value; a right number with no source is half wrong."),
    (22, r"decid|decision|judg|ambigu|choose", "Put judgment calls in front of the agent where he has already decided "
        "(which repo, keep or delete, push or hold). The brain arm should match his recorded choice."),
    # Longer workflows and agency
    (23, r"session|multi.?step|step 2|later", "Build multi-session tasks: step 2 runs in a fresh session hours later and "
        "can only succeed by recovering what step 1 found. Score the finished step 2."),
    (24, r"flak|fail|error|refus|retry", "Inject failures mid-task: a refused push, a full disk, a command that fails "
        "once. Score recovery, and count loops (the same command repeated without progress)."),
    (25, r"build|fix|checker|check\.py|hidden", "Use build-test-fix tasks with a hidden checker the agent never sees. "
        "Score the artifact, never the agent's own claim that it is done."),
    (26, r"kill|crash|restart", "Kill the agent at a random point and resume in a new session. Count work lost and work "
        "repeated; a brain arm should lose less."),
    (27, r"claim|mesh|coordinat|share|conflict", "Test coordination: two agents, one shared file, the claim protocol. "
        "Score collisions, lost edits and waiting time with and without the brain."),
    (28, r"message|spec|inbox|board|mid.?task", "Change the spec mid-task with a new message at minute 10. Score against "
        "the latest spec, and time from the message to the agent acting on it."),
    (29, r"done|finish|complete|early|stop", "Hide a remaining part: a task that looks finished at 80%. Score stopping "
        "early as a failure; the checker knows the full list."),
    (30, r"loop|stuck|stall|repeat", "Measure loops and stalls on every agentic run: repeated identical commands, minutes "
        "without a file change, and stopping before the checker passes."),
    (31, r"subagent|helper|delegat", "No subagents in the tested agents unless his words in that session name them. A "
        "test whose arm delegated measures the helper, not the brain."),
    (32, r"toy|real|snapshot|repo|environment", "Use real environments: read-only snapshots of his repos and our tool "
        "folders, not toy sandboxes. The brain's knowledge is about the real ones."),
    # Auditing
    (33, r"proof|audit|lemma|inequal|quantifier", "Proof audits need subtle plants: an index off by one, a swapped "
        "quantifier, a dropped measurability or integrability condition, a limit exchange with no domination. Keep half "
        "the copies clean."),
    (34, r"report|table|result|number", "Audit results: restate a real report with one wrong number deep in a table. "
        "The auditor must find it and give the right value from the source file."),
    (35, r"code|script|hook|bug|review", "Code audits: plant one real bug in a copy of one of our scripts (a hook, a "
        "launcher) and ask for a review. Score finding it, locating it, and the fix."),
    (36, r"cit|arxiv|author|bib|reference", "Citation audits: plant a wrong arXiv id, a wrong author order (the DAPS "
        "case), a year off by one. The brain has caught these before; test whether it still does."),
    (37, r"false.?alarm|clean|control|wolf", "Score false alarms as hard as misses. An auditor that flags everything "
        "should score worse than one that flags nothing."),
    (38, r"statement|paper|document|cv|7.page", "Audit long documents: plant three inconsistencies across sections of a "
        "7-page statement (a number, a date, a claim stated two ways). Report found, located, fixed."),
    # Harder recall
    (39, r"recall|memory|remember", "Ask by paraphrase with no shared words, and in his style: typos, half sentences, "
        "pronouns for things named days ago."),
    (40, r"when|date|timeline|history", "Ask what a value was on a given date, not what it is now. Score the date "
        "window as well as the value."),
    (41, r"why|reason|conclu|learn", "Ask for reasons, not facts: what we concluded after the failure that followed X, "
        "and what we did next. One recall cannot answer it."),
    (42, r"typo|spell|misspel", "Use his real typos ('brian', 'agetn', 'comptuer'). Respelling them changed 4 of 7 "
        "misses into hits before; test whether both arms cope."),
    (43, r"three|3.hop|chain|path", "Three-hop recall: the answer sits in a memory that only a second memory names, and "
        "that one only a third points to."),
    (44, r"load|latency|slow|second|time", "Time counts. Report seconds per item per arm, under the machine's real load. "
        "A brain that answers right in 20 minutes loses to git in 2."),
    # Method
    (45, r"model|sonnet|opus|gpt|codex|claude", "Same model in both arms, from his list only: Claude on claude-opus-5-5 at "
        "high, Codex on gpt-6.1-sol at high or lower. No Sonnet, no Fable, nothing above high."),
    (46, r"grad|judge|blind|regrad", "Grade both arms in one pass with the same grader, blind to the arm. A regrade "
        "moves verdicts; never compare arms graded in different passes."),
    (47, r"answer|give.?up|not_found|both", "Report accuracy on items both arms answered separately from give-ups. "
        "Rounds 2 and 3 'won' on persistence while both-answered accuracy was equal."),
    (48, r"push|github|save|publish|result", "Save as you go and push the test, the key, the scores and what was "
        "learned: ai-memories for brain tests, math-research-brain for math tests."),
    (49, r"hypothes|pre.?regist|plan|design", "Write the hypothesis, the primary score and the analysis before the run, "
        "and keep them. Choosing the metric after seeing the scores is not a test."),
    (52, r"graph|sidecar|link walk|graph_lane|brain arm", "Is the link walk on? The graph lane mixes two walks: the use-grown store and the memories' own links in ~/.claude/brain_graph/live/sidecar.sqlite. A missing sidecar quietly leaves the link walk empty (the Mac had none until 3 Nov). Run live_sidecar.py stats, write the link count next to the graph counts in the README, and set BRAIN_RECALL_LOG=0 and BRAIN_RECALL_REFRESH=0 in any recall loop."),
    (51, r"brain arm|graph|recall|node|edge|full brain", "Which graph is your brain arm on? Full = 349,065 nodes and 15,473,498 edges (release brain-graph-full-2026-11-01; on the Mac ~/.claude/dreaming/graph_lane_store). Anything smaller, net.sqlite3 included, is a partial-brain result and must be labeled that way in every result."),
    (50, r"", "Read the task manager now and often: his newest messages on the fleet board and the open asks on the "
        "checklist. Answer his board messages within the tick, not the hour."),
]
CARDS = [(i, re.compile(t, re.I) if t else None, x) for i, t, x in CARDS]


def _text(p):
    if p.get("prompt"):
        return str(p["prompt"])[:6000]
    ti = p.get("tool_input") or {}
    try:
        return json.dumps(ti)[:6000]
    except Exception:
        return str(ti)[:6000]


def pick(text, shown, sid=""):
    """The matching card this session saw least recently. Never-shown ties go in a per-session order (crc32 of
    session and card): breaking them by lowest id starved the late cards, 41-52 drew 18 of 238 fires to 3 Nov 03:22
    and 52 none."""
    hits = [c for c in CARDS if c[1] is not None and c[1].search(text)]
    if not hits:
        hits = [c for c in CARDS if c[1] is None]
    return min(hits, key=lambda c: (shown.get(str(c[0]), 0), zlib.crc32(("%s:%d" % (sid, c[0])).encode())))


def main(raw=None, now=None):
    if os.path.exists(OFF):
        return ""
    raw = sys.stdin.buffer.read().decode("utf-8", "replace") if raw is None else raw
    i = raw.find("{")
    p = json.loads(raw[i:]) if i >= 0 else {}
    if p.get("agent_id") or p.get("agent_type"):
        return ""
    sid = str(p.get("session_id") or "")[:36]
    ev = str(p.get("hook_event_name") or "")
    if not sid or ev not in GAP:
        return ""
    text = _text(p)
    if not GATE.search(text):
        return ""
    now = time.time() if now is None else now
    os.makedirs(STATE, exist_ok=True)
    sp = os.path.join(STATE, re.sub(r"[^A-Za-z0-9_-]", "", sid) + ".json")
    try:
        st = json.load(open(sp, encoding="utf-8"))
    except Exception:
        st = {"shown": {}, "last": {}, "orders": 0}
    if now - st["last"].get(ev, 0) < GAP[ev]:
        return ""
    cid, _, card = pick(text, st["shown"], sid)
    st["shown"][str(cid)] = now
    st["last"][ev] = now
    out = "[test-rigor %d/52] %s" % (cid, card)
    if not st.get("orders"):
        out += "\n" + ORDERS
        st["orders"] = now
    else:
        out += " (His orders on tests and the task manager: %s ~/.claude/dreaming/test_rigor_hook.py --list)" % PY
    tmp = sp + ".%d" % os.getpid()
    json.dump(st, open(tmp, "w", encoding="utf-8"))
    os.replace(tmp, sp)
    with open(os.path.join(STATE, "fires.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": round(now), "session": sid[:8], "event": ev, "card": cid}) + "\n")
    return out


def selftest():
    import tempfile
    global STATE
    STATE = tempfile.mkdtemp(prefix="test_rigor_selftest_")
    assert len(CARDS) == 52 and sorted(c[0] for c in CARDS) == list(range(1, 53))
    pay = lambda ev, **k: json.dumps(dict(session_id="selftest-0001", hook_event_name=ev, **k))
    t0 = 1_000_000.0
    q = "design the proof audit bank with planted errors"
    want = [c[0] for c in CARDS if c[1] is not None and c[1].search(q)]
    a = main(pay("UserPromptSubmit", prompt=q), t0)
    ca = int(a.split()[1].split("/")[0])
    assert ca in want and "His testing orders" in a, a[:80]                                           # a matching card
    assert main(pay("UserPromptSubmit", prompt=q), t0 + 60) == ""                                    # 3-minute gap
    b = main(pay("UserPromptSubmit", prompt=q), t0 + 200)
    cb = int(b.split()[1].split("/")[0])
    assert cb in want and cb != ca and "His testing orders" not in b, b[:80]                          # a new card
    firsts = {pick(q, {}, "s%d" % k)[0] for k in range(200)}
    assert len(firsts) == len(want), (sorted(firsts), want)                     # every matching card leads somewhere
    assert main(pay("UserPromptSubmit", prompt="make dinner"), t0 + 999) == ""                       # no test words
    c = main(pay("PostToolUse", tool_name="Bash", tool_input={"command": "python3 grade.py --arm brain"}), t0 + 1000)
    assert c.startswith("[test-rigor "), c[:80]
    assert main(pay("PostToolUse", tool_name="Bash", tool_input={"command": "grade.py"}), t0 + 1300) == ""  # 10 min
    assert main(json.dumps(dict(session_id="x", hook_event_name="UserPromptSubmit", agent_id="sub",
                                prompt="test bank")), t0) == ""                                       # subagent
    assert main(pay("Stop"), t0 + 5000) == ""
    n = sum(1 for _ in open(os.path.join(STATE, "fires.jsonl")))
    assert n == 3, n
    print("selftest ok: 52 cards, triggers, 3-min and 10-min gaps, subagent silence, %d fires logged" % n)


if __name__ == "__main__":
    if "--list" in sys.argv:
        print(ORDERS + "\n")
        for i, t, x in CARDS:
            print("%2d. %s" % (i, x))
    elif "--selftest" in sys.argv:
        selftest()
    else:
        try:
            s = main()
            if s:
                print(s)
        except Exception:
            pass
