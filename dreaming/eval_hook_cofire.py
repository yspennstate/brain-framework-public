#!/usr/bin/env python3
"""eval_hook_cofire.py - how often the prompt/tool hooks surface the same memory twice (1000-ways #13).

Reads Claude transcripts (hook_success attachments), splits each event's additionalContext into the tagged blocks the
dispatcher joins ("[exact-name] ...", "[unconscious] ...", "[math-sense] ..."), and pulls the memory slugs each block
names (`name_YYYY_MM_DD`, [[slug]], or "card <slug>"). Two rates:

  co-fire   - share of events where one slug is named by two or more different tags in the SAME event
  re-fire   - share of surfaced (session, slug) pairs that come back within WINDOW seconds in a later event of the
              same session (any tag), i.e. the reader saw it a moment ago

usage: python3 eval_hook_cofire.py [--since-hours N] [--window S] [--after-epoch T] [--before-epoch T] [transcript.jsonl ...]
       default: every transcript in ~/.claude/projects/{{BRAIN_SLUG}} touched in the last 48 h, window 600 s
"""
import collections, glob, json, os, re, sys, time
from datetime import datetime

PROJ = os.path.expanduser("~/.claude/projects/{{BRAIN_SLUG}}")
SLUG = re.compile(r"\[\[([a-z0-9][a-z0-9_]{6,}?)\]\]|\b([a-z][a-z0-9_]{6,}_20\d\d_\d\d_\d\d)\b")
TAG = re.compile(r"^\s*\[([a-z][a-z0-9 _-]{1,30})(?:\s*\|[^\]]*)?\]", re.M)


def blocks(text):
    """(tag, block text) for each tagged block; text before the first tag goes under 'untagged'."""
    out, last, tag = [], 0, "untagged"
    for m in TAG.finditer(text):
        out.append((tag, text[last:m.start()]))
        tag, last = m.group(1).split()[0], m.end()
    out.append((tag, text[last:]))
    return [(t, b) for t, b in out if b.strip()]


def events(path):
    for line in open(path, encoding="utf-8", errors="replace"):
        if '"hook_success"' not in line or "additionalContext" not in line:
            continue
        try:
            d = json.loads(line)
            a = d["attachment"]
            ctx = json.loads(a.get("stdout") or "{}").get("hookSpecificOutput", {}).get("additionalContext", "")
        except (ValueError, KeyError, AttributeError):
            continue
        if not ctx or a.get("hookEvent") == "SessionStart":
            continue
        ts = d.get("timestamp", "")
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        yield d.get("sessionId", os.path.basename(path)[:8]), t, a.get("hookEvent", "?"), ctx


def norm(b):
    # a repeat is the same block with only its numbers changed ("stale (8909 seconds)", "overlaps 22 clauses")
    return re.sub(r"\d+", "#", " ".join(b.split()))


def measure(paths, window, after=0.0, before=float("inf")):
    n_ev = n_ev_surf = n_cofire = 0
    pairs = refire = 0
    by_tagpair = collections.Counter()
    last_seen = {}
    # block-level repeats: chars of each tag's block whose number-blind text the session already saw within window
    tag_chars, tag_repeat_chars = collections.Counter(), collections.Counter()
    seen_block = {}
    for p in paths:
        for sess, t, ev, ctx in sorted(events(p), key=lambda e: e[1]):
            if not after <= t < before:
                continue
            n_ev += 1
            slug_tags = collections.defaultdict(set)
            for tag, b in blocks(ctx):
                tag_chars[tag] += len(b)
                k = (sess, tag, norm(b))
                if k in seen_block and t - seen_block[k] <= window:
                    tag_repeat_chars[tag] += len(b)
                seen_block[k] = t
                for m in SLUG.finditer(b):
                    slug_tags[m.group(1) or m.group(2)].add(tag)
            if not slug_tags:
                continue
            n_ev_surf += 1
            multi = {s: tg for s, tg in slug_tags.items() if len(tg) > 1}
            if multi:
                n_cofire += 1
                for tg in multi.values():
                    by_tagpair[" + ".join(sorted(tg))] += 1
            for s in slug_tags:
                pairs += 1
                k = (sess, s)
                if k in last_seen and t - last_seen[k] <= window:
                    refire += 1
                last_seen[k] = t
    return dict(events=n_ev, events_with_a_slug=n_ev_surf, cofire_events=n_cofire,
                cofire_rate=round(n_cofire / n_ev_surf, 4) if n_ev_surf else None,
                surfaced_slugs=pairs, refired_within_window=refire,
                refire_rate=round(refire / pairs, 4) if pairs else None,
                top_cofire_tags=by_tagpair.most_common(8),
                hook_chars=sum(tag_chars.values()), repeat_chars=sum(tag_repeat_chars.values()),
                repeat_share=round(sum(tag_repeat_chars.values()) / max(1, sum(tag_chars.values())), 4),
                repeat_by_tag={t: [c, tag_repeat_chars[t], round(tag_repeat_chars[t] / c, 3)]
                               for t, c in tag_chars.most_common(8)})


if __name__ == "__main__":
    a = sys.argv[1:]
    since, window = 48.0, 600.0
    if "--since-hours" in a:
        i = a.index("--since-hours"); since = float(a[i + 1]); del a[i:i + 2]
    if "--window" in a:
        i = a.index("--window"); window = float(a[i + 1]); del a[i:i + 2]
    after, before = 0.0, float("inf")
    if "--after-epoch" in a:
        i = a.index("--after-epoch"); after = float(a[i + 1]); del a[i:i + 2]
    if "--before-epoch" in a:
        i = a.index("--before-epoch"); before = float(a[i + 1]); del a[i:i + 2]
    paths = a or [p for p in glob.glob(os.path.join(PROJ, "*.jsonl")) if os.path.getmtime(p) > time.time() - since * 3600]
    r = measure(paths, window, after, before)
    r["transcripts"] = len(paths)
    print(json.dumps(r, indent=1))
