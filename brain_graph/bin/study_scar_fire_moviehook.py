"""study_scar_fire_moviehook.py - for the scar-fire cases whose order had no reflex, did the MOVIE hook (a separate
channel from the reflexes: ~/.claude/movie_sentinel/movie_alert_hook.py) put the order's wording into the session before
his correction? Post-hoc beside study_scar_fire_posthoc.py (28 Sep 09:2x, claude-825b1305).

For each case it scans the correction's session transcript up to the correction for hook context that carries the
order's wording (a pattern per theme, below) and reports how many deliveries there were, the first and last relative to
his ask, and the last delivery's opening. A delivery is a transcript record from a hook (an attachment or a system
record) whose [movie-brain] or [movie-projects] section holds the pattern; the agent's own words, tool output and the
other hooks' sections (his own prompts, echoed by the owner-prompts hook, carry every pattern) do not count.

    python study_scar_fire_moviehook.py LABELS_JSON POSTHOC_JSON OUT_JSON
"""
import glob
import io
import json
import os
import re
import sys
from datetime import datetime

TRANSCRIPTS = os.path.join(os.path.expanduser("~"), ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")))
PATTERNS = {
    "voice": re.compile(r"(NARRATION VOICE|VOICE \(|Benjamin|Microsoft neural|most human voice|calm and level)", re.I),
    "retired film": re.compile(r"(Navier[^\n]{0,120}retired|retired[^\n]{0,120}Navier)", re.I),
    "codex pictures": re.compile(r"(Codex[^\n]{0,80}\b[5-7]0 ?(%|percent)|\b[5-7]0 ?(%|percent)[^\n]{0,80}Codex)", re.I),
    "pictures": re.compile(r"(PICTURES|picture[- ]class|words-only|picture is worth)", re.I),
}
# only the movie hook's own sections count; the dispatcher merges several hooks into one record, and the owner-prompts
# hook echoes his own words, which carry every one of these patterns
SECTION = re.compile(r"(?m)^[ \t]*\[([a-z][a-z-]*)\]")
MOVIE_MARKS = ("movie-brain", "movie-projects")


def movie_sections(s):
    cuts = [(m.start(), m.group(1)) for m in SECTION.finditer(s)]
    out = []
    for i, (pos, mark) in enumerate(cuts):
        if mark in MOVIE_MARKS:
            end = cuts[i + 1][0] if i + 1 < len(cuts) else len(s)
            out.append(s[pos:end])
    return out


def stamp(o):
    ts = o.get("timestamp")
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() if ts else None


def hook_texts(o):
    """text a hook put into the session: attachments (hook_success, hook_additional_context) and system records"""
    if o.get("type") not in ("attachment", "system"):
        return []
    out = []

    def walk(x):
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(o.get("attachment") if o.get("type") == "attachment" else o)
    return out


def main():
    lab = {c["n"]: c for c in json.load(io.open(sys.argv[1], encoding="utf-8"))["preventable"]}
    post = json.load(io.open(sys.argv[2], encoding="utf-8"))["rows"]
    rows = []
    for r in post:
        if r["posthoc_class_by_content"] != "no reflex for the order at the time":
            continue
        c = lab[r["n"]]
        t, ask = float(c["t"]), float(c.get("ask_t") or c["t"])
        pat = PATTERNS.get(r["theme"])
        f = glob.glob(os.path.join(TRANSCRIPTS, str(c["sid"])[:8] + "*.jsonl"))
        rec = {"n": r["n"], "theme": r["theme"], "ask": datetime.fromtimestamp(ask).strftime("%m-%d %H:%M"),
               "transcript": bool(f)}
        if not f or pat is None:
            rows.append(rec)
            continue
        got, seen = [], set()
        for ln in io.open(f[0], encoding="utf-8", errors="replace"):
            if not pat.search(ln):
                continue
            o = json.loads(ln)
            ep = stamp(o)
            if ep is None or ep > t:
                continue
            for s in (sec for h in hook_texts(o) for sec in movie_sections(h)):
                m = pat.search(s)
                if m:
                    line = s[max(0, m.start() - 80): m.start() + 220].replace("\n", " ")
                    k = (int(ep // 60), line[:100])
                    if k not in seen:
                        seen.add(k)
                        got.append((ep, line))
        rec.update(deliveries_before_correction=len(got))
        if got:
            rec.update(first_min_vs_ask=round((got[0][0] - ask) / 60), last_min_vs_ask=round((got[-1][0] - ask) / 60),
                       last_text=got[-1][1][:300])
        rows.append(rec)
        print(json.dumps(rec, ensure_ascii=False)[:420])
    json.dump({"rows": rows}, io.open(sys.argv[3], "w", encoding="utf-8"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
