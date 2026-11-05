#!/usr/bin/env python3
"""heed_gate.py - convert the ONE right fire into a real one-time stop (deny-once), not more text.

The lived failure this closes (07-12): the command surface SURFACES a reflex on nearly every mistake
but the session reads past it - ps1_hook_edit surfaced 15x, premortem 12x, all read past. The
"[!] REPEATED xN" escalation is still text, and text habituates. The one surface that HEEDS
(pre_report) earns it by blocking only provable contradictions. This gives the command surface the
same deal: a PreToolUse deny that fires at most ~once, only when FOUR independent conditions agree:

  1. ACT, not mention  - the tool call is a real mutation/execution (act|write proxy), never a
                         read-only command or a prompt that merely quotes scarred vocabulary.
  2. TOP scar, high charge - only the rank-1 fired reflex (charge >= 0.95, IDF-ranked) escalates;
                         generics stay context-only. One focused stop, never a wall.
  3. READ-PAST evidence - the same reflex already surfaced >= MIN_COUNT times this session
                         (instinct_prime's dedup count), i.e. the session is demonstrably not heeding.
  4. FAILURE LINK      - the pending command is evidence-linked to a REAL recent failure in
                         outcomes.jsonl: E = max_f  recency(f) * sim_idf(cmd, f.cmd) >= THETA_E.
                         This is the temporal bipartite fire->failure evidence graph: edges weighted
                         by an IDF-weighted Jaccard kernel (IDF fit on the real typical-action
                         population, so `python/echo/git` weigh ~nothing and structural tokens
                         dominate) times an exponential recency kernel (failures self-excite;
                         measured clustering ~20 min within-session, same-day across sessions).

Un-ignorable without habituation: a deny cannot be read past (the tool call does not run), and the
rate caps make it rare enough to stay informative - once per (session, reflex), <= SESSION_MAX per
session, >= FLEET_GAP_S between any two escalations fleet-wide. The deny is a SPEED BUMP, not a
wall: the reason tells the session to answer the scar in one line and re-run; the state marks the
(session, reflex) pair BEFORE the deny is emitted, so the retry can never be blocked again. If the
state cannot be persisted, we do not deny (no persist -> no block, so a broken disk can never loop).

Enforcement is live by default after the owner's 2026-07-25 correction; HEED_ENABLED is an
informational marker, not a delete-to-disable switch. Fail-safe by construction: any error in
the evidence/state path -> None (allow). Stdlib only, forward-slash paths.

Draft lives in improve_2026_07_12/; paths resolve the same after integration into graph_fire/.
"""
import os, io, json, re, time, math, uuid
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
# when drafted in improve_2026_07_12/ the runtime dirs are one level up; after integration HERE==graph_fire
GFDIR = os.path.dirname(HERE) if os.path.basename(HERE).startswith("improve_") else HERE
DREAM = os.path.dirname(GFDIR)
OUTCOMES = os.path.join(DREAM, "outcomes.jsonl")
RANKING = os.path.join(DREAM, "reflex_ranking.json")
IDF_PATH = os.path.join(HERE, "heed_idf.json")     # ships next to the module (built by eval_heed --build-idf)
STATE = os.path.join(HERE, ".heed_state.json")     # budget ledger (staging while drafted; graph_fire when integrated)
LOG = os.path.join(HERE, "heed_log.tsv")
ENABLED = os.path.join(GFDIR, "HEED_ENABLED")      # informational; see _enforcement_live()


def _enforcement_live():
    """Default live. A missing/broken control module cannot silently disable the gate."""
    try:
        import enforcement
        return enforcement.is_live()
    except Exception:
        return True

# --- knobs (calibrated by eval_heed.py on the real corpora; see EVAL_RESULTS in staging) ---
MIN_CHARGE = 0.95        # only maximal-burn scars may stop work
MIN_COUNT = 2            # the reflex must already have been surfaced (and read past) this session
THETA_E = 0.60           # evidence threshold on E = recency * sim_idf (calibrated on the EVEN half worst-case;
                         # time-true replay is cap-dominated and invariant 0.25-0.60, so take the tight bound)
