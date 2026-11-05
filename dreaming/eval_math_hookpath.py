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
served title carries a stem; silent = no block. For the unanswerable targets: false fire = any block. A row is scored
only when the hook finished and exited 0 and every card it served is complete (a claim id and a trust tag); a timeout,
a nonzero exit, a launch error, output that is not the hook's JSON, an incomplete card or a block with no readable card
is counted apart and kept out of every rate. The
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
HOOK = os.path.join(HERE, "math_sense.py")
STATE = os.path.join(HERE, "state")
BANK = os.path.join(STATE, "math_probe_fresh_20260903.json")
# The block is read by the shared parser (math_block_parse), which reads both card formats. This file kept a
# private pair of patterns until 30 Sep (account02/B07, B07-R79-MATH-HOOKPATH-EVALUATOR-IGNORES-CHILD-EXIT-056);
# on 238 blocks the live hook served on 29 Sep (577 cards) the two gave the same titles and ids on every block.
sys.path.insert(0, HERE)
from math_block_parse import card_is_complete, delivery_problem, iter_cards  # noqa: E402


import os as _ko_os, sys as _ko_sys  # noqa: E402
_ko_sys.path.insert(0, _ko_os.path.join(_ko_os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (R13 trap 1, 27 Sep): brain data is never deleted


def _key(text):
    folded = unicodedata.normalize("NFKD", text or "")
    out = [ch if ch.isalnum() else " " for ch in folded.casefold() if not unicodedata.combining(ch)]
    return " ".join("".join(out).split())


CALL_TIMEOUT_S = 90   # one hook call; a call that runs past it is asked once more (ask, below)


def _call(prompt, e):
    """(stdout, exit code, stderr tail) of one hook call."""
    out = subprocess.run([sys.executable, HOOK], input=json.dumps({"prompt": prompt}),
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=CALL_TIMEOUT_S, env=e)
    return out.stdout.strip(), out.returncode, (out.stderr or "")[-300:]


def ask(prompt, env):
    e = dict(os.environ)
    e.update(env)
    e["PYTHONIOENCODING"] = "utf-8"
    t0 = time.time()
    # 2026-09-25: a call that times out is asked once more, and a row whose second call times out too is MARKED
    # (timed_out) and counted in the summary. Before, a timeout scored the question silent: in a stall of the laptop
    # (25 Sep 22:2x-22:4x) 27 answerable rows of one arm came back empty at 90-103 s each and read as misses, though a
    # paired re-run served every one. The gate reads a counted timeout as contention, as it reads an instrument
    # timeout (INCONCLUSIVE), never as a regression.
    retried = timed_out = False
    raw, rc, err, launch = "", None, "", ""
    try:
        raw, rc, err = _call(prompt, e)
    except subprocess.TimeoutExpired:
        retried = True
        try:
            raw, rc, err = _call(prompt, e)
        except subprocess.TimeoutExpired:
            timed_out = True
        except OSError as x:
            launch = repr(x)[:160]
    except OSError as x:
        launch = repr(x)[:160]
    dt = time.time() - t0
    # 30 Sep (account02/B07, B07-R79-MATH-HOOKPATH-EVALUATOR-IGNORES-CHILD-EXIT-056): a block counts only from a hook
    # that finished and exited 0 - Claude Code uses a UserPromptSubmit hook's output only on exit 0, so a valid-looking
    # block from a hook that then exited 7 was scored as served here and never reached a session. A nonzero exit, a
    # launch error or output that is not the hook's JSON makes the row invalid: its content is not scored, and the
    # summary keeps it out of every rate and counts it.
    invalid, block = "", ""
    if launch:
        invalid = "launch error: " + launch
    elif not timed_out and rc != 0:
        invalid = "hook exited %s" % rc
    elif raw:
        try:
            block = json.loads(raw)["hookSpecificOutput"]["additionalContext"]
        except Exception:
            invalid = "hook output is not the hook's JSON"
    # 30 Sep (account04/B07, B07-R79-INCOMPLETE-CARD-PASSES-REGRESSION-GATES): a card that lost its id or trust line, or
    # a block with text but no readable card, reached the session broken. Until now the header's title still scored a
    # hit; such a row is a delivery failure, kept out of every rate like a crashed call and counted.
    broken = [] if invalid else [c for c in iter_cards(block) if not card_is_complete(c)]
    if not invalid:
        invalid = delivery_problem(block)
    if invalid:
        block = ""
    cards = list(iter_cards(block))
    r = {"block_chars": len(block), "titles": [c.title for c in cards], "ids": [c.node_id for c in cards if c.node_id],
         "seconds": round(dt, 2), "hook_rc": rc}
    if invalid:
        r["invalid"] = invalid
        r["stderr_tail"] = err[-200:]
        if broken:
            r["incomplete_titles"] = [c.title[:80] for c in broken][:3]
    if retried:
        r["retried"] = True
    if timed_out:
        r["timed_out"] = True
    return r


def valid(r):
    """A row scored as an outcome: the hook finished, exited 0 and printed nothing or its JSON."""
    return not r.get("invalid") and not r.get("timed_out")


def stem_hit(titles, stems):
    keys = [_key(t) for t in titles]
    st = [_key(s) for s in stems]
    return any(any(s in k for s in st) for k in keys)


# THE HOOK THIS RUN SCORES IS NAMED (3 Nov 2026).
# HOOK is the math_sense.py beside this file, so a copy of the eval in another folder scores that folder's copy. On 2 Nov five
# runs from a sandbox scored a 20:05 copy with neither vocab file beside it while being read as the live hook. Every run now
# prints the path, its sha256, whether it is byte-identical to ~/.claude/dreaming/math_sense.py, and any neighbour file missing.
NEIGHBOURS = ("math_anchor_vocab.json", "math_vouch_vocab.json", "math_block_parse.py")


def hook_identity():
    import hashlib
    def sha(p):
        try:
            return hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]
        except OSError:
            return None
    live = os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "math_sense.py")
    h, lv = sha(HOOK), sha(live)
    return {"path": HOOK, "sha256_16": h, "live_sha256_16": lv, "same_as_live": bool(h and h == lv),
            "missing_neighbours": [n for n in NEIGHBOURS if not os.path.exists(os.path.join(HERE, n))]}


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
    ident = hook_identity()
    print("hook %s sha256 %s | same as live: %s%s" % (ident["path"], ident["sha256_16"], ident["same_as_live"],
          (" | MISSING beside it: " + ", ".join(ident["missing_neighbours"])) if ident["missing_neighbours"] else ""), flush=True)

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
    with open(keep_old(args.out), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary, lines = summarize(rows, env, args.bank)
    summary["hook"] = ident
    lines.insert(0, "hook sha256 %s same as live %s" % (ident["sha256_16"], ident["same_as_live"]))
    for line in lines:
        print(line)
    with open(keep_old(args.out + ".summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)


def summarize(rows, env, bank):
    """(summary, printed lines). Every rate is over the rows that were scored (valid); a row whose hook call timed
    out twice, exited nonzero, failed to launch or printed something other than its JSON is kept out of every rate
    and counted beside it (not_scored per form; invalid and timed_out overall)."""
    def pct(n, d):
        return "%d/%d (%.1f%%)" % (n, d, 100.0 * n / d) if d else "0/0"
    lines = []
    summary = {"env": env, "bank": os.path.basename(bank), "rows": len(rows)}
    for form in ("q_content", "q_hinted"):
        sub_all = [r for r in rows if r["kind"] == "answerable" and r["form"] == form]
        sub = [r for r in sub_all if valid(r)]
        hit = sum(1 for r in sub if r["hit"]); r1 = sum(1 for r in sub if r["rank1"]); sil = sum(1 for r in sub if not r["titles"])
        summary[form] = {"n": len(sub), "hit": hit, "rank1": r1, "silent": sil, "not_scored": len(sub_all) - len(sub)}
        lines.append("%-10s served-theorem %s  rank-1 %s  silent %s%s" % (
            form, pct(hit, len(sub)), pct(r1, len(sub)), pct(sil, len(sub)),
            ("  (%d not scored)" % (len(sub_all) - len(sub))) if len(sub_all) > len(sub) else ""))
    un_all = [r for r in rows if r["kind"] == "unanswerable"]
    un = [r for r in un_all if valid(r)]
    ff = sum(1 for r in un if r["titles"])
    present = sum(1 for r in un if r.get("target_served"))
    summary["unanswerable"] = {"n": len(un), "false_fire": ff, "target_served": present,
                               "strict_false_fire": ff - present, "strict_n": len(un) - present,
                               "not_scored": len(un_all) - len(un)}
    lines.append("unanswerable false fire %s | strict (targets the store now holds excluded: %d) %s%s"
                 % (pct(ff, len(un)), present, pct(ff - present, len(un) - present),
                    ("  (%d not scored)" % (len(un_all) - len(un))) if len(un_all) > len(un) else ""))
    lg_all = [r for r in rows if r["kind"] == "long"]
    lg = [r for r in lg_all if valid(r)]
    if lg_all:
        served = sum(1 for r in lg if r["titles"])
        summary["long"] = {"n": len(lg), "served": served, "notes": sum(len(r["titles"]) for r in lg),
                           "not_scored": len(lg_all) - len(lg)}
        lines.append("long prompts served %s  notes %d" % (pct(served, len(lg)), summary["long"]["notes"]))
    n_retry = sum(1 for r in rows if r.get("retried"))
    n_to = sum(1 for r in rows if r.get("timed_out"))
    summary["retried"], summary["timed_out"] = n_retry, n_to
    if n_retry:
        lines.append("hook calls over the %d s cap: %d asked again, %d timed out again (marked timed_out, not scored)"
                     % (CALL_TIMEOUT_S, n_retry, n_to))
    bad = [r for r in rows if r.get("invalid")]
    summary["invalid"] = len(bad)
    if bad:
        reasons = {}
        for r in bad:
            reasons[r["invalid"]] = reasons.get(r["invalid"], 0) + 1
        summary["invalid_reasons"] = dict(sorted(reasons.items(), key=lambda kv: -kv[1])[:5])
        lines.append("hook calls not delivered whole: %d, not scored (%s)" % (
            len(bad), "; ".join("%s: %d" % kv for kv in summary["invalid_reasons"].items())))
    secs = [r["seconds"] for r in rows]
    summary["median_seconds"] = sorted(secs)[len(secs) // 2] if secs else None
    lines.append("median hook seconds %s" % summary["median_seconds"])
    return summary, lines


if __name__ == "__main__":
    main()
