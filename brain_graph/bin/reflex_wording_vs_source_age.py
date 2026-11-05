"""reflex_wording_vs_source_age.py - which reflexes' wording is older than the memory they cite? (28 Sep 10:0x,
claude-825b1305)

A reflex copies an order's wording when it is compiled. When the order moves, its memory is often revised in place
rather than superseded (the GPU policy moved four times in two weeks; its reflex kept the older wording for days - the
scar-fire study, 28 Sep). This dates each reflex's last wording change from the reflex-file backups and the live file
(instincts.tsv and its .bak copies: gut and sting per reflex id; a backup's time is its file time, the time of the write
before the copy) and compares it with the modified stamp of each memory the reflex cites in its source. A reflex whose
source memory was modified after its own wording last changed is listed, oldest gap first. Nothing is changed.

    python reflex_wording_vs_source_age.py OUT_JSON
"""
import glob
import io
import json
import os
import re
import sys
from datetime import datetime

DR = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")
MEM = os.path.join(os.path.expanduser("~"), ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")), "memory")
SLUG = re.compile(r"\b([a-z][a-z0-9_]{12,}_20\d\d_\d\d_\d\d)\b")
MOD = re.compile(r"^\s*modified:\s*['\"]?(20\d\d-\d\d-\d\dT[\d:.]+Z?)", re.M)


def tsv_texts(path):
    out = {}
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            next(fh, None)
            for ln in fh:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 7:
                    out[p[0]] = (p[4], p[5], p[6])
    except OSError:
        pass
    return out


def mem_modified(slug):
    p = os.path.join(MEM, slug + ".md")
    try:
        head = io.open(p, encoding="utf-8", errors="replace").read(3000)
    except OSError:
        return None
    m = MOD.search(head)
    if m:
        try:
            return datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return os.path.getmtime(p)


def main():
    live = os.path.join(DR, "instincts.tsv")
    versions = sorted(((os.path.getmtime(p), p) for p in glob.glob(os.path.join(DR, "instincts.tsv.*"))
                       if not p.endswith((".tmp", ".lock"))), key=lambda x: x[0])
    versions.append((os.path.getmtime(live), live))
    first_seen, last_change, prev = {}, {}, {}
    for t, p in versions:
        texts = tsv_texts(p)
        for rid, (gut, sting, source) in texts.items():
            if rid not in first_seen:
                first_seen[rid] = t
                last_change[rid] = t
            elif prev.get(rid) != (gut, sting):
                last_change[rid] = t
            prev[rid] = (gut, sting)
    now_texts = tsv_texts(live)
    rows = []
    for rid, (gut, sting, source) in now_texts.items():
        for slug in set(SLUG.findall(source)):
            m = mem_modified(slug)
            if m is None or rid not in last_change:
                continue
            if m > last_change[rid] + 3600:
                rows.append({"reflex": rid, "source_memory": slug,
                             "wording_last_changed": datetime.fromtimestamp(last_change[rid]).strftime("%Y-%m-%d %H:%M"),
                             "memory_modified": datetime.fromtimestamp(m).strftime("%Y-%m-%d %H:%M"),
                             "gap_days": round((m - last_change[rid]) / 86400, 1), "gut": gut[:160]})
    rows.sort(key=lambda r: -r["gap_days"])
    res = {"reflexes": len(now_texts), "file_versions_read": len(versions),
           "earliest_version": datetime.fromtimestamp(versions[0][0]).strftime("%Y-%m-%d %H:%M"),
           "reflexes_whose_source_memory_changed_later": len(rows), "rows": rows}
    json.dump(res, io.open(sys.argv[1], "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}))
    for r in rows[:25]:
        print("  %-44s wording %s  memory %s  (+%s d)  %s" % (r["reflex"][:44], r["wording_last_changed"],
              r["memory_modified"], r["gap_days"], r["source_memory"][:50]))


if __name__ == "__main__":
    main()
