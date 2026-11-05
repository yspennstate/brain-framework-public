#!/usr/bin/env python3
"""Propose (dry-run) a created: date for undated memories, layered by evidence reliability:
  1) originSessionId -> local transcript's FIRST-record timestamp (immutable session-start; best)
  2) a date leading the DESCRIPTION line (authoring convention: '2026-06-27 full audit ...')
  3) else leave undated (honest - don't guess from an arbitrary body date)
Prints a table with the source; --apply stamps created: (append-only frontmatter, tmp+replace)."""
import io, os, re, sys, json, time, glob
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MEM = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
PROJ = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}")
APPLY = "--apply" in sys.argv
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "CONSCIENCE", "MISTAKES", "CURIOSITY",
        "RECONSOLIDATION_QUEUE", "SALIENCE", "GROWTH", "TOPICS", "TIMELINE", "GRAPH", "BRAIN_INDEX"}
TODAY = time.strftime("%Y-%m-%d")

# sid (full + 8-prefix) -> earliest YYYY-MM-DD from the transcript's first-record timestamp.
# Sources: the live project dir + the full backup archive (14k files, session-id dirs + top-level).
ARCHIVE = os.path.expanduser(
    r"~/brain-repo/11_raw_session_transcripts/claude_projects/{{BRAIN_SLUG}}")
sid_date = {}


def _first_ts_date(path):
    try:
        n = 0
        for ln in io.open(path, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            ts = r.get("timestamp") or r.get("ts")
            if isinstance(ts, str) and ts[:4] == "2026":
                return ts[:10]
            n += 1
            if n > 40:   # session-start ts is in the head; don't scan a whole huge transcript
                break
    except Exception:
        pass
    return None


def _register(sid, d):
    if not d or d > TODAY:
        return
    # keep the EARLIEST date seen for a sid (a session can appear in several places)
    for k in (sid, sid[:8]):
        if k not in sid_date or d < sid_date[k]:
            sid_date[k] = d


sources = glob.glob(os.path.join(PROJ, "*.jsonl")) + glob.glob(os.path.join(ARCHIVE, "*.jsonl"))
for f in sources:
    b = os.path.basename(f)[:-6]
    if not re.match(r"^[0-9a-f]{8}-", b):
        continue
    _register(b, _first_ts_date(f) or time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(f))))
# session-id directories: read the inner <sid>.jsonl if present, else the dir mtime
if os.path.isdir(ARCHIVE):
    for name in os.listdir(ARCHIVE):
        if not re.match(r"^[0-9a-f]{8}-", name):
            continue
        dp = os.path.join(ARCHIVE, name)
        if os.path.isdir(dp):
            inner = os.path.join(dp, name + ".jsonl")
            d = _first_ts_date(inner) if os.path.exists(inner) else None
            _register(name, d or time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(dp))))


def frontmatter(body):
    m = re.match(r"^---\s*\n.*?\n---\s*\n", body, re.S)
    return m.group(0) if m else ""


def propose(fn, body):
    fm = frontmatter(body)
    if not fm or re.search(r"^\s*created:", fm, re.M):
        return None, None
    sid = re.search(r"originSessionId:\s*([0-9A-Za-z-]{8,})", fm)
    if sid:
        key = sid.group(1)
        d = sid_date.get(key) or sid_date.get(key[:8])
        if d and d <= TODAY:
            return d, "transcript"
    # date leading the description line
    dm = re.search(r'^description:\s*"?\W{0,4}(2026-\d\d-\d\d)\b', fm, re.M)
    if dm and dm.group(1) <= TODAY:
        return dm.group(1), "descr-date"
    return None, None


def stamp(path, body, date):
    m = re.match(r"^(---\s*\n.*?\n)(---\s*\n)", body, re.S)
    if not m or re.search(r"^\s*created:", frontmatter(body), re.M):
        return False
    out = m.group(1) + "created: " + date + "\n" + m.group(2) + body[m.end():]
    tmp = path + ".tmp"
    io.open(tmp, "w", encoding="utf-8").write(out)
    os.replace(tmp, path)
    return True


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    from collections import Counter
    src_ct = Counter()
    proposed = left = 0
    rows = []
    for fn in sorted(os.listdir(MEM)):
        if not fn.endswith(".md") or fn[:-3] in SKIP:
            continue
        if re.search(r"_(2026)_(\d\d)_(\d\d)(?:\.md|_|$)", fn):
            continue  # filename-dated: readers already parse it, skip
        p = os.path.join(MEM, fn)
        body = io.open(p, encoding="utf-8", errors="replace").read()
        if re.search(r"^\s*created:", frontmatter(body), re.M):
            continue
        d, src = propose(fn, body)
        if d:
            proposed += 1
            src_ct[src] += 1
            rows.append((fn[:-3], d, src))
            if APPLY:
                stamp(p, body, d)
        else:
            left += 1
    for slug, d, src in rows:
        print("  %-52s %s  (%s)" % (slug[:52], d, src))
    print("proposed %d created: dates (%s); %d undated left (no reliable signal)"
          % (proposed, dict(src_ct), left))
    print("APPLIED." if APPLY else "(dry-run - re-run with --apply)")


if __name__ == "__main__":
    main()
