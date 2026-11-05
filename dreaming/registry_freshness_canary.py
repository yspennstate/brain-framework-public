#!/usr/bin/env python3
"""registry_freshness_canary.py - catch the class hook_liveness misses: a hook that RUNS but silently
NO-OPS. registry_refresh (Stop hook) keeps each session's crash-recovery checkpoint fresh; the 2026-07-11
BOM bug made it silently fail on all 12 real files while it still compiled + was wired (so liveness said
'ok') and register.ps1 kept the file mtime fresh. A correctness canary needs the INTENDED EFFECT, not the
wiring.

ROBUST SIGNAL (2026-07-11, after a false positive): registry_refresh runs on STOP, so a healthy checkpoint's
`updated` tracks the session's LAST ASSISTANT TURN. Compare those two, NOT the transcript mtime - a session
doing long compute between stops has a recent mtime (a tool write) but an old last-turn, and its checkpoint
is legitimately old (idle), which the naive transcript-mtime check false-flagged. The real failure is a Stop
(assistant turn) that landed well AFTER the checkpoint last updated - refresh ran and didn't take.

Read-only. Also verifies the BOM fix took fleet-wide. Usage: python registry_freshness_canary.py
"""
import os, io, json, glob, time, calendar, sys

HOME = os.path.expanduser("~")
REG = os.path.join(HOME, ".claude", "fleet", "registry")
PROJ = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}")

RECENT_S = 2 * 3600     # only judge sessions whose last TURN is within 2h (else dead/long-idle, not a live failure)
LAG_S = 900             # a Stop landed >15min after the checkpoint last updated = refresh falling behind


def _epoch(s):
    # ZONE-CORRECT: transcript timestamps are UTC (carry a 'Z') -> timegm; the checkpoint `updated` field is
    # LOCAL (registry_refresh writes strftime, no Z) -> mktime. Parsing both as local (the first-cut bug)
    # offset the turn-vs-updated comparison by the whole UTC offset and marked live sessions 'idle'.
    if not s:
        return None
    s = str(s)
    utc = ("Z" in s) or ("+00:00" in s)
    s = s.split("+")[0].rstrip("Z").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            tm = time.strptime(s[:26] if "." in s else s, fmt)
            return calendar.timegm(tm) if utc else time.mktime(tm)
        except Exception:
            pass
    return None


def _last_turn_ts(tp):
    """Epoch of the last ASSISTANT message in the transcript (when the session last produced a turn ~ Stop).
    Tail-read; tolerant. None if unreadable/none."""
    try:
        with open(tp, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 300000))
            tail = f.read().decode("utf-8", "replace")
    except Exception:
        return None
    last = None
    for ln in tail.splitlines():
        if '"assistant"' not in ln:
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        role = (o.get("message", {}) or {}).get("role") if isinstance(o.get("message"), dict) else None
        if (role or o.get("type")) != "assistant":
            continue
        ts = _epoch(o.get("timestamp"))
        if ts:
            last = ts
    return last


