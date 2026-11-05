#!/usr/bin/env python3
"""run_dream.py - the MECHANICAL dream cycle for the brain (macOS/POSIX port of run_dream.ps1, minus REM).

Same order and the same log markers the wake check reads ("generating dream agenda", "dream cycle complete"):
  guards (STOP file, exclusive dream.lock) -> dream type by hour (sws < 09, nap < 16, rem) -> consciousness.py and
  dream.py under caps -> the mechanical tail (index, views, reflexes, graph, embeddings; each step capped, its exit
  code logged) -> "dream cycle complete".

THE REM PHASE IS NOT HERE. On Windows run_dream.ps1 launched an unattended headless Claude session with permission
checks disabled to consolidate memories. On this Mac that needs the owner's explicit decision (it is a standing
permission bypass and spends his model quota); until he makes it, dreams are mechanical-only and the wake check will
say the REM phase did not run. Also not ported: dream_git_sync (install step 12), the owner duration request.

usage: python3 run_dream.py [--type sws|nap|rem|mixed]
"""
import argparse, fcntl, glob, os, subprocess, sys, time
from datetime import datetime

HOME = os.path.expanduser("~")
BASE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = os.path.join(BASE, "logs")
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG = os.path.join(LOGDIR, "dream_%s.log" % STAMP)
try:
    SLUG = open(os.path.join(BASE, "BRAIN_SLUG"), encoding="utf-8").read().strip()
except OSError:
    SLUG = HOME.replace("/", "-")
MEM = os.path.join(HOME, ".claude", "projects", SLUG, "memory")
VIEWS = os.path.join(HOME, "brain-views")
PY = sys.executable
STEP_CAP = 1800

