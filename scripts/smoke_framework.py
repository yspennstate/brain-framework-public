#!/usr/bin/env python3
"""Prove a copy of this framework actually works, without touching live state.

The test suites check units. This checks that the four things the framework is FOR still do
their job when driven as a user drives them: remember and recall, coordinate, broadcast, and
record a mathematical claim.

Run it from a fresh clone or a vendored copy of this repository:

    python scripts/smoke_framework.py [--receipt smoke_receipt.json]

By default it tests the checkout it sits in, not an installed home. bootstrap/install.py places
only the roots in its SUBSYSTEMS map, and some components checked here (the math brain, the film
brain) are not among them. --installed HOME tests an install instead: after
`install.py --home HOME --apply`, it copies back only what the installer placed there, so a
checked component the install lacks fails as absent. --receipt writes the kind of tree tested, a
hash over its files, the commit, the installer's hash and every check to a JSON file.

Every component runs against a throwaway directory via its documented seam (see
docs/EXERCISING.md), and the framework itself is COPIED to a temp directory first, because
several scripts write artifacts next to themselves - bm25 its index, instincts its compiled
tsv, dream.py its agenda. Running in place would modify the tree under test.

Each check asserts an effect observed independently of the tool that produced it. Exit 0
means the command ran; that is not the same as working, and this file exists because of the
difference.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print("  %-46s %s%s" % (name, "PASS" if ok else "FAIL",
                            ("  " + detail) if detail else ""))


def run(args, cwd, env, timeout=300):
    p = subprocess.run([sys.executable] + args, cwd=cwd, env=env,
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def make_corpus(path: str) -> None:
    """A corpus whose index lines carry REAL descriptions - the lexical arm matches on
    those, so placeholder text there makes recall correctly find nothing."""
    mems = {
        "alpha_2026_01_01.md": ("alpha note on gradient descent convergence",
                                "Gradient descent converges when the step size is below "
                                "2/L for an L-smooth objective."),
        "beta_2026_01_02.md": ("beta note on step size and smoothness",
                               "Step size selection depends on the smoothness constant L."),
        "gamma_2026_01_03.md": ("unrelated note about kitchen inventory",
                                "Two onions and a jar of paprika."),
    }
    os.makedirs(path, exist_ok=True)
    index = ["# Memory Index"]
    for name, (desc, body) in mems.items():
        slug = name[:-3]
        with open(os.path.join(path, name), "w", encoding="utf-8") as fh:
            fh.write("---\nname: %s\ndescription: %s\nmetadata:\n  type: reference\n---\n\n%s\n"
                     % (slug, desc, body))
        index.append("- [%s](%s) - %s" % (name, name, desc))
    with open(os.path.join(path, "MEMORY.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(index) + "\n")


def check_memory(work: str, tmp: str, base_env: dict) -> None:
    mem = os.path.join(tmp, "memory")
    make_corpus(mem)
    env = dict(base_env, BRAIN_MEM_DIR=mem)
    dreaming = os.path.join(work, "dreaming")

    rc, _ = run(["bm25.py", "build"], dreaming, env)
    record("memory: index builds", rc == 0)

    rc, out = run(["brain.py", "recall", "gradient descent convergence"], dreaming, env)
    found = "alpha_2026_01_01" in out or "beta_2026_01_02" in out
    noise = "gamma_2026_01_03" in out
    record("memory: recall finds the relevant note", rc == 0 and found)
    record("memory: recall rejects the unrelated note", not noise)


def check_mesh(work: str, tmp: str, base_env: dict) -> None:
    state = os.path.join(tmp, "mesh")
    os.makedirs(state, exist_ok=True)
    env = dict(base_env, AGENT_MESH_HOME=state)
    mesh_dir = os.path.join(work, "mesh")
    mesh = ["agent_mesh.py"]

    for agent in ("smoke-a", "smoke-b"):
        run(mesh + ["hello", "--agent", agent, "--runtime", "smoke",
                    "--task", "framework smoke test"], mesh_dir, env)

    rc_a, _ = run(mesh + ["claim", "--agent", "smoke-a", "--resource", "topic:smoke",
                          "--purpose", "hold", "--mode", "exclusive"], mesh_dir, env)
    rc_b, out_b = run(mesh + ["claim", "--agent", "smoke-b", "--resource", "topic:smoke",
                              "--purpose", "should be refused", "--mode", "exclusive"],
                      mesh_dir, env)
    run(mesh + ["release", "--agent", "smoke-a", "--resource", "topic:smoke"], mesh_dir, env)
    rc_b2, _ = run(mesh + ["claim", "--agent", "smoke-b", "--resource", "topic:smoke",
                           "--purpose", "now free", "--mode", "exclusive"], mesh_dir, env)

    record("mesh: first claim accepted", rc_a == 0)
    record("mesh: conflicting claim refused", rc_b != 0 and "conflict" in out_b.lower())
    record("mesh: claim available after release", rc_b2 == 0)


def check_workspace(work: str, tmp: str, base_env: dict) -> None:
    db = os.path.join(tmp, "workspace.sqlite3")
    ws = os.path.join(work, "workspace", "global_workspace")
    env = dict(base_env, GLOBAL_WORKSPACE_DB=db)
    body = "the framework smoke test broadcast a thought"

    rc, _ = run(["workspace.py", "--db", db, "post", "--agent", "smoke",
                 "--kind", "found", "--body", body, "--salience", "4"], ws, env)
    record("workspace: broadcast accepted", rc == 0)
    rc, stream = run(["workspace.py", "--db", db, "stream"], ws, env)
    record("workspace: broadcast appears in the stream", rc == 0 and body[:28] in stream)


def check_dream(work: str, tmp: str, base_env: dict) -> None:
    """The dream cycle is a shipped component and nothing here proved it ran.

    dream.py takes [memory_dir] [out_path] positionally, so it can be driven entirely
    inside the sandbox: a corpus it has never seen, an agenda path in tmp. The check is
    that it READ the corpus, not merely that it exited zero - the agenda has to name a
    memory that only exists in the fixture, and it must not have written into the
    copied tree.
    """
    mem = os.path.join(tmp, "dream_memory")
    make_corpus(mem)
    out = os.path.join(tmp, "DREAM_AGENDA.md")
    dreaming = os.path.join(work, "dreaming")
    tree_agenda = os.path.join(dreaming, "DREAM_AGENDA.md")
    before = os.path.exists(tree_agenda)

    rc, err = run(["dream.py", mem, out], dreaming, base_env)
    record("dream: cycle runs to completion", rc == 0, err.strip()[:70])

    wrote = os.path.isfile(out) and os.path.getsize(out) > 0
    record("dream: writes an agenda", wrote)

    text = ""
    if wrote:
        with open(out, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    # the fixture slugs exist nowhere else, so naming one proves the corpus was read
    record("dream: agenda is built from the corpus it was given",
           any(s in text for s in ("alpha_2026_01_01", "beta_2026_01_02",
                                   "gamma_2026_01_03")))
    record("dream: leaves the tree it ran from alone",
           os.path.exists(tree_agenda) == before)


def check_handover(work: str, tmp: str, base_env: dict) -> None:
    """A session must be able to close out so its context can be cleared.

    job_cycle documents JOB_CYCLE_STATE as its test seam, so the whole faculty runs
    inside the sandbox. The check is that the readme LANDED and is readable back with
    the body intact - filing something unreadable is the failure that matters, because
    nobody notices until the next session needs the handover and finds nothing.
    """
    jc = os.path.join(work, "dreaming", "job_cycle")
    if not os.path.isdir(jc):
        record("handover: present", False, "(dreaming/job_cycle absent)")
        return
    state = os.path.join(tmp, "jobstate")
    env = dict(base_env, JOB_CYCLE_STATE=state)
    body = os.path.join(tmp, "handover_body.md")
    marker = "the framework smoke test filed a handover"
    with open(body, "w", encoding="utf-8") as fh:
        fh.write("## What I did\n\n%s\n" % marker)

    rc, err = run(["job_readme.py", "write", "--title", "smoke handover",
                   "--body-file", body], jc, env)
    # detail only on failure: on success this tool answers with JSON containing an
    # absolute path, and a passing check should not spray the run's home directory
    # across the output.
    record("handover: readme files", rc == 0, "" if rc == 0 else err.strip()[:70])

    # find it back by reading the state tree, not by trusting the writer's exit code
    landed = []
    for dirpath, _dirnames, filenames in os.walk(state):
        for fn in filenames:
            if fn.endswith(".md"):
                landed.append(os.path.join(dirpath, fn))
    record("handover: the readme is on disk", bool(landed))

    text = ""
    for path in landed:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text += fh.read()
    record("handover: the body survived the round trip", marker in text)
    record("handover: nothing written outside the sandbox",
           not os.path.exists(os.path.join(jc, "state")))


def check_mathbrain(work: str, tmp: str, base_env: dict) -> None:
    mb = os.path.join(work, "mathbrain")
    if not os.path.isdir(mb):
        record("math brain: present", False, "(mathbrain/ absent)")
        return
    db = os.path.join(tmp, "mathbrain.sqlite3")
    env = dict(base_env, PYTHONPATH=os.path.join(mb, "src"))
    cli = ["-m", "math_brain.cli"]

    rc, _ = run(cli + ["init", db], mb, env)
    record("math brain: database initialises", rc == 0 and os.path.exists(db))
    rc, _ = run(cli + ["add-claim", db, "Gradient descent converges below 2/L.",
                       "--kind", "theorem", "--domain", "optimization"], mb, env)
    record("math brain: claim accepted", rc == 0)
    rc, out = run(cli + ["recall", db, "gradient descent"], mb, env)
    record("math brain: claim recalled", rc == 0 and "gradient descent" in out.lower())

    # Independent read: not the tool that wrote it.
    rows = 0
    if os.path.exists(db):
        con = sqlite3.connect(db)
        try:
            rows = con.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        except sqlite3.Error:
            rows = -1
        finally:
            con.close()
    record("math brain: claim readable by plain sqlite", rows == 1, "rows=%s" % rows)


def check_filmbrain(work: str, tmp: str, base_env: dict) -> None:
    """The film brain ships WITHOUT its store, on purpose: most of a grown one is the tool
    surface of a single machine. So the thing to prove is not that a database arrives intact,
    it is that the authored craft layer still BUILDS one here and that the built store answers.
    A copied database would have passed this check while describing software this machine does
    not have, which is the reason the database is not copied.
    """
    fb = os.path.join(work, "filmbrain")
    if not os.path.isdir(fb):
        record("film brain: present", False, "(filmbrain/ absent)")
        return
    db = os.path.join(tmp, "filmbrain.db")
    env = dict(base_env, FILMBRAIN_DB=db, FILMBRAIN_ROOT=fb)

    rc, out = run([os.path.join("ingest", "load_craft.py")], fb, env)
    # Named for what it checks: the loader RAN and made a database. It says nothing about
    # what is in it - with the craft files removed this still passes, exit 0 over an empty
    # store - so the two checks below carry the content claim.
    record("film brain: the craft loader runs", rc == 0 and os.path.exists(db))

    rc, out = run(["filmbrain.py", "stats"], fb, env)
    # `stats` prints "nodes N   edges M   nodes+edges K": take the token AFTER "nodes",
    # not the largest number on the line, or the sum passes a check about the parts.
    toks = out.split()
    nodes = 0
    for i, tok in enumerate(toks):
        if tok == "nodes" and i + 1 < len(toks) and toks[i + 1].isdigit():
            nodes = int(toks[i + 1])
            break
    record("film brain: the built store has a craft layer", rc == 0 and nodes >= 400,
           "nodes=%s" % nodes)

    rc, out = run(["filmbrain.py", "recall", "why does synthetic narration sound robotic"], fb, env)
    # An empty store answers this too - with a header and no rows - so the check is that a
    # craft node came back, not that the command printed something.
    record("film brain: recall answers a craft question",
           rc == 0 and "craft." in out, "returned a craft node: %s" % ("craft." in out))

    # Independent read: not the tool that wrote it.
    rows = 0
    if os.path.exists(db):
        con = sqlite3.connect(db)
        try:
            rows = con.execute("SELECT COUNT(*) FROM node").fetchone()[0]
        except sqlite3.Error:
            rows = -1
        finally:
            con.close()
    record("film brain: nodes readable by plain sqlite", rows >= 400, "rows=%s" % rows)


CHECKED_ROOTS = ("dreaming", "mesh", "workspace", "mathbrain", "filmbrain")


def installer_map():
    """bootstrap/install.py's SUBSYSTEMS map {repo path: install target}, or None when it cannot be read"""
    import importlib.util
    path = os.path.join(ROOT, "bootstrap", "install.py")
    try:
        spec = importlib.util.spec_from_file_location("framework_install", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return dict(mod.SUBSYSTEMS)
    except Exception:                                 # noqa: BLE001 - an unreadable map is reported, not guessed
        return None


def installer_coverage():
    """(roots bootstrap/install.py places, roots this smoke checks that it does not place)"""
    smap = installer_map()
    if smap is None:
        return None, ["(bootstrap/install.py could not be read)"]
    placed = sorted(sub.split("/")[0] for sub in smap)
    return placed, [r for r in CHECKED_ROOTS if r not in placed and os.path.isdir(os.path.join(ROOT, r))]


def tree_sha256(top: str) -> str:
    """one hash over every file of the tree actually tested: relative path and content, sorted"""
    import hashlib
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = sorted(d for d in dirnames if d not in ("__pycache__", ".git", ".pytest_cache"))
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            h.update(os.path.relpath(p, top).replace(os.sep, "/").encode("utf-8") + b"\0")
            with open(p, "rb") as fh:
                h.update(hashlib.sha256(fh.read()).digest())
    return h.hexdigest()


def copy_installed(home: str, work: str) -> list:
    """Rebuild the repository layout the checks expect from what bootstrap/install.py placed under HOME, and nothing
    else: each SUBSYSTEMS target is copied back to its repository path. Returns the roots found."""
    found = []
    os.makedirs(work, exist_ok=True)
    for sub, target in (installer_map() or {}).items():
        src = target if os.path.isabs(target) else os.path.join(home, target)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(work, *sub.split("/")),
                            ignore=shutil.ignore_patterns("__pycache__", ".git", ".pytest_cache"))
            found.append(sub)
    return found


