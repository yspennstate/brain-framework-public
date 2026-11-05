"""What Codex agents touch, as the same node events the Claude capture writes (26 Sep).

The graph should hold everything agents touch; Codex does much of the picture, simulation and repository work and was
missing from the graph. This reads the Codex session rollouts (~/.codex/sessions/YYYY/MM/DD/*.jsonl) with the Claude
capture's own helpers (extract_touch_events.py), so a folder, a memory, a command or an error class is one node whoever
touched it:

  CommandExecution   tool Codex:<program>, its paths (file, dir, proj, repo, mem), programs and verbs, dials, hosts,
                     services, stores; a non-zero exit is an err node (Codex:<program>:exit<n> or its class); a
                     brain.py recall's results are recalled nodes; brain.py remember writes a memw node
  FileChange         tool Codex:apply_patch and every changed path (a memory file becomes memw)
  ImageView          tool Codex:view_image and the image's path
  McpToolCall        tool Codex:mcp:<tool>
  AgentMessage       said words (at most 12 a message, as for Claude)
  UserMessage        kw words and the user's verdicts, only in sessions a person drives (codex-tui or the desktop app, not a
                     subagent and not a codex exec worker, whose prompts another agent wrote)
  HookPrompt         the hooks that spoke, reflexes that fired, notes served, machine states (the Claude parsing)
  function_call      the collaboration tools (spawn_agent, send_message, wait_agent, update_plan ...) as Codex:<name>

A session is "x" plus the last seven hex digits of the rollout's id (Codex ids are time-ordered, so their first eight
characters, the Claude key, repeat across sessions started close together). Older rollouts that carry no item_completed
records fall back to their shell function calls. Incremental: codex_state.json keeps each rollout's byte offset and its
session. Usage: extract_codex_events.py [--days 40] [--out PATH] [--state PATH] [--max-files N]"""
import argparse
import collections
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract_touch_events as ct  # noqa: E402

ROOT = os.path.join(os.path.expanduser("~"), ".codex", "sessions")
WANT = (b'"item_completed"', b'"session_meta"', b'"turn_context"', b'"function_call"', b'"developer"')
COLLAB = {"spawn_agent", "send_message", "wait_agent", "update_plan", "list_agents", "get_goal", "update_goal",
          "followup_task", "interrupt_agent", "request_user_input_async", "js", "run", "tool_search"}


def file_path(u):
    u = u or ""
    if u.startswith("file:///"):
        u = u[8:]
    return u.replace("%20", " ")


def shell_string(cmd):
    """The command a shell ran: the -Command argument of a powershell argv, else the argv joined."""
    if isinstance(cmd, list):
        low = [str(x).lower() for x in cmd]
        for flag in ("-command", "-c"):
            if flag in low:
                i = low.index(flag)
                return " ".join(str(x) for x in cmd[i + 1:])
        return " ".join(str(x) for x in cmd)
    return str(cmd or "")


def program(cmd):
    c = re.sub(r"(?m)^\s*#.*$", "", cmd or "").strip()        # a leading comment line is not the program
    return ct.bash_cmd0(c)


