#!/usr/bin/env python3
"""job_readme.py - file the readme for a finished job into the brain, so the context can be thrown away.

    python job_readme.py write --title "..." --body-file body.md [--session <id>] [--remember]
    python job_readme.py seed            # what a freshly cleared context should be handed
    python job_readme.py status          # json: is the current job covered?
    python job_readme.py recall "query"  # grep the job journal

Two tiers on purpose. EVERY job gets a readme in the job journal (state/log/<date>/) plus state/LAST_JOB.md,
which the SessionStart hook hands to the next context after a /clear. Only a job that taught us something
durable also gets a real brain memory (--remember, or just call brain.py remember directly).

Why not a memory per job: MEMORY.md is loaded into EVERY session's context. A line per job would grow the
always-loaded index without bound - which is the very disease the owner asked to cure. The journal keeps
the record without taxing every future session.
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import job_common as J  # noqa: E402

BRAIN = os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "brain.py")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _warn_if_clock_is_invented(body):
    """Warn when the body states a clock time that disagrees with the real one.

    On 2026-08-10 four readmes were filed in one session stamped 07:25, 08:35, 09:10 and
    09:55 while the true time was 04:xx - the inner clock runs fast under load, and the
    work-session hook was printing the real time on every turn. filed_at is always
    correct, but a human reads the prose. This only warns: a readme may legitimately
    quote a past time, and a gate that blocks on that would be worse than the disease.
    """
    hits = re.findall(r"(?i)real clock[^0-9]{0,12}([01]?\d|2[0-3]):([0-5]\d)", body)
    if not hits:
        return
    now = time.localtime()
    for hh, mm in hits:
        drift = abs((int(hh) * 60 + int(mm)) - (now.tm_hour * 60 + now.tm_min))
        drift = min(drift, 1440 - drift)          # wrap around midnight
        if drift > 45:
            print("clock check: the body says 'real clock %s:%s' but it is %02d:%02d "
                  "(%d minutes out). Read the clock, or drop the stamp - filed_at is "
                  "already exact." % (hh, mm, now.tm_hour, now.tm_min, drift),
                  file=sys.stderr)


def cmd_write(a):
    body = ""
    if a.body_file:
        body = io.open(a.body_file, encoding="utf-8", errors="replace").read()
    elif not sys.stdin.isatty():
        body = sys.stdin.read()
    body = body.strip()
    _warn_if_clock_is_invented(body)
    if not body:
        print("refusing to file an empty readme - pass --body-file or pipe the body on stdin",
              file=sys.stderr)
        return 2

    # Identity from this process's own environment, never from the shared current.json. On 2026-07-26 a
    # neighbouring session's Stop hook overwrote that file between this session's hook writing it and this
    # command reading it, and the readme filed under the wrong session - clobbering that session's handover
    # and leaving a pointer that claimed this session's pid for someone else's work.
    session = a.session or J.session_id() or "unknown"
    cur = J.read_current(session)
    if str(cur.get("session") or "") != session:
        cur = {}          # another agent's record. Better an empty ask than a plausible wrong one.
    ask = (a.ask or cur.get("ask") or "").strip()
    # The Stop hook's own feedback arrives as user-turn content, so current.json's
    # `ask` is frequently the hook text rather than anything he typed - and the
    # job_done hook fires at Stop and TELLS the agent to file a readme, so the
    # polluted value is the common case for any long-running session, not a rare
    # one. Observed on three consecutive filings on 2026-08-15, each hand-stripped
    # afterwards. An empty ask is recoverable; a readme that quotes a hook back at
    # him as "What you asked" is worse than one that admits it did not capture.
    _HOOK_PREFIXES = (
        "stop hook feedback",
        "[work-session]",
        "[compute-instinct]",
        "[unconscious]",
        "[association]",
        "[prompt-contract]",
        "[danger-sense]",
        "[project-context]",
        "conscience:",
        "job done - now close it out",
    )
    _low = ask.lower()
    if any(_low.startswith(p) for p in _HOOK_PREFIXES):
        ask = ""
    ask = ask.replace("\n", " ")[:400]
    slug = J.slugify(a.title)
    stamp = time.time()

    head = (
        "# %s\n"
        "slug: %s\n"
        "session: %s\n"
        "filed_at: %.3f\n"
        "finished: %s\n"
        "\n"
        "## What you asked\n%s\n\n"
        % (a.title, slug, session, stamp, _now(), ask or "(not captured)")
    )
    doc = head + body.rstrip() + "\n"

    J._ensure(J.STATE)
    day = time.strftime("%Y-%m-%d")
    logdir = os.path.join(J.LOGDIR, day)
    J._ensure(logdir)
    logpath = os.path.join(logdir, "%s-%s.md" % (time.strftime("%H%M%S"), slug))
    J._ensure(J.PERSESSION)
    mine = J.session_file(session)
    try:
        with io.open(logpath, "w", encoding="utf-8", newline="\n") as f:
            f.write(doc)
        # This session's own copy decides whether THIS session is done; LAST_JOB is just the newest
        # readme from anyone, kept for a clear that comes back with a session id we have not seen.
        with io.open(mine, "w", encoding="utf-8", newline="\n") as f:
            f.write(doc)
        with io.open(J.LAST_JOB, "w", encoding="utf-8", newline="\n") as f:
            f.write(doc)
    except Exception as e:
        print("could not write the readme: %s" % e, file=sys.stderr)
        return 1

    # Leave the handover pointer LAST, once the readme it names is really on disk. A pointer to a file
    # that does not exist yet is the retry_reads_the_status_not_the_work failure with the arrow reversed.
    pointer = J.write_pointer(session, mine, transcript=cur.get("transcript") or "",
                              cwd=cur.get("cwd") or "", title=a.title or slug)

    remembered = None
    if a.remember:
        # SHORTER MEMORY SLUG (2026-08-08): the slug is paid for TWICE in
        # every MEMORY.md link, and the default 60-char title stub made
        # job memories ~75 chars - the single biggest term in a 44 KB
        # index. 32 chars still identifies the job in recall.
        _short = J.slugify(a.title, maxlen=32) if a.title else slug[:32]
        mem_slug = a.remember if isinstance(a.remember, str) and a.remember != "auto" else \
            "job_%s_%s" % (_short, time.strftime("%Y_%m_%d"))
        try:
            r = subprocess.run(
                [sys.executable, BRAIN, "remember", mem_slug, "--type", "project",
                 "--desc", (a.title or slug)[:200], "--body-file", logpath],
                capture_output=True, text=True, timeout=120)
            remembered = mem_slug if r.returncode == 0 else "FAILED: %s" % (r.stderr or r.stdout)[:200]
        except Exception as e:
            remembered = "FAILED: %s" % e

    # Report the identity that was actually RECORDED in the pointer, not the raw CLAUDE_PID. They differ
    # when the env var names a pid that is not a live claude.exe, and in that case the handover will not
    # match - so printing the env value would advertise a delivery the pointer cannot make.
    ident = J.P.identity()
    print(json.dumps({"filed": True, "slug": slug, "log": logpath, "last_job": J.LAST_JOB,
                      "session": session, "pointer": pointer, "claude_pid": ident["claude_pid"],
                      "claude_started": ident["claude_started"], "resolved_by": ident["resolved_by"],
                      "host_pid": ident["host_pid"], "host_name": ident["host_name"],
                      "remembered": remembered}, indent=2))
    return 0


def seed_text():
    if not os.path.exists(J.LAST_JOB):
        return ""
    try:
        doc = io.open(J.LAST_JOB, encoding="utf-8", errors="replace").read()
    except Exception:
        return ""
    return doc


def cmd_seed(a):
    t = seed_text()
    if not t:
        print("(no job readme on file)")
        return 1
    print(t)
    return 0


def cmd_status(a):
    session = a.session or J.session_id()
    cur = J.read_current(session)
    st = J.filed_state(session)
    print(json.dumps({
        "live": J.live(),
        "current": cur,
        "last_job": st,
        "covered": J.covered(session, cur.get("ask_ts", 0.0)) if session else None,
    }, indent=2, default=str))
    return 0


def cmd_recall(a):
    # Tiered match (2026-08-18): exact phrase first; if none, every term anywhere in the
    # doc; if still none, rank by how many terms hit. A 4-word natural question used to
    # dead-end because only the verbatim substring matched.
    q = a.query.lower()
    terms = [t for t in q.split() if t]
    docs = []
    for root, _dirs, files in os.walk(J.LOGDIR):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            p = os.path.join(root, fn)
            try:
                txt = io.open(p, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            first = txt.splitlines()[0] if txt.splitlines() else fn
            docs.append((os.path.getmtime(p), p, first, txt.lower()))
    hits = [(ts, p, first) for ts, p, first, low in docs if q in low]
    tier = "phrase"
    if not hits and len(terms) > 1:
        hits = [(ts, p, first) for ts, p, first, low in docs
                if all(t in low for t in terms)]
        tier = "all-terms"
    if not hits and len(terms) > 2:
        scored = [(sum(1 for t in terms if t in low), ts, p, first)
                  for ts, p, first, low in docs]
        best = max((s for s, *_ in scored), default=0)
        if best >= 2:
            hits = [(ts, p, first) for s, ts, p, first in scored if s == best]
            tier = "best %d/%d terms" % (best, len(terms))
    hits.sort(reverse=True)
    if not hits:
        print("no job readme mentions %r" % a.query)
        return 1
    if tier != "phrase":
        print("[recall: no exact phrase; showing %s matches]" % tier)
    for _ts, p, first in hits[:a.limit]:
        print("%s\n    %s" % (first, p))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write", help="file the readme for the job just finished")
    w.add_argument("--title", required=True)
    w.add_argument("--ask", default="", help="the owner's ask, his words")
    w.add_argument("--body-file")
    w.add_argument("--session")
    w.add_argument("--remember", nargs="?", const="auto", default=None,
                   help="also file a real brain memory (optionally give the slug)")
    w.set_defaults(func=cmd_write)

    s = sub.add_parser("seed", help="print what a cleared context should be handed")
    s.set_defaults(func=cmd_seed)

    t = sub.add_parser("status", help="json state of the current job")
    t.add_argument("--session")
    t.set_defaults(func=cmd_status)

    r = sub.add_parser("recall", help="grep the job journal")
    r.add_argument("query")
    r.add_argument("--limit", type=int, default=10)
    r.set_defaults(func=cmd_recall)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
