"""Fresh recall test for the memory brain: owner-voice questions, gold memory slugs, hit@1 / hit@5 through brain.py recall.

Authored 2026-09-03 19:0x on the owner's ask "i want another test to see how it is doing" - the other half of
the campaign's object. Each question is worded the way he asks (short, his spelling left alone, no slug words),
and the gold is the memory that answers it. A miss is either recall ranking the wrong memory or the memory
being worded so that his question cannot find it - both are the brain's problem. Re-run for the trend.
"""
import io
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# the Windows path off Windows made every gold look missing, n came out 0 and the gate crashed on it (Mac, 30 Oct)
MEM = os.environ.get("BRAIN_MEM_DIR") or (os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory") if os.name == "nt"
       else os.path.join(os.path.expanduser("~"), ".claude", "projects",
                         os.path.expanduser("~").replace("/", "-"), "memory"))

# The question bank is the user's own: questions worded the way they ask, golds are slugs in their memory folder.
# It lives in a JSON file next to this script (or EVAL_FRESH_BANK): {"bank": [[q, [slug, ...]], ...],
# "bank_v2_extra": [...]}. A fresh install has none, so the lists are empty and main() says how to add one.
BANK_FILE = os.environ.get("EVAL_FRESH_BANK") or os.path.join(HERE, "eval_fresh_bank.json")


def _load_bank(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return [], []
    rows = lambda key: [(q, list(g)) for q, g in d.get(key, [])]
    return rows("bank"), rows("bank_v2_extra")


BANK, BANK_V2_EXTRA = _load_bank(BANK_FILE)


TIMEOUT_MARK = "__RECALL_TIMEOUT__"


import os as _ko_os, sys as _ko_sys  # noqa: E402
_ko_sys.path.insert(0, _ko_os.path.join(_ko_os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (R13 trap 1, 27 Sep): brain data is never deleted

# The second half of the bank (bank_v2_extra) is loaded from the same JSON file as the first; a bank is the
# user's own questions and memory slugs and is never published with the framework.


TIMEOUT_MARK = "__RECALL_TIMEOUT__"


import os as _ko_os, sys as _ko_sys  # noqa: E402
_ko_sys.path.insert(0, _ko_os.path.join(_ko_os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (R13 trap 1, 27 Sep): brain data is never deleted


def recall(q, k=8):
    # 600 s, not 180. Measured 2026-09-15: three runs of the SAME query at the SAME setting took
    # 19.7 / 84.0 / 189.7 s - a 9.6x spread on identical work. A 180 s cap sits inside that spread,
    # so an A/B arm died at random and the death was attributed to whichever dial was under test.
    # Matches the owner's 09-15 order that every hook gets 600 s for the same reason.
    try:
        out = subprocess.run([sys.executable, os.path.join(HERE, "brain.py"), "recall", q], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=600).stdout
    except subprocess.TimeoutExpired:
        # A timeout is NOT a miss and NOT a crash. Scoring it as a miss blames the dial under test;
        # raising kills the whole bank mid-run. It is reported separately and left out of the rate.
        return [], TIMEOUT_MARK
    slugs = re.findall(r"^\s*\d\.\d\d\s+\[[^\]]*\]\s+(\S+)", out, re.M)
    return slugs[:k], out


def main():
    # 2026-09-11: `--help` used to run the whole 25-question evaluator (no argument parsing) - the fifth time a
    # script here executed work on a help flag. Help is inert; an unknown flag stops instead of falling through.
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print("usage: eval_brain_recall_fresh.py [--v2]\n  --v2  BANK + BANK_V2_EXTRA; default is BANK alone")
        return 0
    unknown = [a for a in sys.argv[1:] if a not in ("--v2",)]
    if unknown:
        print("unknown argument(s): %s (try --help)" % " ".join(unknown), file=sys.stderr)
        return 2
    v2 = "--v2" in sys.argv
    bank = BANK + BANK_V2_EXTRA if v2 else BANK
    if not bank:
        print("no question bank at %s (write one: {\"bank\": [[question, [gold_slug]]]})" % BANK_FILE)
        return 2
    missing = [s for _, golds in bank for s in golds if not os.path.exists(os.path.join(MEM, s + ".md"))]
    if missing:
        print("gold slugs not on disk (fix the bank):", missing)
    rows = []
    t0 = time.time()
    for q, golds in bank:
        golds = [g for g in golds if os.path.exists(os.path.join(MEM, g + ".md"))]
        if not golds:
            continue
        slugs, raw = recall(q)
        if raw == TIMEOUT_MARK:
            rows.append({"q": q, "golds": golds, "top5": [], "rank": None, "timeout": True})
            continue
        rank = next((i + 1 for i, s in enumerate(slugs) if s in golds), None)
        rows.append({"q": q, "golds": golds, "top5": slugs[:5], "rank": rank})
    timeouts = [r for r in rows if r.get("timeout")]
    rows = [r for r in rows if not r.get("timeout")]
    n = len(rows)
    h1 = sum(1 for r in rows if r["rank"] == 1)
    h5 = sum(1 for r in rows if r["rank"] and r["rank"] <= 5)
    if not n:
        # 2026-10-30 (Mac): every question timed out under load, n was 0 and the summary line died on a
        # ZeroDivisionError, so the gate logged a traceback instead of the cause. Say it and fail.
        print("brain recall fresh bank: n=0 scored, %d of %d question(s) timed out - no rate (%.0fs)"
              % (len(timeouts), len(bank), time.time() - t0))
        return 1
    print(f"brain recall fresh bank: n={n}  hit@1 {h1}/{n} = {100.0*h1/n:.0f}%  hit@5 {h5}/{n} = {100.0*h5/n:.0f}%  ({time.time()-t0:.0f}s)")
    for r in rows:
        mark = "ok " if r["rank"] == 1 else ("~%d " % r["rank"] if r["rank"] else "MISS")
        print(f"  {mark} {r['q'][:62]:62s} -> {r['top5'][0][:60] if r['top5'] else '(nothing)'}")
    # BRAIN_EVAL_TAG keeps two arms of one A/B out of each other's output file. Without it both arms
    # write the same daily name, so arm 2 silently overwrites arm 1 - and an unrelated run earlier the
    # same day is lost too. Sanitised because it becomes a filename.
    tag = re.sub(r"[^A-Za-z0-9_.-]", "", os.environ.get("BRAIN_EVAL_TAG", ""))[:40]
    out = os.path.join(HERE, "state", "brain_recall_fresh_%s%s%s.jsonl"
                       % (time.strftime("%Y%m%d"), "_v2" if v2 else "", ("_" + tag) if tag else ""))
    with io.open(keep_old(out), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    if timeouts:
        # Loud on purpose: a run with timeouts is not comparable with one without.
        print("TIMEOUTS: %d question(s) exceeded 600 s and are OUT of the rate above (not scored as misses):" % len(timeouts))
        for r in timeouts:
            print("  TIMEOUT", r["q"][:70])
    print("rows:", out)


if __name__ == "__main__":
    raise SystemExit(main())
