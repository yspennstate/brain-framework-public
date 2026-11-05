#!/usr/bin/env python3
"""transactive_index.py - who-knows-what across the sessions that wrote the brain (1000-ways #16).

Each memory with an originSessionId is a labelled document: name + description + the first part of its body. A
multinomial naive Bayes over word counts, one class per session with at least MIN_DOCS memories, says which session
a topic belongs to. `--eval` measures that on held-out memories (5 folds, every memory scored by a model that never
saw it) against two baselines: always the largest session, and chance. `--build` writes ~/brain/TRANSACTIVE.md (each
session's memory count, dates and most distinctive words) and a json beside it. `who "<topic>"` ranks the sessions
whose memories the topic reads like, so an agent can open that session's memories or transcript first.

usage: python3 transactive_index.py --eval | --build | who "<topic>"
"""
import collections, json, math, os, random, re, sys

MEM = os.path.expanduser("~/.claude/projects/{{BRAIN_SLUG}}/memory")
OUT_MD = os.path.expanduser("~/brain/TRANSACTIVE.md")
OUT_JSON = os.path.expanduser("~/brain/.transactive_index.json")
MIN_DOCS = 20
BODY_CHARS = 4000
STOP = set("""the and for that this with from was were are not but have has had its it's into than then when what which
who why how all any can could would should will just also only more most some such each other our out over very one
two three about after before because been being both did does doing done down during few further here him his her
she they them their there these those through under until upon while within without you your yes use used using
file files line lines run runs new now see per via set sets get got make made name description type status verified
provisional evidence related memory memories fire when""".split())


def tokens(text):
    # dates and hex ids name the session, not what it knows: the filename date alone ties a memory to the few days
    # its session lived, so both are dropped before counting (measured with and without in --eval)
    text = re.sub(r"\b20\d\d[_-]\d\d[_-]\d\d\b|\b[0-9a-f]{7,}\b", " ", text.lower())
    # snake_case runs are split into their words (min_free_gb counts as min, free), and the body is read to 4000
    # chars: together +4.4..+5.3 points top-1 over three fold seeds, 31 Oct (was whole tokens, 1500 chars)
    return [p for w in re.findall(r"[a-z][a-z0-9_]{2,}", text)
            if not w.isdigit() and not re.search(r"\d{4}", w)
            for p in w.split("_") if len(p) >= 3 and p not in STOP]


def load():
    docs = []
    for f in sorted(os.listdir(MEM)):
        if not f.endswith(".md"):
            continue
        try:
            text = open(os.path.join(MEM, f), encoding="utf-8", errors="replace").read(BODY_CHARS + 3000)
        except OSError:
            continue
        m = re.match(r"---\n(.*?)\n---\n?(.*)", text, re.S)
        if not m:
            continue
        fm, body = m.group(1), m.group(2)
        o = re.search(r"originSessionId:\s*\"?([0-9a-f]{8})", fm)
        if not o:
            continue
        d = re.search(r"^description:\s*(.*)$", fm, re.M)
        c = re.search(r"^\s*created(?:_at)?:\s*\"?(\d{4}-\d{2}-\d{2})", fm, re.M)
        words = tokens(f[:-3].replace("_", " ") + " " + (d.group(1) if d else "") + " " + body[:BODY_CHARS])
        docs.append(dict(slug=f[:-3], session=o.group(1), date=c.group(1) if c else "", words=words))
    counts = collections.Counter(d["session"] for d in docs)
    keep = {s for s, n in counts.items() if n >= MIN_DOCS}
    return [d for d in docs if d["session"] in keep]


class NB:
    def __init__(self, docs):
        self.prior, self.cnt, self.tot = collections.Counter(), {}, collections.Counter()
        self.vocab = set()
        for d in docs:
            s = d["session"]
            self.prior[s] += 1
            c = self.cnt.setdefault(s, collections.Counter())
            c.update(d["words"])
            self.tot[s] += len(d["words"])
            self.vocab.update(d["words"])
        self.n = sum(self.prior.values())
        self.V = len(self.vocab) + 1

    def scores(self, words, use_prior=True):
        out = {}
        for s in self.prior:
            c, t = self.cnt[s], self.tot[s] + self.V
            sc = math.log(self.prior[s] / self.n) if use_prior else 0.0
            for w in words:
                if w in self.vocab:
                    sc += math.log((c[w] + 1) / t)
            out[s] = sc
        return sorted(out, key=out.get, reverse=True)


