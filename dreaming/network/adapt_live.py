"""Adapter: build the event stream from a live deployment's own records.

Sources (all optional; pass the ones you have):
  --prompts-dir   directory of archived prompt files (each md/jsonl file holds dated
                  prompts; JSONL lines with {ts, text} or markdown '## <stamp>' blocks)
  --coactivation  coactivation.tsv (ts \t reader-session \t memory-slug) -> mem: events
  --outcomes      outcomes.jsonl from the dreaming sidecars -> tool events

Emits events.jsonl for build_graph.py / episodes.py. Runs on the deployment's own
data; nothing here ships anywhere.
"""
import argparse, io, json, os, re, sys, time

STAMP = re.compile(r"^##\s+(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?")


def emit(fh, ts, session, kind, nid, text=""):
    fh.write(json.dumps({"ts": ts, "session": session, "kind": kind,
                         "id": nid, "text": text[:2000]}) + "\n")


def parse_ts(v):
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    try:
        return float(s)
    except ValueError:
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?", s)
    if m:
        y, mo, dd, hh, mi, ss = (int(g or 0) for g in m.groups())
        return time.mktime((y, mo, dd, hh, mi, ss or 0, 0, 0, -1))
    return 0.0


def from_prompts_dir(d, out):
    n = 0
    for fn in sorted(os.listdir(d)):
        p = os.path.join(d, fn)
        if fn.endswith(".jsonl"):
            with io.open(p, encoding="utf-8", errors="replace") as fh:
                for ln in fh:
                    try:
                        r = json.loads(ln)
                    except json.JSONDecodeError:
                        continue
                    ts = parse_ts(r.get("ts", 0))
                    text = str(r.get("text", r.get("prompt", "")))
                    if ts and text:
                        emit(out, ts, r.get("session", fn), "prompt",
                             f"{fn}:{int(ts)}", text)
                        n += 1
        elif fn.endswith(".md"):
            cur_ts, buf = None, []
            with io.open(p, encoding="utf-8", errors="replace") as fh:
                for ln in fh:
                    m = STAMP.match(ln)
                    if m:
                        if cur_ts and buf:
                            emit(out, cur_ts, fn, "prompt",
                                 f"{fn}:{int(cur_ts)}", " ".join(buf))
                            n += 1
                        y, mo, dd, hh, mi, ss = (int(g or 0) for g in m.groups())
                        cur_ts = time.mktime((y, mo, dd, hh, mi, ss or 0, 0, 0, -1))
                        buf = []
                    elif cur_ts:
                        buf.append(ln.strip())
            if cur_ts and buf:
                emit(out, cur_ts, fn, "prompt", f"{fn}:{int(cur_ts)}", " ".join(buf))
                n += 1
    return n


def from_coactivation(path, out):
    """Expects an EVENT stream (ts, session, slug). Edge-list files (header a/b/weight)
    are a different artifact and are skipped with a note."""
    n = 0
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        first = fh.readline().rstrip("\n").split("\t")
        if first[:2] == ["a", "b"]:
            print(f"note: {path} is an edge list, not an event stream; skipped")
            return 0
        fh.seek(0)
        for ln in fh:
            parts = ln.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            ts = parse_ts(parts[0])
            if not ts:
                continue
            emit(out, ts, parts[1], "mem", parts[2])
            n += 1
    return n


_CMD_TOK = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{1,40}")


def from_outcomes(path, out):
    """Tool events, canonical: the tool NAME plus the command/script BASENAME are
    both nodes (tool:Bash, tool:Bash:git, tool:Bash:python) so 'every tool an agent
    uses' becomes a node without per-event duplication (his directive: canonical
    nodes, aggregated intensity)."""
    n = 0
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            ts = float(r.get("ts", r.get("time", 0)) or 0)
            tool = r.get("tool") or r.get("tool_name")
            if not (ts and tool):
                continue
            sess = r.get("session", r.get("sid", "?"))
            cmd = str(r.get("cmd", r.get("summary", "")))
            emit(out, ts, sess, "tool", str(tool), cmd[:800])
            n += 1
            # first 1-2 command tokens = the sub-tool actually invoked
            for tok in _CMD_TOK.findall(cmd)[:2]:
                low = tok.lower().lstrip(".")
                if len(low) >= 2 and low not in ("cd", "the", "and"):
                    emit(out, ts + 0.001, sess, "tool", f"{tool}:{low}", "")
                    n += 1
    return n


FM_DESC = re.compile(r"^description:\s*(.+)$", re.M)


def from_memory_dir(d, out):
    """Each memory file becomes a mem: node; its title + description tokens become
    kw: co-activations, timestamped at the file's mtime (a coarse but causal birth).
    Memory BODIES are the largest untapped keyword source."""
    n = 0
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".md") or fn in ("MEMORY.md", "INDEX_ARCHIVE.md"):
            continue
        p = os.path.join(d, fn)
        try:
            ts = os.path.getmtime(p)
            text = io.open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        slug = fn[:-3]
        m = FM_DESC.search(text)
        desc = m.group(1) if m else ""
        emit(out, ts, "corpus", "mem", slug, slug.replace("_", " ") + " " + desc[:1500])
        n += 1
    return n


def from_retrievals(path, out):
    """retrievals.tsv: slug \t epoch \t iso \t session -> a mem: access event
    (recall IS use; access intensity is exactly what the directive asks to aggregate)."""
    n = 0
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        header = fh.readline()
        for ln in fh:
            parts = ln.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            slug, epoch = parts[0], parts[1]
            ts = parse_ts(epoch)
            if not ts:
                continue
            emit(out, ts, parts[3], "mem", slug, "")
            n += 1
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompts-dir")
    ap.add_argument("--coactivation")
    ap.add_argument("--outcomes")
    ap.add_argument("--memory-dir")
    ap.add_argument("--retrievals")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    with io.open(a.out, "w", encoding="utf-8", newline="\n") as out:
        counts = {}
        if a.prompts_dir and os.path.isdir(a.prompts_dir):
            counts["prompts"] = from_prompts_dir(a.prompts_dir, out)
        if a.coactivation and os.path.exists(a.coactivation):
            counts["coactivation"] = from_coactivation(a.coactivation, out)
        if a.outcomes and os.path.exists(a.outcomes):
            counts["outcomes"] = from_outcomes(a.outcomes, out)
        if a.memory_dir and os.path.isdir(a.memory_dir):
            counts["memory"] = from_memory_dir(a.memory_dir, out)
        if a.retrievals and os.path.exists(a.retrievals):
            counts["retrievals"] = from_retrievals(a.retrievals, out)
    tot = sum(counts.values())
    print(f"events={tot} ({counts}) -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
