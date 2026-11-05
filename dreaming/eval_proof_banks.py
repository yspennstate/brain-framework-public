"""Does the math hook serve the results a REAL proof cites? The nightly gate's proof instrument (2026-09-25).

Runs two banks of pasted proofs through the real hook against a store and scores, per proof, whether a served card's TITLE names
each result the proof cites (the proof-audit bank's rule, eval_pivot_reach.named_in: the first two content words of the name):
  named   state/bank_named_proofs_v1.json - 28 proofs from the FEI, QSO, paper23 and Benjamini-Schramm papers, 32 results cited by
          a proper name (held out for the proof-text rule that made the hook read a pasted proof as an argument)
  qform   state/bank_qform_proofs_v1.json - 35 proofs of the quadratic-form density papers, 57 named textbook results
  heldout state/bank_named_proofs_v2.json - 24 proofs of 24 further papers, 33 results, keyed by a dictionary fixed before reading
Before 25 Sep the hook read no pasted proof as an argument and reached 1 of 32 and 4 of 57; see memory
a_pasted_proof_is_an_argument_2026_09_25. A shuffled control scores each block against the next proof's results.

    python eval_proof_banks.py [--store <brain.sqlite3>] [--hook <math_sense.py>] [--jobs 2]
Summary line (the regression gate parses it):
    PROOFBANKS named n=28 served 23 title 20/32 items 17 | qform n=35 served 29 title 21/57 items 13 | shuffled 1 | median 1.2 s
"""
import argparse
import importlib.util
import io
import json
import os
import re
import statistics
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "math_sense.py")
LIVE_STORE = os.path.expanduser("~/math-brain-build/local/population/brain.sqlite3")
BANKS = [("named", os.path.join(HERE, "state", "bank_named_proofs_v1.json")),
         ("qform", os.path.join(HERE, "state", "bank_qform_proofs_v1.json")),
         # 2026-09-25 (campaign mb10d): 24 proofs of 24 papers neither bank above used, keyed by a dictionary written before any of
         # them was read (named_proofs_v2) - the out-of-sample check on the reviewed-name rules
         ("heldout", os.path.join(HERE, "state", "bank_named_proofs_v2.json"))]
TITLE_RE = re.compile(r"^- (.+?) \[[^\]]{1,60}\]:", re.M)
_PV = os.path.join(os.path.expanduser("~/ai-memories-and-functionality"), "12_cognitive_architecture", "brain_improvement",
                   "campaign_2026-09-12_opus_1k", "tools", "eval_pivot_reach.py")


import os as _ko_os, sys as _ko_sys  # noqa: E402
_ko_sys.path.insert(0, _ko_os.path.join(_ko_os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (R13 trap 1, 27 Sep): brain data is never deleted


def _named_in():
    spec = importlib.util.spec_from_file_location("pv", _PV)
    pv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pv)
    return pv.named_in


def run_hook(hook, store, prompt, sess):
    """(block, seconds, invalid). invalid is '' for a clean call, else why its output is not a measurement: a hook that
    exited nonzero, timed out or printed something other than its JSON delivers nothing in a session, so its titles are
    never scored. Until 30 Sep a call that exited nonzero with output was parsed and scored like a clean one (account03/B01,
    B01-R79-GATE-PROOFBANK-VIB-FAILED-HOOK-OUTPUT-SCORED-20260929)."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", MATHBRAIN_STORE=store, CLAUDE_SESSION_ID=sess)
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, hook], input=json.dumps({"prompt": prompt}), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300, env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return "", time.time() - t0, "timeout"
    except OSError as exc:
        return "", time.time() - t0, "launch error: %s" % type(exc).__name__
    if r.returncode != 0:
        return "", time.time() - t0, "hook exited %s" % r.returncode
    out = (r.stdout or "").strip()
    blk = ""
    if out:
        try:
            blk = json.loads(out)["hookSpecificOutput"]["additionalContext"] or ""
        except Exception:
            return "", time.time() - t0, "hook output is not the hook's JSON"
    return blk, time.time() - t0, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=LIVE_STORE)
    ap.add_argument("--hook", default=HOOK)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    for path in (a.hook, a.store):
        if not os.path.isfile(path):
            # a missing hook or store scores every proof silent - a quiet zero, never a measurement
            raise SystemExit("eval_proof_banks: no such file: %s" % path)
    named_in = _named_in()
    stamp = uuid.uuid4().hex[:8]
    parts, all_rows, secs, shuffled, invalid, missing = [], {}, [], 0, {}, []
    for tag, bank_path in BANKS:
        if tag == "heldout" and not os.path.isfile(bank_path):
            # 30 Oct 2026: the held-out bank never travelled to the Mac, and its FileNotFoundError cost the named and
            # qform measurements too. The gate already reads heldout as optional; the line ends by naming the gap.
            missing.append("%s (%s)" % (tag, bank_path))
            continue
        items = json.load(io.open(bank_path, encoding="utf-8"))["items"]

        def one(it):
            blk, dt, bad = run_hook(a.hook, a.store, it["prompt"], "proofbanks-%s-%s" % (stamp, it["id"]))
            if bad:
                return {"id": it["id"], "named": it["named"], "titles": [], "served": False, "seconds": round(dt, 2),
                        "title_hit": [], "invalid": bad}
            titles = TITLE_RE.findall(blk)
            tj = " || ".join(titles)
            return {"id": it["id"], "named": it["named"], "titles": titles, "served": bool(blk), "seconds": round(dt, 2),
                    "title_hit": [n for n in it["named"] if named_in(tj, n)]}
        with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
            scored = list(ex.map(one, items))
        for r in scored:
            if r.get("invalid"):
                invalid[r["invalid"]] = invalid.get(r["invalid"], 0) + 1
        all_rows[tag] = scored
        rows = [r for r in scored if not r.get("invalid")]      # an invalid call is in no count, and is counted below
        n = len(rows)
        for i, r in enumerate(rows):
            nxt = rows[(i + 1) % n]["named"]
            shuffled += sum(1 for nm in nxt if nm not in r["named"] and named_in(" || ".join(r["titles"]), nm))
        secs += [r["seconds"] for r in rows]
        parts.append("%s n=%d served %d title %d/%d items %d" % (tag, n, sum(r["served"] for r in rows),
                                                                sum(len(r["title_hit"]) for r in rows),
                                                                sum(len(r["named"]) for r in rows),
                                                                sum(1 for r in rows if r["title_hit"])))
    if a.out:
        io.open(keep_old(a.out), "w", encoding="utf-8").write(json.dumps(all_rows, ensure_ascii=False, indent=1))
    print("PROOFBANKS %s | shuffled %d | median %.2f s | hook %s | store %s | invalid %d%s%s"
          % (" | ".join(parts), shuffled, statistics.median(secs) if secs else 0.0, a.hook, a.store,
             sum(invalid.values()), (" (%s)" % "; ".join("%s: %d" % kv for kv in sorted(invalid.items()))) if invalid else "",
             (" | missing bank %s" % "; ".join(missing)) if missing else ""))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
