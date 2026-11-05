#!/usr/bin/env python3
"""fire_judge_fulltext.py - full-text fire contexts for trigger-precision work.

The 07-16 trigger lab died on a quantum mismatch: fire-log snips are 120 chars, so
"did the trigger match this fire's text" cannot be answered from the log - the matching
span often lies deeper in the tool input. This joins act/write fires of a target reflex
against the SESSION TRANSCRIPT's full tool_use input (the text instinct_prime actually
matched, up to its 32KB cap), records WHERE the current trigger matched (span + 60-char
context), and emits a labeled-corpus skeleton for the next trigger iteration: each row
carries full_text_head, match_context, and an empty label field for a blind judge or
hand pass.

UTC discipline: transcript stamps are Z; calendar.timegm only (audit 010 F2).
Read-only. Usage: python fire_judge_fulltext.py sim_flatters [--days 7] [--max 40]
       [--out fire_fulltext_<rid>.jsonl]
"""
import calendar, glob, io, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}")
MATCH_CAP = 32768   # lockstep with instinct_prime.MATCH_CAP


def trigger_for(rid):
    with io.open(os.path.join(HERE, "instincts.tsv"), encoding="utf-8") as f:
        next(f)
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if p[0] == rid:
                return p[3]
    raise SystemExit("unknown reflex id: %s" % rid)


def session_inputs(sid):
    """[(epoch, tool_name, full_serialized_input)] - the text the hook matched."""
    hits = glob.glob(os.path.join(PROJ, sid[:8] + "*.jsonl"))
    if not hits:
        return []
    out = []
    with io.open(hits[0], encoding="utf-8", errors="replace") as f:
        for ln in f:
            if '"tool_use"' not in ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("type") != "assistant":
                continue
            try:
                ep = calendar.timegm(time.strptime(r.get("timestamp", "")[:19],
                                                   "%Y-%m-%dT%H:%M:%S"))
            except Exception:
                continue
            for b in ((r.get("message") or {}).get("content") or []):
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    try:
                        txt = json.dumps(b.get("input") or {}, ensure_ascii=False)[:MATCH_CAP]
                    except Exception:
                        continue
                    out.append((ep, b.get("name", ""), txt))
    out.sort(key=lambda t: t[0])
    return out


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        raise SystemExit(__doc__)
    rid = args[0]
    days = int(args[args.index("--days") + 1]) if "--days" in args else 7
    cap = int(args[args.index("--max") + 1]) if "--max" in args else 40
    out_path = (args[args.index("--out") + 1] if "--out" in args
                else os.path.join(os.getcwd(), "fire_fulltext_%s.jsonl" % rid))
    rx = re.compile(trigger_for(rid), re.I)
    cutoff = time.time() - days * 86400
    fires = []
    with io.open(os.path.join(HERE, "instinct_fires.tsv"), encoding="utf-8", errors="replace") as f:
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if len(p) < 6 or p[1] != "PreToolUse" or p[2] not in ("act", "write"):
                continue
            try:
                ts = float(p[0])
            except Exception:
                continue
            if ts < cutoff or p[3] != rid or not re.match(r"^[0-9a-f]{8}-", p[5]):
                continue
            fires.append((ts, p[5]))
    cache, rows, per_session = {}, [], {}
    for ts, sid in fires:
        if len(rows) >= cap:
            break
        if per_session.get(sid, 0) >= 3:
            continue
        tools = cache.setdefault(sid, session_inputs(sid))
        best = None
        for ep, tn, txt in tools:
            if abs(ep - ts) <= 3:
                best = (ep, tn, txt)
                break
        if not best:
            continue
        m = rx.search(best[2])
        rows.append({
            "rid": rid, "ts": ts, "session": sid[:8], "tool": best[1],
            "full_text_head": best[2][:2000],
            "text_len": len(best[2]),
            "match_span": list(m.span()) if m else None,
            "match_context": (best[2][max(0, m.start() - 60):m.end() + 60] if m else None),
            "trigger_matches_fulltext": bool(m),
            "label_domain_at_play": "",   # blind judge / hand pass fills this
        })
        per_session[sid] = per_session.get(sid, 0) + 1
    with io.open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_match = sum(1 for r in rows if r["trigger_matches_fulltext"])
    print("%s: %d cases -> %s | trigger re-matches full text in %d/%d "
          "(a low count means the fire came from graph-augment or a since-changed trigger)"
          % (rid, len(rows), out_path, n_match, len(rows)))


if __name__ == "__main__":
    main()
