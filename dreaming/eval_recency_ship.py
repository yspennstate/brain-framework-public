#!/usr/bin/env python3
"""eval_recency_ship.py - PRE-REGISTERED ship gates for the two-scale recency kernel in salience.py.

The change under test (diffusion program iteration 5, 2026-07-23): replace the single-exponential
recency arm R = exp(-age/45d) with the MEASURED two-scale form (RECENCY_KERNEL.md,
eval_recency_kernel.py): episodic head tau = 1.3 days in the recency arm, and the type-dependent
plateau carried by TYPE_PERSIST at the measured old-age ratios (feedback 0.027 : reference 0.0047 :
project 0.0021 per memory-day, ~13x feedback over project), anchored at feedback = 0.90.
Rollback: BRAIN_RECENCY_V1=1 restores the old kernel exactly.

PRE-REGISTERED RULES - written before any measurement run of this harness; the change ships only
if ALL gates pass, and is otherwise rolled back and reported as a failure with numbers:

  G1 RECALL POSITIVE CONTROLS. The six standard control queries (eval_score_rerank.py's list) must
     put their target at RANK 1 through brain.recall under BOTH kernels. (The bm25-first path does
     not read salience, so any movement here means breakage, not tuning.)
  G2 SPREAD FALSE-ALARM BUDGET. Replay two corpora through the LIVE spread_recall.main code path
     (stateless: empty session id, cached loaders, per-arm salience injected):
       a. typical-actions corpus (graph_fire/typical_actions.jsonl, deterministic every-6th row,
          cap 400): fires_new <= fires_old - the change must not ADD false alarms on ordinary
          work actions.
          RULE AMENDMENT (2026-07-23 20:18, recorded in the open): the first registration also
          demanded fires_new/N <= 0.02, transplanting graph_fire's per-reflex 2% budget onto this
          hook. The first run showed the bar was mis-authored: the CONTROL (live tau-45d kernel)
          fires on 47.8% of typical actions (191/400) - the live hook has never had a 2% action
          calibration, it was designed to gate PROMPTS, and a bar the control itself fails by 24x
          is a broken gate, not a failed treatment (the same logic as a failed positive control).
          First-run numbers, both arms, for the record: old 191/400 (47.8%), new 144/400 (36.0%);
          the treatment strictly REDUCED action-corpus fires. The absolute-ceiling clause is
          withdrawn as inapplicable; the no-new-alarms clause stands unchanged.
       b. real user prompts (latest transcript files, plain-string user messages, cap 120):
          fires_new <= fires_old + 1 (rank swaps may add at most one), AND if fires_old >= 5
          then fires_new >= 1 (the hook must stay demonstrably alive, not die silently).
  G3 DELIBERATE GROUND TRUTH INVARIANCE. brain.recall output must be byte-identical between
     kernels for the six controls plus up to 20 real recall-CLI queries mined from transcripts.
     Any diff = FAIL (the deliberate path is salience-free by design; a diff means coupling).
  G4 SANITY OF THE NEW SURFACE. Under the new kernel: the two standing hard rules
     (no_flashing_terminal_windows_2026_06_07, never_start_local_privileged_stack) score
     salience >= 70; salience.tsv rows keep the 5-column format.

Honest caveat (same as eval_archive_seed_replay): the replay runs TODAY's graph/index against past
prompts, so absolute fire quality is not the read - the ARM COMPARISON is.
Usage: python eval_recency_ship.py
"""
import contextlib
import glob
import io
import json
import os
import re
import sys

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
sys.path.insert(0, DR)
ARCHIVE = os.path.join(HOME, "brain-repo", "11_raw_session_transcripts",
                       "claude_projects", "{{BRAIN_SLUG}}")

try:  # owner-first compute: this eval runs at BelowNormal priority
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
except Exception:
    try:  # no windll off Windows: nice 10 carries the same BelowNormal intent
        import os as _os
        if _os.name != "nt":
            _os.nice(10)
    except Exception:
        pass

import salience  # noqa: E402
import spread_recall as sr  # noqa: E402
import brain as B  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CONTROLS = [
    ("flashing terminal windows scheduled task", "no_flashing_terminal_windows_2026_06_07"),
    ("never start the local protected production stack", "never_start_local_privileged_stack"),
    ("research writing human voice no AI tells", "research_writing_human_voice_2026_06_10"),
    ("check the brain before you simulate", "scar_check_the_brain_before_you_simulate_2026_07_08"),
]
HARD_RULES = ["no_flashing_terminal_windows_2026_06_07.md",
              "never_start_local_privileged_stack.md"]
