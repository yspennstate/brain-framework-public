"""The judge's agenda builder reads per-case windows through a byte seek and builds the same cases (28 Sep 2026,
claude-825b1305).

Before: the builder took 178 s against the dream's 120 s budget, so the judge section (J) was skipped every night since
27 Sep. Each case uses the record within 3 s of its fire and up to 20 min after it; the builders now scan exactly that,
starting from a binary search over the transcript's bytes.

 T1 _seek on a synthetic chronological file lands at or before the first line of the window and less than 300 KB
    before it, on a file of about 20 MB
 T2 a line stamped inside the window but written ten minutes earlier in the file (out of order) is still read,
    because the seek aims 30 minutes before the window; lines stamped outside the window stay out
 E1 the three strata built by the new code equal the old code's, case by case (every field but the case id), on a
    frozen copy of today's fire and suppression logs with the clock pinned
 S1 the new builder is inside the dream's 120 s budget with room to spare (under 60 s here)
With --old OLD.py the old builder is run for E1 (it takes about three minutes on a busy day); without it E1 is skipped.

    python test_judge_window_seek.py [--old fire_judge_nightly.py.bak_seek_20260928]
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path, loader=importlib.machinery.SourceFileLoader(name, path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class PinnedTime:
    """time with time() pinned; everything else is the real module."""
    def __init__(self, t):
        self._t = t

    def time(self):
        return self._t

    def __getattr__(self, k):
        return getattr(time, k)


def seek_tests(fj):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.jsonl")
    base = 1790000000
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        for i in range(60000):
            ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(base + i * 5))
            f.write(json.dumps({"type": "assistant", "timestamp": ts + ".000Z", "pad": "x" * 300}) + "\n")
    size = os.path.getsize(p)
    lo_ep = base + 40000 * 5
    lo_s = fj._iso(lo_ep)
    with io.open(p, "rb") as fb:
        off = fj._seek(fb, lo_s, size)
        fb.seek(off)
        if off:
            fb.readline()
        first = None
        pos = fb.tell()
        for raw in fb:
            if fj._TS_RE.search(raw.decode("utf-8")).group(1) >= lo_s:
                first = pos
                break
            pos += len(raw)
    check(first is not None and off <= first and first - off < 300000,
          "T1 seek lands %d bytes before the window's first line on a %.1f MB file" % ((first or 0) - off, size / 1e6))
    # T2: a line written ten minutes before the window (120 lines at 5 s) but stamped inside it is read
    sid = "feedbeef-0000-0000-0000-000000000000"
    proj = os.path.join(d, "proj")
    os.makedirs(proj)
    q = os.path.join(proj, sid + ".jsonl")
    with io.open(q, "w", encoding="utf-8", newline="\n") as f:
        for i in range(20000):
            ep = base + i * 5
            if i == 11880:
                ep = base + 12010 * 5                        # stamped inside the window, written 10 min before it
            ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ep))
            f.write(json.dumps({"type": "assistant", "timestamp": ts + ".000Z", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "echo %d" % i}}]}}) + "\n")
    old_proj = fj.PROJ
    fj.PROJ = proj
    fj._SCAN_CACHE.clear()
    try:
        tools = fj._session_tools(sid, (base + 12000 * 5, base + 12030 * 5))
    finally:
        fj.PROJ = old_proj
        fj._SCAN_CACHE.clear()
    got = [json.loads(t[2])["command"] for t in tools]
    check("echo 11880" in got and "echo 12000" in got and "echo 11990" not in got and "echo 12031" not in got,
          "T2 the line stamped in the window but written 10 min before it is read; stamps outside stay out (%d records)"
          % len(got))
    shutil.rmtree(d)


def frozen_dir():
    d = tempfile.mkdtemp(prefix="judge_eq_")
    for n in ("instinct_fires.tsv", "instinct_suppressed.tsv", "instincts.tsv"):
        if os.path.exists(os.path.join(HERE, n)):
            shutil.copy2(os.path.join(HERE, n), os.path.join(d, n))
    return d


def build(m, d, t, seed):
    m.HERE = d
    m.time = PinnedTime(t)
    m._SCAN_CACHE.clear()
    t0 = time.time()
    fh = m.firehose_cases(seed=seed)
    su = m.suppressed_cases(seed=seed)
    sec = m.agenda_section()
    dt = time.time() - t0
    tgt = []
    for block in sec.split("```json")[1:]:
        tgt.append(json.loads(block.split("```")[0]))
    strip = lambda cs: [{k: v for k, v in c.items() if k != "case_id"} for c in cs]
    return {"firehose": strip(fh), "suppressed": strip(su), "section_cases": strip(tgt)}, dt


def main():
    new = load(os.path.join(HERE, "fire_judge_nightly.py"), "fj_new")
    seek_tests(new)
    d = frozen_dir()
    t, seed = time.time(), 20260928
    try:
        new_cases, new_dt = build(new, d, t, seed)
        n_new = sum(len(v) for v in new_cases.values())
        check(new_dt < 60 and n_new > 0, "S1 the new builder: %.1f s for %d cases (budget 120 s)" % (new_dt, n_new))
        if "--old" in sys.argv:
            old = load(os.path.join(HERE, sys.argv[sys.argv.index("--old") + 1]), "fj_old")
            old_cases, old_dt = build(old, d, t, seed)
            for k in ("firehose", "suppressed", "section_cases"):
                check(old_cases[k] == new_cases[k],
                      "E1 %s: %d cases from the old builder (%.0f s for all), %d from the new, equal field for field %s" % (
                          k, len(old_cases[k]), old_dt, len(new_cases[k]), old_cases[k] == new_cases[k]))
    finally:
        shutil.rmtree(d)
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
