"""clock_stamp_hook.py - PreToolUse: a stamp written as the time of something done cannot lie in the future.

The inner clock runs fast, and corrections to it do not stick (memory narrated_time_needs_a_clock_read_2026_07_13;
three catches in twenty minutes on 2026-09-24). A reflex can only ask; this hook knows the real clock. It reads the
text a tool call is about to write - a Write's content, an Edit's new string, a Bash or PowerShell command (heredocs,
notes, mesh bodies) - and speaks only when a time stamp's EARLIEST possible meaning is already more than SLACK_MIN
minutes ahead of the real local time: "17:4x" written at 17:22 cannot describe anything that has happened. Plans
("until", "the 19:3x dream"), other days, search patterns and talk about stamps are skipped; a UTC stamp is judged
against UTC. It never blocks.

Measured before landing (2026-09-24, a week of real tool calls replayed at each call's own time): 342 of 75,375 calls
(0.45 pct) carried such a stamp, in 44 sessions, about 50 a day; of 40 sampled at random, 38 were inner-clock errors
("Measured 2026-09-17 10:0x" at 09:29, a checklist note "20:5x:" at 20:37) and 2 were benign (a sed fixing a stamp, a
quoted peer stamp). Log: ~/.claude/compute/clock_stamp_hook.log.

    check(text, now) -> [{"stamp": "17:4x", "ahead_min": 18, "context": "..."}]
    python clock_stamp_hook.py --selftest
"""
import sys
if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)
import datetime as dt
import json
import os
import re
import sys

LOG = os.path.expanduser("~/.claude/compute/clock_stamp_hook.log")

SLACK_MIN = 5
HORIZON_MIN = 180          # a stamp dated today
HORIZON_UNDATED_MIN = 90   # an undated one further ahead than this is more often last night than a fast clock
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
X_STAMP = re.compile(r"(?<![\w/.:+-])([01]?\d|2[0-3]):([0-5])x(?![\w])", re.I)
# an exact stamp only where a record puts one: a line or header start, an opening bracket, or after a done-word
EXACT = re.compile(r"(?im)(?:^[ \t>*#-]*|\(\s*|\b(?:measured|landed|written|wrote|filed|fixed|pushed|launched|"
                   r"finished|verified|checked|corrected|repaired|re-audited|observed|recorded|noted|as of|"
                   r"clock[- ]?read)\s+(?:~\s*)?)"
                   r"([01]?\d|2[0-3]):([0-5]\d)(?![\d:])(?!\.\d)")
# an exact stamp right after a date ("1 Nov 2026 12:20 +04", "measured 1 Nov 12:20", "2026-11-01 12:20"): a dated
# time is a record wherever it sits (1 Nov: three such stamps went out 8 min fast and the EXACT form saw none)
_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
DATED = re.compile(r"(?i)(?:\b20\d\d-\d\d-\d\d|\b\d{1,2}\s+" + _MON + r"(?:\s+20\d\d)?|\b" + _MON +
                   r"\.?\s+\d{1,2}(?:,?\s+20\d\d)?)[ ,]+([01]?\d|2[0-3]):([0-5]\d)(?![\d:])(?!\.\d)")
# the pattern half of a sed substitution (s/OLD/new/) is the stamp being corrected, not a record
# (the s opens a quoted sed script, so "1.4 s/source, finishes 08:2x" is not one)
SED_PATTERN = re.compile(r"(?:['\"]|;\s*)(?:\$|\d+)?\s*s([/#|,])(?:(?!\1).)*$", re.S)
# a compact UTC run id, 20261101T0820Z: agents name runs by the time they start them
RUNID = re.compile(r"(?<!\d)(20\d\d)(\d\d)(\d\d)T([01]\d|2[0-3])([0-5]\d)Z")
# the far end of a range written right after a stamp ("09:27-09:29Z"): its zone is the range's zone, and its time is
# checked too (2 Oct: a peer report said "observed 09:27-09:29Z"; the first end was read as local time, the second
# was never looked at)
RANGE_END = re.compile(r"\s*(?:-|\u2013|\u2014|to)\s*([01]?\d|2[0-3]):([0-5])(\d|x)(?![\d:])(?![a-y])\s*(z\b|utc)?",
                       re.I)
# talk about a stamp, a search pattern, a version or bus id: not a record of when something happened
NOT_A_RECORD = re.compile(r"(?i)(stamp|grep|\brg\b|re\.compile|regex|pattern|\[clock\])[^\n]{0,30}$")
TRAILING_DATE = re.compile(r"(?i)[\s(,]*(?:20\d\d-\d\d-\d\d|\d{1,2}\s+[a-z]{3,9}(?:\s+20\d\d)?|[a-z]{3,9}\.?\s+\d{1,2}|"
                           r"\d\d-\d\d)[\s,(]*~?\s*$")
