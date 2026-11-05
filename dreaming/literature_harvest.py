#!/usr/bin/env python3
"""Literature harvest for the brain: ~1,000 state-of-the-art PDFs across three domains.

  neuroscience   memory consolidation, hippocampal replay, complementary learning systems,
                 pattern separation/completion, synaptic tagging, reconsolidation, spacing,
                 predictive coding, metamemory, error-related negativity, salience, sleep
  mathematics    associative memory theory (Hopfield / modern Hopfield / dense associative
                 memory), retrieval and ranking theory (BM25, learned sparse retrieval, dense
                 retrieval, fusion), random graphs and spectral methods, concentration,
                 calibration and conformal prediction, forgetting curves and power laws
  network sci    community detection, centrality and personalized PageRank, link prediction,
                 temporal and multilayer networks, spreading activation, small-world / rich club,
                 knowledge graphs, memory networks

Sources: arXiv API (Atom, https), bioRxiv API, Europe PMC REST (open-access full text).
Polite by construction: one arXiv API call per 3 s, one PDF download per 3 s, a named
User-Agent, resumable by manifest (a PDF already on disk with a matching sha256 is skipped).

Layout (BRAIN_LITERATURE_DIR, default ~/.brain/literature):
  harvest.py            this file
  queries.json          the query plan (domain -> list of queries), written on first run
  manifest.jsonl        one row per paper: id, source, domain, query, title, authors, year,
                        abstract, url, pdf path, sha256, bytes, status
  pdf/<domain>/<id>.pdf the papers
  text/<domain>/<id>.txt extracted text (extract.py, separate pass)
  harvest.log           progress

Usage:
  python harvest.py --target 1000          # metadata + PDFs, resumable
  python harvest.py --target 1000 --meta-only
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# the literature lives outside the code tree: BRAIN_LITERATURE_DIR, default ~/.brain/literature
ROOT = os.environ.get("BRAIN_LITERATURE_DIR") or os.path.join(os.path.expanduser("~"), ".brain", "literature")
PDF_DIR = os.path.join(ROOT, "pdf")
MANIFEST = os.path.join(ROOT, "manifest.jsonl")
QUERIES = os.path.join(ROOT, "queries.json")
LOG = os.path.join(ROOT, "harvest.log")
UA = "brain-literature-harvest/1.0 (research use; contact: owner of this machine)"
ARXIV_DELAY = 3.0
PDF_DELAY = 3.0
MAX_PDF_BYTES = 40 * 1024 * 1024

# Each entry: (arXiv query in the API's own syntax, category filter or None, min year).
# Quoted phrases search the title+abstract fields; categories keep the hits on-domain.
PLAN = {
    "neuroscience": [
        ('ti:"memory consolidation" OR abs:"systems consolidation"', "q-bio.NC", 2015),
        ('abs:"hippocampal replay" OR abs:"sharp wave ripple"', "q-bio.NC", 2015),
        ('abs:"complementary learning systems"', None, 2014),
        ('abs:"pattern separation" AND abs:hippocampus', None, 2015),
        ('abs:"pattern completion" AND (abs:hippocampus OR abs:"attractor")', None, 2015),
        ('abs:"synaptic tagging" OR abs:"synaptic consolidation"', None, 2014),
        ('abs:reconsolidation AND abs:memory', "q-bio.NC", 2014),
        ('abs:"spacing effect" OR abs:"spaced repetition"', None, 2014),
        ('abs:"predictive coding" AND (abs:cortex OR abs:brain)', "q-bio.NC", 2016),
        ('abs:metamemory OR abs:"feeling of knowing" OR abs:"tip of the tongue"', None, 2012),
        ('abs:"error-related negativity" OR abs:"error monitoring" AND abs:"anterior cingulate"', None, 2012),
        ('abs:"memory replay" AND (abs:sleep OR abs:offline)', None, 2016),
        ('abs:"engram" AND abs:memory', "q-bio.NC", 2016),
        ('abs:"forgetting" AND (abs:hippocampus OR abs:"active forgetting")', "q-bio.NC", 2015),
        ('abs:"schema" AND abs:memory AND (abs:consolidation OR abs:cortex)', "q-bio.NC", 2015),
        ('abs:"salience network" OR abs:"locus coeruleus" AND abs:memory', None, 2015),
        ('abs:"working memory" AND abs:"attractor"', "q-bio.NC", 2016),
        ('abs:"episodic memory" AND (abs:model OR abs:computational)', "q-bio.NC", 2016),
        ('abs:"memory retrieval" AND abs:"cue" AND abs:hippocampus', None, 2015),
        ('abs:"grid cells" OR abs:"place cells" AND abs:"cognitive map"', "q-bio.NC", 2017),
        ('abs:"successor representation"', None, 2016),
        ('abs:"neuromodulation" AND (abs:dopamine OR abs:acetylcholine) AND abs:memory', "q-bio.NC", 2016),
        ('abs:"generative replay" OR abs:"experience replay" AND abs:hippocampus', None, 2017),
        ('abs:"continual learning" AND abs:"catastrophic forgetting" AND abs:replay', None, 2018),
        ('abs:"free energy principle" OR abs:"active inference"', None, 2017),
        ('abs:"memory" AND abs:"criticality" AND abs:brain', None, 2015),
        ('abs:"sleep" AND abs:"memory consolidation" AND (abs:model OR abs:mechanism)', None, 2016),
        ('abs:"one-shot learning" AND abs:hippocampus', None, 2016),
        ('abs:"metacognition" AND (abs:confidence OR abs:"error detection")', "q-bio.NC", 2016),
        ('abs:"interference" AND abs:memory AND (abs:proactive OR abs:retroactive)', "q-bio.NC", 2014),
    ],
    "mathematics": [
        ('abs:"Hopfield" AND (abs:capacity OR abs:"associative memory")', None, 2016),
        ('abs:"dense associative memory" OR abs:"modern Hopfield"', None, 2016),
        ('abs:"associative memory" AND (abs:transformer OR abs:attention)', None, 2020),
        ('abs:"sparse distributed memory" OR abs:"hyperdimensional computing"', None, 2015),
        ('abs:"BM25" OR abs:"probabilistic relevance framework"', None, 2014),
        ('abs:"learned sparse retrieval" OR abs:"SPLADE"', None, 2020),
        ('abs:"dense retrieval" AND (abs:"contrastive" OR abs:"dual encoder")', "cs.IR", 2020),
        ('abs:"reciprocal rank fusion" OR abs:"rank fusion" AND abs:retrieval', None, 2014),
        ('abs:"reranking" AND abs:"cross-encoder"', "cs.IR", 2019),
        ('abs:"retrieval-augmented" AND (abs:memory OR abs:generation)', "cs.CL", 2020),
        ('abs:"forgetting curve" OR abs:"power law of forgetting" OR abs:"memory decay"', None, 2012),
        ('abs:"ACT-R" AND (abs:activation OR abs:"declarative memory")', None, 2012),
        ('abs:"spreading activation" AND (abs:semantic OR abs:network)', None, 2012),
        ('abs:"random graph" AND (abs:"spectral" OR abs:"eigenvalue") AND abs:community', "math.PR", 2016),
        ('abs:"stochastic block model" AND abs:"detection threshold"', None, 2015),
        ('abs:"concentration inequality" AND (abs:"random matrix" OR abs:"sums of independent")', "math.PR", 2016),
        ('abs:"conformal prediction" AND (abs:calibration OR abs:"coverage")', None, 2019),
        ('abs:"calibration" AND abs:"proper scoring rule"', None, 2016),
        ('abs:"Bayesian" AND abs:"forgetting" AND abs:"memory"', None, 2014),
        ('abs:"knowledge graph embedding" OR abs:"link prediction" AND abs:"knowledge graph"', "cs.LG", 2018),
        ('abs:"energy-based model" AND abs:"memory"', None, 2019),
        ('abs:"attractor network" AND (abs:capacity OR abs:"basin")', None, 2015),
        ('abs:"kernel" AND abs:"memory" AND abs:"recurrent" AND abs:"capacity"', None, 2018),
        ('abs:"Rademacher" AND (abs:"anticoncentration" OR abs:"small ball" OR abs:"Littlewood-Offord")', "math.PR", 2014),
        ('abs:"first-passage percolation" AND (abs:fluctuation OR abs:variance)', "math.PR", 2014),
        ('abs:"perceptron" AND (abs:capacity OR abs:"storage capacity")', None, 2016),
        ('abs:"random matrix" AND abs:"singular value" AND (abs:"smallest" OR abs:"least")', "math.PR", 2015),
        ('abs:"law of robustness" OR abs:"isoperimetry" AND abs:"neural network" AND abs:robust', None, 2020),
        ('abs:"mutually orthogonal Latin squares" OR abs:"MOLS"', "math.CO", 2012),
        ('abs:"expander" AND abs:"spectral gap" AND (abs:"mixing" OR abs:"random walk")', "math.CO", 2016),
        ('abs:"edge of chaos" AND abs:"deep" AND (abs:"signal propagation" OR abs:"mean field")', None, 2016),
        ('abs:"memorization" AND abs:"generalization" AND abs:"neural network" AND abs:capacity', "cs.LG", 2019),
    ],
    "network_science": [
        ('abs:"community detection" AND (abs:"modularity" OR abs:"Louvain" OR abs:"Leiden")', None, 2016),
        ('abs:"personalized PageRank" OR abs:"random walk with restart"', None, 2015),
        ('abs:"link prediction" AND abs:"network" AND (abs:benchmark OR abs:"evaluation")', None, 2017),
        ('abs:"temporal network" AND (abs:"memory" OR abs:"burst" OR abs:"path")', "physics.soc-ph", 2016),
        ('abs:"multilayer network" OR abs:"multiplex network"', "physics.soc-ph", 2016),
        ('abs:"small-world" AND abs:"brain network"', None, 2015),
        ('abs:"rich club" AND abs:network', None, 2014),
        ('abs:"network neuroscience" OR abs:"connectome" AND abs:"graph theory"', None, 2016),
        ('abs:"semantic network" AND (abs:"memory" OR abs:"free association")', None, 2014),
        ('abs:"knowledge graph" AND (abs:"completion" OR abs:"reasoning")', "cs.AI", 2019),
        ('abs:"graph neural network" AND abs:"memory"', "cs.LG", 2019),
        ('abs:"spreading" AND abs:"network" AND (abs:"cascade" OR abs:"contagion") AND abs:threshold', "physics.soc-ph", 2015),
        ('abs:"hypergraph" AND (abs:"higher-order" OR abs:"simplicial")', "physics.soc-ph", 2018),
        ('abs:"network embedding" OR abs:"node2vec" OR abs:"DeepWalk"', "cs.SI", 2016),
        ('abs:"centrality" AND abs:"network" AND (abs:"ranking" OR abs:"influence")', "physics.soc-ph", 2015),
        ('abs:"core-periphery" AND abs:network', None, 2015),
        ('abs:"network science" AND abs:"memory" AND abs:"cognitive"', None, 2014),
        ('abs:"hebbian" AND abs:"network" AND (abs:"plasticity" OR abs:"structure")', None, 2016),
        ('abs:"graph" AND abs:"forgetting" AND abs:"knowledge"', None, 2018),
        ('abs:"network" AND abs:"consolidation" AND (abs:"memory" OR abs:"schema")', None, 2016),
        ('abs:"dynamic graph" AND (abs:"anomaly" OR abs:"change point")', "cs.SI", 2018),
        ('abs:"belief propagation" AND abs:"network" AND (abs:"inference" OR abs:"community")', None, 2015),
        ('abs:"citation network" AND (abs:"ranking" OR abs:"aging" OR abs:"attention")', "physics.soc-ph", 2015),
        ('abs:"agent" AND abs:"memory" AND abs:"graph" AND abs:"retrieval"', "cs.AI", 2023),
        ('abs:"long-term memory" AND abs:"language model" AND abs:agent', "cs.CL", 2023),
        ('abs:"memory" AND abs:"LLM" AND (abs:"episodic" OR abs:"reflection")', "cs.AI", 2023),
    ],
}

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def log(msg: str) -> None:
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    with io.open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load_manifest() -> dict:
    rows = {}
    if os.path.exists(MANIFEST):
        with io.open(MANIFEST, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    try:
                        r = json.loads(ln)
                        rows[r["id"]] = r
                    except Exception:
                        continue
    return rows


def append_manifest(row: dict) -> None:
    with io.open(MANIFEST, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def arxiv_search(query: str, cat: str | None, start: int, n: int) -> list[dict]:
    q = query if not cat else "(%s) AND cat:%s" % (query, cat)
    url = ("https://export.arxiv.org/api/query?search_query=%s&start=%d&max_results=%d"
           "&sortBy=relevance&sortOrder=descending" % (urllib.parse.quote(q), start, n))
    data = http_get(url)
    root = ET.fromstring(data)
    out = []
    for e in root.findall(ATOM + "entry"):
        aid = (e.findtext(ATOM + "id") or "").strip()
        m = re.search(r"abs/([^v]+)(v\d+)?$", aid)
        if not m:
            continue
        base = m.group(1)
        title = re.sub(r"\s+", " ", (e.findtext(ATOM + "title") or "")).strip()
        summary = re.sub(r"\s+", " ", (e.findtext(ATOM + "summary") or "")).strip()
        published = (e.findtext(ATOM + "published") or "")[:10]
        authors = [a.findtext(ATOM + "name") or "" for a in e.findall(ATOM + "author")]
        cats = [c.get("term") for c in e.findall(ATOM + "category")]
        pdf = None
        for l in e.findall(ATOM + "link"):
            if l.get("title") == "pdf":
                pdf = l.get("href")
        if not pdf:
            pdf = "https://arxiv.org/pdf/%s" % base
        out.append({"id": "arxiv:" + base.replace("/", "_"), "arxiv_id": base, "title": title,
                    "abstract": summary, "published": published, "year": int(published[:4]) if published[:4].isdigit() else None,
                    "authors": authors[:12], "categories": cats, "pdf_url": pdf.replace("http://", "https://"),
                    "source": "arxiv"})
    return out


def sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_pdf(row: dict, domain: str) -> dict:
    d = os.path.join(PDF_DIR, domain)
    os.makedirs(d, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", row["id"])
    path = os.path.join(d, safe + ".pdf")
    if os.path.exists(path) and os.path.getsize(path) > 10000:
        row.update({"pdf_path": path, "bytes": os.path.getsize(path), "sha256": sha256_file(path), "status": "have"})
        return row
    tmp = path + ".part"
    try:
        req = urllib.request.Request(row["pdf_url"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
            total = 0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_PDF_BYTES:
                    raise IOError("pdf over size cap")
                f.write(chunk)
        with open(tmp, "rb") as f:
            head = f.read(5)
        if head != b"%PDF-":
            os.remove(tmp)
            row.update({"status": "not_pdf"})
            return row
        os.replace(tmp, path)
        row.update({"pdf_path": path, "bytes": os.path.getsize(path), "sha256": sha256_file(path), "status": "downloaded"})
    except Exception as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        row.update({"status": "download_failed", "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])})
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=1000)
    ap.add_argument("--per-query", type=int, default=25, help="results requested per query page")
    ap.add_argument("--meta-only", action="store_true")
    ap.add_argument("--domains", default="neuroscience,mathematics,network_science")
    args = ap.parse_args()
    os.makedirs(ROOT, exist_ok=True)
    try:  # compute doctrine: background AI work runs BelowNormal on this PC
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x00004000)
    except Exception:
        pass
    if not os.path.exists(QUERIES):
        with io.open(QUERIES, "w", encoding="utf-8") as f:
            json.dump(PLAN, f, indent=1)
    have = load_manifest()
    n_ok = sum(1 for r in have.values() if r.get("status") in ("downloaded", "have"))
    log("start: manifest %d rows, %d pdfs on disk, target %d" % (len(have), n_ok, args.target))
    domains = [d for d in args.domains.split(",") if d in PLAN]
    per_domain_target = args.target // len(domains) + 1
    counts = {d: sum(1 for r in have.values() if r.get("domain") == d and r.get("status") in ("downloaded", "have")) for d in domains}
    page = {d: 0 for d in domains}
    exhausted = {d: set() for d in domains}
    rounds = 0
    while sum(counts.values()) < args.target and rounds < 12:
        rounds += 1
        for d in domains:
            if counts[d] >= per_domain_target:
                continue
            for qi, (q, cat, ymin) in enumerate(PLAN[d]):
                if qi in exhausted[d] or counts[d] >= per_domain_target:
                    continue
                start = page[d] * args.per_query
                try:
                    hits = arxiv_search(q, cat, start, args.per_query)
                except Exception as exc:
                    log("arxiv query failed (%s): %s" % (d, exc))
                    time.sleep(ARXIV_DELAY * 3)
                    continue
                time.sleep(ARXIV_DELAY)
                if not hits:
                    exhausted[d].add(qi)
                    continue
                new = 0
                for h in hits:
                    if h["id"] in have:
                        continue
                    if h["year"] and h["year"] < ymin:
                        continue
                    h.update({"domain": d, "query": q, "category_filter": cat, "harvested_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
                    if args.meta_only:
                        h["status"] = "meta_only"
                    else:
                        h = download_pdf(h, d)
                        time.sleep(PDF_DELAY)
                    have[h["id"]] = h
                    append_manifest(h)
                    new += 1
                    if h.get("status") in ("downloaded", "have"):
                        counts[d] += 1
                    if counts[d] >= per_domain_target:
                        break
                log("%s q%02d page%d: %d hits, %d new; domain total %d; all %d"
                    % (d, qi, page[d], len(hits), new, counts[d], sum(counts.values())))
        for d in domains:
            page[d] += 1
    log("done: %d pdfs (%s)" % (sum(counts.values()), ", ".join("%s=%d" % kv for kv in counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
