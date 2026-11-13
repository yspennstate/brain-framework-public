"""Controls for the embeddings lever of recall_levers.py against this tree's embedding_artifact.load_verified_embeddings.

On a synthetic memory folder and schema-v3 sidecar, with every kind of row the loader judges (stat-current, row_safe
off, a touched file with the same text, changed text, a deleted file, a body too short to embed):

 E1 the lever returns the same rows in the same order, the same matrix, the same bias and the same text hashes as
    the original loader, on a cold verify cache and again on the warm cache the first call wrote
 E2 a control: with embedding_row_current forced to True the lever's answer differs (the comparison can fail)
 E3 this tree's load_verified_embeddings is a proven version, so the lever installs

    python test_emb_lever.py      (a few seconds; ALL PASS at the end; needs numpy; writes only to a temp folder)
"""
import io
import json
import os
import random
import shutil
import sys
import tempfile
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def body(rng, i):
    words = " ".join(rng.choice(["kernel", "spectral", "walk", "bound", "graph", "measure", "limit", "proof"])
                     for _ in range(rng.randint(12, 40)))
    return "---\nname: m%03d\ndescription: \"note %d on %s\"\n---\n\n%s\n" % (i, i, words.split()[0], words)


def build(np, ea, builder, root, rng):
    mem = os.path.join(root, "mem")
    os.makedirs(mem)
    n = rng.randint(8, 40)
    slugs, hashes, sizes, mtimes, safe = [], [], [], [], []
    kinds = []
    for i in range(n):
        slug = "m%03d" % i
        text = body(rng, i)
        p = os.path.join(mem, slug + ".md")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        st = os.stat(p)
        slugs.append(slug)
        hashes.append(builder.text_sha256(builder._text_for(text)))
        sizes.append(st.st_size)
        mtimes.append(st.st_mtime_ns)
        kind = rng.choice(["fast", "fast", "fast", "unsafe", "touched", "changed", "deleted", "short"])
        kinds.append(kind)
        safe.append(kind != "unsafe")
    vecs = np.asarray([[rng.uniform(-1, 1) for _ in range(6)] for _ in range(n)], dtype=np.float32)
    buf = io.BytesIO()
    np.savez(buf, slugs=np.asarray(slugs), vecs=vecs, model=np.asarray("test-model"),
             built_at=np.asarray("2026-11-05T00:00:00Z"), schema_version=np.asarray(ea.RUNTIME_SCHEMA_VERSION),
             text_sha256=np.asarray(hashes), source_size=np.asarray(sizes, dtype=np.int64),
             source_mtime_ns=np.asarray(mtimes, dtype=np.int64), row_safe=np.asarray(safe, dtype=np.bool_))
    blob = buf.getvalue()
    side = os.path.join(root, "memory_embeddings.npz")
    with open(side, "wb") as f:
        f.write(blob)
    auth = os.path.join(root, "embeddings_current.json")
    with open(auth, "w", encoding="utf-8") as f:
        json.dump({"schema": ea.AUTHORITY_SCHEMA, "committed_at": "2026-11-05T00:00:01Z", "sidecar": side,
                   "sidecar_sha256": hashlib.sha256(blob).hexdigest(), "sidecar_size": len(blob),
                   "model": "test-model", "built_at": "2026-11-05T00:00:00Z"}, f)
    for slug, kind in zip(slugs, kinds):
        p = os.path.join(mem, slug + ".md")
        st = os.stat(p)
        if kind == "touched":
            os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 7_000_000_000))
        elif kind == "changed":
            with open(p, "a", encoding="utf-8") as f:
                f.write("\nan added line that changes the embedded text\n")
        elif kind == "deleted":
            os.remove(p)
        elif kind == "short":
            with open(p, "w", encoding="utf-8") as f:
                f.write("tiny\n")
    return side, auth, mem


def entries(mem):
    with os.scandir(mem) as it:
        return [e for e in it if e.name.endswith(".md") and e.is_file()]


def view(x):
    if x is None:
        return None
    return (list(x.index.items()), x.matrix.tobytes(), x.bias, sorted(x.text_hashes.items()))


def main():
    import numpy as np
    import embedding_artifact as ea
    import build_memory_embeddings as builder
    import recall_levers as rl
    real = ea.load_verified_embeddings
    check(rl._proven("emb", real), "E3 this tree's load_verified_embeddings is a proven version")
    rl._install_emb(ea, np)
    lever = ea.load_verified_embeddings
    rng = random.Random(20261105)
    tmp = tempfile.mkdtemp(prefix="emb_lever_")
    old_env = os.environ.get("BRAIN_EMB_VERIFY_DIR")
    same = total = active_rows = 0
    caught = False
    try:
        for t in range(30):
            root = os.path.join(tmp, "t%02d" % t)
            os.makedirs(root)
            side, auth, mem = build(np, ea, builder, root, rng)
            got = {}
            for arm, f in (("real", real), ("lever", lever)):
                os.environ["BRAIN_EMB_VERIFY_DIR"] = os.path.join(root, "vc_" + arm)
                ea._RES.clear()
                rl._RES.clear()
                cold = f(side, auth, mem, memory_entries=entries(mem))
                warm = f(side, auth, mem, memory_entries=entries(mem))
                got[arm] = (view(cold), view(warm))
            total += 2
            same += (got["real"][0] == got["lever"][0]) + (got["real"][1] == got["lever"][1])
            if got["real"][0]:
                active_rows += len(got["real"][0][0])
            if t == 0:
                keep = ea.embedding_row_current
                ea.embedding_row_current = lambda *a, **k: True
                try:
                    os.environ["BRAIN_EMB_VERIFY_DIR"] = os.path.join(root, "vc_control")
                    rl._RES.clear()
                    bad = view(lever(side, auth, mem, memory_entries=entries(mem)))
                finally:
                    ea.embedding_row_current = keep
                caught = bad != got["real"][0]
        check(same == total and active_rows > 0,
              "E1 lever equals the original loader on %d of %d calls (cold and warm cache, %d active rows)"
              % (same, total, active_rows))
        check(rl.STATS["emb"][0] >= 60, "E1 every lever call was served by the fast form (%d)" % rl.STATS["emb"][0])
        check(caught, "E2 control: a loader that accepts every row is caught")
    finally:
        ea.load_verified_embeddings = real
        if old_env is None:
            os.environ.pop("BRAIN_EMB_VERIFY_DIR", None)
        else:
            os.environ["BRAIN_EMB_VERIFY_DIR"] = old_env
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
        print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
        raise SystemExit(0)
    sys.exit(main())
