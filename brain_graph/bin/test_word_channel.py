"""Controls both ways for the membrane's word channel (26 Sep). Fire log and session state go to scratch, never live."""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import json
import os
import sys
import tempfile
import time

BIN = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "bin")
sys.path.insert(0, BIN)
import live_membrane as LM

tmp = tempfile.mkdtemp(prefix="wordctl_")
LM.FIRES = os.path.join(tmp, "fires.jsonl")
LM.SESS = os.path.join(tmp, "sessions")
LM.SERVED = os.path.join(tmp, "served.jsonl")
LAW = "owner_law_brain_data_is_never_deleted_2026_09_26"
MEMDIR = os.path.join(os.path.expanduser("~"), ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace("\\", "-").replace("/", "-")), "memory")


def transcript(name, records):
    """Compact JSON with a sessionId, as Claude Code writes them (the capture's pre-filter matches '"type":"user"')."""
    import uuid
    p = os.path.join(tmp, name + ".jsonl")
    sid = str(uuid.uuid4())
    with open(p, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(dict(r, sessionId=sid, cwd=os.path.expanduser("~")), separators=(",", ":")) + "\n")
    return p


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


def user(text, t):
    return {"type": "user", "timestamp": iso(t), "message": {"role": "user", "content": text},
            "origin": {"kind": "human"}}


def read_call(path, t):
    return {"type": "assistant", "timestamp": iso(t), "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "tu1", "name": "Read", "input": {"file_path": path}}]}}


results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print("%-4s %s %s" % ("PASS" if cond else "FAIL", name, detail))


now = time.time()
strong = ("write a rule, the data relevant to this kind of brain activity and improving the brain should never ever be "
          "deleted, because the most important research product on this whole computer is the brain")

# 0. the candidate function on its own
t0 = time.time()
if not os.path.exists(LM.SIDECAR):          # a fresh install: no graph built yet, nothing to read
    print("SKIP - no graph sidecar yet (a fresh install builds it on the first graph build): %s" % LM.SIDECAR)
    sys.exit(0)
lv = LM.Live()
cands = LM.word_candidates(__import__("extract_touch_events").words(strong, cap=40), set(), lv.db)
ms = (time.time() - t0) * 1000
check("candidates: the law is the top match for his own words", cands and cands[0]["slug"] == LAW,
      "top %s score %s (%.0f ms)" % (cands[0]["slug"] if cands else None, cands[0]["score"] if cands else None, ms))

# 1. default policy (serve_word off): his prompt in an interactive session -> computed and logged, not shown
os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"
_pol = LM.policy
p1 = transcript("s1", [user(strong, now - 1)])
out0 = LM.run({"hook_event_name": "UserPromptSubmit", "session_id": "ctl-word-0", "transcript_path": p1,
               "prompt": strong}, now=now, force=True)
check("default: word line not shown (shadow)", "the prompt's own words match it" not in out0, repr(out0[:160]))
# 1a. serve_word on, bar 40, interactive -> shown
LM.policy = lambda: dict(_pol(), word_min=40.0, serve_word=True)
out = LM.run({"hook_event_name": "UserPromptSubmit", "session_id": "ctl-word-1", "transcript_path": p1,
              "prompt": strong}, now=now, force=True)
check("on + interactive: word line served", "the prompt's own words match it" in out and LAW in out, repr(out[:200]))
# 1b. serve_word on, bar 40, headless -> never shown
os.environ["CLAUDE_CODE_ENTRYPOINT"] = "sdk-cli"
outh = LM.run({"hook_event_name": "UserPromptSubmit", "session_id": "ctl-word-1h", "transcript_path": p1,
               "prompt": strong}, now=now, force=True)
check("on + headless: no word line", "the prompt's own words match it" not in outh, repr(outh[:160]))
os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"
# (filters below run with the word line switched on)
# 2. same session, same prompt, 60 s later -> not served again (refractory)
out2 = LM.run({"hook_event_name": "UserPromptSubmit", "session_id": "ctl-word-1", "transcript_path": p1,
               "prompt": strong}, now=now + 60, force=True)
check("negative: no repeat of the same memory in the session", LAW not in out2, repr(out2[:160]))

# 3. a prompt with no content -> no word line
p3 = transcript("s3", [user("keep working", now - 1)])
out3 = LM.run({"hook_event_name": "UserPromptSubmit", "session_id": "ctl-word-3", "transcript_path": p3,
               "prompt": "keep working"}, now=now, force=True)
check("negative: 'keep working' serves no word line", "the prompt's own words match it" not in out3, repr(out3[:160]))

# 4. hook text arriving as a prompt is not his -> no word line
fake = "Stop hook feedback: " + strong
p4 = transcript("s4", [user(fake, now - 1)])
out4 = LM.run({"hook_event_name": "UserPromptSubmit", "session_id": "ctl-word-4", "transcript_path": p4,
               "prompt": fake}, now=now, force=True)
check("negative: hook feedback is not his prompt", "the prompt's own words match it" not in out4, repr(out4[:160]))

# 5. the session already read the law -> not served
p5 = transcript("s5", [read_call(os.path.join(MEMDIR, LAW + ".md"), now - 30), user(strong, now - 1)])
out5 = LM.run({"hook_event_name": "UserPromptSubmit", "session_id": "ctl-word-5", "transcript_path": p5,
               "prompt": strong}, now=now, force=True)
check("negative: a memory the session already read is not served", LAW not in out5, repr(out5[:200]))

# 6. a tool-call moment carries no word channel
out6 = LM.run({"hook_event_name": "PostToolUse", "session_id": "ctl-word-6", "transcript_path": p1,
               "prompt": strong}, now=now, force=True)
check("negative: tool-call moments get no word line", "the prompt's own words match it" not in out6, repr(out6[:160]))

LM.policy = _pol
# 7. the fire log records the word candidates on prompts (the learner's shadow)
recs = [json.loads(x) for x in open(LM.FIRES, encoding="utf-8")]
check("log: records carry the session kind", any(r.get("ep") == "cli" for r in recs) and any(r.get("ep") == "sdk-cli" for r in recs))
check("log: prompt evaluations carry 'word'", any(r.get("ev") == "prompt" and r.get("word") for r in recs),
      "%d records" % len(recs))
check("log: the served word line is recorded as kind 'word'",
      any(any(s[0] == "word" for s in (r.get("served") or [])) for r in recs))
# 8. timing of a prompt evaluation with the channel
ts = []
for i in range(5):
    a = time.time()
    LM.run({"hook_event_name": "UserPromptSubmit", "session_id": "ctl-word-t%d" % i, "transcript_path": p1,
            "prompt": strong}, now=now + 10 * i, force=True)
    ts.append((time.time() - a) * 1000)
print("prompt evaluation ms:", [round(x) for x in ts])
print("RESULT %d/%d" % (sum(ok for _n, ok in results), len(results)), "scratch", tmp)
