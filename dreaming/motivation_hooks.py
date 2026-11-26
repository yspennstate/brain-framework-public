# -*- coding: utf-8 -*-
"""Motivation hooks: one short encouraging line for an agent at the moment it needs one: when it is stuck, thinks it
cannot do something, or has just been corrected.

The 1,000 lines are motivation_hooks.jsonl beside this file (ids M0001..M1000, 25 kinds of 40). A kind is chosen from a
measured signal, never at random:
- PostToolUseFailure: a failed call (tool_failed), three failures inside ten minutes (repeated_failure). A failure
  whose text is a refusal by a rule, a guard, a card or the user gets a rule_stopped line: those say the rule stands
  and point at the allowed road. No line ever tells an agent to push past a rule or to call something done.
- UserPromptSubmit: the user's prompt reads as a correction (after_correction), is a loop re-fire (loop_refire) or is long
  (big_ask); the agent's last message says it cannot (said_cannot), is stuck (said_stuck) or blames itself
  (self_blame); otherwise the work area named in the prompt (math, debugging, writing, films, sims, brain_building,
  code_build) or a general kind (praise, creative, honest_report, teamwork, long_haul, unfamiliar, finishing).
- SessionStart: session_start, or after_compaction when the source is a compaction.
ONE line per fire and a per-session cooldown (the brain fires less on purpose): ten minutes after a strong
signal, twenty after an ordinary failure, thirty between ordinary prompt lines, none in a session's first ten minutes
except the session-start line. A lone failed call is silent (a search that finds nothing also arrives as a failure):
the second failure inside ten minutes gets a tool_failed line, the third a repeated_failure line. Failures within five
seconds count as one, and a per-session lock keeps parallel failed calls from each printing a line. A refusal is read
only in the 200 characters around the words that matched, and an honest "could not verify" gets an honest_report
line, never a you-can-do-it one. On a prompt this program first runs the hourly piece (motivation_hourly.py, imported
from beside this file): when that speaks, it is the only line, so an agent never gets two at once and one process
serves both. Within a kind the line shown least recently in this session goes first; sessions start at different lines.
Claude Code: plain text through hook_dispatch.py. Codex: --codex prints JSON hookSpecificOutput. Every fire is logged
to ~/.claude/motivation_hooks/fires.jsonl. Silent on any error. A file ~/.claude/motivation_hooks/OFF silences it.
MOTIVATION_STATE and MOTIVATION_FIRES override the stores (tests). Other uses: --selftest, --status,
--pick <kind> (print one line of that kind; --pick list names the kinds)."""
import hashlib
import json
import os
import re
import sys
import time

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(HERE, "motivation_hooks.jsonl")
BASE = os.path.join(HOME, ".claude", "motivation_hooks")
STATE = os.environ.get("MOTIVATION_STATE") or os.path.join(BASE, "state")
FIRES = os.environ.get("MOTIVATION_FIRES") or os.path.join(BASE, "fires.jsonl")
OFF = os.path.join(BASE, "OFF")
CODEX = "--codex" in sys.argv

GAP_STRONG = 600        # after a strong signal (said cannot, stuck, self blame, correction, repeated failure, rule stop)
GAP_FAIL = 1200         # after an ordinary failed call
GAP_PROMPT = 1800       # between ordinary prompt lines
QUIET_START = 600       # no ordinary prompt line in a session's first ten minutes
FAIL_WINDOW = 600
FAIL_SAME_S = 5         # failed calls this close together are one failure (parallel calls)
GAP_AFTER_LINE = 90     # a repeated_failure line never follows another line by less than this
LONG_HAUL_S = 2 * 3600
NEAR = 200              # a refusal or a doubt is read only this close to the words that matched

DOUBT_RE = re.compile(
    r"(?:could ?n[o']t|couldn't|can ?not|can't|unable to|did not|didn't) (?:verify|confirm|reproduce|check|read|"
    r"rule out|measure|test)|DATA INCOMPLETE|\bnot (?:verified|confirmed|tested|measured|read)\b|\bunverified\b|"
    r"\bnot sure\b|\buncertain\b", re.I)
