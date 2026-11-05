#!/usr/bin/env python3
"""gemini_brain_hook.py - the brain's lanes for Gemini CLI sessions (interactive and the math-brain swarm workers).

Owner order 2026-09-07 13:0x: "make sure hooks are working for gemini as well ... i want the hooks in the brain to
always connect." Gemini CLI 0.58 has hooks (BeforeAgent / BeforeTool / AfterTool / SessionStart / Stop-like AfterAgent;
stdin JSON with session_id, hook_event_name, prompt; stdout hookSpecificOutput.additionalContext; timeout in ms,
default 60000). The brain's lanes speak the Claude hook dialect (hook_event_name UserPromptSubmit / PreToolUse / Stop,
prompt, session_id). This adapter maps one to the other and runs the lanes as subprocesses with a generous budget,
concatenating what they inject. One adapter process per event instead of one process per lane in the settings file,
so a hundred swarm workers do not multiply the spawn count.

Settings (Gemini): {"hooks": {"BeforeAgent": [{"matcher": "*", "hooks": [{"type": "command",
  "command": "python -s ~/.claude/dreaming/gemini_brain_hook.py", "timeout": 600000}]}], ...}}
Lanes per event: BeforeAgent -> instinct_prime, proof_sense, math_sense, waste_sense, spread_recall, fok_probe
(GEMINI_BRAIN_LANES=light restricts to instinct_prime, proof_sense, math_sense for the swarm workers);
BeforeTool -> instinct_prime (the PreToolUse reflexes); AfterAgent -> waste_sense (Stop). Fail-safe: silent on error.
    python gemini_brain_hook.py --selftest
"""
import io, json, os, subprocess, sys, time

DREAMING = os.path.dirname(os.path.abspath(__file__))
LANES = {
    "UserPromptSubmit": ["instinct_prime.py", "proof_sense.py", "math_sense.py", "waste_sense.py", "spread_recall.py", "fok_probe.py"],
    "PreToolUse": ["instinct_prime.py"],
    "Stop": ["waste_sense.py"],
}
LIGHT = {"UserPromptSubmit": ["instinct_prime.py", "proof_sense.py", "math_sense.py"], "PreToolUse": ["instinct_prime.py"], "Stop": ["waste_sense.py"]}
EVENT_MAP = {"BeforeAgent": "UserPromptSubmit", "BeforeTool": "PreToolUse", "AfterAgent": "Stop", "SessionEnd": "Stop"}
LANE_BUDGET_S = float(os.environ.get("GEMINI_BRAIN_LANE_BUDGET_S", "540"))


def run_lane(script, payload):
    p = os.path.join(DREAMING, script)
    if not os.path.exists(p):
        return ""
    try:
        r = subprocess.run([sys.executable, "-s", p], input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=LANE_BUDGET_S, creationflags=(0x08000000 if os.name == "nt" else 0), env=dict(os.environ, PYTHONUTF8="1"))
        out = (r.stdout or "").strip()
        if not out:
            return ""
        d = json.loads(out)
        return ((d.get("hookSpecificOutput") or {}).get("additionalContext") or d.get("additionalContext") or "").strip()
    except Exception:
        return ""


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    gev = data.get("hook_event_name") or "BeforeAgent"
    ev = EVENT_MAP.get(gev)
    if not ev:
        return
    payload = {"hook_event_name": ev, "session_id": str(data.get("session_id") or os.environ.get("GEMINI_SESSION_ID") or ""),
               "prompt": data.get("prompt") or "", "tool_name": data.get("tool_name") or "", "tool_input": data.get("tool_input") or {},
               "cwd": data.get("cwd") or os.getcwd(), "runtime": "gemini"}
    light = os.environ.get("GEMINI_BRAIN_LANES") == "light" or "--light" in sys.argv
    lanes = (LIGHT if light else LANES).get(ev, [])
    parts = []
    for script in lanes:
        text = run_lane(script, payload)
        if text:
            parts.append(text)
    if not parts:
        return
    block = "\n\n".join(parts)
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": gev, "additionalContext": block}}))


def _selftest():
    proof = ("You are auditing a numbered mathematical argument.\n\nCLAIM: Let $X_n$ be independent and uniformly bounded. Then $S_n/n \\to 0$ as $n \\to \\infty$ almost surely.\n\n"
             "ARGUMENT:\n1. By Chebyshev, $P(|S_n| > n\\epsilon) \\le C/(n \\epsilon^2)$ for all $n$.\n2. Hence, as $n \\to 0$, the probabilities are summable and Borel-Cantelli applies.\n3. Therefore $S_n/n \\to 0$ almost surely.\n\nReturn ONLY a JSON object")
    t0 = time.time()
    sys.stdin = io.StringIO(json.dumps({"hook_event_name": "BeforeAgent", "session_id": "gemini-selftest-%d" % int(t0), "prompt": proof}))
    buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
    try:
        main()
    finally:
        sys.stdout = old
    out = buf.getvalue()
    d = json.loads(out) if out else {}
    ctx = (d.get("hookSpecificOutput") or {}).get("additionalContext", "")
    tags = [t for t in ("[proof-sense]", "[math-brain]", "[unconscious]", "[waste-sense]") if t in ctx]
    ok = "[proof-sense]" in ctx and d.get("hookSpecificOutput", {}).get("hookEventName") == "BeforeAgent"
    print("gemini_brain_hook selftest: %s in %.1fs - lanes delivered %s (%d chars)" % ("PASS" if ok else "FAIL", time.time() - t0, tags, len(ctx)))
    return 0 if ok else 1


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print("usage: gemini_brain_hook.py [--selftest]   (hook: reads the event JSON on stdin; help is inert)")
        raise SystemExit(0)
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    try:
        main()
    except Exception:
        pass
