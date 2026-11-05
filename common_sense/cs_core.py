"""Common-sense hooks: the engine.

Each csNNN_<name>.py beside this file is one plain question a sensible person asks before one kind of action
(a broad delete, a number that looks too good, a "you sure?"). This module decides which of them speak on a hook
payload. A hook that speaks prints one line: the question, the rule that answers it, why the rule exists, and how
far its fires have been judged on target. The line is a question, not a verdict.

The same engine serves Claude Code (UserPromptSubmit and PreToolUse hooks, plain stdout) and Codex (hooks.json,
JSON additionalContext). Run every hook in one process with --all: one scoring pass for the whole set, and a payload
that wakes nothing reads and writes no file.

  cs_core.py --all [--codex]            decide on the stdin payload, print every picked line
  cs_core.py --list | --show CS003      the hooks
  cs_core.py --query "<text>" [--tool]  which hooks a text would wake, with scores (no state read or written)
  cs_core.py --judge CS003 on|off       judge a fire; a hook off target on most of its first 10 judgments goes quiet
  cs_core.py --stats                    fires and judgments per hook
  cs_core.py --selftest                 every hook's fire and quiet examples, plus the engine's own controls

Off switch: the file ENABLED beside this one. Absent, every hook is silent.
State lives in CS_STATE_DIR (default ./state): per-session back-off, the fire log, the judgments.

A hook file holds one dict, HOOK, read with ast.literal_eval (never imported for scoring):
  id, area, title, ask, rule, why     the printed line
  events     "prompt", "tool" or "both"
  gate       plain substrings, at least one must occur in the lowered text (cheap first filter)
  path, cmd  regexes on the tool call's file path / command; a tool call needs one of them to hit (3 points)
  kw2, kw    regexes on the text, 2 and 1 points each, capped at 4 and 3
  need, need_tool   the score a prompt / tool call must reach
  not        regexes that veto the hook
  pri        tie-break; fallback=True means a generic hook that gives way to any specific hook of its area
  fire, quiet   examples the selftest checks: "p:<prompt>", "c:<command>", "w:<path>|<content>"
"""
import io
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK_DIR = os.environ.get("CS_HOOK_DIR") or HERE
STATE_DIR = os.environ.get("CS_STATE_DIR") or os.path.join(HERE, "state")
ENABLED = os.path.join(HERE, "ENABLED")
CACHE = os.path.join(STATE_DIR, "registry_cache.json")
TEXT_CAP = 8000
PROMPT_MAX, TOOL_MAX = 2, 1          # at most two lines on a prompt, one on a tool call
TOOL_GAP_S = 300                     # after a tool-call fire, tool calls stay quiet this long in that session
BACKOFF_S = (2700, 7200, 28800)      # one hook in one session: again after 45 min, then 2 h, then 8 h
SHARED_S = 60                        # hooks run in separate processes agree on one decision for this long
LINE_CAP = 700
FILE_RE = re.compile(r"^cs\d{3}_[a-z0-9_]+\.py$")

# Text the harness injects into a prompt is not the user's words: scoring it wakes hooks on boilerplate.
_BLOCK = re.compile(r"<(system-reminder|task-notification|local-command-stdout|local-command-caveat)\b[^>]*>.*?</\1\s*>",
                    re.S | re.I)
_RX = {}


def _rx(p):
    r = _RX.get(p)
    if r is None:
        try:
            r = re.compile(p, re.I)
        except re.error:
            r = False
        _RX[p] = r
    return r or None


def _digest(data):
    import hashlib
    return hashlib.sha1(data).hexdigest()


# ---------------------------------------------------------------- registry
def _files():
    out = []
    try:
        with os.scandir(HOOK_DIR) as it:
            for e in it:
                if FILE_RE.match(e.name):
                    st = e.stat()
                    out.append("%s:%d:%d" % (e.name, st.st_mtime_ns, st.st_size))
    except OSError:
        pass
    return sorted(out)


