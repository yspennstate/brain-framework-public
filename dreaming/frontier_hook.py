"""frontier_hook.py - deliver the math brain's frontier on an open problem to the agent working it, on the agent's
own turns (PreToolUse), not only on the owner's prompts.

Why: the math hook runs on UserPromptSubmit, so research sessions driven by a goal received almost nothing (4 cards in
28 hours for the quadratic-form session, 1 in 21 for FEI; memory the_math_brain_barely_reaches_the_research_agents_
2026_09_24). T17 (preregistered, blind judge, 25 Sep) found a plan written with the program digest preferred 7 of 8
times and on target 10 against 6 (memory t17_frontier_digest_v2_passed_its_preregistered_bar_2026_09_25).

When it speaks: a session is working a program when a tool call touches the program's folder in the open-problems
repository, or when the session's own task line on the agent mesh names the program. Then frontier_digest.section()
gives the program's frontier once and, at most hourly after that, only what was added since. Otherwise silent, and
silent on any error. Standard library only; the math store and the mesh are opened read-only.
State: one small JSON per session under frontier_hook_state/ beside this file."""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
STATE_DIR = os.environ.get("FRONTIER_HOOK_STATE", os.path.join(HERE, "frontier_hook_state"))
EVERY_S = 3600          # a delta at most hourly, as frontier_digest.section() is built for
MESH_EVERY_S = 1800     # re-read the session's mesh task at most this often
STATE_TTL_S = 3 * 86400
CAP = 1 << 20
# a tool call works a program when it touches the program's folder AS A PATH (proofnet24/<folder>, either slash, JSON
# doubling included); words alone (FEI, quadratic form, even the bare folder name in a message) name it too often in
# passing - a test, a film, a report, a note about this hook - so they count only in the session's own mesh task
FOLDERS = {"FEI": re.compile(r"proofnet24[/\\]+P03-fei", re.I), "QFORM": re.compile(r"proofnet24[/\\]+QFORM-density", re.I)}
# WHERE the folder is looked for (2026-09-25): only in the parts of a call that name a place - the file a Read, Write or
# Edit opens, a search's path, a Glob pattern, a shell command, the session's cwd. The whole tool input used to be
# matched, so an Edit whose TEXT mentioned proofnet24/QFORM-density (a ledger line about this hook) got the frontier as
# if the session worked the program (16:58 on 25 Sep; the day's three other folder deliveries were real touches: an ls
# of the folder, a sparse-checkout of it, a script globbing its files). In a shell command the body of a heredoc that
# cat or tee writes to a file is text being written and is dropped; a heredoc fed to an interpreter is code and stays.
PATH_KEYS = ("file_path", "notebook_path", "path")
_HEREDOC_OPEN = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_WRITER = re.compile(r"^\s*(?:sudo\s+)?(?:cat|tee)\b")


def _drop_written_heredocs(cmd):
    """The command with the bodies of text-writing heredocs (cat > f <<EOF, cat >> f <<'EOF', tee -a f <<EOF) removed."""
    lines = cmd.split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        m = _HEREDOC_OPEN.search(line)
        segment = re.split(r"\|\|?|;|&&", line[:m.start()])[-1] if m else ""
        if m and _WRITER.match(segment):
            out.append(line)
            tag, j = m.group(2), i + 1
            while j < len(lines) and lines[j].strip() != tag:
                j += 1
            i = j + 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _touch_text(data):
    """The text in which a program's folder counts as touched: cwd, path fields, a Glob pattern, a shell command."""
    tool_input = data.get("tool_input")
    parts = [str(data.get("cwd") or "")]
    if isinstance(tool_input, dict):
        for key in PATH_KEYS:
            if isinstance(tool_input.get(key), str):
                parts.append(tool_input[key])
        if str(data.get("tool_name") or "") == "Glob" and isinstance(tool_input.get("pattern"), str):
            parts.append(tool_input["pattern"])
        if isinstance(tool_input.get("command"), str):
            parts.append(_drop_written_heredocs(tool_input["command"][:20000]))
    elif isinstance(tool_input, str):
        parts.append(tool_input[:20000])
    return " ".join(parts)


