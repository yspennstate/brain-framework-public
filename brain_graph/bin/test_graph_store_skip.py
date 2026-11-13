"""Controls for graph_store.build(skip_session=...).

A synthetic touch log: one owner session and one headless test worker read the same memory, each goes on to a memory of
its own, and filler sessions keep every memory under the hub cut. Built three ways into temporary folders - no skip, a
skip of the worker, a skip of nobody - and compared; then the command line itself, with the session classifier pointed
at two fixture transcripts (a worker and one of his), must leave the worker out by default and keep it on request.
Nothing live is read or written.

    python test_graph_store_skip.py [GRAPH_STORE_PY]
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile

BIN = os.path.dirname(os.path.abspath(__file__))
FAILS = []


def load(path):
    if not path.endswith(".py"):
        dst = os.path.join(tempfile.mkdtemp(prefix="gs_copy_"), "graph_store.py")
        shutil.copy(path, dst)
        path = dst
    sys.path.insert(0, BIN)
    spec = importlib.util.spec_from_file_location("graph_store_under_test", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def fixture(d):
    ev, t = [], 1.79e9
    def touch(s, k, i):
        nonlocal t
        t += 20.0
        ev.append({"ts": t, "session": s, "kind": k, "id": i})
    touch("hhhhhhhh", "recalled", "shared_memory_a")
    touch("hhhhhhhh", "mem", "shared_memory_a")
    touch("hhhhhhhh", "mem", "owner_next_memory_b")
    touch("wwwwwwww", "recalled", "shared_memory_a")
    touch("wwwwwwww", "mem", "shared_memory_a")
    touch("wwwwwwww", "mem", "worker_only_memory_c")
    for n in range(8):                                    # filler: keeps every item under the hub share
        s = "f%07d" % n
        touch(s, "mem", "filler_%d_x" % n)
        touch(s, "mem", "filler_%d_y" % n)
    p = os.path.join(d, "events_touch.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        for e in ev:
            fh.write(json.dumps(e) + "\n")
    return p


def nodes(out):
    raw = json.load(open(os.path.join(out, "nodes.json"), encoding="utf-8"))
    names = raw.get("names", raw) if isinstance(raw, dict) else raw
    return {str(x) for x in names}


def main():
    gs = load(sys.argv[1] if len(sys.argv) > 1 else os.path.join(BIN, "graph_store.py"))
    d = tempfile.mkdtemp(prefix="gs_skip_test_")
    ev = fixture(d)
    a, b, c = (os.path.join(d, x) for x in ("plain", "skip_worker", "skip_none"))
    ma = gs.build(ev, a)
    mb = gs.build(ev, b, skip_session=lambda s: s == "wwwwwwww")
    mc = gs.build(ev, c, skip_session=lambda s: False)
    na, nb, nc = nodes(a), nodes(b), nodes(c)
    has = lambda ns, x: any(x in n for n in ns)
    check("1 without a skip the worker's own memory is in the store", has(na, "worker_only_memory_c"), str(sorted(na)))
    check("2 with the worker skipped its own memory is not", not has(nb, "worker_only_memory_c"), str(sorted(nb)))
    check("3 the owner session's memories stay when the worker is skipped",
          has(nb, "shared_memory_a") and has(nb, "owner_next_memory_b"), str(sorted(nb)))
    check("4 the build counts what it skipped (one session, three events)",
          mb.get("skipped_sessions") == 1 and mb.get("skipped_events") == 3, json.dumps({k: mb.get(k) for k in
                                                                                        ("skipped_sessions", "skipped_events")}))
    def content(out):                                     # everything but the build's own clock
        n = json.load(open(os.path.join(out, "nodes.json"), encoding="utf-8"))
        if isinstance(n, dict) and isinstance(n.get("meta"), dict):
            n["meta"].pop("built", None)
        return n, open(os.path.join(out, "uses.json"), "rb").read(), open(os.path.join(out, "edges.npz"), "rb").read()
    check("5 a skip that matches nobody builds the same store as no skip (all but the build time)", content(a) == content(c))
    check("6 without the argument nothing is skipped", ma.get("skipped_events", 0) == 0 and ma.get("skipped_sessions", 0) == 0)
    cli_controls(gs, d, ev)
    print("%d of 8 controls pass" % (8 - len(FAILS)))
    return 1 if FAILS else 0


def cli_controls(gs, d, ev):
    """7-8: `graph_store.py build` applies the scheduled builds' population rule unless told otherwise
    (the command used to keep the test workers run_graph.py leaves out)."""
    import session_kinds as sk
    proj = os.path.join(d, "projects", "p")
    os.makedirs(proj)
    for sid, entry, first in (("wwwwwwww-0000-0000-0000-000000000000", "sdk-cli", "[headless-worker] grade this"),
                              ("hhhhhhhh-0000-0000-0000-000000000000", "cli", "keep working")):
        with open(os.path.join(proj, sid + ".jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "user", "entrypoint": entry, "message": {"role": "user", "content": first}}) + "\n")
    saved = (sk.PROJECTS, sk.CODEX, sk.CACHE, sys.argv)
    sk.PROJECTS, sk.CODEX, sk.CACHE = os.path.join(d, "projects"), os.path.join(d, "no_codex"), os.path.join(d, "kinds.json")
    sk.reset()
    got = {}
    try:
        for name, extra in (("cli_default", []), ("cli_keep", ["--include-test-workers"])):
            out = os.path.join(d, name)
            sys.argv = ["graph_store.py", "build", "--events", ev, "--out", out] + extra
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                    gs.main()
                got[name] = (nodes(out), json.loads(buf.getvalue().strip().splitlines()[-1]))
            except (SystemExit, Exception) as e:
                got[name] = (set(), {"error": "%s %s" % (type(e).__name__, e)})
    finally:
        sk.PROJECTS, sk.CODEX, sk.CACHE, sys.argv = saved
        sk.reset()
    has = lambda ns, x: any(x in n for n in ns)
    n7, m7 = got["cli_default"]
    check("7 the command line leaves the test worker out by default",
          not has(n7, "worker_only_memory_c") and has(n7, "owner_next_memory_b") and m7.get("skipped_sessions") == 1,
          json.dumps(m7)[:200])
    n8, m8 = got["cli_keep"]
    check("8 --include-test-workers keeps it", has(n8, "worker_only_memory_c") and m8.get("skipped_sessions", 0) == 0,
          json.dumps(m8)[:200])


if __name__ == "__main__":
    sys.exit(main())
