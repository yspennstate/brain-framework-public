"""Pre-gate candidate-pool test: does the WALK reach undrawn pairs that the FIRE GATE then
suppresses? Replays the day's real triggers (seeds recovered from transcripts, same method as
measure_spread_reach.py) through spread_recall's own _spread(), collects ALL candidates above
CAND_FLOOR (pre margin/salience/redundancy gates), and measures the undrawn-link fraction in
the candidate population vs the fired population (known: 41/42 drawn).

Caveat carried: same-day graph, but the graph is non-stationary; this is a same-day replay,
not a reconstruction of the exact at-fire state.
"""
import json, os, re, sys, io
from collections import defaultdict

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
TR = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}")
sys.path.insert(0, DR)
import spread_recall as sr

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

def norm(s):
    s = s.lower().replace("-", "_")
    return re.sub(r"_\d{4}_\d{2}_\d{2}$", "", s)

_body_cache = {}
def body(slug):
    if slug not in _body_cache:
        try:
            _body_cache[slug] = io.open(os.path.join(MEM, slug + ".md"),
                                        encoding="utf-8", errors="replace").read()
        except Exception:
            _body_cache[slug] = ""
    return _body_cache[slug]

def links_of(slug):
    return set(norm(m) for m in re.findall(r"\[\[([^\]\|]+?)\]\]", body(slug)))

def direct_link(a, b):
    return norm(b) in links_of(a) or norm(a) in links_of(b)

# --- recover the day's real triggers (seeds) exactly as measure_spread_reach did ---
fired = json.load(io.open(os.path.join(DR, ".spread_fired.json"), encoding="utf-8"))
fires = sorted(((k.split("|", 1)[0], k.split("|", 1)[1], v) for k, v in fired.items()
                if UUID_RE.match(k.split("|", 1)[0])), key=lambda x: x[2])
idx = sr._index_lines()

triggers = []   # (sess, fired_slug, seeds:set)
for sess, slug, ts in fires:
    path = os.path.join(TR, sess + ".jsonl")
    if not os.path.exists(path):
        continue
    entries, hit = {}, None
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            if '"hook_additional_context"' in ln and ("reminds me of `" + slug + "`") in ln:
                try:
                    hit = json.loads(ln)
                except Exception:
                    pass
            if '"uuid"' in ln:
                try:
                    j = json.loads(ln)
                    if "uuid" in j:
                        entries[j["uuid"]] = j
                except Exception:
                    pass
    if hit is None:
        continue
    seeds, cur = set(), hit
    for _ in range(8):
        cur = entries.get(cur.get("parentUuid", ""), None)
        if cur is None:
            break
        pj = json.dumps(cur)
        m = re.search(r'"file_path"\s*:\s*"([^"]*?memory[^"]*?\.md)"', pj.replace("\\\\", "/"))
        if m:
            seeds = {os.path.basename(m.group(1))[:-3]}
            break
        if "attachment" in cur:
            continue
        msg = cur.get("message", {}) or {}
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content
                               if isinstance(c, dict) and c.get("type") == "text")
        if isinstance(content, str) and content.strip() and msg.get("role") == "user":
            s, _lex = sr._seeds_from_prompt(sr._words(content), idx)
            seeds = set(s)
            break
    if seeds:
        # for read-triggers replay with the same charge the hook uses
        triggers.append((sess, slug, seeds))

# --- replay each trigger; collect the pre-gate candidate pool ---
adj, deg = sr._load_graph()
tot_c = tot_undrawn = 0
top5_c = top5_undrawn = 0
per_trigger = []
for sess, fired_slug, seeds in triggers:
    seed_w = {s: 2.2 for s in seeds} if len(seeds) == 1 else {s: 1.0 for s in seeds}
    act, parent = sr._spread(seed_w, adj, deg)
    cands = sorted(((a, s) for s, a in act.items()
                    if s not in seeds and s not in sr.SKIP and a >= sr.CAND_FLOOR),
                   reverse=True)
    und = [s for a, s in cands if not any(direct_link(s, sd) for sd in seeds)]
    tot_c += len(cands); tot_undrawn += len(und)
    t5 = [s for a, s in cands[:5]]
    t5u = [s for s in t5 if not any(direct_link(s, sd) for sd in seeds)]
    top5_c += len(t5); top5_undrawn += len(t5u)
    per_trigger.append((sess[:8], next(iter(seeds)) if len(seeds) == 1 else f"{len(seeds)} seeds",
                        len(cands), len(und), len(t5u)))

print(f"triggers replayed={len(triggers)}")
print(f"ALL candidates:   {tot_c}   undrawn={tot_undrawn}  ({100*tot_undrawn/max(1,tot_c):.0f}%)")
print(f"TOP-5 candidates: {top5_c}   undrawn={top5_undrawn}  ({100*top5_undrawn/max(1,top5_c):.0f}%)")
print(f"FIRED (from measure_spread_reach): 42 judged, undrawn=1 (2%)")
print("\nsess      trigger                                      cands undrawn top5-undrawn")
for r in per_trigger:
    print(f"{r[0]}  {str(r[1])[:44]:46s} {r[2]:4d} {r[3]:6d} {r[4]:8d}")
