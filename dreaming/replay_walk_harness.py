#!/usr/bin/env python3
"""replay_walk_harness.py - side-effect-free replay+judge harness for spread_recall's WALK.

The dream ledger's standing condition: the walk stays untouched until it has its own
replay+judge harness. This is that harness's v1.

Method
  - SANDBOX: copy every DR-rooted input spread_recall reads into a scratch dir, patch
    spread_recall.DR there, and truncate the two habituation sources (surfaced.tsv,
    retrievals.tsv) to rows BEFORE the replay window so cross-session habituation
    evolves along the replay instead of leaking the future backwards.
  - REPLAY: feed each historical owner prompt (PROMPTS.jsonl: session, ts, text)
    chronologically through spread_recall.main() with captured stdin/stdout; fires
    append to the SANDBOX surfaced.tsv only.
  - JUDGE: a fire scores a HIT if the surfaced slug is read by that same session
    within HORIZON after the prompt (retrievals.tsv, real rows), counting only
    novel reads (not read by that session before the prompt).
  - FIDELITY: replayed fire set vs the real surfaced.tsv rows in the window - the
    calibration that says the sandbox reproduces the live walk before any variant
    is trusted.

Stated limitation (same family as eval_prompt_recall's caveat): edges, salience and
the corpus are TODAY's; a past prompt is replayed against a slightly newer graph.
Fidelity quantifies how much that matters instead of assuming.

Usage: python replay_walk_harness.py [--since 2026-07-20] [--limit 100]
Read-only outside its sandbox. No model calls.
"""
import argparse
import io
import json
import os
import shutil
import sys
import time
from collections import defaultdict

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
LEDGER = os.path.join(MEM, "prompts", "PROMPTS.jsonl")
HORIZON_S = 3600.0

SANDBOX_INPUTS = [
    "spread_edges.tsv", "salience.tsv", "mistake_weights.tsv",
    "keyword_nodes.tsv", "keyword_related.tsv", "keyword_graph_edges.tsv",
    "superseded.tsv", ".embedding_current.json",
]
TRUNCATED = ["surfaced.tsv", "retrievals.tsv"]


def build_sandbox(base, since_epoch):
    os.makedirs(base, exist_ok=True)
    for fn in SANDBOX_INPUTS:
        src = os.path.join(DR, fn)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(base, fn))
    for fn in TRUNCATED:
        src = os.path.join(DR, fn)
        dst = os.path.join(base, fn)
        kept = 0
        with io.open(src, encoding="utf-8", errors="replace") as f, \
                io.open(dst, "w", encoding="utf-8") as out:
            for i, ln in enumerate(f):
                if i == 0 and fn == "retrievals.tsv":
                    out.write(ln)
                    continue
                p = ln.rstrip("\n").split("\t")
                try:
                    ep = float(p[1]) if fn == "retrievals.tsv" else float(p[0])
                except (ValueError, IndexError):
                    continue
                if ep < since_epoch:
                    out.write(ln)
                    kept += 1
        print(f"  sandbox {fn}: kept {kept} pre-window rows")
    fired = os.path.join(base, ".spread_fired.json")
    io.open(fired, "w", encoding="utf-8").write("{}")


