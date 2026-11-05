#!/usr/bin/env python3
"""console.py - the OWNER's one terminal to see and drive the entire fleet.

One place to talk to every agent (claude + codex, live or dead, subagent or an open
interactive terminal) *through you*, systematically. It is purely ADDITIVE: it CALLS the
existing, tested tools - nudge.py (status/mail/wake) and agent_mesh.py (send/inbox/digest)
- and never edits the launchers, settings.json, or the (currently dark) mailbox hook.

The console has its OWN mesh identity, `owner-console`, so replies address the owner and it
never speaks or reads as a work agent. Two delivery channels, used so a message reaches an
agent whichever one is live:
  - mailbox  (nudge.py mail): surfaces IN a claude agent's turn once the mailbox hook is
    enabled; until then it is queued and delivered on the agent's next wake. No codex hook.
  - mesh     (agent_mesh.py send): reaches any agent - claude or codex - in its next digest.

Commands:
  python console.py list                      # unified roster: A-name, state, mail, task
  python console.py say <who> "message"       # who = A-name | sid-prefix | mesh-id | all
  python console.py broadcast "message"       # = say all
  python console.py wake <who> [--go]         # dry-run by default; --go actually wakes
  python console.py inbox [-n N]              # replies to owner-console + delivered mailbox
  python console.py board                      # the mesh campaign/agent/claims board
  python console.py mistakes [-n N]           # recent cross-agent lessons (learn from each other)
  python console.py help
"""
import os, sys, re, json, glob, time, subprocess, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nudge  # reuse tested procs/registry/mail; importing runs no CLI (guarded by __main__)

HOME = os.path.expanduser("~")
REG = nudge.REG
MAILBOX = nudge.MAILBOX
PROJ_ROOT = os.path.join(HOME, ".claude", "projects")
MESH = os.path.join(HOME, "brain-repo",
                    "12_cognitive_architecture", "agent_mesh", "agent_mesh.py")
BRAIN_MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
ME = "owner-console"             # the console's OWN mesh identity - never impersonate a work agent
NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _mesh(cmd_args, timeout=30):
    try:
        r = subprocess.run(["python", MESH] + cmd_args, capture_output=True, text=True,
                           timeout=timeout, creationflags=NO_WIN)
        return ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:
        return "(mesh call failed: %s)" % e


_ENSURED = False


def _ensure_identity():
    """Register the console's own mesh identity once per run (idempotent) so replies address
    owner-console and we never speak/read as a work agent - the v1 defect was impersonating
    claude-fleet-hub and marking ITS inbox delivered."""
    global _ENSURED
    if not _ENSURED:
        _mesh(["hello", "--agent", ME, "--runtime", "claude"])
        _ENSURED = True


def load_registry():
    reg = {}
    for f in glob.glob(os.path.join(REG, "*.json")):
        try:
            e = json.load(open(f, encoding="utf-8-sig"))
        except Exception:
            continue
        sid = e.get("id") or os.path.basename(f)[:-5]
        reg[sid] = e
    return reg


def aname_of(entry):
    """Owner-facing name = the A<n>/C<n> token the registry title already carries."""
    tok = ((entry.get("title") or "").strip().split() or [""])[0]
    if tok[:1] in ("A", "C") and tok[1:].isdigit():
        return tok
    return ""


def _live_snapshot():
    out = nudge._ps(
        "Get-CimInstance Win32_Process -Filter \"name='claude.exe' OR name='codex.exe'\" | "
        "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress").stdout
    procs = json.loads(out) if out and out.strip() else []
    if isinstance(procs, dict):
        procs = [procs]
    allout = nudge._ps("Get-CimInstance Win32_Process | Select-Object ParentProcessId | "
                       "ConvertTo-Json -Compress").stdout
    allp = json.loads(allout) if allout and allout.strip() else []
    if isinstance(allp, dict):
        allp = [allp]
    ppids = {p.get("ParentProcessId") for p in allp}
    return procs, ppids


def _transcript_age(sid):
    cands = glob.glob(os.path.join(PROJ_ROOT, "*", sid + ".jsonl"))
    if not cands:
        return None
    return time.time() - max(os.path.getmtime(c) for c in cands)