def scan(path, start, st, emit, stats, end=None):
    """Read a rollout up to a pinned complete-line boundary, when supplied.

    A live writer may append while this runs. Its new records belong to the
    next capture, so scanning and the durable cursor name exactly the same bytes.
    """
    with open(path, "rb") as fh:
        fh.seek(start)
        pos = start
        while end is None or pos < end:
            raw = fh.readline() if end is None else fh.readline(end - pos)
            if not raw:
                if end is not None and pos < end:
                    raise OSError("transcript_shortened_during_scan")
                break
            pos += len(raw)
            if end is not None and not raw.endswith(b"\n"):
                raise OSError("transcript_complete_boundary_changed")
            if len(raw) > (4 << 20) or not any(w in raw for w in WANT):
                continue
            try:
                o = json.loads(raw)
            except Exception:
                stats["bad_json"] += 1
                continue
            p = o.get("payload")
            if not isinstance(p, dict):
                continue
            typ = o.get("type")
            if typ == "session_meta":
                sid = str(p.get("id") or p.get("session_id") or "").replace("-", "")
                if sid:
                    st["session"] = "x" + sid[-7:]
                st["cwd"] = file_path(p.get("cwd"))
                src = p.get("source")
                st["driven"] = (p.get("originator") in ("codex-tui", "Codex Desktop") and isinstance(src, str)
                                and src in ("cli", "vscode"))
                continue
            if typ == "turn_context":
                st["cwd"] = file_path(p.get("cwd")) or st.get("cwd", "")
                continue
            s = st.get("session")
            ts = ct.epoch(o.get("timestamp") or "")
            if not s or not ts:
                continue
            cwd = st.get("cwd", "")
            if typ == "event_msg" and p.get("type") == "item_completed":
                st["items"] = True
                it = p.get("item") or {}
                kind = it.get("type")
                if kind == "CommandExecution":
                    cmd = shell_string(it.get("command"))
                    icwd = file_path(it.get("cwd")) or cwd
                    prog = program(cmd)
                    label = "Codex:" + prog
                    emit(ts, s, "tool", label)
                    seen = set()
                    for m in ct.PATH_RX.finditer(cmd):
                        for kn in ct.path_nodes(ct.HOME + m.group(1), icwd):
                            if kn not in seen:
                                seen.add(kn)
                                emit(ts, s, *kn)
                    for kn in ct.shell_nodes(cmd, icwd):
                        if kn[1] and kn not in seen:
                            seen.add(kn)
                            emit(ts, s, *kn)
                    if "brain.py recall" in cmd or "brain.py neighbors" in cmd:
                        for slug in dict.fromkeys(ct.RECALL_RX.findall(str(it.get("stdout") or ""))):
                            emit(ts, s, "recalled", slug)
                    m = re.search(r"brain\.py\s+remember\s+([a-z0-9_]{8,})", cmd)
                    if m:
                        emit(ts, s, "memw", m.group(1))
                    code = it.get("exit_code")
                    if (isinstance(code, int) and code != 0) or it.get("status") in ("failed", "declined"):
                        txt = str(it.get("stderr") or it.get("aggregated_output") or "")[:4000]
                        cls = ct.err_class(label, txt)
                        if cls.endswith(":error") and isinstance(code, int):
                            cls = "%s:exit%d" % (label, code)
                        emit(ts, s, "err", cls)
                    stats["commands"] += 1
                elif kind == "FileChange":
                    emit(ts, s, "tool", "Codex:apply_patch")
                    seen = set()
                    for pth in (it.get("changes") or {}):
                        for k2, nid in ct.path_nodes(pth):
                            if k2 == "mem":
                                k2 = "memw"
                            if (k2, nid) not in seen:
                                seen.add((k2, nid))
                                emit(ts, s, k2, nid)
                elif kind == "ImageView":
                    emit(ts, s, "tool", "Codex:view_image")
                    for kn in ct.path_nodes(file_path(it.get("path"))):
                        emit(ts, s, *kn)
                elif kind == "McpToolCall":
                    emit(ts, s, "tool", "Codex:mcp:" + str(it.get("tool") or "tool")[:30])
                elif kind == "AgentMessage":
                    txt = " ".join(str(c.get("text") or "") for c in (it.get("content") or []) if isinstance(c, dict))
                    for w in ct.words(txt, cap=12):
                        emit(ts, s, "said", w)
                elif kind == "UserMessage" and st.get("driven"):
                    txt = " ".join(str(c.get("text") or "") for c in (it.get("content") or []) if isinstance(c, dict))
                    txt = ct.his_text({}, txt.strip())     # the Claude capture's test for the user's own words (26 Sep)
                    if txt:
                        for w in ct.words(txt, cap=40):
                            emit(ts, s, "kw", w)
                        stats["prompts"] += 1
                        head = txt[:600]
                        if len(txt) < 4000:
                            if ct.CORRECTION.search(head):
                                emit(ts, s, "verdict", "correction")
                            if ct.PRAISE.search(head):
                                emit(ts, s, "verdict", "praise")
                            if ct.REPEAT.search(head):
                                emit(ts, s, "verdict", "repeat")
                elif kind == "HookPrompt":
                    txt = "\n".join(str(f.get("text") or "") for f in (it.get("fragments") or []) if isinstance(f, dict))
                    for tag in dict.fromkeys(ct.TAG_RX.findall(txt)):
                        emit(ts, s, "hook", tag)
                    for slug in dict.fromkeys(ct.SERVED_RX.findall(txt)):
                        emit(ts, s, "served", slug)
                    if "[unconscious]" in txt:
                        for slug in dict.fromkeys(ct.SCAR_RX.findall(txt)):
                            if len(slug) >= 8:
                                emit(ts, s, "reflex", slug)
                    m = ct.STATE_RX.search(txt)
                    if m:
                        for sx in dict.fromkeys(x.strip() for x in m.group(1).split(",")):
                            if re.fullmatch(r"[a-z_]{4,40}", sx):
                                emit(ts, s, "state", sx)
                continue
            if typ == "response_item" and p.get("type") == "message" and p.get("role") == "developer":
                # the brain's hooks reach Codex as developer messages that open with their tag ([unconscious],
                # [prompts] ...); the session's opening instructions are developer messages too, but open with
                # '<' or '#' and are skipped (compacted records replay old messages and are not read at all)
                txt = "\n".join(str(c.get("text") or "") for c in (p.get("content") or []) if isinstance(c, dict))
                if not txt.lstrip().startswith("[") or len(txt) > 200000:
                    continue
                for tag in dict.fromkeys(ct.TAG_RX.findall(txt)):
                    emit(ts, s, "hook", tag)
                for slug in dict.fromkeys(ct.SERVED_RX.findall(txt)):
                    emit(ts, s, "served", slug)
                if "[unconscious]" in txt:
                    for slug in dict.fromkeys(ct.SCAR_RX.findall(txt)):
                        if len(slug) >= 8:
                            emit(ts, s, "reflex", slug)
                m = ct.STATE_RX.search(txt)
                if m:
                    for sx in dict.fromkeys(x.strip() for x in m.group(1).split(",")):
                        if re.fullmatch(r"[a-z_]{4,40}", sx):
                            emit(ts, s, "state", sx)
                if "[checklist]" in txt:
                    for m2 in ct.ASK_HOOK_RX.finditer(txt):
                        emit(ts, s, "ask", m2.group(1))
                stats["hook_messages"] += 1
                continue
            if typ == "response_item" and p.get("type") == "function_call":
                name = p.get("name") or ""
                if name in COLLAB:
                    emit(ts, s, "tool", "Codex:" + name)
                elif name in ("shell_command", "shell") and not st.get("items"):
                    try:
                        args = json.loads(p.get("arguments") or "{}")
                    except Exception:
                        args = {}
                    cmd = shell_string(args.get("command"))
                    wd = args.get("workdir") or cwd
                    emit(ts, s, "tool", "Codex:" + program(cmd))
                    seen = set()
                    for m in ct.PATH_RX.finditer(cmd):
                        for kn in ct.path_nodes(ct.HOME + m.group(1), wd):
                            if kn not in seen:
                                seen.add(kn)
                                emit(ts, s, *kn)
                    for kn in ct.shell_nodes(cmd, wd):
                        if kn[1] and kn not in seen:
                            seen.add(kn)
                            emit(ts, s, *kn)
                    stats["commands_fallback"] += 1


