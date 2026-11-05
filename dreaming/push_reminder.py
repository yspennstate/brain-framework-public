#!/usr/bin/env python3
"""push_reminder.py - the silent hook that tells an agent, every 30 minutes, to push its work to the right repo.

Owner order 2026-09-07 13:5x: "create a new hook where every 20 minutes agents are told to push their work,
actually make it every 30 minutes or before their summary (a silent hook i don't need to see it), and the push
should be to the correct repos to main so that it is uploaded online fully."

Registered on UserPromptSubmit and PreToolUse (Bash/PowerShell) for Claude Code and Codex. On each fire it
checks a per-session state file; when 30 minutes have passed since the session's first event or its last
reminder, it injects one short instruction as hook context - the routing table (which kind of work goes to
which repository, main branch) and the standing push discipline (commit by exact path under a mesh claim,
secret-scan the exact range, rebase on reject, never force, then verify the remote moved). Hook context is
read by the model and never shown to the owner, which is the silence he asked for. A Stop event cannot
carry context without re-prompting the model, so the "before your summary" half rides on the PreToolUse fire:
the instruction says to push before the final report, and a long turn gets the reminder on its next tool call
once the half hour is up. Fail-safe: any error -> silent exit 0.

    python push_reminder.py --selftest
"""
import io
import json
import os
import sys
import time

HOME = os.path.expanduser("~")
DREAMING = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(DREAMING, "push_reminder_state")
LOG = os.path.join(DREAMING, "push_reminder.log")
INTERVAL_S = 30 * 60
# The routing table is deployment-specific: which repository holds which kind of work. The long form
# lives in a document the deployment names (PUSH_ROUTING_DOC); the short form below is the generic
# shape, and a deployment replaces it wholesale by pointing PUSH_ROUTING_FILE at a text file.
ROUTING_DOC = os.environ.get("PUSH_ROUTING_DOC", os.path.join(HOME, "brain-repo", "REPO_ROUTING.md"))
ROUTING_FILE = os.environ.get("PUSH_ROUTING_FILE", "")

ROUTING_DEFAULT = """[push-reminder] Half an hour has passed: push the work you have finished so far, and push again before
your final report. Everything goes to the MAIN branch of the repository that already holds its kind:
- brain memories, hook and dreaming code, audits, agent-mesh records, the brain-improvement ledger and
  research campaign trees -> the brain repository (main); memories and hooks mirror into their existing
  directories there; copy the live file, byte for byte.
- math brain content and code -> the math brain repository: content travels as the store export, code in place;
  push the clone's tracked branch (promotion to main is the partner's call).
- the brain framework (sanitized: no partner names, home paths, private slugs, instrument names or domain
  vocabulary; run scripts/check_no_private_labels.py) -> the framework repository (main), and the same
  sanitized files into any downstream tree that vendors it.
- everything else -> the repository that already holds that kind of work; nothing new is created to receive it.
Discipline: claim the paths on the mesh first; commit by explicit pathspec only (never a bare `git add -A`, never
safe_push.py --message without --paths); `scan_secrets.py --range <origin>..<HEAD>` must print clean before the push;
rebase on reject, never force, never rewrite shared history; after pushing, `git ls-remote` and confirm the remote
moved. Ask before pushing anything that carries protected intellectual property or credentials. Full table: %s"""


def _routing_text():
    if ROUTING_FILE:
        try:
            with io.open(ROUTING_FILE, encoding="utf-8", errors="replace") as fh:
                text = fh.read().strip()
            if text:
                return text if "%s" in text else text + "\nFull table: %s"
        except OSError:
            pass
    return ROUTING_DEFAULT


ROUTING = _routing_text()


def log(msg):
    try:
        with io.open(LOG, "a", encoding="utf-8") as fh:
            fh.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def decide(payload, now=None, state_dir=None):
    """Return the hook output dict (possibly empty) for one event; pure apart from the state file."""
    now = time.time() if now is None else now
    state_dir = state_dir or STATE_DIR
    event = str(payload.get("hook_event_name") or "")
    if event not in ("UserPromptSubmit", "PreToolUse"):
        return {}
    session = str(payload.get("session_id") or "nosession")
    session = "".join(ch for ch in session if ch.isalnum() or ch in "-_")[:80] or "nosession"
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, session + ".json")
    st = {}
    try:
        st = json.load(io.open(path, encoding="utf-8"))
    except Exception:
        st = {}
    first = float(st.get("first_seen") or now)
    last = float(st.get("last_fired") or 0.0)
    anchor = last if last else first
    fire = (now - anchor) >= INTERVAL_S
    st.setdefault("first_seen", first)
    if fire:
        st["last_fired"] = now
        st["fires"] = int(st.get("fires") or 0) + 1
    tmp = path + ".%d.tmp" % os.getpid()
    try:
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        os.replace(tmp, path)
    except Exception:
        pass
    if not fire:
        return {}
    log("fire session=%s event=%s n=%s" % (session, event, st.get("fires")))
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": ROUTING % ROUTING_DOC}}


def selftest():
    import tempfile
    d = tempfile.mkdtemp(prefix="push_reminder_")
    t0 = 1_000_000.0
    p = {"hook_event_name": "UserPromptSubmit", "session_id": "selftest-1"}
    assert decide(p, now=t0, state_dir=d) == {}, "no fire on the first event"
    assert decide(p, now=t0 + 10 * 60, state_dir=d) == {}, "no fire at ten minutes"
    out = decide(p, now=t0 + 31 * 60, state_dir=d)
    assert "additionalContext" in out.get("hookSpecificOutput", {}), "fires after thirty minutes"
    assert decide(p, now=t0 + 32 * 60, state_dir=d) == {}, "silent again right after a fire"
    out2 = decide({"hook_event_name": "PreToolUse", "session_id": "selftest-1"}, now=t0 + 62 * 60, state_dir=d)
    assert out2.get("hookSpecificOutput", {}).get("hookEventName") == "PreToolUse", "fires again after another half hour"
    assert decide({"hook_event_name": "Stop", "session_id": "selftest-1"}, now=t0 + 99 * 60, state_dir=d) == {}, "never on Stop"
    print("selftest ok: silent for 30 min, one fire per half hour on UserPromptSubmit/PreToolUse, never on Stop")


def main():
    if "--selftest" in sys.argv:
        selftest()
        return 0
    try:
        raw = sys.stdin.read(512 * 1024)
        payload = json.loads(raw) if raw.strip() else {}
        out = decide(payload)
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=False))
    except Exception as exc:  # fail-safe: never block a session
        log("error: %r" % (exc,))
    return 0


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print("usage: push_reminder.py   (hook: reads the event JSON on stdin; help is inert)")
        raise SystemExit(0)
    sys.exit(main())
