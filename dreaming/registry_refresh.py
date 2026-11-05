#!/usr/bin/env python3
"""registry_refresh.py - Stop hook: keep the fleet crash-recovery registry CHECKPOINT fresh.

The recovery apparatus (~\\.claude\\fleet\\) relaunches a crashed agent using the
registry's task + checkpoint (relaunch.ps1 builds the resume prompt from them). But register.ps1 is
MANUAL - agents register at startup and rarely update - so a long-running agent's registry checkpoint
goes stale/empty, and a crash resumes it with the wrong task and no next-step. Verified 2026-07-11:
this session's registry entry carried an hours-old task and an EMPTY checkpoint. See
[[project_brain_pre_mistake_hardening_2026_07_11]] and [[fleet_recovery_apparatus_2026_07_11]].

This closes gap 1: every Stop, refresh the PER-SESSION registry entry (registry/<session_id>.json) with
a short gist of the last turn (the last-on-disk-state + next-step the resume prompt needs). Per-session
by design, so - unlike the single shared RESUME.md (gap 2) - concurrent sessions never clobber each
other's recovery state.

UPDATE-ONLY, never creates: register.ps1 owns initial registration (it knows runtime/title/task and
deliberately skips subagents/dreams/delegation workers); this only keeps an EXISTING entry fresh, so it
can never pollute the registry with non-top-level sessions. Fail-safe silent; writes a file, blocks nothing.
"""
import sys, os, io, json, re, time

HOME = os.path.expanduser("~")
DREAMING = os.path.dirname(os.path.abspath(__file__))
# test seam (mirrors resume_checkpoint's RESUME_STATE_DIR): point tests at a temp registry.
REG_DIR = os.environ.get("FLEET_REGISTRY_DIR", os.path.join(HOME, ".claude", "fleet", "registry"))