# THE RECEIVER, NOT THE SESSION (30 Sep 2026; the 1000-page report's D12-T01, second channel). A
# subagent's tool step carries its parent's session_id and its own agent_id, so the state below was shared with the
# parent: a research subagent working the program's folder got nothing for an hour after its parent had touched the
# folder, and the child that does the proof work is the one that needs the frontier. State is now kept per receiver,
# "<session8>-<agent>" for a subagent's step; the mesh task is still looked up by the session, where the mesh keeps it.
_AGENT_TRANSCRIPT = re.compile(r"/subagents/agent-([A-Za-z0-9]+)\.jsonl$")


def _receiver8(data, session8):
    """session8, or session8-<agent id> when the step is a subagent's (agent_id, or a subagents/ transcript path)."""
    agent = str(data.get("agent_id") or "")
    if not agent:
        path = str(data.get("agent_transcript_path") or data.get("transcript_path") or "").replace("\\", "/")
        m = _AGENT_TRANSCRIPT.search(path)
        agent = m.group(1) if m else ""
    agent = re.sub(r"[^A-Za-z0-9]", "", agent)[:24]
    return session8 + "-" + agent if agent else session8


def _state_path(session8):
    return os.path.join(STATE_DIR, re.sub(r"[^0-9A-Za-z_-]", "_", session8) + ".json")


def _load(session8):
    try:
        with open(_state_path(session8), encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def _save(session8, st):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        p = _state_path(session8)
        tmp = p + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, p)
    except Exception:
        pass


def _prune(now):
    """Drop other sessions' state older than STATE_TTL_S; cheap, and only when a delivery happens."""
    try:
        for name in os.listdir(STATE_DIR):
            p = os.path.join(STATE_DIR, name)
            if name.endswith(".json") and now - os.path.getmtime(p) > STATE_TTL_S:
                os.remove(p)
    except Exception:
        pass