PLAN_BEFORE = re.compile(r"(?i)\b(will|next|until|till|by|before|deadline|scheduled|due|tonight|tomorrow|after|from|"
                         r"tonight'?s|wait(?:ing)?|resume[sd]?|expected|expect|planned|plan|every|each|starts?|runs?|fires?|"
                         r"window|the|at\s+the|eta|around|to)\s*[~(]?\s*$")
PLAN_CLAUSE = re.compile(r"(?i)\b(until|till|deadline|expected|expect|eta|will|scheduled|not before|due at|due by|"
                         r"estimated|projected|should (?:finish|end|land|be done))\b[^.;\n]{0,40}$")
OTHER_DAY = re.compile(r"(?i)\b(yesterday|last night|overnight|the night before|the day before)\b")
ISO_DATE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
MD_DATE = re.compile(r"(?<![\d-])(0?[1-9]|1[0-2])-(\d\d)(?![\d-])")
D_MON = re.compile(r"(?i)\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b")
MON_D = re.compile(r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b")


def _date_before(pre, today):
    """The date written just before a stamp: True if it is today, False if another day, None if none."""
    found = []
    for m in ISO_DATE.finditer(pre):
        found.append((m.end(), (int(m.group(1)), int(m.group(2)), int(m.group(3)))))
    for m in D_MON.finditer(pre):
        found.append((m.end(), (today.year, MONTHS[m.group(2).lower()[:3]], int(m.group(1)))))
    for m in MON_D.finditer(pre):
        found.append((m.end(), (today.year, MONTHS[m.group(1).lower()[:3]], int(m.group(2)))))
    for m in MD_DATE.finditer(pre):
        found.append((m.end(), (today.year, int(m.group(1)), int(m.group(2)))))
    if not found:
        return None
    y, mo, d = max(found)[1]
    return (y, mo, d) == (today.year, today.month, today.day)


def check(text, now=None, utc_now=None):
    now = now or dt.datetime.now()
    utc_now = utc_now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    out, seen = [], set()
    cands = [(m, m.group(1), int(m.group(2)) * 10, "x") for m in X_STAMP.finditer(text)]
    cands += [(m, m.group(1), int(m.group(2)), "exact") for m in EXACT.finditer(text)]
    cands += [(m, m.group(1), int(m.group(2)), "exact") for m in DATED.finditer(text)]
    for m in RUNID.finditer(text):
        if (int(m.group(1)), int(m.group(2)), int(m.group(3))) != (utc_now.year, utc_now.month, utc_now.day):
            continue
        ahead = (utc_now.replace(hour=int(m.group(4)), minute=int(m.group(5)), second=0, microsecond=0)
                 - utc_now).total_seconds() / 60.0
        if (SLACK_MIN < ahead <= HORIZON_MIN and m.start() not in seen
                and not SED_PATTERN.search(text[max(0, m.start() - 200):m.start()])):
            seen.add(m.start())
            out.append({"stamp": m.group(0), "ahead_min": int(round(ahead)), "kind": "runid",
                        "context": text[max(0, m.start() - 40):m.end() + 16].replace("\n", " / ")})
    for m, hh, mm, kind in cands:
        start = m.start(1)
        if start in seen:
            continue
        seen.add(start)
        pre = text[max(0, start - 48):start]
        post = text[m.end():m.end() + 16]
        undated_pre = TRAILING_DATE.sub("", pre)
        if (PLAN_BEFORE.search(pre[-24:]) or PLAN_BEFORE.search(undated_pre[-24:]) or PLAN_CLAUSE.search(undated_pre)
                or OTHER_DAY.search(pre + post)):
            continue
        near = text[max(0, start - 6):m.end() + 6]
        if "\\|" in near or NOT_A_RECORD.search(pre) or SED_PATTERN.search(text[max(0, start - 200):start]):
            continue
        q = text[start - 1:start] if start > 0 else ""
        if q and q in "'\"`" and text[m.end():m.end() + 1] == q:
            continue                                    # a quoted stamp is talk about a stamp, not a record
        rng = RANGE_END.match(post)
        ref = now
        if re.match(r"\s*(z\b|utc)", post, re.I) or (rng and rng.group(4)):
            ref = utc_now
        same = _date_before(pre, ref)
        if same is False:
            continue
        times = [(int(hh), mm, kind)]
        if rng:
            rx = rng.group(3).lower() == "x"
            times.append((int(rng.group(1)), int(rng.group(2)) * 10 + (0 if rx else int(rng.group(3))),
                          "x" if rx else "exact"))
        for th, tm, tk in times:
            stamp = ref.replace(hour=th, minute=tm, second=0, microsecond=0)
            ahead = (stamp - ref).total_seconds() / 60.0
            if SLACK_MIN < ahead <= (HORIZON_MIN if same else HORIZON_UNDATED_MIN):
                shown = "%02d:%s" % (th, ("%dx" % (tm // 10)) if tk == "x" else "%02d" % tm)
                ctx = (pre[-40:] + text[start:m.end()] + post).replace("\n", " / ")
                out.append({"stamp": shown, "ahead_min": int(round(ahead)), "kind": tk, "context": ctx})
                break
    return out


def _selftest():
    now = dt.datetime(2026, 9, 24, 17, 22, 8)
    utc = now - dt.timedelta(hours=3)
    cases = [
        ("\n17:4x  Dream sync, the durable half", 1), ("## LANDED 2026-09-24 17:4x: a mirror conflict", 1),
        ("## DEVIATION 2 (17:3x, after pair 1 finished", 1), ("measured 17:58 on the box", 1),
        ("\n17:2x  Brain wake flagged", 0), ("written 17:15 from the file times", 0),
        ("the 19:3x dream will push it", 0), ("First real run: 19:30.", 0), ("until 18:30 his clock", 0),
        ("his 23:4x follow-up yesterday", 0), ("## 2026-09-23 18:4x: yesterday's entry", 0),
        ("pushed 15:2x (09-23)", 0), ("OWNER ORDER 2026-09-21 20:5x, relayed", 0), ("measured 14:40Z", 1), ("measured 14:10Z", 0), ("tonight's 19:30 dream", 0), ("RUN_TIME=15:45:00", 0), ("run at 15:45 his clock", 0),
        ("clock 18:50 while the log says", 0), ("(17:5x his clock)", 1), ("24 Sep 17:5x his clock", 1),
        ("23 Sep 17:5x his clock", 0), ("expected by 17:4x", 0), ("from 17:3x to 17:5x", 0),
        # v2, from the replay's misses: a bus id, a search pattern, talk about a stamp, a plan word hidden by a date,
        # an undated stamp far ahead (more often last night); a dated-today stamp far ahead still fires
        ("GPUs 0 (17:50.0) and 3", 0), ('grep -c "17:4x\\|17:5x" LEDGER.md', 0),
        ("is stamped 2026-09-24 17:5x; the machine clock", 0), ("until the rebuild finishes (2026-09-24 17:5x)", 0),
        ("green when I promoted the slot at 19:0x and", 0), ("## CORRECTION, 2026-09-24 19:0x: the dirty", 1),
        # v3: quoting a stamp, or the hook's own line, is not a record
        ("\"[clock] '17:4x' is 20 min ahead of the real clock\"", 0), ('wrote "17:4x" into a ledger', 0),
        ("the ledger line `17:5x` was wrong", 0), ("17:4x' is the stamp he quoted", 1),
        # v4 (2 Oct): 'observed' marks a record; a range's far end carries the zone and is checked itself
        ("observed 17:58 on the laptop", 1), ("observed 17:10 on the laptop", 0),
        ("observed 2 Oct 14:35-14:39Z", 0), ("observed 14:35-14:39Z", 1), ("observed 14:05-14:10Z", 0),
        ("checked 14:10-14:35Z", 1), ("checked 14:10-14:15Z", 0), ("(17:0x-17:4x his clock)", 1),
        ("from 14:10 to 14:35Z", 0), ("window 17:30-17:45", 0),
        # v5 (1 Nov): a dated exact stamp anywhere, and a compact UTC run id
        ("Mac mini, 24 Sep 2026 17:40 +03, claude-x.", 1), ("Measured 24 Sep 17:40 by claude-x", 1),
        ("checked on 2026-09-24 17:45, all", 1), ("registered 24 Sep 17:10 before the run", 0),
        ("bets resolve by 24 Sep 17:50", 0), ("23 Sep 17:50 his clock", 0), ("at 2026-09-24T17:40:11Z", 0),
        ("runs/M01/20260924T1440Z_M01_REVIEW", 1), ("runs/M01/20260924T1410Z_M01_REVIEW", 0),
        ("runs/M01/20260923T1440Z_M01_REVIEW", 0),
        ("sed -i '' 's/24 Sep 17:40 by/24 Sep 17:12 by/' x.md", 0), ("sed 's#runs/20260924T1440Z_A#runs/B#'", 0),
        ("sed -i '' 's/24 Sep 17:12 by/24 Sep 17:40 by/' x.md", 1), ("if t < '2026-09-24T17:40'", 0),
        ("about 1.4 s/source, measured 24 Sep 17:40 on the Mac", 1), ("sed -i '' '$ s/relaunched 17:40/relaunched 17:10/'", 0),
        ("sed -i '' 's/a/b/; s/^Update 24 Sep 17:40\\./Update 24 Sep 17:10./' f", 0),
        ("sed -i '' '550s/^24 Sep 17:40 W04/24 Sep 17:10 W04/' f", 0),
    ]
    bad = 0
    for text, want in cases:
        got = len(check(text, now, utc))
        ok = (got > 0) == bool(want)
        bad += not ok
        print("  %s %-48s fired=%d want=%d" % ("PASS" if ok else "FAIL", text.strip()[:48], got, want))
    print("check: %d/%d" % (len(cases) - bad, len(cases)))
    # the hook end to end, as its own process, with stamps placed relative to the real clock
    import subprocess
    t = dt.datetime.now()
    ahead = t + dt.timedelta(minutes=20)
    past = t - dt.timedelta(minutes=30)
    fwd = "%d:%dx" % (ahead.hour, ahead.minute // 10)
    back = "%d:%dx" % (past.hour, past.minute // 10)
    runs = [({"tool_name": "Bash", "tool_input": {"command": "python checklist.py note T-1 \"%s: study done\"" % fwd}}, True),
            ({"tool_name": "Write", "tool_input": {"file_path": "x.md", "content": "\n%s  measured the queue\n" % back}}, False),
            ({"tool_name": "Edit", "tool_input": {"file_path": "x.md", "old_string": "a", "new_string": "measured %s" % fwd}}, True),
            ({"tool_name": "Read", "tool_input": {"file_path": "%s.md" % fwd}}, False),
            ({"tool_name": "Bash", "tool_input": {"command": "echo the %s dream will push it" % fwd}}, False)]
    env = dict(os.environ, CLOCK_STAMP_HOOK_NOLOG="1")
    for payload, loud_want in runs:
        r = subprocess.run([sys.executable, "-s", os.path.abspath(__file__)], input=json.dumps(payload), env=env,
                           capture_output=True, text=True, timeout=60, creationflags=0x08000000 if os.name == "nt" else 0)
        loud = bool(r.stdout.strip())
        shaped = (not loud) or ("hookSpecificOutput" in json.loads(r.stdout))
        ok = loud == loud_want and r.returncode == 0 and shaped
        bad += not ok
        print("  %s hook %-5s loud=%s want=%s rc=%d" % ("PASS" if ok else "FAIL", payload["tool_name"], loud, loud_want,
                                                        r.returncode))
    print("clock_stamp_hook selftest: %s" % ("PASS" if bad == 0 else "FAIL (%d)" % bad))
    return bad == 0


def _texts(tool, ti):
    if tool == "Write":
        return [ti.get("content") or ""]
    if tool == "Edit":
        return [ti.get("new_string") or ""]
    if tool == "MultiEdit":
        return [e.get("new_string") or "" for e in (ti.get("edits") or []) if isinstance(e, dict)]
    if tool in ("Bash", "PowerShell"):
        return [ti.get("command") or ""]
    return []


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        tool = data.get("tool_name") or ""
        texts = [t for t in _texts(tool, data.get("tool_input") or {}) if isinstance(t, str) and ":" in t]
        if not texts:
            return
        now = dt.datetime.now()
        hits = [h for t in texts for h in check(t, now)]
        if not hits:
            return
        uniq = []
        for h in hits:
            if h["stamp"] not in [u["stamp"] for u in uniq]:
                uniq.append(h)
        shown = ", ".join("'%s' is %d min ahead" % (h["stamp"], h["ahead_min"]) for h in uniq[:2])
        # the machine's own offset: this said UTC+03:00 as a constant, and the Mac runs at +04:00
        off = now.astimezone().strftime("%z") or "+0000"
        ctx = ("[clock] %s of the real clock (now %s local, UTC%s:%s). Nothing already done can be stamped in the "
               "future: the inner clock runs fast. Put the time a shell printed (date), or drop the stamp. If it names a "
               "plan or another day, ignore this." % (shown, now.strftime("%H:%M:%S"), off[:3], off[3:5]))
        if not os.environ.get("CLOCK_STAMP_HOOK_NOLOG"):
            try:
                with open(LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"at": now.strftime("%Y-%m-%d %H:%M:%S"), "session": data.get("session_id", ""),
                                        "tool": tool, "hits": [{k: h[k] for k in ("stamp", "ahead_min", "kind")}
                                                               for h in hits[:4]]}) + "\n")
            except Exception:
                pass
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": ctx}}))
    except Exception:
        return  # fail-open, always


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    main()
