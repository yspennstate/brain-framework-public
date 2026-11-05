#!/usr/bin/env python3
"""suggest_orphan_links.py - connect the memories that spreading activation CANNOT reach.

Census 2026-07-10: 336 of 1456 non-substrate memories (23%) have graph degree 0 - zero wiki-links
and zero co-read edges - so the association walk (spread_recall) can NEVER surface them, however
relevant. 97 more are degree 1. That is ~30% of the corpus invisible to "this reminds me...". Whole
research series (e.g. arnold_diffusion_cycle18..36) sit orphaned: real content, unreachable.

This is a different problem from suggest_links (which bridges already-connected pairs): an orphan has
no shared neighbours, so Adamic-Adar is blind to it. The right attachment signals are:
  1. SERIES  - a shared stem before a trailing cycle-number/date (arnold_diffusion_cycleNN, ...) is one
     research program; consecutive members almost certainly belong linked. Pure filename structure,
     no model, near-certain.
  2. COSINE  - the bge-small content sidecar separates related from unrelated at AUC ~0.90 (STRONG,
     eval_semantic_recall), so an orphan's top cosine neighbours are its natural attachment points.

Read-only curation aid: writes orphan_links.md (each orphan + its best attachment candidates and why)
for the dream / owner to author by hand. It NEVER edits a memory - connecting 336 orphans by machine
would be a massive graph mutation racing the concurrent dreams (the clobber + non-stationary churn the
brain-method memory warns against). The single-threaded nightly dream is the safe author.
Usage:  python suggest_orphan_links.py [--top N] [--quiet]
"""
import io, os, re, sys
from collections import defaultdict
try:
    import embedding_artifact
except Exception:
    embedding_artifact = None
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
COACT = os.path.join(DR, "coactivation.tsv")
EMB = os.path.join(DR, "memory_embeddings.npz")
EMB_AUTHORITY = os.path.join(DR, ".embedding_current.json")
OUT = os.path.join(DR, "orphan_links.md")
LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
DATE_TAIL = re.compile(r"_\d{4}_\d{2}_\d{2}$")
NUM_TAIL = re.compile(r"(?:_cycle|_c|_part|_pt|_v|_wave|_round|_day)?_?\d{1,3}$")
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES", "CONSCIENCE", "CURIOSITY"}


def norm(s):
    s = s.strip()
    if s.endswith(".md"):
        s = s[:-3]
    return DATE_TAIL.sub("", s.replace("-", "_").lower())


def stem(canon):
    """Series stem: strip a trailing cycle/part/number token after the date is already gone."""
    return NUM_TAIL.sub("", canon).rstrip("_")


def _arg(flag, default, cast):
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == flag and i + 1 < len(a):
            try:
                return cast(a[i + 1])
            except Exception:
                return default
    return default


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    top = _arg("--top", 40, int)
    quiet = "--quiet" in sys.argv[1:]
    real = {norm(fn): fn[:-3] for fn in os.listdir(MEM) if fn.endswith(".md") and fn[:-3] not in SKIP}
    deg = defaultdict(int)
    desc = {}
    for fn in os.listdir(MEM):
        if not fn.endswith(".md") or fn[:-3] in SKIP:
            continue
        src = norm(fn)
        if src not in real:
            continue
        try:
            body = io.open(os.path.join(MEM, fn), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        dm = re.search(r'description:\s*"?(.+?)"?\s*\n', body)
        desc[src] = (dm.group(1) if dm else "").strip()[:110]
        for m in LINK_RE.findall(body):
            b = norm(m)
            if b in real and b != src:
                deg[src] += 1
                deg[b] += 1
    if os.path.exists(COACT):
        for ln in io.open(COACT, encoding="utf-8", errors="replace"):
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3 and p[0] != "a":
                a, b = norm(p[0]), norm(p[1])
                if a in real and b in real and a != b:
                    deg[a] += 1
                    deg[b] += 1

    orphans = sorted(s for s in real if deg.get(s, 0) == 0)

    # SERIES index: stem -> members (for orphan attachment to its own program)
    by_stem = defaultdict(list)
    for s in real:
        st = stem(s)
        if st and st != s:
            by_stem[st].append(s)

    # COSINE via sidecar (optional)
    idx = mat = None
    try:
        if embedding_artifact is None:
            raise ImportError("embedding artifact verifier unavailable")
        loaded = embedding_artifact.load_verified_embeddings(
            EMB, EMB_AUTHORITY, MEM, "BAAI/bge-small-en-v1.5", verify_all_rows=True
        )
        if loaded is not None:
            raw_idx, mat, _ = loaded
            idx = {norm(s): i for s, i in raw_idx.items()}
    except Exception:
        idx = mat = None

    def cosine_top(s, k=3):
        if idx is None or s not in idx:
            return []
        import numpy as np
        v = mat[idx[s]]
        sims = mat @ v
        order = np.argsort(-sims)
        inv = {i: sl for sl, i in idx.items()}
        out = []
        for i in order:
            cand = inv.get(int(i))
            if cand and cand != s and cand in real and cand not in SKIP:
                out.append((cand, float(sims[int(i)])))
            if len(out) >= k:
                break
        return out

    lines = ["# Orphan memories and where to attach them", "",
             f"{len(orphans)} memories have graph degree 0 - INVISIBLE to spreading-activation recall.",
             "Each below is a real memory nothing can reach; author a [[link]] from a suggested anchor so",
             "the walk can find it. SERIES anchors (same research program) are near-certain; COSINE anchors",
             "are the content-nearest memories (bge sidecar, AUC ~0.90). Read-only - the dream/owner authors.",
             ""]
    shown = 0
    for s in orphans:
        if shown >= top:
            break
        st = stem(s)
        series = [m for m in by_stem.get(st, []) if m != s][:3] if st else []
        cos = cosine_top(s, 3)
        if not series and not cos:
            continue
        shown += 1
        lines.append(f"- `{real.get(s, s)}`")
        lines.append(f"    - {desc.get(s, '')}")
        if series:
            lines.append(f"    - SERIES anchors: {', '.join(real.get(m, m) for m in series)}")
        if cos:
            lines.append("    - COSINE anchors: " + ", ".join(f"{real.get(c, c)} ({v:.2f})" for c, v in cos))
    try:
        with io.open(OUT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        if not quiet:
            print("suggest_orphan_links: write failed:", e)
        return
    if not quiet:
        print(f"suggest_orphan_links: {len(orphans)} degree-0 orphans; wrote {shown} with attachment "
              f"candidates to {OUT}" + ("" if idx is not None else " (no sidecar - SERIES anchors only)"))
        for s in orphans[:6]:
            cos = cosine_top(s, 1)
            tag = (f" ~ {real.get(cos[0][0], cos[0][0])} ({cos[0][1]:.2f})" if cos else "")
            print(f"  orphan {real.get(s, s)}{tag}")


if __name__ == "__main__":
    main()
