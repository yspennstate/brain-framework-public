#!/usr/bin/env python3
"""hook_liveness.py - is the data still flowing? A wired hook can silently die - its script deleted,
renamed, or broken by an edit - and nothing notices until a whole faculty has been dark for days. That
is the Azure-cutover shape (a green light while the thing behind it quietly changed). guard_audit.py
asks "is each guard exposed to its fault"; brain_status counts what the guards caught. Neither asks the
plainest operational question: for every hook settings.json says should run, would it actually run, and
is the data it produces still arriving? This does, and it's the observer the owner keeps asking for
("build useful observers, audit that data is flowing").

Four honest reads, kept separate because they mean different things:
  1. WIRING HEALTH  - every hook wired in settings.json: does its script exist and COMPILE? A missing or
     broken wired script is a genuinely dead hook - the real failure mode, caught here.
  2. DATA-FLOW      - the append-only streams a hook writes on every relevant event (outcomes, reality_log,
     coactivation, salience, spread_edges, reliability): age + whether they grew since the last run. A
     producer that stopped is a silent regression.
  3. FIRE-RECENCY   - each .X_fired.json stamp's age. INFORMATIONAL ONLY: danger/FONK/reality-block stamp
     only on a positive event, so a stale stamp means "no cause to fire lately," NOT "dead." Labeled so.
  4. CODEX CONTRACT - the independent hooks.json shape, pure warning-wrapper selftests, and privacy-safe
     Codex host counters. Claude wiring cannot prove that a warning reaches Codex.

Read-only except a tiny snapshot (.hook_liveness_snap.json) to measure stream growth run-to-run. Always
exits 0. Usage: python hook_liveness.py
"""
import os, io, sys, json, re, time, py_compile

HERE = os.path.dirname(os.path.abspath(__file__))
MEMDIR = os.path.expanduser("~/.claude/projects/{{BRAIN_SLUG}}/memory")
SETTINGS = os.path.expanduser(r"~/.claude/settings.json")
CODEX_HOOKS = os.path.expanduser(r"~/.codex/hooks.json")
CODEX_CONFIG = os.path.expanduser(r"~/.codex/config.toml")
CODEX_SESSIONS = os.path.expanduser(r"~/.codex/sessions")


def _codex_sessions_in_window(window_s, root=None):
    """True if any Codex session jsonl was written inside the window.

    Distinguishes broken-telemetry (sessions ran, wrappers silent) from
    idle-by-absence (no sessions, silence expected). Added 2026-08-14.
    """
    root = root or CODEX_SESSIONS
    cutoff = time.time() - window_s
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".jsonl"):
                try:
                    if os.path.getmtime(os.path.join(dirpath, f)) >= cutoff:
                        return True
                except OSError:
                    continue
    return False
CODEX_METRICS = os.path.join(HERE, "error_graph_runtime", "hook_health.sqlite3")
SNAP = os.path.join(HERE, ".hook_liveness_snap.json")
DAY = 86400.0

# streams a hook appends to on every relevant event, and who writes them (for a stopped-producer flag)
STREAMS = {
    "outcomes.jsonl": "error_monitor.py (PostToolUse)",
    "reality_log.jsonl": "reality_monitor.py (Stop)",
    "coactivation.tsv": "coactivation_end.py (Stop)",
    "salience.tsv": "salience.py (dream)",
    "spread_edges.tsv": "build_spread_edges.py (dream)",
    "reliability.jsonl": "ern_reliability.py (dream SWS)",
    # nightly persist writers added 2026-07-15 (the built-but-never-invoked audit): both write
    # real content on every dream persist (no re-stamping relay in either path), so the 3-day
    # mtime threshold is honest. heed_audit/ensure_index are log-line + conditional writers -
    # deliberately NOT here (a quiet queue must not read as a dead producer).
    "reflex_ranking.json": "build_reflex_ranking.py (dream persist, nightly)",
    # dream_grow's liveness reads its RAN-STAMP, not net.sqlite3: the db's mtime is
    # re-stamped by mere READERS (measured 07-15 - a read-only eval refreshed it), which
    # is exactly the freshness-theater the equity-breaker scar names. The stamp is written
    # only when growth actually ran.
    os.path.join("network", ".grow_ran.json"): "dream_grow.py (dream persist, nightly)",
    # the reflex gate's evidence (29 Sep, scheduled audit account04/B04 r6, B04-R43-STALE-PRECISION-PRIOR): the
    # judge appends verdicts only when it judges, so its log's age is the data's age; the table is rebuilt from
    # the log, and a rebuild over an unchanged log would look fresh on its own. The judge stopped from 26 Sep to
    # the 27th's evening with nothing saying so, and the gate went on reading a day-old table.
    "judged_nightly.jsonl": "fire_judge_nightly.py (dream, nightly: verdicts on sampled fires)",
    "reflex_precision.json": "reflex_precision.py (dream, nightly: the precision table instinct_prime gates on)",
}
# a stream checked on a shorter clock than the three days above: both of these are written every night
STREAM_MAX_AGE = {"judged_nightly.jsonl": 1.5 * 86400.0, "reflex_precision.json": 1.5 * 86400.0}


def _stale_hint(fn, platform=None):
    """What a STALE line should add when the cause is known. The judge's verdicts come from a model reading
    DREAM_AGENDA.md's J sections in the REM pass, and run_dream.py (the runner off Windows) has no REM pass, so there
    the log ages until an awake agent judges the cases. 5 Nov 2026: a Mac read 'producer may have stopped' for
    1.7 days while nothing was broken and nothing would ever write it."""
    if fn == "judged_nightly.jsonl" and (platform or sys.platform) != "win32":
        return (" - no REM pass on this runner, so nothing judges fires unless an awake agent does:"
                " judge DREAM_AGENDA.md's J, J2 and J3 cases with judge_agenda.py (show, template, write)")
    return ""
# .X_fired.json stamp -> the hook it belongs to, and whether it stamps only on a positive fire
STAMPS = {
    # a folder since 2026-09-02 (instinct_prime keeps one state file per session); the retired single file read
    # MISSING here from then on
    ".instinct_fired": ("instinct_prime.py", "every prime"),
    ".spread_fired.json": ("spread_recall.py", "on a surfaced association"),
    ".ern_fired.json": ("error_monitor.py", "positive-only (a felt error)"),
    ".danger_fired.json": ("danger_mode.py", "positive-only (danger tier)"),
    ".fonk_fired.json": ("fok_probe.py", "positive-only (feeling-of-not-knowing)"),
    ".sourced_fired.json": ("sourced_memory.py", "on a memory write"),
    ".reality_blocked.json": ("reality_monitor.py", "positive-only (a block)"),
}


def _index_coverage():
    """(indexed, eligible, stranded) for the BM25 lexical index, or None if unreadable.

    ELIGIBILITY MUST MIRROR THE PRODUCER. bm25.read_docs skips SKIP_FILES and any slug starting
    with "dream_", so counting raw *.md against the index reports ~31 permanent phantom gaps and
    the check cries wolf on its first run. SKIP_FILES is imported rather than copied so that list
    cannot drift; the dream_ prefix is mirrored here and must be kept in step with bm25.read_docs."""
    try:
        import gzip, glob
        from bm25 import SKIP_FILES
        # Read the bytes and let go of the index before parsing them: on Windows no publish can replace it while
        # this holds it, and a parse that streams from the open file takes 9-13 s under load (13 Oct 2026).
        with open(os.path.join(HERE, "bm25_index.json.gz"), "rb") as fh:
            blob = fh.read()
        with gzip.GzipFile(fileobj=io.BytesIO(blob), mode="rb") as gz:
            with io.TextIOWrapper(gz, encoding="utf-8") as f:
                dl = json.load(f).get("dl") or {}
        del blob
        eligible = [s for s in (os.path.basename(q)[:-3]
                                for q in glob.glob(os.path.join(MEMDIR, "*.md")))
                    if s not in SKIP_FILES and not re.fullmatch(r"dream_\d{4}_\d{2}_\d{2}", s)]
        if not eligible:
            return None
        return len(dl), len(eligible), sum(1 for s in eligible if s not in dl)
    except Exception:
        return None


