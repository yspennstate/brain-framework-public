# -*- coding: utf-8 -*-
"""Compaction hooks: agents compact at task boundaries, sooner, and lose nothing when they do (9 Oct 2026: an agent that has finished
a subject compacts then, compaction follows tasks rather than a full context, and it happens sooner).

Fifty brain hooks in compact_hooks.jsonl (the math brain's fifty are HOOK_IDEAS_v6.jsonl under primary_directive/
math_hooks). Each is fired by a measured signal; at most one per event (two right after a compaction); a per-session
cooldown per hook; within a kind the hook shown least recently goes first, so the fifty rotate.
- A task or subject ends: a checklist entry closed, a final report ("This was your ..."), a push, a commit, a released
  claim, a handoff sent, a report written to Downloads, a gate decision written, tests passed, a checkpoint written.
- The context is large: the live token count from the transcript's last usage record (bands 200k, 250k, 280k, 350k,
  400k, 500k), a new ask or a change of subject on a full context, a correction, an idle gap past the prompt cache.
- The context grows needlessly: a big tool result, a whole-file read, long command output, many files read, many small
  steps, repeated failures, a subagent's output, a fetched page, a background job's handle.
- Around a compaction: right after one (SessionStart source "compact"), a resume prompt, a headless stage ending.
Claude Code: run by hook_dispatch.py (plain text). Codex: run from ~/.codex/hooks.json with --codex (JSON
hookSpecificOutput, the only form Codex delivers). Every fire is logged to brain_graph/compactions/hook_fires.jsonl.
Silent on error. COMPACT_HOOKS_STATE and COMPACT_HOOKS_FIRES override the stores (tests)."""
import io
import json
import os
import re
import sys
import time

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, "compact_hooks.jsonl")
T_START = time.time()       # under hook_dispatch the module is run afresh for each hook call
STATE = os.environ.get("COMPACT_HOOKS_STATE") or os.path.join(HOME, ".claude", "compact_hooks", "state")
FIRES = os.environ.get("COMPACT_HOOKS_FIRES") or os.path.join(HOME, ".claude", "brain_graph", "compactions", "hook_fires.jsonl")
CODEX = "--codex" in sys.argv
SHELLS = ("Bash", "PowerShell", "shell", "exec_command", "local_shell")
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch")
DONE_RE = re.compile(r"(?:task_checklist_hook|checklist)\.py[\"']?\s+done\b", re.I)
REPORT_RE = re.compile(r"\(?\bThis was (?:your|his) \d{1,2}:\d{2}", re.I)
PUSH_RE = re.compile(r"\bgit\s+push\b|\bpushed\s+[0-9a-f]{7,}", re.I)
COMMIT_RE = re.compile(r"\bgit\b[^\n|;&]{0,60}\bcommit\b", re.I)
RELEASE_RE = re.compile(r"agent_mesh\.py[\"']?\s+release\b", re.I)
# 14 Oct 2026: of 143 Claude claim_released fires 9-14 Oct, 21 were `release --help` or `-h`, and a refused release
# (an argparse error, no matching claim, a pending handoff) fired it too; the gate text arm fired on a Read of this file.
RELEASE_HELP_RE = re.compile(r"\s(?:-h|--help)\b")
RELEASE_CUT_RE = re.compile(r"\\n|[|;&>)]")
RELEASE_REFUSED_RE = re.compile(r"no matching live claim was released|provide --resource or --all"
                                r"|agent_mesh\.py(?: release)?: error:|use handoff-cancel")