def parse_hook(path):
    """The hooks of one file: its HOOK dict, or a pack's HOOKS list."""
    import ast
    src = io.open(path, encoding="utf-8").read()
    for n in ast.parse(src).body:
        if not isinstance(n, ast.Assign):
            continue
        names = {getattr(t, "id", "") for t in n.targets}
        if "HOOK" in names:
            h = ast.literal_eval(n.value)
            if isinstance(h, dict):
                h["_file"] = os.path.basename(path)
                return [h]
        if "HOOKS" in names:
            hs = ast.literal_eval(n.value)
            return [dict(h, _file=os.path.basename(path)) for h in (hs if isinstance(hs, list) else ())
                    if isinstance(h, dict)]
    return []


_REG = None


def registry():
    global _REG
    if _REG is not None:
        return _REG
    sig = _files()
    try:
        c = json.load(io.open(CACHE, encoding="utf-8"))
        if c.get("sig") == sig:
            _REG = c["hooks"]
            return _REG
    except Exception:
        pass
    hooks = []
    for s in sig:
        try:
            hooks.extend(h for h in parse_hook(os.path.join(HOOK_DIR, s.split(":", 1)[0])) if h.get("id"))
        except Exception:
            pass
    hooks.sort(key=lambda h: (len(h["id"]), h["id"]))
    _REG = hooks
    _atomic(CACHE, json.dumps({"sig": sig, "hooks": hooks}, ensure_ascii=True))
    return _REG


def by_id(i):
    i = str(i).upper()
    for h in registry():
        if h["id"] == i:
            return h
    return None


# ---------------------------------------------------------------- scoring
def event_of(payload):
    name = str(payload.get("hook_event_name") or "")
    if name == "UserPromptSubmit":
        return "prompt"
    return "tool" if payload.get("tool_name") or name in ("PreToolUse", "PostToolUse") else "prompt"


def user_text(t):
    """The prompt without the blocks the harness injected into it."""
    return _BLOCK.sub(" ", t) if "<" in t else t


def texts(payload, event):
    """(text the words are matched in, path in lower case with forward slashes, command)."""
    if event == "prompt":
        t = payload.get("prompt") or payload.get("user_prompt") or ""
        t = t if isinstance(t, str) else json.dumps(t, default=str)
        return user_text(t)[:TEXT_CAP], "", ""
    ti = payload.get("tool_input") or {}
    if not isinstance(ti, dict):
        ti = {}
    cmd = ti.get("command") or ""
    if isinstance(cmd, list):
        cmd = " ".join(str(c) for c in cmd)
    cmd = str(cmd)[:TEXT_CAP]
    path = str(ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or "")
    parts = [ti.get("content"), ti.get("new_string")]
    for e in ti.get("edits") or []:
        if isinstance(e, dict):
            parts.append(e.get("new_string"))
    body = "\n".join(p for p in parts if isinstance(p, str))[:TEXT_CAP]
    return "\n".join(x for x in (cmd, path, body) if x), path.replace("\\", "/").lower(), cmd


def score(h, event, low, text, path, cmd):
    """Score of one hook, 0 when it cannot speak here. A tool call needs a path or command hit."""
    ev = h.get("events", "both")
    if ev != "both" and ev != event:
        return 0
    if not any(g in low for g in h.get("gate") or ()):
        return 0
    s = 0
    if event == "tool":
        hit = 0
        r = _rx(h["path"]) if h.get("path") else None
        if r and path and r.search(path):
            hit = 3
        r = _rx(h["cmd"]) if h.get("cmd") else None
        if r and cmd and r.search(cmd):
            hit = 3
        if not hit:
            return 0
        s += hit
    for key, w, cap in (("kw2", 2, 4), ("kw", 1, 3)):
        got = 0
        for p in h.get(key) or ():
            r = _rx(p)
            if r and r.search(text):
                got += w
                if got >= cap:
                    break
        s += min(got, cap)
    if s < (h.get("need_tool", 4) if event == "tool" else h.get("need", 2)):
        return 0
    for p in h.get("not") or ():
        r = _rx(p)
        if r and r.search(text):
            return 0
    return s


def candidates(payload, event):
    text, path, cmd = texts(payload, event)
    if not text or (event == "tool" and "/common_sense/" in path):
        return []
    low = text.lower()
    out = []
    for h in registry():
        if h.get("off"):
            continue
        s = score(h, event, low, text, path, cmd)
        if s:
            out.append((s, h))
    return out


# ---------------------------------------------------------------- judgments and state
_JUDGED = None


