"""scope_lint.py - find stored rules that are wider than the words he gave them in.

On 19 Sep he asked for one film to be ninety percent found footage; the movie hook stored it as a law for every film, and the
histories of 25-26 Sep followed it (Food 9 percent Codex pictures) until he corrected it in anger on 26 Sep. The agents obeyed
the brain; the brain had the scope wrong. This is a first pass for the others, over every owner-order memory and every line of
the movie hook's rule digest: a unit is flagged when his quoted words carry a one-film scope ("this movie", "for this film",
"the kernel lecture") and none of his own words widen it ("from now on", "all the movies", "every", "always", "in general"),
while the rule's own words state or imply every such thing ("every film", "all films", "LAW", "always"). A flag is a question
for a reader, not a finding: the hand reading decides. Only film-family words count as narrowing, where a family exists that a
one-item order can be mistaken for; his quoted words are taken only where they are marked as his (see QUOTE).

    python scope_lint.py [--out FILE.json]      prints the flagged units and writes them with their evidence
"""
import argparse
import glob
import io
import json
import os
import re
import sys

H = os.path.expanduser("~")
MEM = os.path.join(H, ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")), "memory")
HOOK = os.path.join(H, ".claude", "movie_sentinel", "movie_alert_hook.py")

THING = (r"(?:movie|moive|mvoie|film|flim|fim|video|vidoe|viode|lecture|episode|episod|poem|peom|short|thumbnail|"
         r"thumnial|image|picture|footage|slide)")
NARROW = re.compile(r"\b(?:this|that|the new|the current|the latest|the first|the second|the) (?:[a-z0-9'\-]+ ){0,3}"
                    + THING + r"s?\b|\bfor this\b|\bin this " + THING, re.I)
WIDE_HIS = re.compile(r"\bfrom now on\b|\bform now on\b|\bfrom here on\b|\bgoing forward\b|\bin general\b|\balways\b|"
                      r"\bevery (?:single )?" + THING + r"|\ball (?:the |of the |my |our )?(?:[a-z]+ )?" + THING + r"s\b|"
                      r"\bany " + THING + r"|\beach " + THING + r"|\bfor all\b|\bthe future\b|\bfuture " + THING + r"s?\b|"
                      r"\bnever\b|\bevery time\b", re.I)
WIDE_RULE = re.compile(r"\bevery (?:" + THING + r")\b|\ball (?:" + THING + r")s\b|\bany (?:" + THING + r")\b|\bLAW\b|"
                       r"\blaw\b|\balways\b|\bfor all\b|\bevery agent\b|\ball agents\b|\bfrom now on\b", re.I)
# his words, where they are marked as his: *"..."* in a memory; "..." within 40 characters after words / said / asked /
# order / wrote / typed; and in the movie hook's digest, '...' right after a colon, semicolon or opening bracket
QUOTE = re.compile(r'\*"([^"]{25,600})"\*'
                   r'|(?:words?|said|asked|order|wrote|typed)[^"\n]{0,40}"([^"]{25,600})"'
                   r"|[:;(]\s'([^']{25,600})'")


def units():
    for p in sorted(glob.glob(os.path.join(MEM, "*.md"))):
        name = os.path.basename(p)[:-3]
        try:
            s = io.open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if not (name.startswith("owner_") or "status: owner-stated" in s):
            continue
        yield "memory", name, s
    try:
        src = io.open(HOOK, encoding="utf-8", errors="replace").read()
    except OSError:
        return
    body = src[src.index("def digest_lines():"):]
    body = body[body.index("out = ["):]
    body = body[:body.index("\n    ]\n")] if "\n    ]\n" in body else body[:20000]
    for m in re.finditer(r'"\s*(\d{1,2}) ([A-Z][^"]{3,120})', body):
        nxt = re.search(r'"\s*\d{1,2} [A-Z]', body[m.end():])
        chunk = body[m.start(): m.end() + (nxt.start() if nxt else 4000)]
        yield "movie_hook", "rule %s" % m.group(1), re.sub(r'"\s*\n\s*"', "", chunk)


def lint(kind, name, text):
    quotes = [next(g for g in m if g).strip() for m in QUOTE.findall(text)]
    his = " | ".join(quotes)
    rule = QUOTE.sub(" ", text)
    narrow = sorted(set(x.group(0).lower() for x in NARROW.finditer(his)))
    wide_his = sorted(set(x.group(0).lower() for x in WIDE_HIS.finditer(his)))
    wide_rule = sorted(set(x.group(0).lower() for x in WIDE_RULE.finditer(rule)))
    if narrow and wide_rule and not wide_his:
        return {"kind": kind, "name": name, "his_narrow": narrow[:6], "rule_wide": wide_rule[:6],
                "his_words": [q[:220] for q in quotes if NARROW.search(q)][:3]}
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    flags, n = [], 0
    for kind, name, text in units():
        n += 1
        f = lint(kind, name, text)
        if f:
            flags.append(f)
    for f in flags:
        print("%-10s %-80s narrow=%s wide=%s" % (f["kind"], f["name"][:80], f["his_narrow"][:3], f["rule_wide"][:3]))
        for q in f["his_words"][:2]:
            print("           his words: %s" % q[:200])
    print("%d units read, %d flagged" % (n, len(flags)))
    if a.out:
        json.dump({"units": n, "flags": flags}, io.open(a.out, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    sys.exit(main())