def state_of(sid, procs, ppids):
    mine = [p for p in procs if sid in (p.get("CommandLine") or "")]
    if not mine:
        age = _transcript_age(sid)
        if age is not None and age < 300:
            return "ALIVE*", 0          # plain-started (no sid in cmdline) but transcript hot
        return "DEAD", 0
    working = any(p.get("ProcessId") in ppids for p in mine)
    return ("WORKING" if working else "IDLE"), len(mine)


def mail_count(sid):
    return len(glob.glob(os.path.join(MAILBOX, sid, "*.md")))


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _is_real_sid(sid):
    """A resolved 36-char string is only a real session if it is UUID-shaped AND a registry entry or a
    transcript exists - otherwise nudge.resolve_sid returns a 36-char MESH id as a 'sid' and a say to it
    silently creates a mailbox nobody reads (audit 2026-07-12)."""
    if not _UUID.match(sid or ""):
        return False
    return (os.path.exists(os.path.join(REG, sid + ".json"))
            or bool(glob.glob(os.path.join(PROJ_ROOT, "*", sid + ".jsonl"))))


def resolve(who, reg):
    """who -> (sid_or_None, runtime, mesh_id). A-name, sid prefix, or a mesh identity."""
    for sid, e in reg.items():
        if aname_of(e).lower() == who.lower():
            return sid, e.get("runtime", "claude"), None
    try:
        sid = nudge.resolve_sid(who)
        if _is_real_sid(sid):
            return sid, reg.get(sid, {}).get("runtime", "claude"), None
        # a 36-char string that is not a real session (e.g. a mesh id) -> fall through to mesh
    except SystemExit as e:
        # ambiguous prefix (>1 match): surface the match list instead of silently mesh-sending to it
        if "matches" in str(e) and " 0 matches" not in str(e):
            print("console: %s" % e)
    return None, None, who


# ---------------------------------------------------------------- commands

def cmd_list(args):
    reg = load_registry()
    procs, ppids = _live_snapshot()
    rows = []
    for sid, e in reg.items():
        st, n = state_of(sid, procs, ppids)
        rows.append((aname_of(e) or "-", sid[:8], e.get("runtime", "?"), st,
                     "!" if e.get("disabled") else "", mail_count(sid),
                     (e.get("title") or "")[:46]))
    order = {"WORKING": 0, "IDLE": 1, "ALIVE*": 2, "DEAD": 3}
    rows.sort(key=lambda r: (order.get(r[3], 4), r[0]))
    print("%-5s %-9s %-7s %-8s %-3s %-4s %s" %
          ("name", "sid", "runtime", "state", "off", "mail", "title"))
    live = 0
    for r in rows:
        if r[3] in ("WORKING", "IDLE", "ALIVE*"):
            live += 1
        print("%-5s %-9s %-7s %-8s %-3s %-4s %s" %
              (r[0], r[1], r[2], r[3], r[4], r[5] or "", r[6]))
    _ensure_identity()
    print("\n%d registered, %d live. Mesh view (who is doing what right now):\n" % (len(rows), live))
    print(_mesh(["digest", "--agent", ME, "--no-mark-delivered"]))


def _say_one(who, message, reg):
    sid, runtime, mesh_id = resolve(who, reg)
    if sid:
        # a session target. claude -> mailbox (the sanctioned owner/supervisor channel, keyed by
        # sid: queued now, surfaced in-turn once the mailbox gate opens, else on next wake).
        # codex has no mailbox hook, so it must be addressed by its mesh id instead.
        if runtime == "codex":
            return ("%s (%s) is codex - no mailbox hook. Address it by its mesh id "
                    "(see `console.py board`), e.g. codex-<name>." % (who, sid[:8]))
        nudge.do_mail(sid, message, "owner")
        return "%s (%s): queued to mailbox" % (who, sid[:8])
    # a mesh identity (any runtime) - reaches it in its next mesh digest
    m = _mesh(["send", "--agent", ME, "--to", mesh_id, "--kind", "message",
               "--topic", "owner", "--body", message])
    return "%s: mesh %s" % (mesh_id, "sent" if "message_id" in m else "FAILED -> " + m[:100])


def cmd_say(args):
    reg = load_registry()
    _ensure_identity()
    message = "[from owner] " + args.message
    if args.who.lower() == "all":
        return cmd_broadcast(args)
    print(_say_one(args.who, message, reg))
    print("\nnote: the in-turn mailbox hook is currently DARK (safety gate); mesh delivery and "
          "queued mailbox both still reach the agent. `console.py inbox` to read replies.")