BARE_EXIT_RE = re.compile(r"\s*(?:Error:\s*)?(?:Exit code|exit status) 1\s*", re.I)

REFUSAL_RE = re.compile(
    r"refus|denied|\bdeny\b|not allowed|forbidden|blocked|\bguard\b|\bbanned?\b|\bhis call\b|"
    r"needs? (?:his|a) (?:word|card|approval)|approval card|security card|permission|must not|"
    r"\bnever (?:run|start|ssh|use|touch|send)\b|rejected|declined|doesn't want|does not want|\blaw\b", re.I)
CANNOT_RE = re.compile(
    r"\bi (?:can ?not|can't|am unable to|could ?n[o']t|couldn't)\b|\bnot possible\b|\bimpossible\b|"
    r"\bthere is no way\b|\bno way to\b|\bunable to\b", re.I)
STUCK_RE = re.compile(
    r"\bstuck\b|going in circles|not converging|\bno progress\b|out of ideas|\bdead end\b|keeps? failing|"
    r"still failing|still fails|i (?:do not|don't) know (?:how|why)", re.I)
BLAME_RE = re.compile(
    r"\bmy (?:mistake|error|fault)\b|\bi was wrong\b|\bi got (?:it|this|that) wrong\b|\bi should have\b|"
    r"\bi made an? (?:mistake|error)\b|\bi apologi[sz]e\b|\bi(?:'m| am) sorry\b|\bi missed\b", re.I)
CORRECTION_RE = re.compile(
    r"\bwrong\b|not what i (?:asked|said|meant|wanted)|i never (?:said|told|asked)|\bwtf\b|"
    r"what (?:are|do) you (?:doing|talking|mean)|i (?:already )?(?:told|asked) you|\bhorrible\b|\bcrap\b|"
    r"enough is enough|why did you|for the love of|\bstop (?:doing|with)\b", re.I)
LOOP_RE = re.compile(r"fire this every|\(Loop started", re.I)
FINISH_RE = re.compile(r"This was (?:your|his) \d|final report|nearly done|almost done|last step|wrapping up", re.I)
UNFAMILIAR_RE = re.compile(r"unfamiliar|not familiar|first time (?:i|we)|never used|new to (?:me|this)", re.I)
AREAS = (
    ("math", re.compile(r"\b(?:theorem|lemma|proof|prove|conjecture|open problem|inequalit|corollary|math)\w*", re.I)),
    ("sims", re.compile(r"\b(?:simulation|backtest|sweep|plateau|lanes?|sqqq|uvxy|drawdown|cagr)\w*", re.I)),
    ("films", re.compile(r"\b(?:film|movie|shorts?|youtube|clip|render|narration|veo)\w*", re.I)),
    ("writing", re.compile(r"\b(?:paper|draft|abstract|manuscript|cover letter|research statement|essay|chapter)\w*", re.I)),
    ("brain_building", re.compile(r"\b(?:brain|memory|memories|recall|reflex|instinct)\w*", re.I)),
    ("debugging", re.compile(r"\b(?:bug|traceback|debug|exception|crash|not working|broken)\w*", re.I)),
    ("code_build", re.compile(r"\b(?:script|program|implement|build|code|hook|install|deploy)\w*", re.I)),
)
GENERAL = ("praise", "creative", "honest_report", "teamwork")
STRONG = ("said_cannot", "said_stuck", "self_blame", "after_correction", "repeated_failure", "rule_stopped")


