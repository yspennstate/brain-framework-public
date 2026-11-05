#!/usr/bin/env python3
"""build_near_duplicate_report.py - pattern separation for the corpus: which memories are near-twins?

Measured 2026-09-02 on the live sidecar (14,843 memories, 384-d): 2.6 percent of memories have a nearest
neighbour above cosine 0.95 and 26 percent above 0.90 (audit waves, watchdog quiet entries, job readmes
filed beside their memory). Dense neighbourhoods are where retrieval saturates - the Hopfield-style
iteration experiment collapsed for exactly this reason - and where a stale twin can outrank its correction.

This builder does NOT edit any memory. It writes a report of candidate pairs above a cosine threshold
that are not already linked by superseded_by in either direction, with a proposed direction (older ->
newer, by the date in the slug, else by file mtime) for a dream or a person to accept. Refreshed by the
nightly dream after the embedding sidecar; read-only over the corpus; fail-open (an unreadable sidecar
writes a one-line report saying so).

Output:  <brain views dir>/NEAR_DUPLICATES.md and NEAR_DUPLICATES.json
Usage:   python build_near_duplicate_report.py [--threshold 0.97] [--max 400]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
POINTER = os.path.join(HERE, ".embedding_current.json")
_BRAIN_SLUG = HOME.replace(":", "-").replace("\\", "-").replace("/", "-")
MEM = os.environ.get("BRAIN_MEM_DIR") or os.path.join(HOME, ".claude", "projects", _BRAIN_SLUG, "memory")
VIEWS = os.environ.get("BRAIN_VIEWS_DIR") or os.path.join(HOME, ".brain", "views")
DATE_RE = re.compile(r"(20\d\d)[_-](\d\d)[_-](\d\d)")


def slug_date(slug: str):
    for m in reversed(DATE_RE.findall(slug)):
        try:
            return time.mktime(time.strptime("-".join(m), "%Y-%m-%d"))
        except ValueError:
            continue
    return None


def superseded_by(path: str):
    try:
        # the frontmatter sits at the top; 6000 bytes covers the longest description lines in the corpus
        head = io.open(path, encoding="utf-8", errors="replace").read(6000)
        if "\n---" in head:
            head = head[: head.index("\n---") + 4]
    except OSError:
        return None
    m = re.search(r"^\s*superseded_by:\s*([A-Za-z0-9_-]+)", head, re.M)
    return m.group(1) if m else None


def filing_stamp(path: str):
    """The memory's filing stamp (supersession.memory_stamp over its closed frontmatter); {} on any error.

    2026-09-22 (round 5 W4, F54): the proposed direction was the slug date, so two near-twins filed the same day
    were ordered by whichever the cosine loop met first - the cause of all nine inverted verdicts of the 200-pair
    owner-order judge. `created_at` (written by brain.py remember since that day) orders them exactly; a pair
    without it on both sides keeps the old key and, when that key cannot tell the two apart, says so."""
    try:
        import supersession
        return supersession.memory_stamp(supersession.frontmatter_block(path))
    except Exception:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.97)
    ap.add_argument("--max", type=int, default=400)
    a = ap.parse_args()
    os.makedirs(VIEWS, exist_ok=True)
    out_md = os.path.join(VIEWS, "NEAR_DUPLICATES.md")
    out_json = os.path.join(VIEWS, "NEAR_DUPLICATES.json")
    try:
        import numpy as np
        ptr = json.load(io.open(POINTER, encoding="utf-8"))
        z = np.load(ptr["sidecar"], allow_pickle=True)
        slugs = [str(s) for s in z["slugs"]]
        V = z["vecs"].astype(np.float32)
    except Exception as exc:
        io.open(out_md, "w", encoding="utf-8").write(
            "# Near-duplicate report\n\nsidecar unreadable (%s: %s); nothing measured.\n" % (type(exc).__name__, exc))
        print("near-duplicates: sidecar unreadable", exc)
        return 0
    V /= (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)
    n = V.shape[0]
    pairs = []
    B = 1024
    for i in range(0, n, B):
        S = V[i:i + B] @ V.T
        for r in range(S.shape[0]):
            S[r, i + r] = -1.0
        rows, cols = np.where(S >= a.threshold)
        for r, c in zip(rows, cols):
            gi = i + r
            if gi < c:
                pairs.append((float(S[r, c]), slugs[gi], slugs[c]))
    pairs.sort(reverse=True)
    rows = []
    linked = 0
    for cos, s1, s2 in pairs:
        p1, p2 = os.path.join(MEM, s1 + ".md"), os.path.join(MEM, s2 + ".md")
        if not (os.path.exists(p1) and os.path.exists(p2)):
            continue
        sb1, sb2 = superseded_by(p1), superseded_by(p2)
        if sb1 == s2 or sb2 == s1:
            linked += 1
            continue
        st1, st2 = filing_stamp(p1), filing_stamp(p2)
        if st1.get("at") is not None and st2.get("at") is not None:
            older, newer = (s1, s2) if st1["at"] <= st2["at"] else (s2, s1)
            order = "created_at"
        else:
            d1 = slug_date(s1) or os.path.getmtime(p1)
            d2 = slug_date(s2) or os.path.getmtime(p2)
            older, newer = (s1, s2) if d1 <= d2 else (s2, s1)
            order = "same_day_unordered" if d1 == d2 else "date"
        rows.append({"cosine": round(cos, 4), "older": older, "newer": newer, "order": order,
                     "proposal": "%s superseded_by %s" % (older, newer)})
        if len(rows) >= a.max:
            break
    fam = {}
    for r in rows:
        key = re.sub(r"_?20\d\d[_-]\d\d[_-]\d\d.*$", "", r["older"])[:40]
        fam[key] = fam.get(key, 0) + 1
    with io.open(out_json, "w", encoding="utf-8") as f:
        json.dump({"built": time.strftime("%Y-%m-%dT%H:%M:%S"), "threshold": a.threshold, "n_memories": n,
                   "pairs_above_threshold": len(pairs), "already_linked": linked, "proposals": rows}, f, indent=1)
    lines = ["# Near-duplicate report (pattern separation)", "",
             "Built %s over %d embedded memories; cosine >= %.2f; %d pairs above threshold, %d already linked by "
             "superseded_by, %d proposals below. Nothing here has been applied: a proposal is accepted by adding "
             "`superseded_by: <newer>` to the OLDER memory's frontmatter (`brain.py supersede <older> --by <newer>`; "
             "`supersede-part --clause` when only one clause moved), and only when the "
             "newer one really replaces the older; twins that are distinct records (two watchdog windows, two audit findings) "
             "stay as they are." % (time.strftime("%Y-%m-%d %H:%M"), n, a.threshold, len(pairs), linked, len(rows)), ""]
    if fam:
        lines.append("Families with the most proposals: " + ", ".join("%s (%d)" % kv for kv in sorted(fam.items(), key=lambda kv: -kv[1])[:8]))
        lines.append("")
    for r in rows:
        lines.append("- %.4f  `%s`  ->  `%s`%s" % (
            r["cosine"], r["older"], r["newer"],
            "  (same day, filing order not recorded - check the direction)"
            if r.get("order") == "same_day_unordered" else ""))
    with io.open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("near-duplicates: %d memories, %d pairs >= %.2f, %d already linked, %d proposals -> %s" % (
        n, len(pairs), a.threshold, linked, len(rows), out_md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
