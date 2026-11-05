#!/usr/bin/env python3
"""prompt_origin.py - decide who authored a prompt, and whether it is a repeat of an earlier one.

WHY THIS EXISTS (measured 2026-08-22)
prompt_archive.py's record builder writes ts/session/cwd/source/class_provisional/redacted/text
and nothing else. It never wrote `origin`. So `origin` sat on 11,628 of 18,626 ledger records and
was ABSENT on 6,997 - every one of them a live-hook record from 2026-07 (1,331) and 2026-08
(5,667). build_prompt_views.py branches on `origin == "automation"`, so that branch was dead code
across the whole recent period, and nothing could separate a prompt the owner typed from the same
prompt re-fired by a /loop cron job.

The damage lands exactly where he looks. He routinely tells an agent to "read my last 100 prompts".
In the brain-topic slice: 2026-04 was 51 records / 51 distinct, 2026-06 29/28, 2026-07 124/107,
and 2026-08 231 records / 91 distinct (39.4%) - three loop mandates alone accounting for 131 of
them (x66, x40, x25). A single 2026-08-21 /loop order appears as 40 records. So "the last 100
prompts" had quietly become about 40 real asks and 60 machine echoes.

THE KEY (chosen by measurement, not taste - 18,629 records, referee = full-text similarity >= 0.90)
  prefix-300   19.0%  false merges  - REJECTED. Fanned-out agent prompts share a boilerplate
                                      header ("You are auditing a numbered mathematical argument.
                                      CLAIM: ...") and differ only in the payload.
  exact         0.00% false merges  - safe, but leaves one owner ask showing as 9 records because
                                      each /loop tick rewrites a trailing state note.
  tailstrip     2.52% false merges  - strips a trailing (...) / [...] block, balanced for nesting
                                      (the notes contain their own parentheses).
  HYBRID        0.45% false merges  - exact for ordinary prompts, tailstrip for loop-shaped ones.
                                      All 12 flagged pairs are the SAME ask with different agent
                                      state notes, i.e. the referee is wrong there, not the key.
Hybrid takes the 2026-08-21 loop group from 40 records to 2 distinct asks (exact leaves 9), and
loop-shaped records ledger-wide from 342 to 90.

FIELDS WRITTEN
  origin     "owner"      the owner typed it
             "automation" agent-authored loop scaffolding - a tick header this fleet wrote itself
  repeat_of  ts of the first sighting of this same ask (absent on the first occurrence)
  repeat_n   2,3,4... on repeats (absent on the first occurrence)
Distinct owner asks = records with origin "owner" and no `repeat_of`.

The live hook must stay O(1) and fail-open, so recent keys live in a small sidecar rather than a
scan of the 66MB ledger.
"""
import os, io, json, re, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
RECENT_KEEP = 800

# Agent-authored scaffolding: a loop tick rewrites the owner's order into a mandate header carrying
# its own operating instructions. Those words are the fleet's, not his, and must never be read back
# as an owner ask.
_AGENT_AUTHORED = re.compile(
    r"^\s*(OVERNIGHT\b.{0,60}\bMANDATE\b"
    r"|CONTINUE THE \d+-HOUR\b"
    r"|\[?(SYSTEM|AUTO|SCHEDULED)[- ]?(NOTIFICATION|TICK|FIRE)\b"
    # bank grader prompts: the acceptance arms call `claude -p` on this machine, and the
    # CLI's UserPromptSubmit hook archived each call as if he had typed it (39 of 113
    # owner rows between 00:45 and 03:4x on 2026-08-29)
    r"|You are auditing a numbered mathematical argument\b"
    r"|You are grading a mathematical argument from its text alone\b"
    # this fleet's own hook-delivery probes (`claude -p` with a prompt that says so), 2026-09-20
    r"|\[probe\]|Automated delivery probe of a hook\b"
    # headless picture workers run one `codex exec` per batch and the Codex hook archived each batch prompt as his
    # (142 ledger lines and a checklist entry per batch by 05:1x on 2026-09-23)
    # (any count and film: "for a history documentary", and since 09-23 16:50 "for three history documentaries")
    # (and since 09-23 17:27 "You are THE picture maker for a cinematic ... documentary", which the "a" form missed)
    # (2026-09-24: and "You are a picture maker for YouTube thumbnails", which no longer names a documentary - it
    # landed in the hourly block as his ask, re-fired x3; any picture-maker brief is a worker's, whatever it makes)
    r"|\[headless-worker\]|You are (?:a|the) picture maker\b"
    # the harness's own continuation turn when a usage limit resets, sent to every waiting session in the same
    # minute; the dream re-stamped each earlier burst by hand (08-19..09-17) and the 09-23 14:50 one landed as six
    # of his asks and six checklist entries
    r"|Your claude\.ai usage limit has reset\b"
    # a Codex hook's own prompt, and a relayed agent message, arrive as user turns and are not his (2026-09-20)
    r"|<hook_prompt\b|<agent-message\b|Another Claude session sent a message\b"
    # harness-injected user turns: a mesh message relayed into the session, the plugin list, notifications (2026-09-20)
    r"|<cross-session-message\b|<recommended_plugins\b|<task-notification\b|<system-reminder\b|<local-command-stdout\b|<agent-notification\b"
    # Codex's interruption marker, Claude's, and a supervisor's resume envelope typed into a saved thread (2026-09-24)
    r"|<turn_aborted\b|\[Request interrupted by user|FLEET CONTINUITY ENVELOPE\b"
    # the restore agent re-typing each session's goal after a restart: his ask is already in the ledger at the time he
    # gave it, and seven of these rows read as fresh asks of 02:53-03:0x on 2026-09-24 (a /goal he types stays his)
    r"|/goal \[Restored\b"
    # the Codex app's own suggestion task, run in its install directory (30 Sep 03:01 it landed in the ledger and the hourly
    # block as his ask, and a verification card answered it)
    r"|#\s*Overview\s+Generate \d+ to \d+ hyperpersonalized suggestions\b)", re.I)