def write_receipt(path, placed, left_out, failed, kind="source_tree", home=None, tested_sha=None) -> None:
    import hashlib
    import json

    def sha(p):
        try:
            with open(p, "rb") as fh:
                return hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            return None
    try:
        commit = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True,
                                text=True, timeout=30).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        commit = None
    receipt = {"artifact_kind": kind, "source": ROOT, "source_commit": commit, "installed_home": home,
               "tested_tree_sha256": tested_sha,
               "installer_sha256": sha(os.path.join(ROOT, "bootstrap", "install.py")),
               "installer_places": placed, "checked_but_not_installed": left_out,
               "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in RESULTS],
               "passed": len(RESULTS) - len(failed), "total": len(RESULTS)}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, indent=1)
    print("receipt: %s" % path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keep", action="store_true",
                    help="keep the temp directory for inspection")
    ap.add_argument("--receipt", metavar="PATH",
                    help="write what was tested (tree kind, commit, installer hash, checks) as JSON")
    ap.add_argument("--installed", metavar="HOME",
                    help="test what bootstrap/install.py placed under HOME (run it with --home HOME --apply first) "
                         "instead of this checkout; a checked component the install did not place fails as absent")
    args = ap.parse_args(argv)

    tmp = tempfile.mkdtemp(prefix="framework_smoke_")
    work = os.path.join(tmp, "framework")
    if args.installed:
        if installer_map() is None or not os.path.isdir(args.installed):
            print("cannot test an install: bootstrap/install.py unreadable or %s is not a directory" % args.installed)
            shutil.rmtree(tmp, ignore_errors=True)
            return 2
        found = copy_installed(os.path.abspath(args.installed), work)
        kind = "installed_tree"
    else:
        shutil.copytree(ROOT, work,
                        ignore=shutil.ignore_patterns("__pycache__", ".git", ".pytest_cache"))
        kind = "source_tree"
    tested_sha = tree_sha256(work)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")

    placed, left_out = installer_coverage()
    print("framework smoke test")
    print("  source : %s" % ROOT)
    if args.installed:
        print("  tested : the install under %s (%d roots copied back from where install.py placed them)"
              % (os.path.abspath(args.installed), len(found)))
    else:
        print("  tested : this checkout (a source tree, not an installed home)")
    if left_out:
        print("  bootstrap/install.py does not place: %s" % ", ".join(left_out))
    print("  tree   : sha256 %s" % tested_sha[:16])
    print("  sandbox: %s\n" % tmp)

    for name, fn in (("memory", check_memory), ("mesh", check_mesh),
                     ("workspace", check_workspace), ("dream", check_dream),
                     ("handover", check_handover), ("math brain", check_mathbrain),
                     ("film brain", check_filmbrain)):
        try:
            fn(work, tmp, env)
        except Exception as exc:                      # noqa: BLE001 - report, do not mask
            record("%s: raised %s" % (name, type(exc).__name__), False, str(exc)[:70])

    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n%d/%d checks passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    if args.receipt:
        write_receipt(args.receipt, placed, left_out, failed, kind=kind,
                      home=os.path.abspath(args.installed) if args.installed else None, tested_sha=tested_sha)
    if args.keep:
        print("sandbox kept at %s" % tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
