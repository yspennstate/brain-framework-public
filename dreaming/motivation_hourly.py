# -*- coding: utf-8 -*-
"""The hourly motivation piece: once an hour, each session gets one strong paragraph that encourages the agent to be
creative and to do big, careful work.

The pieces are motivation_hourly.jsonl beside this file (ids H01.., each under about 320 characters). Delivery: no background process and no window; this runs as a UserPromptSubmit hook and speaks on the
first prompt after each hour mark of a session (first piece twenty minutes into a session, then every sixty). A
session sees every piece once before any repeats, and sessions start at different pieces. On a prompt where this
speaks, motivation_hooks.py stays silent, so an agent never gets two at once.
Claude Code: plain text through hook_dispatch.py. Codex: --codex prints JSON hookSpecificOutput. Fires are logged to
~/.claude/motivation_hooks/fires.jsonl (hook H..). Silent on any error; the file ~/.claude/motivation_hooks/OFF
silences it. MOTIVATION_STATE and MOTIVATION_FIRES override the stores (tests). --selftest, --now (print one piece)."""
import hashlib
import json
import os
import re
import sys
import time

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
PIECES = os.path.join(HERE, "motivation_hourly.jsonl")
BASE = os.path.join(HOME, ".claude", "motivation_hooks")
STATE = os.environ.get("MOTIVATION_STATE") or os.path.join(BASE, "state")
FIRES = os.environ.get("MOTIVATION_FIRES") or os.path.join(BASE, "fires.jsonl")
OFF = os.path.join(BASE, "OFF")
CODEX = "--codex" in sys.argv
EVERY_S = 3600
FIRST_S = 1200


def load_pieces():
    rows = []
    try:
        with open(PIECES, "r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if raw:
                    rows.append(json.loads(raw))
    except (OSError, ValueError):
        return []
    return rows


def state_path(sid):
    os.makedirs(STATE, exist_ok=True)
    return os.path.join(STATE, re.sub(r"[^A-Za-z0-9_.-]", "_", sid or "unknown")[:80] + ".hourly.json")


def run(data, now=None, pieces=None):
    now = now or time.time()
    if os.path.exists(OFF) or (data.get("hook_event_name") or "") != "UserPromptSubmit":
        return ""
    sid = data.get("session_id") or data.get("conversation_id") or ""
    p = state_path(sid)
    try:
        with open(p, "r", encoding="utf-8") as fh:
            st = json.load(fh)
        if not isinstance(st, dict):
            st = {}
    except (OSError, ValueError):
        st = {}
    fresh = "first_seen" not in st
    st.setdefault("first_seen", now)
    last = st.get("last") or 0
    due = (now - last >= EVERY_S) if last else (now - st["first_seen"] >= FIRST_S)
    row = None
    if due:
        if pieces is None:
            pieces = load_pieces()
        shown = [i for i in (st.get("shown") or []) if isinstance(i, str)]
        left = [r for r in pieces if r["id"] not in shown]
        if not left and pieces:
            shown, left = [], list(pieces)
        if left:
            row = min(left, key=lambda r: hashlib.sha1((sid + r["id"]).encode("utf-8", "replace")).hexdigest())
            st["shown"] = shown + [row["id"]]
            st["last"] = now
    if row or fresh:
        tmp = "%s.%d.tmp" % (p, os.getpid())
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(st, fh)
            os.replace(tmp, p)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
    if not row:
        return ""
    try:
        os.makedirs(os.path.dirname(FIRES), exist_ok=True)
        with open(FIRES, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                                 "runtime": "codex" if CODEX else "claude", "session_id": sid,
                                 "event": "UserPromptSubmit", "hook": row["id"], "kind": "hourly"}) + "\n")
    except OSError:
        pass
    return "[motivation hourly %s] %s" % (row["id"], row["text"])


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    except AttributeError:      # in-process under hook_dispatch the stdin is a StringIO, already text
        raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return
    if not isinstance(data, dict):
        return
    text = run(data)
    if not text:
        return
    if CODEX:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}}))
    else:
        print(text)


def selftest():
    import shutil
    import tempfile
    global STATE, FIRES, OFF
    keep = (STATE, FIRES, OFF)
    tmp = tempfile.mkdtemp(prefix="motivation_hourly_selftest_")
    STATE, FIRES, OFF = os.path.join(tmp, "state"), os.path.join(tmp, "fires.jsonl"), os.path.join(tmp, "OFF")
    pieces = load_pieces()
    res = []
    t0 = 2000000.0
    ev = {"hook_event_name": "UserPromptSubmit", "session_id": "h1", "prompt": "x"}
    res.append(("at least 24 pieces, each one paragraph under 420 chars",
                len(pieces) >= 24 and all("\n" not in r["text"] and len(r["text"]) <= 420 for r in pieces)))
    res.append(("silent on a session's first prompt", run(dict(ev), t0, pieces) == ""))
    res.append(("silent ten minutes in", run(dict(ev), t0 + 600, pieces) == ""))
    a = run(dict(ev), t0 + 1300, pieces)
    res.append(("speaks twenty minutes in", a.startswith("[motivation hourly H")))
    res.append(("silent thirty minutes after speaking", run(dict(ev), t0 + 3100, pieces) == ""))
    b = run(dict(ev), t0 + 1300 + 3600, pieces)
    res.append(("speaks again an hour later, with a different piece", b.startswith("[motivation hourly H") and b != a))
    seen = {a, b}
    tt = t0 + 1300 + 3600
    for _ in range(len(pieces) - 2):
        tt += 3600
        seen.add(run(dict(ev), tt, pieces))
    res.append(("every piece is shown once before any repeats", len(seen) == len(pieces) and "" not in seen))
    res.append(("then it starts over", run(dict(ev), tt + 3600, pieces).startswith("[motivation hourly H")))
    firsts = set()
    for n in range(10):
        run(dict(ev, session_id="w%d" % n), t0, pieces)
        firsts.add(run(dict(ev, session_id="w%d" % n), t0 + 1300, pieces))
    res.append(("sessions start at different pieces", len(firsts) >= 5))
    res.append(("silent on other events", run({"hook_event_name": "PreToolUse", "session_id": "h2"}, t0 + 9e6, pieces) == ""))
    open(OFF, "w").close()
    res.append(("the OFF file silences it", run(dict(ev, session_id="h1"), tt + 99999, pieces) == ""))
    os.remove(OFF)
    STATE, FIRES, OFF = keep
    shutil.rmtree(tmp, ignore_errors=True)
    bad = [n for n, ok in res if not ok]
    for n, ok in res:
        print("%s  %s" % ("PASS" if ok else "FAIL", n))
    print("SELFTEST %s: %d of %d" % ("PASS" if not bad else "FAIL", len(res) - len(bad), len(res)))
    return 0 if not bad else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--now" in sys.argv:
        rows = load_pieces()
        if rows:
            r = rows[int(time.time() // 3600) % len(rows)]
            print("[motivation hourly %s] %s" % (r["id"], r["text"]))
        sys.exit(0)
    try:
        main()
    except Exception:
        pass