def load_catalog(path=None):
    by_kind = {}
    try:
        with open(path or CATALOG, "r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                r = json.loads(raw)
                by_kind.setdefault(r["kind"], []).append(r)
    except (OSError, ValueError, KeyError):
        return {}
    return by_kind


def state_path(sid, suffix=".json"):
    os.makedirs(STATE, exist_ok=True)
    return os.path.join(STATE, re.sub(r"[^A-Za-z0-9_.-]", "_", sid or "unknown")[:80] + suffix)


def load_state(sid):
    try:
        with open(state_path(sid), "r", encoding="utf-8") as fh:
            st = json.load(fh)
            return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(sid, st):
    p = state_path(sid)
    tmp = "%s.%d.tmp" % (p, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        os.replace(tmp, p)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def hourly_module():
    """motivation_hourly.py from beside this file, with this run's stores (None when it cannot be loaded)."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("motivation_hourly", os.path.join(HERE, "motivation_hourly.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.STATE, mod.FIRES, mod.OFF, mod.CODEX = STATE, FIRES, OFF, CODEX
        return mod
    except Exception:
        return None


class SessionLock(object):
    """One hook process at a time per session: parallel failed calls each start a process, and without this each would
    read the same old state and print a line. Waits up to 1.5 s; a lock older than 15 s is taken over."""

    def __init__(self, sid):
        self.path = state_path(sid, ".lock")
        self.held = False

    def __enter__(self):
        for _ in range(30):
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                self.held = True
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > 15:
                        os.remove(self.path)
                        continue
                except OSError:
                    pass
                time.sleep(0.05)
            except OSError:
                return self
        return self

    def __exit__(self, *exc):
        if self.held:
            try:
                os.remove(self.path)
            except OSError:
                pass
        return False


def near(rx, text, m):
    """Does rx match within NEAR characters of the match m in text?"""
    return bool(rx.search(text[max(0, m.start() - NEAR):m.end() + NEAR]))


def _assistant_text(line):
    """The text of one transcript line when it is an agent message that carries text, else ''."""
    if b'"assistant"' not in line:
        return ""
    try:
        rec = json.loads(line.decode("utf-8", "replace"))
    except ValueError:
        return ""
    if not isinstance(rec, dict) or rec.get("type") != "assistant":
        return ""
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return content[-2000:]
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p)[-2000:]
    return ""


def last_assistant_text(path, max_bytes=8000000, chunk=1000000):
    """The text of the agent's last message in a Claude Code transcript (best effort; '' when unreadable).
    Read backward in chunks: measured 22 Nov 2026, build 2.1.330 writes request records of up to 250 KB between
    messages, and the newest message with text sat 3.5 MB from the end of a transcript (a fixed 160 KB tail held none)."""
    if not path:
        return ""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            pos, tail, done = size, b"", 0
            while pos > 0 and done < max_bytes:
                step = min(chunk, pos)
                pos -= step
                fh.seek(pos)
                buf = fh.read(step) + tail
                done += step
                lines = buf.split(b"\n")
                tail = lines[0] if pos > 0 else b""        # the first piece may be half a line: keep it for the next chunk
                for line in reversed(lines[1:] if pos > 0 else lines):
                    text = _assistant_text(line)
                    if text:
                        return text
    except OSError:
        return ""
    return ""


def error_text(data):
    resp = data.get("error") or data.get("tool_response") or ""
    if not isinstance(resp, str):
        try:
            resp = json.dumps(resp)
        except (TypeError, ValueError):
            resp = str(resp)
    return resp[:6000]


def decide(data, st, now, sid, last_text=None):
    """Return (kind, gap_class) or (None, None). gap_class: 'strong', 'fail', 'prompt', 'start'."""
    ev = data.get("hook_event_name") or ""
    last_fire = st.get("last_fire") or 0
    first_seen = st.get("first_seen") or now
    if ev == "SessionStart":
        if now - (st.get("last_start") or 0) < 600:
            return None, None
        return ("after_compaction" if (data.get("source") or "") == "compact" else "session_start"), "start"
    if ev == "PostToolUseFailure":
        if data.get("is_interrupt"):
            return None, None
        err = error_text(data)
        if REFUSAL_RE.search(err[:800]):        # a rule said no: never counted as a stuck failure
            return ("rule_stopped", "strong") if now - last_fire >= GAP_STRONG else (None, None)
        if BARE_EXIT_RE.fullmatch(err):         # exit 1 and nothing else: a search that found nothing
            return None, None
        fails = [t for t in (st.get("fails") or []) if now - t < FAIL_WINDOW]
        if fails and now - fails[-1] < FAIL_SAME_S:
            return None, None                   # the same moment as the last failure: one failure
        fails.append(now)
        st["fails"] = fails[-12:]
        if len(fails) >= 3:
            if now - (st.get("last_repeat") or 0) >= GAP_STRONG and now - last_fire >= GAP_AFTER_LINE:
                st["last_repeat"] = now
                return "repeated_failure", "strong"
            return None, None
        if len(fails) == 2 and now - last_fire >= GAP_FAIL:
            return "tool_failed", "fail"
        return None, None
    if ev != "UserPromptSubmit":
        return None, None
    prompt = data.get("prompt") or ""
    if not isinstance(prompt, str):
        prompt = str(prompt)
    head = prompt[:4000]
    is_loop = bool(LOOP_RE.search(head))
    if last_text is None:
        last_text = ""
    if now - last_fire >= GAP_STRONG:
        if not is_loop and len(head) < 1500 and CORRECTION_RE.search(head):
            return "after_correction", "strong"
        if last_text:
            m = CANNOT_RE.search(last_text)
            if m:
                if near(DOUBT_RE, last_text, m):        # an honest doubt is praised, never argued with
                    return "honest_report", "strong"
                return ("rule_stopped" if near(REFUSAL_RE, last_text, m) else "said_cannot"), "strong"
            m = STUCK_RE.search(last_text)
            if m:
                return ("rule_stopped" if near(REFUSAL_RE, last_text, m) else "said_stuck"), "strong"
            if BLAME_RE.search(last_text):
                return "self_blame", "strong"
    if now - last_fire < GAP_PROMPT or now - first_seen < QUIET_START:
        return None, None
    pool = []
    if is_loop:
        pool.append("loop_refire")
    elif len(prompt) > 600:
        pool.append("big_ask")
    for kind, rx in AREAS:
        if rx.search(head):
            pool.append(kind)
            break
    if last_text and FINISH_RE.search(last_text):
        pool.append("finishing")
    if last_text and UNFAMILIAR_RE.search(last_text):
        pool.append("unfamiliar")
    if now - first_seen >= LONG_HAUL_S:
        pool.append("long_haul")
    pool.extend(GENERAL)
    kind_last = st.get("kind_last") or {}
    pool.sort(key=lambda k: (kind_last.get(k) or 0))      # the kind shown least recently in this session goes first
    return pool[0], "prompt"


def choose_line(by_kind, kind, st, sid):
    rows = by_kind.get(kind) or []
    if not rows:
        return None
    shown = st.get("shown") or {}

    def key(r):
        return (shown.get(r["id"]) or 0, hashlib.sha1((sid + r["id"]).encode("utf-8", "replace")).hexdigest())
    return min(rows, key=key)


def run(data, now=None, by_kind=None, last_text=None):
    """One hook call. Returns the text to deliver ('' for silence). Writes state and the fire log."""
    now = now or time.time()
    if os.path.exists(OFF):
        return ""
    ev = data.get("hook_event_name") or ""
    if ev not in ("UserPromptSubmit", "PostToolUseFailure", "SessionStart"):
        return ""
    sid = data.get("session_id") or data.get("conversation_id") or ""
    with SessionLock(sid) as lock:
        if not lock.held:
            return ""                       # another process of this session is deciding: one line, not two
        st = load_state(sid)
        st.setdefault("first_seen", now)
        if ev == "UserPromptSubmit":
            hourly = hourly_module()
            piece = ""
            if hourly is not None:
                try:
                    piece = hourly.run(data, now)
                except Exception:
                    piece = ""
            if piece:                       # the hourly piece is this prompt's only line
                st["last_fire"] = now
                save_state(sid, st)
                return piece
            if last_text is None and now - (st.get("last_fire") or 0) >= GAP_STRONG:
                last_text = last_assistant_text(data.get("transcript_path") or "")   # only when a line could fire
        kind, cls = decide(data, st, now, sid, last_text)
        row = None
        if kind:
            if by_kind is None:
                by_kind = load_catalog()
            row = choose_line(by_kind, kind, st, sid)
        if not row:
            save_state(sid, st)
            return ""
        st["last_fire"] = now
        if cls == "start":
            st["last_start"] = now
        st.setdefault("shown", {})[row["id"]] = now
        st.setdefault("kind_last", {})[kind] = now
        save_state(sid, st)
    try:
        os.makedirs(os.path.dirname(FIRES), exist_ok=True)
        with open(FIRES, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                                 "runtime": "codex" if CODEX else "claude", "session_id": sid, "event": ev,
                                 "hook": row["id"], "kind": kind}) + "\n")
    except OSError:
        pass
    return "[motivation %s | %s] %s" % (row["id"], kind, row["text"])


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    except AttributeError:      # in-process under hook_dispatch the stdin is a StringIO, already text
        raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return
    if not isinstance(data, dict):
        return
    if os.path.exists(os.path.join(BASE, "CAPTURE")):      # wiring check: which fields a real payload carries (names only)
        try:
            with open(os.path.join(BASE, "capture.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": time.strftime("%H:%M:%S"), "event": data.get("hook_event_name"),
                                     "keys": sorted(data.keys()), "is_interrupt": data.get("is_interrupt"),
                                     "error_chars": len(error_text(data))}) + "\n")
        except OSError:
            pass
    text = run(data)
    if not text:
        return
    if CODEX:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": data.get("hook_event_name") or "UserPromptSubmit",
                                                 "additionalContext": text}}))
    else:
        print(text)


def status():
    n = 0
    kinds = {}
    sessions = set()
    last = ""
    try:
        with open(FIRES, "r", encoding="utf-8") as fh:
            for raw in fh:
                try:
                    r = json.loads(raw)
                except ValueError:
                    continue
                n += 1
                kinds[r.get("kind")] = kinds.get(r.get("kind"), 0) + 1
                sessions.add(r.get("session_id"))
                last = r.get("at") or last
    except OSError:
        pass
    by_kind = load_catalog()
    print("catalog: %d lines in %d kinds (%s)" % (sum(len(v) for v in by_kind.values()), len(by_kind), CATALOG))
    print("fires: %d in %d session(s), last %s; switch-off file %s: %s"
          % (n, len(sessions), last or "none", OFF, "PRESENT (silent)" if os.path.exists(OFF) else "absent"))
    for k in sorted(kinds, key=lambda k: -kinds[k]):
        print("  %-18s %d" % (k, kinds[k]))


def selftest():
    import tempfile
    global STATE, FIRES, OFF, CODEX
    keep = (STATE, FIRES, OFF)
    tmp = tempfile.mkdtemp(prefix="motivation_selftest_")
    STATE, FIRES, OFF = os.path.join(tmp, "state"), os.path.join(tmp, "fires.jsonl"), os.path.join(tmp, "OFF")
    by_kind = load_catalog()
    results = []

    def check(name, ok):
        results.append((name, bool(ok)))

    def kind_of(text):
        m = re.match(r"\[motivation (M\d{4}) \| (\w+)\] ", text or "")
        return m.group(2) if m else None

    t0 = 1000000.0
    total = sum(len(v) for v in by_kind.values())
    check("catalog holds 1,000 lines in 25 kinds of 40", total == 1000 and len(by_kind) == 25
          and all(len(v) == 40 for v in by_kind.values()))
    fail = {"hook_event_name": "PostToolUseFailure", "session_id": "s1", "tool_name": "Bash", "error": "exit code 1: no such file"}
    check("a lone failed call is silent", run(dict(fail), t0, by_kind) == "")
    check("a failure two seconds later is the same failure: silent", run(dict(fail), t0 + 2, by_kind) == "")
    a = run(dict(fail), t0 + 30, by_kind)
    check("the second failure inside ten minutes gets a tool_failed line", kind_of(a) == "tool_failed")
    check("the line is one line under 200 chars", a and "\n" not in a and len(a) < 200)
    check("a third failure right after that line is silent", run(dict(fail), t0 + 60, by_kind) == "")
    c = run(dict(fail), t0 + 130, by_kind)
    check("the next failure, ninety seconds after the line, gets repeated_failure", kind_of(c) == "repeated_failure")
    check("further failures inside ten minutes are silent", run(dict(fail), t0 + 300, by_kind) == "")
    bare = {"hook_event_name": "PostToolUseFailure", "session_id": "s2", "tool_name": "Bash", "error": "Exit code 1"}
    check("exit 1 with no text (a search that found nothing) never fires",
          [run(dict(bare), t0 + n * 30, by_kind) for n in range(5)] == [""] * 5)
    f2 = dict(fail, session_id="s2b")
    run(dict(f2), t0, by_kind)
    run(dict(f2), t0 + 700, by_kind)
    check("two failures more than ten minutes apart are two lone failures: silent",
          not os.path.exists(FIRES) or '"s2b"' not in open(FIRES, encoding="utf-8").read())
    r = run({"hook_event_name": "PostToolUseFailure", "session_id": "s3",
             "error": "write_guard: refused, this file needs an approval card"}, t0, by_kind)
    check("a refusal by a guard gets rule_stopped, never tool_failed", kind_of(r) == "rule_stopped")
    check("an interrupted call is silent", run({"hook_event_name": "PostToolUseFailure", "session_id": "s3b",
                                                 "error": "x", "is_interrupt": True}, t0, by_kind) == "")
    p = {"hook_event_name": "UserPromptSubmit", "session_id": "s4", "prompt": "please check the file list"}
    check("a plain prompt in a fresh session is silent", run(dict(p), t0, by_kind, last_text="Here is the list.") == "")
    d = run(dict(p), t0 + 5, by_kind, last_text="I cannot find a way to parse this file.")
    check("an agent saying it cannot gets said_cannot", kind_of(d) == "said_cannot")
    e = run(dict(p, session_id="s5"), t0, by_kind, last_text="I cannot do this: the guard refused it and it needs an approval card.")
    check("cannot because a rule refused gets rule_stopped, not said_cannot", kind_of(e) == "rule_stopped")
    far = "The card K7 was installed and the guard is fine. " + "The rows were read one by one. " * 14 + "I cannot find a way to parse this file."
    check("a card or guard named far from the cannot does not turn it into rule_stopped",
          kind_of(run(dict(p, session_id="s5b"), t0, by_kind, last_text=far)) == "said_cannot")
    doubt = run(dict(p, session_id="s5c"), t0, by_kind, last_text="I could not verify the row counts, so this is DATA INCOMPLETE.")
    check("an honest could-not-verify gets honest_report, never said_cannot", kind_of(doubt) == "honest_report")
    g = run(dict(p, session_id="s6"), t0, by_kind, last_text="I am stuck; the build keeps failing.")
    check("an agent saying it is stuck gets said_stuck", kind_of(g) == "said_stuck")
    h = run(dict(p, session_id="s7"), t0, by_kind, last_text="That was my mistake, I should have sized it.")
    check("self blame gets self_blame", kind_of(h) == "self_blame")
    i = run(dict(p, session_id="s8", prompt="no that is wrong, i never said to flatten"), t0, by_kind, last_text="Done.")
    check("a correction from the user gets after_correction", kind_of(i) == "after_correction")
    j = run(dict(p, session_id="s9", prompt="keep going on the lemma and the proof"), t0 + 700, by_kind, last_text="ok")
    check("an ordinary prompt before the session is ten minutes old is silent", j == "")
    run(dict(p, session_id="s10"), t0, by_kind, last_text="ok")
    k = run(dict(p, session_id="s10", prompt="keep going on the lemma and the proof"), t0 + 700, by_kind, last_text="ok")
    check("an ordinary prompt ten minutes in gets one line from the area or a general kind",
          kind_of(k) in ("math",) + GENERAL)
    k2 = run(dict(p, session_id="s10", prompt="keep going on the lemma and the proof"), t0 + 1000, by_kind, last_text="ok")
    check("and the next ordinary prompt inside thirty minutes is silent", k2 == "")
    h1 = run(dict(p, session_id="s10"), t0 + 1300, by_kind, last_text="I cannot find a way to do it.")
    check("twenty minutes in, the hourly piece is the prompt's only line", h1.startswith("[motivation hourly H") and "\n" not in h1)
    check("and the prompt right after an hourly piece is silent",
          run(dict(p, session_id="s10"), t0 + 1360, by_kind, last_text="I cannot find a way to do it.") == "")
    seen, hourly_n = set(), 0
    tt = t0 + 5000
    for _ in range(14):
        tt += 1900
        out = run(dict(p, session_id="s10", prompt="keep going on the lemma and the proof"), tt, by_kind, last_text="ok")
        hourly_n += out.startswith("[motivation hourly")
        seen.add(kind_of(out))
    check("ordinary lines rotate across kinds, with hourly pieces between them", len(seen - {None}) >= 4 and hourly_n >= 5)
    s = run({"hook_event_name": "SessionStart", "session_id": "s11", "source": "compact"}, t0, by_kind)
    check("a compaction gets after_compaction", kind_of(s) == "after_compaction")
    s2 = run({"hook_event_name": "SessionStart", "session_id": "s12", "source": "startup"}, t0, by_kind)
    check("a new session gets session_start", kind_of(s2) == "session_start")
    import threading
    outs = []
    burst = {"hook_event_name": "PostToolUseFailure", "session_id": "s13", "error": "refused by the guard"}
    ths = [threading.Thread(target=lambda: outs.append(run(dict(burst), t0, by_kind))) for _ in range(6)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    check("six failed calls at the same moment print one line, not six", sum(1 for o in outs if o) == 1)
    tr = os.path.join(tmp, "transcript.jsonl")
    with open(tr, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "message": {"content": "go"}}) + "\n")
        fh.write(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "x"}, {"type": "text", "text": "I am stuck on this build."}]}}) + "\n")
        fh.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}}) + "\n")
    check("the agent's last words are read from a transcript file",
          kind_of(run(dict(p, session_id="s13b", transcript_path=tr), t0, by_kind)) == "said_stuck")
    ids = set()
    tt = t0
    for _ in range(40):
        tt += 700
        out = run({"hook_event_name": "PostToolUseFailure", "session_id": "s14", "error": "refused by the guard"}, tt, by_kind)
        m = re.match(r"\[motivation (M\d{4})", out or "")
        if m:
            ids.add(m.group(1))
    check("forty fires of one kind show forty different lines", len(ids) == 40)
    first = set()
    for n in range(12):
        out = run({"hook_event_name": "SessionStart", "session_id": "v%d" % n, "source": "startup"}, t0, by_kind)
        first.add(out)
    check("sessions start at different lines", len(first) >= 6)
    open(OFF, "w").close()
    check("the OFF file silences it", run(dict(fail, session_id="s15"), t0, by_kind) == "")
    os.remove(OFF)
    banned = re.compile(r"push through|ignore the rule|bypass|override|whatever it takes|just say done", re.I)
    check("no line tells an agent to push past a rule", not any(banned.search(r["text"]) for v in by_kind.values() for r in v))
    check("rule_stopped lines never say retry or find a way around",
          not any(re.search(r"\bretry\b|way around|get around", r["text"], re.I) for r in by_kind.get("rule_stopped", [])))
    try:
        n_log = sum(1 for _ in open(FIRES, encoding="utf-8"))
    except OSError:
        n_log = 0
    check("every fire is logged", n_log > 50)
    STATE, FIRES, OFF = keep
    bad = [n for n, ok in results if not ok]
    for n, ok in results:
        print("%s  %s" % ("PASS" if ok else "FAIL", n))
    print("SELFTEST %s: %d of %d" % ("PASS" if not bad else "FAIL", len(results) - len(bad), len(results)))
    try:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass
    return 0 if not bad else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--status" in sys.argv:
        status()
        sys.exit(0)
    if "--pick" in sys.argv:
        cat = load_catalog()
        want = sys.argv[sys.argv.index("--pick") + 1] if len(sys.argv) > sys.argv.index("--pick") + 1 else "list"
        if want not in cat:
            print("kinds: " + ", ".join(sorted(cat)))
            sys.exit(0 if want == "list" else 2)
        rows = cat[want]
        row = rows[int(time.time()) % len(rows)]
        print("[motivation %s | %s] %s" % (row["id"], want, row["text"]))
        sys.exit(0)
    try:
        main()
    except Exception:
        pass