DREAM_COMMIT_MAX_H = 30   # the dream runs at least nightly (03:30); 30 h means a whole cycle produced no commit


def _dream_commits_unpushed():
    """(local_only_count, newest_dream_commit_on_remote, remote_source) for the brain repo, asking GitHub itself
    with a 20 s bound (falls back to the local origin/main ref, labelled as such). None when git cannot answer."""
    import subprocess
    repo = os.path.join(os.path.expanduser("~"), "brain-repo")

    def _git(args, timeout):
        p = subprocess.run(["git", "-C", repo] + args, capture_output=True, text=True, timeout=timeout)
        return (p.stdout.strip() if p.returncode == 0 else None)

    try:
        newest = _git(["log", "-1", "--grep=Nightly dream: consolidation", "--format=%H", "main"], 60)
        if not newest:
            return None
        remote_oid, src = None, "origin/main (local ref, remote unreachable)"
        try:
            out = _git(["ls-remote", "--heads", "origin", "main"], 20)
            if out:
                remote_oid, src = out.split()[0], "origin/main (asked GitHub)"
        except Exception:
            remote_oid = None
        if remote_oid:
            have = subprocess.run(["git", "-C", repo, "cat-file", "-e", remote_oid + "^{commit}"],
                                  capture_output=True, text=True, timeout=30).returncode == 0
            if not have:
                # the remote moved past what the local clone has fetched: local-only count is then measured
                # against the local origin/main ref, but the ancestry test still uses the real remote head
                # after a bounded fetch
                subprocess.run(["git", "-C", repo, "fetch", "-q", "--no-tags", "origin", "main"],
                               capture_output=True, text=True, timeout=60)
        base = remote_oid or _git(["rev-parse", "origin/main"], 30)
        if not base:
            return None
        count = _git(["rev-list", "--count", base + "..main"], 60)
        anc = subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", newest, base],
                             capture_output=True, text=True, timeout=60).returncode == 0
        return (int(count) if count is not None else -1, anc, src)
    except Exception:
        return None


def _last_dream_commit_age():
    """Seconds since the newest 'Nightly dream: consolidation' commit in the brain repo, from git's own log
    (the artifact, not the dream's log line). None if git cannot answer - reported as not clean, never as ok."""
    import subprocess
    repo = os.path.join(os.path.expanduser("~"), "brain-repo")
    try:
        p = subprocess.run(["git", "-C", repo, "log", "-1", "--grep=Nightly dream: consolidation", "--format=%ct"],
                           capture_output=True, text=True, timeout=60)
        if p.returncode != 0 or not p.stdout.strip():
            return None
        return max(0.0, time.time() - float(p.stdout.strip().splitlines()[0]))
    except Exception:
        return None


def _no_dream_committer():
    """Why this machine makes no dream commit, or None when it should. The consolidation commit comes from
    run_dream.ps1; with no PowerShell the dream is run_dream.py, which commits nothing (the Mac mini, whose
    ~/brain-repo is also a shallow clone). Only consulted when no such commit is found."""
    import shutil
    if any(shutil.which(x) for x in ("pwsh", "powershell", "powershell.exe")):
        return None
    return "no PowerShell here, so the dream is run_dream.py, which makes no consolidation commit"


def _wired_anywhere(script):
    """True if a hook config on this machine names the script (Claude settings, the dispatcher list, Codex)."""
    for p in (SETTINGS, os.path.join(HERE, "hook_dispatch_list.json"), CODEX_HOOKS):
        try:
            if script in io.open(p, encoding="utf-8").read():
                return True
        except Exception:
            continue
    return False


def _age(path):
    try:
        if os.path.isdir(path):             # newest committed per-session state
            newest = None
            with os.scandir(path) as entries:
                for entry in entries:
                    if not entry.name.endswith(".json"):
                        continue
                    try:
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        modified = entry.stat(follow_symlinks=False).st_mtime
                    except OSError:         # cleanup or access failure in one session
                        continue
                    newest = modified if newest is None else max(newest, modified)
            return (time.time() - newest) if newest is not None else None
        return time.time() - os.path.getmtime(path)
    except Exception:
        return None


def _fmt(age):
    if age is None:
        return "MISSING"
    if age < 3600:
        return f"{age/60:.0f}m"
    if age < DAY:
        return f"{age/3600:.1f}h"
    return f"{age/DAY:.1f}d"


def _script_path(token, alt_dirs=None):
    """Resolve the path settings actually names; only bare/relative hooks default to HERE.
    alt_dirs: extra base dirs (e.g. a hex-decoded runpy launch dir) to try for a BARE name before
    falling back to HERE - else a hook launched via runpy.run_path(os.path.join(p,'x.py')) with
    p=bytes.fromhex(..) is fabricated DEAD, because its real dir is invisible to the .py-token regex."""
    token = os.path.expandvars(os.path.expanduser(str(token or "").strip().strip('"\'')))
    if not token:
        return None
    if os.path.isabs(token):
        return os.path.normpath(token)
    cands = [os.path.normpath(os.path.join(HERE, token))]
    for d in (alt_dirs or []):
        try:
            cands.append(os.path.normpath(os.path.join(d, token)))
        except Exception:
            pass
    for c in cands:
        if os.path.exists(c):
            return c
    return cands[0]  # keep the HERE default so a genuinely-missing hook still reports DEAD


def _hex_dirs(cmd):
    """Real launch dirs hidden as hex literals: `p=bytes.fromhex('..').decode(); run_path(join(p,'x.py'))`
    (the error_graph shadow hook does this). The .py-token regex only sees the bare name, so decode the
    hex to recover the true dir and resolve the bare name against it."""
    out = []
    for h in re.findall(r"bytes\.fromhex\(\s*['\"]([0-9a-fA-F]+)['\"]\s*\)", cmd or ""):
        try:
            dec = bytes.fromhex(h).decode("utf-8", "replace").strip()
            if dec and dec not in out:
                out.append(dec)
        except Exception:
            pass
    return out