RELEASED_N_RE = re.compile(r"released\\*\"\s*:\s*(\d+)")
READ_TOOLS = ("Read", "Grep", "Glob", "NotebookRead")
GATE_TEXT_RE = re.compile(r"(?<![\"'])GO written")
SEND_RE = re.compile(r"agent_mesh\.py[\"']?\s+send\b|live_inbox\.py[\"']?\s+send\b|\.send\(\s*[\"']claude-", re.I)
PASSED_RE = re.compile(r"\b\d+ passed\b", re.I)
ERROR_RE = re.compile(r"Traceback \(most recent call last\)|\bError:|\bexit code [1-9]|\bFAILED\b")
CHECKPOINT_RE = re.compile(r"NEXT|CHECKPOINT", re.I)
RESUME_RE = re.compile(r"\b(?:continue|resume|pick (?:it )?up|where (?:were|was) (?:we|i))\b", re.I)
SWITCH_RE = re.compile(r"^\s*(?:also|now|actually|another|next|ok,? now|new task|and)\b", re.I)
CORRECT_RE = re.compile(r"\b(?:no,|wrong|that's not|that is not|i said|not what i)\b", re.I)
ORDER = ["compaction_node_saved", "compaction_node_missing", "codex_compacted", "after_compact", "checklist_done", "final_report", "report_written", "gate_written", "pushed", "commit_made",
         "claim_released", "message_sent", "tests_passed", "checkpoint_written", "headless_stage", "owner_correction",
         "subject_switch", "resume_prompt", "idle_gap", "new_prompt_big", "codex_big", "whole_read", "big_bash",
         "big_output", "many_files_read", "very_many_calls", "many_calls", "error_loop", "subagent", "web_fetch",
         "background_started", "lesson_before_compact", "tokens_500k", "tokens_400k", "tokens_350k", "tokens_280k",
         "tokens_250k", "tokens_200k"]


def load_hooks():
    out = []
    with io.open(HOOKS, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def tail_lines(path, nbytes=600000):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - nbytes))
            data = fh.read()
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[1:] if size > nbytes else lines


def context_facts(path):
    """(tokens in the live context, seconds since the last assistant record, last assistant text) from the transcript tail."""
    tokens, idle, text = None, None, ""
    for line in reversed(tail_lines(path)):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        # 9 Oct 18:2x: a compaction newer than the last usage record means the context is now small. Without this the first
        # prompt after a compaction read the pre-compaction usage (957k) and fired "a new ask on a full context".
        if tokens is None and rec.get("type") == "system" and rec.get("subtype") == "compact_boundary":
            tokens = int((rec.get("compactMetadata") or {}).get("postTokens") or 0)
        if tokens is None and (rec.get("type") == "compacted" or (rec.get("payload") or {}).get("type") == "compacted"):
            tokens = 0   # a Codex rollout's compaction record
        if tokens is None:
            msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
            u = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
            if u:
                tokens = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
            pay = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
            if pay.get("type") == "token_count":
                last = (pay.get("info") or {}).get("last_token_usage") or {}
                if last:
                    tokens = int(last.get("input_tokens") or 0)
        if rec.get("type") == "assistant" and idle is None:
            ts = str(rec.get("timestamp") or "")
            try:
                t0 = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")) - (time.altzone if time.daylight else time.timezone)
                idle = max(0.0, time.time() - t0)
            except ValueError:
                idle = None
            for part in (rec.get("message") or {}).get("content") or []:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "") + "\n" + text
        if tokens is not None and idle is not None and text:
            break
    return tokens, idle, text


def state_path(sid):
    os.makedirs(STATE, exist_ok=True)
    return os.path.join(STATE, re.sub(r"[^A-Za-z0-9_.-]", "_", sid or "unknown") + ".json")


def released_claim(cmd, resp_s):
    """A release that ran: some `agent_mesh.py release` call in the command is not a help call, and the result shows no
    refusal unless it also shows a claim released (a loop can refuse one resource and release the next)."""
    if not any(not RELEASE_HELP_RE.search(RELEASE_CUT_RE.split(cmd[m.end():], 1)[0]) for m in RELEASE_RE.finditer(cmd)):
        return False
    tail = resp_s[-3000:]
    return not RELEASE_REFUSED_RE.search(tail) or any(int(c) > 0 for c in RELEASED_N_RE.findall(tail))


