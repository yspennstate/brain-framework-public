"""backfill_history.py - six months of agent work into the brain graph (26 Sep, claude-825b1305).

His order of 26 Sep 15:5x: "if you can find data which will allow you to build the connection in this brain going back
over the past 6 months, because we were supposed to be saving that data for months, then implement it." The live
capture reads the last 40 days of Claude transcripts (Claude Code deleted older ones until cleanupPeriodDays was raised
on 16 Sep). What survives from before is the repository mirror, 11_raw_session_transcripts/ (one snapshot, 7 July):

  claude_projects/   4,043 session transcripts (April to 7 July), some gzipped or split to pass GitHub's 100 MB limit
  codex_sessions/    Codex rollouts (April to 7 July); ~/.codex/sessions still holds July to now

This runs the capture's own parsers over them - extract_touch_events.scan_file for Claude, extract_codex_events.scan for
Codex - so every node is named exactly as the live capture names it, and writes:

  events_history_claude.jsonl   Claude, the mirror (static: nothing after 7 July is in it)
  events_codex_all.jsonl        Codex, the mirror plus every local rollout the mirror does not hold; the hourly capture
                                keeps appending to it (codex_all_state.json holds each local rollout's offset)
graph_store.build reads them with events_touch.jsonl (run_graph.py EVENT_FILES). Runs BelowNormal, out of power
throttling, no window, no child process; resumable (a finished file is skipped on a rerun).

    python backfill_history.py claude|codex|all [--max-files N]
"""
import argparse
import collections
import glob
import gzip
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_touch_events as X  # noqa: E402

HOME = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph")
MIRROR = os.path.join(os.path.expanduser("~"), "brain-repo", "11_raw_session_transcripts")
OUT_CLAUDE = os.path.join(HOME, "events_history_claude.jsonl")
OUT_CODEX = os.path.join(HOME, "events_codex_all.jsonl")
STATE_CLAUDE = os.path.join(HOME, "history_claude_state.json")
STATE_CODEX = os.path.join(HOME, "codex_all_state.json")
CODEX_LOCAL = os.path.join(os.path.expanduser("~"), ".codex", "sessions")
OUT_VERDICTS = os.path.join(HOME, "events_verdicts.jsonl")
PROJ = os.path.join(os.path.expanduser("~"), ".claude", "projects")
STRING_USER = b'"role":"user","content":"'        # a user record whose content is a string (a prompt, not a tool result)


def log(msg):
    with open(os.path.join(HOME, "logs", "backfill.log"), "a", encoding="utf-8") as f:
        f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    print(msg, flush=True)


