#!/usr/bin/env python3
"""Build the judge's case file: stratified sample of judgment-reflex fires with
transcript context. Read-only over the fire log + transcripts.

Each case: the fired reflex (id + gut text), the tool call at the fire, and the next
N tool calls / assistant texts from the same session - enough for a blind judge to
answer (a) is the reflex's domain actually at play, (b) did conduct after the fire
show the prescribed caution. The mechanical surface class is recorded for the ANALYSIS
join but deliberately NOT shown to the judge (no framing inheritance).

Runbook (installed 07-16): run from a scratch dir; it writes judge_cases.jsonl there.
Then build batch prompts + blind judge subagents per
judgment_reflex_domain_inversion_damped_2026_07_16's session; aggregate with
fire_judge_aggregate.py judged_batch_*.json. Judge served-model is UNVERIFIED in the
07-16 run - record it if the harness exposes response-side model.
"""
import calendar, io, json, glob, os, random, re, sys, time, collections

DREAM = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")
sys.path.insert(0, DREAM)
import fire_surface

FIRELOG = os.path.join(DREAM, "instinct_fires.tsv")
PROJ = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}")
OUT = os.path.join(os.getcwd(), "judge_cases.jsonl")

TARGET = {"sim_flatters", "risk_vs_measured_record", "ib_marks",
          "vxn_long_tenor_state", "stale_mark_banks"}
PER_CELL = 5          # per (reflex, surface-group) cell
DAYS = 4
SELF = set(fire_surface.SELF_REFERENTIAL)


def gut_text():
    out = {}
    with io.open(os.path.join(DREAM, "instincts.tsv"), encoding="utf-8") as f:
        next(f)
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 5:
                out[p[0]] = p[4]
    return out


def load_session_tools(session_id):
    hits = glob.glob(os.path.join(PROJ, session_id[:8] + "*.jsonl"))
    if not hits:
        return []
    tools = []
    with io.open(hits[0], encoding="utf-8", errors="replace") as f:
        for ln in f:
            if '"tool_use"' not in ln and '"text"' not in ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("type") != "assistant":
                continue
            ts = r.get("timestamp", "")
            try:
                ep = calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))  # transcript stamps are UTC (Z); mktime read them as LOCAL, a -3h shift here (audit 010 F2)
            except Exception:
                continue
            content = (r.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    try:
                        txt = json.dumps(b.get("input") or {}, ensure_ascii=False)[:1500]
                    except Exception:
                        txt = ""
                    tools.append((ep, "tool:" + b.get("name", ""), txt))
                elif b.get("type") == "text":
                    t = (b.get("text") or "")[:1200]
                    if t.strip():
                        tools.append((ep, "assistant_text", t))
    tools.sort(key=lambda t: t[0])
    return tools


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    cutoff = time.time() - DAYS * 86400
    fires = []
    with io.open(FIRELOG, encoding="utf-8", errors="replace") as f:
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
            surf = fire_surface.classify(p[2], p[4])
            group = ("selfref" if surf in SELF else
                     "trading" if surf == "trading" else "other")
            fires.append({"ts": ts, "rid": p[3], "snip": p[4], "session": p[5],
                          "surface": surf, "group": group})

    # only sessions with a transcript in THIS project dir are judgeable (codex-CLI fires
    # have none here - the pilot hit the same wall); filter BEFORE stratifying so a cell
    # full of codex fires still yields its judgeable members instead of vanishing
    have_transcript = {}
    def judgeable(sid):
        if sid not in have_transcript:
            have_transcript[sid] = bool(glob.glob(os.path.join(PROJ, sid[:8] + "*.jsonl")))
        return have_transcript[sid]
    n_all = len(fires)
    fires = [fr for fr in fires if judgeable(fr["session"])]
    print("fires: %d total, %d with transcripts (codex/other-runtime excluded)" % (n_all, len(fires)))

    rng = random.Random(20260716)
    cells = collections.defaultdict(list)
    for fr in fires:
        cells[(fr["rid"], fr["group"])].append(fr)
    sample = []
    for key, lst in sorted(cells.items()):
        rng.shuffle(lst)
        seen_sessions = set()
        take = []
        for fr in lst:                      # max 2 per session per cell - diversity
            if sum(1 for t in take if t["session"] == fr["session"]) >= 2:
                continue
            take.append(fr)
            if len(take) >= PER_CELL:
                break
        sample.extend(take)

    cache = {}
    cases = []
    for i, fr in enumerate(sample):
        tools = cache.setdefault(fr["session"], load_session_tools(fr["session"]))
        if not tools:
            continue
        at = [t for t in tools if abs(t[0] - fr["ts"]) <= 3]
        after = [t for t in tools if 3 < t[0] - fr["ts"] <= 25 * 60][:6]
        if not at and not after:
            continue
        cases.append({
            "case_id": "c%03d" % i, "rid": fr["rid"], "ts": fr["ts"],
            "session": fr["session"][:8], "surface": fr["surface"], "group": fr["group"],
            "fire_snip": fr["snip"],
            "at_fire": [{"kind": k, "text": x} for _, k, x in at[:2]],
            "after": [{"kind": k, "text": x} for _, k, x in after],
        })

    with io.open(OUT, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    dist = collections.Counter((c["rid"], c["group"]) for c in cases)
    print("cases written:", len(cases), "->", OUT)
    for k, v in sorted(dist.items()):
        print("  %-24s %-8s %d" % (k[0], k[1], v))


if __name__ == "__main__":
    main()