def candidates(ev, data, tokens, idle, last_text, st):
    k = (tokens or 0) // 1000
    tool = data.get("tool_name", "") or ""
    tin = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    cmd = json.dumps(tin, ensure_ascii=False)
    fpath = str(tin.get("file_path") or tin.get("path") or "")
    resp = data.get("tool_response")
    resp_s = resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False) if resp is not None else ""
    if tool in EDIT_TOOLS and not isinstance(resp, str):
        # 9 Oct 18:2x: an Edit's hook payload carries the whole original file and the patch; the model sees one line.
        # Measured as output it fired "a large tool result" on a 46k-character file and its text could fake an error.
        resp_s = ""
    n = len(resp_s) // 1000
    kinds = set()
    if ev == "SessionStart" and data.get("source") == "compact":
        kinds.add("after_compact")
    if ev == "PostToolUse":
        st["calls"] = st.get("calls", 0) + 1
        if tool == "Read":
            st["reads"] = st.get("reads", 0) + 1
        if ERROR_RE.search(resp_s[-4000:]):
            st["errors"] = st.get("errors", 0) + 1
        shell = tool in SHELLS
        if shell and DONE_RE.search(cmd):
            kinds.add("checklist_done")
        if PUSH_RE.search(cmd) or PUSH_RE.search(resp_s[-3000:]):
            kinds.add("pushed")
        if shell and COMMIT_RE.search(cmd):
            kinds.add("commit_made")
        if shell and RELEASE_RE.search(cmd) and released_claim(cmd, resp_s):
            kinds.add("claim_released")
        if SEND_RE.search(cmd):
            kinds.add("message_sent")
        if PASSED_RE.search(resp_s[-3000:]) and "failed" not in resp_s[-3000:].lower():
            kinds.add("tests_passed")
        if tool in ("Write", "Edit") and re.search(r"[\\/]Downloads[\\/]", fpath):
            kinds.add("report_written")
        if (tool in ("Write", "Edit") and re.search(r"GO_[^\\/]*\.json$", fpath)) or (
                tool not in READ_TOOLS and GATE_TEXT_RE.search(resp_s[-3000:])):
            kinds.add("gate_written")
        if tool in ("Write", "Edit") and CHECKPOINT_RE.search(os.path.basename(fpath)):
            kinds.add("checkpoint_written")
        if tool in ("Agent", "Task"):
            kinds.add("subagent")
        if tool in ("WebFetch", "WebSearch"):
            kinds.add("web_fetch")
        if tin.get("run_in_background") or re.search(r"Start-Process|start /b", cmd, re.I):
            kinds.add("background_started")
        if tool == "Read" and n >= 20 and not tin.get("limit"):
            kinds.add("whole_read")
        elif shell and n >= 15:
            kinds.add("big_bash")
        elif n >= 20:
            kinds.add("big_output")
        if st.get("reads", 0) >= 8 and k >= 120:
            kinds.add("many_files_read")
        if st["calls"] >= 25:
            kinds.add("very_many_calls")
        elif st["calls"] >= 12 and k >= 150:
            kinds.add("many_calls")
        if st.get("errors", 0) >= 3:
            kinds.add("error_loop")
    if ev == "UserPromptSubmit":
        st["calls"], st["reads"], st["errors"] = 0, 0, 0
        prompt = (data.get("prompt") or "")[:400]
        if RESUME_RE.search(prompt):
            kinds.add("resume_prompt")
        if idle is not None and idle >= 3600 and k >= 200:
            kinds.add("idle_gap")
        if k >= 150 and CORRECT_RE.search(prompt):
            kinds.add("owner_correction")
        if k >= 150 and SWITCH_RE.search(prompt):
            kinds.add("subject_switch")
        if k >= 200 and len(prompt) >= 20:
            kinds.add("new_prompt_big")
        if CODEX and k >= 200:
            kinds.add("codex_big")
    if ev == "Stop":
        if REPORT_RE.search(last_text[-4000:]):
            kinds.add("final_report")
        if os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk"):
            kinds.add("headless_stage")
        if k >= 270:
            kinds.add("lesson_before_compact")
    for t, kind in ((500, "tokens_500k"), (400, "tokens_400k"), (350, "tokens_350k"), (280, "tokens_280k"),
                    (250, "tokens_250k"), (200, "tokens_200k")):
        if k >= t:
            kinds.add(kind)
            break
    fill = {"k": k, "n": n, "m": int((idle or 0) // 60), "c": st.get("calls", 0), "r": st.get("reads", 0),
            "e": st.get("errors", 0), "x": max(1, round(k / 100.0, 1))}
    return [x for x in ORDER if x in kinds], fill


def choose(hooks, kinds, shown, now, limit, fresh=()):
    """Kinds in priority order; a kind rests for its shortest cooldown after it fires (no nagging on repeats); within a
    kind the hook shown least recently goes first, ties in file order; spare slots go to the same kinds' next hooks.
    A kind in `fresh` does not rest: it answers an event that is new each time (a compaction), not a repeat."""
    picked = []
    for kind in kinds:
        mine = [h for h in hooks if h["kind"] == kind]
        rests = kind not in fresh
        if not mine or (rests and now - shown.get("kind:" + kind, 0) < 60 * min(h.get("cooldown_min", 30) for h in mine)):
            continue
        ready = sorted((h for h in mine if not rests or now - shown.get(h["id"], 0) >= 60 * h.get("cooldown_min", 30)),
                       key=lambda h: shown.get(h["id"], 0))
        for h in ready:
            if len(picked) < limit:
                picked.append(h)
        if len(picked) >= limit:
            break
    return picked


def _node_rows(session_id, within_s=1800):
    """Compaction nodes this session saved in the last half hour (compactions/INDEX.jsonl), newest last.
    within_s=None takes every row of the session: a full Codex UUID needs no window, and a row's "at" is the
    compaction's own time, which a long turn or a late sweep puts days before the sweep that wrote the row."""
    out = []
    quick = session_id if isinstance(session_id, str) and PLAIN_ID_RE.match(session_id) else None
    try:
        with io.open(os.path.join(os.path.dirname(FIRES), "INDEX.jsonl"), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if quick and quick not in line:
                    continue                        # another session's row: not worth parsing
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("session_id") != session_id:
                    continue
                if within_s is None:
                    out.append(r)
                    continue
                try:
                    t = time.mktime(time.strptime(r.get("at", "")[:19], "%Y-%m-%dT%H:%M:%S"))
                except ValueError:
                    continue
                if time.time() - t <= within_s:
                    out.append(r)
    except OSError:
        pass
    return out


PLAIN_ID_RE = re.compile(r"[A-Za-z0-9_.:-]+\Z")
NODE_FRESH_S = 30             # this compaction's row: written by PostCompact around this hook's start (12:07:53 -> :55)
NODE_WAITS = (2, 2, 2, 2, 2)  # up to 10 s for it on a busy box
CP52_GRACE_S = 180            # no row by then on a later event: the node really is missing (CP52)
COMPACTION_KINDS = ("compaction_node_saved", "compaction_node_missing", "after_compact")


def _named_on_disk(sp):
    try:
        got = json.load(io.open(sp, encoding="utf-8")).get("cp51_named") or []
        return [n for n in got if isinstance(n, str)]
    except (OSError, ValueError, AttributeError):
        return []


def _this_compactions_rows(sid, sp, st):
    """The node rows written for THIS compaction, newest last: the session's rows from NODE_FRESH_S before the hook
    started on that no earlier SessionStart named, polled for up to sum(NODE_WAITS) seconds.
    14 Oct 12:07:53 (claude-1789621d-r): the old half-hour window named compaction_claude_aeda7a9b_20261014T120520, a
    sibling subagent's node 153 s old, as this compaction's; this compaction's own row landed at 12:07:55. A subagent's
    compaction reaches the hook with its parent's session id and transcript, so a session's window holds its siblings'
    nodes; and in any session that compacted in the half hour before, a lost node read as saved. Run this once per
    compaction: a second SessionStart for the same compaction would find its node named and report it missing."""
    named = set(st.get("cp51_named") or [])
    for wait in (0,) + NODE_WAITS:
        if wait:
            time.sleep(wait)
            named.update(_named_on_disk(sp))      # a sibling's SessionStart may have named its own row meanwhile
        rows = [r for r in _node_rows(sid, within_s=NODE_FRESH_S + max(0.0, time.time() - T_START))
                if r.get("name") not in named]
        if rows:
            return rows
    return []


def _note_error(msg):
    """A hook says nothing on error, but it writes the error down, next to the sweep's own errors.log."""
    try:
        with io.open(os.path.join(os.path.dirname(FIRES), "errors.log"), "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "error": "compact_hooks: " + msg[:400]}) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _maybe_spawn_sweep():
    """At most every 15 minutes across all sessions: the compaction sweep in the background (pythonw, no window,
    BelowNormal). It saves what a PostCompact hook missed and every Codex compaction (the pool's workers run hookless).
    Until 14 Oct this module never imported subprocess: every spawn since 9 Oct raised NameError into the bare except
    below, so the marker moved and no sweep ever started (EVENTS held one sweep row, a hand run on 9 Oct)."""
    mark = os.path.join(os.path.dirname(FIRES), "sweep_spawned")
    if os.environ.get("COMPACT_NO_SPAWN"):
        return
    try:
        if time.time() - os.path.getmtime(mark) < 900:
            return
    except OSError:
        pass
    try:
        import subprocess  # here, not at the top: one spawn per 15 minutes; the module runs on every tool call
        os.makedirs(os.path.dirname(mark), exist_ok=True)
        with io.open(mark, "w") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%S"))
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        args = [pyw if os.path.exists(pyw) else sys.executable, "-X", "utf8", os.path.join(HERE, "compact_nodes.py"),
                "--sweep", "--days", "1"]
        base = 0x00000008 | 0x00004000 | 0x08000000   # DETACHED_PROCESS | BELOW_NORMAL | CREATE_NO_WINDOW
        err = None
        for flags in (base | 0x01000000, base):       # break away from the hook's job when it is allowed
            try:
                subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 close_fds=True, creationflags=flags)
                return
            except OSError as exc:
                err = exc
        _note_error("sweep not started: " + repr(err))
    except Exception as exc:  # noqa: BLE001
        _note_error("sweep not started: " + repr(exc))


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    except AttributeError:  # in-process under hook_dispatch the stdin is a StringIO, already text
        raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return
    ev = data.get("hook_event_name") or ""
    sid = data.get("session_id") or data.get("conversation_id") or ""
    codex_new = 0
    if CODEX and ev == "Stop" and data.get("transcript_path"):
        try:  # every Codex compaction becomes its own node at the end of the turn that contains it
            sys.path.insert(0, HERE)
            import compact_nodes
            codex_new = compact_nodes.sweep_file(data["transcript_path"]) or 0
        except Exception:
            pass
    if ev in ("Stop", "SessionStart"):
        _maybe_spawn_sweep()
    # after the spawn: the tail read of a large transcript on a busy box can use most of the Stop event's 20 s
    tokens, idle, last_text = context_facts(data.get("transcript_path") or "")
    sp = state_path(sid)
    try:
        st = json.load(io.open(sp, encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    kinds, fill = candidates(ev, data, tokens, idle, last_text, st)
    # 9 Oct 19:3x (his 19:0x ask): after a compaction, say whether it became a brain node, so a lost one is caught at once
    compact_start = ev == "SessionStart" and data.get("source") == "compact"
    fresh = COMPACTION_KINDS if compact_start else ()
    rows = None
    if compact_start and not CODEX:
        rows = _this_compactions_rows(sid, sp, st)
        if not rows:
            # 15 Oct 2026: all 554 CP52 fires since 9 Oct were false alarms - the node landed 5-20 s after the poll ended
            # (the PostCompact writer runs beside this hook and takes 15-30 s on a busy box). Holding SessionStart longer
            # would stall every resume, so the verdict waits for this session's next events instead.
            st["cp52_pending"] = time.time()
    elif not CODEX and st.get("cp52_pending"):
        try:
            since = float(st["cp52_pending"])
        except (TypeError, ValueError):
            since = 0.0
        named = set(_named_on_disk(sp)) | set(st.get("cp51_named") or [])
        rows = [r for r in _node_rows(sid, within_s=NODE_FRESH_S + max(0.0, time.time() - since))
                if r.get("name") not in named]
        if rows or time.time() - since >= CP52_GRACE_S:
            st.pop("cp52_pending", None)
            fresh = COMPACTION_KINDS
            if not rows:
                kinds = ["compaction_node_missing"] + kinds
        else:
            rows = None
    if rows:
        fill["node"] = rows[-1].get("name", "")
        if fill["node"]:
            seen = []
            for n in _named_on_disk(sp) + list(st.get("cp51_named") or []):
                if n != fill["node"] and n not in seen:
                    seen.append(n)
            st["cp51_named"] = (seen + [fill["node"]])[-40:]
        kinds = ["compaction_node_saved"] + kinds
    if CODEX and codex_new:
        # Use the same header-verified, complete UUID as the node writer.
        try:
            identity = compact_nodes._codex_identity(data["transcript_path"])
            compact_sid = identity["session_id"]
        except (OSError, ValueError):
            identity, compact_sid = {}, ""
        # 14 Oct: no time window here. The old 24 h window dropped a compaction older than a day, so CP53 named no node.
        rows = _node_rows(compact_sid, within_s=None) if compact_sid else []
        fill["node"] = rows[-1].get("name", "") if rows else "(see compactions/INDEX.jsonl)"
        # The exact manual-checkpoint command. Codex thread ids are UUIDv7: threads started within about a minute share
        # their first 8 characters, so codex-<sid8> alone cannot find the rollout.
        try:
            actor = compact_nodes._verified_pool_actor(identity) if identity else None
        except Exception:  # noqa: BLE001 - a hook never dies on its own lookup
            actor = None
        fill["tool"] = os.path.join(HERE, "codex_manual_compact.py").replace("\\", "/")
        fill["sid"] = compact_sid or "<the thread uuid at the end of your rollout file name>"
        fill["rollout"] = data["transcript_path"]
        fill["agent"] = actor or ("codex-" + compact_sid[:8] if compact_sid else "codex-<first 8 characters of that uuid>")
        kinds = ["codex_compacted"] + kinds
    shown = st.setdefault("shown", {})
    now = time.time()
    picked = choose(load_hooks(), kinds, shown, now, 2 if ev == "SessionStart" else 1, fresh=fresh)
    for h in picked:
        shown[h["id"]] = now
        shown["kind:" + h["kind"]] = now
    tmp = "%s.%d.tmp" % (sp, os.getpid())
    try:
        with io.open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(st))
        os.replace(tmp, sp)
    except OSError as exc:   # a sibling's hook holding the file (subagents share the parent's state): say it anyway
        _note_error("state not written: " + repr(exc))
        try:
            os.remove(tmp)
        except OSError:
            pass
    if not picked:
        return
    lines = []
    for h in picked:
        try:
            body = h["text"].format(**fill)
        except (KeyError, IndexError, ValueError):
            body = h["text"]
        lines.append("[compact %s | %s] %s" % (h["id"], h["title"], body))
    try:
        os.makedirs(os.path.dirname(FIRES), exist_ok=True)
        with io.open(FIRES, "a", encoding="utf-8", newline="\n") as fh:
            for h in picked:
                fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "runtime": "codex" if CODEX else "claude",
                                     "session_id": sid, "event": ev, "hook": h["id"], "kind": h["kind"], "tokens": tokens}) + "\n")
    except OSError:
        pass
    text = "\n".join(lines)
    if CODEX:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": ev or "UserPromptSubmit", "additionalContext": text}}))
    else:
        print(text)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