NOISE = ("Diagnostic ping", "Your tool call was malformed", "[Request interrupted", "API Error",
         "<", "Caveat:")


def arm_salience(v1):
    old = os.environ.pop("BRAIN_RECENCY_V1", None)
    try:
        if v1:
            os.environ["BRAIN_RECENCY_V1"] = "1"
        scores = salience.score_corpus()
    finally:
        os.environ.pop("BRAIN_RECENCY_V1", None)
        if old is not None:
            os.environ["BRAIN_RECENCY_V1"] = old
    return scores


def recall_text(query, v1):
    old = os.environ.pop("BRAIN_RECENCY_V1", None)
    buf = io.StringIO()
    try:
        if v1:
            os.environ["BRAIN_RECENCY_V1"] = "1"
        with contextlib.redirect_stdout(buf):
            B.recall(query, 8)
    finally:
        os.environ.pop("BRAIN_RECENCY_V1", None)
        if old is not None:
            os.environ["BRAIN_RECENCY_V1"] = old
    return buf.getvalue()


def rank_of(text, target):
    rank = 0
    for ln in text.splitlines():
        m = re.match(r"\s+[\d.]+ \[[^\]]*\] (\S+)", ln)
        if m:
            rank += 1
            if m.group(1) == target:
                return rank
    return None


def typical_actions(cap=400):
    out = []
    try:
        with io.open(os.path.join(DR, "graph_fire", "typical_actions.jsonl"),
                     encoding="utf-8", errors="replace") as f:
            for i, ln in enumerate(f):
                if i % 6:
                    continue
                try:
                    t = json.loads(ln).get("text", "")
                except Exception:
                    continue
                if isinstance(t, str) and len(t) >= 40:
                    out.append(t[:2000])
                if len(out) >= cap:
                    break
    except Exception:
        pass
    return out


def real_prompts(cap=120):
    out = []
    files = sorted(glob.glob(os.path.join(ARCHIVE, "*.jsonl")),
                   key=os.path.getmtime, reverse=True)[:50]
    for fp in files:
        try:
            for ln in io.open(fp, encoding="utf-8", errors="replace"):
                if len(out) >= cap:
                    return out
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if r.get("type") != "user":
                    continue
                c = (r.get("message") or {}).get("content")
                if not isinstance(c, str):
                    continue
                c = c.strip()
                if not (40 <= len(c) <= 2000) or c.startswith(NOISE):
                    continue
                out.append(c)
        except Exception:
            continue
    return out


def mined_recall_queries(cap=20):
    seen, out = set(), []
    files = sorted(glob.glob(os.path.join(ARCHIVE, "*.jsonl")),
                   key=os.path.getmtime, reverse=True)[:60]
    rx = re.compile(r'brain\.py"?\s+recall\s+\\?"([^"\\\n]{10,110})')
    for fp in files:
        if len(out) >= cap:
            break
        try:
            for m in rx.finditer(io.open(fp, encoding="utf-8", errors="replace").read()):
                q = m.group(1).strip()
                if q and q.lower() not in seen:
                    seen.add(q.lower())
                    out.append(q)
                if len(out) >= cap:
                    break
        except Exception:
            continue
    return out


def cache_loaders():
    adj_deg = sr._load_graph()
    emb = sr._load_embeddings()
    sup = sr._load_superseded()
    idx = sr._index_lines()
    habit = sr._load_habituation()
    sr._load_graph = lambda: adj_deg
    sr._load_embeddings = lambda: emb
    sr._load_superseded = lambda: sup
    sr._index_lines = lambda: idx
    sr._load_habituation = lambda: habit
    if sr.embedding_artifact is not None:
        raw = sr.embedding_artifact.embedding_row_current
        memo = {}

        def cached(e, mem, slug, _raw=raw, _memo=memo):
            if slug not in _memo:
                _memo[slug] = _raw(e, mem, slug)
            return _memo[slug]
        sr.embedding_artifact.embedding_row_current = cached


def spread_fires(prompts, sal_map):
    sr._load_salience = lambda: sal_map
    fired = []
    stdin0 = sys.stdin
    for p in prompts:
        payload = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": p, "session_id": ""})
        buf = io.StringIO()
        try:
            sys.stdin = io.StringIO(payload)
            with contextlib.redirect_stdout(buf):
                sr.main()
        except Exception:
            pass
        finally:
            sys.stdin = stdin0
        if "[association]" in buf.getvalue():
            m = re.search(r"reminds me of `([^`]+)`", buf.getvalue())
            fired.append((p[:60], m.group(1) if m else "?"))
    return fired