def evaluate(docs, folds=5, seed=7):
    random.Random(seed).shuffle(docs)
    top1 = top3 = 0
    for k in range(folds):
        test = docs[k::folds]
        train = [d for i, d in enumerate(docs) if i % folds != k]
        nb = NB(train)
        for d in test:
            r = nb.scores(d["words"])
            top1 += r[0] == d["session"]
            top3 += d["session"] in r[:3]
    n = len(docs)
    counts = collections.Counter(d["session"] for d in docs)
    return dict(memories=n, sessions=len(counts), top1=round(top1 / n, 4), top3=round(top3 / n, 4),
                majority_baseline=round(counts.most_common(1)[0][1] / n, 4), chance=round(1 / len(counts), 4),
                top3_majority_baseline=round(sum(v for _, v in counts.most_common(3)) / n, 4))


def distinctive(nb, s, k=10):
    bg = collections.Counter()
    for c in nb.cnt.values():
        bg.update(c)
    btot = sum(bg.values()) + nb.V
    c, t = nb.cnt[s], nb.tot[s] + nb.V
    sc = {w: math.log((c[w] + 1) / t) - math.log((bg[w] + 1) / btot) for w, v in c.items() if v >= 3}
    return sorted(sc, key=sc.get, reverse=True)[:k]


def build(docs):
    nb = NB(docs)
    ev = evaluate(list(docs))
    rows = []
    for s, n in nb.prior.most_common():
        ds = sorted(d["date"] for d in docs if d["session"] == s and d["date"])
        rows.append(dict(session=s, memories=n, first=ds[0] if ds else "", last=ds[-1] if ds else "",
                         words=distinctive(nb, s)))
    json.dump(dict(eval=ev, sessions=rows), open(OUT_JSON, "w"), indent=1)
    with open(OUT_MD, "w") as f:
        f.write("# Who knows what (transactive index, generated by transactive_index.py --build)\n\n")
        f.write("Sessions with at least %d memories, by memory count, with the words that set each apart. Held-out "
                "check: top-1 %.1f%%, top-3 %.1f%% over %d memories and %d sessions (largest-session baseline %.1f%%, "
                "chance %.1f%%). Ask it: `python3 ~/.claude/dreaming/transactive_index.py who \"<topic>\"`.\n\n"
                % (MIN_DOCS, 100 * ev["top1"], 100 * ev["top3"], ev["memories"], ev["sessions"],
                   100 * ev["majority_baseline"], 100 * ev["chance"]))
        for r in rows:
            f.write("- %s - %d memories, %s..%s: %s\n" % (r["session"], r["memories"], r["first"], r["last"],
                                                          ", ".join(r["words"])))
    return ev


def who(topic, docs, k=5):
    nb = NB(docs)
    q = set(tokens(topic))
    ranked = nb.scores(list(q), use_prior=False)[:k]
    for s in ranked:
        # the session's memories that share the most query words, so the pointer lands on the topic
        mine = sorted((d for d in docs if d["session"] == s), key=lambda d: -len(q & set(d["words"])))
        ex = [d["slug"] for d in mine[:3]]
        print("%s  (%d memories)  e.g. %s" % (s, nb.prior[s], ", ".join(ex)))


if __name__ == "__main__":
    a = sys.argv[1:]
    docs = load()
    if a[:1] == ["--eval"]:
        print(json.dumps(evaluate(docs)))
    elif a[:1] == ["--build"]:
        print(json.dumps(build(docs)))
    elif a[:1] == ["who"] and len(a) > 1:
        who(" ".join(a[1:]), docs)
    else:
        print(__doc__)
