#!/usr/bin/env python3
"""proof_deny_hook.py - PreToolUse: DENY a Bash command ONLY when it is mechanically PROVEN to fail.

The heeding frontier (audit 2026-07-12): a PreToolUse *warning* cannot stop the command it fires on
(the model sees it only next to the tool result, after the act) - so read-past at act time is
structurally unstoppable by more text. The one thing that stops it is a DENY, and a deny is only safe
when failure is CERTAIN. graph_fire/proof_of_failure.py carries the proof: (A) an unquoted C:\\ path in
python/node argv where the forward-slash form exists and the bash-mangled form does not (ENOENT
certain), (B) a python heredoc whose body fails compile() (SyntaxError certain).

CRITICAL: Bash-only. PowerShell does NOT mangle backslashes, so `python C:\\x\\y.py` is VALID there
(measured: 78 valid PowerShell commands vs 0 false-fires on 4860 real Bash commands). This hook returns
immediately for any non-Bash tool.

Healer-first: SHADOW (log-only, allow) unless graph_fire/PROOF_DENY_LIVE exists. Caps 3/session; the
budget persists BEFORE the deny (no persist -> no deny, so a broken disk cannot loop). Bypass with
HEED_OK=1 in the command (deliberate failure-testing). Fail-OPEN: any error -> allow (emit nothing).
Revert = rm graph_fire/PROOF_DENY_LIVE (behavioral) or unregister the hook (total).
"""
import sys, os, io, json, time, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
GF = os.path.join(HERE, "graph_fire")
LIVE = os.path.join(GF, "PROOF_DENY_LIVE")
LOG = os.path.join(GF, "proof_deny_log.tsv")
STATE = os.path.join(GF, ".proof_deny_state.json")
STATE_LOCK = STATE + ".lock"
SESSION_MAX = 3
FLEET_GAP_S = 300


def _claim(session, live):
    """Atomically reserve rate budget before a deny; false on every doubt."""
    lock_fd = None
    temp_state = None
    try:
        if not live:
            return True                       # shadow: no budget spent, always logs
        try:
            lock_fd = os.open(STATE_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(lock_fd, ("%s\n" % os.getpid()).encode("ascii"))
        except Exception:
            return False                      # contention/stale lock -> allow, never double-claim
        now = time.time()
        if os.path.exists(STATE):
            try:
                with io.open(STATE, encoding="utf-8") as f:
                    st = json.load(f)
            except Exception:
                return False                  # corrupt/unreadable state must not reset fleet caps
        else:
            st = {}
        if not isinstance(st, dict):
            return False
        sess = {k: v for k, v in st.get("sessions", {}).items()
                if isinstance(v, list) and v and now - v[-1] < 86400}
        if now - float(st.get("last_fleet_ts", 0) or 0) < FLEET_GAP_S:
            return False
        mine = sess.get(session, [])
        if len(mine) >= SESSION_MAX:
            return False
        mine.append(now)
        sess[session] = mine
        temp_state = STATE + ".%s.%s.tmp" % (os.getpid(), time.time_ns())
        with io.open(temp_state, "w", encoding="utf-8") as f:
            json.dump({"sessions": sess, "last_fleet_ts": now}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_state, STATE)
        temp_state = None
        return True
    except Exception:
        return False
    finally:
        if temp_state:
            try:
                os.remove(temp_state)
            except Exception:
                pass
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except Exception:
                pass
            try:
                os.remove(STATE_LOCK)
            except Exception:
                pass


def _predicate_id(module):
    """Bind every shadow observation to the exact predicate bytes it tested."""
    try:
        path = getattr(module, "__file__", "")
        if not path or not os.path.isfile(path) or os.path.getsize(path) > 1_000_000:
            return "unknown"
        h = hashlib.sha256()
        with io.open(path, "rb") as f:
            for chunk in iter(lambda: f.read(128 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except Exception:
        return "unknown"


def _log(session, proof, mode, cmd, predicate_id):
    try:
        snip = (cmd or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")[:110]
        with io.open(LOG, "a", encoding="utf-8", newline="") as f:
            f.write("%.0f\t%s\t%s\t%s\t%s\t%s\n" %
                    (time.time(), (session or "")[:12], mode, proof, snip, predicate_id))
            f.flush()
            os.fsync(f.fileno())
        return True
    except Exception:
        return False


def _reason(proof, corrected):
    if proof.startswith("A:"):
        base = ("[proof-deny] This command is PROVABLY going to fail: an unquoted C:\\ backslash path in "
                "a path argument (python/node). Git Bash strips the backslashes, so the command is handed a path that "
                "cannot resolve (ENOENT) - the forward-slash form exists on disk, the mangled form does "
                "not. This is the recurring agent_mesh mistake (CLAUDE.md's own command, failed 7x). ")
        if corrected:
            base += "Corrected (forward slashes): %s" % corrected[:400]
        return base + "  (force with HEED_OK=1 if you truly intend the failing form.)"
    return ("[proof-deny] This command is PROVABLY going to fail: the python heredoc body does not "
            "compile - %s. Fix the source (or write it to a .py file and run that) before re-running. "
            "(force with HEED_OK=1 to run it anyway.)" % proof.split(":", 1)[-1])


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
        if data.get("hook_event_name") != "PreToolUse":
            return
        if data.get("tool_name") != "Bash":
            return                                       # CRITICAL: Bash only; PowerShell backslashes are valid
        cmd = ((data.get("tool_input", {}) or {}).get("command", "")) or ""
        if not cmd or "HEED_OK=1" in cmd:
            return                                       # bypass for deliberate failure-testing
        sys.path.insert(0, GF)
        import proof_of_failure as pf
        predicate_id = _predicate_id(pf)
        if predicate_id == "unknown":
            return                                       # an unbound deny cannot be audited safely
        proven, corrected, proof = pf.prove_doomed(cmd, data.get("cwd"))
        if not proven:
            return
        live = os.path.exists(LIVE)
        session = data.get("session_id", "") or ""
        ok_budget = _claim(session, live)
        logged = _log(session, proof, "deny" if (live and ok_budget) else "shadow", cmd, predicate_id)
        if not live or not ok_budget or not logged:
            return                                       # shadow: measured, not felt
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": _reason(proof, corrected)[:1400]}}))
    except Exception:
        return


if __name__ == "__main__":
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import brain_switch as _bsw
        _bsw.gate()
    except SystemExit:
        raise
    except Exception:
        pass
    main()