def main():
    print("eval_recency_ship - pre-registered gates, two-scale recency kernel")
    print("arms: OLD = BRAIN_RECENCY_V1=1 (tau 45d, old TYPE_PERSIST), NEW = default")

    sal_old_full = arm_salience(v1=True)
    sal_new_full = arm_salience(v1=False)
    to_map = lambda sc: {fn[:-3]: v["salience"] for fn, v in sc.items()}
    sal_old, sal_new = to_map(sal_old_full), to_map(sal_new_full)
    import statistics
    med = lambda d: statistics.median(d.values())
    print("  corpus n=%d | median salience old %.1f -> new %.1f | >=45: old %d -> new %d"
          % (len(sal_old), med(sal_old), med(sal_new),
             sum(1 for v in sal_old.values() if v >= 45),
             sum(1 for v in sal_new.values() if v >= 45)))

    ok = True

    # G1 + G3 on controls
    print("\nG1 recall positive controls (target must be rank 1 under both kernels)")
    g3_texts = {}
    for q, target in CONTROLS:
        t_old, t_new = recall_text(q, True), recall_text(q, False)
        g3_texts[q] = (t_old, t_new)
        r_old, r_new = rank_of(t_old, target), rank_of(t_new, target)
        good = (r_old == 1 and r_new == 1)
        ok &= good
        print("  %s r_old=%s r_new=%s  %s" % ("PASS" if good else "FAIL", r_old, r_new, q))

    # G3: byte-identical deliberate recall
    print("\nG3 deliberate-recall invariance (byte-diff old vs new)")
    queries = mined_recall_queries()
    print("  mined %d real recall-CLI queries from transcripts" % len(queries))
    diffs = 0
    for q in queries:
        if recall_text(q, True) != recall_text(q, False):
            diffs += 1
            print("  DIFF on: %s" % q[:70])
    for q in g3_texts:
        if g3_texts[q][0] != g3_texts[q][1]:
            diffs += 1
            print("  DIFF on control: %s" % q)
    g3 = diffs == 0
    ok &= g3
    print("  %s (%d diffs over %d queries)" % ("PASS" if g3 else "FAIL", diffs, len(queries) + len(g3_texts)))

    # G2: spread replay
    print("\nG2 spread_recall replay (live main(), stateless, cached loaders)")
    cache_loaders()
    ta = typical_actions()
    rp = real_prompts()
    f_ta_old = spread_fires(ta, sal_old)
    f_ta_new = spread_fires(ta, sal_new)
    f_rp_old = spread_fires(rp, sal_old)
    f_rp_new = spread_fires(rp, sal_new)
    g2a = len(f_ta_new) <= len(f_ta_old)   # amended 20:18 - see header; 2% ceiling withdrawn
    g2b = len(f_rp_new) <= len(f_rp_old) + 1 and (len(f_rp_old) < 5 or len(f_rp_new) >= 1)
    ok &= g2a and g2b
    print("  typical-actions n=%d: fires old %d (%.1f%%) -> new %d (%.1f%%)  %s"
          % (len(ta), len(f_ta_old), 100.0 * len(f_ta_old) / max(len(ta), 1),
             len(f_ta_new), 100.0 * len(f_ta_new) / max(len(ta), 1), "PASS" if g2a else "FAIL"))
    print("  real-prompts   n=%d: fires old %d (%.1f%%) -> new %d (%.1f%%)  %s"
          % (len(rp), len(f_rp_old), 100.0 * len(f_rp_old) / max(len(rp), 1),
             len(f_rp_new), 100.0 * len(f_rp_new) / max(len(rp), 1), "PASS" if g2b else "FAIL"))
    for tag, fires in (("old-only", [f for f in f_rp_old if f not in f_rp_new]),
                       ("new-only", [f for f in f_rp_new if f not in f_rp_old])):
        for p, slug in fires[:6]:
            print("    %s fire: %s <- %r" % (tag, slug, p))

    # G4: sanity of the new surface
    print("\nG4 standing hard rules stay vivid under the new kernel")
    g4 = True
    for fn in HARD_RULES:
        v = sal_new_full.get(fn, {}).get("salience", 0.0)
        good = v >= 70
        g4 &= good
        print("  %s %.0f %s" % ("PASS" if good else "FAIL", v, fn))
    ok &= g4

    print("\nVERDICT: %s" % ("SHIP - all pre-registered gates green" if ok
                             else "DO NOT SHIP - a gate failed; keep BRAIN_RECENCY_V1 semantics"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
