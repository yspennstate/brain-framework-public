"""hook_dispatch_deadman.py - unwire the one-process hook dispatcher after N minutes unless it is confirmed.

Written 2026-09-17 07:0x for the case the 09-15 scar left open: the dispatcher sits in front of every session's hooks,
a bad wiring blocks every tool call, and a tool call is the only thing the wiring agent has - on 09-15 the owner had to
run --off from a terminal by hand. This watcher runs DETACHED and needs no tool call: at the deadline it runs
hook_dispatch_wire.py --off unless ~/.claude/dreaming/HOOK_DISPATCH_CONFIRMED exists, which the wiring
agent creates only after seeing hooks fire and gates hold on its own calls. If tool calls are blocked, no file appears
and the wiring comes off by itself. Confirmed or not, it logs what it did to hook_dispatch.log.

    pythonw -X utf8 hook_dispatch_deadman.py <minutes>
"""
import os, subprocess, sys, time

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIRM = os.path.join(HERE, "HOOK_DISPATCH_CONFIRMED")
LOG = os.path.expanduser(r"~/.claude/compute/hook_dispatch.log")
def log(m):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "  deadman: " + m + "\n")
    except Exception:
        pass
minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
log("armed for %.0f min; confirm file %s" % (minutes, CONFIRM))
deadline = time.time() + minutes * 60
while time.time() < deadline:
    time.sleep(5)
    if os.path.exists(CONFIRM):
        log("confirmed at %s - the wiring stays" % time.strftime("%H:%M:%S")); sys.exit(0)
if os.path.exists(CONFIRM):
    log("confirmed at the deadline - the wiring stays"); sys.exit(0)
r = subprocess.run([sys.executable.replace("pythonw.exe", "python.exe"), "-X", "utf8", os.path.join(HERE, "hook_dispatch_wire.py"), "--off"],
                   capture_output=True, text=True, creationflags=(0x08000000 if os.name == "nt" else 0))
log("UNCONFIRMED after %.0f min - ran --off: rc=%s %s" % (minutes, r.returncode, (r.stdout or r.stderr or "").strip()[:160]))
