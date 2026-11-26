"""Compaction guard: warn when automatic compaction is not set up or is not happening.

The brain keeps every Claude session lean by compacting automatically near 300k tokens of context
("autoCompactWindow": 300000 in ~/.claude/settings.json; each compaction is saved as a brain node by
compact_nodes.py). A machine whose settings lack that line lets sessions grow to well over a million tokens a call. This hook makes that loud instead of silent.

As a UserPromptSubmit hook (reads the hook JSON on stdin) it prints a warning when
  1. settings.json has no autoCompactWindow, or one above MAX_WINDOW, or autoCompactEnabled is false; or
  2. this session's last call read more than OVER x the window (compaction is not happening).
It prints nothing when all is well. It never blocks and fails open.

  python compaction_guard.py --fleet [--minutes 60]   every session written in the last hour, oversize ones listed
  python compaction_guard.py --selftest
"""
import glob, io, json, os, sys, time

DEFAULT_WINDOW = 300000
MAX_WINDOW = 400000
OVER = 1.25
LOG = os.path.expanduser("~/.claude/compute/compaction_guard.log")
HEAD = "[brain-setup] WARNING - THE FULL BRAIN HAS NOT BEEN SET UP PROPERLY: "


def settings_problem(path=None):
    path = path or os.path.expanduser("~/.claude/settings.json")
    try:
        d = json.load(io.open(path, encoding="utf-8"))
    except Exception as e:
        return None, "cannot read %s (%s)" % (path, e)
    w = d.get("autoCompactWindow")
    if d.get("autoCompactEnabled") is False:
        return w, "autoCompactEnabled is false in %s; automatic compaction is off" % path
    if not isinstance(w, int):
        return None, ("%s has no \"autoCompactWindow\" line, so sessions run to the model's full window "
                      "(hundreds of thousands of tokens a call) instead of compacting near %d. "
                      "Fix: add \"autoCompactWindow\": %d to it." % (path, DEFAULT_WINDOW, DEFAULT_WINDOW))
    if w > MAX_WINDOW:
        return w, "autoCompactWindow is %d in %s; the brain's setting is %d" % (w, path, DEFAULT_WINDOW)
    return w, None


def last_context(transcript, tail=4 << 20):
    """Tokens the session's last model call read (input + cache read + cache write), from the transcript tail."""
    try:
        with open(transcript, "rb") as f:
            f.seek(0, 2)
            n = f.tell()
            f.seek(max(0, n - tail))
            lines = f.read().decode("utf-8", "ignore").splitlines()
    except Exception:
        return None
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        m = d.get("message")
        u = m.get("usage") if isinstance(m, dict) else None
        if isinstance(u, dict):
            return (u.get("input_tokens", 0) or 0) + (u.get("cache_read_input_tokens", 0) or 0) + \
                   (u.get("cache_creation_input_tokens", 0) or 0)
    return None


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def check(transcript, settings_path=None):
    out = []
    w, prob = settings_problem(settings_path)
    if prob:
        out.append(HEAD + prob)
    win = w if isinstance(w, int) else DEFAULT_WINDOW
    ctx = last_context(transcript) if transcript else None
    if ctx and ctx > OVER * win:
        out.append(HEAD + ("this session's last call read %d tokens, past the %d auto-compaction point, so "
                           "compaction is not happening here. Every call now costs about %.1fx what it should. "
                           "Run /compact at the end of the current step, and check autoCompactWindow in "
                           "~/.claude/settings.json." % (ctx, win, ctx / float(win))))
    try:   # alerts from compact_watch.py (fleet seats not compacting, compactions that never became nodes)
        a = json.load(io.open(os.path.expanduser("~/.claude/compute/compaction_alerts.json"), encoding="utf-8"))
        for line in (a.get("alerts") or [])[:3]:
            out.append(HEAD + line + " (compact_watch.py, %s)" % a.get("at", "?"))
    except Exception:
        pass
    return out


def fleet(minutes):
    w, prob = settings_problem()
    win = w if isinstance(w, int) else DEFAULT_WINDOW
    if prob:
        print(HEAD + prob)
    now, bad, n = time.time(), [], 0
    for t in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
        try:
            if now - os.path.getmtime(t) > minutes * 60:
                continue
        except OSError:
            continue
        n += 1
        c = last_context(t)
        if c and c > OVER * win:
            bad.append((c, os.path.basename(t)[:36]))
    print("%d session(s) written in the last %d min; %d past %.0f%% of the %d window:" % (n, minutes, len(bad), OVER * 100, win))
    for c, t in sorted(bad, reverse=True):
        print("  %9d  %s" % (c, t))
    return 1 if (bad or prob) else 0


def selftest():
    import tempfile
    d = tempfile.mkdtemp()
    s_ok, s_bad = os.path.join(d, "ok.json"), os.path.join(d, "bad.json")
    json.dump({"autoCompactWindow": 300000}, open(s_ok, "w"))
    json.dump({"model": "opus"}, open(s_bad, "w"))
    t_small, t_big = os.path.join(d, "s.jsonl"), os.path.join(d, "b.jsonl")
    u = lambda n: json.dumps({"message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": n, "cache_creation_input_tokens": 5}}})
    open(t_small, "w").write(u(200000) + "\n")
    open(t_big, "w").write(u(100) + "\n" + u(700000) + "\n")
    cases = [("good settings, small session: silent", check(t_small, s_ok) == []),
             ("missing autoCompactWindow: warns", len(check(t_small, s_bad)) == 1),
             ("good settings, 700k session: warns", len(check(t_big, s_ok)) == 1),
             ("missing setting and 700k: two warnings", len(check(t_big, s_bad)) == 2),
             ("last call is the one read", last_context(t_big) == 700015),
             ("no transcript: settings only", check(None, s_ok) == [])]
    for name, ok in cases:
        print("%s  %s" % ("PASS" if ok else "FAIL", name))
    return 0 if all(ok for _, ok in cases) else 1


def main():
    a = sys.argv[1:]
    if "--selftest" in a:
        return selftest()
    if "--fleet" in a:
        m = int(a[a.index("--minutes") + 1]) if "--minutes" in a else 60
        return fleet(m)
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        data = {}
    try:
        msgs = check(data.get("transcript_path"))
    except Exception:
        return 0
    if msgs:
        log("%s %s" % (data.get("session_id", "?")[:8], " | ".join(m[len(HEAD):][:160] for m in msgs)))
        print("\n".join(msgs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