def load_episodes(since_iso, limit):
    eps = []
    for ln in io.open(LEDGER, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("origin", "owner") != "owner":
            continue
        ts, sess, text = r.get("ts") or "", r.get("session") or "", r.get("text") or ""
        if not (ts and sess and len(text) >= 30):
            continue
        if ts < since_iso:
            continue
        eps.append((ts, sess, text))
    eps.sort()
    return eps[:limit] if limit else eps


def load_reads():
    reads = defaultdict(list)   # session12 -> [(epoch, slug)]
    with io.open(os.path.join(DR, "retrievals.tsv"), encoding="utf-8", errors="replace") as f:
        for i, ln in enumerate(f):
            if i == 0:
                continue
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 4:
                try:
                    reads[p[3][:12]].append((float(p[1]), p[0]))
                except ValueError:
                    pass
    for v in reads.values():
        v.sort()
    return reads


def iso_to_epoch(iso):
    import datetime
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def replay(module, payload):
    old_in, old_out = sys.stdin, sys.stdout
    cap = io.StringIO()
    try:
        sys.stdin = io.StringIO(json.dumps(payload, ensure_ascii=False))
        sys.stdout = cap
        module.main()
    except Exception:
        return None
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    raw = cap.getvalue()
    if not raw:
        return None
    try:
        ctx = json.loads(raw)["hookSpecificOutput"].get("additionalContext", "")
    except Exception:
        return None
    return ctx or None


def surfaced_slug(ctx):
    import re
    m = re.search(r"`([a-zA-Z0-9_\-]+)`", ctx or "")
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-07-20")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--sandbox", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "sandbox_walk"))
    args = ap.parse_args()

    since_iso = args.since + "T00:00"
    since_epoch = iso_to_epoch(args.since + "T00:00:00+00:00")
    print(f"building sandbox at {args.sandbox} (window start {args.since})")
    build_sandbox(args.sandbox, since_epoch)

    sys.path.insert(0, DR)
    import spread_recall
    spread_recall.DR = args.sandbox
    # module-level path constants captured at import - re-root every DR-based one
    for name in dir(spread_recall):
        val = getattr(spread_recall, name)
        if (isinstance(val, str) and val.startswith(DR)
                and not val.startswith(args.sandbox) and name.isupper()):
            setattr(spread_recall, name, val.replace(DR, args.sandbox, 1))
    assert spread_recall.DR == args.sandbox, spread_recall.DR  # double-patch guard: a sandbox UNDER the module dir re-patches patched paths
    # MEM stays real (read-only: index lines, memory bodies)

    episodes = load_episodes(since_iso, args.limit)
    reads = load_reads()
    print(f"replaying {len(episodes)} owner prompts chronologically...")

    fires = []
    t0 = time.time()
    for i, (ts, sess, text) in enumerate(episodes):
        payload = {"hook_event_name": "UserPromptSubmit", "prompt": text,
                   "session_id": sess}
        ctx = replay(spread_recall, payload)
        if ctx:
            slug = surfaced_slug(ctx)
            fires.append({"ts": ts, "sess": sess[:12], "slug": slug, "ctx": ctx[:120]})
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(episodes)} ({time.time()-t0:.0f}s), fires so far {len(fires)}")

    hits = 0
    for f in fires:
        ep = iso_to_epoch(f["ts"] if len(f["ts"]) > 16 else f["ts"] + ":00+00:00")
        sess_reads = reads.get(f["sess"], [])
        before = {slug for e, slug in sess_reads if e <= ep}
        after = {slug for e, slug in sess_reads if ep < e <= ep + HORIZON_S}
        if f["slug"] and f["slug"] in (after - before):
            hits += 1
            f["hit"] = True
    n = len(episodes)
    print(f"\nRESULT: {len(fires)} fires / {n} prompts ({len(fires)/max(n,1)*100:.1f}% fire rate); "
          f"{hits} hits (surfaced slug read novel within {HORIZON_S/60:.0f}m) "
          f"= precision {hits/max(len(fires),1)*100:.0f}%")
    out = os.path.join(os.path.dirname(args.sandbox), "walk_replay_fires.json")
    io.open(out, "w", encoding="utf-8").write(json.dumps(fires, ensure_ascii=False, indent=1))
    print(f"fires written to {out}")

    # fidelity: real surfaced.tsv rows in-window (kind not premise) for the same sessions
    real = 0
    with io.open(os.path.join(DR, "surfaced.tsv"), encoding="utf-8", errors="replace") as f:
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3:
                try:
                    if float(p[0]) >= since_epoch and (len(p) < 4 or p[3] != "premise"):
                        real += 1
                except ValueError:
                    pass
    print(f"fidelity anchor: live surfaced.tsv rows in-window (all sessions/events): {real} "
          f"(replay covers owner USP prompts only - compare shapes, not counts)")


if __name__ == "__main__":
    main()
