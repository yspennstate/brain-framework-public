#!/usr/bin/env python3
"""Replay real prompts through graph-only versus the verified active BGE authority.

Causality note: the graph, salience and index are TODAY'S state for all arms, so the comparison
isolates the SIDECAR's marginal effect on which association (if any) speaks. It does not measure
absolute historical fire quality. In-process, with EMB/STATE patched to scratch - the live hook
files and dedup state are never touched.

FIRST RUN 2026-07-09 (34 prompts, 16 fires judged): bge LOOKED worse (helpful-rate 0.57 vs LSA
0.67) on n=7 fires - kept as a small-sample cautionary tale; the real run below inverts it.

SECOND RUN, same night (--files 120: 416 prompts, 104 distinct fires judged blind, single judge):
  helpful-rate: graph-only 16/61=0.262, LSA 17/79=0.215, bge 14/55=0.255
  soft (helpful=1, tangential=0.5): 0.369 / 0.285 / 0.373
  head-to-head on the 57 prompts where LSA and bge differ: bge's silences removed 29 WRONG fires
  vs 7 helpful lost (about 4:1); bge's added fires: 4 helpful, 6 tangential, 6 wrong.
  VERDICT: bge's margin compression is precision-POSITIVE - what it silences is mostly fires that
  were wrong. Do NOT retune MARGIN to restore the LSA-era fire rate; the lower rate is the point.
  Caveats: absolute rates are pessimistic by construction (TODAY's graph replayed against
  months-old prompts - the arm COMPARISON is the valid read, not the level); single judge; harness
  noise prompts (diagnostic pings, malformed-tool echoes) can fire and land in the denominator.
  Protocol: run this (--files N to widen), fill VERDICT lines in judge_sheet.md
  (helpful/tangential/wrong), unblind via replay_fires.json (verdicts saved judge_verdicts*.json).
"""
import glob
import io
import json
import os
import sys

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
SCRATCH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DR)
import spread_recall as sr

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BAD_STARTS = ("[", "<", "Stop hook", "Caveat", "API Error", "This session is being continued")


def harvest(n_files=25, lo=25, hi=600):
    files = sorted(glob.glob(os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "*.jsonl")),
                   key=os.path.getmtime)
    seen, out = set(), []
    for fp in files[-n_files:]:
        try:
            for ln in io.open(fp, encoding="utf-8", errors="replace"):
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
    # transcripts are RAW - a pasted key rides along unless redacted (the RunPod-key scar's surface);
    # replay_fires.json/judge_sheet.md may be persisted/mirrored, so redact at harvest (2026-07-09).
    try:
        from prompt_archive import redact
        out = [redact(t)[0] for t in out]   # redact() returns (text, changed)
    except Exception:
        print("WARNING: prompt_archive.redact unavailable - harvested prompts are UNREDACTED; "
              "scan before persisting anything derived from them.")
    return out


ARMS = {
    "none": None,
    "bge": os.path.join(DR, "memory_embeddings.npz"),
}
AUTHORITY = os.path.join(DR, ".embedding_current.json")


def run_arm(prompt, arm, i):
    sr.EMB = ARMS[arm] or os.path.join(SCRATCH, "no_such_sidecar.npz")
    sr.EMB_AUTHORITY = AUTHORITY if arm == "bge" else os.path.join(
        SCRATCH, "no_such_authority.json"
    )
    sr.STATE = os.path.join(SCRATCH, ".spread_fired_replay.json")
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": prompt,
               "session_id": "replay-%s-%d" % (arm, i)}
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
    prompts = harvest(n_files=n_files)
    try:
        import embedding_artifact
        _, receipt = embedding_artifact.resolve_authority_sidecar(ARMS["bge"], AUTHORITY)
        loaded = embedding_artifact.load_verified_embeddings(
            ARMS["bge"], AUTHORITY, sr.MEM, sr.EXPECTED_EMB_MODEL, verify_all_rows=True
        )
        if loaded is None or not loaded[0]:
            raise ValueError("authority bytes or exact current rows failed consumer preflight")
        print("embedding_generation=%s model=%s active_rows=%d" %
              (receipt["sidecar_sha256"][:12], receipt["model"], len(loaded[0])))
    except Exception as exc:
        raise SystemExit("no verified active BGE authority: %s" % exc)
    print("replaying %d prompts x %d arms (from %d transcript files)" %
          (len(prompts), len(ARMS), n_files))
    rows = []
    for i, p in enumerate(prompts):
        r = {"prompt": p}
        for arm in ARMS:
            r[arm] = run_arm(p, arm, i)
        rows.append(r)
    fired = {a: sum(1 for r in rows if r[a]) for a in ARMS}
    print("fire counts:", fired, "of", len(prompts))
    diff = [r for r in rows if len({r[a] for a in ARMS}) > 1]
    print("prompts where arms disagree: %d" % len(diff))
    json.dump(rows, io.open(os.path.join(SCRATCH, "replay_fires.json"), "w", encoding="utf-8"))
    # judging sheet: every distinct (prompt, slug) pair, arm labels hidden, shuffled deterministically
    pairs = sorted({(r["prompt"], r[a]) for r in rows for a in ARMS if r[a]})
    import random
    random.Random(5).shuffle(pairs)
    with io.open(os.path.join(SCRATCH, "judge_sheet.md"), "w", encoding="utf-8") as f:
        f.write("# Blind judging sheet - is the fired memory HELPFUL for this prompt?\n"
                "# (helpful = reading it would materially inform the turn; tangential; wrong)\n\n")
        for k, (p, s) in enumerate(pairs):
            f.write("## %d\nPROMPT: %s\nFIRED: %s\nVERDICT: \n\n" % (k, p, s))
    print("wrote replay_fires.json + judge_sheet.md (%d distinct pairs to judge)" % len(pairs))


if __name__ == "__main__":
    main()