MIN_SHARED = 3           # >= this many shared informative tokens with the failed command
WINDOW_S = 24 * 3600     # how far back a failure can be linked
TAU_S = 4 * 3600         # recency decay: same-day links stay alive, week-old ones die
CROSS_W = 0.7            # cross-session failures count, discounted (backslash bug hit 3 sessions in a day)
SESSION_MAX = 2          # hard cap: escalations per session
FLEET_GAP_S = 1200       # hard cap: >= 20 min between any two escalations machine-wide
TAIL_BYTES = 120000      # bound the outcomes read (rare path only: after conditions 1-3 pass)
_LOCK_TIMEOUT_S = 0.25

# condition 2b: a deny is an ACT-surface intervention - only reflexes that are dangerous AT the
# write/run (the scar tells you a concrete command-level fix: strip the \r, do not commit the secret,
# do not run the bot) may stop a tool call. A claim/reasoning reflex (outside_view, ib_marks on a doc,
# steelman) can co-occur with a token-overlapping failure but denying it is a category error - you
# cannot edit the command to satisfy it. Mirrors habituation_filter.DANGER_SURFACE's 'act' set exactly
# (kept inline so the wake-path import graph stays acyclic, same as _act_proxy/_READONLY above).
# Reproduction (2026-07-12) showed the un-gated top-charge rule mis-fired on outside_view + ib_marks;
# this narrows the deny to genuine act-hazards, the recurring class the owner cares about.
ACT_REFLEXES = frozenset({
    "ps1_hook_edit", "utf8_text_surgery", "heredoc_verbatim_surgery", "crlf_cross_os_parse",
    "windows_backslash_path", "hasty_pattern_match", "secrets_in_git", "windowless",
    "concurrent_races", "premortem", "heavy_compute_remote", "no_local_privileged_stack",
    "preserve_before_destroy", "figure_filename_clobber", "pinned_file_two_tripwires",
    "gate_tool_needs_env", "his_desktop_is_not_an_automation_surface", "healer_first_do_no_harm",
    "stamp_only_on_delivery",
})

_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9]{2,}")
_HEXY = re.compile(r"^[0-9a-f]{6,}$")   # commit shas / uuid chunks: volatile, never informative
# keep in sync with instinct_prime._READONLY (a read-only command that MENTIONS scars is not an act)
_READONLY = re.compile(r"^\s*(grep|rg|cat|less|head|tail|ls|find|wc|git (log|status|diff|show|blame)|"
                       r"python[0-9.]* (-c )?[\w./\\]*(recall|status|audit|eval|check|_test|probe)|echo|"
                       r"type|Get-Content|Select-String|Get-ChildItem)\b", re.I)
_STOP = set("""the a an and or of to in for by is it this that with from as at not do if then else echo
python python3 py exe cmd exec run git head tail cat grep sed awk sort json txt tsv csv log tmp temp
file files dir path paths line lines print import def none true false null var set out err new test""".split())


def _toks(s):
    out = set()
    for w in _WORD.findall((s or "")[:200].lower()):
        if w in _STOP or w.isdigit() or _HEXY.match(w):
            continue
        out.add(w)
    return out


_IDF = None


def _idf():
    global _IDF
    if _IDF is None:
        try:
            _IDF = json.load(io.open(IDF_PATH, encoding="utf-8")).get("idf", {})
        except Exception:
            _IDF = {}
    return _IDF


def sim_idf(atoks, btoks, idf):
    """IDF-weighted Jaccard between two command token sets. 0 unless >= MIN_SHARED shared tokens."""
    inter = atoks & btoks
    if len(inter) < MIN_SHARED:
        return 0.0
    union = atoks | btoks
    num = sum(float(idf.get(t, idf.get("__max__", 3.0))) for t in inter)
    den = sum(float(idf.get(t, idf.get("__max__", 3.0))) for t in union)
    return (num / den) if den > 0 else 0.0


