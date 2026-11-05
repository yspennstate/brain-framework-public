"""source_check_0810.py - are the 10 Aug batch's concept claims what their sources say? (account03/B12,
B12-R34-FIRST-RECORD-IMMUTABILITY-NOT-AUTHENTICITY-20260928; registered 28 Sep 10:2x in THREE_DAY_RUN_2026-09-27.md;
claude-825b1305)

The first-record rule shows what the 10 Aug ingest wrote and that nothing changed it since; it cannot show that what it
wrote is right. For the concept claims of that batch ("harvested overview (Wikipedia lead)" and the one "QA-corrected"
card) there are two sources the ingest did not write:
  1. the harvested artifact of the claim each one replaced: the replaced node's metadata names a content-addressed file
     (artifact_path, artifact_digest) in the store's object folder, written before 10 Aug; its sha256 is checked first;
  2. the lead of the Wikipedia article the replaced node is titled with, as the Wikipedia API's plain-text intro gives
     it today (the article may have moved since August; a miss there alone is a source change, not a verdict).
A statement is FOUND in a source when, whitespace collapsed and Unicode normalised, it is contained in it; otherwise the
longest run of the statement's words found in order and the share of its word 3-grams found are reported. The lemmas
and edges of the batch are read by hand against the campaign ledger; this script covers the concept rows only.

    python source_check_0810.py STORE OBJECTS_ROOT OUT_JSON [--wiki-cache CACHE_JSON] [--offline]
STORE may be a fixture copy; the batch is every claim created on 2026-08-10 whose domain names a Wikipedia lead or a
QA correction. With --offline the Wikipedia leads come from CACHE_JSON only (a fixture run must not refetch).
"""
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

UA = "brain-source-check/1.0 (research; read-only)"
API = "https://en.wikipedia.org/w/api.php"


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip()


def words(s):
    return re.findall(r"[0-9A-Za-zÀ-ɏ]+", norm(s).casefold())


def match(stmt, src):
    a, b = norm(stmt), norm(src)
    if not a:
        return {"found": False, "why": "empty statement"}
    if a in b:
        return {"found": True, "how": "contained"}
    wa, wb = words(stmt), words(src)
    grams = lambda w: {tuple(w[i:i + 3]) for i in range(max(0, len(w) - 2))}
    ga, gb = grams(wa), grams(wb)
    share = round(len(ga & gb) / len(ga), 3) if ga else 0.0
    j = " ".join(wb)
    run = 0
    for k in range(len(wa), 0, -1):              # longest prefix of the statement's words found in order
        if " ".join(wa[:k]) in j:
            run = k
            break
    return {"found": False, "trigram_share": share, "prefix_words_found": run, "statement_words": len(wa)}


