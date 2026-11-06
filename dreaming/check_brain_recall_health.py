#!/usr/bin/env python3
"""One command that says whether the brain's recall is actually working.

Written 2026-07-28 after a session lost measurable work to a recall gap that was
already flagged: memories were written, indexed, and invisible, because the
semantic sidecar had not rebuilt in days and the lexical index predated them.
Filing a defect is transport; this is the verifier.

Laptop-safe: reads coverage state and queries BM25. No model, no network.

    python check_brain_recall_health.py

Exit 0 when both arms are healthy, 1 when either is degraded, so it can gate a
nightly job. Prints what to do about each failure rather than only that it failed.
"""
import io
import json
import re
import os
import subprocess
import sys
from datetime import datetime, timezone


def _brain_slug_resolve():
    """The memory corpus this install belongs to.

    Order matters. $BRAIN_SLUG wins so a caller can point one script at another corpus.
    Then BRAIN_SLUG beside this file, which bootstrap/install.py writes with the slug of the
    --project-dir it was given; without it an install whose project directory is not the home
    directory builds a corpus nothing ever reads. The home-derived value stays last so a tree
    that works today keeps working untouched.
    """
    env = os.environ.get("BRAIN_SLUG", "").strip()
    if env:
        return env
    try:
        marker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "BRAIN_SLUG")
        with open(marker, encoding="utf-8") as fh:
            named = fh.read().strip()
        if named:
            return named
    except OSError:
        pass
    home = os.path.expanduser("~")
    return home.replace(":", "-").replace(chr(92), "-").replace("/", "-")


if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file works at module scope, so the guard
    # must precede the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)

DR = os.path.dirname(os.path.abspath(__file__))
# Claude Code names a project directory after the home path, with ':' and the
# separators replaced by '-'. Deriving it keeps this tree free of one machine's
# username and correct on any other - and unlike a template token it is a real
# value at import time.
_BRAIN_SLUG = _brain_slug_resolve()
MEM = os.environ.get("BRAIN_MEM_DIR") or os.path.join(os.path.expanduser("~"), ".claude", "projects",
                                                     _BRAIN_SLUG, "memory")

problems = []

