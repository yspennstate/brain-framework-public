#!/usr/bin/env python3
"""literature_license.py - record each harvested arXiv paper's actual license from its abs page.

A math-store draft integrator admits open licenses only (CC-BY, CC-BY-SA, CC0, ...). The arXiv API feed
does not carry the license; the abs page does (a link to creativecommons.org, or to arXiv's non-exclusive
distribution license). This fetches abs pages at the polite pace, resumably, and writes
<literature dir>/licenses.jsonl: {"arxiv_id", "license", "license_url", "fetched"}. Non-open papers are
recorded too, so the draft builder can exclude them and say how many it excluded. Measured on one harvest
(2026-09-02, 157 papers): 64 percent arXiv non-exclusive, 26 percent CC-BY / CC-BY-SA, 10 percent NC or ND.

Usage: python literature_license.py [--ids-from bundles|manifest] [--domains a,b] [--limit N]
Env:   BRAIN_LITERATURE_DIR (default ~/.brain/literature), MATHBRAIN_INBOX (bundle dir for --ids-from bundles)
"""
import argparse
import glob
import io
import json
import os
import re
import sys
import time
import urllib.request

HOME = os.path.expanduser("~")
LIT = os.environ.get("BRAIN_LITERATURE_DIR") or os.path.join(HOME, ".brain", "literature")
INBOX = os.environ.get("MATHBRAIN_INBOX") or os.path.join(HOME, "math-brain-inbox", "literature-lane")
OUT = os.path.join(LIT, "licenses.jsonl")
UA = "brain-literature-license-check/1.0 (research use)"
PACE_S = 3.0
LICENSES = [
    (r"creativecommons\.org/licenses/by-sa/([0-9.]+)", "CC-BY-SA-{v}"),
    (r"creativecommons\.org/licenses/by-nc-sa/([0-9.]+)", "CC-BY-NC-SA-{v}"),
    (r"creativecommons\.org/licenses/by-nc-nd/([0-9.]+)", "CC-BY-NC-ND-{v}"),
    (r"creativecommons\.org/licenses/by-nc/([0-9.]+)", "CC-BY-NC-{v}"),
    (r"creativecommons\.org/licenses/by-nd/([0-9.]+)", "CC-BY-ND-{v}"),
    (r"creativecommons\.org/licenses/by/([0-9.]+)", "CC-BY-{v}"),
    (r"creativecommons\.org/publicdomain/zero/([0-9.]+)", "CC0-{v}"),
    (r"arxiv\.org/licenses/nonexclusive-distrib/([0-9.]+)", "arxiv-nonexclusive-distrib-{v}"),
    (r"arxiv\.org/licenses/assumed-1991-2003", "arxiv-assumed-1991-2003"),
]


def parse_license(html):
    for pat, fmt in LICENSES:
        m = re.search(pat, html)
        if m:
            v = m.group(1) if m.groups() else ""
            return fmt.format(v=v), m.group(0)
    return "unknown", ""


def ids_from_bundles():
    ids = set()
    for p in glob.glob(os.path.join(INBOX, "bundle_*.json")):
        for e in json.load(io.open(p, encoding="utf-8"))["entries"]:
            sid = e.get("source", {}).get("source_id", "")
            if sid.startswith("arxiv:"):
                ids.add(sid.split(":", 1)[1])
    return sorted(ids)


def ids_from_manifest(domains):
    ids = set()
    for ln in io.open(os.path.join(LIT, "manifest.jsonl"), encoding="utf-8"):
        try:
            m = json.loads(ln)
        except ValueError:
            continue
        if not domains or m.get("domain") in domains:
            ids.add(m.get("arxiv_id") or m.get("id", "").split(":")[-1])
    return sorted(i for i in ids if i)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids-from", default="manifest", choices=["bundles", "manifest"])
    ap.add_argument("--domains", default="")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    done = {}
    if os.path.exists(OUT):
        for ln in io.open(OUT, encoding="utf-8"):
            try:
                r = json.loads(ln)
                done[r["arxiv_id"]] = r
            except ValueError:
                continue
    ids = ids_from_bundles() if a.ids_from == "bundles" else ids_from_manifest(set(d for d in a.domains.split(",") if d))
    todo = [i for i in ids if i not in done]
    if a.limit:
        todo = todo[: a.limit]
    print("ids %d, already %d, to fetch %d" % (len(ids), len(ids) - len(todo), len(todo)), flush=True)
    os.makedirs(LIT, exist_ok=True)
    n_ok = 0
    with io.open(OUT, "a", encoding="utf-8") as out:
        for k, aid in enumerate(todo):
            url = "https://arxiv.org/abs/" + aid
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=40) as r:
                    html = r.read(400000).decode("utf-8", "replace")
                lic, lurl = parse_license(html)
                out.write(json.dumps({"arxiv_id": aid, "license": lic, "license_url": lurl,
                                      "fetched": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")
                out.flush()
                n_ok += 1
            except Exception as exc:
                out.write(json.dumps({"arxiv_id": aid, "license": "fetch-error", "license_url": "",
                                      "error": str(exc)[:120], "fetched": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")
                out.flush()
            if (k + 1) % 25 == 0:
                print("  ... %d/%d" % (k + 1, len(todo)), flush=True)
            time.sleep(PACE_S)
    print("fetched %d; licenses file %s" % (n_ok, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
