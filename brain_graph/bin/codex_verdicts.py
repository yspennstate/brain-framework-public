"""His corrections, repeats and praise in Codex sessions.

events_verdicts.jsonl, the file the brain's correction studies and graph_store.build read, is built by
backfill_history.verdicts() from Claude transcripts alone, so none of the 112 corrections and repeats it held for
1-28 Sep came from a Codex session, while 357 of his 5,431 prompts in that time were typed in one. This writes the Codex
counterpart to its own file, events_verdicts_codex.jsonl, and leaves the live store alone: graph_store.build keeps
reading events_verdicts.jsonl only, until a Codex touch bridge exists to link a Codex verdict to what came before it
(the design the scheduled auditors were asked for, R25).

The test is the Claude one, applied to the same kind of input:
  - a prompt of his: a Codex row of the prompt ledger (memory/prompts/PROMPTS.jsonl) with origin "owner". The ledger
    reads the user messages of interactive rollouts only (session_meta source "cli"; spawned threads are skipped) and
    the live Codex prompt hook, and classes a prompt written by a script "automation";
  - the verdict: extract_touch_events' CORRECTION, PRAISE and REPEAT patterns on the first 600 characters of a prompt
    under 4,000 characters, exactly as X.his_verdicts does for Claude.
Row: {"ts": epoch, "session": the capture's key for the session, "kind": "verdict", "id": correction|praise|repeat,
      "runtime": "codex", "source": the ledger row's source, "session_id": the full id, "session_prefix": the ledger's
      12 characters, "resolved": history text|rollout text|unconfirmed|ambiguous|unknown}.
The session key is the one extract_codex_events gives the same session's touches: "x" plus the last seven hex of the
full id. The ledger keeps only the id's first 12 characters, and a Codex id is time-ordered, so its first 8 hex are the
same for every session started within about 65 s; until 28 Sep this file keyed by them, which merged 8 such groups of
his sessions and could never meet a touch. The
full id is taken only where the prompt's own text is found under it: in Codex's prompt history (~/.codex/history.jsonl,
full ids, written until 23 Sep) or inside that session's rollout. Otherwise the row is left unkeyed; a prefix is never
given to the first or the only id known for it.
The ledger's ts is local wall-clock time (checked 28 Sep against rollouts: 13:55:11 in the ledger = 10:55:11Z in the
rollout, and the same for the live hook's rows), so it is converted with the machine's zone (time.mktime).

    python codex_verdicts.py              write ~/.claude/brain_graph/events_verdicts_codex.jsonl from scratch
    python codex_verdicts.py --check 30   also compare 30 random rows' times with their rollouts and print the offsets
    python codex_verdicts.py --since 2026-09-01 --dry   count only
"""
import argparse
import collections
import glob
import io
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_touch_events as X  # noqa: E402