# --- semantic arm -----------------------------------------------------------
status_path = os.path.join(DR, ".embedding_status.json")
try:
    with io.open(status_path, encoding="utf-8") as fh:
        st = json.load(fh)
    missing = int(st.get("missing", -1))
    stale = int(st.get("stale", -1))
    age_h = float(st.get("sidecar_age_hours", -1))
    complete = bool(st.get("complete", False))
    print(f"semantic: embedded {st.get('embedded')}/{st.get('eligible')} "
          f"missing={missing} stale={stale} age={age_h:.1f}h "
          f"complete={complete}")
    # EXPECTED LAG IS NOT A DEFECT (2026-09-14). `complete` is False whenever ANY memory lacks a vector,
    # and the embedding refresh runs every three hours, so between refreshes there are always a few
    # just-written memories without one: this arm printed DEGRADED on 2 missing of 16,010 with a
    # two-hour-old sidecar, which is the system working exactly as designed. A gate that is yellow by
    # construction trains every reader to ignore it (the owner's 2026-09-02 order: fire less, be more
    # confident when you fire). The lexical arm below already solved its own version of this by
    # self-healing first. So: count the memories written SINCE the sidecar was built - those cannot
    # possibly be embedded yet - and treat the arm as healthy when every missing vector is one of them,
    # nothing is stale/extra/duplicated, and the backlog is small. Anything older than the sidecar with
    # no vector is still a real defect and still trips.
    lag = 0
    try:
        built = st.get("built_at") or ""
        b = datetime.strptime(built[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        lag = sum(1 for n in os.listdir(MEM)
                  if n.endswith(".md") and os.path.getmtime(os.path.join(MEM, n)) > b)
    except Exception:
        lag = -1
    # A memory written after the sidecar was built reads as `missing`; one EDITED after it reads as
    # `stale`. Both are the same lag and both are cleared by the next refresh, so both are counted
    # against it. `extra` and `duplicates` are not lag - they mean the sidecar disagrees with the
    # corpus - and still trip the arm however fresh it is.
    behind = missing + int(st.get("stale", 0) or 0)
    clean_shape = (int(st.get("extra", 0) or 0) == 0 and int(st.get("duplicates", 0) or 0) == 0)
    expected_lag = (1 <= behind <= 25 and lag >= 0 and behind <= lag and clean_shape and age_h <= 6)
    if expected_lag:
        print(f"semantic: {behind} memor(y|ies) written or edited since the sidecar was built {age_h:.1f}h "
              f"ago are not embedded yet - that is the 3-hourly refresh's normal lag, not a defect "
              f"({lag} file(s) newer than the sidecar)")
    elif not complete or missing > 25 or age_h > 36:
        problems.append(
            f"SEMANTIC DEGRADED - {missing} memories have no vector and the "
            f"sidecar is {age_h:.0f}h old. Recent nodes are reachable by "
            f"keyword and graph but NOT by paraphrase.\n"
            f"    Fix: rebuild the sidecar with build_memory_embeddings.py on a machine "
            f"where the model fits in memory, then publish it with "
            f"publish_memory_embeddings.py.\n"
            f"    Laptop-safe check only: build_memory_embeddings.py --plan-only")
except Exception as exc:
    problems.append(f"SEMANTIC UNREADABLE - {status_path}: {exc}")

# --- lexical arm ------------------------------------------------------------
# The real test is not "does the index exist" but "does it contain the newest
# memories". Take the most recently modified memory files and require BM25 to
# return each one for a query built from its own slug.
try:
    # WRITE-TIME LANE (2026-09-02): bm25.py refresh keeps the index current (recall(), remember() and
    # the 15-minute task), so the probe below measures the STEADY state only once the refresh has
    # landed. Measured the day the lane shipped: this check read LEXICAL 2/5 when run inside the few
    # seconds between another agent's remember() and its background refresh, and 5/5 a minute later.
    # Running the laptop-safe refresh here first (0.3 s when nothing is fresh) removes that false
    # DEGRADED and makes the check self-healing: the verdict reflects the index every reader loads.
    try:
        import bm25 as _bm25
        _added, _gone = _bm25.refresh(quiet=True)
        if _added or _gone:
            print(f"lexical: refreshed the index first (+{_added} fresh, -{_gone} gone)")
    except Exception as _exc:
        print(f"lexical: refresh skipped ({type(_exc).__name__}: {_exc}) - probing the index as it is")
    # Exclude the all-caps system files (MEMORY, CONSCIENCE, MISTAKES,
    # PREDICTIONS, OPEN_QUESTIONS, STATE_OF_MIND...). Test the STEM: a plain
    # f.isupper() returns False for "CONSCIENCE.md" because the extension is
    # lowercase, which made this checker report a false miss the first time it
    # ran. A verifier that cries wolf gets ignored, which defeats the point.
    files = [f for f in os.listdir(MEM)
             if f.endswith(".md") and not f[:-3].replace("_", "").isupper()]
    files.sort(key=lambda f: os.path.getmtime(os.path.join(MEM, f)),
               reverse=True)
    probes = files[:5]
    misses = []
    for fname in probes:
        slug = fname[:-3]
        # Drop only a trailing _YYYY_MM_DD. A compaction slug ends in its own
        # timestamp (..._20261101T105555); cutting at " 2026" left "compaction
        # claude <id>", which every compaction of that session matches, and the
        # newest one fell out of the top hits: a false LEXICAL DEGRADED.
        query = re.sub(r"_20\d\d_\d\d_\d\d$", "", slug).replace("_", " ")
        out = subprocess.run(
            [sys.executable, os.path.join(DR, "bm25.py"), "query", query],
            capture_output=True, text=True, cwd=DR).stdout
        if slug not in out:
            misses.append(slug)
    print(f"lexical: {len(probes) - len(misses)}/{len(probes)} newest memories "
          f"retrievable by their own words")
    if misses:
        problems.append(
            "LEXICAL DEGRADED - the BM25 index does not contain the newest "
            f"memories: {', '.join(m[:48] for m in misses)}\n"
            "    Fix is laptop-safe and takes ~2s: python bm25.py build")
except Exception as exc:
    problems.append(f"LEXICAL UNCHECKABLE - {exc}")

print()
if problems:
    for p in problems:
        print("  " + p)
    print(f"\nverdict: DEGRADED ({len(problems)} arm(s))")
    sys.exit(1)
print("verdict: HEALTHY - both recall arms reach the newest memories")
sys.exit(0)