def judged():
    """{id: (on, off)} over the first 10 judgments of each hook."""
    global _JUDGED
    if _JUDGED is not None:
        return _JUDGED
    j = {}
    try:
        for ln in io.open(os.path.join(STATE_DIR, "judged.jsonl"), encoding="utf-8"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            on, off = j.get(r.get("id"), (0, 0))
            if on + off < 10:
                j[r.get("id")] = (on + (r.get("v") == "on"), off + (r.get("v") == "off"))
    except OSError:
        pass
    _JUDGED = j
    return j


def silenced(i):
    on, off = judged().get(i, (0, 0))
    return off >= 6 or (on + off >= 4 and on == 0)


def precision(i):
    on, off = judged().get(i, (0, 0))
    return (on + 1.0) / (on + off + 2.0)


def _atomic(path, text):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".%d.tmp" % os.getpid()
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        for i in range(5):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                time.sleep(0.05 * (i + 1))
        os.remove(tmp)
    except OSError:
        pass
    return False


def load_state():
    try:
        st = json.load(io.open(os.path.join(STATE_DIR, "state.json"), encoding="utf-8"))
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def save_state(st, now):
    for k in list(st):
        v = st[k]
        if k == "_shared":
            st[k] = {a: b for a, b in v.items() if now - b.get("t", 0) < SHARED_S}
        elif not isinstance(v, dict) or now - v.get("last", 0) > 86400:
            st.pop(k, None)
    _atomic(os.path.join(STATE_DIR, "state.json"), json.dumps(st, ensure_ascii=True))


def _append(name, row):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with io.open(os.path.join(STATE_DIR, name), "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")
    except Exception:
        pass


def session_of(payload):
    return str(payload.get("session_id") or payload.get("sessionId") or payload.get("thread_id") or "unknown")


# ---------------------------------------------------------------- decision
_DECIDED = {}


def decide(payload, event, use_state=True):
    """Ids picked for this payload, at most PROMPT_MAX / TOOL_MAX, best score first, after the session's back-off."""
    key = event + ":" + _digest(json.dumps(payload, sort_keys=True, default=str).encode("utf-8", "replace"))
    if use_state and key in _DECIDED:
        return _DECIDED[key]
    cands = [(s, h) for s, h in candidates(payload, event) if not silenced(h["id"])]
    areas = {h.get("area") for s, h in cands if not h.get("fallback")}
    cands = [(s, h) for s, h in cands if not (h.get("fallback") and h.get("area") in areas)]
    cands.sort(key=lambda x: (-x[0], -x[1].get("pri", 1), -precision(x[1]["id"]), x[1]["id"]))
    lim = PROMPT_MAX if event == "prompt" else TOOL_MAX
    picks = []
    if cands and not use_state:
        picks = [h["id"] for s, h in cands[:lim]]
    elif cands:
        now = time.time()
        st = load_state()
        sh = st.setdefault("_shared", {}).get(key)
        if sh and now - sh.get("t", 0) < SHARED_S:
            picks = list(sh.get("ids") or [])
        else:
            sess = session_of(payload)
            ss = st.get(sess) if isinstance(st.get(sess), dict) else {}
            ss.setdefault("seen", {})
            if not (event == "tool" and now - ss.get("tool_last", 0) < TOOL_GAP_S):
                for s, h in cands:
                    n, last = (ss["seen"].get(h["id"]) or [0, 0])[:2]
                    if n and now - last < BACKOFF_S[min(n, 3) - 1]:
                        continue
                    picks.append(h["id"])
                    if len(picks) >= lim:
                        break
            if picks:
                for i in picks:
                    ss["seen"][i] = [(ss["seen"].get(i) or [0, 0])[0] + 1, now]
                if event == "tool":
                    ss["tool_last"] = now
                ss["last"] = now
                st[sess] = ss
                st["_shared"][key] = {"t": now, "ids": picks}
                save_state(st, now)
                _append("fires.jsonl", {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "session": sess, "event": event,
                                        "ids": picks, "scores": {h["id"]: s for s, h in cands if h["id"] in picks}})
    if use_state:
        if len(_DECIDED) > 64:
            _DECIDED.clear()
        _DECIDED[key] = picks
    return picks


# ---------------------------------------------------------------- the line
def ascii_only(s):
    table = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
             "…": "...", " ": " "}
    return "".join(table.get(c, c) for c in str(s)).encode("ascii", "ignore").decode("ascii")


