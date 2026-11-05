"""slow_sense_selftest.py - does the slowness hook actually say the right thing, end to end?

The hook's pieces were each tested on their own and both passed while the whole path was broken: a state missing
one key printed a literal {reserved_cores} in the middle of a sentence, because the advice was filled with
format(**state) and the failure fell back to the raw template. A reader who sees an instrument print its own
braces stops believing the instrument.

So this drives the REAL hook through its documented env seams (SLOW_SENSE_STATE, SLOW_SENSE_MARKS) with
synthetic states, and asserts on what a reader would actually see.

    python slow_sense_selftest.py          run every case, exit non-zero on any failure

Cases, and why each one is here:
  healthy      an empty verdict list must produce NO output at all - an instrument that chatters gets muted
  disk         the newer verdict must render with its numbers, and must NOT advise re-running the exclusions
  renders      the silent-failure verdict must render and name the daemon to check
  partial      a state missing a key must still render every number it HAS, and print no raw braces
  stale        a probe that has stopped must SAY SO - silence from a dead instrument reads as good news
"""
import atexit, json, os, shutil, subprocess, sys, tempfile, time

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


_MADE = []


def _mkdtemp():
    """A temp dir that is actually removed when the test process exits.

    The two callers below shared their directories across calls - fresh_marks() exists so a
    mark store can be handed back into run() to exercise the repeat suppression - so a
    try/finally at either call site would delete a store the next call still needs. atexit
    fires once, after every assertion, which is the only moment all of them are dead.

    Without this the selftest left one directory per run: 124 of them in %TEMP% on 09-17,
    the oldest 1.2 days old. 0.1 MB, and about a hundred directory entries a day forever.
    """
    d = tempfile.mkdtemp(prefix="slow_sense_selftest_")
    _MADE.append(d)
    return d


@atexit.register
def _cleanup():
    for d in _MADE:
        shutil.rmtree(d, ignore_errors=True)

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "slow_sense.py")


def run(state, session, marks=None):
    """marks=None gives a fresh mark store (one-shot case); passing one back exercises the repeat suppression."""
    d = marks or _mkdtemp()
    sp = os.path.join(d, "state.json")
    with open(sp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    env = dict(os.environ, SLOW_SENSE_STATE=sp, SLOW_SENSE_MARKS=os.path.join(d, "marks"))
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(
        {"hook_event_name": "UserPromptSubmit", "session_id": session}),
        capture_output=True, text=True, env=env)
    out = (r.stdout or "").strip()
    if not out:
        return ""
    try:
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]
    except Exception:
        return out


def fresh_marks():
    """A mark store shared across calls, so the repeat suppression can be exercised."""
    return _mkdtemp()


def base(**kw):
    st = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "at_epoch": time.time(), "verdict": [],
          "open_ms_scanned": 0.3, "open_ms_excluded": 0.2, "msmpeng_pct": 0.4,
          "renders_outside_batch": 0, "renders_total": 20, "idle_usable_cores": 0, "starved": False,
          "cores_pinned": 2, "cores": 16, "ps_start_wall_s": 0.4, "ps_start_cpu_s": 0.3,
          "reserved_cores": []}
    st.update(kw)
    return st


def main():
    fails = []

    def check(name, cond, detail=""):
        print("  %-52s %s" % (name, "ok" if cond else "FAIL " + detail))
        if not cond:
            fails.append(name)

    print("slow_sense end-to-end selftest")

    t = run(base(), "st-healthy")
    check("healthy state says nothing at all", t == "", repr(t[:80]))

    t = run(base(verdict=["open_latency_disk_contention"], open_ms_scanned=594.0,
                 open_ms_excluded=122.0, msmpeng_pct=2.0), "st-disk")
    check("disk contention renders its numbers", "594.0 ms" in t and "2.0 pct" in t)
    check("disk contention does NOT send you to the exclusions", "no-op" in t)

    t = run(base(verdict=["renders_not_pooled"], renders_outside_batch=22, renders_total=25), "st-renders")
    check("misrouted renders render their counts", "22 of 25" in t)
    check("misrouted renders name the daemon to check", "core_steer" in t)

    st = base(verdict=["reservation_pending_restart"])
    del st["reserved_cores"]
    t = run(st, "st-partial")
    check("a partial state prints no raw braces", "{" not in t, repr(t[:80]))
    check("a partial state names what is missing", "(not measured)" in t)

    t = run(base(verdict=["reservation_pending_restart"], at="2026-01-01 00:00:00",
                 at_epoch=time.time() - 7200), "st-stale")
    check("a stale probe is reported, not passed over", "STALE" in t)

    # THE SEAM. Every case above uses a fresh mark store, so none of them touches the repeat suppression - and
    # that is where this hook fails silently. Suppression too aggressive and the instrument goes quiet forever
    # while every individual case still passes; too weak and it prints on every prompt until agents mute it.
    m = fresh_marks()
    st = base(verdict=["renders_not_pooled"], renders_outside_batch=22, renders_total=25)
    first = run(st, "st-seam", marks=m)
    second = run(st, "st-seam", marks=m)
    check("the same session is told once", bool(first) and not second,
          "first=%r second=%r" % (first[:40], second[:40]))
    other = run(st, "st-seam-other", marks=m)
    check("a DIFFERENT session is still told", bool(other), repr(other[:40]))

    print()
    if fails:
        print("FAILED: %d of the cases above" % len(fails))
        return 1
    print("every case passes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
