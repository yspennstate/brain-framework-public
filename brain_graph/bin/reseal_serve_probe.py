"""reseal_serve_probe.py - does the math hook serve the 10 Aug curated cards before and after the re-seal? (28 Sep,
claude-825b1305; the served-block comparison registered at 10:2x, first a targeted look.)

Today the hook's last read (selection_integrity -> current_note.read_current_note) refuses a card whose stored node
digest matches neither the node nor its body ("stored_node_digest_mismatch"), and the 34 curated nodes of the 10 Aug
batch are exactly such cards; none of them appears in the serve archive (24-26 Sep, 12,811 records, 319 distinct cards).
This asks the hook, as its own process per prompt, one targeted question per card family on two stores: a byte copy of
the live store and the same copy re-sealed as a dry run (reseal_math_rows.py --first-record), each with the live object
folder beside it. The hook code is the livepost80 arm (the live hook, block for block); the environment is the campaign's
rehearsal environment with MATHBRAIN_STORE pointed at each copy. It prints which batch cards each block serves.

    python reseal_serve_probe.py BASE_STORE CAND_STORE OUT_JSON
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import io
import json
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.expanduser("~/.claude/dreaming"))
from hookcall import cap_for, run_hook  # noqa: E402

ARM = os.path.expanduser("~/math-brain-build/local/mb10d/arms/livepost80/dreaming/math_sense.py")
ENV = {"MATHBRAIN_TIME_BUDGET_S": "300", "MATHBRAIN_INTEGRITY_GRACE_S": "60", "PYTHONIOENCODING": "utf-8",
       "MATHBRAIN_USE_LOG": "0",
       "MATH_BRAIN_CLAIM_VECS": os.path.expanduser("~/math-brain-build/local/mb10d/vectors/claim_embeddings_ext_20260926.npz")}
ID_RE = re.compile(r"claim_[0-9a-f]{64}")
PROMPTS = [
    "What does Lagrange's theorem say about the order of a subgroup of a finite group?",
    "State the intermediate value theorem for a continuous real function on a closed interval [a, b].",
    "How many trees are there on n labeled vertices? Which formula gives the count?",
    "When is a finite graph planar? What are the forbidden subdivisions in Kuratowski's characterization?",
    "State Rouche's theorem about the number of zeros of f and f + g inside a closed contour.",
    "What does the Mordell-Weil theorem say about the group of rational points of an abelian variety over a number field?",
    "In first-passage percolation on Z^2 with edge weights in [a, b], how far can a geodesic from the origin to (0, r) "
    "wander transversally?",
    "If X^L is X with a block of independent coordinates resampled, what is E[(X - X^L)^2] in terms of Var(X) and "
    "Cov(X, X^L)?",
    "State the Schwarz lemma for a holomorphic self-map of the unit disk fixing the origin.",
    "State Brouwer's fixed-point theorem for continuous maps of a compact convex set.",
]


def block_of(stdout):
    if not (stdout or "").strip():
        return ""
    try:
        return json.loads(stdout)["hookSpecificOutput"]["additionalContext"] or ""
    except Exception:
        return stdout


def main():
    base, cand, out = sys.argv[1:4]
    batch = set(json.load(io.open(os.path.expanduser(
        r"~\.claude\brain_graph\study\source_check_0810\rows_0810.json"), encoding="utf-8"))["nodes"][i]["id"]
        for i in range(34))
    rows = []
    for q in PROMPTS:
        row = {"prompt": q}
        for arm, store in (("base", base), ("cand", cand)):
            env = dict(os.environ, **ENV, MATHBRAIN_STORE=store)
            r = run_hook([sys.executable, ARM], json.dumps({"prompt": q, "session_id": "rsp-" + uuid.uuid4().hex[:12]}),
                         env, cap=cap_for(env))
            b = block_of(r.stdout)
            ids = ID_RE.findall(b)
            row[arm] = {"served": bool(b.strip()), "cards": len(set(ids)), "batch_cards": sorted(set(ids) & batch),
                        "timed_out": bool(r.timed_out), "sec": round(r.seconds, 1), "block": b}
        row["same_block"] = row["base"]["block"] == row["cand"]["block"]
        rows.append(row)
        print("%-70s base %s/%d batch %d | cand %s/%d batch %d | same %s" % (
            q[:70], row["base"]["served"], row["base"]["cards"], len(row["base"]["batch_cards"]), row["cand"]["served"],
            row["cand"]["cards"], len(row["cand"]["batch_cards"]), row["same_block"]), flush=True)
    json.dump({"base": base, "cand": cand, "rows": rows}, io.open(out, "w", encoding="utf-8"), indent=1,
              ensure_ascii=False)


if __name__ == "__main__":
    main()
