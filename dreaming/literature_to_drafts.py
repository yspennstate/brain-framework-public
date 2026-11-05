#!/usr/bin/env python3
"""literature_to_drafts.py - turn harvested papers' theorem, lemma, proposition and definition statements
into math-store draft bundles (spec mathbrain-draft-v1), so the store learns the research areas a bank tests
and its notes builder can serve them.

Why: a notes arm can only help on subjects the store holds. Measured 2026-09-02 on a 240-item research
bank: the served notes mentioned an item's retrieval target for 28 percent of items, and nine of the
fourteen largest families had zero coverage - the store had learned one campaign, the bank tested others.

Contract kept: kind concept (statement only, proof_text null) or definition; the statement is a SHORT
attributed excerpt of a mathematical statement (a fact is not copyrightable; long prose is), cut only at a
sentence boundary and never mid-hypothesis - a block that reaches the cap without a boundary is dropped
(an under-hypothesized statement is worse than a missing one); source = arXiv id + abs URL + the paper's
REAL license from licenses.jsonl (literature_license.py) - only CC-BY / CC-BY-SA / CC0 papers are filed;
namespace harvest.arxiv.<domain>; at most 200 entries per bundle and PER_PAPER per paper; the integrator
refuses duplicate keys, so the statement hash is part of every key and logical name.

Usage: python literature_to_drafts.py [--domains mathematics,network_science] [--out DIR]
       [--per-paper 12] [--max-chars 420] [--prefix lit] [--dry]
Env:   BRAIN_LITERATURE_DIR (default ~/.brain/literature), MATHBRAIN_INBOX (default ~/math-brain-inbox/literature-lane)
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys

HOME = os.path.expanduser("~")
LIT = os.environ.get("BRAIN_LITERATURE_DIR") or os.path.join(HOME, ".brain", "literature")
INBOX = os.environ.get("MATHBRAIN_INBOX") or os.path.join(HOME, "math-brain-inbox", "literature-lane")
MANIFEST = os.path.join(LIT, "manifest.jsonl")
TEXT = os.path.join(LIT, "text")
LABEL = r"(Theorem|Lemma|Proposition|Corollary|Definition)"
BLOCK = re.compile(r"(?m)^[ \t]*" + LABEL + r"[ \t]*([0-9]+(?:\.[0-9]+){0,3})[.:]?[ \t]*(\(([^)\n]{0,90})\))?[ \t]*\.?[ \t]*\n?(.*?)(?=\n[ \t]*\n|\n[ \t]*Proof\b|\bProof\.|\n[ \t]*" + LABEL + r"[ \t]*[0-9]|\Z)", re.S)
MATHY = re.compile(r"[=<>≤≥∈∑∫∏√∞≠⊂⊆∀∃λμσρ]|\b(for all|for every|there exist|such that|probability|almost surely|with high probability|if and only if|converges|bounded|inequality|eigenvalue|random|graph|matrix|measure|function)\b", re.I)
GARBAGE = re.compile(r"[�]|(\w) (\w) (\w) (\w) (\w) (\w)|\.{4,}|\bpage\s+\d+\b|©|arXiv:\d", re.I)


def clean(s):
    s = s.replace("­", "").replace("ﬁ", "fi").replace("ﬂ", "fl").replace("ﬀ", "ff").replace("ﬃ", "ffi")
    s = re.sub(r"(?<=[a-z])[-‐‑­]\s+(?=[a-z])", "", s)   # PDF hyphenation: "ac- cording" -> "according"
    s = re.sub(r"\s+", " ", s).strip()
    return s


def sentence_cut(s, max_chars):
    """s if it ends at a sentence boundary within max_chars, else the longest sentence-bounded prefix of at
    least 80 chars, else None (never a mid-hypothesis cut)."""
    if len(s) <= max_chars:
        if s.endswith((".", "!", "?", ")", "]")):
            return s
        return (s + ".") if s.count("(") == s.count(")") else None
    cut = s.rfind(". ", 0, max_chars)
    return s[: cut + 1] if cut >= 80 else None


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:70]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domains", default="mathematics,network_science")
    ap.add_argument("--out", default=INBOX)
    ap.add_argument("--per-paper", type=int, default=12)
    ap.add_argument("--max-chars", type=int, default=420)
    ap.add_argument("--prefix", default="lit", help="bundle file prefix; an integrator's ledger remembers rejected names, so a rebuild needs a new prefix")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    meta = {}
    if os.path.exists(MANIFEST):
        for ln in io.open(MANIFEST, encoding="utf-8"):
            try:
                m = json.loads(ln)
            except ValueError:
                continue
            meta[m.get("arxiv_id") or m.get("id")] = m
    lic = {}
    lp = os.path.join(LIT, "licenses.jsonl")
    if os.path.exists(lp):
        for ln in io.open(lp, encoding="utf-8"):
            try:
                r = json.loads(ln)
                lic[r["arxiv_id"]] = r.get("license", "unknown")
            except ValueError:
                continue

    def open_license(aid):
        L = lic.get(aid, "unknown")
        u = L.upper()
        if u.startswith(("CC-BY-NC", "CC-BY-ND")):
            return None
        return L if u.startswith(("CC-BY-SA", "CC-BY", "CC0")) else None

    entries, seen = [], set()
    stats = {"papers": 0, "papers_open": 0, "papers_no_license": 0, "papers_closed": 0, "blocks": 0, "kept": 0,
             "dropped_cut": 0, "dropped_garbage": 0, "dropped_dup": 0, "dropped_short": 0}
    for dom in a.domains.split(","):
        d = os.path.join(TEXT, dom.strip())
        for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if not fn.endswith(".txt"):
                continue
            aid = fn[len("arxiv_"):-4] if fn.startswith("arxiv_") else fn[:-4]
            m = meta.get(aid, {})
            title = clean(m.get("title", ""))[:90] or aid
            stats["papers"] += 1
            L = open_license(aid)
            if L is None:
                stats["papers_no_license" if aid not in lic else "papers_closed"] += 1
                continue
            stats["papers_open"] += 1
            try:
                t = io.open(os.path.join(d, fn), encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            n_paper = 0
            for bm in BLOCK.finditer(t):
                stats["blocks"] += 1
                label, num, paren, body = bm.group(1), bm.group(2), bm.group(4), bm.group(5)
                s = clean(body)
                if len(s) < 60:
                    stats["dropped_short"] += 1
                    continue
                if GARBAGE.search(s) or not MATHY.search(s):
                    stats["dropped_garbage"] += 1
                    continue
                s2 = sentence_cut(s, a.max_chars)
                if not s2:
                    stats["dropped_cut"] += 1
                    continue
                h = hashlib.sha256(re.sub(r"[^a-z0-9]", "", s2.lower()).encode()).hexdigest()[:16]
                if h in seen:
                    stats["dropped_dup"] += 1
                    continue
                seen.add(h)
                name = (paren or "").strip()
                head = "%s %s%s of arXiv:%s" % (label, num, (" (" + name + ")") if name else "", aid)
                entries.append({
                    "key": ("lit-%s-%s-%s" % (slug(aid), slug(label + "-" + num), h[:6]))[:60],
                    "namespace": "harvest.arxiv." + dom.strip().replace("_", "-"),
                    "logical_name": ("arxiv-%s-%s-%s-%s" % (slug(aid), label.lower(), num.replace(".", "-"), h[:6]))[:80],
                    "kind": "definition" if label == "Definition" else "concept",
                    "title": (head + ": " + title)[:140],
                    "statement": "[%s, '%s'] %s" % (head, title, s2),
                    "proof_text": None,
                    "assumptions": [],
                    "domain": "harvested statement (arXiv %s, %s): %s" % (aid, str(m.get("year") or m.get("published", ""))[:4], dom.strip()),
                    "source": {"source_id": "arxiv:" + aid, "url": "https://arxiv.org/abs/" + aid,
                               "revision": "as-read", "license": L, "excerpt": None},
                })
                stats["kept"] += 1
                n_paper += 1
                if n_paper >= a.per_paper:
                    break
    print("stats", stats, "entries", len(entries))
    if a.dry:
        for e in entries[:4]:
            print("  ", e["title"][:100], "::", e["statement"][:200])
        return 0
    os.makedirs(a.out, exist_ok=True)
    n = 0
    for i in range(0, len(entries), 200):
        n += 1
        payload = {"spec": "mathbrain-draft-v1", "author_agent": os.path.basename(a.out.rstrip("\\/")),
                   "bundle_slug": "%s-arxiv-%03d" % (a.prefix, n), "entries": entries[i:i + 200], "relations": []}
        p = os.path.join(a.out, "bundle_%s_%03d.json" % (a.prefix, n))
        tmp = p + ".part"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    print("wrote %d bundle(s) -> %s" % (n, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
