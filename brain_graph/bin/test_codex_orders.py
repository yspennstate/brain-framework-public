"""test_codex_orders.py - the Codex orders hook (~/.claude/dreaming/codex_orders_hook.py) against a copy of the installed
membrane, its log and state in a temp dir (27 Sep, claude-825b1305). His prompt, then eight tool calls, in three sessions
whose rollouts say source "cli" (he types there), "exec" (codex exec) and nothing (no transcript): the order is served
once, as Codex JSON, in the first only; every evaluation of all three is logged.

    python test_codex_orders.py [PATH_TO_live_membrane.py] [PATH_TO_codex_orders_hook.py]
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

H = os.path.expanduser("~")
MEMB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(H, ".claude", "brain_graph", "bin", "live_membrane.py")
HOOK = sys.argv[2] if len(sys.argv) > 2 else os.path.join(H, ".claude", "dreaming", "codex_orders_hook.py")
tmp = tempfile.mkdtemp(prefix="codex_orders_test_")
shutil.copy(MEMB, os.path.join(tmp, "live_membrane.py"))
sys.path.insert(0, tmp)
sys.path.insert(1, os.path.join(H, ".claude", "brain_graph", "bin"))
import live_membrane as LM  # the copy under test
real_policy = LM.policy
LM.policy = lambda: dict(real_policy(), serve_orders_codex=True, orders_min=30.0, orders_stable=2, hold_every=0)
spec = importlib.util.spec_from_file_location("coh", HOOK)
Hk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(Hk)
Hk.BIN, Hk.LIVE = tmp, tmp
Hk.STATE_DIR, Hk.LOG = os.path.join(tmp, "state"), os.path.join(tmp, "log.jsonl")


def rollout(source):
    p = os.path.join(tmp, "rollout_%s.jsonl" % source)
    meta = {"type": "session_meta", "payload": {"id": "x", "source": source,
                                                  "originator": "codex_exec" if source == "exec" else "codex_cli_rs"}}
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(meta) + "\n")
    return p


cmds = ["ffmpeg -i board_01.mp4 -c:v libx264 out.mp4", "python -m manim render scene.py Board01 -qh",
        "blender -b film.blend -a", "ffmpeg -hwaccel cuda -i in.mp4 -c:v h264_nvenc out.mp4",
        "nvidia-smi --query-gpu=utilization.gpu --format=csv", "python movie_projects/render_one.py --film kernel",
        "ffmpeg -i a.mp4 -i b.wav -c:v copy out.mp4", "python movie_projects/render_one.py --film path"]
ask = "ya i don't understand why gpu is still not being used at all even though we are having computations problems, please fix this"
served = {}
t = 1790530000.0
for name, path in (("cli", rollout("cli")), ("exec", rollout("exec")), ("none", None)):
    sid = "codex-test-0927-" + name
    seq = [{"hook_event_name": "UserPromptSubmit", "session_id": sid, "prompt": ask}]
    seq += [{"hook_event_name": "PreToolUse", "session_id": sid, "tool_input": {"command": c}} for c in cmds]
    served[name] = []
    for i, p in enumerate(seq):
        if path:
            p["transcript_path"] = path
        out = Hk.run(p, now=t + 30 * i)
        if out:
            served[name].append(out["hookSpecificOutput"]["additionalContext"])
rows = [json.loads(l) for l in open(Hk.LOG, encoding="utf-8")]
for r in rows:
    print(r["sid"][-4:], r["ev"], "lead", r["lead"], r["score"], "run", r["run"], "ia", r.get("ia"), "served", r["served"])
for name in served:
    print(name, "served lines:", len(served[name]))
for s in served["cli"]:
    print("  ", s)
ok = (len(served["cli"]) == 1 and served["cli"][0].startswith("[his standing order] `")
      and "his later words win" in served["cli"][0] and not served["exec"] and not served["none"]
      and sum(1 for r in rows if r["served"]) == 1 and len({r["sid"] for r in rows}) == 3)
print("RESULT", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