def _act_proxy(event, data):
    """Mirror of instinct_prime._act_proxy (kept local so runtime import graphs stay acyclic)."""
    if event != "PreToolUse":
        return "prompt"
    tn = (data.get("tool_name") or "")
    if tn in ("Edit", "Write", "NotebookEdit"):
        return "write"
    cmd = ((data.get("tool_input", {}) or {}).get("command", "")) or ""
    return "read" if _READONLY.match(cmd) else "act"


def _cmd_text(data):
    ti = data.get("tool_input", {}) or {}
    c = ti.get("command")
    if c:
        return str(c)
    # Edit/Write: the failure join keys on what commands broke; file_path + snippets is the best analog
    return " ".join(str(ti.get(k, "")) for k in ("file_path", "old_string", "new_string", "content"))[:400]


_RANK = None


def _surface_wt():
    global _RANK
    if _RANK is None:
        try:
            _RANK = json.load(io.open(RANKING, encoding="utf-8")).get("surface_weight", {}) or {}
        except Exception:
            _RANK = {}
    return _RANK


def _top(fired):
    """The single most escalation-worthy fired reflex: charge desc, IDF surface weight desc, count desc."""
    try:
        wt = _surface_wt()
        return sorted(fired, key=lambda it: (float(it.get("charge", 0.6)), wt.get(it.get("id"), 0.5),
                                             int(it.get("_count", 1))), reverse=True)[0]
    except Exception:
        return None


def recent_failures(now=None, tail_bytes=TAIL_BYTES):
    """[(ts, session, cmd, sig)] from the outcomes tail inside WINDOW_S. Fail-safe -> []."""
    try:
        now = now or time.time()
        if not os.path.exists(OUTCOMES):
            return []
        with open(OUTCOMES, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - tail_bytes))
            tail = f.read().decode("utf-8", "replace")
        out = []
        for ln in tail.splitlines()[-400:]:
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except Exception:
                continue
            ts = float(o.get("ts", 0) or 0)
            if 0 < now - ts <= WINDOW_S and (o.get("cmd") or ""):
                out.append((ts, o.get("session", "") or "", o["cmd"], o.get("sig", [])))
        return out
    except Exception:
        return []


def evidence(cmdtext, session, now=None, fails=None):
    """E = max_f recency(f) * session_weight(f) * sim_idf(cmd, f.cmd) over recent real failures.
    Returns (E, failure_tuple_or_None). The temporal bipartite evidence edge, scored."""
    try:
        now = now or time.time()
        idf = _idf()
        at = _toks(cmdtext)
        if not at:
            return 0.0, None
        best, bf = 0.0, None
        for (ts, fsess, fcmd, fsig) in (fails if fails is not None else recent_failures(now)):
            s = sim_idf(at, _toks(fcmd), idf)
            if s <= 0:
                continue
            w = math.exp(-max(0.0, now - ts) / TAU_S) * (1.0 if (session and fsess == session) else CROSS_W)
            e = w * s
            if e > best:
                best, bf = e, (ts, fsess, fcmd, fsig)
        return best, bf
    except Exception:
        return 0.0, None


def _lock_byte(handle, unlock=False):
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        mode = msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK
        msvcrt.locking(handle.fileno(), mode, 1)
    else:
        import fcntl
        mode = fcntl.LOCK_UN if unlock else (fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle.fileno(), mode)


@contextmanager
def _state_lock():
    """Cross-process advisory lock. Failure/timeout yields False so the gate fails open."""
    handle = None
    acquired = False
    try:
        handle = open(STATE + ".lock", "a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + _LOCK_TIMEOUT_S
        while True:
            try:
                _lock_byte(handle)
                acquired = True
                break
            except (OSError, IOError):
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.005)
    except Exception:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        yield False
        return
    try:
        yield acquired
    finally:
        if acquired and handle is not None:
            try:
                _lock_byte(handle, unlock=True)
            except Exception:
                pass
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