def _log(now, session8, programs, chars, trigger):
    """One row per delivery, so the channel's reach on his real traffic can be counted later."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(os.path.join(STATE_DIR, "deliveries.tsv"), "a", encoding="utf-8") as f:
            f.write("%s\t%.0f\t%s\t%s\t%d\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)), now,
                                                  session8, ",".join(programs), chars, trigger))
    except Exception:
        pass


# THE TOP-200 ROWS (14 Oct 2026; the goal: agents do not spend
# time resolving the same things twice, and push forward through the frontier framework). A call that touches a
# folder STAFF.json assigns to a row, or the row's own folder under frontier/rows, gets that row's short brief (failure
# point, obstruction, targets, the routes not to redo, how the row is scored) once, and again only when the brief has
# changed, at most hourly. Read-only; silent on any error.
TOP200_ROOT = os.environ.get("FRONTIER_TOP200_ROOT",
                             "C:/Users/me/brain-repo/06_math_research/frontier")
_top200_cache = {"mtime": None, "rx": []}


def _top200_patterns():
    """[(row, compiled path regex)] from STAFF.json, rebuilt when the file changes."""
    p = os.path.join(TOP200_ROOT, "STAFF.json")
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return []
    if _top200_cache["mtime"] != mt:
        rx = []
        try:
            with open(p, encoding="utf-8") as f:
                staff = json.load(f)
            for row, info in staff.items():
                if not re.fullmatch(r"T\d{3}", row):
                    continue
                for folder in [os.path.join(TOP200_ROOT, "rows", row)] + list(info.get("folders", [])):
                    parts = [re.escape(x) for x in folder.replace("\\", "/").rstrip("/").split("/")[-2:]]
                    rx.append((row, re.compile(r"[/\\]+".join(parts) + r"(?![0-9A-Za-z_])", re.I)))
        except Exception:
            rx = []
        _top200_cache.update(mtime=mt, rx=rx)
    return _top200_cache["rx"]


def _top200_brief(row):
    import importlib.util
    spec = importlib.util.spec_from_file_location("frontier_top200", os.path.join(TOP200_ROOT, "frontier.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.brief_text(row, True)


def _top200_part(text, task, st, now, brief_fn=None):
    """The briefs owed to this receiver for the rows its call touched (or its mesh task names); updates st."""
    rows = sorted({row for row, rx in _top200_patterns() if rx.search(text)})
    staffed = {row for row, _ in _top200_patterns()}
    rows = sorted(set(rows) | {r for r in re.findall(r"\bT\d{3}\b", task or "") if r in staffed})
    if not rows:
        return ""
    seen = st.setdefault("top200", {})
    out = []
    for row in rows:
        last = seen.get(row) or {}
        if last and now - float(last.get("at", 0)) < EVERY_S:
            continue
        try:
            body = (brief_fn or _top200_brief)(row)
        except Exception:
            continue
        import hashlib
        h = hashlib.md5(body.encode("utf-8", "replace")).hexdigest()[:12]   # hash() is salted per process
        if last.get("hash") == h:
            seen[row] = {"at": now, "hash": h}
            continue
        seen[row] = {"at": now, "hash": h}
        if "No dossier yet" in body[:400]:
            body = body.split("\n\n")[0] + ": no dossier yet - nobody solves this row until its lead writes one."
        out.append("[frontier %s] %s" % (row, body[:6000]))
    return "\n\n".join(out)[:9000]


def decide(data, now=None, digest_mod=None):
    """Return the text to deliver ('' for nothing). Pure apart from the state file and read-only stores."""
    now = time.time() if now is None else now
    session = str(data.get("session_id") or "")
    if not re.match(r"^[0-9a-f]{8}-", session):
        return ""     # a harness or test run (no uuid session id), as math_sense.py tells them apart
    session8 = session[:8]
    receiver = _receiver8(data, session8)
    text = _touch_text(data)
    st = _load(receiver)
    try:
        top = _top200_part(text, (st.get("mesh") or {}).get("task") or "", st, now)
    except Exception:
        top = ""
    if top:
        _save(receiver, st)
        _log(now, receiver, ["top200"], len(top), "folder")
        return top
    fd = digest_mod
    if fd is None:
        import frontier_digest as fd
    touched = [p for p, rx in FOLDERS.items() if rx.search(text)]
    mesh = st.get("mesh") or {}
    if now - float(mesh.get("checked", 0)) >= MESH_EVERY_S:
        runtime = "codex" if "codex" in str(data.get("transcript_path") or "").lower() else "claude"
        mesh = {"checked": now, "task": fd.mesh_task(session8, runtime)}
        st["mesh"] = mesh
        dirty = True
    else:
        dirty = False
    named = fd.programs_of([mesh.get("task") or ""])
    programs = sorted(set(touched) | set(named))
    if not programs:
        if dirty:
            _save(receiver, st)
        return ""
    fr = st.get("frontier") or {}
    if all(p in fr and now - float(fr[p]) < EVERY_S for p in programs):
        if dirty:
            _save(receiver, st)
        return ""
    # section() reads the programs from the texts it is given; hand it exactly the programs chosen here
    words = " ".join(fd.PROGRAMS[p]["title"] for p in programs)
    out, st2 = fd.section([words], {"frontier": fr}, now=now, every_s=EVERY_S, session8=None)
    st["frontier"] = st2.get("frontier") or fr
    _save(receiver, st)
    if out:
        trigger = "+".join(t for t, hit in (("folder", set(touched) & set(programs)), ("mesh", set(named) & set(programs))) if hit)
        _log(now, receiver, programs, len(out), trigger)
        _prune(now)
    return out


def main():
    try:
        # the dispatcher runs an allowlisted hook in-process with a TEXT stdin (io.StringIO, no .buffer); read that
        # too, or the hook would fail silently in-process while its subprocess control passed
        if hasattr(sys.stdin, "buffer"):
            raw = sys.stdin.buffer.read(CAP + 1)
        else:
            raw = sys.stdin.read(CAP + 1).encode("utf-8", "replace")
        if len(raw) > CAP:
            return 0
        data = json.loads(raw.decode("utf-8", "replace"))
        if not isinstance(data, dict):
            return 0
        text = decide(data)
        if text:
            event = str(data.get("hook_event_name") or "PreToolUse")
            # ASCII JSON (15 Oct 2026): run as a Codex subprocess on Windows, stdout is the ANSI code page, so a dash
            # came out as byte 0x96 (not UTF-8; Codex drops the line) and a lambda raised and printed nothing
            sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}},
                                        ensure_ascii=True))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
