"""Does the math hook serve the campaign's OWN result when asked in the owner's voice?

Runs bank_ownvoice_prob.json through the real hook against a given store (MATHBRAIN_STORE) and scores,
per question: served (the hook spoke), hit_result (a served node's title carries "(our id KEY)" - only the
new result nodes do), hit_any (a served node's title or body names the problem the question is about).
Keys are node NAMES, not answers, so nothing here leaks to a solver.

Since 2026-09-25 hit_result also counts the block's "same result on record: <title> (<id>)" line. The hook's
same-result rule (MATHBRAIN_SAME_RESULT, landed 25 Sep 02:46) shows one card per result pair - our memory-born
card - and names the research ledger's card, the one whose title carries "(our id KEY)", on that line instead of
serving it; the old count read every such block as a miss (22 -> 11 of 30 on the 25 Sep gate). The served-cards-only
count is still reported as "strict".

    python eval_ownvoice.py --store <brain.sqlite3> --out X.jsonl [--tag live|copy] [--hook <math_sense.py>] [--env K=V]
"""
import argparse, io, json, os, re, sqlite3, subprocess, sys, time, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "math_sense.py")
BANK = os.path.join(HERE, "state", "bank_ownvoice_prob.json")   # the campaign copy is lanes/m1_probstore/bank_ownvoice_prob.json
SRC_RE = re.compile('^[ ]*[(](?:source-id:[ ]*)?([A-Za-z0-9_]{16,})[ ]*(?:[|]|[)])', re.M)  # id, not label prose: the label lost "source-id:" on 2026-09-13
# Since 25 Sep 22:1x every record-line label counts, not only "same result on record": the hook shows ONE line per record per
# block (MATHBRAIN_PITFALL_LINE_DEDUP, arm cand67), so when an "answered since" line on another card already names the result, the
# "same result on record" line that repeated it is gone, and a reader still sees the result's id (ov13 read as a miss).
REC_RE = re.compile('^[ ]*(?:same result on record|answered since|corrected since|on record): .*[(]([A-Za-z0-9_]{16,})[)][ ]*$', re.M)  # the last "(id)" on the line; titles carry parentheses


import os as _ko_os, sys as _ko_sys  # noqa: E402
_ko_sys.path.insert(0, _ko_os.path.join(_ko_os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (R13 trap 1, 27 Sep): brain data is never deleted


def run_hook(prompt, store, session, hook=HOOK, env_pairs=()):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", MATHBRAIN_STORE=store, CLAUDE_SESSION_ID=session)
    for kv in env_pairs:
        k, _, v = kv.partition("=")
        env[k] = v
    t0 = time.time()
    r = subprocess.run([sys.executable, hook], input=json.dumps({"prompt": prompt}), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=180, env=env,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = r.stdout or ""
    if r.returncode != 0 and not out.strip():
        raise RuntimeError("hook exited %s with no output: %s" % (r.returncode, (r.stderr or "")[-300:]))
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
    ap.add_argument("--hook", default=HOOK, help="the math_sense.py to run (default: the live hook)")
    ap.add_argument("--env", action="append", default=[], help="K=V set for every hook call (repeatable)")
    args = ap.parse_args()
    if not os.path.isfile(args.hook) or not os.path.isfile(args.store):
        # a missing hook or store scores every question silent - a quiet zero, never a measurement
        raise SystemExit("eval_ownvoice: no such %s: %s" % (("hook", args.hook) if not os.path.isfile(args.hook) else ("store", args.store)))
    items = json.load(io.open(BANK, encoding="utf-8"))["items"]
    db = sqlite3.connect("file:%s?mode=ro" % args.store.replace("\\", "/"), uri=True)
    titles = {r[0]: (r[1] or "") for r in db.execute("select id, title from nodes")}
    stamp = uuid.uuid4().hex[:8]
    rows, served = [], 0
    hit_result = hit_any = hit_strict = 0
    for it in items:
        ctx, dt = run_hook(it["question"], args.store, "ownvoice-%s-%s" % (stamp, it["id"]), args.hook, args.env)
        ids = SRC_RE.findall(ctx)
        rec = [i for i in REC_RE.findall(ctx) if i not in ids]
        key = it["expect_key"].upper()
        mark = "(our id %s)" % key
        r_strict = any(mark in titles.get(i, "") for i in ids)
        r_hit = r_strict or any(mark in titles.get(i, "") for i in rec)
        a_hit = r_hit
        if not a_hit and ids:
            q = ",".join("'%s'" % i for i in ids)
            for (t, b) in db.execute("select title, body from nodes where id in (%s)" % q):
                if key in (t or "") or key in (b or "")[:4000]:
                    a_hit = True
                    break
        s = bool(ctx.strip())
        served += s; hit_result += r_hit; hit_any += a_hit; hit_strict += r_strict
        rows.append({"id": it["id"], "key": key, "served": s, "hit_result": r_hit, "hit_result_strict": r_strict,
                     "hit_any": a_hit, "served_ids": ids[:5], "record_ids": rec[:5], "seconds": round(dt, 2), "tag": args.tag})
    with io.open(keep_old(args.out), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    n = len(items)
    print("OWNVOICE[%s] n=%d served %d hit_result %d hit_any %d strict %d | store %s | hook %s%s"
          % (args.tag, n, served, hit_result, hit_any, hit_strict, args.store, args.hook,
             (" | env " + " ".join(args.env)) if args.env else ""))


if __name__ == "__main__":
    main()
