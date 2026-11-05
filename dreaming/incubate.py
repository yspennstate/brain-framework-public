#!/usr/bin/env python3
"""incubate.py - opportunistic assimilation: has new evidence quietly arrived for a problem the brain
PARKED and stopped thinking about? Surfaces the "wait, this recent thing bears on that stuck question"
that the subconscious is supposed to catch.

Grounded: Seifert et al. 1995 (opportunistic assimilation) - an unsolved problem leaves a "failure index"
primed in memory, and the mind surfaces it later when the environment happens to supply a relevant cue,
without deliberate search. Sio & Ormerod 2009 (meta-analysis) - the incubation effect is real and larger
when the delay is filled with rest/low-load activity, which is why the DREAM, not a mid-work hook, is the
right home: the offline pass is exactly the primed-but-not-actively-worked state where assimilation fires.
Zeigarnik - the open loop stays active; here that open loop is a line in OPEN_QUESTIONS.md.

Everything else that reads OPEN_QUESTIONS surfaces it as a static list. This one MATCHES each parked
question against the memories written SINCE it was parked - new evidence the question didn't have when it
was filed - and flags where a recent memory shares real substance with a stuck question. The dream then
reads the pair and, if the connection is genuine, advances or answers the question (never auto-edits).

  python incubate.py [recent_days]      # default: memories from the last 21 days
"""
import os, io, re, sys, time
from collections import defaultdict

HOME = os.path.expanduser("~")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
OQ = os.path.join(MEM, "OPEN_QUESTIONS.md")

STOP = set(("about above across after again against because before being below between both cannot could "
            "does doing during each else ever every from have here into just like made make many more most "
            "much must never only other over same should since some such than that their them then there "
            "these they this those through under until very were what when where which while with would "
            "your memory brain session sessions owner agent work working note gist scar the and for not but "
            "you our its was are has had will can now new one two his him her all any question questions "
            "open dream connects origin answered where does whether could would still").split())


def _toks(s):
    return set(w for w in re.findall(r"[a-zA-Z][a-zA-Z_]{4,}", s.lower()) if w not in STOP)


def _date(slug):
    m = re.search(r"_(\d{4})_(\d{2})_(\d{2})", slug)
    if not m:
        return None
    try:
        return time.mktime((int(m.group(1)), int(m.group(2)), int(m.group(3)), 12, 0, 0, 0, 0, -1))
    except Exception:
        return None


def _questions():
    """Parse open '- [ ]' blocks: text (possibly multi-line), the [[links]] it names, its origin date."""
    try:
        raw = io.open(OQ, encoding="utf-8", errors="replace").read()
    except Exception:
        return []
    qs = []
    cur = None
    for ln in raw.splitlines():
        if re.match(r"\s*- \[ \]", ln):
            if cur:
                qs.append(cur)
            cur = ln
        elif cur is not None and ln.strip() and not ln.startswith("- ["):
            cur += " " + ln.strip()
        elif cur is not None and (not ln.strip() or ln.startswith("- [x]")):
            qs.append(cur)
            cur = None
    if cur:
        qs.append(cur)
    out = []
    for q in qs:
        links = set(l[:-3] if l.endswith(".md") else l for l in re.findall(r"\[\[([^\]|]+?)\]\]", q))
        dm = re.search(r"(\d{4})-(\d{2})-(\d{2})", q)      # origin date (dream YYYY-MM-DD)
        origin = None
        if dm:
            try:
                origin = time.mktime((int(dm.group(1)), int(dm.group(2)), int(dm.group(3)), 12, 0, 0, 0, 0, -1))
            except Exception:
                pass
        text = re.sub(r"\[\[[^\]]+\]\]|https?://\S+|`[^`]+`", " ", q)
        out.append({"text": re.sub(r"\s+", " ", q).strip(), "toks": _toks(text), "links": links, "origin": origin})
    return out


def _recent(days):
    """Memories written in the last `days`, with their token set - the new evidence to test against."""
    cut = time.time() - days * 86400
    out = []
    try:
        files = [f for f in os.listdir(MEM) if f.endswith(".md") and f[0].islower()]
    except Exception:
        return out
    for f in files:
        slug = f[:-3]
        d = _date(slug)
        if not d or d < cut:
            continue
        try:
            head = io.open(os.path.join(MEM, f), encoding="utf-8", errors="replace").read(1400)
        except Exception:
            continue
        m = re.search(r'\ndescription:\s*["\']?(.+?)["\']?\s*\n[A-Za-z]', head, re.S)
        desc = m.group(1) if m else head.split("---", 2)[-1][:300]
        out.append({"slug": slug, "date": d, "toks": _toks(slug.replace("_", " ") + " " + desc)})
    return out


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 21
    qs = _questions()
    recent = _recent(days)
    print("=" * 66)
    print(" INCUBATE  -  new evidence for a parked question?  (last %d days)" % days)
    print("=" * 66)
    if not qs:
        print("\nno open questions parked")
        return
    if not recent:
        print("\nno recent memories to assimilate")
        return

    hits = []
    for q in qs:
        if len(q["toks"]) < 3:
            continue
        for r in recent:
            if r["slug"] in q["links"]:
                continue                                   # already cited by the question - not new
            if q["origin"] and r["date"] <= q["origin"] + 43200:
                continue                                   # not written AFTER the question was parked
            shared = q["toks"] & r["toks"]
            if len(shared) >= 3:
                score = len(shared) / (len(q["toks"] | r["toks"]) ** 0.5)
                hits.append((score, len(shared), q, r, shared))
    if not hits:
        print("\nnothing recent bears on a parked question yet (the loops stay open)")
        return
    hits.sort(key=lambda h: -h[0])
    seen_pairs = set()
    print("\nParked questions that RECENT work may bear on (the dream: read the pair, advance the question")
    print("only if the connection is real - a shared word is a hypothesis, not an answer):\n")
    shown = 0
    for score, n, q, r, shared in hits:
        key = (q["text"][:40], r["slug"])
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        qshort = re.sub(r"^\s*- \[ \]\s*", "", q["text"])[:130]
        print("  parked: %s" % qshort)
        print("    <- %s" % r["slug"])
        print("       shared: %s" % ", ".join(sorted(shared)[:8]))
        print()
        shown += 1
        if shown >= 8:
            break


if __name__ == "__main__":
    main()
