#!/usr/bin/env python3
"""fire_judge_nightly.py - the judge pass on a cadence (built 2026-07-16, closing the
'repeatable model pass' leg of the heeding question).

Samples a handful of judgment-reflex fires from the last day (transcript-backed, UTC
windows - the 07-16 lessons baked in), renders them as a dream-agenda section with the
same blind protocol the 74-case run used, and the NIGHT DREAM judges them inline -
appending one JSON line per case to judged_nightly.jsonl. A slow series accumulates at
zero marginal cost; fire_telemetry can read its trend once it has volume.

Called by dream.py at agenda build (guarded). Standalone test: python fire_judge_nightly.py
"""
import calendar, glob, io, json, os, random, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
# the Claude project directory whose transcripts are judged: BRAIN_PROJECT_DIR wins, else the slug in
# BRAIN_PROJECT_SLUG under ~/.claude/projects (the slug is the working directory with separators replaced by dashes)
PROJ = os.environ.get("BRAIN_PROJECT_DIR") or os.path.join(
    os.path.expanduser("~"), ".claude", "projects", os.environ.get("BRAIN_PROJECT_SLUG", "default"))
OUTLOG = os.path.join(HERE, "judged_nightly.jsonl")
TARGET = ("sim_flatters", "risk_vs_measured_record", "ib_marks",
          "vxn_long_tenor_state", "stale_mark_banks")
N_CASES = 4


def _gut():
    out = {}
    try:
        with io.open(os.path.join(HERE, "instincts.tsv"), encoding="utf-8") as f:
            next(f)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 5:
                    out[p[0]] = p[4]
    except Exception:
        pass
    return out


_TS_RE = re.compile(r'"timestamp":\s*"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)')
WINDOW_PAD = 25 * 60   # seconds before the first and after the last fire of a session that a case can need


def _iso(ep):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ep))


def _spans(order, ts_index=0, sid_index=2):
    """{session: (lo_epoch, hi_epoch)} over the candidate fires, padded by WINDOW_PAD, so a session's
    transcript is parsed only around the fires the sheet can use. Transcripts run to 250 MB a day
    (2026-09-02); parsing every line of every session made a 14-case sheet take twenty minutes."""
    span = {}
    for row in order:
        ts, sid = row[ts_index], row[sid_index]
        lo, hi = span.get(sid, (ts, ts))
        span[sid] = (min(lo, ts), max(hi, ts))
    return {s: (lo - WINDOW_PAD, hi + WINDOW_PAD) for s, (lo, hi) in span.items()}


def _in_window(ln, lo_s, hi_s):
    """Cheap pre-check on the raw line: the ISO stamp sorts lexicographically, so no parse is needed."""
    if lo_s is None:
        return True
    m = _TS_RE.search(ln)
    return (m is None) or (lo_s <= m.group(1) <= hi_s)


_SCAN_CACHE = {}
SEEK_BACKOFF = 30 * 60      # the seek aims this far before a window's start, so a line stamped slightly out of order
                            # near the boundary is still read (the per-line stamp check then filters as before)


def _case_window(ts):
    """What one case uses: the record within 3 s of the fire and up to 20 min after it (the 'at' and 'after' lists).
    28 Sep: the builders scanned one window per session spanning every candidate fire of the day - for a busy session
    the whole day - and the target stratum scanned whole transcripts with no window; the builder took 178 s against the
    dream's 120 s budget and the judge section was skipped every night since 27 Sep."""
    return (ts - 120, ts + 21 * 60)


def _seek(fb, lo_s, size):
    """Byte offset at or before the first line stamped >= lo_s, by binary search over the file (lines are chronological,
    as the scan already assumes). Stops refining under 256 KB; a region with no stamp is treated as earlier."""
    lo, hi = 0, size
    while hi - lo > 262144:
        mid = (lo + hi) // 2
        fb.seek(mid)
        fb.readline()                                   # the rest of the line the seek landed in
        stamp = None
        for _ in range(200):
            ln = fb.readline()
            if not ln:
                break
            m = _TS_RE.search(ln.decode("utf-8", "replace"))
            if m:
                stamp = m.group(1)
                break
        if stamp is None or stamp < lo_s:
            lo = mid
        else:
            hi = mid
    return lo


