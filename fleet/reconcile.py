#!/usr/bin/env python3
"""reconcile.py - refresh registry CHECKPOINTS for every registered live claude session from its transcript,
independent of whether registry_refresh is wired in that session's Stop hooks.

Closes the "pre-wiring" gap: a session that started BEFORE registry_refresh was added to settings.json does
not run it on Stop (hooks load at session start), so its registry checkpoint goes stale - a crash would then
resume it from a stale point. Verified 2026-07-11: 0c44dd79's transcript was 1min fresh while its registry
checkpoint was 25min stale. This refreshes them from OUTSIDE their hooks.

SAFE: UPDATE-ONLY. It reuses registry_refresh.refresh(), which never creates an entry and only rewrites the
checkpoint of an EXISTING (already-vetted, non-dream) entry - so it cannot register a dream/worker or spawn
a window. Only touches sessions whose transcript changed within --max-age-min (default 30) so it never churns
a dead entry. Read-only except registry checkpoint fields.

Usage: python reconcile.py [--dry] [--max-age-min N]
Intended to be run on-demand, or right before a crash-recovery (`relaunch.ps1`), or by a windowless task.
"""
import sys, os, glob, json, time

DR = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")
sys.path.insert(0, DR)
import registry_refresh as RR  # noqa: E402
PROJ = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}")


def main():
    dry = "--dry" in sys.argv
    stale_only = "--stale-only" in sys.argv   # night-watch mode: refresh ONLY genuinely-stale entries (fewer
    #                                           writes - "only medicate the sick", the healer discipline)
    max_age = 30 * 60
    if "--max-age-min" in sys.argv:
        try:
            max_age = int(sys.argv[sys.argv.index("--max-age-min") + 1]) * 60
        except Exception:
            pass
    now = time.time()
    refreshed, noop, notlive, stale_fixed = [], [], [], []
    for f in glob.glob(os.path.join(RR.REG_DIR, "*.json")):
        try:
            d = json.load(open(f, encoding="utf-8-sig"))
        except Exception:
            continue
        sid = d.get("id", "")
        tf = os.path.join(PROJ, sid + ".jsonl")
        if not os.path.exists(tf):
            continue                                   # codex / no claude transcript - out of scope
        if now - os.path.getmtime(tf) > max_age:
            notlive.append(sid[:8])
            continue                                   # not live - don't churn a dead entry
        # was the registry checkpoint stale relative to recent transcript activity?
        try:
            reg_age = now - time.mktime(time.strptime(d.get("updated", ""), "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            reg_age = 1e9
        tf_age = now - os.path.getmtime(tf)
        was_stale = reg_age - tf_age > 600             # registry >10min behind the transcript
        if dry:
            (stale_fixed if was_stale else refreshed).append(sid[:8])
            continue
        if stale_only and not was_stale:
            continue                                    # night-watch: leave fresh entries untouched
        out = RR.refresh(sid, tf)                       # update-only; refresh from the whole transcript
        if out:
            refreshed.append(sid[:8])
            if was_stale:
                stale_fixed.append(sid[:8])
        else:
            noop.append(sid[:8])                        # no new assistant text - checkpoint left intact
    tag = "DRY-RUN (no writes) " if dry else ""
    print("%sreconcile: %d live-registered claude sessions refreshed | %d had a STALE checkpoint fixed | "
          "%d no-op | %d not-live-skipped" % (tag, len(refreshed), len(stale_fixed), len(noop), len(notlive)))
    if stale_fixed:
        print("  stale checkpoints %s: %s" % ("that WOULD be fixed" if dry else "fixed", ", ".join(sorted(set(stale_fixed)))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