HOME = os.path.expanduser("~")
LEDGER = os.path.join(HOME, ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")), "memory", "prompts", "PROMPTS.jsonl")
OUT = os.path.join(HOME, ".claude", "brain_graph", "events_verdicts_codex.jsonl")
ROLLOUTS = os.path.join(HOME, ".codex", "sessions")
HISTORY = os.path.join(HOME, ".codex", "history.jsonl")
ROLLOUT_ID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")


def capture_key(full_id):
    """the key extract_codex_events gives a Codex session: "x" plus the last seven hex of its id ("" if not a full id)"""
    s = (full_id or "").replace("-", "")
    return "x" + s[-7:] if len(s) == 32 else ""


def _text_key(text):
    return " ".join((text or "").split())[:40]


def _rollout_key(text):
    """the start of a prompt as it appears verbatim inside a rollout's JSON (cut before the first character JSON escapes)"""
    t = (text or "").strip()[:60]
    for i, ch in enumerate(t):
        if ch in '"\\\n\r\t' or ord(ch) < 32:
            t = t[:i]
            break
    return t if len(t) >= 12 else ""


def _typed_texts(o):
    """the texts of a rollout record that is a message sent to the session: a UserMessage item, a user_message event, or
    a user-role message item (how a prompt with images was recorded on 5 Sep); nothing for developer context, the
    agent's replies or tool output"""
    p = o.get("payload") or {}
    if o.get("type") == "event_msg":
        if p.get("type") == "user_message":
            return [str(p.get("message") or "")]
        it = p.get("item") or {}
        if p.get("type") == "item_completed" and it.get("type") == "UserMessage":
            return [str(c.get("text") or "") for c in it.get("content") or [] if isinstance(c, dict)]
    if o.get("type") == "response_item" and p.get("type") == "message" and p.get("role") == "user":
        return [str(c.get("text") or "") for c in p.get("content") or [] if isinstance(c, dict)
                and c.get("type") in ("input_text", "text")]
    return []


def _in_rollout(path, text):
    """the prompt's opening opens a message sent to that session. Until 28 Sep any line counted: the prompts hook puts
    his earlier prompts into every session's developer context, and a worker's instruction can quote one, so a prompt
    typed in one session was also found in others"""
    key = _rollout_key(text)
    if not key:
        return False
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if key not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if any(t.lstrip().startswith(key) for t in _typed_texts(o)):
                    return True
    except OSError:
        return False
    return False


class Resolver:
    """Full Codex session ids for the ledger's 12-character prefixes. A session is accepted only on exact provenance:
    the prompt's own text in Codex's prompt history under that id, or inside that id's rollout. A prefix with one known
    id is not proof: the prompt's real session can be missing from both sources, and then the one known id is someone
    else's. Anything else is left unkeyed:
    "unconfirmed", "ambiguous" (two ids match) or "unknown" (no id has the prefix)."""

    def __init__(self, history=None, rollouts=None):
        self.by_prefix = collections.defaultdict(set)
        self.by_text = collections.defaultdict(set)
        self.rollout_path = {}
        try:
            with io.open(history or HISTORY, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    sid = str(o.get("session_id") or "")
                    if len(sid.replace("-", "")) == 32:
                        self.by_prefix[sid[:12]].add(sid)
                        self.by_text[(sid[:12], _text_key(o.get("text")))].add(sid)
        except OSError:
            pass
        for f in glob.glob(os.path.join(rollouts or ROLLOUTS, "*", "*", "*", "rollout-*.jsonl")):
            m = ROLLOUT_ID.search(f)
            if m:
                self.by_prefix[m.group(1)[:12]].add(m.group(1))
                self.rollout_path[m.group(1)] = f

    def full(self, prefix, text):
        p = (prefix or "")[:12]
        c = self.by_prefix.get(p) or set()
        if not c:
            return "", "unknown"
        t = self.by_text.get((p, _text_key(text))) or set()
        if len(t) == 1:
            return next(iter(t)), "history text"
        if len(t) > 1:
            return "", "ambiguous"
        hits = sorted(i for i in c if i in self.rollout_path and _in_rollout(self.rollout_path[i], text))
        if len(hits) == 1:
            return hits[0], "rollout text"
        return "", ("ambiguous" if hits else "unconfirmed")


def his_codex_rows(since=""):
    with io.open(LEDGER, encoding="utf-8") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            if "codex" not in (o.get("source") or "").lower() or o.get("origin") != "owner":
                continue
            if since and (o.get("ts") or "") < since:
                continue
            yield o


def verdicts_of(text):
    c = (text or "").strip()
    if not c or len(c) >= 4000:
        return []
    head = c[:600]
    return [v for v, rx in (("correction", X.CORRECTION), ("praise", X.PRAISE), ("repeat", X.REPEAT)) if rx.search(head)]


def epoch_local(ts):
    return time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))


def check_times(rows, n):
    """Offsets (ledger epoch - rollout epoch) for n random rows whose rollout is still on disk."""
    rows = [r for r in rows if len((r.get("text") or "").strip()) >= 12]
    random.Random(20260928).shuffle(rows)
    got = []
    for r in rows:
        if len(got) >= n:
            break
        sid = (r.get("session") or "")[:12]
        files = glob.glob(os.path.join(ROLLOUTS, "*", "*", "*", "rollout-*%s*.jsonl" % sid))
        if not files:
            continue
        key = (r.get("text") or "").strip()[:30]
        for f in files:
            with io.open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if key and key in line and '"role":"user"' in line.replace(" ", ""):
                        try:
                            ts = json.loads(line).get("timestamp") or ""
                        except Exception:
                            continue
                        roll = X.epoch(ts)
                        if roll:
                            got.append(epoch_local(r["ts"]) - roll)
                        break
            if len(got) and len(got) >= n:
                break
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--check", type=int, default=0)
    a = ap.parse_args()
    rows = list(his_codex_rows(a.since))
    res = Resolver()
    out, kinds, how, prompts_with = [], collections.Counter(), collections.Counter(), 0
    for r in rows:
        vs = verdicts_of(r.get("text"))
        if not vs:
            continue
        prompts_with += 1
        ts = epoch_local(r["ts"])
        prefix = (r.get("session") or "")[:12]
        full, h = res.full(prefix, r.get("text"))
        how[h] += 1
        for v in vs:
            kinds[v] += 1
            out.append({"ts": round(ts, 3), "session": capture_key(full), "kind": "verdict", "id": v, "runtime": "codex",
                        "source": r.get("source"), "session_id": full, "session_prefix": prefix, "resolved": h})
    out.sort(key=lambda d: d["ts"])
    print("his Codex prompts %d%s; with a verdict %d; %s; session ids: %s" % (
        len(rows), (" since " + a.since) if a.since else "", prompts_with, dict(kinds), dict(how)))
    if a.check:
        offs = check_times(rows, a.check)
        if offs:
            offs.sort()
            print("time check: %d rows; ledger minus rollout, seconds: min %.0f, median %.0f, max %.0f" % (
                len(offs), offs[0], offs[len(offs) // 2], offs[-1]))
        else:
            print("time check: no rollout found for the sampled rows")
    if a.dry:
        return 0
    new = OUT + ".new"
    with io.open(new, "w", encoding="utf-8", newline="\n") as fh:
        for d in out:
            fh.write(json.dumps(d) + "\n")
    if os.path.exists(OUT):
        from quarantine_rows import keep_old   # the previous file is brain data: keep a copy before replacing it
        keep_old(OUT)
    os.replace(new, OUT)
    print("wrote %d rows to %s" % (len(out), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
