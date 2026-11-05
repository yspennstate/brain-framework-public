"""study_tokenizer_variants.py - three tokenizer fixes aimed at the misses of his own questions (27 Sep, claude-825b1305).

Nine of his 56 owner-voice questions miss the top five. About half are neighbouring memories about the same event; the
rest are words the index cannot join: "box 4" against box04 (a lone digit is dropped), "version 3" against
version_1_and_3, "pay" against "pays", "september 2" against 2026_09_02. Three additive variants, each adding tokens on
both sides and removing none, so an exact match keeps its weight:
  canon  a word followed by a short number, however written (box 4, box-04, box_4, box04), gains one token box4
  stem   a plural or third-person -s word gains its stem (pays -> pay, gates -> gate; S-stemmer, length >= 4)
  dates  a month-day, however written (september 2, sep 2nd, 2026-09-02, 2026_09_02), gains one token md0902
Scored with the live scorer (bm25.BM25.from_docs) and the live document text (slug x3, body, attachments, the
questions of doc expansion), on his 56 (gold), his 82 first prompts (what the session read), the agents' judged
searches, and the 968 reworded bank questions with their verbatim twins (quarantined keys). Counts only in the result.

    python study_tokenizer_variants.py OUT_JSON [--variants base,canon,stem,dates,all] [--skip-bank]
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import collections
import hashlib
import io
import json
import math
import os
import re
import sys
import time

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
sys.path.insert(0, DR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm25  # noqa: E402
from quarantine_rows import keep_old  # a rerun never destroys an earlier run's rows (brain data, 26 Sep law)

BASE_TOKENS = bm25.tokens
BASE_READ_DOC = bm25.read_doc
Q = os.path.join(HOME, "bank_recall_keys_quarantine")
STOP = bm25.STOP
MONTH = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
         "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
         "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12}
WN_TOKEN = re.compile(r"^([a-z]{3,})_?0*([0-9]{1,3})$")
WN_TEXT = re.compile(r"\b([a-z]{3,})[ _-]0*([0-9]{1,3})\b")
DATE_NUM = re.compile(r"\b20[0-9]{2}[-_ ]([01][0-9])[-_ ]([0-3][0-9])\b")
DATE_WORD = re.compile(r"\b(" + "|".join(sorted(MONTH, key=len, reverse=True)) + r")\.? ([0-3]?[0-9])(?:st|nd|rd|th)?\b")
DATE_WORD2 = re.compile(r"\b([0-3]?[0-9])(?:st|nd|rd|th)? (?:of )?(" + "|".join(sorted(MONTH, key=len, reverse=True)) + r")\b")


# canonw (27 Sep 03:3x): the same join, only for the words he numbers things with - "box 4", "version 3", "episode 12",
# "account 4", "gen 8" - so that a word like "day" or "top" with a number does not gain a token in thousands of memories
WN_WORDS = {"box", "version", "gen", "generation", "episode", "account", "acct", "phase", "round", "part", "slot",
            "chapter", "season", "stage", "tier", "paper"}


def canon_extra(toks, low, words=None):
    out = []
    for t in toks:
        m = WN_TOKEN.match(t)
        if m and m.group(1) not in STOP and (words is None or m.group(1) in words):
            out.append(m.group(1) + (m.group(2).lstrip("0") or "0"))
    for m in WN_TEXT.finditer(low):
        if m.group(1) not in STOP and (words is None or m.group(1) in words):
            out.append(m.group(1) + (m.group(2).lstrip("0") or "0"))
    return out


def s_stem(w):
    if len(w) < 4 or not w.isalpha():
        return None
    if w.endswith("ies") and not w.endswith(("eies", "aies")):
        return w[:-3] + "y"
    if w.endswith("es") and not w.endswith(("aes", "ees", "oes")):
        return w[:-1]
    if w.endswith("s") and not w.endswith(("us", "ss", "is")):
        return w[:-1]
    return None


def date_extra(low):
    out = []
    for m in DATE_NUM.finditer(low):
        mo, d = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            out.append("md%02d%02d" % (mo, d))
    for m in DATE_WORD.finditer(low):
        mo, d = MONTH[m.group(1)], int(m.group(2))
        if 1 <= d <= 31 and not (m.group(1) == "may" and False):
            out.append("md%02d%02d" % (mo, d))
    for m in DATE_WORD2.finditer(low):
        mo, d = MONTH[m.group(2)], int(m.group(1))
        if 1 <= d <= 31:
            out.append("md%02d%02d" % (mo, d))
    return out


def make_tokens(canon, stem, dates):
    def tok(text):
        base = BASE_TOKENS(text)
        low = (text or "").lower()
        extra = []
        if canon == "w":
            extra += canon_extra(base, low, WN_WORDS)
        elif canon:
            extra += canon_extra(base, low)
        if stem:
            extra += [st for t in base for st in [s_stem(t)] if st and st != t and st not in STOP]
        if dates == "placebo":
            # the date tokens' length without their match (fleet B02 r36, 27 Sep): one token per date occurrence that
            # nothing can ever match, so every document grows exactly as it does with the date tokens and BM25's
            # length normalization moves the same way, while no question can use them
            extra += ["zzplacebo%d" % next(_PLACEBO_N) for _ in date_extra(low)]
        elif dates:
            extra += date_extra(low)
        return base + extra
    return tok


import itertools
_PLACEBO_N = itertools.count()


def make_read_doc(tok):
    """The LIVE document (bm25.read_doc as it stands, dates and questions included), plus only the variant's extra
    tokens: those of the text the head budget covers (about 12,000 characters) and of the memory's questions.
    (Rewritten 27 Sep 03:3x: the first version rebuilt the head itself, which stopped matching the live index the moment
    date tokens went live after the head budget.)"""
    def read_doc(mem_dir, slug):
        tks = BASE_READ_DOC(mem_dir, slug)
        if not tks:
            return tks
        try:
            with io.open(os.path.join(mem_dir, slug + ".md"), encoding="utf-8", errors="replace") as fh:
                body = fh.read()
        except Exception:
            return tks
        body = re.sub(r"^---.*?---", lambda m: re.sub(r"^(name|metadata|node_type|type|originSessionId).*$", "",
                                                        m.group(0), flags=re.M), body, count=1, flags=re.S)
        sw = slug.replace("_", " ")
        text = (sw + " " + sw + " " + sw + " " + body)[:12000]
        extras = tok(text)[len(BASE_TOKENS(text)):]
        if bm25._EXPANSION_ON:
            q = bm25.expansions().get(slug)
            if q:
                qt = " ".join(q[0])
                extras += tok(qt)[len(BASE_TOKENS(qt)):]
        return tks + extras
    return read_doc


VARIANTS = {"base": (False, False, False), "canon": (True, False, False), "stem": (False, True, False),
            "dates": (False, False, True), "all": (True, True, True), "canonw": ("w", False, False),
            "placebo": (False, False, "placebo")}


def half(qid):
    return "tune" if int(hashlib.sha1(qid.encode()).hexdigest(), 16) % 2 == 0 else "test"


def rank_of(top, gold):
    return next((i + 1 for i, s in enumerate(top) if s.lower() in gold), 0)


def main():
    a = sys.argv[1:]
    out_path = a[0]
    names = (a[a.index("--variants") + 1] if "--variants" in a else "base,canon,stem,dates,all").split(",")
    # every variant is the LIVE document plus its extra tokens, and date tokens are live (bm25 default since 27 Sep
    # 03:1x): a dates arm on top of them counts each date twice and its 'base' already has them. Measuring the dates
    # change itself needs them off in the base (27 Sep 09:3x: a rerun without this read base 44 = dates 44 on his 56
    # and nearly went out as "the live date change shows no gain")
    if bm25._DATES_ON and any(VARIANTS[n][2] for n in names):
        sys.exit("date tokens are already in the live document; run with BRAIN_TOKENS_DATES=0 to measure a dates arm")
    t0 = time.time()
    import eval_brain_recall_fresh as fresh
    from eval_prompt_recall_fullstack import episodes
    bank = [(q, {g.lower() for g in golds}) for q, golds in fresh.BANK + fresh.BANK_V2_EXTRA]
    eps = [(p, {r.lower() for r in reads}) for _s, p, reads in episodes()]
    import eval_expansion_agent_recalls as ear
    agent = [(r["q"], set(opened)) for r, opened in ear.judged_recalls()]
    keys = []
    if "--skip-bank" not in a:
        solved = set(json.load(open(os.path.join(Q, "headless_solver_sessions_20260926.json"), encoding="utf-8")).values())
        keys = [k for k in (json.loads(l) for l in open(os.path.join(Q, "recall_keys.jsonl"), encoding="utf-8")
                            if l.strip()) if k["id"] not in solved]
    res = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "n_bank": len(bank), "n_ep": len(eps), "n_agent": len(agent),
           "n_keys": len(keys), "variants": {}}
    per_variant_ranks = {}
    for name in names:
        c, s, d = VARIANTS[name]
        tok = make_tokens(c, s, d)
        bm25.read_doc = make_read_doc(tok)
        docs = bm25.read_docs()
        bm25.tokens = tok
        eng = bm25.BM25.from_docs(docs)
        ranks = {}
        for tag, items in (("bank", bank), ("ep", eps), ("agent", agent)):
            ranks[tag] = [rank_of([x for x, _ in eng.score(q, topk=60)], g) for q, g in items]
        for form in (("q_paraphrase", "q_verbatim") if keys else ()):
            ranks[form] = {k["id"]: rank_of([x for x, _ in eng.score(k[form], topk=50)], {k["source_slug"].lower()})
                           for k in keys}
        bm25.tokens = BASE_TOKENS
        bm25.read_doc = BASE_READ_DOC
        per_variant_ranks[name] = ranks
        summ = {"avgdl": round(eng.avgdl, 1), "vocab": len(eng.df)}
        for tag in ("bank", "ep", "agent"):
            rk = ranks[tag]
            if rk:
                summ[tag] = {"hit1": sum(1 for r in rk if r == 1), "hit5": sum(1 for r in rk if 0 < r <= 5),
                             "hit10": sum(1 for r in rk if 0 < r <= 10),
                             "mrr": round(sum(1.0 / r for r in rk if r) / len(rk), 4)}
        for form in (("q_paraphrase", "q_verbatim") if keys else ()):
            for h in ("tune", "test"):
                rk = [r for qid, r in ranks[form].items() if half(qid) == h]
                summ["%s_%s" % (form, h)] = {"hit5": round(sum(1 for r in rk if 0 < r <= 5) / len(rk), 4),
                                             "mrr": round(sum(1.0 / r for r in rk if r) / len(rk), 4), "n": len(rk)}
        res["variants"][name] = summ
        print(name, json.dumps(summ), "(%.0f s)" % (time.time() - t0), flush=True)
    if "base" in per_variant_ranks:
        b = per_variant_ranks["base"]
        res["flips_vs_base"] = {}
        for name, rk in per_variant_ranks.items():
            if name == "base":
                continue
            f = {}
            for tag in ("bank", "ep", "agent"):
                x, y = b[tag], rk[tag]
                f[tag] = {"top5_in": sum(1 for p, q in zip(x, y) if (0 < q <= 5) and not (0 < p <= 5)),
                          "top5_out": sum(1 for p, q in zip(x, y) if (0 < p <= 5) and not (0 < q <= 5)),
                          "better": sum(1 for p, q in zip(x, y) if (q or 99) < (p or 99)),
                          "worse": sum(1 for p, q in zip(x, y) if (q or 99) > (p or 99))}
            for form in (("q_paraphrase", "q_verbatim") if keys else ()):
                x, y = b[form], rk[form]
                f[form] = {"top5_in": sum(1 for qid in x if 0 < y[qid] <= 5 and not 0 < x[qid] <= 5),
                           "top5_out": sum(1 for qid in x if 0 < x[qid] <= 5 and not 0 < y[qid] <= 5)}
            res["flips_vs_base"][name] = f
        print(json.dumps(res["flips_vs_base"]), flush=True)
    res["seconds"] = round(time.time() - t0)
    json.dump(res, open(keep_old(out_path), "w", encoding="utf-8"), indent=1)
    with open(keep_old(os.path.join(Q, "tokenizer_variant_rows.json")), "w", encoding="utf-8") as fh:
        json.dump({n: {t: (v if isinstance(v, list) else v) for t, v in r.items()} for n, r in per_variant_ranks.items()},
                  fh)
    print("wrote", out_path)


if __name__ == "__main__":
    main()
