"""hook_dispatch_wire.py - wire (or unwire) the single-process hook dispatcher in settings.json.

  python -X utf8 hook_dispatch_wire.py --on    PreToolUse UserPromptSubmit
  python -X utf8 hook_dispatch_wire.py --off

--on  copies the CURRENT hooks section to hook_dispatch_list.json (the dispatcher's source of truth for what to run,
      matchers and all), then replaces each named event in settings.json with one block: matcher ".*", one hook,
      the dispatcher. Everything else in settings.json is untouched. A timestamped backup is written first.
--off restores the named events from hook_dispatch_list.json - the exact blocks that were there before.

The dispatcher itself stays inert unless HOOK_DISPATCH_ENABLED exists, so --on plus a missing flag file = hooks that
do not run at all. --on creates the flag; --off removes it.
"""
import json, os, shutil, sys, time

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


SETTINGS = os.path.expanduser(r"~/.claude/settings.json")
LIST = os.path.expanduser(r"~/.claude/dreaming/hook_dispatch_list.json")
ENABLED = os.path.expanduser(r"~/.claude/dreaming/HOOK_DISPATCH_ENABLED")
DISPATCH_CMD = "python -s ~/.claude/dreaming/hook_dispatch.py"


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(p, obj):
    tmp = p + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")
    os.replace(tmp, p)


def backup():
    b = SETTINGS + ".bak_dispatch_" + time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(SETTINGS, b)
    return b


def main():
    if "--off" in sys.argv:
        s = load(SETTINGS)
        saved = load(LIST)
        b = backup()
        n = 0
        for ev, blocks in saved.items():
            if ev in s.get("hooks", {}):
                s["hooks"][ev] = blocks
                n += 1
        save(SETTINGS, s)
        if os.path.exists(ENABLED):
            os.remove(ENABLED)
        print("restored %d events from %s; backup %s; flag removed" % (n, LIST, os.path.basename(b)))
        return 0

    events = [a for a in sys.argv[1:] if not a.startswith("-")] or ["PreToolUse", "UserPromptSubmit"]
    s = load(SETTINGS)
    hooks = s.get("hooks", {})
    for ev in events:
        if ev not in hooks:
            print("no such event in settings.json: %s" % ev)
            return 2
        if any(("hook_dispatch.py" in (h.get("command", "") + " ".join(h.get("args") or [])))
               for blk in hooks[ev] for h in blk.get("hooks", [])):
            print("%s already wired to the dispatcher - refusing to double-wire" % ev)
            return 2
    # the list file must hold the FULL current hooks section, so the dispatcher can find every entry and --off can restore
    save(LIST, hooks)
    b = backup()
    for ev in events:
        hooks[ev] = [{"matcher": ".*", "hooks": [{"type": "command", "command": DISPATCH_CMD, "timeout": 600}]}]
    s["hooks"] = hooks
    save(SETTINGS, s)
    open(ENABLED, "w", encoding="utf-8").write(time.strftime("%Y-%m-%d %H:%M:%S") + " wired: " + ",".join(events) + "\n")
    print("wired %s; list saved to %s; backup %s; flag created" % (", ".join(events), os.path.basename(LIST), os.path.basename(b)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
