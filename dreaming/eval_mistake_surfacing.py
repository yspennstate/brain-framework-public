#!/usr/bin/env python3
"""eval_mistake_surfacing.py - A/B replay for the mistake-first surfacing change (2026-07-25).

Two arms, byte-identical except mistake_weights.tsv: OFF patches sr.MW to a nonexistent path
(the fail-open zero map = pre-change behaviour), ON uses the built artifact. Same prompts, same
graph, same sidecar, scratch dedup state, replay-shaped session ids so no live ledger is touched
(the ledgers-polluted-by-own-gate scar). The arm COMPARISON is the valid read, not absolute
historical quality - today's graph against old prompts (same caveat as eval_llm_judge_replay).

Reported:
  fire counts per arm, and the mistake-class share of fires (sev>=0.5 / >=0.8)
  displacements: prompts where ON fires a different slug than OFF (the precision risk surface -
    eyeball these in mistake_surfacing_diff.json before believing the change)
  rank probe: per prompt, best raw-activation rank of any sev>=0.8 node in the pre-gate pool,
    OFF vs ON - "surfaces faster" measured directly as rank/reach, independent of the speak gate

Run: python eval_mistake_surfacing.py [--files N]   (default 25 transcript files)
"""
import glob
import io
import json
import os
import sys

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
sys.path.insert(0, DR)
import spread_recall as sr

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BAD_STARTS = ("[", "<", "Stop hook", "Caveat", "API Error", "This session is being continued")
MW_REAL = os.path.join(DR, "mistake_weights.tsv")
MW_OFF = os.path.join(DR, "no_such_mistake_weights.tsv")
STATE_SCRATCH = os.path.join(DR, ".spread_fired_mw_replay.json")
DIFF_OUT = os.path.join(DR, "mistake_surfacing_diff.json")


def harvest(n_files=25, lo=25, hi=600):
    files = sorted(glob.glob(os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "*.jsonl")),
                   key=os.path.getmtime)
    seen, out = set(), []
    for fp in files[-n_files:]:
        try:
            for ln in io.open(fp, encoding="utf-8", errors="replace"):
                if '"user"' not in ln:  # every user record carries "type":"user"; skip the rest unparsed
                    continue
                try:
                    o = json.loads(ln)
                except Exception:
                    continue
                if o.get("type") != "user":
                    continue
                c = (o.get("message", {}) or {}).get("content")
                if isinstance(c, str):
                    txt = c
                elif isinstance(c, list):
                    txt = " ".join(x.get("text", "") for x in c
                                   if isinstance(x, dict) and x.get("type") == "text")
                else:
                    continue
                t = " ".join(txt.split())
                if not (lo <= len(t) <= hi) or any(t.startswith(b) for b in BAD_STARTS):
                    continue
                key = t[:60].lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(t)
        except Exception:
            pass
    try:
        from prompt_archive import redact
        out = [redact(t)[0] for t in out]
    except Exception:
        print("WARNING: prompt_archive.redact unavailable - prompts UNREDACTED; scan before persisting.")
    return out


ARCHIVE = os.path.join(HOME, "brain", "brain-repo", "01_ai_consciousness", "claude", "memory",
                       "prompts", "archive")


def harvest_archive(n, exclude=(), lo=25, hi=600):
    """Up to n owner prompts from the prompt archive shards, newest first, with harvest()'s filters.

    2026-10-29: the Mac keeps one transcript (most sessions write none), so harvest() found 8 prompts and the
    regression gate's recall check sat INCOMPLETE. The archive holds the owner's words from every machine; this
    tops a sample up from it. harvest() itself is unchanged for its other callers."""
    seen = {t[:60].lower() for t in exclude}
    out = []
    for fp in sorted(glob.glob(os.path.join(ARCHIVE, "PROMPTS_2*.jsonl")), reverse=True):
        try:
            lines = io.open(fp, encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            continue
        for ln in reversed(lines):
            try:
                o = json.loads(ln)
            except Exception:
                continue
            if o.get("origin") != "owner" or o.get("repeat_of"):
                continue
            t = " ".join(str(o.get("text", "")).split())
            if not (lo <= len(t) <= hi) or any(t.startswith(b) for b in BAD_STARTS):
                continue
            key = t[:60].lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(t)
            if len(out) >= n:
                break
        if len(out) >= n:
            break
    try:
        from prompt_archive import redact
        out = [redact(t)[0] for t in out]
    except Exception:
        print("WARNING: prompt_archive.redact unavailable - prompts UNREDACTED; scan before persisting.")
    return out


def run_arm(prompt, mw_path, label, i):
    sr.MW = mw_path
    sr.STATE = STATE_SCRATCH
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": prompt,
               "session_id": "replay-%s-%d" % (label, i)}
    old_in, old_out = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(json.dumps(payload))
    sys.stdout = cap = io.StringIO()
    try:
        sr.main()
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    out = cap.getvalue().strip()
    if not out:
        return None
    try:
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        import re
        m = re.search(r"this reminds me of `([^`]+)`", ctx)
        return m.group(1) if m else None
    except Exception:
        return None