def main():
    ct.polite()
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=40)
    ap.add_argument("--out", default="events_codex.jsonl")
    ap.add_argument("--state", default="codex_state.json")
    ap.add_argument("--max-files", type=int, default=0)
    a = ap.parse_args()
    try:
        state = json.load(open(a.state, encoding="utf-8"))
    except Exception:
        state = {}
    # Exactly once, as extract_touch_events since 28 Sep: a rollout's events are written only after
    # it scans cleanly, the log is on disk before the state says it was read, and a run killed before its state was
    # replaced leaves a tail that the next run does not write a second time. The state entry a scan works on is a
    # copy, so a rollout that raises part way leaves its old entry as it was.
    key = os.path.normcase(os.path.abspath(a.out))
    committed = state.get(ct.COMMITTED)
    if not isinstance(committed, dict) or key not in committed:
        committed = dict(committed) if isinstance(committed, dict) else {}
        committed[key] = os.path.getsize(a.out) if os.path.exists(a.out) else 0
        state[ct.COMMITTED] = committed
        ct._save_state(a.state, state)
    pending, rec = ct.uncommitted_tail(a.out, committed.get(key))
    cutoff = time.time() - a.days * 86400
    files = [f for f in glob.glob(os.path.join(ROOT, "*", "*", "*", "*.jsonl")) if os.path.getmtime(f) >= cutoff]
    files.sort(key=os.path.getmtime)
    if a.max_files:
        files = files[-a.max_files:]
    stats, kinds = collections.Counter(), collections.Counter()
    t0 = time.time()
    buf, took = [], []

    def emit(ts, s, kind, nid):
        line = json.dumps({"ts": round(ts, 3), "session": s, "kind": kind, "id": nid})
        if pending.get(line, 0) > 0:
            pending[line] -= 1
            took.append(line)
        else:
            buf.append(line + "\n")
        kinds[kind] += 1

    with open(a.out, "a", encoding="utf-8") as out:
        for i, f in enumerate(files):
            size = os.path.getsize(f)
            st = json.loads(json.dumps(state.get(f) or {"offset": 0}))
            start = int(st.get("offset", 0))
            if start > size:
                st, start = {"offset": 0}, 0
            # The last record may still be in flight. Never advance past it,
            # even if its current bytes already happen to parse as valid JSON.
            end = ct.complete_end(f, start)
            if start == end:
                continue
            buf.clear()
            took.clear()
            try:
                scan(f, start, st, emit, stats, end=end)
            except Exception as e:
                for line in took:
                    pending[line] += 1
                stats["file_errors"] += 1
                print("  skip %s: %s" % (os.path.basename(f), type(e).__name__), file=sys.stderr)
                continue
            if buf:
                out.write("".join(buf))
            stats["tail_matched"] += len(took)
            st["offset"] = end
            state[f] = st
            stats["files"] += 1
            stats["bytes"] += end - start
            if (i + 1) % 200 == 0:
                print("  %d of %d rollouts, %.0f MB, %.0f s" % (i + 1, len(files), stats["bytes"] / 2**20,
                                                              time.time() - t0), flush=True)
        ct._sync(out)
    state[ct.COMMITTED] = dict(committed, **{key: os.path.getsize(a.out)})
    ct._save_state(a.state, state)
    tail_note = ""
    if rec["tail_lines"] or rec["torn"] or rec.get("shorter_than_committed"):
        tail_note = "; an interrupted run's tail: %s, %d of its lines not written again, %d not met again" % (
            rec, stats["tail_matched"], sum(v for v in pending.values() if v > 0))
    print("scanned %d rollouts (%.0f MB) in %.0f s; events by kind %s; commands %d (+%d from old-format calls); "
          "prompts %d; bad json %d; file errors %d%s" % (
              stats["files"], stats["bytes"] / 2**20, time.time() - t0, dict(kinds), stats["commands"],
              stats["commands_fallback"], stats["prompts"], stats["bad_json"], stats["file_errors"], tail_note))


if __name__ == "__main__":
    main()
