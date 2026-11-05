# -*- coding: utf-8 -*-
"""verify_sense.py - UserPromptSubmit: when the work will produce a proof, a code change or a number, hand the agent
the checks that could fail on it before it starts (30 Sep 2026).

The design goal: hooks prompt agents to check proofs, ideas and code with Lean, to audit their
work, and to plan how to verify it in more than one way. The card is short and concrete - the route for
this kind of work, the command that runs it, and the rung the claim is allowed to reach - because a reminder to be
careful measured as noise (the 30 Sep design review: "a reminder is not a verifier"). verify_gate.py is the other end:
at the Stop it blocks once when the report claims more than the turn checked.

One card per kind of work (math, code, result) per session, again after three hours. Silent on status questions and
errands. Runs for Claude (hook_dispatch) and Codex (hooks.json); the output is the JSON both accept. Fail-safe silent.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)
import hashlib
import io
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_DIR = os.environ.get("VERIFY_HOOKS_STATE_DIR") or HERE   # a test's state stays out of the live log
STATE = os.path.join(_DIR, ".verify_sense_state.json")
LOG = os.path.join(_DIR, "verify_sense_log.jsonl")
REARM_S = 3 * 3600
LEAN = "python " + os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "lean_check.py").replace(chr(92), "/")

WORK = re.compile(r"\b(?:prove|proving|proof|show\s+that|derive|verify|check|audit|fix|fixing|implement|build|write|make|"
                  r"create|add|solve|solving|compute|calculate|measure|test|run|improve|refactor|debug|formali[sz]e|port|"
                  r"update|finish|repair|rewrite|estimate|simulate|benchmark|re-?derive|lean\s+algorithm)\b", re.I)
# Kinds tuned on his last 3,000 prompts (30 Sep): the first cut matched 394 prompts as code work, mostly thumbnail tests,
# brain tests and film errands ("test", "hook", "api" alone), and 137 as results, mostly status questions ("how many
# simulations ..."). A card on a film errand is the noise he asked the brain to stop making (2 Sep: "fire less, and be
# more confident when it fires"), so each kind now needs a signal of its own kind of work.
FILM = re.compile(r"\b(?:movie|moive|film|flim|video|vidoe|episode|lecture|narrat\w*|render\w*|thumbnail|thumnial|"
                  r"youtube|poem|short|documentary|slide|blender|codex\s+image)\w*\b", re.I)
KINDS = [
    ("math", re.compile(r"\b(?:prove|proof|theorem|lemma|corollary|proposition|conjecture|inequality|lean\b|lean\s+algorithm|"
                        r"mathlib|formali[sz]\w*|counterexample|derivation|open\s+problem|q\.?e\.?d|asymptotic|martingale|"
                        r"eigenvalue|singular\s+value)\b|\\begin\{(?:theorem|lemma|proof)\}", re.I)),
    ("code", re.compile(r"\b(?:bug|buggy|traceback|stack\s*trace|refactor\w*|pytest|unit\s+tests?|off-by-one|segfault|"
                        r"(?:fix|debug|implement|rewrite|write|patch)\s+(?:the|a|this|that|my)?\s*"
                        r"(?:code|function|class|module|parser|unit\s+test|engine|regex))\b|"
                        r"\b(?:fix|debug|implement|patch|rewrite|refactor)\b.{0,60}\b[\w/\\.-]+\.(?:py|js|ts|lean|rs|go|java|cpp)\b",
                        re.I)),
    ("result", re.compile(r"\b(?:compute|calculate|recompute|estimate)\b.{0,80}\b(?:median|mean|variance|probability|p-?value|"
                          r"rate|ratio|return|drawdown|sharpe|number|value|bound|constant|expectation|error)\b|"
                          r"\bwhat\s+is\s+the\s+(?:median|mean|p-?value|probability|expected\s+value|variance)\b", re.I)),
]
# A harness or hook turn is not a request of his: Codex delivers a Stop hook's continuation and a goal's resume as user
# turns (<hook_prompt>, <goal_context>, <codex_internal_context>), and a card there would answer the machine - verify_gate's
# own block among them (report D02-T04). A prompt he starts with a paste (<pasted_content ...>) is still his.
NOT_HIS = re.compile(r"^\s*(?:<(?:hook_prompt|goal_context|codex_internal_context|system-reminder|task-notification|"
                     r"cross-session-message|agent-message|agent-notification|subagent_notification|turn_aborted|"
                     r"local-command|environment_context|user_instructions|recommended_plugins)\b|Stop hook feedback|"
                     r"\[verify(?:-gate|-op)?\b)", re.I)
# a question about state or an errand is not work that produces a claim
ERRAND = re.compile(r"^\s*(?:how\s+(?:is|are|long|close)|is\s+(?:the|it|there)|are\s+(?:the|we|there)|did\s+(?:you|we|it)|"
                    r"what\s+(?:is|are|did)|where\s+|open\b|show\s+me|give\s+me\s+(?:a|the)\s+(?:report|list|link)|tell\s+me|"
                    r"can\s+you\s+(?:open|show|tell))", re.I)

CARDS = {
    "math": ("[verify] Before you call a mathematical claim proved, name the checks that could fail on it and run them: "
             "compute small and boundary cases; look for a counterexample to every hypothesis you do not use; check each "
             "side condition where it is used (a denominator you cancel is nonzero on the whole range, a limit moved "
             "past a sum or integral has a dominating or monotone bound, a witness chosen after 'for all n' may depend "
             "on n). Formalize the key step in Lean: " + LEAN + " --stmt \"example ... := by ...\" (Mathlib; add --core "
             "for Nat/Int/List statements). A pass certifies the Lean statement only, so write it back in words and "
             "compare it with yours. Report the rung it reached: conjecture, numerical evidence, sketch, proof, or "
             "Lean-checked."),
    "code": ("[verify] Before you say fixed or working: show a check that fails on the old code and passes on the new "
             "(run it against both, or keep the failing output); rerun after your last edit, since a pass before an edit "
             "says nothing about it; add one route that does not share the implementation's assumptions - a "
             "hand-computed case, a boundary input, or a reference implementation. For a pure function with a crisp "
             "spec, a Lean #eval or decide check on small inputs is a cheap extra route (" + LEAN + " --core)."),
    "result": ("[verify] Before you report a number, get it two independent ways (another code path, method or data "
               "source), check its magnitude, sign and units against something you already know, and if the two "
               "disagree, report the disagreement rather than the nicer number."),
}


def classify(prompt):
    """The kinds of claim-producing work this prompt asks for, in card order."""
    text = (prompt or "")[:6000]
    if not text.strip() or NOT_HIS.match(text) or not WORK.search(text):
        return []
    if ERRAND.search(text) and len(text) < 160 and not re.search(r"\b(?:and|then)\s+(?:fix|prove|implement|build|write)\b", text, re.I):
        return []
    kinds = [k for k, rx in KINDS if rx.search(text)]
    # a film about mathematics is film work; its proofs are narrated, not proved (sampled misfires: "a movie that shows
    # the proof", "the person says every proof is done on the board twice")
    if FILM.search(text) and not re.search(r"\b(?:lean|paper|conjecture|open\s+problem)\b", text, re.I):
        kinds = [k for k in kinds if k != "math"]
    return kinds


def _state():
    try:
        with io.open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save(st):
    now = time.time()
    st = {k: v for k, v in st.items() if isinstance(v, dict) and any(now - t < 86400 for t in v.values())}
    try:
        tmp = STATE + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        os.replace(tmp, STATE)
    except Exception:
        pass


def _seen(sess):
    """One row per prompt read, beside the log: the denominator of the fire rate, and the proof the hook runs."""
    try:
        with io.open(os.path.join(os.path.dirname(LOG), "verify_hooks_seen.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": int(time.time()), "hook": "sense", "s": sess[:12]}) + "\n")
    except Exception:
        pass


def decide(data):
    """The card text for this prompt, or '' - at most two kinds, each once per session per REARM_S."""
    prompt = str(data.get("prompt") or data.get("text") or "")
    if prompt.strip():
        _seen(str(data.get("session_id") or data.get("thread_id") or ""))
    try:
        # the archive's own test for a machine-written turn (agent-authored text, the Codex app's background task); on
        # 30 Sep 03:01 that app's suggestion prompt drew a code card here
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import prompt_origin
        if prompt_origin.machine_turn(prompt, str(data.get("cwd") or "")):
            return ""
    except Exception:
        pass
    kinds = classify(prompt)
    if not kinds:
        return ""
    sess = str(data.get("session_id") or data.get("thread_id") or "")
    st = _state()
    seen = st.get(sess) or {}
    now = time.time()
    fresh = [k for k in kinds if now - seen.get(k, 0) >= REARM_S][:2]
    if not fresh:
        return ""
    for k in fresh:
        seen[k] = now
    if sess:
        st[sess] = seen
        _save(st)
    try:
        with io.open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": now, "session": sess, "kinds": fresh, "matched": kinds,
                                 "prompt_sha": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]}) + "\n")
    except Exception:
        pass
    return "\n".join(CARDS[k] for k in fresh)


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    try:
        block = decide(data)
    except Exception:
        return
    if block:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": block}}))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        import test_verify_hooks
        raise SystemExit(test_verify_hooks.main(["sense"]))
    main()
