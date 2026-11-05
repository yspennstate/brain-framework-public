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
import argparse, fcntl, glob, json, os, subprocess, sys, time
from datetime import datetime

HOME = os.path.expanduser("~")
BASE = os.path.dirname(os.path.abspath(__file__))
# project_memory reads the governor state at ~/.brain-framework by default; on this Mac the governor writes here
os.environ.setdefault("PROJECT_MEMORY_COMPUTE_STATE", os.path.join(HOME, ".claude", "compute", "compute_state.json"))
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
# launchd starts dreams 8 h apart and will not start one while the last still runs (3 Nov: the 11:30 dream ran to 20:48
# under swap, and 19:30 never started). Steps are cut to fit, and the tail left over waits for the next dream.
DREAM_BUDGET_S = int(os.environ.get("DREAM_BUDGET_S", 8 * 3600 - 600))
MIN_STEP_S = 120
T_START = time.time()
# 4 Nov 20:10: the 19:30 dream's embedding refresh loaded its model while the owner's 13B film rendered a clip, swap
# went 2.6 -> 6.2 GB and the film's guard killed the clip (7 min lost). These steps now wait for the Mac's shared
# heavy_job.lock (the film releases it between clips) and hold it while they run. HEAVY_RUN="" turns this off.
HEAVY_RUN = os.environ.get("HEAVY_RUN", os.path.join(HOME, "brain", "porting", "heavy_lock_run.sh"))
# bm25 too (claude-52d17aea's read of the same log): its full build ran 20:03:27-20:09:46 beside that clip, swap
# 2.9 -> 3.7 GB at io 1710/s, before the embedding step took it to 6.2. Under the lock it delays a clip ~6 min at worst.
HEAVY_STEPS = {"embedding refresh", "embedding refresh (retry)", "embedding publish", "bm25"}
EMB_REFRESH = ("embedding refresh", ["build_memory_embeddings.py", "--incremental-from",
                                     os.path.join(BASE, "memory_embeddings.npz")])

# run_dream.ps1's tail, in its order; Windows-only steps (the brain_improvement dashboard + its mesh claim) left out
TAIL = [
    ("compact_index apply", ["compact_index.py", "apply"]),
    # The Mac's ledger is partial, so a plain republish would replace Windows' rows (3,547 in October). Fetch the
    # shards from origin/main first (unmodified files only), then publish the UNION (BR15, 29 Oct); --check stays strict.
    ("prompt shards fetch", ["fetch_prompt_shards.py"]),
    ("shard_prompt_archive union", ["shard_prompt_archive.py", "--union-existing"]),
    ("salience view refresh", ["salience.py", MEM, VIEWS]),
    ("growth view refresh", ["growth.py", MEM, VIEWS]),
    # the navigation views (BRAIN_INDEX, TOPICS, TIMELINE, GRAPH, RELATED, SEARCH, STATS) had no step here and stayed
    # at the install copy until W56 (30 Oct). Built from the LIVE folder: the repo mirror is never refreshed on the
    # Mac. A full build took ~80 min niced at load 50-60 (30 Oct), hence its own cap; the builder renames each view
    # into place, so a capped run keeps the previous generation. Views under VIEWS_FRESH_H old are not rebuilt (a
    # deferral, see main): three dreams a day each redoing 80 min of work bought nothing.
    # the builder lives in a framework checkout and reads the brain repo; both default to the Mac's layout
    ("navigation views", [os.path.join(os.environ.get("BRAIN_FRAMEWORK_DIR") or os.path.join(HOME, "brain", "brain-framework"),
                                       "manifests", "build_tools", "build_brain_architecture.py"),
                          os.environ.get("BRAIN_REPO_DIR") or os.path.join(HOME, "brain", "ai-memories-and-functionality"),
                          MEM], 7200),
    ("instincts selftest", ["instincts.py", "--selftest"]),
    ("instincts compile", ["instincts.py", MEM]),
    ("rotate hook fire logs", ["rotate_logs.py", "--apply"]),
    ("strong-node observe watch", ["strong_node_watch.py"]),
    ("mistake_graph watch", ["mistake_graph.py", "--watch"]),
    # 1000_WAYS item 63: memories filed after a correction that still cite the old slug (read-only; the
    # queue is a review list, about 3 in 10 inline rows were truly stale on a 29 Oct hand read)
    ("supersession cascade", ["supersession_cascade.py", "--queue", os.path.join(BASE, "supersession_cascade_queue.tsv")]),
    # 31 Oct: run_dream.ps1 rebuilds superseded.tsv every cycle (schema_supersede.py --apply) and this port had no
    # such step, so on the Mac the ledger spread_recall routes through grew only by hand: 22 of 557 frontmatter
    # supersessions were missing from it and recall could serve those stale memories beside their corrections.
    # No --apply: the ledger is rebuilt before that flag is read, and stamping old memories' frontmatter would edit
    # existing memories on the Mac, which waits on GIT_SYNC_APPROVED.
    ("supersession ledger", ["schema_supersede.py"]),
    # step 6c (owner-approved 29 Oct): build the signed project-memory hot cache. Exit 75 is a deliberate deferral
    # while the owner is active or the governor is stale, not a failure of the step.
    ("project-memory hourly", ["project_memory_cli.py", "hourly", "--config",
                               os.path.join(HOME, ".brain-framework", "private", "projects.json")]),
    # the Windows box runs this every 15 minutes from Task Scheduler; the Mac has no such job (launchd persistence was
    # refused 29-30 Oct), so new math results reach the store at least once a dream (installed 30 Oct)
    ("math transport", ["math_transport.py", "run"]),
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
    ("build_communities", ["build_communities.py", "--quiet"]),
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
    EMB_REFRESH,
    ("embedding publish", ["publish_memory_embeddings.py", "--staged", os.path.join(BASE, "memory_embeddings.next.npz")]),
    ("embedding coverage status", ["embedding_coverage.py", "--write-status"]),
    ("hook liveness", ["hook_liveness.py"]),
]


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("%s  %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))


VIEWS_FRESH_H = 20


def _views_fresh():
    """True when the slowest view (RELATED.md, written last) was rebuilt within VIEWS_FRESH_H hours."""
    try:
        return time.time() - os.path.getmtime(os.path.join(VIEWS, "RELATED.md")) < VIEWS_FRESH_H * 3600
    except OSError:
        return False


def _publish_without_refresh(name, failed):
    """Why the embedding publish should not run, or ''. A staged file left by a failed refresh is stale."""
    if name != "embedding publish" or EMB_REFRESH[0] not in failed:
        return ""
    staged = os.path.join(BASE, "memory_embeddings.next.npz")
    try:
        age = "staged file from %s" % time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(staged)))
    except OSError:
        age = "nothing staged"
    return "the embedding refresh failed this run; %s" % age


