"""Measure: do spread_recall's real-session fires connect memory pairs that have NO direct
wiki-link (i.e., pairs the nightly linking pass never drew, reachable only via the Hebbian
co-read layer)? Feeds the fleet-sampling hunch in OPEN_QUESTIONS.

Method: for each fired (session, surfaced) in .spread_fired.json (UUID sessions only —
synthetic eval ids excluded), find the fire in the session transcript, recover the trigger
(Read of a memory file → that slug is the seed; else the user prompt → re-derive seeds with
spread_recall's own functions against the CURRENT index [drift caveat]), then test whether
surfaced ↔ any seed has a direct [[wiki-link]] in either file.
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

fired = json.load(io.open(os.path.join(DR, ".spread_fired.json"), encoding="utf-8"))
fires = []
for key, ts in fired.items():
    sess, slug = key.split("|", 1)
    if UUID_RE.match(sess):
        fires.append((sess, slug, ts))
fires.sort(key=lambda x: x[2])

idx = sr._index_lines()

results = []
for sess, slug, ts in fires:
    path = os.path.join(TR, sess + ".jsonl")
    if not os.path.exists(path):
        results.append((sess, slug, "no-transcript", None, None)); continue
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
        results.append((sess, slug, "no-fire-line", None, None)); continue
    # walk up the parent chain: attachments chain off each other before the real trigger
    seeds, trig = set(), "?"
    cur = hit
    for _ in range(8):
        cur = entries.get(cur.get("parentUuid", ""), None)
        if cur is None:
            break
        pj = json.dumps(cur)
        m = re.search(r'"file_path"\s*:\s*"([^"]*?memory[^"]*?\.md)"', pj.replace("\\\\", "/"))
        if m:
            trig = "read"
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
            trig = "prompt"
            s, _lex = sr._seeds_from_prompt(sr._words(content), idx)
            seeds = set(s)
            break
    if not seeds:
        results.append((sess, slug, trig + ":no-seeds", None, None)); continue
    linked = [s for s in seeds if direct_link(s, slug)]
    results.append((sess, slug, trig, len(seeds), linked))

judged = [r for r in results if r[3]]
nolink = [r for r in judged if not r[4]]
print(f"fires(real sessions)={len(fires)}  judged={len(judged)}  "
      f"no-direct-wiki-link={len(nolink)}  ({100*len(nolink)/max(1,len(judged)):.0f}%)")
print("\nsess      surfaced                                            trig    seeds link?")
for sess, slug, trig, n, linked in results:
    tag = ("NO-LINK" if linked == [] else (",".join(linked[:2]) if linked else trig))
    print(f"{sess[:8]}  {slug[:50]:52s} {trig:7s} {str(n or '-'):>4s}  {tag[:40]}")