def jload(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def jsave(p, obj):
    tmp = p + ".tmp"
    json.dump(obj, open(tmp, "w", encoding="utf-8"))
    os.replace(tmp, p)


def mirror_units(root):
    """Each transcript as one readable unit: a plain .jsonl, a .jsonl.gz, or the parts of a split .gz."""
    units = collections.OrderedDict()
    for p in glob.glob(os.path.join(root, "**", "*"), recursive=True):
        if os.path.isdir(p):
            continue
        if p.endswith(".jsonl"):
            units[p] = [p]
        elif p.endswith(".jsonl.gz"):
            units[p] = [p]
        elif ".jsonl.gz.part" in p:
            key = p.split(".part")[0]
            units.setdefault(key, []).append(p)
    for k in units:
        units[k].sort()
    return units


def materialise(parts, tmpdir):
    """A path the scanners can read: the file itself, or its parts decompressed into one temporary file."""
    if len(parts) == 1 and parts[0].endswith(".jsonl"):
        return parts[0], False
    dst = os.path.join(tmpdir, "unit.jsonl")
    if len(parts) == 1:
        with gzip.open(parts[0], "rb") as src, open(dst, "wb") as out:
            while True:
                b = src.read(1 << 22)
                if not b:
                    break
                out.write(b)
        return dst, True
    gz = os.path.join(tmpdir, "unit.gz")
    with open(gz, "wb") as out:
        for p in parts:
            with open(p, "rb") as src:
                out.write(src.read())
    with gzip.open(gz, "rb") as src, open(dst, "wb") as out:
        while True:
            b = src.read(1 << 22)
            if not b:
                break
            out.write(b)
    os.remove(gz)
    return dst, True


def claude(max_files=0):
    units = mirror_units(os.path.join(MIRROR, "claude_projects"))
    done = jload(STATE_CLAUDE)
    stats, kinds = collections.Counter(), collections.Counter()
    t0 = time.time()
    todo = [k for k in units if k not in done]
    if max_files:
        todo = todo[:max_files]
    log("claude history: %d transcripts in the mirror, %d to read" % (len(units), len(todo)))
    tmpdir = tempfile.mkdtemp(prefix="bg_backfill_")
    with open(OUT_CLAUDE, "a", encoding="utf-8") as out:
        def emit(ts, s, kind, nid):
            out.write(json.dumps({"ts": round(ts, 3), "session": s, "kind": kind, "id": nid}) + "\n")
            kinds[kind] += 1
        for i, k in enumerate(todo):
            try:
                path, tmp = materialise(units[k], tmpdir)
                X.scan_file(path, 0, emit, stats)
                stats["bytes"] += os.path.getsize(path)
                if tmp:
                    os.remove(path)
            except Exception as e:
                stats["file_errors"] += 1
                log("  skip %s: %s: %s" % (os.path.basename(k), type(e).__name__, str(e)[:120]))
            done[k] = 1
            if (i + 1) % 200 == 0:
                out.flush()
                jsave(STATE_CLAUDE, done)
                log("  claude %d/%d transcripts, %.0f MB, %d events, %.0f s" % (
                    i + 1, len(todo), stats["bytes"] / 2**20, sum(kinds.values()), time.time() - t0))
    jsave(STATE_CLAUDE, done)
    try:
        os.rmdir(tmpdir)
    except OSError:
        pass
    log("claude history done: %d transcripts, %.0f MB, events %s, bad json %d, file errors %d, %.0f s" % (
        len(todo), stats["bytes"] / 2**20, dict(kinds), stats["bad_json"], stats["file_errors"], time.time() - t0))


def codex(max_files=0, local_only=False):
    import extract_codex_events as C
    state = jload(STATE_CODEX)
    stats, kinds = collections.Counter(), collections.Counter()
    t0 = time.time()
    local = {os.path.basename(p): p for p in glob.glob(os.path.join(CODEX_LOCAL, "*", "*", "*", "*.jsonl"))}
    units = [] if local_only else [(k, v) for k, v in mirror_units(os.path.join(MIRROR, "codex_sessions")).items()
                                   if os.path.basename(k).replace(".gz", "") not in local]
    todo = [(k, v, None) for k, v in units if k not in state]
    # rollouts the 40-day Codex capture already reads (events_codex.jsonl, its state codex_state.json) stay with it;
    # this file takes the older local ones it never saw
    recent = set(jload(os.path.join(HOME, "codex_state.json")))
    for name, p in sorted(local.items(), key=lambda kv: os.path.getmtime(kv[1])):
        if p in recent:
            continue
        st = state.get(p) or {"offset": 0}
        if os.path.getsize(p) > int(st.get("offset", 0)):
            todo.append((p, [p], st))
    if max_files:
        todo = todo[:max_files]
    log("codex: %d mirror-only rollouts, %d local rollouts with new bytes" % (
        sum(1 for t in todo if t[2] is None), sum(1 for t in todo if t[2] is not None)))
    tmpdir = tempfile.mkdtemp(prefix="bg_backfill_cx_")
    with open(OUT_CODEX, "a", encoding="utf-8") as out:
        def emit(ts, s, kind, nid):
            out.write(json.dumps({"ts": round(ts, 3), "session": s, "kind": kind, "id": nid}) + "\n")
            kinds[kind] += 1
        for i, (k, parts, st) in enumerate(todo):
            try:
                if st is None:                       # a mirror unit, read once
                    path, tmp = materialise(parts, tmpdir)
                    C.scan(path, 0, {"offset": 0}, emit, stats)
                    stats["bytes"] += os.path.getsize(path)
                    if tmp:
                        os.remove(path)
                    state[k] = {"mirror": 1}
                else:                                # a local rollout, from its saved offset
                    size = os.path.getsize(k)
                    start = int(st.get("offset", 0))
                    if start > size:
                        st, start = {"offset": 0}, 0
                    C.scan(k, start, st, emit, stats)
                    st["offset"] = size
                    state[k] = st
                    stats["bytes"] += size - start
            except Exception as e:
                stats["file_errors"] += 1
                log("  skip %s: %s: %s" % (os.path.basename(k), type(e).__name__, str(e)[:120]))
            if (i + 1) % 300 == 0:
                out.flush()
                jsave(STATE_CODEX, state)
                log("  codex %d/%d rollouts, %.0f MB, %d events, %.0f s" % (
                    i + 1, len(todo), stats["bytes"] / 2**20, sum(kinds.values()), time.time() - t0))
    jsave(STATE_CODEX, state)
    try:
        os.rmdir(tmpdir)
    except OSError:
        pass
    log("codex done: %d rollouts, %.0f MB, events %s, bad json %d, file errors %d, %.0f s" % (
        len(todo), stats["bytes"] / 2**20, dict(kinds), stats.get("bad_json", 0), stats["file_errors"],
        time.time() - t0))


OUT_GAP = os.path.join(HOME, "events_history_gap.jsonl")
DREAM = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")


def _first_ts(path, last=False):
    """The earliest (or latest) event time in an events file, read cheaply."""
    best = None
    with open(path, "rb") as fh:
        for raw in fh:
            try:
                i = raw.index(b",")
                ts = float(raw[7:i])
            except (ValueError, IndexError):
                continue
            if best is None or (ts > best if last else ts < best):
                best = ts
    return best


_FIELD = {k: __import__("re").compile(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)("?)' % k)
          for k in ("command", "file_path", "path", "notebook_path", "pattern")}


def _lenient_input(raw):
    """The reflex log keeps the first few hundred characters of a tool's input, so most rows are cut mid-JSON. Parse it
    whole when it parses; otherwise pull the string fields out of the fragment (a cut command keeps its head, which is
    what names the program, the folders and the hosts). Marks the edit/write keys that tell a read from a write."""
    try:
        inp = json.loads(raw)
        if isinstance(inp, dict):
            return inp
    except ValueError:
        pass
    out = {}
    for k, rx in _FIELD.items():
        m = rx.search(raw)
        if m:
            try:
                out[k] = json.loads('"%s"' % m.group(1).rstrip("\\"))
            except ValueError:
                out[k] = m.group(1).replace("\\\\", "\\")
    if out:
        for k in ("old_string", "content", "new_source"):
            if '"%s"' % k in raw:
                out[k] = ""
    return out


def gap():
    """8 July to 16 August: the Claude transcripts of those weeks were deleted by the old 30-day cleanup and exist nowhere.
    What the brain itself logged in them survives: every tool call that fired a reflex (instinct_fires.2026-07/08.tsv: time,
    session, the reflex, the tool's input - file paths, commands) and every note it surfaced (surfaced.tsv). Each row
    becomes the events the capture would have made of that call (tool, files, folders, commands, hosts) plus the reflex
    or served node, written only for the weeks no transcript covers, so nothing is counted twice."""
    import hashlib
    import re as _re
    lo = _first_ts(OUT_CLAUDE, last=True) or 0.0              # the mirror's last event (7 July)
    hi = _first_ts(os.path.join(HOME, "events_touch.jsonl")) or 9e18   # the live capture's first (mid August)
    log("gap window %s .. %s" % (time.strftime("%Y-%m-%d %H:%M", time.localtime(lo)),
                                  time.strftime("%Y-%m-%d %H:%M", time.localtime(hi))))
    kinds = collections.Counter()
    seen_calls = set()
    sid_rx = _re.compile(r"^[0-9a-f]{8}-[0-9a-f]{3,4}")
    t0 = time.time()
    with open(OUT_GAP, "w", encoding="utf-8") as out:
        def emit(ts, s, kind, nid):
            if nid:
                out.write(json.dumps({"ts": round(ts, 3), "session": s, "kind": kind, "id": nid}) + "\n")
                kinds[kind] += 1
        for f in sorted(glob.glob(os.path.join(DREAM, "instinct_fires.2026-0[78].tsv"))):
            for line in open(f, encoding="utf-8", errors="replace"):
                p = line.rstrip("\n").split("\t")
                try:
                    ts = float(p[0])
                except (ValueError, IndexError):
                    continue
                if not lo < ts < hi or len(p) < 6:
                    continue
                sid = next((x for x in p[5:] if sid_rx.match(x)), None)
                if not sid:
                    continue
                s = X.norm_session(sid)
                slug = p[3].strip()
                if slug:
                    emit(ts, s, "reflex", slug)
                inp = _lenient_input(p[4])
                if not inp:
                    continue
                key = hashlib.sha1(("%s|%.0f|%s" % (s, ts, p[4][:400])).encode("utf-8", "replace")).hexdigest()
                if key in seen_calls:                 # several reflexes fire on one call: its nodes once
                    continue
                seen_calls.add(key)
                cmd = inp.get("command")
                fp = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
                writes = "old_string" in inp or "content" in inp or "new_source" in inp
                if isinstance(cmd, str):
                    emit(ts, s, "tool", "Bash:" + X.bash_cmd0(cmd))
                    paths = [os.path.expanduser("~") + m.group(1) for m in X.PATH_RX.finditer(cmd)]
                    got = set()
                    for pth in paths:
                        for kind, nid in X.path_nodes(pth, None):
                            if (kind, nid) not in got:
                                got.add((kind, nid))
                                emit(ts, s, kind, nid)
                    for kind, nid in X.shell_nodes(cmd, ""):
                        if (kind, nid) not in got:
                            got.add((kind, nid))
                            emit(ts, s, kind, nid)
                elif isinstance(fp, str):
                    emit(ts, s, "tool", "Edit" if "old_string" in inp else ("Write" if "content" in inp else "Read"))
                    for kind, nid in X.path_nodes(fp, None):
                        emit(ts, s, "memw" if kind == "mem" and writes else kind, nid)
                elif isinstance(inp.get("pattern"), str):
                    emit(ts, s, "tool", "Grep")
        sp = os.path.join(DREAM, "surfaced.tsv")
        if os.path.exists(sp):
            for line in open(sp, encoding="utf-8", errors="replace"):
                p = line.rstrip("\n").split("\t")
                try:
                    ts = float(p[0])
                except (ValueError, IndexError):
                    continue
                if lo < ts < hi and len(p) >= 3 and sid_rx.match(p[1] + "-000"[:0] if len(p[1]) >= 8 else "x"):
                    emit(ts, X.norm_session(p[1]), "served", p[2].strip())
    log("gap done: %d tool calls, events %s, %.0f s" % (len(seen_calls), dict(kinds), time.time() - t0))


def _verdicts_in(path, emit, stats):
    """His corrections, repeats and praise in one transcript: string user records that are prompts he typed, and (since
    27 Sep) the prompts he typed while the agent was working, which the transcript keeps only as queued_command
    attachments - 1,213 of his 5,269 prompts since 16 Aug and 59 of his 165 corrections were there and nowhere else."""
    seen = {}
    with open(path, "rb") as fh:
        for raw in fh:
            if STRING_USER not in raw and b"queued_command" not in raw:
                continue
            try:
                o = json.loads(raw)
            except Exception:
                stats["bad_json"] += 1
                continue
            t = o.get("type")
            if t == "user":
                content = (o.get("message") or {}).get("content")
                if not isinstance(content, str):
                    continue
            elif t == "attachment":
                a = o.get("attachment") or {}
                content = a.get("prompt") if a.get("type") == "queued_command" else None
                if not isinstance(content, str):
                    continue
            else:
                continue
            c = X.his_text(o, content.strip())
            if not c:
                stats["not_his"] += 1
                continue
            s = X.norm_session(o.get("sessionId") or "")
            ts = X.epoch(o.get("timestamp") or "")
            if not (s and ts) or not X._first_sight(seen, s, c, ts):
                continue
            stats["his_prompts"] += 1
            if t == "attachment":
                stats["his_queued_prompts"] += 1
            for v in X.his_verdicts(o, c):
                emit(ts, s, v)


def verdicts(out_path=OUT_VERDICTS):
    """events_verdicts.jsonl (or out_path) from scratch: his corrections, repeats and praise in every Claude transcript there is (the
    mirror, April to 7 July; ~/.claude/projects, 10 Aug on), read with the capture's test for a prompt of his
    (X.his_text, 26 Sep: before it, 92 percent of the captured 'corrections' were hook feedback). graph_store.build takes
    his corrections from this file alone (build_config.json verdict_events_file); the hourly capture appends to it."""
    seen, rows = set(), []
    stats, kinds = collections.Counter(), collections.Counter()
    t0 = time.time()

    def emit(ts, s, v):
        k = (s, round(ts, 3), v)
        if k not in seen:
            seen.add(k)
            rows.append(k)
            kinds[v] += 1
    units = mirror_units(os.path.join(MIRROR, "claude_projects"))
    tmpdir = tempfile.mkdtemp(prefix="bg_verdicts_")
    for i, k in enumerate(units):
        try:
            path, tmp = materialise(units[k], tmpdir)
            _verdicts_in(path, emit, stats)
            if tmp:
                os.remove(path)
        except Exception as e:
            stats["file_errors"] += 1
            log("  skip %s: %s" % (os.path.basename(k), type(e).__name__))
        if (i + 1) % 1000 == 0:
            log("  verdicts: mirror %d/%d, %s, %.0f s" % (i + 1, len(units), dict(kinds), time.time() - t0))
    try:
        os.rmdir(tmpdir)
    except OSError:
        pass
    n_mirror = len(rows)
    live = [p for p in glob.glob(os.path.join(PROJ, "**", "*.jsonl"), recursive=True)
            if os.sep + "memory" + os.sep not in p and os.sep + "subagents" + os.sep not in p]
    for i, p in enumerate(live):
        try:
            _verdicts_in(p, emit, stats)
        except Exception:
            stats["file_errors"] += 1
        if (i + 1) % 2000 == 0:
            log("  verdicts: live %d/%d, %s, %.0f s" % (i + 1, len(live), dict(kinds), time.time() - t0))
    rows.sort(key=lambda r: r[1])
    new = out_path + ".new"
    with open(new, "w", encoding="utf-8") as out:
        for s, ts, v in rows:
            out.write(json.dumps({"ts": ts, "session": s, "kind": "verdict", "id": v}) + "\n")
    # the previous file is brain data: a transcript gone since its build takes its rows with it, so keep a copy first
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from quarantine_rows import keep_old
    keep_old(out_path)
    os.replace(new, out_path)
    log("verdicts done: %d events (%d from the mirror's %d transcripts, %d from %d live ones) %s; his prompts %d, "
        "records not his %d, bad json %d, file errors %d, %.0f s" % (
            len(rows), n_mirror, len(units), len(rows) - n_mirror, len(live), dict(kinds), stats["his_prompts"],
            stats["not_his"], stats["bad_json"], stats["file_errors"], time.time() - t0))


def main():
    X.polite()
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=("claude", "codex", "codex-local", "gap", "verdicts", "all"))
    ap.add_argument("--max-files", type=int, default=0)
    ap.add_argument("--out", default=OUT_VERDICTS, help="verdicts: where to write")
    a = ap.parse_args()
    os.makedirs(os.path.join(HOME, "logs"), exist_ok=True)
    if a.what in ("claude", "all"):
        claude(a.max_files)
    if a.what in ("codex", "all"):
        codex(a.max_files)
    if a.what == "codex-local":
        codex(a.max_files, local_only=True)
    if a.what in ("gap", "all"):
        gap()
    if a.what == "verdicts":
        verdicts(a.out)


if __name__ == "__main__":
    main()