def _load_state_locked():
    """Return a valid state dict, or None for corrupt/unreadable state."""
    if not os.path.exists(STATE):
        return {}
    try:
        state = json.load(io.open(STATE, encoding="utf-8"))
        if not isinstance(state, dict):
            return None
        float(state.get("last_fleet_ts", 0) or 0)
        sessions = state.get("sessions", {})
        if not isinstance(sessions, dict):
            return None
        for session, rec in sessions.items():
            if not isinstance(session, str) or not isinstance(rec, dict):
                return None
            reflex = rec.get("reflex", {})
            if not isinstance(reflex, dict):
                return None
            for rid, ts in reflex.items():
                if not isinstance(rid, str):
                    return None
                float(ts)
        return state
    except Exception:
        return None


def _save_state_locked(state):
    try:
        tmp = STATE + ".tmp-%d-%s" % (os.getpid(), uuid.uuid4().hex)
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(state, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, STATE)
        return True
    except Exception:
        return False


def _claim_budget(session, rid, now=None):
    """Atomically enforce caps and persist BEFORE deny. Corruption is preserved and fails open."""
    try:
        now = now or time.time()
        with _state_lock() as locked:
            if not locked:
                return False
            state = _load_state_locked()
            if state is None:
                return False
            sessions = state.get("sessions", {})
            # prune: drop sessions whose newest escalation is >24h old
            sessions = {
                key: value for key, value in sessions.items()
                if now - max([0.0] + list((value.get("reflex") or {}).values())) < 86400
            }
            if now - float(state.get("last_fleet_ts", 0) or 0) < FLEET_GAP_S:
                return False
            rec = sessions.get(session) or {"reflex": {}}
            if rid in (rec.get("reflex") or {}):
                return False                   # once per (session, reflex): retry passes
            if len(rec.get("reflex") or {}) >= SESSION_MAX:
                return False
            rec.setdefault("reflex", {})[rid] = now
            sessions[session] = rec
            return _save_state_locked({"sessions": sessions, "last_fleet_ts": now})
    except Exception:
        return False


def _reason(top, E, fail, count):
    ts, fsess, fcmd, fsig = fail
    age_m = int(max(0, time.time() - ts) / 60)
    sig = "; ".join(fsig) if isinstance(fsig, list) else str(fsig)
    same = "THIS session" if fsess and top.get("_sess") == fsess else ("session " + (fsess[:8] or "?"))
    # The marker leads the string. It sits at a FIXED position so a consumer can check provenance
    # by prefix; buried mid-sentence it was silently stripped by the Codex adapter, which meant
    # real heed denies never reached a Codex agent at all.
    return ("[heed-gate] HEED GATE - one-time stop, already disarmed for this scar this session. "
            "'%s' has surfaced %dx this session and been read past each time, and this command is "
            "evidence-linked (E=%.2f) to a command that ALREADY FAILED %d min ago in %s (%s): %s | "
            "The scar: %s (-> [[%s]]). Do ONE of: fix the command per the scar, or state in one line "
            "why it does not apply - then re-run. The re-run will NOT be blocked; this gate fires at "
            "most twice a session, so this is signal, not wallpaper."
            % (top.get("id"), count, E, age_m, same, sig or "hard failure", (fcmd or "")[:90],
               (top.get("gut") or "")[:160], top.get("source") or top.get("src") or "MISTAKES"))


