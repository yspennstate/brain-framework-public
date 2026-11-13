"""reseal_blockdiff.py - what the math hook serves on the live store against the same store re-sealed (registered 28 Sep
11:0x in THREE_DAY_RUN_2026-09-27.md; claude-825b1305).

Every prompt of cand80's 898 (plain v1-v3, the 40 unanswerable probes, the absent bank, his gate-passing prompts) goes
through the livepost80 hook, as its own process, once with MATHBRAIN_STORE on a byte copy of the live store and once on
the same copy after the re-seal's dry run. The two blocks are compared as text; the ids of the 10 Aug batch cards in each
block are recorded, and for a plain-bank question whose id names a card, whether that card is first. His prompts' text and
blocks are not written, only their sha1 and the ids served.

    python reseal_blockdiff.py BASE_STORE CAND_STORE OUT.jsonl [--jobs 2]
"""
import argparse
import concurrent.futures as cf
import hashlib
import io
import json
import os
import re
import sys
import uuid

TOOLS = os.path.expanduser("~/brain-repo/12_cognitive_architecture/brain_improvement/campaign_2026-09-24_mathbrain_10d/tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.expanduser("~/.claude/dreaming"))
import run_cand77_blockdiff as RB  # noqa: E402
from hookcall import cap_for, run_hook  # noqa: E402

ARM = os.path.expanduser("~/math-brain-build/local/mb10d/arms/livepost80/dreaming/math_sense.py")
ENV = {"MATHBRAIN_TIME_BUDGET_S": "300", "MATHBRAIN_INTEGRITY_GRACE_S": "60", "PYTHONIOENCODING": "utf-8",
       "MATHBRAIN_USE_LOG": "0",
       "MATH_BRAIN_CLAIM_VECS": os.path.expanduser("~/math-brain-build/local/mb10d/vectors/claim_embeddings_ext_20260926.npz")}
CARD = re.compile(r"claim_[0-9a-f]{64}")
BATCH = set(n["id"] for n in json.load(io.open(os.path.expanduser(
    r"~\.claude\brain_graph\study\source_check_0810\rows_0810.json"), encoding="utf-8"))["nodes"])


def one(job, base, cand):
    bank, key, q = job
    got = {}
    for arm, store in (("base", base), ("cand", cand)):
        env = dict(os.environ, **ENV, MATHBRAIN_STORE=store)
        r = run_hook([sys.executable, ARM], json.dumps({"prompt": q, "session_id": "rbd-" + uuid.uuid4().hex[:12]}),
                     env, cap=cap_for(env))
        got[arm] = (RB.block_of(r.stdout), r)
    (ba, ra), (bc, rc) = got["base"], got["cand"]
    ib, ic = CARD.findall(ba), CARD.findall(bc)
    ob = list(dict.fromkeys(ib))
    oc = list(dict.fromkeys(ic))
    row = {"bank": bank, "key": key, "served_base": bool(ba.strip()), "served_cand": bool(bc.strip()),
           "same_text": ba == bc, "cards_base": ob, "cards_cand": oc,
           "batch_base": [c for c in ob if c in BATCH], "batch_cand": [c for c in oc if c in BATCH],
           "timed_out": bool(ra.timed_out or rc.timed_out), "sec_base": round(ra.seconds, 2),
           "sec_cand": round(rc.seconds, 2),
           "sha_base": hashlib.sha1(ba.encode("utf-8")).hexdigest()[:12],
           "sha_cand": hashlib.sha1(bc.encode("utf-8")).hexdigest()[:12]}
    if bank.startswith("plain") and CARD.fullmatch(str(key)):
        row["own_card_first_base"] = bool(ob) and ob[0] == key
        row["own_card_first_cand"] = bool(oc) and oc[0] == key
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("cand")
    ap.add_argument("out")
    ap.add_argument("--jobs", type=int, default=2)
    a = ap.parse_args()
    work = RB.items({"plain", "unans"})
    for it in json.load(io.open(os.path.join(RB.BANKS, "absent_plain_v1.json"), encoding="utf-8"))["items"]:
        work.append(("absent", it["id"], it["question"]))
    work += RB.items({"his"})
    print("prompts:", len(work), flush=True)
    with io.open(a.out, "w", encoding="utf-8", newline="\n") as fh, cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        for n, row in enumerate(ex.map(lambda j: one(j, a.base, a.cand), work)):
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            if n % 50 == 0:
                print(n, flush=True)
    print("done", len(work))


if __name__ == "__main__":
    main()
