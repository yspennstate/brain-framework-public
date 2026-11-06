#!/usr/bin/env python3
"""canonical_route.py - name the canonical brain-improvement contract when a request asks for it.

WHY (2026-09-05). The contract's sixth acceptance gate reads "the wake or task-routing path names
this prompt when a relevant request arrives" and had sat UNCHECKED since 2026-08-18. Deliberate
recall (`brain.py recall`) already routes the contract through an intent gate in recall_policy.json,
but no prompt-time hook ran that gate, so a session opened with "keep improving the brain under the
canonical contract" got twelve hooks' worth of context and not one line naming the contract. This
hook closes that gap with the SAME gate the recall path uses (brain._intent_gate_strength on the
v2 node's intent_gate), so the two cannot drift apart.

WHAT IT DOES. On UserPromptSubmit it runs the intent gate on the prompt. The gate requires a
modification action AND an AI persistent-system referent in one clause (or the exact canonical
name), with negation and no-change vetoes - the same rule that keeps "build a sales dashboard" and
"brain surgery recovery time" silent in recall. On a hit it injects ONE line naming the contract
file, its brain node and the subsystem's live state, once per session (24 h dedupe), and logs the
fire to canonical_route_fires.tsv for the precision judge. Fail-safe silent on any error; honours
the brain switch (/dont).

    python canonical_route.py --selftest    the recall regression's positives/negatives through the
                                            gate AND through the hook protocol (stdin JSON -> stdout)
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import time

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


DR = os.path.dirname(os.path.abspath(__file__))
if DR not in sys.path:
    sys.path.insert(0, DR)

NODE = "canonical_brain_improvement_prompt_v2_2026_08_15"
CONTRACT = ("~/brain-repo/12_cognitive_architecture/"
            "brain_improvement/CANONICAL_PROMPT.md")
STATE = os.environ.get("CANONICAL_ROUTE_STATE", os.path.join(DR, "canonical_route_state.json"))
FIRES = os.environ.get("CANONICAL_ROUTE_FIRES", os.path.join(DR, "canonical_route_fires.tsv"))
DEDUP_SECS = 24 * 3600
NOISE_PREFIXES = ("Diagnostic ping", "Your tool call was malformed", "[Request interrupted", "API Error")


def _read_stdin_json():
    try:
        buf = getattr(sys.stdin, "buffer", None)
        raw = buf.read() if buf is not None else sys.stdin.read().encode("utf-8", "surrogateescape")
        text = raw.decode("utf-8", "replace").lstrip("\ufeff")
        return json.loads(text) if text.strip() else {}
    except Exception:
        return None


# THE GATE'S VERDICT, KEPT PER PROMPT (6 Nov 2026). Importing brain.py for the gate cost about 20 ms a prompt (13 of
# them its own copy of spread_recall), and the gate is silent on nearly every prompt. Its answer depends on three
# things only: the prompt, recall_policy.json and brain.py's code. Loop sessions send the same text again and again
# (2,120 prompts from 4 to 6 Nov held 42 distinct texts), so the answer is kept under a hash of the prompt, for as long
# as both files keep the size, mtime and inode they had when it was computed. Any change to either empties the cache.
# Only real (uuid) sessions write it. CANONICAL_ROUTE_GATE_CACHE=0 turns it off.
GATE_CACHE = os.environ.get("CANONICAL_ROUTE_GATE_CACHE", os.path.join(DR, "canonical_route_gate_cache.json"))
GATE_CACHE_MAX = 400


def _gate_ident():
    """[node, brain.py, its stat, policy, its stat] as the gate would read them, or None."""
    try:
        import importlib.util

        spec = importlib.util.find_spec("brain")
        bp = os.path.abspath(spec.origin) if spec and spec.origin else ""
        if not bp:
            return None
        pol = os.environ.get("BRAIN_RECALL_POLICY", os.path.join(os.path.dirname(bp), "recall_policy.json"))
        ident = [NODE, bp]
        for p in (bp, pol):
            st = os.stat(p)
            ident += [p, st.st_mtime_ns, st.st_size, st.st_ino] if p == pol else [st.st_mtime_ns, st.st_size, st.st_ino]
        return ident
    except Exception:
        return None


def _gate_cached(key, ident):
    try:
        with io.open(GATE_CACHE, encoding="utf-8") as handle:
            cache = json.load(handle)
        if isinstance(cache, dict) and cache.get("ident") == ident:
            v = (cache.get("v") or {}).get(key)
            return None if v is None else float(v)
    except Exception:
        pass
    return None


def _gate_keep(key, ident, strength):
    try:
        try:
            with io.open(GATE_CACHE, encoding="utf-8") as handle:
                cache = json.load(handle)
        except Exception:
            cache = {}
        v = cache.get("v") if isinstance(cache, dict) and cache.get("ident") == ident else None
        v = dict(v) if isinstance(v, dict) else {}
        v.pop(key, None)
        v[key] = strength
        while len(v) > GATE_CACHE_MAX:
            v.pop(next(iter(v)))
        tmp = GATE_CACHE + ".%d.tmp" % os.getpid()
        with io.open(tmp, "w", encoding="utf-8") as handle:
            json.dump({"ident": ident, "v": v}, handle)
        os.replace(tmp, GATE_CACHE)
    except Exception:
        pass


def gate_strength(prompt: str, session: str = "") -> float:
    """The recall path's own intent gate on this prompt; 0.0 means silent."""
    use = GATE_CACHE not in ("", "0")
    key = hashlib.sha256(prompt.encode("utf-8", "replace")).hexdigest()
    ident = _gate_ident() if use else None
    if ident is not None:
        hit = _gate_cached(key, ident)
        if hit is not None:
            return hit
    import brain

    policy = brain._load_recall_policy()
    gate = (policy.get(NODE) or {}).get("intent_gate")
    strength = float(brain._intent_gate_strength(prompt, gate) or 0.0) if gate else 0.0
    # kept only if neither file moved while it was computed, and only for a real session
    if ident is not None and re.match(r"^[0-9a-f]{8}-", session or "") and _gate_ident() == ident:
        _gate_keep(key, ident, strength)
    return strength