# the Codex desktop app runs its own background tasks with its install directory as the working directory
_CODEX_APP_CWD = re.compile(r"[\\/]AppData[\\/]Local[\\/]OpenAI[\\/]Codex[\\/]bin(?:[\\/]|$)", re.I)
# the same grader prompts end with a JSON-only instruction; a body match catches variants
_GRADER_BODY = re.compile(r"\bReturn ONLY a JSON object\b", re.I)
_TICK_BODY = re.compile(r"\bEACH TICK:\s", re.I)

# Loop-shaped: re-fired verbatim by cron, with only a trailing state note mutating between ticks.
_LOOPISH = re.compile(r"^\s*(/loop\b|/dream\b|OVERNIGHT\b.{0,60}\bMANDATE\b|CONTINUE THE \d+-HOUR\b)", re.I)


def is_agent_authored(text):
    return bool(_AGENT_AUTHORED.match(text or "") or _TICK_BODY.search(text or "")
                or _GRADER_BODY.search(text or ""))


def machine_turn(text, cwd="", session=""):
    """Whether a user-shaped turn was written by a machine: agent-authored text, a harness's synthetic session id, or the
    Codex app's own background task (its install directory as the working directory). The archive stamps these
    'automation'; hooks that answer his prompts use the same test so a machine turn gets no answer meant for him."""
    return bool(is_agent_authored(text) or _synthetic_session(session) or _CODEX_APP_CWD.search(cwd or ""))


def is_loopish(text):
    return bool(_LOOPISH.match(text or "") or _TICK_BODY.search(text or ""))


def _base(text):
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _strip_tail_block(s):
    """Drop ONE trailing (...) or [...] block, balanced - the state notes nest their own parens,
    and an unbalanced regex silently left 9 'distinct' copies of one ask."""
    s = s.rstrip()
    if not s or s[-1] not in ")]":
        return s
    close = s[-1]
    openc = "(" if close == ")" else "["
    depth = 0
    for i in range(len(s) - 1, -1, -1):
        if s[i] == close:
            depth += 1
        elif s[i] == openc:
            depth -= 1
            if depth == 0:
                return s[:i].rstrip()
    return s


def normkey(text):
    """Hybrid key: exact for ordinary prompts, tail-stripped for loop-shaped ones."""
    b = _base(text)
    if is_loopish(text):
        for _ in range(3):
            n = _strip_tail_block(b)
            if n == b or len(n) < 16:
                break
            b = n
    return hashlib.md5(b.encode("utf-8")).hexdigest()


def classify(text, seen):
    """(origin, repeat_of, repeat_n). `seen` maps key -> [first_ts, count]; the caller updates it."""
    origin = "automation" if is_agent_authored(text) else "owner"
    hit = seen.get(normkey(text))
    if hit:
        return origin, hit[0], hit[1] + 1
    return origin, None, None


# ---------- sidecar used by the live hook ----------
def _recent_path():
    return os.environ.get("PROMPT_ORIGIN_RECENT") or os.path.join(HERE, ".prompt_recent.json")


def load_recent():
    try:
        with io.open(_recent_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d.get("seen", {}), d.get("order", [])
    except Exception:
        return {}, []


def save_recent(seen, order):
    """Atomic. A crash mid-write must not leave a truncated sidecar behind."""
    try:
        while len(order) > RECENT_KEEP:
            seen.pop(order.pop(0), None)
        p = _recent_path()
        tmp = p + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"seen": seen, "order": order}, f)
        os.replace(tmp, p)
    except Exception:
        pass


_UUID_RX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{3,4}$|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _synthetic_session(sess):
    s = str(sess or "")
    return bool(s) and not _UUID_RX.match(s)


def stamp(rec):
    """Add origin / repeat_of / repeat_n to a live record. Never raises - archiving is fail-open."""
    try:
        text = rec.get("text", "")
        seen, order = load_recent()
        origin, rof, rn = classify(text, seen)
        # the ledger keeps the first 12 chars of a real Claude or Codex session id (8 hex, dash, 3 hex); a harness replaying a prompt through the hooks
        # (the dispatcher control gate ctlA1-/ctlA2-/ctlB-, timing probes, delivery probes) is not him, whatever
        # the text says - 33 "check the machine speed" rows became a standing ask in the hourly block (2026-09-20)
        if _synthetic_session(rec.get("session")) or _CODEX_APP_CWD.search(str(rec.get("cwd") or "")):
            origin = "automation"
        rec["origin"] = origin
        if rof:
            rec["repeat_of"] = rof
            rec["repeat_n"] = rn
        k = normkey(text)
        if k in seen:
            seen[k][1] += 1
        else:
            seen[k] = [rec.get("ts") or "", 1]
            order.append(k)
        save_recent(seen, order)
    except Exception:
        pass
    return rec
