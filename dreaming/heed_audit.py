#!/usr/bin/env python3
"""heed_audit.py - given a reflex FIRED mid-session, did behavior change? (OQ #1179's
downstream half; fired_vs_shipped.py measures the upstream miss rate.)

"Is the gut load-bearing or decorative" needs the fired->heeded join: a reflex fires at
time T in session S; does S's transcript AFTER T show the prescribed action? Most reflexes
prescribe judgment (unmeasurable without a model pass), but a MECHANICAL subset prescribes
a detectable action, so this instrument reports that subset honestly and leaves the rest
alone:

  ps1_hook_edit            -> a [Parser]::ParseFile call afterwards
  utf8_text_surgery        -> -Encoding UTF8 / python io.open(encoding=...) afterwards
  secrets_in_git           -> scan_secrets / git diff --cached inspection afterwards
  windows_backslash_path   -> a forward-slash C:/ path in a subsequent Bash command

Interpretation discipline: a pattern match proves an action of the prescribed CLASS
followed the fire - not that the agent consciously heeded; absence within the window is
fired-and-no-visible-compliance. To make the rate meaningful there is a CONTROL: the same
pattern's rate in windows starting at random tool positions in the SAME sessions (i.e.
how often that action class happens anyway). The reportable number is the LIFT
(heed rate / control rate) per reflex, in literal units. Only proxy=act/write fires from
real uuid sessions count (mention-fires would dilute both sides).

Read-only over instinct_fires.tsv + the session transcripts. Usage:
  python heed_audit.py [--days 4] [--window 8] [--minutes 20]
      [--until 2026-07-15T00:00:00] [--jsonl heed_trend.jsonl]

--until anchors the window's END in the past (fires + transcripts both persist), so a
day-by-day retrospective series is computable: --days 1 --until <midnight> per day.
--jsonl appends one machine-readable row per run so the series ACCUMULATES somewhere a
dream can read (the 07-15 wiring only piped stdout into the dream log - honest text,
unreadable series).
"""
import argparse
import io
import json
import glob
import os
import random
import re
import time
import calendar

HERE = os.path.dirname(os.path.abspath(__file__))
FIRELOG = os.path.join(HERE, "instinct_fires.tsv")
PROJ = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}")

COMPLY = {
    "ps1_hook_edit": re.compile(r"ParseFile|ascii_lint", re.I),
    "utf8_text_surgery": re.compile(r"-Encoding\s+UTF8|io\.open\([^)]*encoding|encoding=[\"']utf-?8", re.I),
    # 2026-08-14: widened - real usage inserts flags between git and diff
    # (git -C <path> diff --cached), which the old adjacent form missed on
    # every push of a 10-push night; also count the repo pre-push hook.
    "secrets_in_git": re.compile(r"scan_secrets|git\s+(?:-C\s+\S+\s+)?diff\s+--(?:cached|staged)|check_no_secrets", re.I),
    "windows_backslash_path": re.compile(r"[\"' (]C:/[A-Za-z0-9_/.-]+", ),
}


def load_fires(days, until=None):
    until = until or time.time()
    cutoff = until - days * 86400
    out = []
    with io.open(FIRELOG, encoding="utf-8", errors="replace") as f:
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if len(p) < 6:
                continue
            try:
                ts = float(p[0])
            except Exception:
                continue
            if ts < cutoff or ts > until:
                continue
            if p[1] != "PreToolUse" or p[2] not in ("act", "write"):
                continue
            rid, session = p[3], p[5]
            if rid in COMPLY and re.match(r"^[0-9a-f]{8}-", session):
                out.append((ts, rid, session))
    return out


