"""Does the math hook serve the campaign's OWN result when asked in the owner's voice?

Runs bank_ownvoice_prob.json through the real hook against a given store (MATHBRAIN_STORE) and scores,
per question: served (the hook spoke), hit_result (a served node's title carries "(our id KEY)" - only the
new result nodes do), hit_any (a served node's title or body names the problem the question is about).
Keys are node NAMES, not answers, so nothing here leaks to a solver.

    python eval_ownvoice.py --store <brain.sqlite3> --out X.jsonl [--jobs 1] [--tag live|copy]
"""
import argparse, io, json, os, re, sqlite3, subprocess, sys, time, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "math_sense_frozen_20260913.py")
BANK = os.path.join(HERE, "state", "bank_ownvoice_prob.json")   # the campaign copy is lanes/m1_probstore/bank_ownvoice_prob.json
SRC_RE = re.compile(r"source-id:\s*([a-z_0-9]+)")


def run_hook(prompt, store, session):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", MATHBRAIN_STORE=store, CLAUDE_SESSION_ID=session)
    t0 = time.time()
    r = subprocess.run([sys.executable, HOOK], input=json.dumps({"prompt": prompt}), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=180, env=env)
    out = r.stdout or ""
    try:
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    except Exception:
        ctx = out
    return ctx, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    items = json.load(io.open(BANK, encoding="utf-8"))["items"]
    db = sqlite3.connect("file:%s?mode=ro" % args.store.replace("\\", "/"), uri=True)
    titles = {r[0]: (r[1] or "") for r in db.execute("select id, title from nodes")}
    bodies = {}
    stamp = uuid.uuid4().hex[:8]
    rows, served = [], 0
    hit_result = hit_any = 0
    for it in items:
        ctx, dt = run_hook(it["question"], args.store, "ownvoice-%s-%s" % (stamp, it["id"]))
        ids = SRC_RE.findall(ctx)
        key = it["expect_key"].upper()
        r_hit = any(("(our id %s)" % key) in titles.get(i, "") for i in ids)
        a_hit = r_hit
        if not a_hit and ids:
            q = ",".join("'%s'" % i for i in ids)
            for (t, b) in db.execute("select title, body from nodes where id in (%s)" % q):
                if key in (t or "") or key in (b or "")[:4000]:
                    a_hit = True
                    break
        s = bool(ctx.strip())
        served += s; hit_result += r_hit; hit_any += a_hit
        rows.append({"id": it["id"], "key": key, "served": s, "hit_result": r_hit, "hit_any": a_hit,
                     "served_ids": ids[:5], "seconds": round(dt, 2), "tag": args.tag})
    with io.open(args.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    n = len(items)
    print("OWNVOICE[%s] n=%d served %d hit_result %d hit_any %d | store %s" % (args.tag, n, served, hit_result, hit_any, args.store))


if __name__ == "__main__":
    main()
