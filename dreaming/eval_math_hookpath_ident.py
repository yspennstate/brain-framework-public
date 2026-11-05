"""Score the math brain through the REAL hook path (a subprocess per prompt, stdin JSON, the same entry point
Claude Code calls), not through retrieve()+_select() in-process.

Why this exists (2026-09-11). eval_math_fresh.py and eval_math_probes.py import math_sense and call
_select() directly, so they never see the on-topic gate that main() applies AFTER _select (2026-09-07), nor the
prefilter cut, nor the cost gate. On 2026-09-11 the health check read 4 of 8 must-speak probes silent while the
in-process evaluators would still have read the fresh bank near its 09-03 numbers: the theorem was found and
selected, then dropped by a gate the evaluators never ran. A number "through the hook" must be measured through
the hook.

    python eval_math_hookpath.py --out state/hookpath_<tag>.jsonl [--bank state/math_probe_fresh_20260903.json]
                                 [--jobs 4] [--env MATHBRAIN_GATE_FLOOR=25 ...] [--long-prompts SAMPLE.json]

Scoring is the fresh bank's: theorem-level, a served title carrying one of the target's stems; rank-1 = the FIRST
served title carries a stem; silent = no block. For the unanswerable targets: false fire = any block. The
optional --long-prompts file (a plit SAMPLE.json or any JSON list of {"item_id","prompt"|"argument"|"text"}) is a
control for the proof-audit shape the on-topic gate was tuned on: it reports how many long prompts get a block and
which node ids, so a change meant for short questions can be shown not to move the long ones.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "math_sense_ident.py")
STATE = os.path.join(HERE, "state")
BANK = os.path.join(STATE, "math_probe_fresh_20260903.json")
TITLE_RE = re.compile(r"^- (.+?) \[[^\]]*, score [0-9.]+\]:", re.M)
SRC_RE = re.compile(r"\(source-id: ([^)]+)\)")


def _key(text):
    folded = unicodedata.normalize("NFKD", text or "")
    out = [ch if ch.isalnum() else " " for ch in folded.casefold() if not unicodedata.combining(ch)]
    return " ".join("".join(out).split())


def ask(prompt, env):
    e = dict(os.environ)
    e.update(env)
    e["PYTHONIOENCODING"] = "utf-8"
    t0 = time.time()
    try:
        out = subprocess.run([sys.executable, HOOK], input=json.dumps({"prompt": prompt}),
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=90, env=e)
        raw = out.stdout.strip()
    except subprocess.TimeoutExpired:
        raw = ""
    dt = time.time() - t0
    block = ""
    if raw:
        try:
            block = json.loads(raw)["hookSpecificOutput"]["additionalContext"]
        except Exception:
            block = ""
    titles = TITLE_RE.findall(block)
    ids = SRC_RE.findall(block)
    return {"block_chars": len(block), "titles": titles, "ids": ids, "seconds": round(dt, 2)}


def stem_hit(titles, stems):
    keys = [_key(t) for t in titles]
    st = [_key(s) for s in stems]
    return any(any(s in k for s in st) for k in keys)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", default=BANK)
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--long-prompts", default=None)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    env = dict(kv.split("=", 1) for kv in args.env)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    bank = json.load(open(args.bank, encoding="utf-8"))
    jobs = []
    for it in bank["answerable"]:
        for form in ("q_content", "q_hinted"):
            jobs.append(("answerable", it["key"], it.get("area"), form, it[form], it["stems"]))
    for it in bank["unanswerable"]:
        q = it.get("q") or it.get("q_content") or it.get("question") or it.get("q_hinted")
        jobs.append(("unanswerable", it.get("key"), it.get("area"), "q", q, it.get("stems") or []))
    if args.long_prompts:
        lp = json.load(open(args.long_prompts, encoding="utf-8"))
        if isinstance(lp, dict):
            lp = lp.get("items") or lp.get("sample") or list(lp.values())[0]
        for it in lp:
            txt = it.get("prompt") or it.get("argument") or it.get("text") or it.get("body") or ""
            if isinstance(txt, dict):
                txt = json.dumps(txt)
            jobs.append(("long", it.get("item_id") or it.get("id"), it.get("area"), "prompt", txt, []))
    if args.limit:
        jobs = jobs[: args.limit]
    print("jobs:", len(jobs), "env:", env)

    def run(j):
        kind, key, area, form, q, stems = j
        r = ask(q, env)
        r.update({"kind": kind, "key": key, "area": area, "form": form, "prompt_chars": len(q or "")})
        if kind == "answerable":
            r["hit"] = stem_hit(r["titles"], stems)
            r["rank1"] = bool(r["titles"]) and stem_hit(r["titles"][:1], stems)
        elif kind == "unanswerable":
            # 2026-09-12: the bank's absent targets were absent on 09-03; the 09-07 corpus lanes brought several of
            # them in (Pinsker, Le Cam, Shearer, Bochner, Dvoretzky, van der Waerden, Frankl-Wilson ...). A block whose
            # served title carries the target's own stem is the store ANSWERING, not a false fire - counted apart.
            r["target_served"] = bool(r["titles"]) and stem_hit(r["titles"], stems)
        return r

    rows = []
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for r in ex.map(run, jobs):
            rows.append(r)
    with open(args.out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    def pct(n, d):
        return "%d/%d (%.1f%%)" % (n, d, 100.0 * n / d) if d else "0/0"
    summary = {"env": env, "bank": os.path.basename(args.bank), "rows": len(rows)}
    for form in ("q_content", "q_hinted"):
        sub = [r for r in rows if r["kind"] == "answerable" and r["form"] == form]
        hit = sum(1 for r in sub if r["hit"]); r1 = sum(1 for r in sub if r["rank1"]); sil = sum(1 for r in sub if not r["titles"])
        summary[form] = {"n": len(sub), "hit": hit, "rank1": r1, "silent": sil}
        print("%-10s served-theorem %s  rank-1 %s  silent %s" % (form, pct(hit, len(sub)), pct(r1, len(sub)), pct(sil, len(sub))))
    un = [r for r in rows if r["kind"] == "unanswerable"]
    ff = sum(1 for r in un if r["titles"])
    present = sum(1 for r in un if r.get("target_served"))
    summary["unanswerable"] = {"n": len(un), "false_fire": ff, "target_served": present,
                               "strict_false_fire": ff - present, "strict_n": len(un) - present}
    print("unanswerable false fire %s | strict (targets the store now holds excluded: %d) %s"
          % (pct(ff, len(un)), present, pct(ff - present, len(un) - present)))
    lg = [r for r in rows if r["kind"] == "long"]
    if lg:
        served = sum(1 for r in lg if r["titles"])
        summary["long"] = {"n": len(lg), "served": served, "notes": sum(len(r["titles"]) for r in lg)}
        print("long prompts served %s  notes %d" % (pct(served, len(lg)), summary["long"]["notes"]))
    secs = [r["seconds"] for r in rows]
    summary["median_seconds"] = sorted(secs)[len(secs) // 2] if secs else None
    print("median hook seconds", summary["median_seconds"])
    with open(args.out + ".summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)


if __name__ == "__main__":
    main()
