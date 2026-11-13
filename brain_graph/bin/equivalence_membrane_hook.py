"""equivalence_membrane_hook.py - graph_membrane_hook.py in the dispatcher's process vs its own.

hook_dispatch.py runs a hook in-process only when it is on hook_dispatch_allow.json, and a hook goes on that list only
with evidence that in-process and own-process runs give the same exit and the same output. Five payloads: two silent by
construction (no transcript; STOP present), three on real transcripts with a fresh session id per run (the hook keeps
per-session state, so each run starts clean). Digits are masked before comparing (ages and weights move by the second).

    python equivalence_membrane_hook.py      exit 0 = identical on every payload
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import glob
import json
import os
import re
import subprocess
import sys
import uuid

HOOK = os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "graph_membrane_hook.py")
sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".claude", "dreaming"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hook_dispatch as HD  # noqa: E402
import live_membrane as LM  # noqa: E402

PROJ = os.path.join(os.path.expanduser("~"), ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")))


def own_process(payload):
    p = subprocess.run([sys.executable, "-s", HOOK], input=json.dumps(payload).encode("utf-8"), capture_output=True,
                       timeout=120, creationflags=0x08000000 if os.name == "nt" else 0)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def in_process(payload):
    r = HD.run_in_process(HOOK, json.dumps(payload))
    return r[0], r[1]


def mask(s):
    return re.sub(r"\d+", "#", s or "")


def main():
    files = sorted((p for p in glob.glob(os.path.join(PROJ, "*.jsonl")) if os.path.getsize(p) > 3_000_000),
                   key=os.path.getmtime, reverse=True)[:6]
    payloads = [({"hook_event_name": "UserPromptSubmit", "session_id": "eq-" + uuid.uuid4().hex, "prompt": "x"}, None)]
    for p in files[:3]:
        payloads.append(({"hook_event_name": "UserPromptSubmit", "transcript_path": p, "prompt": "keep working on this"},
                         None))
    payloads.append(({"hook_event_name": "PostToolUse", "transcript_path": files[3] if len(files) > 3 else files[0],
                      "tool_name": "Bash"}, "stop"))
    ok_all, loud = True, 0
    for base, mode in payloads:
        outs = []
        for runner in (own_process, in_process):
            pl = dict(base, session_id="eq-" + uuid.uuid4().hex)
            if mode == "stop":
                open(LM.STOP, "w").close()
            try:
                rc, out = runner(pl)
            finally:
                if mode == "stop" and os.path.exists(LM.STOP):
                    os.remove(LM.STOP)
            outs.append((rc, mask(out)))
            try:
                os.remove(LM._state_path(pl["session_id"]))
            except OSError:
                pass
        same = outs[0] == outs[1]
        loud += bool(outs[0][1])
        ok_all &= same
        print("%s  %-16s %s rc %s/%s, %d/%d chars" % ("SAME" if same else "DIFF", base["hook_event_name"],
                                                     "stop" if mode else os.path.basename(base.get("transcript_path", "-"))[:12],
                                                     outs[0][0], outs[1][0], len(outs[0][1]), len(outs[1][1])))
        if not same:
            print("   own: %r\n   in:  %r" % (outs[0][1][:300], outs[1][1][:300]))
    print("identical on %d payloads, %d loud" % (len(payloads), loud) if ok_all else "NOT identical")
    sys.exit(0 if ok_all and loud >= 1 else 1)


if __name__ == "__main__":
    main()