def _last_assistant(tp):
    # Scan the WHOLE transcript for the last assistant TEXT message. A fixed-size tail (the earlier 200KB,
    # then 800KB windows) MISSES the true last message on a long session: when more than a window's worth of
    # user / system-reminder / tool-result content sits AFTER it, the window returns an older message = a
    # STALE checkpoint. Reproduced 2026-07-11 on a 7MB transcript (the 800KB window returned a message 3
    # turns old). Streaming the whole file fixes it; a cheap b'"assistant"' prefilter avoids json.loads on
    # every line, and MAX_SCAN caps an extreme transcript so a Stop hook never stalls. Partial-first-line
    # drop happens only in the capped case (a mid-UTF-8 byte-cut can't then inject mojibake).
    MAX_SCAN = 80 * 1024 * 1024
    last = ""
    try:
        with open(tp, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            start = max(0, size - MAX_SCAN)
            f.seek(start)
            buf = f.read()
        if start > 0:
            nl = buf.find(b"\n")
            if nl != -1:
                buf = buf[nl + 1:]
        for ln in buf.split(b"\n"):
            if b'"assistant"' not in ln:     # cheap prefilter: skip non-assistant lines without a json parse
                continue
            try:
                obj = json.loads(ln.decode("utf-8", "replace"))
            except Exception:
                continue
            msg = obj.get("message", {}) if isinstance(obj.get("message"), dict) else {}
            if (msg.get("role") or obj.get("role") or obj.get("type")) != "assistant":
                continue
            content = msg.get("content") or obj.get("content") or ""
            if isinstance(content, str):
                txt = content
            elif isinstance(content, list):
                txt = " ".join(c.get("text", "") for c in content
                               if isinstance(c, dict) and c.get("type") == "text")
            else:
                txt = ""
            if txt.strip():
                last = txt
        return last
    except Exception:
        return ""


def _gist(text, n=300):
    """Compress the last turn to a recovery breadcrumb: strip harness XML/markdown, collapse whitespace,
    prefer to keep a trailing 'next step' sentence if present."""
    t = re.split(r"<task-notification|<system-reminder|\[SYSTEM NOTIFICATION", text or "")[0]
    t = re.sub(r"<[^>]{1,120}>", " ", t)
    t = re.sub(r"[`*#>_]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= n:
        return t
    # keep the tail (where a status/next-step usually lives), on a sentence boundary
    tail = t[-n:]
    cut = re.search(r"[.!?]\s", tail)
    return tail[cut.end():] if cut else tail


def _first_user(tp):
    """The session's FIRST real user message = its launch task, what a fresh auto-register anchors on.
    Scanned from the START; skips tool-results (role=user with no text) and harness-only blocks."""
    try:
        with io.open(tp, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                if '"user"' not in ln:
                    continue
                try:
                    obj = json.loads(ln)
                except Exception:
                    continue
                msg = obj.get("message", {}) if isinstance(obj.get("message"), dict) else {}
                if (msg.get("role") or obj.get("role") or obj.get("type")) != "user":
                    continue
                content = msg.get("content") or obj.get("content") or ""
                if isinstance(content, str):
                    txt = content
                elif isinstance(content, list):
                    txt = " ".join(c.get("text", "") for c in content
                                   if isinstance(c, dict) and c.get("type") == "text")
                else:
                    txt = ""
                stripped = re.sub(r"<task-notification.*?</task-notification>|<system-reminder.*?</system-reminder>",
                                  "", txt, flags=re.S).strip()
                if len(stripped) >= 8:
                    return txt
    except Exception:
        return ""
    return ""


def refresh(session_id, transcript_path, reg_dir=None):
    """Pure, testable: update registry/<id>.json's checkpoint+updated if it EXISTS. Returns the path
    written, or None (no entry / no session / error)."""
    reg_dir = reg_dir or REG_DIR
    if not session_id:
        return None
    f = os.path.join(reg_dir, session_id + ".json")
    if not os.path.exists(f):
        return None                      # update-only: register.ps1 owns creation
    try:
        # utf-8-SIG: register.ps1 (PowerShell) writes the registry files with a UTF-8 BOM; plain utf-8
        # json.load throws "Unexpected UTF-8 BOM" -> the old except-return-None silently no-op'd on ALL
        # 12 real files while the selftest (BOM-free) passed. utf-8-sig tolerates BOM and no-BOM alike.
        obj = json.load(io.open(f, encoding="utf-8-sig"))
    except Exception:
        return None
    gist = _gist(_last_assistant(transcript_path)) if transcript_path else ""
    if not gist:
        return None                      # nothing to record - leave the prior checkpoint intact
    obj["checkpoint"] = gist
    obj["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        # utf-8-SIG (write a BOM) to match register.ps1: PS 5.1's bare Get-Content reads a NO-BOM utf-8 file
        # as ANSI and bakes mojibake, which relaunch.ps1 then puts into the resume prompt. Verified 2026-07-11:
        # a no-BOM entry's em-dashes came back as 'Ã¢' through PS. registry_refresh READS utf-8-sig already;
        # now it WRITES it too, so the file round-trips clean through both PS and python readers.
        with io.open(f, "w", encoding="utf-8-sig") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=4)
        return f
    except Exception:
        return None


def ensure_registered(session_id, transcript_path, reg_dir=None):
    """Create registry/<id>.json if MISSING, for a top-level non-dream claude session - so crash-recovery
    coverage stops depending on a session remembering to self-register (register.ps1 is manual; -Discover
    false-skips agents that merely mention 'run_dream'). SAFE guards, verified 2026-07-11: a dream is a
    headless `claude -p` carrying $env:DREAM_TYPE (set by run_dream.ps1, propagated through Start-Process)
    -> skipped, so it can never be relaunched into a window; subagents fire SubagentStop (not Stop) and codex
    has no claude hook, so neither reaches here; and 0 bare `claude -p` workers exist. EXISTING entries stay
    with refresh() (update-only, never clobber the task). Returns the created path, or None."""
    reg_dir = reg_dir or REG_DIR
    if not session_id:
        return None
    if os.environ.get("DREAM_TYPE"):
        return None                          # a dream (headless -p) must NEVER be registered -> never a window
    f = os.path.join(reg_dir, session_id + ".json")
    if os.path.exists(f):
        return None                          # already registered - refresh() owns updates
    fu = _first_user(transcript_path) if transcript_path else ""
    fu = re.sub(r"<task-notification.*?</task-notification>|<system-reminder.*?</system-reminder>"
                r"|<local-command-caveat>.*?</local-command-caveat>|<local-command-stdout>.*?</local-command-stdout>",
                "", fu, flags=re.S)
    # slash-command launches carry the owner's actual ask in <command-args>; without this the
    # derived task was the caveat boilerplate (found 07-17: two live agents' crash-resume
    # prompts read "Caveat: The messages below were generated...")
    m = re.search(r"<command-args>(.*?)</command-args>", fu, flags=re.S)
    if m and m.group(1).strip():
        fu = m.group(1)
    task = re.sub(r"\s+", " ", re.sub(r"<[^>]{1,120}>", " ", fu)).strip()[:220]  # HEAD of the launch prompt
    if not task:
        return None                          # no launch prompt to anchor recovery on - don't create a blank
    try:
        os.makedirs(reg_dir, exist_ok=True)
        entry = {"id": session_id, "runtime": "claude", "title": session_id[:8], "task": task,
                 "cwd": HOME, "model": "", "checkpoint": "", "disabled": False, "auto": True,
                 "updated": time.strftime("%Y-%m-%dT%H:%M:%S")}
        with io.open(f, "w", encoding="utf-8-sig") as fh:
            json.dump(entry, fh, ensure_ascii=False, indent=4)
        return f
    except Exception:
        return None


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    try:
        if data.get("hook_event_name") != "Stop":
            return
        sid = data.get("session_id") or ""
        tp = data.get("transcript_path") or ""
        ensure_registered(sid, tp)   # auto-register a NEW top-level non-dream session (create-if-missing)
        refresh(sid, tp)             # keep the checkpoint fresh (existing + just-created entries)
    except Exception:
        return


def selftest():
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory() as td:
        reg = os.path.join(td, "registry")
        os.makedirs(reg)
        sid = "test-sess-123"
        # a registered entry with an EMPTY, stale checkpoint (the bug)
        pre = {"id": sid, "runtime": "claude", "title": "T", "task": "old task",
               "cwd": "C:\\x", "model": "", "checkpoint": "", "updated": "2026-07-11T20:26:24"}
        # write the entry WITH a BOM (utf-8-sig) - this is what register.ps1/PowerShell actually produces;
        # the selftest must exercise the real on-disk condition, or it flatters (it did: 12/12 real files
        # silently failed while a BOM-free selftest passed).
        json.dump(pre, io.open(os.path.join(reg, sid + ".json"), "w", encoding="utf-8-sig"))
        # a transcript ending on an assistant status message
        tp = os.path.join(td, "t.jsonl")
        with io.open(tp, "w", encoding="utf-8") as f:
            f.write(json.dumps({"message": {"role": "user", "content": "do x"}}) + "\n")
            f.write(json.dumps({"message": {"role": "assistant", "content":
                    "Done: fixed the parser and verified 5/5. **Next:** wire the registry refresh and test it."}}) + "\n")
        out = refresh(sid, tp, reg_dir=reg)
        # the written file must carry a UTF-8 BOM so PS 5.1's bare Get-Content doesn't misread non-ASCII as
        # ANSI and mojibake the resume prompt (regression for the 2026-07-11 class).
        with open(os.path.join(reg, sid + ".json"), "rb") as fb:
            if fb.read(3) != b"\xef\xbb\xbf":
                print("  FAIL: registry file has no UTF-8 BOM (PS readers will mojibake non-ASCII)"); ok = False
        got = json.load(io.open(os.path.join(reg, sid + ".json"), encoding="utf-8-sig"))
        if out is None:
            print("  FAIL: refresh returned None on an existing entry"); ok = False
        if "wire the registry refresh" not in got.get("checkpoint", ""):
            print("  FAIL: checkpoint not refreshed ->", got.get("checkpoint")[:60]); ok = False
        if got.get("task") != "old task" or got.get("title") != "T":
            print("  FAIL: preserved fields were clobbered"); ok = False
        if got.get("updated") == pre["updated"]:
            print("  FAIL: updated timestamp not advanced"); ok = False
        # update-only: a NON-registered session must NOT create an entry
        if refresh("not-registered-sess", tp, reg_dir=reg) is not None:
            print("  FAIL: created an entry for an unregistered session (pollution)"); ok = False
        if os.path.exists(os.path.join(reg, "not-registered-sess.json")):
            print("  FAIL: pollution file created"); ok = False
        # regression for the tail-staleness class: the true last assistant message buried behind >1 window
        # of trailing non-assistant content (big tool outputs / system reminders). A fixed-window scan
        # misses it; whole-file must not. Reproduced live 2026-07-11 on a 7MB transcript.
        tp2 = os.path.join(td, "big.jsonl")
        with io.open(tp2, "w", encoding="utf-8") as f:
            f.write(json.dumps({"message": {"role": "assistant", "content": "TRUE-LAST: the real next step is X."}}) + "\n")
            junk = json.dumps({"message": {"role": "user", "content": "Z" * 2000}})
            for _ in range(1200):        # ~2.5MB of trailing non-assistant content after the last assistant msg
                f.write(junk + "\n")
        got2 = _last_assistant(tp2)
        if "TRUE-LAST" not in got2:
            print("  FAIL: tail-staleness - last assistant lost behind %dMB trailing junk -> %r"
                  % (os.path.getsize(tp2) // (1024 * 1024), got2[:50])); ok = False
        # --- auto-register (ensure_registered): create-if-missing for a NEW non-dream session ---
        reg2 = os.path.join(td, "reg2"); os.makedirs(reg2)
        newsid = "beadfeed-1111-2222-3333-444455556666"
        tp3 = os.path.join(td, "new.jsonl")
        with io.open(tp3, "w", encoding="utf-8") as f:
            f.write(json.dumps({"message": {"role": "user", "content": "/goal harden the widget for 3 hours"}}) + "\n")
            f.write(json.dumps({"message": {"role": "assistant", "content": "On it. Next: build the widget."}}) + "\n")
        # 1) creates for a new session, task taken from the first user message
        c = ensure_registered(newsid, tp3, reg_dir=reg2)
        got3 = json.load(io.open(os.path.join(reg2, newsid + ".json"), encoding="utf-8-sig")) if c else {}
        if not c or "harden the widget" not in got3.get("task", ""):
            print("  FAIL: auto-register did not create with the launch task ->", got3.get("task")); ok = False
        if got3.get("auto") is not True:
            print("  FAIL: auto-registered entry not marked auto=true"); ok = False
        # 2) a DREAM (DREAM_TYPE set) must NEVER be registered - it would relaunch into a window
        os.environ["DREAM_TYPE"] = "SWS"
        try:
            if ensure_registered("dreamsid-9999", tp3, reg_dir=reg2) is not None:
                print("  FAIL: a dream (DREAM_TYPE set) got auto-registered"); ok = False
        finally:
            del os.environ["DREAM_TYPE"]
        # 3) EXISTING entry -> ensure_registered is a no-op (refresh owns updates; don't clobber the task)
        if ensure_registered(newsid, tp3, reg_dir=reg2) is not None:
            print("  FAIL: ensure_registered overwrote an existing entry"); ok = False
        # 4) no first-user text -> don't create a blank entry
        tp4 = os.path.join(td, "nouser.jsonl")
        io.open(tp4, "w", encoding="utf-8").write(json.dumps({"message": {"role": "assistant", "content": "hi"}}) + "\n")
        if ensure_registered("nouser-sid-0000", tp4, reg_dir=reg2) is not None:
            print("  FAIL: created a blank entry with no launch task"); ok = False
    print(("PASS" if ok else "FAIL") + ": registry_refresh selftest")
    return ok


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    main()