def _fresh(session: str) -> bool:
    """True once per session per 24 h; test fixtures (non-uuid sessions) never persist."""
    if not session:
        return True
    now = time.time()
    try:
        state = json.load(io.open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
    except Exception:
        state = {}
    state = {key: value for key, value in state.items() if now - float(value) < DEDUP_SECS}
    if now - float(state.get(session, 0)) < DEDUP_SECS:
        return False
    state[session] = now
    if re.match(r"^[0-9a-f]{8}-", session):
        try:
            tmp = STATE + ".%d.tmp" % os.getpid()
            with io.open(tmp, "w", encoding="utf-8") as handle:
                json.dump(state, handle)
            os.replace(tmp, STATE)
        except Exception:
            pass
    return True


def _log_fire(session: str, strength: float, prompt: str) -> None:
    try:
        digest = hashlib.sha256(prompt.encode("utf-8", "replace")).hexdigest()[:12]
        head = " ".join(prompt.split())[:100]
        with io.open(FIRES, "a", encoding="utf-8", newline="\n") as handle:
            handle.write("%.0f\t%s\t%.2f\t%s\t%s\n" % (time.time(), session[:12], strength, digest, head))
    except Exception:
        pass


def message(strength: float) -> str:
    return (
        "[router] This request asks to change the persistent AI brain, its math layer, or the "
        "brain-improvement program itself. The governing contract is the canonical brain-improvement "
        "prompt v0.2: " + CONTRACT + " (brain node " + NODE + "). Read it in full, then STATUS.json "
        "and DASHBOARD.md beside it and the last rows of IMPROVEMENT_LEDGER.jsonl; resume the open "
        "gates, do not recreate the subsystem. (hint: intent-gate strength %.1f - a modification "
        "action plus an AI-system referent in one clause; the same gate deliberate recall uses; "
        "said once per session)" % strength
    )


def route(data: dict) -> str | None:
    """Return the injection text for this hook event, or None to stay silent."""
    if not isinstance(data, dict) or data.get("hook_event_name") != "UserPromptSubmit":
        return None
    prompt = data.get("prompt", "") or ""
    if not prompt.strip() or prompt.lstrip().startswith(NOISE_PREFIXES):
        return None
    session = str(data.get("session_id") or "")
    strength = gate_strength(prompt, session)
    if strength <= 0.0:
        return None
    if not _fresh(session):
        return None
    _log_fire(session, strength, prompt)
    return message(strength)


def main() -> int:
    try:
        import brain_switch

        brain_switch.gate()
    except SystemExit:
        raise
    except Exception:
        pass
    data = _read_stdin_json()
    if data is None:
        return 0
    try:
        text = route(data)
    except Exception:
        return 0
    if text:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit", "additionalContext": text}}))
    return 0


def _selftest() -> int:
    import subprocess
    import tempfile

    import recall_policy_regression as reg

    failures = []

    def check(condition, label):
        print(("  ok   " if condition else "  FAIL ") + label)
        if not condition:
            failures.append(label)

    # 1. The gate itself, on the recall regression's fixtures (never indexed; they live in that module).
    pos = {q: gate_strength(q) for q in reg.POSITIVES}
    neg = {q: gate_strength(q) for q in reg.NEGATIVES}
    col = {q: gate_strength(q) for q in reg.HEALTH_COLLISION}
    for q, s in pos.items():
        print("     positive %.1f  %s" % (s, q))
    hits = sum(1 for s in pos.values() if s > 0)
    check(hits >= 5, "gate: at least 5 of the %d recall positives route (%d did)" % (len(pos), hits))
    loud = [q for q, s in neg.items() if s > 0]
    check(not loud, "gate: all %d recall negatives stay silent (loud: %s)" % (len(neg), loud))
    print("     known health collision (reported, never a failure):",
          {q: round(s, 1) for q, s in col.items()})
    own = ("Read ~/Downloads/KEEP_IMPROVING_THE_BRAIN.md in full, then follow it: wake into "
           "the brain, join the mesh, read STATUS.json and DASHBOARD.md of the brain_improvement "
           "subsystem, and keep improving the brain under the canonical contract.")
    check(gate_strength(own) > 0, "gate: the 2026-09-05 launch prompt that opened this session routes")
    quiet = [
        "keep working on the Bastogne documentary and open a chrome window",
        "how much cpu are we using right now",
        "queue 10k simulations on box 3 and make sure box 5 is running the vol-pair study",
        "what did the brain say about the rebalancing clock yesterday",
        "explain how the brain's recall works, no changes",
    ]
    loud2 = [q for q in quiet if gate_strength(q) > 0]
    check(not loud2, "gate: five owner-shaped non-brain-change prompts stay silent (loud: %s)" % loud2)

    # 2. The hook protocol end to end: stdin JSON -> stdout JSON, dedupe, silence, with a temp state.
    with tempfile.TemporaryDirectory(prefix="canonical_route_selftest_") as tmp:
        env = dict(os.environ)
        env["CANONICAL_ROUTE_STATE"] = os.path.join(tmp, "state.json")
        env["CANONICAL_ROUTE_FIRES"] = os.path.join(tmp, "fires.tsv")
        env["PYTHONIOENCODING"] = "utf-8"

        def run(payload):
            proc = subprocess.run([sys.executable, "-s", os.path.abspath(__file__)],
                                  input=json.dumps(payload).encode("utf-8"),
                                  capture_output=True, env=env, timeout=60)
            out = proc.stdout.decode("utf-8", "replace").strip()
            return proc.returncode, (json.loads(out) if out else None)

        session = "0123abcd-selftest-0000-0000-000000000000"
        code, out = run({"hook_event_name": "UserPromptSubmit", "session_id": session,
                         "prompt": "implement more canonical brain improvements"})
        ctx = ((out or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")
        check(code == 0 and "[router]" in ctx and NODE in ctx and "CANONICAL_PROMPT.md" in ctx,
              "hook: a positive prompt injects one line naming the contract file and node")
        code, out = run({"hook_event_name": "UserPromptSubmit", "session_id": session,
                         "prompt": "implement more canonical brain improvements"})
        check(code == 0 and out is None, "hook: the same session is silent the second time (24 h dedupe)")
        code, out = run({"hook_event_name": "UserPromptSubmit", "session_id": "abcd0123-other-0000-0000-000000000000",
                         "prompt": "Build a sales dashboard for the restaurant"})
        check(code == 0 and out is None, "hook: a negative prompt is silent")
        code, out = run({"hook_event_name": "PreToolUse", "session_id": "abcd0123-tool-0000-0000-000000000000",
                         "tool_input": {"command": "implement more canonical brain improvements"}})
        check(code == 0 and out is None, "hook: a tool event is never routed (prompt surface only)")
        proc = subprocess.run([sys.executable, "-s", os.path.abspath(__file__)], input=b"{not json",
                              capture_output=True, env=env, timeout=60)
        check(proc.returncode == 0 and not proc.stdout.strip(), "hook: malformed stdin is silent and exits 0")
        fires = io.open(env["CANONICAL_ROUTE_FIRES"], encoding="utf-8").read().splitlines()
        check(len(fires) == 1 and fires[0].count("\t") == 4, "log: exactly one fire row with five tab-separated fields")
    print("selftest: %d failure(s)" % len(failures))
    return 3 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(_selftest())
    sys.exit(main())
