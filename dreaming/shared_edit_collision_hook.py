"""shared_edit_collision_hook.py - PreToolUse: speak when another session edited the same shared brain file minutes ago.

His 27 Sep 16:29 idea was that the brain should fire when a mistake is about to be made. Two sessions changing one shared
file within minutes of each other is such a moment, and it is visible in the state rather than in anyone's words. Measured
before building (28 Sep, THREE_DAY_RUN_2026-09-27.md 09:5x-11:1x): over 21-28 Sep, 2,948 Edit/Write calls went into the
shared brain roots and half had no mesh claim, yet no edit went into another agent's live claim. The overlaps that did
happen were unclaimed: replayed with this rule at a 30-minute window it would have spoken 10 times in the week, every one
a memory that two or three sessions changed within 7-27 minutes of each other (mostly when he gave one order to several
agents). A warning on every unclaimed edit would have spoken about 200 times a day for the same week.

For an Edit, Write, MultiEdit or NotebookEdit under the shared roots, it appends one line to the edit ledger (time,
session, path) and speaks once per session and file in WINDOW_MIN when another session's line for the same file is newer
than WINDOW_MIN. It takes no mesh claim, never blocks, and on any error says nothing. The ledger is brain data: append
only, never deleted (owner law 2026-09-26).

Reading the ledger, deciding and appending this edit's line are one transaction under a short lock on LEDGER + ".lock"
(28 Sep, scheduled audit account03/B15, B15-R41-READ-BEFORE-APPEND-TOCTOU-20260928: two sessions editing one file at the
same moment both read the ledger before either appended, and neither was told). The lock is held for the few
milliseconds of that transaction, never through the edit; if it cannot be had within LOCK_WAIT_S the hook goes ahead
without it rather than hold up the edit.

    python shared_edit_collision_hook.py --selftest
Env for tests: SHARED_EDIT_LEDGER (another ledger file), SHARED_EDIT_NOW (epoch seconds), SHARED_EDIT_TEST_PAUSE
(seconds to wait between the decision and the append, to widen the window a race needs).
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)
import hashlib
import io
import json
import os
import sys
import time

try:
    import msvcrt
except ImportError:                      # not Windows
    msvcrt = None
    import fcntl

LOCK_WAIT_S = 2.0

HOME = os.path.expanduser("~")
LEDGER = os.environ.get("SHARED_EDIT_LEDGER") or os.path.join(HOME, ".claude", "brain_graph", "live",
                                                             "shared_edit_ledger.jsonl")
WINDOW_MIN = 30
TAIL_BYTES = 262144                      # a week of shared edits is about 450 KB; the last half hour sits in the tail
TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
ROOTS = [os.path.normcase(os.path.abspath(r)) for r in (
    os.path.join(HOME, "brain-repo"),
    os.path.join(HOME, ".claude", "dreaming"),
    os.path.join(HOME, ".claude", "brain_graph"),
    os.path.join(HOME, ".claude", "projects", (os.environ.get("BRAIN_SLUG") or HOME.replace(":", "-").replace(chr(92), "-").replace("/", "-")), "memory"),
    os.path.join(HOME, "math-store-build"))]


def shared(path):
    p = os.path.normcase(os.path.abspath(path))
    return any(p.startswith(r + os.sep) for r in ROOTS) and p != os.path.normcase(os.path.abspath(LEDGER))


def tail_lines(path):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            n = fh.tell()
            fh.seek(max(0, n - TAIL_BYTES))
            data = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = data.splitlines()
    if n > TAIL_BYTES and lines:
        lines = lines[1:]                  # the first line of a cut tail is partial
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def session_key(session_id):
    """The identity two ledger rows are compared on: a hash of the whole session id. Until 28 Sep the hook kept and
    compared only its first 8 characters (account01/B19 auto11 and account03/B19 r61, R41), so two sessions sharing
    them were one session to it. No two of 1,088 Claude session ids share them, but Codex ids are time-ordered and
    share them for sessions started within about a minute. The 8 characters stay in the row for display."""
    return hashlib.sha256(str(session_id).encode("utf-8", "replace")).hexdigest()[:16]


def same_session(row, sid, key):
    """True for a row of this session: on the key when the row carries one; rows written before the key existed
    carry only the first 8 characters and are compared on those."""
    rk = row.get("k")
    if rk and key:
        return rk == key
    return row.get("s") == sid


def decide(sid, path, now, rows, key=None):
    """-> (other_row or None). Speaks when another session edited this file within WINDOW_MIN and this session has not
    already been told about it within WINDOW_MIN."""
    w = WINDOW_MIN * 60.0
    others = [r for r in rows if r.get("p") == path and not same_session(r, sid, key) and now - r.get("t", 0) <= w]
    if not others:
        return None
    if any(r.get("p") == path and same_session(r, sid, key) and r.get("fired") and now - r.get("t", 0) <= w
           for r in rows):
        return None
    return max(others, key=lambda r: r.get("t", 0))


class _Lock:
    """An exclusive lock on LEDGER + '.lock' for the read-decide-append transaction; acquired=False after LOCK_WAIT_S."""

    def __enter__(self):
        self.fh, self.acquired = None, False
        try:
            os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
            self.fh = open(LEDGER + ".lock", "a+b")
            deadline = time.time() + LOCK_WAIT_S
            while True:
                try:
                    if msvcrt:
                        self.fh.seek(0)
                        msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.acquired = True
                    break
                except OSError:
                    if time.time() > deadline:
                        break
                    time.sleep(0.005)
        except OSError:
            pass
        return self

    def __exit__(self, *exc):
        try:
            if self.acquired:
                if msvcrt:
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        if self.fh:
            self.fh.close()
        return False


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if data.get("tool_name") not in TOOLS:
            return
        ti = data.get("tool_input") or {}
        path = ti.get("file_path") or ti.get("notebook_path")
        if not path or not shared(path):
            return
        full = str(data.get("session_id") or "") or "unknown"
        sid, key = full[:8], session_key(full)
        p = os.path.normcase(os.path.abspath(path))
        now = float(os.environ.get("SHARED_EDIT_NOW") or time.time())
        pause = float(os.environ.get("SHARED_EDIT_TEST_PAUSE") or 0)
        with _Lock():
            other = decide(sid, p, now, tail_lines(LEDGER), key)
            rec = {"t": round(now, 1), "s": sid, "k": key, "p": p, "tool": data.get("tool_name")}
            if other:
                rec["fired"] = True
                rec["other"] = other.get("s")
            if pause:
                time.sleep(pause)
            try:
                os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
                with io.open(LEDGER, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(json.dumps(rec) + "\n")
            except OSError:
                pass
        if other:
            mins = max(0, int((now - other.get("t", now)) // 60))
            msg = ("[collision] Another session (claude-%s) edited %s %d minute%s ago. Before this edit, re-read the "
                   "file as it is now and check who holds it on the mesh; two sessions changing one shared file is the "
                   "moment to coordinate, not to overwrite.\n  (hint: replayed over 21-28 Sep this fired 10 times in a "
                   "week, each a real overlap; a question, not a verdict)" % (
                       other.get("s"), os.path.basename(p), mins, "" if mins == 1 else "s"))
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}}))
    except Exception:
        return


def selftest():
    import tempfile
    d = tempfile.mkdtemp()
    led = os.path.join(d, "ledger.jsonl")
    mem = os.path.join(HOME, ".claude", "projects", (os.environ.get("BRAIN_SLUG") or HOME.replace(":", "-").replace(chr(92), "-").replace("/", "-")), "memory", "x_selftest_2026_09_28.md")
    outside = os.path.join(d, "not_shared.md")
    other_mem = mem.replace("x_selftest_", "z_selftest_")
    t0 = 1_800_000_000.0
    ok = []

    def run(sid, path, now, tool="Edit", full=None, ledger=None, hook=None):
        env = dict(os.environ, SHARED_EDIT_LEDGER=ledger or led, SHARED_EDIT_NOW=str(now))
        import subprocess
        r = subprocess.run([sys.executable, hook or os.path.abspath(__file__)],
                           input=json.dumps({"tool_name": tool, "session_id": full or (sid + "0000-aaaa"),
                                             "tool_input": {"file_path": path}}),
                           capture_output=True, text=True, env=env, creationflags=(0x08000000 if os.name == "nt" else 0))
        return r.stdout.strip()

    ok.append(("first edit, nobody else: silent", run("aaaaaaaa", mem, t0) == ""))
    ok.append(("own second edit: silent", run("aaaaaaaa", mem, t0 + 60) == ""))
    ok.append(("another session 5 min later: fires", "[collision]" in run("bbbbbbbb", mem, t0 + 300)))
    ok.append(("same session again within the window: silent", run("bbbbbbbb", mem, t0 + 400) == ""))
    ok.append(("a third session 2 h later: silent", run("cccccccc", mem, t0 + 7500) == ""))
    ok.append(("outside the roots: silent", run("dddddddd", outside, t0 + 7600) == ""))
    ok.append(("a Read is ignored", run("eeeeeeee", mem, t0 + 7610, tool="Read") == ""))
    lines = io.open(led, encoding="utf-8").read().splitlines()
    ok.append(("ledger: five shared edits recorded, the outside path and the Read not", len(lines) == 5))
    # two sessions whose ids share their first 8 characters (account01/B19 auto11, account03/B19 r61; R41): one session
    # to the hook before 28 Sep, two now. Codex ids are time-ordered and look like these for sessions a minute apart.
    a_full, b_full = "01a0c3c2-1111-7aaa-8aaa-000000000001", "01a0c3c2-2222-7bbb-8bbb-000000000002"
    src = io.open(os.path.abspath(__file__), encoding="utf-8").read()

    def prefix_case(tag, hook=None):
        """-> (warned, rows recorded): the rows prove the hook ran through main, so silence is not a swallowed crash"""
        pled = os.path.join(d, "prefix_%s.jsonl" % tag)
        run("", mem, t0, full=a_full, ledger=pled, hook=hook)
        warned = "[collision]" in run("", mem, t0 + 300, full=b_full, ledger=pled, hook=hook)
        rows = io.open(pled, encoding="utf-8").read().splitlines() if os.path.exists(pled) else []
        return warned, len(rows)
    ok.append(("two sessions sharing their first 8 characters, 5 min apart: fires", prefix_case("keyed")[0]))
    keyline = "        sid, key = full[:8], session_key(full)\n"
    if src.count(keyline) == 1:
        nokey = os.path.join(d, "nokey_hook.py")
        io.open(nokey, "w", encoding="utf-8").write(src.replace(keyline, "        sid, key = full[:8], None\n"))
        warned, nrows = prefix_case("nokey", hook=nokey)
        ok.append(("negative control, compared on 8 characters: the same pair stays silent while both edits are "
                   "recorded (%d rows)" % nrows, not warned and nrows == 2))
    else:
        ok.append(("negative control, compared on 8 characters: the key line was not found", False))
    # rows written before the key existed carry only the 8 characters and are compared on them
    lled = os.path.join(d, "legacy.jsonl")
    with io.open(lled, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"t": t0, "s": "legacy01", "p": os.path.normcase(os.path.abspath(mem)), "tool": "Edit"}) + "\n")
        fh.write(json.dumps({"t": t0, "s": "01a0c3c2", "p": os.path.normcase(os.path.abspath(other_mem)),
                             "tool": "Edit"}) + "\n")
    ok.append(("a legacy row (no key) of another session fires",
               "[collision]" in run("", mem, t0 + 300, full=a_full, ledger=lled)))
    ok.append(("a legacy row (no key) with this session's 8 characters stays silent",
               run("", other_mem, t0 + 300, full=a_full, ledger=lled) == ""))
    # the race account03/B15 found (R41): two sessions start an edit of one file at the same moment. A pause between
    # the decision and the append widens the window; under the lock the second waits and is told.
    ok.extend(race_checks(os.path.abspath(__file__), d, mem))
    # the same race with the lock taken out: the check must be able to fail (both read before either appends)
    src = io.open(os.path.abspath(__file__), encoding="utf-8").read()
    if src.count("        with _Lock():\n") == 1:
        unlocked = os.path.join(d, "unlocked_hook.py")
        io.open(unlocked, "w", encoding="utf-8").write(src.replace("        with _Lock():\n",
                                                                   "        with open(os.devnull):\n"))
        name, good = race_checks(unlocked, d, mem, expect_fixed=False)[0]
        ok.append(("negative control, no lock: " + name, good))
    else:
        ok.append(("negative control, no lock: the lock line was not found", False))
    for name, good in ok:
        print(("PASS " if good else "FAIL ") + name)
    return all(g for _, g in ok)


def race_checks(hook, d, mem, expect_fixed=True):
    import subprocess
    t0 = 1_800_100_000.0
    out = []
    run_id = os.path.splitext(os.path.basename(hook))[0]      # a ledger of its own for every hook this is run on

    def pair(tag, sid_a, sid_b, path_a, path_b):
        led = os.path.join(d, "race_%s_%s.jsonl" % (run_id, tag))
        procs = []
        for sid, path in ((sid_a, path_a), (sid_b, path_b)):
            env = dict(os.environ, SHARED_EDIT_LEDGER=led, SHARED_EDIT_NOW=str(t0), SHARED_EDIT_TEST_PAUSE="0.4")
            procs.append(subprocess.Popen([sys.executable, hook], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True, env=env, creationflags=(0x08000000 if os.name == "nt" else 0)))
        for pr, (sid, path) in zip(procs, ((sid_a, path_a), (sid_b, path_b))):
            pr.stdin.write(json.dumps({"tool_name": "Edit", "session_id": sid + "0000-aaaa",
                                       "tool_input": {"file_path": path}}))
            pr.stdin.close()
        outs = [pr.stdout.read() for pr in procs]
        for pr in procs:
            pr.wait()
        rows = [json.loads(l) for l in io.open(led, encoding="utf-8").read().splitlines()] if os.path.exists(led) else []
        return sum("[collision]" in o for o in outs), rows

    warned, rows = pair("same", "ra000001", "rb000002", mem, mem)
    want = 1 if expect_fixed else 0
    out.append(("race: two sessions, one file, at once: %d warning (want %d), %d rows" % (warned, want, len(rows)),
                warned == want and len(rows) == 2))
    other = mem.replace("x_selftest_", "y_selftest_")
    warned, rows = pair("diff", "ra000003", "rb000004", mem, other)
    out.append(("race: two sessions, two files, at once: %d warnings, %d rows" % (warned, len(rows)),
                warned == 0 and len(rows) == 2))
    warned, rows = pair("self", "ra000005", "ra000005", mem, mem)
    out.append(("race: one session twice, one file, at once: %d warnings, %d rows" % (warned, len(rows)),
                warned == 0 and len(rows) == 2))
    return out


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    main()