def _wired_hooks():
    # concrete configured path -> events. The old basename-only map fabricated a dead hook whenever
    # settings intentionally pointed outside .claude/dreaming (agent_mesh/mesh_hook.py exposed this);
    # a hex-encoded runpy launch dir (error_graph/hook.py) is the same class - _hex_dirs recovers it.
    m = {}
    try:
        d = json.load(io.open(SETTINGS, encoding="utf-8"))
    except Exception:
        return m
    for ev, groups in d.get("hooks", {}).items():
        for g in groups:
            for h in g.get("hooks", []):
                for a in (h.get("args", []) or []):
                    if isinstance(a, str) and a.lower().endswith(".py"):
                        path = _script_path(a)
                        if path:
                            m.setdefault(path, set()).add(ev)
                cmd = h.get("command", "") if isinstance(h.get("command", ""), str) else ""
                hexdirs = _hex_dirs(cmd)
                for match in re.findall(
                        r'(?i)(?:"([^"\r\n]+\.py)"|\'([^\'\r\n]+\.py)\'|([^\s"\';&|]+\.py))',
                        cmd):
                    token = next((part for part in match if part), "")
                    path = _script_path(token, hexdirs)
                    if path:
                        m.setdefault(path, set()).add(ev)
    return m


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    wired = _wired_hooks()
    # 1. WIRING HEALTH
    print("=" * 74)
    print(" HOOK LIVENESS  -  would it run, and is the data flowing?")
    print("=" * 74)
    print("\nWIRING HEALTH (wired in settings.json -> script exists + compiles):")
    dead = []
    for path in sorted(wired, key=lambda value: (os.path.basename(value).lower(), value.lower())):
        script = os.path.basename(path)
        if not os.path.exists(path):
            status = "DEAD - script missing"
            dead.append(script)
        else:
            try:
                py_compile.compile(path, doraise=True)
                status = "ok"
            except Exception as e:
                status = f"BROKEN - {type(e).__name__}"
                dead.append(script)
        location = "" if os.path.dirname(path) == HERE else " @ " + path
        print(f"  {script:24} [{', '.join(sorted(wired[path]))}]  {status}{location}")

    # A Stop blocker short-circuits hooks configured after it. The wall-clock contract must therefore be
    # the FINAL Stop hook, or timed sessions silently miss report, mailbox, and reasoning guards. This was
    # the live order until 2026-07-13; audit the flattened group order so it cannot drift back unnoticed.
    _order_bad = []

    def _stop_label(_hook):
        _args = _hook.get("args", []) or []
        _cmd = str(_hook.get("command", ""))
        _pyargs = [a for a in _args if isinstance(a, str) and a.lower().endswith(".py")]
        if _pyargs:
            return os.path.basename(_pyargs[-1])
        if "ERROR_GRAPH_MODE" in _cmd:
            return "error_graph/hook.py"
        _m = re.search(r"([A-Za-z0-9_./\\:-]+\.py)", _cmd)
        if _m:
            return os.path.basename(_m.group(1).replace("\\", "/"))
        return _cmd[:60]

    try:
        _cfg = json.load(io.open(SETTINGS, encoding="utf-8"))
        _stop_order = []
        for _group in _cfg.get("hooks", {}).get("Stop", []):
            for _hook in _group.get("hooks", []):
                _stop_order.append(_stop_label(_hook))
        # 2026-09-20: since hook_dispatch.py took over the Stop event (2026-09-15/17), settings.json holds ONE
        # dispatcher entry and the real list lives in hook_dispatch_list.json. The dispatcher runs EVERY hook
        # of the event in one process and aggregates the blocks afterwards (hook_dispatch.merge), so a Stop
        # blocker no longer short-circuits the hooks after it and the order is not load-bearing; what must
        # hold is that work_timer.py is in the dispatched list at all. Before this the checker read the
        # dispatcher entry itself as "work_timer.py is not final" on every wake - a permanent red verdict is
        # how a safety checker stops being read [[a_pass_needs_a_way_to_fail]].
        # 2026-10-29 (Mac): the canonical file (17 Oct) allows only NAMED standalone Stop entries beside the dispatcher
        # (the harness runs an event's hooks in parallel); an unknown sibling still fails. The Mac's is the keep-working gate.
        _STANDALONE_STOP_OK = {"keep_working_after_compact.py", "keep_working_brain.py"}
        _dispatched = ("hook_dispatch.py" in _stop_order and
                       all(s == "hook_dispatch.py" or s in _STANDALONE_STOP_OK for s in _stop_order))
        if _dispatched:
            _list_path = os.path.join(HERE, "hook_dispatch_list.json")
            _list = json.load(io.open(_list_path, encoding="utf-8"))
            _dispatched_order = []
            for _group in _list.get("Stop", []):
                for _hook in _group.get("hooks", []):
                    _dispatched_order.append(_stop_label(_hook))
            if "work_timer.py" not in _dispatched_order:
                _order_bad.append("Stop order unsafe: work_timer.py is not in the dispatcher's Stop list (%s)" %
                                  (" -> ".join(_dispatched_order) if _dispatched_order else "empty list"))
            _order_note = ("ok - Stop is dispatched in one process (%d hooks run, blocks aggregated), so order is "
                           "not load-bearing; work_timer.py present" % len(_dispatched_order))
        else:
            if not _stop_order or _stop_order[-1] != "work_timer.py":
                _order_bad.append("Stop order unsafe: work_timer.py is not final (%s)" %
                                  (" -> ".join(_stop_order) if _stop_order else "no Stop hooks"))
            _order_note = "ok - work_timer.py is final; report/mail/reasoning guards run first"
        print("\nSTOP ORDER (a blocker prevents later warning delivery):")
        print("  " + (_order_bad[0] if _order_bad else _order_note))
    except Exception as _exc:
        _order_bad.append("Stop order audit failed: %s" % type(_exc).__name__)

    # Codex uses a separate lifecycle host and hooks.json. A valid Claude settings file says nothing
    # about whether those warnings can reach Codex, so validate the event contract, run both pure wrapper
    # selftests in-process (no console child), and read the privacy-safe host telemetry independently.
    _codex_bad = []
    _codex_note = ""
    try:
        import contextlib as _contextlib
        import codex_warning_acceptance as _cwa
        import codex_reflex_hook as _crh
        import codex_stop_guard as _csg
        _ccfg = json.load(io.open(CODEX_HOOKS, encoding="utf-8"))
        _codex_bad.extend(_cwa.validate(_ccfg))
        with _contextlib.redirect_stdout(io.StringIO()):
            if _cwa._selftest():
                _codex_bad.append("codex_warning_acceptance.py selftest failed")
        for _name, _mod in (("codex_reflex_hook.py", _crh), ("codex_stop_guard.py", _csg)):
            _sink = io.StringIO()
            with _contextlib.redirect_stdout(_sink):
                _rc = _mod._selftest()
            if _rc:
                _codex_bad.append("%s selftest failed" % _name)
        # Load the local metrics reader by path: importing the generic name ``metrics`` can silently
        # reuse an unrelated module already present in a long-lived Python host.
        import importlib.util as _importlib_util
        import sqlite3 as _sqlite3
        _metrics_path = os.path.join(HERE, "error_graph", "metrics.py")
        _spec = _importlib_util.spec_from_file_location("_hook_liveness_error_graph_metrics",
                                                       _metrics_path)
        if _spec is None or _spec.loader is None:
            raise ImportError("cannot load error_graph metrics reader")
        _eg_metrics = _importlib_util.module_from_spec(_spec)
        _spec.loader.exec_module(_eg_metrics)
        _health = _eg_metrics.summarize(CODEX_METRICS, since_s=7 * DAY)
        _groups = [g for g in _health.get("groups", []) if g.get("runtime") == "codex"]
        _calls = sum(int(g.get("calls") or 0) for g in _groups)
        _errors = sum(int(g.get("errors") or 0) + int(g.get("timeouts") or 0) for g in _groups)
        _mode_calls = {mode: sum(int(g.get("calls") or 0) for g in _groups if g.get("mode") == mode)
                       for mode in ("shadow", "advisory", "block")}
        # ``summarize.last_bucket_s`` is global across runtimes. Query the bounded categorical table
        # read-only so active Claude traffic cannot make a dead Codex path look fresh.
        _last = _last_failed = _last_clean = None
        if os.path.isfile(CODEX_METRICS):
            _con = None
            try:
                _uri = __import__("pathlib").Path(CODEX_METRICS).resolve().as_uri() + "?mode=ro"
                _con = _sqlite3.connect(_uri, uri=True, timeout=0.05, isolation_level=None)
                _con.execute("PRAGMA query_only=ON")
                _row = _con.execute(
                    "SELECT MAX(bucket_start_s), "
                    "MAX(CASE WHEN errors+timeouts>0 THEN bucket_start_s END), "
                    "MAX(CASE WHEN calls>errors+timeouts THEN bucket_start_s END) "
                    "FROM hook_health_5m "
                    "WHERE runtime=? AND bucket_start_s>=?",
                    ("codex", int(time.time() - 7 * DAY))).fetchone()
                _last = _row[0] if _row else None
                _last_failed = _row[1] if _row else None
                _last_clean = _row[2] if _row else None
            finally:
                if _con is not None:
                    _con.close()
        _age_note = _fmt(time.time() - float(_last)) if _last else "never"
        _codex_note = ("%d calls/7d (observer=%d reflex=%d Stop=%d), %d errors+timeouts, "
                       "last Codex bucket %s ago" %
                       (_calls, _mode_calls["shadow"], _mode_calls["advisory"], _mode_calls["block"],
                        _errors, _age_note))
        # 2026-08-14: silence is only a regression if Codex actually RAN in
        # the window. Zero sessions means the wrappers had nothing to emit
        # (measured: 157 sessions 08-01..05, zero since 08-07, login log
        # untouched since April - codex_telemetry_silence_is_idle_not_broken).
        # A WARN that cannot tell broken from unused trains readers to skip
        # real regressions.
        if not _codex_sessions_in_window(7 * DAY):
            _codex_note += "; codex idle (no sessions in 7d) - wrapper silence expected"
        else:
            if not _calls or _last is None:
                _codex_bad.append("no Codex lifecycle host telemetry in 7d")
            if not _mode_calls["advisory"]:
                _codex_bad.append("no advisory-reflex wrapper telemetry in 7d")
            if not _mode_calls["block"]:
                _codex_bad.append("no Stop-guard wrapper telemetry in 7d")
        # Strict >: a mixed bucket (one fail-open write drop amid a healthy
        # 5-min window) is the SAME bucket for both maxima and is recovered
        # by its own clean calls; >= re-raised the recorder's designed
        # fail-open floor every night (diagnosed and retired 2026-07-14,
        # 10 errors / 0 timeouts in 48,836 calls, scattered 1-2 per busy
        # bucket - see codex_telemetry_flag_retired_2026_07_14).
        if _last_failed is not None and (_last_clean is None or _last_failed > _last_clean):
            _codex_bad.append("latest Codex lifecycle telemetry includes an unrecovered error/timeout")
        # Configuration is not delivery until the normal Codex host trusts every handler. Never forge
        # these hashes: missing entries require the owner's explicit /hooks review.
        import tomllib as _tomllib
        with open(CODEX_CONFIG, "rb") as _fh:
            _trust_doc = _tomllib.load(_fh)
        _trust = _trust_doc.get("hooks", {}).get("state", {})
        _event_ids = {"UserPromptSubmit": "user_prompt_submit", "PreToolUse": "pre_tool_use",
                      "PostToolUse": "post_tool_use", "Stop": "stop"}
        _expected_trust = []
        for _event, _event_id in _event_ids.items():
            for _gi, _group in enumerate(_ccfg.get("hooks", {}).get(_event, [])):
                for _hi, _handler in enumerate(_group.get("hooks", [])):
                    _expected_trust.append("%s:%s:%d:%d" %
                                           (os.path.normpath(CODEX_HOOKS), _event_id, _gi, _hi))
        _missing_trust = [key for key in _expected_trust
                          if not isinstance(_trust.get(key), dict)
                          or not _trust[key].get("trusted_hash")]
        _codex_note += ", trust %d/%d" % (len(_expected_trust) - len(_missing_trust),
                                          len(_expected_trust))
        if _missing_trust:
            _codex_bad.append("Codex hook trust incomplete (%d/%d trusted); owner must review /hooks" %
                              (len(_expected_trust) - len(_missing_trust), len(_expected_trust)))
    except FileNotFoundError as _exc:
        # 2026-10-29 (Mac): a machine with Codex but no ~/.codex/hooks.json printed only "FileNotFoundError"; name
        # the missing file, since that means Codex sessions there run without any brain hook.
        _codex_bad.append("Codex hooks not installed: %s missing, Codex sessions here run with no brain hooks"
                          % (getattr(_exc, "filename", None) or CODEX_HOOKS))
    except Exception as _exc:
        _codex_bad.append("Codex warning contract audit failed: %s" % type(_exc).__name__)
    print("\nCODEX STATIC WARNING CONTRACT + SHADOW HOST TELEMETRY:")
    if _codex_note:
        print("  telemetry - " + _codex_note)
    if _codex_bad:
        for _problem in _codex_bad:
            print("  " + _problem)
    else:
        print("  ok - static contract, wrapper selftests, trust records, and telemetry present")

    # 2. DATA-FLOW (growth since last run)
    try:
        snap = json.load(io.open(SNAP, encoding="utf-8")) if os.path.exists(SNAP) else {}
    except Exception:
        snap = {}
    newsnap, stale = {}, []
    print("\nDATA-FLOW (append-only streams; grew since last liveness run?):")
    for fn, who in STREAMS.items():
      # per-stream fault isolation: a meta-checker that catches SILENT deaths must never itself die on
      # one stream and take the whole audit down (it did, 2026-07-09 - the exact who-watches-the-watchman
      # failure this tool exists to prevent). One bad stream -> one ERROR line, the rest still audited.
      try:
        p = os.path.join(HERE, fn)
        age = _age(p)
        try:
            size = os.path.getsize(p)
        except Exception:
            size = None
        newsnap[fn] = size
        # snap[fn] can be None (the file was missing on a PRIOR run, so its size was stored as null);
        # `int > None` crashes. Guard both.
        grew = (size is not None and snap.get(fn) is not None and size > snap[fn])
        first = fn not in snap
        if age is None:
            # a missing output is ambiguous: never-ran vs ran-but-output-not-yet-due. Producers that
            # can legitimately produce nothing leave a ran-stamp; read it before crying wolf
            # (this line falsely accused ern_reliability of never running, 2026-07-09).
            ran = _age(os.path.join(HERE, "." + os.path.splitext(fn)[0] + "_ran.json"))
            if ran is not None and ran < 2 * DAY:
                note = f"not yet due (producer ran {_fmt(ran)} ago, output legitimately empty)"
            else:
                note = "MISSING (producer never ran)"
        elif age > STREAM_MAX_AGE.get(fn, 3 * DAY):
            note = f"STALE {_fmt(age)} - producer may have stopped" + _stale_hint(fn); stale.append(fn)
        else:
            note = f"fresh {_fmt(age)}" + ("" if first else (", grew" if grew else ", no growth"))
      except Exception as e:
        note = f"ERROR auditing this stream: {e}"; stale.append(fn)
      print(f"  {fn:20} {note:42} <- {who}")

    # 3. FIRE-RECENCY (informational)
    print("\nFIRE-RECENCY (informational - positive-only stamps stay stale when there's no cause):")
    for stamp, (hook, kind) in STAMPS.items():
        age = _age(os.path.join(HERE, stamp))
        print(f"  {hook:22} last stamp {_fmt(age):>7}   ({kind})")

    try:
        json.dump(newsnap, io.open(SNAP, "w", encoding="utf-8"))
    except Exception:
        pass

    # 4. CHECKPOINT CORRECTNESS (2026-07-11): wiring+growth can be green while a hook silently NO-OPs
    # (the registry_refresh BOM bug did - it compiled, was wired, and register.ps1 kept the file mtime
    # fresh, so this audit would have said 'ok'). Check the INTENDED EFFECT: a live session's crash-recovery
    # checkpoint must track its last turn. Fold any real failure into `dead` so the verdict + wake surface it.
    try:
        import registry_freshness_canary as _rfc
        _crows, _cflags = _rfc.check()
        # INFORMATIONAL only (NOT folded into `dead`). Since 2026-07-13 work_timer is final, a timed loop no
        # longer prevents resume_checkpoint/registry_refresh from running. Old lag can remain until each
        # already-active session reaches another Stop; a many-session pattern still indicates regression.
        print("\nCHECKPOINT CORRECTNESS (informational; timed loops no longer suppress refresh):")
        if _cflags:
            for _fl in _cflags:
                print("  note " + _fl)
        else:
            print("  ok - every recently-active session's checkpoint tracks its last turn")
    except Exception:
        pass

    # 5. SAFETY CORRECTNESS (2026-07-12/13): verify EFFECT, not mere wiring. Numeric P&L memories are not
    # blocking oracles (the 07-12 frame correction retired them), so test the durable, owner-confirmed
    # invariants instead: sign, fixed-sides-only, and non-linear leveraged ETFs. Also prove that the retired
    # hot-period grid number no longer blocks a different frame.
    _safety_bad = list(_order_bad) + list(_codex_bad)
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(HERE, "graph_fire"))
        import pre_report as _pr
        _adv = ["His current strategy loses money every year.",
                "He should sell puts and collect premium.",
                "<ticker> carry is just the expense ratio and negligible because it is a linear -3x fund."]
        _blk = sum(1 for _t in _adv if _pr.check(_t).get("should_block"))
        if _blk < len(_adv):
            _safety_bad.append("pre_report durable-invariant blockers REGRESSED (%d/%d blocked - run referee_suite.py)" % (_blk, len(_adv)))
        _retired = _pr.check("The grid backtest earns $180k per year.").get("should_block")
        if _retired:
            _safety_bad.append("pre_report stale numeric oracle REGRESSED (retired grid magnitude still blocks)")
        print("\nSAFETY CORRECTNESS (durable report invariants + stale-oracle retirement):")
        if _safety_bad:
            for _problem in _safety_bad:
                print("  " + _problem)
        else:
            print("  ok - blocks all %d durable contradictions; retired magnitude does not block" % len(_adv))
    except Exception as _exc:
        _safety_bad.append("safety correctness audit failed: %s" % type(_exc).__name__)
        print("\nSAFETY CORRECTNESS (durable report invariants + stale-oracle retirement):")
        print("  " + _safety_bad[-1])

    # status marker (2026-07-09): a ran-stamp + the current dead/stale verdict, so the meta-checker's
    # OWN liveness is observable and a nightly run can surface a real problem to the wake, instead of
    # this audit only ever running when a session happens to invoke it by hand (it went silently dead
    # ~17h before a manual run caught it - the watchman was not watched).
    try:
        pass  # status file is written AFTER section 5 now - see below
    except Exception:
        pass

    # 5. DERIVED ARTIFACTS (2026-08-17). Recall reads DERIVED files - a lexical index, an
    # embedding sidecar - that no hook writes and no append-only stream covers, so nothing above
    # watched them. On 2026-08-17 the BM25 index had been frozen 25.8h with 136 memories invisible
    # to lexical recall (99 of them a single campaign day): BrainDream was disabled for compute
    # DISTRESS and the cheap index build went down with the expensive REM pass. Recall never looked
    # broken - the semantic half stayed fresh, so queries kept returning plausible, well-connected
    # OLD memories. A half-blind index does not look blind; it looks like a corpus with nothing new.
    #
    # TWO NUMBERS, BECAUSE THEY MEAN DIFFERENT THINGS. AGE is the trigger. The bar was 30h when the
    # only builder was the nightly dream; since 2026-08-17 the rebuild rides the 3-hourly
    # BrainEmbeddingRefresh launcher instead, so 8h is ~2 missed cycles and 30h would have taken a
    # day to notice a broken pipeline. Retune this if that cadence changes again. STRANDED is the damage - memories
    # already written that lexical recall cannot see. Stranded alone must NOT trigger: a normal
    # working day writes ~100 memories that are legitimately unindexed until the next nightly build,
    # so alarming on that count would cry wolf every evening. Report both, trigger on age.
    _artifact_bad = []
    print("\nDERIVED ARTIFACTS (recall inputs no hook writes; age triggers, coverage quantifies):")
    cov = _index_coverage()
    idx_age = _age(os.path.join(HERE, "bm25_index.json.gz"))
    if cov is None:
        print(f"  bm25 lexical index     age {_fmt(idx_age):>7}   coverage UNKNOWN (unreadable) <- not clean")
        _artifact_bad.append("bm25 index unreadable")
    else:
        indexed, eligible, stranded = cov
        missed_cycle = idx_age is not None and idx_age > 8 * 3600
        verdict = "MISSED A REBUILD CYCLE" if missed_cycle else "ok"
        print(f"  bm25 lexical index     age {_fmt(idx_age):>7}   {indexed}/{eligible} eligible docs, "
              f"{stranded} stranded from lexical recall - {verdict}")
        if missed_cycle:
            _artifact_bad.append(f"bm25 index {_fmt(idx_age)} old, {stranded} memories stranded "
                                 f"(cheap fix: python bm25.py build)")
    # THE CORRECTION POINTERS (2026-09-22). `superseded_by:` is how a stale memory is kept from being served
    # as current, and `resolve()` fails OPEN: a pointer at a file that does not exist leaves the stale memory
    # served while its own frontmatter says it was corrected, and nothing anywhere says so. It has cost us
    # already - the 08-20 MV-steering-OFF reflex kept firing for two days after he reversed it because that
    # memory's pointer named a slug nobody ever filed. Five were dangling when this was first measured (three
    # truncated slugs, so the bug is at write time; brain.py now refuses such a write). Only DANGLING trips:
    # PROSE is reported but not tripped on, because a memory corrected IN ITS OWN BODY has no successor slug
    # to name and would leave this red for ever - and a red that is always red stops being read.
    try:
        import supersession as _sup
        _p_routes, _p_dangling, _p_prose, _p_partial = _sup.audit_pointers(MEMDIR)
        _p_recov = sum(1 for _r in _p_prose if _r[3])
        print(f"  correction pointers    {len(_p_routes):>7} ok   {len(_p_dangling)} dangling, "
              f"{len(_p_prose)} prose ({_p_recov} recovered by the leading slug) - "
              f"{'POINTERS NAME NOTHING' if _p_dangling else 'ok'}")
        if _p_dangling:
            _artifact_bad.append(
                "%d correction pointer(s) name a memory that does not exist, so the stale memory is served "
                "as CURRENT (resolve fails open): %s - run python supersession.py audit --memory-dir \"%s\""
                % (len(_p_dangling), ", ".join("%s -> %s" % (_s, _t) for _s, _f, _t in _p_dangling[:3]),
                   MEMDIR))
    except Exception as _exc:
        print("  correction pointers    UNKNOWN   (audit failed: %s) <- not clean" % type(_exc).__name__)
        _artifact_bad.append("correction-pointer audit failed: %s" % type(_exc).__name__)
    # THE MATH TRANSPORT (2026-09-22). A theorem written as a memory is invisible to the math hook until it is a
    # claim in the math store; that step was a person twice (09-20 hand-ingest, lapsed by 09-22 with ZERO claims
    # on the read-once FEI theorem while five memories carried it). math_transport.py now carries results
    # automatically every 15 minutes (task BrainMathTransport15m). This trips when the transport has not run for
    # two hours (the task is dead) or a queued result has waited two days for an agent's eye - the two ways the
    # store goes stale again without anyone saying so.
    try:
        import io as _io, json as _json, time as _time
        _mt_state = os.path.join(os.path.dirname(os.path.abspath(__file__)), "math_transport_state.json")
        _mt_queue = os.path.join(os.path.dirname(os.path.abspath(__file__)), "math_transport_queue.jsonl")
        _mt = _json.load(_io.open(_mt_state, encoding="utf-8")) if os.path.exists(_mt_state) else {}
        _mt_age = None
        if _mt.get("last_run"):
            _mt_age = (_time.time() - _time.mktime(_time.strptime(_mt["last_run"], "%Y-%m-%dT%H:%M:%S"))) / 3600.0
        _mt_q, _mt_oldest = {}, 0.0
        # ONE READER OF THE QUEUE (30 Sep). This check kept its own parser, which closed an entry only on ingested or
        # dismissed; the transport also closes on revised and revision_dismissed. A QFORM result dismissed by hand on
        # 25 Sep 02:48 therefore read as "waited 136h for an agent" in every wake-up line from 27 Sep on, a false alarm
        # that teaches readers to skip this block. Ask the transport itself; the local parse is only the fallback.
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import math_transport as _mtr
            _mt_q = dict(_mtr.queued())
        except Exception:
            _mt_q = {}
            if os.path.exists(_mt_queue):
                for _line in _io.open(_mt_queue, encoding="utf-8", errors="replace"):
                    try:
                        _r = _json.loads(_line)
                    except Exception:
                        continue
                    if _r.get("action") == "queued":
                        _mt_q[_r["slug"]] = _r
                    elif _r.get("action") in ("ingested", "dismissed", "revised", "revision_dismissed"):
                        _mt_q.pop(_r.get("slug"), None)
        for _r in _mt_q.values():
            try:
                _mt_oldest = max(_mt_oldest, (_time.time() - _time.mktime(_time.strptime(_r["ts"], "%Y-%m-%dT%H:%M:%S"))) / 3600.0)
            except Exception:
                pass
        _mt_bad = []
        if _mt_age is None:
            _mt_bad.append("the math transport has NEVER RUN")
        elif _mt_age > 2.0:
            # the 15-min task is Windows-only; on the Mac the transport runs only inside a dream (no launchd job yet)
            _mt_bad.append("the math transport last ran %.1fh ago (%s)" % (_mt_age, "task BrainMathTransport15m dead?"
                           if sys.platform == "win32" else "no Mac scheduler: it runs only in a dream; a launchd job "
                           "waits on the owner"))
        if _mt_oldest > 48.0:
            _mt_bad.append("a queued math result has waited %.0fh for an agent (python math_transport.py status)" % _mt_oldest)
        # A STALE PREFILTER INDEX (2026-09-22, round 5): built 14:51, it hid the twelve conjecture nodes filed 19:11-22:39
        # from every query. The hook now scores newer nodes on the fly and the transport rebuilds a stale index, so this
        # trips only when both have failed: nodes newer than an index that is more than an hour old.
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import math_prefilter as _mp
            _mp_store = os.environ.get("MATHBRAIN_STORE", "")
            _mp_fresh = _mp.fresh_nodes(_mp_store, limit=1000)
            if _mp_fresh:
                _mp_age = (_time.time() - os.path.getmtime(_mp.index_path(_mp_store))) / 3600.0
                if _mp_age > 1.0:
                    _mt_bad.append("the math prefilter index is %.1fh old with %d newer store node(s) the transport has not"
                                   " folded in (python math_prefilter.py build <store>)" % (_mp_age, len(_mp_fresh)))
        except Exception:
            pass
        print("  math transport         %s  %d queued%s - %s"
              % (("last run %.1fh ago" % _mt_age) if _mt_age is not None else "never ran", len(_mt_q),
                 (" (oldest %.0fh)" % _mt_oldest) if _mt_q else "", "; ".join(_mt_bad) if _mt_bad else "ok"))
        _artifact_bad.extend(_mt_bad)
    except Exception as _exc:
        print("  math transport         UNKNOWN   (status read failed: %s) <- not clean" % type(_exc).__name__)
        _artifact_bad.append("math transport status read failed: %s" % type(_exc).__name__)
    # THE DISPATCHER ALLOWLIST (2026-09-23, round 5). hook_dispatch.py runs a hook in its own process unless
    # hook_dispatch_allow.json, newer than the dispatcher, names it. An edit to the dispatcher on 09-21 17:56 left the
    # list stale for 32 hours; the dispatcher wrote "ALLOWLIST STALE" into its own log on every event and nobody read
    # it, while every tool call paid a median 19.6 s of hooks (3.7 s once re-signed) and every prompt 39.7 s (12.0 s).
    # A log is not a report: say it where a session wakes.
    try:
        _hd_dir = os.path.dirname(os.path.abspath(__file__))
        _hd_py, _hd_al = os.path.join(_hd_dir, "hook_dispatch.py"), os.path.join(_hd_dir, "hook_dispatch_allow.json")
        if os.path.exists(os.path.join(_hd_dir, "HOOK_DISPATCH_ENABLED")) and os.path.exists(_hd_py):
            _hd_why = ""
            if not os.path.exists(_hd_al):
                _hd_why = "missing"
            elif os.path.getmtime(_hd_al) < os.path.getmtime(_hd_py):
                _hd_why = "older than hook_dispatch.py (edited %s, list signed %s)" % (
                    time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(_hd_py))),
                    time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(_hd_al))))
            if _hd_why:
                print("  dispatcher allowlist   STALE - %s: every hook spawns its own process; re-sign with"
                      " python -X utf8 hook_dispatch_control.py" % _hd_why)
                _artifact_bad.append("dispatcher allowlist %s - every hook spawns (run hook_dispatch_control.py)" % _hd_why)
            else:
                print("  dispatcher allowlist   ok")
    except Exception as _exc:
        print("  dispatcher allowlist   UNKNOWN   (%s)" % type(_exc).__name__)
    # FILM HOOKS THE FAST LIST DOES NOT HOLD (13 Oct 2026, R21). Only a control run adds a hook to that list, and nobody
    # ran one after MR166-MR193 were wired (8-13 Oct): 28 film hooks started a Python process each on every tool call
    # and prompt of every session - the dispatcher's PreToolUse median 6.03 s over the hour before they joined, 0.62 s
    # after. hook_dispatch_extend.py tests and adds them; this line says when one is waiting.
    try:
        import hook_dispatch_extend as _hx
        _hx_miss = _hx.unlisted_film_hooks()
        if _hx_miss:
            print("  dispatcher film hooks  %d wired film hook(s) not on the fast list (%s) - each starts its own process"
                  " on every tool call and prompt; add them: python -X utf8 hook_dispatch_extend.py --film"
                  % (len(_hx_miss), ", ".join(_hx_miss[:3]) + (" ..." if len(_hx_miss) > 3 else "")))
            _artifact_bad.append("%d film hook(s) off the dispatcher fast list - each spawns a process per tool call"
                                 " (python -X utf8 hook_dispatch_extend.py --film)" % len(_hx_miss))
        elif _hx_miss is not None:
            print("  dispatcher film hooks  ok (all on the fast list)")
    except Exception as _exc:
        print("  dispatcher film hooks  UNKNOWN   (%s)" % type(_exc).__name__)
    # THE MATH HOOK IN PRODUCTION (2026-09-22). mathbrain_silence.tsv (real sessions only; the harness writes its
    # own file) records WHY the hook served nothing. From 2026-09-15 20:25 every real-session row was
    # 'ModuleNotFoundError: numpy' - the dispatcher launches hooks as `python -s`, numpy lives in the user site -
    # 710 rows over eight days, and nobody read the file: the wake check looked at freshness, never at the reason.
    # An exception row is not an abstention; one in the last 24 h means the hook is BROKEN for everyone.
    try:
        _ms_path = os.path.join(HERE, "mathbrain_silence.tsv")
        _ms_exc, _ms_last, _ms_rows = 0, "", 0
        _ms_refused, _ms_refused_last = 0, ""
        _ms_last_exc_ts, _ms_ok_ts = 0.0, []  # when the last exception fell, and every clean live fire's time
        _ms_exc3, _ms_rows3 = 0, 0          # rows AFTER the hook file last changed decide "still broken" vs "recovered"
        _ms_cut3 = time.time() - 3 * 3600   # (a fix lands mid-day; rows before it are history, rows after it are the test)
        try:
            _ms_cut3 = max(_ms_cut3, os.path.getmtime(os.path.join(HERE, "math_sense.py")))
        except Exception:
            pass
        if os.path.exists(_ms_path):
            _ms_cut = time.time() - 24 * 3600
            for _ms_line in io.open(_ms_path, encoding="utf-8", errors="replace"):
                _ms_c = _ms_line.rstrip("\n").split("\t")
                if len(_ms_c) < 3:
                    continue
                try:
                    _ms_ts = float(_ms_c[0])
                except Exception:
                    continue
                if _ms_ts < _ms_cut:
                    continue
                if _ms_c[2].startswith("skip_"):
                    # a pre-retrieval skip (relay envelope, picture batch - 2026-09-23) exits before the imports that
                    # broke for eight days, so it proves nothing about the retrieval path either way
                    continue
                _ms_rows += 1
                if "selected_card_refused" in _ms_c[2] and not _ms_c[2].startswith("integrity:"):
                    # REFUSED-CARD SILENCES (2026-10-31). The hook picked a card the integrity reader refused, so the
                    # whole block went silent: a bad card in the store, not a broken hook, and no exception either.
                    # 5-15 Oct had 33 of these in real sessions and this check called each day "ok". Each refusal
                    # writes two rows (SelectionUnavailable + integrity:); count the first only.
                    _ms_refused += 1
                    _ms_refused_last = _ms_c[2].split(":")[-1].strip()[:50]
                _ms_is_exc = bool(re.match(r"^[A-Za-z]*(Error|Exception)\b", _ms_c[2]))
                if _ms_is_exc:
                    _ms_exc += 1
                    _ms_last = _ms_c[2].strip()[:70]
                    _ms_last_exc_ts = max(_ms_last_exc_ts, _ms_ts)
                else:
                    _ms_ok_ts.append(_ms_ts)
                if _ms_ts >= _ms_cut3:
                    _ms_rows3 += 1
                    if _ms_is_exc:
                        _ms_exc3 += 1
        # A SERVE IS A LIVE FIRE TOO (2026-09-23). The silence log records only the prompts the hook answered with
        # nothing, so a hook that serves every math prompt writes no row at all - and this check read that as "has not
        # fired live since", a red that stayed up for hours after the numpy fix while 92 real sessions were being
        # served. surfaced.tsv carries one `mathbrain` row per real-session serve (math_sense.py writes it only for
        # uuid sessions, the same test as the silence log), so a serve after the cut is the proof the path works.
        _ms_served3 = 0
        try:
            _sf = os.path.join(HERE, "surfaced.tsv")
            if os.path.exists(_sf):
                for _sf_line in io.open(_sf, encoding="utf-8", errors="replace"):
                    _sf_c = _sf_line.rstrip("\n").split("\t")
                    if len(_sf_c) >= 4 and _sf_c[3] == "mathbrain":
                        try:
                            _sf_ts = float(_sf_c[0])
                            if _sf_ts >= _ms_cut3:
                                _ms_served3 += 1
                            if _sf_ts >= time.time() - 24 * 3600:
                                _ms_ok_ts.append(_sf_ts)
                        except Exception:
                            pass
        except Exception:
            pass
        if _ms_exc3:
            print("  math hook (live)       %d exception rows since the hook file last changed (or 3h) (%d in 24h; last: %s) - BROKEN IN PRODUCTION"
                  % (_ms_exc3, _ms_exc, _ms_last))
            _artifact_bad.append("the math hook raised in %d live fires since the hook file last changed (or 3h) (%s) - it serves nobody until fixed" % (_ms_exc3, _ms_last))
        elif _ms_exc and (_ms_rows3 or _ms_served3):
            print("  math hook (live)       %d exception rows earlier in 24h, none since the hook file last changed (or 3h) (%d live silences, %d live serves since) - recovered"
                  % (_ms_exc, _ms_rows3, _ms_served3))
        elif _ms_exc and sum(1 for _t in _ms_ok_ts if _t > _ms_last_exc_ts):
            # TWO QUESTIONS, NOT ONE (2026-09-23). "Is the exception still happening?" is answered by clean live fires
            # AFTER the last exception row; "does the latest edit work in production?" needs a real prompt after the
            # edit. On 09-23 the numpy exceptions had ended at 09-22 19:3x with hundreds of serves after them, but a
            # 06:18 edit met no real math prompt before dawn, and this line told every waking session that the hook
            # "raised 12 times and has not fired live since (numpy)" - an old fault named as a current one.
            _ms_after = sum(1 for _t in _ms_ok_ts if _t > _ms_last_exc_ts)
            _ms_quiet_h = (time.time() - max(_ms_ok_ts)) / 3600.0
            try:
                _ms_edit_h = (time.time() - os.path.getmtime(os.path.join(HERE, "math_sense.py"))) / 3600.0
            except Exception:
                _ms_edit_h = -1.0
            _ms_last_ok = time.strftime("%m-%d %H:%M", time.localtime(max(_ms_ok_ts)))
            _ms_exc_end = time.strftime("%m-%d %H:%M", time.localtime(_ms_last_exc_ts))
            if _ms_quiet_h < 12:
                print("  math hook (live)       %d exception rows in 24h, all before %s, %d clean live fires after (last %s); "
                      "no real math prompt in the %.1fh since (newest hook edit %.1fh ago) - quiet, not a fault"
                      % (_ms_exc, _ms_exc_end, _ms_after, _ms_last_ok, _ms_quiet_h, _ms_edit_h))
            else:
                print("  math hook (live)       exceptions ended %s, but NO clean live fire for %.1fh (last %s) <- not clean"
                      % (_ms_exc_end, _ms_quiet_h, _ms_last_ok))
                _artifact_bad.append("the math hook has had no clean live fire for %.1fh (its old exceptions ended %s)"
                                     % (_ms_quiet_h, _ms_exc_end))
        elif _ms_exc:
            print("  math hook (live)       %d exception rows in 24h and NO live fire since the hook file last changed (or 3h) - fix unverified in production <- not clean"
                  % _ms_exc)
            _artifact_bad.append("the math hook raised %d times in 24h and has not fired live since (%s) - unverified" % (_ms_exc, _ms_last))
        else:
            print("  math hook (live)       %d silences in 24h, none an exception - ok" % _ms_rows)
        if _ms_refused:
            # a store fault, not a hook fault: printed, not added to _artifact_bad (the hook is working as built)
            print("  math hook (store)      %d live prompts silenced in 24h by a refused selected card (last: %s) <- a bad "
                  "card is hiding answers; math_refused_cards.py names them" % (_ms_refused, _ms_refused_last))
    except Exception as _exc:
        print("  math hook (live)       UNKNOWN   (silence log read failed: %s) <- not clean" % type(_exc).__name__)
        _artifact_bad.append("math hook silence log read failed: %s" % type(_exc).__name__)
    # THE DREAM'S COMMIT (2026-09-02). Every dream from 08-25 to 09-02 logged 'persist commit deferred: scoped
    # git add failed (exit=128)' and nobody read it: the wake quotes the dream's reflection, not its persist
    # line, so six days of nightly consolidation stayed local while the run read as complete. This probe reads
    # the repo's own history - the artifact, not the log - and trips when no "Nightly dream: consolidation"
    # commit has landed within DREAM_COMMIT_MAX_H (the dream runs at least daily). The reason lives in the
    # newest dream log's 'git said:' line (run_dream.ps1 captures git's stderr since 2026-09-02).
    dream_commit_age = _last_dream_commit_age()
    _no_committer = _no_dream_committer() if dream_commit_age is None else None
    if _no_committer:
        print(f"  dream repo commit      n/a           {_no_committer}")
    elif dream_commit_age is None:
        print("  dream repo commit      age UNKNOWN   (git log unreadable) <- not clean")
        _artifact_bad.append("last dream commit age unreadable (git log failed)")
    else:
        late = dream_commit_age > DREAM_COMMIT_MAX_H * 3600
        print(f"  dream repo commit      age {_fmt(dream_commit_age):>7}   newest 'Nightly dream: consolidation' commit - "
              f"{'NO DREAM COMMIT IN ' + str(DREAM_COMMIT_MAX_H) + 'h' if late else 'ok'}")
        if late:
            _artifact_bad.append(f"no dream commit for {_fmt(dream_commit_age)} - the persist step is deferring; "
                                 f"read 'git said:' in the newest dreaming/logs/dream_*.log")
    # ...AND IS IT ON GITHUB (2026-09-20). A commit that exists locally satisfies the probe above while the push
    # is rejected every cycle: from 2026-09-19 19:30 to 09-20 11:30 three consolidation commits sat on local
    # main (pushed=False in every log, the reason swallowed by Out-Null) because waking sessions had pushed 28
    # commits from worktrees and the exact-SHA push was a non-fast-forward. The brain on GitHub was a day stale
    # and nothing said so. This asks the remote itself (ls-remote, 20 s bound) and counts local-only commits.
    _unpushed = None if _no_committer else _dream_commits_unpushed()
    if _no_committer:
        print("  dream commit on GitHub n/a           no dream commit is made on this machine")
    elif _unpushed is None:
        print("  dream commit on GitHub UNKNOWN   (remote or git unreadable within the bound) <- not clean")
        _artifact_bad.append("could not verify that the newest dream commit reached origin/main (ls-remote/rev-list failed)")
    else:
        _n_local, _on_remote, _remote_src = _unpushed
        if _on_remote:
            print(f"  dream commit on GitHub yes       newest consolidation commit is on {_remote_src}; local-only commits: {_n_local} - ok")
        else:
            print(f"  dream commit on GitHub NO        {_n_local} local-only commit(s) on main ({_remote_src}) - "
                  f"the exact-SHA push is being rejected; rebase main and push")
            _artifact_bad.append(f"newest dream commit is NOT on {_remote_src} ({_n_local} local-only commits): the dream's "
                                 f"push is rejected as non-fast-forward; rebase local main onto origin/main and push "
                                 f"(run_dream.ps1's Sync-LocalMainToAdvertised does this each cycle since 2026-09-20)")

    # THE OWNER-PROMPT LAYERS (2026-09-18). Every session's wake picture and every hourly delta read
    # memory/prompts/owner_prompts_hourly.json, rebuilt by the BrainOwnerPromptsHourly task (hourly, 30 min limit,
    # incremental ledger read). A task that stops - a disabled trigger, a stall past its limit, a broken lexicon -
    # leaves agents reading yesterday's threads with no visible symptom, because the hook keeps printing whatever
    # the file holds. Age triggers at 3 h (two missed cycles); the hook's own --status shows the same.
    _op_json = os.path.join(MEMDIR, "prompts", "owner_prompts_hourly.json")
    _op_age = _age(_op_json)
    # A machine that never wired owner_prompts_hook.py has no reader for the build, so a missing or old one
    # starves nothing (the Mac mini, 30 Oct). Wired, the 3 h rule holds as before.
    _op_wired = _wired_anywhere("owner_prompts_hook.py")
    _op_late = _op_wired and (_op_age is None or _op_age > 3 * 3600)
    if not _op_wired:
        print(f"  owner-prompt layers    age {_fmt(_op_age):>7}   n/a - owner_prompts_hook.py is not wired here, nothing reads the build")
    else:
        print(f"  owner-prompt layers    age {_fmt(_op_age):>7}   hourly build read by every wake and delta - "
              f"{'NO FRESH BUILD IN 3h' if _op_late else 'ok'}")
    if _op_late:
        _artifact_bad.append(f"owner-prompt layers build {_fmt(_op_age)} old - the BrainOwnerPromptsHourly task is not landing; "
                             f"run python owner_prompts_hook.py --status, then owner_prompts_hourly.py")
    # The window itself: the builder keeps 3000 prompts. A rotated or sharded ledger (shard_prompt_archive.py exists)
    # would pass the age check with a fresh, much shorter file - the wake picture would quietly show days, not weeks.
    try:
        _op_n = len(json.load(io.open(_op_json, encoding="utf-8")).get("prompts") or [])
    except Exception:
        _op_n = None
    if _op_n is not None and _op_n < 2000:
        print(f"  owner-prompt window     {_op_n} prompts   expected about 3000 - SHORT WINDOW (ledger rotated or sharded?)")
        _artifact_bad.append(f"owner-prompt layers hold {_op_n} prompts, expected about 3000 - the ledger may have been rotated; "
                             f"run owner_prompts_hourly.py --full and read owner_prompts_hook.py --status")
    # One prompt hook, wired once. A second entry for owner_prompt_context.py doubles every session's prompt injection.
    try:
        _hd = io.open(os.path.join(HERE, "hook_dispatch_list.json"), encoding="utf-8").read()
        if "owner_prompt_context.py" in _hd:
            print("  owner-prompt wiring    owner_prompt_context.py is ALSO wired on its own - prompts are injected twice")
            _artifact_bad.append("owner_prompt_context.py is wired beside owner_prompts_hook.py in hook_dispatch_list.json - remove the second entry")
    except Exception:
        pass
    # Any script wired twice under one event and matcher runs twice and prints twice. One landing once copied a whole
    # 221-hook group and for four days nothing said so (1 Nov 2026, hook_double_wiring.py).
    _dl = os.path.join(HERE, "hook_dispatch_list.json")
    if os.path.exists(_dl):
        try:
            from hook_double_wiring import double_wired as _dw
            for _ev, _dup in _dw(_dl).items():
                print(f"  double wiring          {_ev}: {len(_dup)} script(s) wired twice, e.g. {_dup[0][1][-70:]}")
                _artifact_bad.append(f"{_ev}: {len(_dup)} hook script(s) wired twice in hook_dispatch_list.json under one "
                                     f"matcher - each runs and prints twice; merge the groups (python hook_double_wiring.py)")
        except Exception as _exc:
            _artifact_bad.append("double-wiring check failed: %s" % type(_exc).__name__)

    # WRITE THE STATUS FILE HERE, NOT EARLIER. It used to be written before section 5 ran, so
    # _artifact_bad could not possibly appear in it: a missed rebuild cycle printed to the dream
    # log and the WAKE NEVER SAW IT - exactly the silent failure section 5 exists to prevent,
    # reproduced by the patch that added section 5. wake_brain.ps1 reads dead / stale / safety
    # only, so artifact problems are ALSO appended to `stale`, which lets the existing consumer
    # surface them without editing the session wake path. `artifacts` is for machine readers.
    try:
        json.dump({"ts": time.time(), "dead": dead,
                   "stale": list(stale) + list(_artifact_bad),
                   "safety": _safety_bad, "artifacts": _artifact_bad},
                  io.open(os.path.join(HERE, ".liveness_status.json"), "w", encoding="utf-8"))
    except Exception:
        pass

    print("\nVERDICT:", end=" ")
    if dead:
        print(f"ATTENTION - {len(dead)} wired hook(s) missing/broken: {', '.join(dead)}")
    elif _safety_bad:
        print(f"ATTENTION - {len(_safety_bad)} safety/delivery contract failure(s); see SAFETY CORRECTNESS")
    elif _artifact_bad:
        print(f"ATTENTION - {len(_artifact_bad)} derived artifact(s) behind: {'; '.join(_artifact_bad)}")
    elif stale:
        print(f"watch - {len(stale)} stream(s) stale (>3d): {', '.join(stale)} (fine if that faculty was idle)")
    else:
        print("all wired hooks compile; all streams fresh. Data is flowing.")


if __name__ == "__main__":
    main()
