#!/usr/bin/env python3
"""eval_type_utility.py - do the dream's REFLECTIVE memories earn their keep, per type, with CIs?

The owner's standing question is "smarter or just bigger" - does the brain improve, or only accumulate.
The 2026 reflective-memory benchmarks (Connecting the Dots) draw the right distinction: inference-level
memories (gists, daydreams/hunches, methods, scars) are a different KIND from verbatim episodic records,
and the test is whether they get REUSED, not whether they exist. This measures, per memory type, the
rate at which a memory written in the trace era is later co-read - with a Wilson score interval, so a
thin type reports an honest band, not a point estimate that a handful of reads could swing.

A type whose re-read rate's CI sits clearly below the episodic baseline is producing bulk, not value,
and is a candidate for wake-spotlight de-prioritisation (the attention budget is ~4 chunks; Cowan). A
type clearly above earns its slot. Overlapping CIs = not enough data yet, and it says so.

Ground truth = the retrieval trace (build_retrieval_trace.py). Read-only, laptop-safe, no model.
Usage: python eval_type_utility.py
"""
import os, io, re, time, math
from collections import defaultdict

DR = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
TRACE = os.path.join(DR, "retrievals.tsv")
TRACE_START = "2026-06-25"   # the trace-coverage era; memories older than this predate observation


def wilson(k, n, z=1.96):
    """Wilson score interval for a binomial rate - honest on small n (unlike +-sqrt(p(1-p)/n))."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d
    return (p, max(0.0, center - half), min(1.0, center + half))


def mem_type(slug, body):
    if slug.startswith("gist_"):
        return "gist"
    if slug.startswith("daydream_") or slug.startswith("dream_"):
        return "daydream/dream"
    if slug.startswith("method_"):
        return "method"
    if slug.startswith("scar_"):
        return "scar"
    if slug.startswith("feedback_"):
        return "feedback"
    m = re.search(r"^\s*(?:type|node_type):\s*(\w+)", body[:600], re.M)
    if m and m.group(1) in ("user", "project", "reference", "feedback"):
        return m.group(1)
    return "episodic/other"


def main():
    if not os.path.exists(TRACE):
        print("eval_type_utility: no retrievals.tsv yet.")
        return
    # trace: slug -> earliest-read epoch(s)
    reads = defaultdict(list)
    for i, ln in enumerate(io.open(TRACE, encoding="utf-8", errors="replace")):
        if i == 0:
            continue
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 2:
            try:
                reads[p[0]].append(float(p[1]))
            except Exception:
                pass
    cutoff = time.mktime(time.strptime(TRACE_START, "%Y-%m-%d"))
    datep = re.compile(r"(20\d\d)[_-](\d\d)[_-](\d\d)")

    by_type = defaultdict(lambda: [0, 0])   # type -> [n_memories, n_rereadd]
    for fn in os.listdir(MEM):
        if not fn.endswith(".md"):
            continue
        slug = fn[:-3]
        # A slug can carry a non-date year-pair in its body ("..._2021_22_2026_08_29"):
        # the FIRST match read as a date crashed the dream's eval step on 2026-09-01
        # ("time data '2021-22-20'"). The creation date is the LAST parseable match.
        created = None
        for m in reversed(datep.findall(slug)):
            try:
                created = time.mktime(time.strptime("-".join(m), "%Y-%m-%d"))
                break
            except ValueError:
                continue
        if created is None:
            continue
        if created < cutoff:
            continue   # predates the trace; can't fairly ask if it was re-read
        try:
            body = io.open(os.path.join(MEM, fn), encoding="utf-8", errors="replace").read(600)
        except Exception:
            body = ""
        t = mem_type(slug, body)
        later = [e for e in reads.get(slug, []) if e > created + 43200]   # re-read >12h after birth
        by_type[t][0] += 1
        if later:
            by_type[t][1] += 1

    base = by_type.get("episodic/other", [0, 0])
    base_rate = (base[1] / base[0]) if base[0] else None
    print("eval_type_utility: re-read rate by memory type (trace era, memories created since %s)" % TRACE_START)
    print("  %-16s %5s %7s   %-18s %s" % ("type", "n", "reread", "rate [95% Wilson]", "vs episodic baseline"))
    for t, (n, k) in sorted(by_type.items(), key=lambda kv: -kv[1][1] / max(1, kv[1][0])):
        p, lo, hi = wilson(k, n)
        verdict = ""
        if base_rate is not None and t != "episodic/other" and n >= 4:
            if lo > base_rate:
                verdict = "ABOVE baseline (earns its slot)"
            elif hi < base_rate:
                verdict = "BELOW baseline (bulk, not value)"
            else:
                verdict = "CI overlaps baseline (too few to call)"
        print("  %-16s %5d %6.0f%%   %.2f [%.2f-%.2f]   %s" % (t, n, 100 * p, p, lo, hi, verdict))
    if base_rate is not None:
        print("  episodic/other baseline rate: %.2f (n=%d)" % (base_rate, base[0]))
    print("  read: a type whose CI sits clearly below baseline is a wake-spotlight de-prioritisation "
          "candidate; clearly above earns its slot; overlapping = accumulate more trace before deciding.")


if __name__ == "__main__":
    main()