def rank_probe(prompts, weights):
    """Pre-gate reach: best raw-activation rank of any sev>=0.8 node, OFF vs ON, per prompt.
    Direct _spread calls on identical seeds - supersession/scoring left out on purpose; both
    arms see the same simplification so the DELTA is clean."""
    idx = sr._index_lines()
    adj, deg = sr._load_graph()
    big = {s for s, v in weights.items() if v >= sr.MW_SEV_BIG}
    deltas, reached_off, reached_on = [], 0, 0
    for p in prompts:
        seeds, _ = sr._seeds_from_prompt(sr._words(p), idx)
        if not seeds:
            continue

        def best_rank(mw):
            act, _ = sr._spread(seeds, adj, deg, mw)
            order = sorted(((a, s) for s, a in act.items() if s not in seeds), reverse=True)
            for r, (_, s) in enumerate(order, 1):
                if s in big:
                    return r
            return None

        r_off, r_on = best_rank({}), best_rank(weights)
        if r_off is not None:
            reached_off += 1
        if r_on is not None:
            reached_on += 1
        if r_off is not None and r_on is not None:
            deltas.append(r_off - r_on)   # positive = the scar moved UP under the change
    return deltas, reached_off, reached_on


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    n_files = 25
    if "--files" in sys.argv:
        try:
            n_files = int(sys.argv[sys.argv.index("--files") + 1])
        except Exception:
            pass
    if not os.path.exists(MW_REAL):
        raise SystemExit("mistake_weights.tsv missing - run build_mistake_weights.py first")
    sr.MW = MW_REAL
    weights = sr._load_mistake_weights()
    prompts = harvest(n_files=n_files)
    print("replaying %d prompts x 2 arms (from %d transcript files); %d weighted nodes, %d big"
          % (len(prompts), n_files, len(weights),
             sum(1 for v in weights.values() if v >= sr.MW_SEV_BIG)))
    try:
        os.remove(STATE_SCRATCH)
    except OSError:
        pass
    rows = []
    for i, p in enumerate(prompts):
        off = run_arm(p, MW_OFF, "mwoff", i)
        on = run_arm(p, MW_REAL, "mwon", i)
        rows.append({"prompt": p, "off": off, "on": on})
    n = len(rows)
    fires_off = sum(1 for r in rows if r["off"])
    fires_on = sum(1 for r in rows if r["on"])

    def mshare(arm, floor):
        return sum(1 for r in rows if r[arm] and weights.get(r[arm], 0.0) >= floor)

    print("fires: OFF %d/%d  ON %d/%d  (rate ratio %.2f)"
          % (fires_off, n, fires_on, n, (fires_on / fires_off) if fires_off else float("inf")))
    print("mistake-class fires (sev>=0.5): OFF %d  ON %d   (sev>=0.8): OFF %d  ON %d"
          % (mshare("off", 0.5), mshare("on", 0.5), mshare("off", 0.8), mshare("on", 0.8)))
    diff = [r for r in rows if r["off"] != r["on"]]
    json.dump(diff, io.open(DIFF_OUT, "w", encoding="utf-8"), indent=1)
    print("arms disagree on %d prompts -> %s (eyeball before believing)" % (len(diff), DIFF_OUT))
    deltas, roff, ron = rank_probe(prompts, weights)
    if deltas:
        deltas.sort()
        med = deltas[len(deltas) // 2]
        up = sum(1 for d in deltas if d > 0)
        print("rank probe (pre-gate, sev>=0.8): reachable OFF %d ON %d of %d seeded prompts; "
              "median rank shift %+d (up on %d/%d)"
              % (roff, ron, len(deltas) and len(prompts), med, up, len(deltas)))
    else:
        print("rank probe: no seeded prompt reaches a big mistake node in either arm")


if __name__ == "__main__":
    main()