# run_dream.ps1's tail, in its order; Windows-only steps (the brain_improvement dashboard + its mesh claim) left out
TAIL = [
    ("compact_index apply", ["compact_index.py", "apply"]),
    # NOT on this Mac: shard_prompt_archive.py rebuilds each month's repo shard from THIS machine's live prompt
    # ledger. Here that ledger holds only the prompts typed on the Mac (13 on 29 Oct) while the repo's October shard
    # holds 3,547 from Windows - publishing would replace them. Re-enable only once the ledgers are merged.
    ("salience view refresh", ["salience.py", MEM, VIEWS]),
    ("growth view refresh", ["growth.py", MEM, VIEWS]),
    ("instincts selftest", ["instincts.py", "--selftest"]),
    ("instincts compile", ["instincts.py", MEM]),
    ("rotate hook fire logs", ["rotate_logs.py", "--apply"]),
    ("strong-node observe watch", ["strong_node_watch.py"]),
    ("mistake_graph watch", ["mistake_graph.py", "--watch"]),
    # memories filed after a correction that still cite the old slug (read-only review queue)
    ("supersession cascade", ["supersession_cascade.py", "--queue", os.path.join(BASE, "supersession_cascade_queue.tsv")]),
    ("ern_learn", ["ern_learn.py"]),
    ("ern_reliability", ["ern_reliability.py"]),
    ("reflex_ranking refresh", ["build_reflex_ranking.py", "--write", "--quiet"]),
    ("network dream_grow", ["network/dream_grow.py"]),
    ("heed_audit trend", ["heed_audit.py", "--days", "1", "--jsonl", os.path.join(BASE, "heed_trend.jsonl")]),
    ("precision_audit", ["precision_audit.py", "--days", "2", "--exposure"]),
    ("precision mechanism split", ["precision_audit.py", "--days", "1", "--by-mechanism"]),
    ("predict", ["predict.py", MEM]),
    ("build_prompt_views", ["build_prompt_views.py"]),
    ("ensure_index", ["ensure_index.py", "--write"]),
    ("orphan_edges build", ["build_orphan_edges.py", "--quiet"]),
    ("build_series_edges", ["build_series_edges.py", "--quiet"]),
    ("keyword_edges build (+component bridges)", ["build_keyword_edges.py", "--quiet"]),
    ("keyword topic nodes", ["build_keyword_nodes.py"]),
    ("regression gate", ["brain_regression_gate.py"]),
    ("build_spread_edges", ["build_spread_edges.py"]),
    ("build_mistake_weights", ["build_mistake_weights.py"]),
    ("build_near_duplicate_report", ["build_near_duplicate_report.py"]),
    ("reflex_precision", ["reflex_precision.py"]),
    ("bridge_health", ["bridge_health.py", "--samples", "150"]),
    ("suggest_links", ["suggest_links.py", "--quiet"]),
    ("build_programs_view", ["build_programs_view.py", "--quiet"]),
    ("suggest_orphan_links", ["suggest_orphan_links.py", "--quiet"]),
    ("bm25", ["bm25.py", "build"]),
    ("retrieval trace rebuild", ["build_retrieval_trace.py"]),
    ("base_activation", ["base_activation.py"]),
    # incremental: reuse every vector whose text is unchanged (28,020 of 28,022 on 29 Oct), then publish the
    # staged generation through the authority pointer - the builder alone never replaces the live sidecar
    ("embedding refresh", ["build_memory_embeddings.py", "--incremental-from", os.path.join(BASE, "memory_embeddings.npz")]),
    ("embedding publish", ["publish_memory_embeddings.py", "--staged", os.path.join(BASE, "memory_embeddings.next.npz")]),
    ("embedding coverage status", ["embedding_coverage.py", "--write-status"]),
    ("hook liveness", ["hook_liveness.py"]),
]


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("%s  %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))


def run_step(name, argv, cap=STEP_CAP):
    script = os.path.join(BASE, argv[0])
    if not os.path.exists(script):
        log("STEP SKIPPED: %s - %s not installed" % (name, argv[0]))
        return None
    t0 = time.time()
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            rc = subprocess.run([PY, "-s", script] + argv[1:], stdout=f, stderr=subprocess.STDOUT, timeout=cap, cwd=BASE,
                                env=dict(os.environ, PYTHONUTF8="1", DREAM_RUNNER="run_dream.py")).returncode
    except subprocess.TimeoutExpired:
        log("STEP CAPPED: %s hit its %ds cap" % (name, cap))
        return 124
    log(("STEP FAILED: %s exit %d" % (name, rc) if rc else "step ok: %s" % name) + " (%.0fs)" % (time.time() - t0))
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type")
    a = ap.parse_args()
    os.makedirs(LOGDIR, exist_ok=True)
    for old in glob.glob(os.path.join(LOGDIR, "*")):
        try:
            if time.time() - os.path.getmtime(old) > 30 * 86400:
                os.remove(old)
        except OSError:
            pass
    if os.path.exists(os.path.join(BASE, "STOP")):
        log("STOP file present - not dreaming")
        return 0
    lockp = os.path.join(BASE, "dream.lock")
    lockf = open(lockp, "a+")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another dream is in progress - exiting")
        return 0
    try:
        os.utime(lockp, None)
        h = datetime.now().hour
        tnext = os.path.join(BASE, "DREAM_TYPE_NEXT.txt")
        if a.type:
            dtype = a.type
        elif os.path.exists(tnext):
            dtype = open(tnext, encoding="utf-8").readline().strip()
            os.replace(tnext, os.path.join(BASE, "DREAM_TYPE_NEXT.last"))
        else:
            dtype = "sws" if h < 9 else ("nap" if h < 16 else "rem")
        if dtype not in ("sws", "nap", "rem", "mixed"):
            dtype = "mixed"
        os.environ["DREAM_TYPE"] = dtype
        log("refreshing consciousness tiers + generating dream agenda (type=%s)" % dtype)
        run_step("consciousness", ["consciousness.py"], cap=900)
        agenda_rc = run_step("dream agenda", ["dream.py"], cap=1800)
        # Only the REM pass reads the agenda, and it does not run here, so a missing agenda no longer aborts the tail:
        # on a loaded machine the agenda can hit its cap, and the abort used to cost every mechanical step after it.
        agenda_failed = bool(agenda_rc) or not os.path.exists(os.path.join(BASE, "DREAM_AGENDA.md"))
        if agenda_failed:
            log("agenda not regenerated (rc %s) - counted as a failed step, mechanical tail still runs" % agenda_rc)
        log("REM phase not run: mechanical-only dream (the owner has not decided on unattended REM sessions on this Mac)")
        log("mechanical tail starting (%d steps)" % len(TAIL))
        for name, argv in TAIL:
            if os.path.exists(os.path.join(BASE, "STOP")):
                log("STOP file present - tail stopped before %s" % name)
                break
            run_step(name, argv)
        log("dream cycle complete")
        if agenda_failed:
            return 2
    finally:
        try:
            fcntl.flock(lockf, fcntl.LOCK_UN)
            lockf.close()
            os.remove(lockp)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