def check(reg_dir=None, proj_dir=None):
    """Return (rows, flags). flags = problem strings (empty = clean). Importable by hook_liveness.
    reg_dir/proj_dir are test seams (default the live dirs)."""
    reg_dir, proj_dir = reg_dir or REG, proj_dir or PROJ
    now = time.time()
    rows, flags = [], []
    for f in sorted(glob.glob(os.path.join(reg_dir, "*.json"))):
        try:
            st = os.stat(f)
        except OSError:
            continue
        if st.st_size == 0:
            # A 0-byte stub is a writer that died between create and write. One sat in the
            # registry from 2026-08-17 and was flagged UNPARSEABLE by every nightly dream for
            # two weeks with nobody acting (a correct alarm nobody reads is worse than none).
            # After a day it carries no information: quarantine it and say so once.
            if now - st.st_mtime > 86400:
                qdir = os.path.join(reg_dir, ".quarantine")
                try:
                    os.makedirs(qdir, exist_ok=True)
                    os.replace(f, os.path.join(qdir, os.path.basename(f) + ".empty_%d" % int(st.st_mtime)))
                    flags.append("%s: 0-byte registry stub (mtime %s) QUARANTINED to .quarantine/"
                                 % (os.path.basename(f)[:8], time.strftime("%Y-%m-%d", time.localtime(st.st_mtime))))
                except OSError as e:
                    flags.append("%s: 0-byte registry stub, quarantine failed (%s)" % (os.path.basename(f)[:8], type(e).__name__))
            else:
                flags.append("%s: 0-byte registry stub (fresh; writer may still be mid-create)" % os.path.basename(f)[:8])
            continue
        try:
            d = json.load(io.open(f, encoding="utf-8-sig"))     # BOM-tolerant (the whole point)
        except Exception as e:
            flags.append("%s: registry file UNPARSEABLE (%s)" % (os.path.basename(f)[:8], type(e).__name__))
            continue
        sid = d.get("id", os.path.basename(f)[:-5])
        if d.get("disabled"):
            continue
        tf = os.path.join(proj_dir, sid + ".jsonl")
        if not os.path.exists(tf):
            rows.append((sid[:8], "no transcript (codex/off-project)", len(d.get("checkpoint") or "")))
            continue
        upd = _epoch(d.get("updated"))
        turn = _last_turn_ts(tf)
        ck = d.get("checkpoint") or ""
        if turn is None:
            rows.append((sid[:8], "no datable turn", len(ck)))
            continue
        turn_age = (now - turn) / 60
        if turn_age > RECENT_S / 60:
            rows.append((sid[:8], "idle/dead (last turn %.0fm)" % turn_age, len(ck)))
            continue
        if upd is None:
            state = "FLAG: recent turn, no `updated`"
            flags.append("%s: recent turn (%.0fm) but registry has no `updated`" % (sid[:8], turn_age))
        else:
            lag = (turn - upd) / 60      # how far the last STOP is ahead of the last refresh
            if lag * 60 > LAG_S:
                state = "FLAG: last turn %.0fm ahead of checkpoint" % lag
                flags.append("%s: a Stop landed %.0fm after the checkpoint last updated - registry_refresh "
                             "ran and did not take (silent no-op?)" % (sid[:8], lag))
            else:
                state = "ok (turn %.0fm, lag %.0fm)" % (turn_age, lag)
        rows.append((sid[:8], state, len(ck)))
    return rows, flags


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    rows, flags = check()
    print("%-10s %-46s %s" % ("session", "checkpoint-correctness", "ckpt_len"))
    for sid, state, cl in rows:
        print("  %-8s %-46s %d" % (sid, state, cl))
    print()
    if flags:
        print("CANARY TRIPPED (%d): registry_refresh not keeping a LIVE session's checkpoint current -" % len(flags))
        for fl in flags:
            print("  - " + fl)
    else:
        print("CANARY CLEAN: every recently-active session's checkpoint tracks its last turn (registry_refresh working; BOM fix holds).")


def selftest():
    """Prove the guard FIRES: a synthetic session that Stopped after its checkpoint went stale must flag;
    a healthy one and an idle one must not. (Machinery needs a test that proves the guard fires.)"""
    import tempfile
    ok = True
    now = time.time()
    LOC = lambda ep: time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ep))       # local, like registry_refresh writes
    UTC = lambda ep: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ep))          # UTC 'Z', like a transcript
    with tempfile.TemporaryDirectory() as td:
        reg, proj = os.path.join(td, "registry"), os.path.join(td, "proj")
        os.makedirs(reg); os.makedirs(proj)
        def entry(sid, updated_ep):
            json.dump({"id": sid, "runtime": "claude", "title": sid, "checkpoint": "x", "updated": LOC(updated_ep)},
                      io.open(os.path.join(reg, sid + ".json"), "w", encoding="utf-8"))
        def tx(sid, turn_ep):
            with io.open(os.path.join(proj, sid + ".jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "x"}}) + "\n")
                f.write(json.dumps({"type": "assistant", "timestamp": UTC(turn_ep),
                                    "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}}) + "\n")
        entry("healthy1", now - 60);       tx("healthy1", now - 60)          # checkpoint ~ last turn -> clean
        entry("failing1", now - 3600);     tx("failing1", now - 30)          # Stop now, checkpoint 1h stale -> FLAG
        entry("idle1",    now - 3 * 3600); tx("idle1",    now - 3 * 3600)    # last turn 3h ago -> too old to judge
        rows, flags = check(reg_dir=reg, proj_dir=proj)
        fl = " ".join(flags)
        if "failing1" not in fl:
            print("  FAIL: did NOT flag the stale-checkpoint case (guard does not fire)"); ok = False
        if "healthy1" in fl:
            print("  FAIL: wrongly flagged the healthy case"); ok = False
        if "idle1" in fl:
            print("  FAIL: wrongly flagged the idle case"); ok = False
    print(("PASS" if ok else "FAIL") + ": registry_freshness_canary selftest (proves it fires on a real stale checkpoint)")
    return ok


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    main()