def _session_scan(sid, window=None):
    """One pass over the session's transcript -> (tools, prompts). Transcript lines are chronological, so
    the read stops at the first stamp past the window's end, and lines before its start are skipped on the
    raw stamp without a parse. Cached per (sid, window) so the two callers share one read. Before this
    (2026-09-02) every case read its session's whole transcript twice - up to 250 MB a day - and a
    fourteen-case suppressed sheet over one hour took twenty minutes."""
    key = (sid, window)
    if key in _SCAN_CACHE:
        return _SCAN_CACHE[key]
    hits = glob.glob(os.path.join(PROJ, sid[:8] + "*.jsonl"))
    tools, prompts = [], []
    if not hits:
        _SCAN_CACHE[key] = (tools, prompts)
        return tools, prompts
    lo_s = hi_s = None
    if window:
        lo_s, hi_s = _iso(window[0]), _iso(window[1])
    try:
        with io.open(hits[0], "rb") as fb:
            if lo_s is not None:
                fb.seek(_seek(fb, _iso(window[0] - SEEK_BACKOFF), os.path.getsize(hits[0])))
                if fb.tell():
                    fb.readline()                       # start on a whole line
            for raw in fb:
                ln = raw.decode("utf-8", "replace")
                is_user = '"type":"user"' in ln or '"type": "user"' in ln
                # prompts typed while the agent works are queued_command attachments, never user records (27 Sep:
                # a quarter of the prompts, a third of the corrections - often the reaction to what a fire preceded)
                is_queued = 'queued_command' in ln
                if not is_user and not is_queued and '"tool_use"' not in ln and '"text"' not in ln:
                    continue
                if lo_s is not None:
                    m = _TS_RE.search(ln)
                    if m:
                        if m.group(1) > hi_s:
                            break
                        if m.group(1) < lo_s:
                            continue
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                try:
                    # transcript stamps are UTC (Z): timegm, never mktime (audit 010 F2)
                    ep = calendar.timegm(time.strptime(r.get("timestamp", "")[:19], "%Y-%m-%dT%H:%M:%S"))
                except Exception:
                    continue
                if r.get("type") == "assistant":
                    for b in ((r.get("message") or {}).get("content") or []):
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "tool_use":
                            try:
                                txt = json.dumps(b.get("input") or {}, ensure_ascii=False)[:1200]
                            except Exception:
                                txt = ""
                            tools.append((ep, "tool:" + b.get("name", ""), txt))
                        elif b.get("type") == "text":
                            t = (b.get("text") or "")[:900]
                            if t.strip():
                                tools.append((ep, "assistant_text", t))
                elif r.get("type") == "user" and not r.get("isMeta"):
                    c = (r.get("message") or {}).get("content")
                    if isinstance(c, str) and c.strip() and not c.lstrip().startswith("<"):
                        prompts.append((ep, "user_prompt", c[:700]))
                elif r.get("type") == "attachment" and (r.get("attachment") or {}).get("type") == "queued_command":
                    c = (r.get("attachment") or {}).get("prompt")
                    if isinstance(c, str) and c.strip() and not c.lstrip().startswith("<") \
                            and not any(abs(ep - p[0]) <= 1800 and p[2] == c[:700] for p in prompts[-20:]):
                        prompts.append((ep, "user_prompt", c[:700]))
    except Exception:
        pass
    tools.sort(key=lambda t: t[0])
    prompts.sort(key=lambda t: t[0])
    _SCAN_CACHE[key] = (tools, prompts)
    return tools, prompts


def _session_tools(sid, window=None):
    """(epoch, 'tool:<name>' | 'assistant_text', text) for the session's tool calls and assistant text."""
    return _session_scan(sid, window)[0]