def full_line(h):
    on, off = judged().get(h["id"], (0, 0))
    hint = "judged on target %d of %d" % (on, on + off) if on + off else "unjudged"
    why = (" Why: " + h["why"]) if h.get("why") else ""
    s = "[common sense %s | %s] %s %s%s (%s; a question, not a verdict. Judge: cs_core.py --judge %s on|off)" % (
        h["id"], h["title"], h["ask"], h["rule"], why, hint, h["id"])
    return ascii_only(" ".join(s.split()))


def line_for(h):
    s = full_line(h)
    return s if len(s) <= LINE_CAP else s[:LINE_CAP - 4] + " ..."


def emit(lines, codex, event):
    if not lines:
        return
    text = "\n".join(lines)
    if codex:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse" if event == "tool" else "UserPromptSubmit", "additionalContext": text}},
            ensure_ascii=True))
    else:
        print(text)


def _payload_in():
    try:
        p = json.loads(sys.stdin.read() or "{}")
    except Exception:
        p = {}
    return p if isinstance(p, dict) else {}


def run(hook):
    """Entry of a single hook file run on its own: print this hook's line if it was picked. Never raises."""
    try:
        if not os.path.exists(ENABLED):
            return
        payload = _payload_in()
        event = event_of(payload)
        if hook.get("id") in decide(payload, event):
            emit([line_for(by_id(hook["id"]) or hook)], "--codex" in sys.argv, event)
    except Exception:
        pass


def run_all(codex):
    try:
        if not os.path.exists(ENABLED):
            return
        payload = _payload_in()
        event = event_of(payload)
        emit([line_for(by_id(i)) for i in decide(payload, event) if by_id(i)], codex, event)
    except Exception:
        pass


# ---------------------------------------------------------------- tools
def payload_for(example, session="cs-selftest"):
    """A hook payload from an example string: p:<prompt>, c:<command>, w:<path>|<content>."""
    kind, body = example[:2], example[2:]
    if kind == "c:":
        return "tool", {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": body},
                        "session_id": session}
    if kind == "w:":
        path, _, content = body.partition("|")
        return "tool", {"hook_event_name": "PreToolUse", "tool_name": "Write",
                        "tool_input": {"file_path": path, "content": content}, "session_id": session}
    return "prompt", {"hook_event_name": "UserPromptSubmit", "prompt": body, "session_id": session}