def _log(session, rid, E, proxy, cmdtext, mode):
    try:
        snip = re.sub(r"sk-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----",
                      "<masked>", cmdtext or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")[:120]
        with io.open(LOG, "a", encoding="utf-8", newline="") as f:
            f.write("%.0f\t%s\t%s\t%.3f\t%s\t%s\t%s\n" % (time.time(), (session or "")[:12], rid, E, proxy, mode, snip))
    except Exception:
        pass


def _deposit(session, top):
    """Share the escalation with the conscious layer: deposit the scar hot + a REAL-failure danger bump
    (the escalation is failure-linked by construction - exactly the danger channel's intended trigger)."""
    try:
        import sys
        sys.path.insert(0, GFDIR)
        import gfcore
        f = {"id": top.get("id"), "charge": float(top.get("charge", 1.0)), "src": top.get("source") or top.get("src")}
        gfcore.note_fires(session, [f], extra_danger=0.6)
    except Exception:
        pass


def evaluate(event, data, fired, now=None):
    """The one entry point instinct_prime calls after _dedup/_render. Returns None (do nothing) or
    {"decision": "deny", "reason": str}. Fail-safe: ANY error -> None."""
    try:
        if event != "PreToolUse" or not fired:
            return None
        session = data.get("session_id", "") or ""
        if not re.match(r"^[0-9a-f]{8}-", session):
            return None                                    # real sessions only; fixtures never denied
        proxy = _act_proxy(event, data)
        if proxy not in ("act", "write"):
            return None                                    # condition 1: a real act, never a mention
        top = _top(fired)
        if not top or float(top.get("charge", 0)) < MIN_CHARGE:
            return None                                    # condition 2: only a maximal-burn scar
        if top.get("id") not in ACT_REFLEXES:
            return None                                    # condition 2b: only an ACT-hazard may deny
        count = int(top.get("_count", 1))
        if count < MIN_COUNT:
            return None                                    # condition 3: demonstrably read past already
        cmdtext = _cmd_text(data)
        if len(cmdtext) < 12:
            return None
        E, fail = evidence(cmdtext, session, now=now)
        if E < THETA_E or fail is None:
            return None                                    # condition 4: linked to a REAL recent failure
        if not _claim_budget(session, top.get("id", "?"), now=now):
            return None                                    # caps; persisted BEFORE the deny
        live = _enforcement_live()
        _log(session, top.get("id", "?"), E, proxy, cmdtext, "deny" if live else "shadow")
        _deposit(session, top)
        if not live:
            return None                                    # shadow: measured, not felt
        top["_sess"] = session
        return {"decision": "deny", "reason": _reason(top, E, fail, count)}
    except Exception:
        return None


def _selftest():
    ok = True
    base = {"session_id": "89f84043-0000-0000-0000-000000000000", "tool_name": "Bash",
            "tool_input": {"command": "python C:/x/y.py --run everything now with tokens matching nothing"}}
    hot = [{"id": "ps1_hook_edit", "charge": 1.0, "_count": 5, "gut": "g", "source": "MISTAKES"}]
    # no failure link -> None even with a hot repeated scar on an act
    r = evaluate("PreToolUse", base, list(hot))
    print("  [%s] act+hot+repeat but NO failure link -> %s (want None)" % ("ok" if r is None else "FAIL", r))
    ok &= r is None
    # mention (read-only) never escalates even with everything else
    rd = dict(base, tool_input={"command": "grep -n ps1_hook_edit ~/.claude/dreaming/instincts.tsv"})
    r = evaluate("PreToolUse", rd, list(hot))
    print("  [%s] read-only mention -> %s (want None)" % ("ok" if r is None else "FAIL", r))
    ok &= r is None
    # junk inputs must never throw
    for junk in (None, {}, {"tool_input": 5}, {"session_id": None}):
        try:
            evaluate("PreToolUse", junk or {}, hot)
            evaluate("Stop", base, hot)
            evaluate("PreToolUse", base, None)
        except Exception as e:
            print("  FAIL-SAFE BREACH:", e)
            ok = False
    print("heed_gate selftest", "OK (fail-safe held)" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        raise SystemExit(0)
    sys.exit(_selftest())