def wiki_leads(titles, cache_path, offline):
    cache = {}
    if cache_path and os.path.exists(cache_path):
        cache = json.load(io.open(cache_path, encoding="utf-8"))
    need = [t for t in titles if t not in cache]
    if need and offline:
        raise SystemExit("offline and %d titles are not cached" % len(need))
    for i in range(0, len(need), 10):
        batch = need[i:i + 10]
        q = urllib.parse.urlencode({"action": "query", "format": "json", "prop": "extracts", "exintro": 1,
                                    "explaintext": 1, "redirects": 1, "exlimit": "max", "titles": "|".join(batch)})
        req = urllib.request.Request(API + "?" + q, headers={"User-Agent": UA})
        data = json.load(urllib.request.urlopen(req, timeout=30))
        redir = {r["from"]: r["to"] for r in data.get("query", {}).get("redirects", [])}
        normd = {r["from"]: r["to"] for r in data.get("query", {}).get("normalized", [])}
        pages = {p.get("title"): p for p in data.get("query", {}).get("pages", {}).values()}
        for t in batch:
            name = redir.get(normd.get(t, t), normd.get(t, t))
            p = pages.get(name) or {}
            cache[t] = {"resolved_title": name, "extract": p.get("extract"), "pageid": p.get("pageid"),
                        "fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        time.sleep(1)
    if cache_path and not offline:
        json.dump(cache, io.open(cache_path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return cache


def main():
    store, objroot, out = sys.argv[1:4]
    cache_path = sys.argv[sys.argv.index("--wiki-cache") + 1] if "--wiki-cache" in sys.argv else None
    offline = "--offline" in sys.argv
    db = sqlite3.connect("file:%s?mode=ro" % store.replace("\\", "/"), uri=True)
    db.row_factory = sqlite3.Row
    rows = []
    for r in db.execute("SELECT id, payload_json, created_at FROM claims WHERE created_at LIKE '2026-08-10%'"):
        p = json.loads(r["payload_json"])
        dom = str(p.get("domain") or "")
        if "Wikipedia lead" not in dom and "QA-corrected" not in dom:
            continue
        node = db.execute("SELECT title, body, metadata_json FROM nodes WHERE id = ?", (r["id"],)).fetchone()
        meta = json.loads(node["metadata_json"]) if node else {}
        rep = meta.get("replaces")
        old = db.execute("SELECT payload_json FROM claims WHERE id = ?", (rep,)).fetchone() if rep else None
        oldn = db.execute("SELECT title, metadata_json FROM nodes WHERE id = ?", (rep,)).fetchone() if rep else None
        om = json.loads(oldn["metadata_json"]) if oldn else {}
        art, art_ok, art_text = om.get("artifact_path"), None, None
        if art:
            fp = os.path.join(objroot, art.replace("/", os.sep))
            if os.path.exists(fp):
                blob = open(fp, "rb").read()
                art_ok = ("sha256:" + hashlib.sha256(blob).hexdigest()) == om.get("artifact_digest")
                art_text = blob.decode("utf-8", "replace")
        rows.append({"id": r["id"], "domain": dom, "status": p.get("status"), "statement": p.get("statement"),
                     "node_body_equals_statement": bool(node) and node["body"] == p.get("statement"),
                     "replaces": rep, "replaced_title": oldn["title"] if oldn else None,
                     "replaced_statement": json.loads(old["payload_json"]).get("statement") if old else None,
                     "artifact_path": art, "artifact_digest_ok": art_ok, "_art": art_text})
    titles = sorted({x["replaced_title"] for x in rows if x["replaced_title"]})
    leads = wiki_leads(titles, cache_path, offline)
    for x in rows:
        art = x.pop("_art")
        x["in_artifact"] = match(x["statement"], art) if art else {"found": False, "why": "no artifact"}
        x["replaced_in_artifact"] = match(x["replaced_statement"], art) if (art and x["replaced_statement"]) else None
        lead = (leads.get(x["replaced_title"]) or {}).get("extract") if x["replaced_title"] else None
        x["wikipedia_title"] = (leads.get(x["replaced_title"]) or {}).get("resolved_title")
        x["in_wikipedia_lead_today"] = match(x["statement"], lead) if lead else {"found": False, "why": "no lead"}
        x["statement_equals_replaced"] = x["statement"] == x["replaced_statement"]
    res = {"store": store, "rows": len(rows),
           "artifact_digest_ok": sum(1 for x in rows if x["artifact_digest_ok"]),
           "found_in_artifact": sum(1 for x in rows if x["in_artifact"].get("found")),
           "found_in_wikipedia_lead_today": sum(1 for x in rows if x["in_wikipedia_lead_today"].get("found")),
           "found_in_neither": [x["id"] for x in rows if not x["in_artifact"].get("found")
                                and not x["in_wikipedia_lead_today"].get("found")],
           "detail": rows}
    json.dump(res, io.open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(json.dumps({k: v for k, v in res.items() if k != "detail"}, indent=1))


if __name__ == "__main__":
    main()