def selftest():
    import shutil
    import subprocess
    import tempfile
    global STATE_DIR, CACHE, _JUDGED, _REG
    fails, n = [], 0
    hooks = registry()
    ids = [h["id"] for h in hooks]
    if len(ids) != len(set(ids)):
        fails.append("duplicate hook ids")
    for h in hooks:
        for f in ("id", "area", "title", "ask", "rule", "events", "gate"):
            if not h.get(f):
                fails.append("%s lacks %s" % (h.get("id"), f))
        if not h.get("fire") or not h.get("quiet"):
            fails.append("%s needs fire and quiet examples" % h["id"])
        if len(full_line(h)) > LINE_CAP:
            fails.append("%s line is cut at %d chars" % (h["id"], LINE_CAP))
        for ex in h.get("fire") or ():
            n += 1
            ev, p = payload_for(ex)
            if h["id"] not in [x["id"] for s, x in candidates(p, ev)]:
                fails.append("%s does not wake on its fire example %r" % (h["id"], ex[:70]))
        for ex in h.get("quiet") or ():
            n += 1
            ev, p = payload_for(ex)
            if h["id"] in [x["id"] for s, x in candidates(p, ev)]:
                fails.append("%s wakes on its quiet example %r" % (h["id"], ex[:70]))
    # controls on the engine itself, on a private state folder
    old = STATE_DIR, CACHE
    tmp = tempfile.mkdtemp(prefix="cs_selftest_")
    STATE_DIR, CACHE, _JUDGED = tmp, os.path.join(tmp, "registry_cache.json"), None
    try:
        for ex in ("c:echo hi", "p:thanks", "p:<system-reminder>are you sure? check it again, why would it</system-reminder> ok"):
            n += 1
            ev, p = payload_for(ex)
            if decide(p, ev):
                fails.append("control %r woke %s" % (ex[:50], decide(p, ev)))
        firing = next((h for h in hooks if h.get("events") in ("prompt", "both")
                       and any(e.startswith("p:") for e in h.get("fire") or ())), None)
        if firing:
            ex = next(e for e in firing["fire"] if e.startswith("p:"))
            ev, p = payload_for(ex, session="bk1")
            first = decide(p, ev)
            _DECIDED.clear()
            p2 = dict(p, prompt=p["prompt"] + " ")
            again = decide(p2, ev)
            n += 2
            if firing["id"] not in first:
                fails.append("back-off control: %s did not fire the first time" % firing["id"])
            if firing["id"] in again:
                fails.append("back-off control: %s fired twice in one session within 45 min" % firing["id"])
            for _ in range(6):
                _append("judged.jsonl", {"id": firing["id"], "v": "off"})
            _JUDGED = None
            n += 1
            if not silenced(firing["id"]):
                fails.append("judging control: six off-target judgments did not silence %s" % firing["id"])
            # the CLI path: ENABLED absent = silent; Codex output is JSON with additionalContext
            env = dict(os.environ, CS_STATE_DIR=tempfile.mkdtemp(prefix="cs_selftest_cli_"))
            _ev, p3 = payload_for(ex, session="cli1")
            for codex in (False, True):
                n += 1
                o = subprocess.run([sys.executable, os.path.abspath(__file__), "--all"] + (["--codex"] if codex else []),
                                   input=json.dumps(p3), capture_output=True, text=True, env=env, timeout=60)
                out = o.stdout.strip()
                if not os.path.exists(ENABLED):
                    if out:
                        fails.append("ENABLED is absent but --all printed")
                elif codex:
                    try:
                        if firing["id"] not in json.loads(out)["hookSpecificOutput"]["additionalContext"]:
                            raise ValueError
                    except Exception:
                        fails.append("--codex output is not the JSON Codex reads: %r" % out[:80])
                elif firing["id"] not in out:
                    fails.append("--all did not print %s" % firing["id"])
                env["CS_STATE_DIR"] = tempfile.mkdtemp(prefix="cs_selftest_cli_")
    finally:
        STATE_DIR, CACHE = old
        _JUDGED = None
        shutil.rmtree(tmp, ignore_errors=True)
    print("%d hooks, %d checks, %d failures" % (len(hooks), n, len(fails)))
    for f in fails:
        print("  FAIL " + f)
    return 1 if fails else 0


def stats():
    fires = {}
    try:
        for ln in io.open(os.path.join(STATE_DIR, "fires.jsonl"), encoding="utf-8"):
            for i in json.loads(ln).get("ids") or ():
                fires[i] = fires.get(i, 0) + 1
    except (OSError, ValueError):
        pass
    for h in registry():
        on, off = judged().get(h["id"], (0, 0))
        print("%-6s fires %4d  judged on %d off %d%s  %s" % (h["id"], fires.get(h["id"], 0), on, off,
                                                           "  QUIET" if silenced(h["id"]) else "", h["title"]))


def main(argv):
    if "--all" in argv:
        run_all("--codex" in argv)
        return 0
    if "--list" in argv:
        for h in registry():
            print("%-6s %-6s %-7s %s" % (h["id"], h.get("area", ""), h.get("events", "both"), h["title"]))
        return 0
    if "--show" in argv:
        h = by_id(argv[argv.index("--show") + 1])
        print(json.dumps(h, indent=1, ensure_ascii=True) if h else "no such hook")
        return 0 if h else 2
    if "--query" in argv:
        text = argv[argv.index("--query") + 1]
        ev, p = payload_for(("c:" if "--tool" in argv else "p:") + text)
        for s, h in sorted(candidates(p, ev), key=lambda x: -x[0]):
            print("%2d %s %s" % (s, h["id"], h["title"]))
        return 0
    if "--judge" in argv:
        i = argv.index("--judge")
        hid, v = argv[i + 1].upper(), argv[i + 2]
        if v not in ("on", "off") or not by_id(hid):
            print("usage: --judge CS003 on|off")
            return 2
        _append("judged.jsonl", {"id": hid, "v": v, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                 "note": " ".join(argv[i + 3:])[:300]})
        return 0
    if "--stats" in argv:
        stats()
        return 0
    if "--selftest" in argv:
        return selftest()
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