def cmd_broadcast(args):
    reg = load_registry()
    _ensure_identity()
    message = "[owner broadcast] " + args.message
    procs, ppids = _live_snapshot()
    n = 0
    for sid, e in reg.items():
        st, _ = state_of(sid, procs, ppids)
        if st in ("WORKING", "IDLE", "ALIVE*") and e.get("runtime") != "codex" and not e.get("disabled"):
            nudge.do_mail(sid, message, "owner")
            n += 1
    m = _mesh(["send", "--agent", ME, "--to", "all", "--kind", "message", "--topic", "owner",
               "--body", message])
    print("broadcast: queued mailbox for %d live claude sessions; mesh fanout -> %s" %
          (n, "ok" if "fanout" in m else m[:120]))


def cmd_wake(args):
    reg = load_registry()
    sid, runtime, mesh_id = resolve(args.who, reg)
    if not sid:
        print("wake needs a claude/codex session (A-name or sid); %r looks like a mesh-only id. "
              "Use `say` to message it." % args.who)
        return
    call = [sys.executable, os.path.join(nudge.FLEET, "nudge.py"), "wake", sid]
    if not args.go:
        call.append("--dry-run")
    r = subprocess.run(call, capture_output=True, text=True, creationflags=NO_WIN)
    print((r.stdout or "") + (r.stderr or ""))
    if not args.go:
        print("(dry-run: re-run with --go to actually wake)")


def cmd_inbox(args):
    _ensure_identity()
    print("=== replies to owner-console (mesh inbox) ===")
    print(_mesh(["inbox", "--agent", ME]))
    print("\n=== recently delivered mailbox mail (what actually reached agents) ===")
    delivered = glob.glob(os.path.join(MAILBOX, "*", "delivered", "*.md"))
    delivered.sort(key=os.path.getmtime, reverse=True)
    for f in delivered[:args.n]:
        sid = os.path.basename(os.path.dirname(os.path.dirname(f)))
        print("  %s  %s" % (sid[:8], os.path.basename(f)))


def cmd_board(args):
    print(_mesh(["status", "--markdown"], timeout=45))


def cmd_mistakes(args):
    """Recent cross-agent lessons: brain memories of type feedback/self-mistake, newest first."""
    files = glob.glob(os.path.join(BRAIN_MEM, "*.md"))
    picked = []
    for f in files:
        try:
            head = open(f, encoding="utf-8", errors="replace").read(1200)
        except Exception:
            continue
        low = head.lower()
        if os.path.basename(f) in ("MEMORY.md", "MISTAKES.md", "OPEN_QUESTIONS.md",
                                   "CONSCIENCE.md", "SALIENCE.md", "CURIOSITY.md"):
            continue
        if "type: feedback" in low or "self-mistake" in low:
            desc = ""
            for line in head.splitlines():
                if line.strip().startswith("description:"):
                    desc = line.split("description:", 1)[1].strip().strip('"')[:150]
                    break
            picked.append((os.path.getmtime(f), os.path.basename(f), desc))
    picked.sort(reverse=True)
    print("recent cross-agent lessons (newest first):\n")
    for _, name, desc in picked[:args.n]:
        print("  - %s\n      %s" % (name, desc))


def main():
    ap = argparse.ArgumentParser(prog="console.py", description="owner one-terminal fleet control")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list")
    s = sub.add_parser("say");        s.add_argument("who"); s.add_argument("message")
    b = sub.add_parser("broadcast");  b.add_argument("message")
    w = sub.add_parser("wake");       w.add_argument("who"); w.add_argument("--go", action="store_true")
    i = sub.add_parser("inbox");      i.add_argument("-n", type=int, default=15)
    sub.add_parser("board")
    mk = sub.add_parser("mistakes");  mk.add_argument("-n", type=int, default=12)
    sub.add_parser("help")
    a = ap.parse_args()
    if a.cmd in (None, "list"):
        return cmd_list(a)
    if a.cmd == "say":
        return cmd_say(a)
    if a.cmd == "broadcast":
        return cmd_broadcast(a)
    if a.cmd == "wake":
        return cmd_wake(a)
    if a.cmd == "inbox":
        return cmd_inbox(a)
    if a.cmd == "board":
        return cmd_board(a)
    if a.cmd == "mistakes":
        return cmd_mistakes(a)
    if a.cmd == "help":
        print(__doc__)


if __name__ == "__main__":
    sys.exit(main())
