"""Negative control for the owner-voice instrument: does a served block match ONLY its own key?

Re-scores a recorded run (eval_ownvoice.py --out X.jsonl) with every question's key ROTATED to the next
question's key. If the hit_result count stays near zero the instrument's hits are key-specific; if it stays
high, "hit" was measuring something that any served block satisfies and the number means nothing.

    python ownvoice_negative_control.py --store <brain.sqlite3> --run ownvoice_LIVE_default_on.jsonl
"""
import argparse, io, json, sqlite3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--run", required=True)
    a = ap.parse_args()
    rows = [json.loads(l) for l in io.open(a.run, encoding="utf-8") if l.strip()]
    db = sqlite3.connect("file:%s?mode=ro" % a.store.replace("\\", "/"), uri=True)
    titles = {r[0]: (r[1] or "") for r in db.execute("select id, title from nodes")}
    keys = [r["key"] for r in rows]
    own = sum(1 for r in rows if any(("(our id %s)" % r["key"]) in titles.get(i, "") for i in r["served_ids"]))
    rot = 0
    for j, r in enumerate(rows):
        k = keys[(j + 1) % len(keys)]
        if k == r["key"]:
            continue
        rot += any(("(our id %s)" % k) in titles.get(i, "") for i in r["served_ids"])
    served = sum(1 for r in rows if r["served"])
    print("NEGATIVE CONTROL[%s] n=%d served %d | own-key hits %d | rotated-key hits %d" % (a.run, len(rows), served, own, rot))
    return 0 if rot <= 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
