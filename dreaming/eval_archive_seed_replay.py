#!/usr/bin/env python3
"""eval_archive_seed_replay.py - should the live hooks seed from INDEX_ARCHIVE.md lines too?

The filed question (OPEN_QUESTIONS 2026-07-09): compaction archived a memory's index line the same
night a replayed owner prompt needed it - the hook fell back to weaker slug-word seeding. brain.py
`recall` (the pull side) already searches both; the HOOK side was left to "decide with the judged
replay, not by instinct."

Mechanical prior worth stating: the BM25 arm of _seeds_from_prompt indexes every memory BODY
(read_docs skips nothing but SKIP_FILES/dream_*), so an archived memory can already seed through
BM25 - only the INDEX-LINE arm (overlap>=2 against MEMORY.md description lines) is blind to the
archive. So the honest first read is a DIFF COUNT: replay real prompts through spread_recall twice -
arm A = stock _index_lines (MEMORY.md only), arm B = MEMORY.md + INDEX_ARCHIVE.md (live wins on
slug collision) - and count how many fires change at all. If ~nothing changes, the question closes
as null (BM25 already covers the archive). If fires differ, the changed pairs go to a blind judge
sheet (arm labels hidden) and the helpful-rate of B-only vs A-only fires decides.

Same in-process harness as eval_llm_judge_replay (EMB=production bge, STATE=scratch, non-uuid
session ids so no live state is touched). Same causality caveat: TODAY's graph/index against past
prompts - the ARM COMPARISON is the valid read, not absolute fire quality.
Usage: python eval_archive_seed_replay.py [--files N]
"""
import glob
import io
import json
import os
import re
import sys

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
SCRATCH = os.environ.get("ARCHIVE_REPLAY_SCRATCH") or os.path.join(
    HOME, "AppData", "Local", "Temp", "claude", "{{BRAIN_SLUG}}",
    "dabc7e39-9830-4a74-8650-34f449ad9c7b", "scratchpad")
sys.path.insert(0, DR)
import spread_recall as sr

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BAD_STARTS = ("[", "<", "Stop hook", "Caveat", "API Error", "This session is being continued")


def harvest(n_files=40, lo=25, hi=600):
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
    # transcripts are RAW - a pasted key rides along unless redacted (the RunPod-key scar came from
    # exactly this surface). Route every harvested prompt through prompt_archive's live redaction;
    # the persisted replay rows get mirrored to the pushed repo, so this must not fail open silently.
    try:
        from prompt_archive import redact
        out = [redact(t)[0] for t in out]   # redact() returns (text, changed)
    except Exception:
        print("WARNING: prompt_archive.redact unavailable - harvested prompts are UNREDACTED; "
              "do not persist rows beyond the scratchpad without a scan_secrets pass.")
    return out


_stock_index_lines = sr._index_lines


def _index_lines_with_archive():
    """MEMORY.md lines + INDEX_ARCHIVE.md lines; the live index wins a slug collision."""
    out = {}
    try:
        with io.open(os.path.join(MEM, "INDEX_ARCHIVE.md"), encoding="utf-8", errors="replace") as f:
            for ln in f:
                m = re.match(r"- \[([^\]]+?)\.md\]", ln)
                if m:
                    out[m.group(1)] = ln.strip()
    except Exception:
        pass
    out.update(_stock_index_lines())
    return out


def run_arm(prompt, use_archive, i):
    sr.EMB = os.path.join(DR, "memory_embeddings.npz")          # production sidecar
    sr.STATE = os.path.join(SCRATCH, ".spread_fired_arcreplay.json")
    sr._index_lines = _index_lines_with_archive if use_archive else _stock_index_lines
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": prompt,
               "session_id": "arcrep-%s-%d" % ("b" if use_archive else "a", i)}
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
    n_files = 40
    if "--files" in sys.argv:
        try:
            n_files = int(sys.argv[sys.argv.index("--files") + 1])
        except Exception:
            pass
    prompts = harvest(n_files=n_files)
    print("replaying %d prompts x 2 arms (A=live index, B=live+archive) from %d files" % (len(prompts), n_files))
    rows = []
    for i, p in enumerate(prompts):
        a = run_arm(p, False, i)
        b = run_arm(p, True, i)
        rows.append({"prompt": p, "a": a, "b": b})
    fa = sum(1 for r in rows if r["a"])
    fb = sum(1 for r in rows if r["b"])
    diff = [r for r in rows if r["a"] != r["b"]]
    print("fires: A=%d  B=%d  of %d prompts;  DIFFERING outcomes: %d" % (fa, fb, len(rows), len(diff)))
    for r in diff:
        print("  A=%-52s B=%-52s" % (str(r["a"])[:52], str(r["b"])[:52]))
        print("    prompt: %s" % r["prompt"][:130])
    json.dump(rows, io.open(os.path.join(SCRATCH, "archive_replay.json"), "w", encoding="utf-8"))
    if not diff:
        print("\nVERDICT-SHAPE: null - archive index lines change nothing the hook says; "
              "BM25 (which already reads every memory body) covers the archive. Close the question.")
    else:
        print("\n%d differing outcomes -> judge them blind before wiring (helpful/tangential/wrong)." % len(diff))


if __name__ == "__main__":
    main()