def _session_prompts(sid, window=None):
    """(epoch, 'user_prompt', text) for the session's real user prompts - string content that is not a
    local command, caveat or system reminder. Prompt-time fires (UserPromptSubmit) can only be judged
    against the prompt itself; instinct_fires.tsv redacts it by design (2026-09-02: the top three firers
    of the day were 43 percent of all fires and every one of their prompt snips reads
    <exact-owner-clause-prompt-redacted>, so the log alone cannot say whether they were on-domain)."""
    return _session_scan(sid, window)[1]


FIREHOSE_N = 6          # extra cases per night from the day's top firers (all proxies, prompt included)
FIREHOSE_TOP = 8        # how many of the day's top firers (outside TARGET) the sample is drawn from
SUPPRESSED_N = 4        # cases per night from what the gates SILENCED (owner 2026-09-02 fire-less order): the
                        # falsifier - a suppressed fire judged on-domain is a fire the gate should have let through


def firehose_cases(n=FIREHOSE_N, days=1, seed=None, since=None):
    """Cases from the day's TOP FIRERS outside TARGET, across proxies (prompt/act/write/read). The
    2026-09-02 profile: 6,014 fires in a day, 43 percent at prompt time, three recognition cues
    (verify_adversarially 1,558, steelman_opposite 484, proof_step_needs_derivation 172) at the top -
    never judged, because TARGET only names five domain judgment reflexes and the act/write proxies. A reflex
    that fires on most prompts is noise unless a judge says otherwise; this stratum gets it judged.
    Deterministic by day (same seed rule as agenda_section). Never raises; [] on any trouble."""
    try:
        cutoff = float(since) if since else time.time() - days * 86400   # since: epoch seconds, e.g. to judge only fires after a trigger edit
        fires = []
        with io.open(os.path.join(HERE, "instinct_fires.tsv"), encoding="utf-8", errors="replace") as f:
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) < 6:
                    continue
                try:
                    ts = float(p[0])
                except Exception:
                    continue
                if ts < cutoff or p[3] in TARGET or not re.match(r"^[0-9a-f]{8}-", p[5]):
                    continue
                if p[2] not in ("prompt", "act", "write", "read"):
                    continue
                fires.append((ts, p[3], p[5], p[2], p[6] if len(p) > 6 else ""))   # col 6: the firing path (regex | graph | ff)
        if not fires:
            return []
        counts = {}
        for _, rid, _, _, _ in fires:
            counts[rid] = counts.get(rid, 0) + 1
        top = set(r for r, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:FIREHOSE_TOP])
        pool = [x for x in fires if x[1] in top]
        rng = random.Random(int(time.time() // 86400) if seed is None else seed)
        rng.shuffle(pool)
        # stratify: alternate proxies so a prompt-heavy day does not drown the act/write fires
        by_proxy = {}
        for x in pool:
            by_proxy.setdefault(x[3], []).append(x)
        order = []
        while len(order) < len(pool):
            for k in ("prompt", "act", "write", "read"):
                if by_proxy.get(k):
                    order.append(by_proxy[k].pop())
        guts = _gut()
        cases, seen = [], set()
        for ts, rid, sid, proxy, path in order:
            if len(cases) >= n:
                break
            if (sid, rid) in seen:
                continue
            win = _case_window(ts)
            tools, prompts = _session_tools(sid, win), _session_prompts(sid, win)
            if proxy == "prompt":
                at = [t for t in prompts if abs(t[0] - ts) <= 3][:1]
            else:
                at = [t for t in tools if abs(t[0] - ts) <= 3][:1]
            after = [t for t in tools if 3 < t[0] - ts <= 20 * 60][:4]
            if not at:
                continue
            seen.add((sid, rid))
            cases.append({"case_id": "f%s_%03d" % (time.strftime("%m%d%H%M%S"), len(cases)),   # seconds: two sheets a minute collided (09-02)
                          "fired_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),   # so a judge can split fires before/after a trigger edit
                          "stratum": "firehose", "proxy": proxy, "instinct": rid,
                          "path": path,   # regex = the reflex's own trigger; graph = an association near a scar; ff = forward feed
                          "fires_today": counts.get(rid, 0), "gut": guts.get(rid, "")[:300],
                          "at_fire": ["[%s] %s" % (k, x) for _, k, x in at],
                          "after": ["[%s] %s" % (k, x) for _, k, x in after]})
        return cases
    except Exception:
        return []


def firehose_section(n=FIREHOSE_N):
    """Markdown block for the dream agenda: judge the top-firer cases. '' when nothing to judge."""
    cases = firehose_cases(n)
    if not cases:
        return ""
    out = ["## J2. Judge these TOP-FIRER fires (firehose stratum - %d cases, ~5 min)" % len(cases),
           "",
           "These reflexes fire the most (fires_today) and have never been judged. For each case the",
           "agent had just submitted at_fire (a prompt, or a tool call) when the instinct (gut) was",
           "injected. Judge on the transcript alone - domain_at_play: is the subject this instinct",
           "guards actually at stake in at_fire (vocabulary overlap alone is NO)? followed: did conduct",
           "in `after` show the prescribed caution (yes/no/no_opportunity)? useful: would a careful",
           "agent have WANTED this injection here (yes/no)?",
           "APPEND one JSON line per case to " + OUTLOG.replace("\\", "/") + " exactly as",
           "(one object per line, each line ending in a newline; if the file's last byte is not a newline,",
           "write one first - a row glued onto the previous line hides both rows from every reader):",
           '{"case_id": "...", "instinct": "...", "stratum": "firehose", "proxy": "...", "path": "copy the case\'s path",'
           ' "fired_at": "copy the case\'s fired_at",'
           ' "domain_at_play": "yes|no|unclear", "followed": "yes|no|no_opportunity",'
           ' "useful": "yes|no", "why": "one sentence", "judged_by": "dream"}',
           "(path and fired_at are copied from the case: the precision table is kept per path, so a graph-path",
           "association can be judged separately from the reflex's own trigger.)",
           ""]
    for c in cases:
        out.append("```json")
        out.append(json.dumps(c, ensure_ascii=False))
        out.append("```")
    return "\n".join(out) + "\n"


def suppressed_cases(n=SUPPRESSED_N, days=1, seed=None):
    """Cases from what the priming hook's gates SILENCED (instinct_suppressed.tsv: ts, event, proxy,
    reflex, reason, session). The 2026-09-02 order made the hook fire less - a precision gate on
    recognition cues, a cap of two, the read-only-mention and prose-write filters, the per-session
    backoff. Each of those can eat a fire that was on-domain; this stratum puts a sample of them in front
    of the judge, gate-reasons first (precision, prose, cap, mention) and the by-design backoff last.
    A suppressed case judged domain_at_play=yes is the cost of firing less, to be weighed against the
    fires it saved. Deterministic by day. Never raises; [] on any trouble."""
    try:
        path = os.path.join(HERE, "instinct_suppressed.tsv")
        if not os.path.exists(path):
            return []
        cutoff = time.time() - days * 86400
        rows = []
        with io.open(path, encoding="utf-8", errors="replace") as f:
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) < 6:
                    continue
                try:
                    ts = float(p[0])
                except Exception:
                    continue
                if ts < cutoff or not re.match(r"^[0-9a-f]{8}-", p[5]):
                    continue
                rows.append((ts, p[3], p[5], p[2], p[4]))
        if not rows:
            return []
        rng = random.Random(int(time.time() // 86400) + 7 if seed is None else seed)
        rng.shuffle(rows)
        by_reason = {}
        for x in rows:
            by_reason.setdefault(x[4], []).append(x)
        # gate reasons first in a fixed order, then ANY other reason the hook logs (precision_graph since
        # 2026-09-02 14:0x); a reason missing from this list used to leave its rows unpopped and the loop
        # below never ended - the builder hung for twenty minutes on a one-hour sheet before it was found
        keys = [k for k in ("precision", "precision_graph", "prose", "cap", "mention", "backoff") if k in by_reason]
        keys += sorted(k for k in by_reason if k not in keys)
        order = []
        while any(by_reason.get(k) for k in keys):
            for k in keys:
                if by_reason.get(k):
                    order.append(by_reason[k].pop())
        counts = {}
        for _, rid, _, _, reason in rows:
            counts[(rid, reason)] = counts.get((rid, reason), 0) + 1
        guts = _gut()
        cases, seen = [], set()
        for ts, rid, sid, proxy, reason in order:
            if len(cases) >= n:
                break
            if (sid, rid, reason) in seen:
                continue
            win = _case_window(ts)
            tools, prompts = _session_tools(sid, win), _session_prompts(sid, win)
            if proxy == "prompt":
                at = [t for t in prompts if abs(t[0] - ts) <= 3][:1]
            else:
                at = [t for t in tools if abs(t[0] - ts) <= 3][:1]
            after = [t for t in tools if 3 < t[0] - ts <= 20 * 60][:4]
            if not at:
                continue
            seen.add((sid, rid, reason))
            cases.append({"case_id": "s%s_%03d" % (time.strftime("%m%d%H%M%S"), len(cases)),
                          "fired_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
                          "stratum": "suppressed", "proxy": proxy, "instinct": rid, "reason": reason,
                          "suppressed_today_this_reason": counts.get((rid, reason), 0),
                          "gut": guts.get(rid, "")[:300],
                          "at_fire": ["[%s] %s" % (k, x) for _, k, x in at],
                          "after": ["[%s] %s" % (k, x) for _, k, x in after]})
        return cases
    except Exception:
        return []


def suppressed_section(n=SUPPRESSED_N):
    """Markdown block for the dream agenda: judge what the gates silenced. '' when nothing to judge."""
    cases = suppressed_cases(n)
    if not cases:
        return ""
    out = ["## J3. Judge these SUPPRESSED fires (what the gates silenced - %d cases, ~3 min)" % len(cases),
           "",
           "The priming hook did NOT show these (reason: precision gate, cap of two, read-only mention",
           "filter, prose-write filter, or the per-session backoff). Judge as for the firehose - domain_at_play:",
           "was the subject this instinct guards actually at stake in at_fire? useful: would a careful agent",
           "have WANTED this injection here? A yes/yes here is a fire the gate should have let through; count",
           "them against the fires the gate saved before loosening anything. followed is judged on `after`.",
           "APPEND one JSON line per case to " + OUTLOG.replace("\\", "/") + " exactly as",
           "(one object per line, each line ending in a newline; if the file's last byte is not a newline,",
           "write one first - a row glued onto the previous line hides both rows from every reader):",
           '{"case_id": "...", "instinct": "...", "stratum": "suppressed", "reason": "...", "proxy": "...",'
           ' "fired_at": "copy the case\'s fired_at",'
           ' "domain_at_play": "yes|no|unclear", "followed": "yes|no|no_opportunity",'
           ' "useful": "yes|no", "why": "one sentence", "judged_by": "dream"}',
           ""]
    for c in cases:
        out.append("```json")
        out.append(json.dumps(c, ensure_ascii=False))
        out.append("```")
    return "\n".join(out) + "\n"


def agenda_section():
    """Markdown section with N_CASES to judge inline, or ''. Never raises."""
    try:
        cutoff = time.time() - 1 * 86400
        fires = []
        with io.open(os.path.join(HERE, "instinct_fires.tsv"),
                     encoding="utf-8", errors="replace") as f:
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) < 6:
                    continue
                try:
                    ts = float(p[0])
                except Exception:
                    continue
                if ts < cutoff or p[1] != "PreToolUse" or p[2] not in ("act", "write"):
                    continue
                if p[3] not in TARGET or not re.match(r"^[0-9a-f]{8}-", p[5]):
                    continue
                fires.append((ts, p[3], p[5], p[2], p[6] if len(p) > 6 else ""))
        if not fires:
            return ""
        # deterministic-by-day seed: same night -> same sample (a retried dream re-judges
        # the same cases instead of double-consuming the day's budget)
        rng = random.Random(int(time.time() // 86400))
        rng.shuffle(fires)
        guts = _gut()
        cases, seen_sessions = [], set()
        for ts, rid, sid, proxy, path in fires:
            if len(cases) >= N_CASES:
                break
            if sid in seen_sessions:
                continue
            tools = _session_tools(sid, _case_window(ts))
            if not tools:
                continue
            at = [t for t in tools if abs(t[0] - ts) <= 3][:1]
            after = [t for t in tools if 3 < t[0] - ts <= 20 * 60][:4]
            if not at and not after:
                continue
            seen_sessions.add(sid)
            # %H%M keeps ids unique across same-day agenda rebuilds - three builds on
            # 07-20 reissued n0720_000..003 for different samples (collision ledgered in
            # judged_nightly_case_id_collision_2026_07_16)
            cases.append({"case_id": "n%s_%03d" % (time.strftime("%m%d%H%M"), len(cases)),
                          "fired_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
                          "stratum": "target", "proxy": proxy, "path": path,   # per-path precision (09-02)
                          "instinct": rid, "gut": guts.get(rid, "")[:300],
                          "at_fire": [x for _, k, x in at],
                          "after": ["[%s] %s" % (k, x) for _, k, x in after]})
        if not cases:
            return ""
        out = ["## J. Judge these reflex fires (nightly model pass - 4 cases, ~5 min)",
               "",
               "For each case: the agent was about to run at_fire when the instinct (gut) was",
               "injected. Judge on the transcript alone - domain_at_play: is the subject matter",
               "this instinct guards actually at stake (vocabulary overlap alone is NO)?",
               "followed: did conduct in `after` show the prescribed caution (yes/no/no_opportunity)?",
               "APPEND one JSON line per case to " + OUTLOG.replace("\\", "/") + " exactly as",
           "(one object per line, each line ending in a newline; if the file's last byte is not a newline,",
           "write one first - a row glued onto the previous line hides both rows from every reader):",
               '{"case_id": "...", "instinct": "...", "stratum": "target", "path": "copy the case\'s path", "fired_at": "copy the case\'s fired_at", "domain_at_play": "yes|no|unclear",'
               ' "followed": "yes|no|no_opportunity", "why": "one sentence", "judged_by": "dream"}',
               ""]
        for c in cases:
            out.append("```json")
            out.append(json.dumps(c, ensure_ascii=False))
            out.append("```")
        return "\n".join(out) + "\n" + firehose_section() + suppressed_section()
    except Exception:
        return ""


if __name__ == "__main__":
    import argparse
    import sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--firehose", type=int, default=0, help="write N firehose cases (top firers, all proxies) as a judge sheet")
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--since", default=None, help="only fires at or after this local time, 'YYYY-MM-DD HH:MM' (e.g. to judge a rewritten trigger on fresh fires)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--sheet", default=None, help="path for the judge sheet (json); default stdout")
    ap.add_argument("--suppressed", type=int, default=0, help="write N suppressed-fire cases (what the gates silenced) as a judge sheet")
    a = ap.parse_args()
    if a.firehose or a.suppressed:
        since = time.mktime(time.strptime(a.since, "%Y-%m-%d %H:%M")) if a.since else None
        cs = (firehose_cases(a.firehose, days=a.days, seed=a.seed, since=since) if a.firehose
              else suppressed_cases(a.suppressed, days=a.days, seed=a.seed))
        txt = json.dumps(cs, ensure_ascii=False, indent=1)
        if a.sheet:
            with io.open(a.sheet, "w", encoding="utf-8") as f:
                f.write(txt)
            print("%s sheet: %d cases -> %s" % ("firehose" if a.firehose else "suppressed", len(cs), a.sheet))
        else:
            print(txt)
        sys.exit(0)
    s = agenda_section()
    print(s[:1500] if s else "(no cases in window)")
