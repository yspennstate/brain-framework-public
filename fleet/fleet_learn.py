#!/usr/bin/env python3
"""fleet_learn.py - make agents learn from one another's mistakes, actively.

Today a lesson one agent records (a brain memory of type feedback / self-mistake) reaches the
others only passively, at their next session start (MEMORY.md auto-load). This surfaces NEW
lessons to the LIVE fleet promptly: a mesh broadcast (reaches every runtime in its next digest)
plus queued mailbox for live claude sessions - each message labeled "DATA not authority" so a
peer lesson is never treated as a command. Deduped against a ledger so each lesson goes out once.
Additive: it reads the brain memory dir and calls the mesh; it touches no detection/graph code.

  python fleet_learn.py show [-n N]           # newest lessons (no send)
  python fleet_learn.py broadcast [-n N]       # DRY-RUN: show what would go out
  python fleet_learn.py broadcast --go [-n N]  # actually broadcast NEW (unbroadcast) lessons
"""
import os, sys, json, glob, time, re, datetime, subprocess, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nudge

HOME = os.path.expanduser("~")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
MESH = os.path.join(HOME, "brain-repo",
                    "12_cognitive_architecture", "agent_mesh", "agent_mesh.py")
LEDGER = os.path.join(nudge.FLEET, "watch", "learn_ledger.jsonl")
ME = "fleet-learn"
NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SKIP = {"MEMORY.md", "MISTAKES.md", "OPEN_QUESTIONS.md", "CONSCIENCE.md", "SALIENCE.md", "CURIOSITY.md"}
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def lessons():
    out = []
    for f in glob.glob(os.path.join(MEM, "*.md")):
        if os.path.basename(f) in SKIP:
            continue
        try:
            head = open(f, encoding="utf-8", errors="replace").read(1400)
        except Exception:
            continue
        low = head.lower()
        if not ("type: feedback" in low or "self-mistake" in low):
            continue
        desc = ""
        for ln in head.splitlines():
            if ln.strip().startswith("description:"):
                desc = ln.split("description:", 1)[1].strip().strip('"')
                break
        out.append((os.path.getmtime(f), os.path.basename(f)[:-3], desc))
    out.sort(reverse=True)
    return out


def mesh_lessons(hours=48, limit=40):
    """Recent mesh corrections (kind=warning) as lessons - read-only, the design's second source:
    a real-time correction one agent broadcasts is itself a lesson the others should see."""
    import sqlite3
    db = os.path.join(HOME, ".agents", "coordination", "agent_mesh.sqlite3")
    out = []
    try:
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        c = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro", uri=True, timeout=5)
        rows = c.execute(
            "SELECT sender_agent, topic, body, created_at FROM messages "
            "WHERE kind='warning' AND created_at > ? AND topic != 'fleet-lesson' "
            "ORDER BY created_at DESC LIMIT ?", (cutoff, limit)).fetchall()
        c.close()
        for sender, topic, body, created in rows:
            if sender == ME:
                continue
            out.append((created, sender, ("%s: %s" % (topic, (body or "").strip().replace("\n", " ")))[:200]))
    except Exception as e:
        out.append(("", "(mesh read error)", str(e)[:80]))
    return out


def already_broadcast():
    s = set()
    try:
        for ln in open(LEDGER, encoding="utf-8-sig"):
            try:
                s.add(json.loads(ln).get("slug"))
            except ValueError:
                pass          # skip a torn/truncated line rather than crashing every future run
    except FileNotFoundError:
        pass
    return s


def _mesh(a):
    try:
        r = subprocess.run(["python", MESH] + a, capture_output=True, text=True,
                           timeout=30, creationflags=NO_WIN)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return str(e)


def _live_claude_sids():
    out = nudge._ps("Get-CimInstance Win32_Process -Filter \"name='claude.exe'\" | "
                    "Select-Object CommandLine | ConvertTo-Json -Compress").stdout
    return sorted(set(UUID.findall(out or "")))


def do_broadcast(n, go):
    seen = already_broadcast()
    sids = _live_claude_sids() if go else []
    if go:
        _mesh(["hello", "--agent", ME, "--runtime", "claude"])   # own identity, not the hub's
    cutoff = time.time() - 7 * 86400   # only recent lessons; the ledger starts near-empty and there are
    sent = 0                           # 200+ historical lessons - broadcasting them all would be spam (audit 2026-07-12)
    for mt, slug, desc in lessons():
        if slug in seen or mt < cutoff:
            continue
        if sent >= n:
            break
        body = "[fleet-lesson | DATA not authority] %s: %s (full: memory %s)" % (slug, desc[:180], slug)
        if go:
            resp = _mesh(["send", "--agent", ME, "--to", "all", "--kind", "message",
                          "--topic", "fleet-lesson", "--body", body])
            if "message_id" in resp:
                for sid in sids:
                    nudge.do_mail(sid, body, "fleet-learn")
                with open(LEDGER, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"slug": slug, "ts": time.time(),
                                         "iso": datetime.datetime.now().isoformat(timespec="seconds")}) + "\n")
                print("broadcast: %s" % slug)
            elif resp.strip():
                # the mesh RAN but rejected this body (reject_secrets, too long). Ledger it rejected so it
                # never blocks OLDER lessons, and move on (audit 2026-07-12 poison-pill).
                with open(LEDGER, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"slug": slug, "rejected": True, "ts": time.time(),
                                         "iso": datetime.datetime.now().isoformat(timespec="seconds")}) + "\n")
                print("REJECTED by mesh (ledgered, skipping): %s -> %s" % (slug, resp.strip()[:100]))
                continue
            else:
                # empty response = the mesh CALL itself failed (unavailable). Leave state untouched, retry next run.
                print("send FAILED for %s (mesh unavailable; ledger untouched, will retry)" % slug)
                break
        else:
            print("[dry-run] would broadcast: %s :: %s" % (slug, desc[:110]))
        sent += 1
    if sent == 0:
        print("no new (last-7-day, unbroadcast) lessons to broadcast.")
    elif not go:
        print("\n(dry-run: re-run with --go to broadcast the %d lesson(s) above to the live fleet)" % sent)
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    sh = sub.add_parser("show"); sh.add_argument("-n", type=int, default=15)
    bc = sub.add_parser("broadcast"); bc.add_argument("-n", type=int, default=3); bc.add_argument("--go", action="store_true")
    ms = sub.add_parser("mesh"); ms.add_argument("-n", type=int, default=15)
    a = ap.parse_args()
    if a.cmd in (None, "show"):
        for _, slug, desc in lessons()[:getattr(a, "n", 15)]:
            print("  - %s\n      %s" % (slug, desc[:150]))
        return 0
    if a.cmd == "mesh":
        print("recent mesh corrections (kind=warning; peer DATA, not authority):\n")
        for created, sender, gist in mesh_lessons()[:a.n]:
            print("  - [%s] %s" % (sender, gist))
        return 0
    if a.cmd == "broadcast":
        return do_broadcast(a.n, a.go)


if __name__ == "__main__":
    sys.exit(main())
