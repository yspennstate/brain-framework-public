"""compact_nodes._overlay_vectors starts the fresh-vector overlay after an anchor is written, and stays quiet in a test
folder or when switched off. Runs a copy of compact_nodes.py beside a stand-in fresh_vectors.py that only drops a
marker file, so no real overlay starts. POSIX only (the call is a no-op on Windows by design)."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
res = []


def ok(name, cond, info=""):
    res.append(bool(cond))
    print("%s %s %s" % ("PASS" if cond else "FAIL", name, info))


if os.name == "nt":
    print("SKIP: POSIX only")
    sys.exit(0)

work = tempfile.mkdtemp(prefix="cn_overlay_")
try:
    shutil.copy2(os.path.join(HERE, "compact_nodes.py"), work)
    marker = os.path.join(work, "spawned")
    with open(os.path.join(work, "fresh_vectors.py"), "w") as fh:
        fh.write("import os\n\ndef spawn_detached():\n    open(%r, 'a').write('x')\n    return 1\n" % marker)
    probe = "import sys; sys.path.insert(0, %r); import compact_nodes as c; c._overlay_vectors()" % work
    base = {k: v for k, v in os.environ.items()
            if k not in ("COMPACT_NODES_ROOT", "COMPACT_ANCHOR_DIR", "COMPACT_NO_VECTORS")}
    base["HOME"] = work          # keep the module's default paths inside the scratch folder

    def run(extra):
        if os.path.exists(marker):
            os.remove(marker)
        env = dict(base, **extra)
        r = subprocess.run([sys.executable, "-B", "-c", probe], env=env, capture_output=True, text=True, timeout=60)
        return r.returncode, os.path.exists(marker), r.stderr.strip()[-200:]

    rc, hit, err = run({})
    ok("overlay starts when no switch is set", rc == 0 and hit, err)
    rc, hit, err = run({"COMPACT_NO_VECTORS": "1"})
    ok("COMPACT_NO_VECTORS=1 keeps it off", rc == 0 and not hit, err)
    rc, hit, err = run({"COMPACT_NODES_ROOT": os.path.join(work, "root")})
    ok("a test store (COMPACT_NODES_ROOT) keeps it off", rc == 0 and not hit, err)
    rc, hit, err = run({"COMPACT_ANCHOR_DIR": os.path.join(work, "anchors")})
    ok("a test anchor folder (COMPACT_ANCHOR_DIR) keeps it off", rc == 0 and not hit, err)
    os.remove(os.path.join(work, "fresh_vectors.py"))
    rc, hit, err = run({})
    ok("a missing fresh_vectors.py is swallowed", rc == 0 and not hit, err)
finally:
    shutil.rmtree(work, ignore_errors=True)
print("RESULT %d/%d" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