def _budget_cap(cap):
    """The cap a step may have inside the dream's budget, or 0 when too little is left to start one."""
    left = int(DREAM_BUDGET_S - (time.time() - T_START))
    return min(cap, left) if left >= MIN_STEP_S else 0


def _machine():
    """The governor's reading as a step starts. 3 Nov: whether capped steps are the ones run under swap could not be
    measured over 17 dream logs, because neither the logs nor the governor kept the machine's state."""
    try:
        d = json.load(open(os.environ["PROJECT_MEMORY_COMPUTE_STATE"], encoding="utf-8"))
        return "%s swap %.0f MB io %.0f/s" % (d.get("status"), d.get("swap_used_mb") or 0, d.get("swap_io_ps") or 0)
    except Exception:
        return "unknown"


def run_step(name, argv, cap=STEP_CAP):
    script = os.path.join(BASE, argv[0])
    if not os.path.exists(script):
        log("STEP SKIPPED: %s - %s not installed" % (name, argv[0]))
        return None
    cmd = [PY, "-s", script] + argv[1:]
    heavy = name in HEAVY_STEPS and HEAVY_RUN and os.path.exists(HEAVY_RUN)
    if heavy:
        cmd = ["/bin/zsh", HEAVY_RUN, "dream %s: %s" % (STAMP, name)] + cmd
    log("step start: %s (machine %s)%s" % (name, _machine(), " under heavy_job.lock" if heavy else ""))
    t0 = time.time()
    with open(LOG, "a", encoding="utf-8") as f:
        # own process group, so a cap kills the lock waiter and its child together (the waiter's TERM trap frees the lock)
        p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=BASE, start_new_session=True,
                             env=dict(os.environ, PYTHONUTF8="1", DREAM_RUNNER="run_dream.py"))
        try:
            rc = p.wait(timeout=cap)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, 15)
                p.wait(timeout=30)
            except Exception:
                try:
                    os.killpg(p.pid, 9)
                except Exception:
                    pass
                p.wait()
            log("STEP CAPPED: %s hit its %ds cap" % (name, cap))
            return 124
    if rc == 75:
        # EX_TEMPFAIL: the step chose not to run (project-memory hourly defers under compute warn/throttle). That is
        # a deferral, not a failure - the 29 Oct 19:31 nap reported 3 failures when 2 were real.
        log("STEP DEFERRED: %s exit 75 (did not run by design)" % name + " (%.0fs)" % (time.time() - t0))
        return rc
    log(("STEP FAILED: %s exit %d" % (name, rc) if rc else "step ok: %s" % name) + " (%.0fs)" % (time.time() - t0))
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type")
    a = ap.parse_args()
    os.makedirs(LOGDIR, exist_ok=True)
    # no 30-day purge here: the brain's logs in ~/.claude/dreaming are never deleted (owner, 26 Sep)
    if os.path.exists(os.path.join(BASE, "STOP")):
        log("STOP file present - not dreaming")
        return 0
    lockp = os.path.join(BASE, "dream.lock")
    lockf = open(lockp, "a+")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lockf.close()
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
        agenda = os.path.join(BASE, "DREAM_AGENDA.md")
        before = os.path.getmtime(agenda) if os.path.exists(agenda) else None
        agenda_rc = run_step("dream agenda", ["dream.py"], cap=1800)
        # A failed or killed agenda child can leave yesterday's agenda in place (BI1-008): existence is not enough.
        failed, deferred, capped = [], [], []
        if agenda_rc == 124:
            capped.append("dream agenda")
        # Only the REM pass reads the agenda, and it does not run here, so a stale agenda no longer aborts the tail.
        # 1 Nov 19:30: the agenda hit its cap on a swapping machine (consciousness 752 s, agenda over 1800 s, the same
        # 1.4-2.4x ratio every dream since 30 Oct) and the abort cost all 46 steps, the nightly edge rebuild among them.
        if agenda_rc or not os.path.exists(agenda) or os.path.getmtime(agenda) == before:
            log("agenda not regenerated (rc %s) - counted as a failed step, mechanical tail still runs" % agenda_rc)
            failed.append("dream agenda")
        log("REM phase not run: mechanical-only dream (the owner has not decided on unattended REM sessions on this Mac)")
        log("mechanical tail starting (%d steps)" % len(TAIL))
        for step in TAIL:
            name, argv = step[0], step[1]
            if os.path.exists(os.path.join(BASE, "STOP")):
                log("STOP file present - tail stopped before %s; dream cycle STOPPED, not complete" % name)
                return 3
            stale = _publish_without_refresh(name, failed)
            cap = _budget_cap(step[2] if len(step) > 2 else STEP_CAP)
            if not cap:
                log("step deferred: %s (dream budget of %ds spent)" % (name, DREAM_BUDGET_S))
                rc = 75
            elif name == "navigation views" and _views_fresh():
                log("step deferred: navigation views (RELATED.md under %d h old)" % VIEWS_FRESH_H)
                rc = 75
            elif stale:
                # it would only be refused, after holding memory_write.lock against every remember for up to the cap
                log("step deferred: embedding publish (%s)" % stale)
                rc = 75
            else:
                rc = run_step(name, argv, cap=cap)
            # The publisher refuses a generation when a memory lands during the build, and with the fleets filing
            # one every few minutes the 1 Nov dream lost that race (401 memories left out of semantic recall). Each
            # retry rebuilds only the rows that moved (433 encoded in 45 s by hand that morning), so two are cheap.
            for _ in range(2 if name == "embedding publish" else 0):
                if rc in (0, 75, 124):
                    break
                if not _budget_cap(STEP_CAP):
                    break
                log("embedding publish refused - rebuilding the moved rows and publishing again")
                if run_step("embedding refresh (retry)", EMB_REFRESH[1], cap=_budget_cap(STEP_CAP)) == 0 and _budget_cap(STEP_CAP):
                    rc = run_step(name, argv, cap=_budget_cap(STEP_CAP))
            if rc == 75:
                deferred.append(name)
            elif rc:
                failed.append(name)
                if rc == 124:
                    capped.append(name)
        if deferred:
            log("deferred by design (not run, not failed): %s" % ", ".join(deferred))
        # D3 #35: a step cut at its cap is fragmented sleep, not a broken step (2 Nov 03:30: all 3 "failed" steps were
        # cut at 1800 s on a loaded box, and the summary line said only "failed"). The count is per run, for the trend.
        if capped:
            log("fragmented: %d step(s) cut at their cap: %s" % (len(capped), ", ".join(capped)))
        # The marker stays for the wake check; the count and exit code say whether every step held (BI1-007).
        if failed:
            log("dream cycle complete with %d failed step(s): %s" % (len(failed), ", ".join(failed)))
            return 2
        log("dream cycle complete")
    finally:
        try:
            fcntl.flock(lockf, fcntl.LOCK_UN)
            lockf.close()
            # Keep the flock inode stable across invocations; do not unlink.
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
