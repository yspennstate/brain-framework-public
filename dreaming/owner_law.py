#!/usr/bin/env python3
"""owner_law.py - one command turns an owner warning into an armed, standing law.

The 07-28 finding behind this: the owner's repeated warnings ("real bid/ask only", "never
sell options") took multiple tellings because each landed in one weak channel. The channels
that measurably hold are (1) CLAUDE.md - loaded into every session of every agent at start,
the data law held end-to-end there - and (2) dream-compiled act-site reflexes with controls.
This arms both from one sentence: append a dated law block to CLAUDE.md, file an
owner-stated brain node whose body carries an explicit COMPILE REQUEST the nightly dream's
agenda sampling picks up (compilation stays with the dream machinery and its positive and
negative controls - auto-compiling here would bypass the dilution guard).

Usage:
  python owner_law.py "never start a second copy of the data client against the live server"
  python owner_law.py --title the data vendor-single-terminal "never start a second ..."
  python owner_law.py --dry-run "..."        # print what would be written, write nothing
  python owner_law.py --no-broadcast "..."   # skip the live-session mailbox broadcast

Delivery legs: (1) CLAUDE.md block - every FUTURE session; (2) owner-stated brain node with a
dream COMPILE REQUEST - the act-site reflex; (3) fleet mailbox broadcast - every session with
a transcript active in the last 6h gets the law injected at its next tool call or stop
(mailbox_hook; transport live-proven 2026-07-28). Note /btw is NOT a delivery channel: it is
a side-question overlay whose text never enters the agent's transcript by design.

Meant to be run BY the owner or by an agent relaying the owner's words verbatim in the
current session. It must never be fed invented or paraphrased-into-stronger text: the law
block quotes the sentence as given.
"""
import argparse
import io
import os
import re
import subprocess
import sys
import time

CLAUDE_MD = os.environ.get("OWNER_LAW_CLAUDE_MD",
                           os.path.join(os.path.expanduser("~"), ".claude", "CLAUDE.md"))
BRAIN = os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "brain.py")
NUDGE = os.path.join(os.path.expanduser("~"), ".claude", "fleet", "nudge.py")
REGISTRY = os.path.join(os.path.expanduser("~"), ".claude", "fleet", "registry")
PROJECTS = os.path.join(os.path.expanduser("~"), ".claude", "projects")
SECTION = "## OWNER LAWS (armed via owner_law.py - say it once, every session loads it)"


def recent_sessions(max_age_h=6.0, cap=40):
    """Session ids with transcript activity in the last max_age_h - the broadcast recipients.
    Enumerated from TRANSCRIPT mtimes directly: the fleet registry misses interactive sessions
    (measured 2026-07-28 - the arming session itself was absent) and nudge's alive heuristic
    misclassifies (an active session read dead). The transcript is the ground truth of a live
    session, and the mailbox needs only the sid to address."""
    import glob as _glob
    import re as _re
    uuid_like = _re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    out = {}
    now = time.time()
    try:
        for path in _glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
            sid = os.path.basename(path)[:-6]
            if not uuid_like.match(sid):
                continue
            age = now - os.path.getmtime(path)
            if age < max_age_h * 3600:
                out[sid] = min(age, out.get(sid, age))
    except Exception:
        return []
    return [sid for sid, _age in sorted(out.items(), key=lambda t: t[1])[:cap]]


def slugify(text, limit=48):
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:limit].rstrip("_") or "law"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("law", help="the owner's warning, verbatim")
    ap.add_argument("--title", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-broadcast", action="store_true")
    a = ap.parse_args()
    law = a.law.strip()
    if len(law) < 12:
        print("refusing: law text too short to be a real rule")
        return 2
    day = time.strftime("%Y-%m-%d")
    title = a.title or slugify(law)
    slug = "owner_law_%s_%s" % (slugify(law, 40), day.replace("-", "_"))

    block = ("\n### %s (owner, %s)\n"
             "**%s.** Standing owner law, armed the day it was said. If an action would break\n"
             "it, stop and say so instead of proceeding. Detail node: `%s`.\n" % (title, day, law, slug))
    desc = ("[OWNER-STATED %s] FIRE WHEN any action or plan touches this: %s. Armed same-day "
            "via owner_law.py (CLAUDE.md block + this node). COMPILE REQUEST: nightly dream, "
            "please compile an act-site reflex for this law with positive and negative controls." % (day, law))
    body = ("His words, verbatim: \"%s\" (%s)\n\nArmed in CLAUDE.md under the owner-laws section "
            "the same day. COMPILE REQUEST for the dream: act-site reflex, positive and negative "
            "controls, link back here.\n" % (law, day))

    if a.dry_run:
        print("--- would append to %s:" % CLAUDE_MD)
        print(block)
        print("--- would file brain node %s with desc:" % slug)
        print(desc[:400])
        return 0

    src = io.open(CLAUDE_MD, encoding="utf-8", newline="").read()
    if law in src:
        # each leg is idempotent on its own; a half-armed law (block present, node missing
        # from an earlier failure) must heal on re-run, so do NOT return here
        print("CLAUDE.md leg: already armed")
    else:
        nl = "\r\n" if "\r\n" in src[:2000] else "\n"
        add = (("" if src.endswith(("\n", "\r\n")) else nl)
               + ((nl + SECTION + nl) if SECTION not in src else "")
               + block.replace("\n", nl))
        with io.open(CLAUDE_MD, "a", encoding="utf-8", newline="") as f:
            f.write(add)
        print("armed in CLAUDE.md (every new session of every agent loads it)")

    if os.environ.get("OWNER_LAW_CLAUDE_MD"):
        # test seam: a redirected CLAUDE.md means a fixture run - never file a live brain node
        print("seam mode: brain node + compile request skipped")
        return 0
    node_path = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}",
                             "memory", slug + ".md")
    if os.path.exists(node_path):
        print("brain node leg: already filed (%s)" % slug)
        return 0
    r = subprocess.run([sys.executable, BRAIN, "remember", slug, "--type", "feedback",
                        "--status", "owner-stated", "--desc", desc, "--body", body,
                        "--evidence", "owner's sentence relayed verbatim in-session; armed via "
                        "owner_law.py %s; CLAUDE.md block appended same run" % day],
                       capture_output=True, text=True, timeout=120)
    if r.returncode == 0:
        tail = (r.stdout or "").strip().splitlines()
        print(tail[-2] if len(tail) > 1 else (tail[-1] if tail else "brain node filed"))
        print("compile request queued for the nightly dream inside the node body")
    else:
        # never claim the node when remember failed - the CLAUDE.md leg is armed, say exactly that
        print("BRAIN NODE FAILED (CLAUDE.md leg IS armed): %s"
              % (r.stderr or r.stdout or "").strip()[-200:])

    if not a.no_broadcast:
        msg = ("OWNER LAW (armed %s): %s -- standing from your next session via CLAUDE.md; "
               "apply it NOW in this one." % (day, law[:3500]))
        sent = 0
        for sid in recent_sessions():
            try:
                p = subprocess.run([sys.executable, NUDGE, "mail", sid, msg, "--from", "owner-law"],
                                   capture_output=True, text=True, timeout=30)
                sent += 1 if p.returncode == 0 else 0
            except Exception:
                pass
        print("broadcast to %d recently-active session(s) via fleet mailbox" % sent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