def _iso_epoch(s):
    try:
        return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def load_session_tools(session_id):
    """Time-ordered [(epoch, tool_name, compact_text)] for one session's tool calls."""
    path = None
    for c in glob.glob(os.path.join(PROJ, session_id[:8] + "*.jsonl")):
        path = c
        break
    if not path:
        return []
    tools = []
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            if '"tool_use"' not in ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("type") != "assistant":
                continue
            ep = _iso_epoch(r.get("timestamp", "") or "")
            if ep is None:
                continue
            content = (r.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    try:
                        txt = json.dumps(b.get("input") or {}, ensure_ascii=False)[:4000]
                    except Exception:
                        txt = ""
                    tools.append((ep, b.get("name", ""), txt))
    tools.sort(key=lambda t: t[0])
    return tools


def window_after(tools, epoch, k, minutes):
    # STRICTLY after the fire (audit 010 finding 5): the old `epoch - 2` start counted the
    # triggering tool call itself, whose text often carries the compliance pattern (a
    # command already writing -Encoding UTF8 fires the reflex AND satisfies the detector) -
    # measured inflation up to 2x on secrets_in_git days. The warning is delivered on the
    # call at `epoch`; conduct it could have changed starts at the NEXT call.
    lo = [t for t in tools if t[0] > epoch + 1]
    return [t for t in lo[:k] if t[0] <= epoch + minutes * 60]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=4, help="window length in days; a fraction is fine (0.25 = six hours)")
    ap.add_argument("--window", type=int, default=8, help="tool calls examined after the fire")
    ap.add_argument("--minutes", type=int, default=20)
    ap.add_argument("--controls-per-session", type=int, default=40)
    ap.add_argument("--until", default=None,
                    help="window end: ISO (2026-07-15T00:00:00, local) or epoch; default now")
    ap.add_argument("--jsonl", default=None, metavar="PATH",
                    help="append one machine-readable result row to PATH")
    a = ap.parse_args()

    until = None
    if a.until:
        try:
            until = float(a.until)
        except ValueError:
            until = time.mktime(time.strptime(a.until[:19], "%Y-%m-%dT%H:%M:%S"))

    fires = load_fires(a.days, until)
    if not fires:
        print("heed_audit: no qualifying act/write fires in the window")
        return 0
    sessions = sorted({s for _, _, s in fires})
    cache = {}
    rng = random.Random(20260715)

    stats = {rid: {"fired": 0, "heeded": 0, "ctrl_n": 0, "ctrl_hit": 0} for rid in COMPLY}
    for sid in sessions:
        tools = cache.setdefault(sid, load_session_tools(sid))
        if not tools:
            continue
        sess_fires = [(ts, rid) for ts, rid, s in fires if s == sid]
        for ts, rid in sess_fires:
            win = window_after(tools, ts, a.window, a.minutes)
            if not win:
                continue
            stats[rid]["fired"] += 1
            pat = COMPLY[rid]
            if any(pat.search(txt) for _, _, txt in win):
                stats[rid]["heeded"] += 1
        # controls: the same patterns' base rate at random positions in this session
        for _ in range(min(a.controls_per_session, max(0, len(tools) - a.window))):
            i = rng.randrange(0, len(tools) - a.window)
            win = tools[i:i + a.window]
            for rid, pat in COMPLY.items():
                stats[rid]["ctrl_n"] += 1
                if any(pat.search(txt) for _, _, txt in win):
                    stats[rid]["ctrl_hit"] += 1

    print("heed_audit: %d act/write fires over %dd across %d sessions; window=%d calls/%dmin%s"
          % (len(fires), a.days, len(sessions), a.window, a.minutes,
             (" until=%s" % a.until) if a.until else ""))
    for rid, s in sorted(stats.items()):
        if not s["fired"]:
            print("  %-24s no qualifying fires" % rid)
            continue
        hr = s["heeded"] / s["fired"]
        cr = (s["ctrl_hit"] / s["ctrl_n"]) if s["ctrl_n"] else 0.0
        lift = (hr / cr) if cr > 0 else float("inf")
        print("  %-24s fired=%d heeded=%.0f%% control=%.0f%% lift=%.1fx"
              % (rid, s["fired"], 100 * hr, 100 * cr, lift))
    print("(pattern-class detection; judgment reflexes are out of scope by design - see docstring)")
    # 2026-08-22: these rates are for the JOINABLE SUBSET, not for fires, and the gap is large and
    # uneven. Measured that day: the fire log holds 2,476 distinct sessions and only 439 (17.7%)
    # have a transcript in the Claude project dir, so ~42% of fires are joinable at all. 44% of ALL
    # fires carry a UUIDv7 session id - Codex sessions, whose rollouts live under
    # ~/.codex/sessions/YYYY/MM/DD/ and which this loader has never read. The Codex share is uneven
    # per reflex (windows_backslash_path 81%, utf8_text_surgery 72%, ps1_hook_edit 55%), so the
    # subset differs BETWEEN reflexes and the rates are not comparable to each other. Printing this
    # every run because the numbers above read exactly like population rates and are not.
    try:
        _cov_sessions = len(sessions)
    except Exception:
        _cov_sessions = -1
    print("  [scope] rates above cover only fires joinable to a Claude transcript (%d sessions this "
          "run). Codex fires - ~44%% of the log, uneven by reflex - are NOT represented, and the "
          "per-reflex subsets differ, so these lifts are not comparable across reflexes."
          % _cov_sessions)
    if a.jsonl:
        # trend row: append-only, one line per run, so the lift series is READABLE by the
        # dreams (not just printed into a log). Stamp both run time and window end.
        try:
            row = {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "until": until or time.time(), "days": a.days,
                   "window": a.window, "minutes": a.minutes,
                   "n_fires": len(fires), "n_sessions": len(sessions),
                   # 2026-08-22: the caveat travels IN THE ROW, not only in stdout. These rows are
                   # sampled by the dreams, and a sampled row that carries rates without its
                   # denominator reads as a population rate. Additive key only - nothing existing
                   # is renamed or removed, so consumers reading known keys are unaffected.
                   "scope": ("joinable-to-Claude-transcript subset only; ~42% of fires are joinable "
                             "at all (439 of 2,476 sessions have a transcript, measured 2026-08-22); "
                             "~44% of all fires are Codex (UUIDv7 ids, rollouts under "
                             "~/.codex/sessions/) and are NOT represented; the Codex share is uneven "
                             "per reflex (windows_backslash_path 81%, utf8_text_surgery 72%, "
                             "ps1_hook_edit 55%), so these lifts are NOT comparable across reflexes"),
                   "stats": stats}
            with io.open(a.jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as e:
            print("heed_audit: jsonl append failed (nonfatal): %s" % e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
