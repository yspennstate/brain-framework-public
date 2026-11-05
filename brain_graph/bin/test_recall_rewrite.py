"""Controls for recall_rewrite.py. Keys stay local: only found / not found and ranks are printed."""
import json
import os
import re
import subprocess
import sys

HOME = os.path.expanduser("~")
RR = os.path.join(HOME, ".claude", "dreaming", "recall_rewrite.py")
BRAIN = os.path.join(HOME, ".claude", "dreaming", "brain.py")
Q = os.path.join(HOME, "bank_recall_keys_quarantine", "recall4_r1")
ROW = re.compile(r"^\s+([0-9]*\.?[0-9]+)\s+\[([^\]]*)\]\s+(\S+)\s*$")
FUSED = re.compile(r"^\s+(\d+)\.\s+\[([^\]]*)\]\s+(\S+)\s*$")


def run(args):
    env = dict(os.environ, RECALL_REWRITE_NO_LOG="1", BRAIN_RECALL_LOG="0")   # bank questions reach neither live log
    p = subprocess.run([sys.executable, "-X", "utf8"] + args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=900, env=env)
    return p.stdout or ""


results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))


# 1. --no-model is the plain recall, in the same order
q0 = "why did the live brain graph stop showing lines in his sessions"
plain = [m.group(3) for m in map(ROW.match, run([BRAIN, "recall", q0, "-n", "10"]).splitlines()) if m]
fused = [m.group(3) for m in map(FUSED.match, run([RR, q0, "-n", "10", "--no-model"]).splitlines()) if m]
check("no-model equals plain recall (top 10)", plain[:10] == fused[:10] and len(fused) == 10,
      "%d vs %d rows" % (len(plain), len(fused)))

# 2. stored rewrites from the probe: questions only a rewrite found
bank = {json.loads(l)["id"]: json.loads(l) for l in open(os.path.join(Q, "bank100.jsonl"), encoding="utf-8") if l.strip()}
rows = [json.loads(l) for l in open(os.path.join(Q, "r1w_rows.jsonl"), encoding="utf-8") if l.strip()]
picked = [r for r in rows if (r.get("base_rank") is None or r["base_rank"] > 5) and r.get("best_rank") and
          r["best_rank"] <= 5 and len(r.get("queries") or []) == 3 and r["id"] in bank][:4]
for r in picked:
    it = bank[r["id"]]
    qtext = it.get("q_paraphrase") or it.get("question") or it.get("q") or ""
    out = run([RR, qtext, "-n", "20", "--rewrites", "|".join(r["queries"])])
    slugs = [m.group(3).lower() for m in map(FUSED.match, out.splitlines()) if m]
    src = str(r["source_slug"]).lower()
    rank = slugs.index(src) + 1 if src in slugs else None
    check("stored rewrites find a source the question missed (%s)" % r["id"], rank is not None,
          "question alone rank %s, fused rank %s" % (r.get("base_rank"), rank))
print("RESULT %d/%d" % (sum(results), len(results)))
