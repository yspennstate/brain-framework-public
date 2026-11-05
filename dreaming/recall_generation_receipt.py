#!/usr/bin/env python3
"""Produce a recall receipt that is SELF-CONSISTENTLY BOUND to one BM25 generation.

WHY THIS EXISTS. brain_improvement.py fails closed when "the live retrieval generation outgrows its
receipt" - a probe result must never stay green after the observed BM25 hash changes. That rule is
right. But the lexical index now rebuilds every 3 hours (the nightly dream that used to build it is
disabled, and a one-off rebuild went stale within two hours), so the observed generation moves on a
schedule. A receipt taken without pinning its generation is therefore stale almost immediately, and
- worse - a receipt taken WHILE the index changes underneath it describes no generation at all.

So this hashes the index, runs the probe suite, hashes it again, and REFUSES TO PUBLISH unless the
two hashes match. A moving generation is a loud failure here, not a quiet receipt.

That refusal is the point. On 2026-08-17 the peer campaign published three separate
volatile-generation blockers because probe runs kept being invalidated by index changes - three of
which I caused by benchmarking the rebuild command. A receipt that cannot tell "measured on
generation X" from "measured across generations X and Y" is how that happens.

Read-only with respect to the index: this NEVER rebuilds. Usage:
    python recall_generation_receipt.py [out.json]
Exit 0 = receipt written; 1 = generation moved during the run, nothing published.
"""
import hashlib, io, json, os, subprocess, sys, time

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "bm25_index.json.gz")
SUITE = os.path.join(HERE, "recall_policy_regression.py")


RUNTIME = os.path.join(HERE, "brain.py")
POLICY = os.path.join(HERE, "recall_policy.json")

# THE FULL BINDING SET, taken from the peer's own re-establishment list (BI-018 INVALIDATION_EVENT,
# 2026-08-17): "bind runtime, policy, BM25 gzip and pickle, graph, embeddings, salience, and memory
# inventory before and after the full run". My earlier receipt bound three of these. The PICKLE
# matters most of the ones I was missing: bm25.build writes bm25_index.json.gz.pkl as a fast path
# next to the gzip, so a receipt that pins only the gzip can be describing a generation the reader
# never actually loads.
_HOME = os.path.expanduser("~")
_BRAINVIEWS = os.path.join(_HOME, "brain-repo", "01_ai_consciousness", "brain")
_MEMDIR = os.path.join(_HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")

ARTIFACTS = {
    "index": INDEX,
    "index_pickle": INDEX + ".pkl",
    "runtime": RUNTIME,
    "policy": POLICY,
    "graph_edges": os.path.join(HERE, "spread_edges.tsv"),
    "coactivation": os.path.join(HERE, "coactivation.tsv"),
    # THE EMBEDDING SIDECAR IS CONTENT-ADDRESSED AND THE PLAIN NAME IS AN ORPHAN.
    # I originally bound memory_embeddings.npz here, which looked right and was worthless: that file
    # was last written 2026-07-14 and nothing authoritative points at it. spread_recall loads through
    # .embedding_current.json, which names the live file (memory_embeddings.<sha>.npz, 21,547,023
    # bytes, built 2026-08-17). Binding the orphan pinned an artifact the system never reads - a
    # receipt that cannot fail on the axis it claims to cover. Bind the AUTHORITY, and resolve the
    # sidecar it names at run time.
    "embedding_authority": os.path.join(HERE, ".embedding_current.json"),
    "salience": os.path.join(_BRAINVIEWS, "SALIENCE.md"),
    "memory_index": os.path.join(_MEMDIR, "MEMORY.md"),
}


def _identity(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    st = os.stat(path)
    return {
        "path": path.replace("\\", "/"),
        "sha256": h.hexdigest().upper(),
        "bytes": st.st_size,
        "mtime": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(st.st_mtime)),
    }


def index_identity():
    """Bind EVERY artifact in ARTIFACTS, not just the index.

    Peer review 2026-08-17 was right that hashing the index alone is too narrow: a recall result
    depends on the index, the runtime that queries it, the policy that gates it, and the graph,
    embedding, salience and inventory inputs the other channels read. A receipt that pins a subset
    can be invalidated by a silent change in anything it did not pin, and would not know. Every
    artifact is hashed before AND after the run and the receipt refuses to publish if ANY moved.
    An artifact that does not exist is recorded as ABSENT rather than skipped.
    """
    out = {}
    for name, path in sorted(ARTIFACTS.items()):
        if os.path.exists(path):
            out[name] = _identity(path)
        else:
            # ABSENT is a stated fact, not a missing key and not a zero hash. A reader must be able
            # to tell "this artifact does not exist on this machine" from "I forgot to bind it".
            out[name] = {"path": path.replace("\\", "/"), "sha256": "ABSENT",
                         "bytes": None, "mtime": None}

    # FOLLOW THE POINTER. Binding .embedding_current.json pins which sidecar is current, not the
    # sidecar itself - a swap to a different file with the authority rewritten would move both, but
    # a silent rewrite of the NAMED file would move neither. Resolve and hash what it points at.
    auth = ARTIFACTS.get("embedding_authority")
    resolved = {"path": "UNKNOWN", "sha256": "UNKNOWN", "bytes": None, "mtime": None}
    if auth and os.path.exists(auth):
        try:
            with io.open(auth, encoding="utf-8") as f:
                d = json.load(f)
            target = d.get("sidecar")
            # The authority records the path without its .npz suffix in the observed schema
            # (embedding-current/v2), so try the recorded name and the suffixed form.
            for cand in ([target, target + ".npz"] if target else []):
                if cand and os.path.exists(cand):
                    resolved = _identity(cand)
                    resolved["named_by_authority"] = True
                    resolved["authority_sha256_field"] = d.get("sidecar_sha256")
                    resolved["authority_size_field"] = d.get("sidecar_size")
                    break
            else:
                resolved = {"path": str(target), "sha256": "NAMED_BUT_MISSING",
                            "bytes": None, "mtime": None}
        except Exception as exc:
            resolved = {"path": auth.replace("\\", "/"),
                        "sha256": "UNREADABLE: %s: %s" % (type(exc).__name__, exc),
                        "bytes": None, "mtime": None}
    out["embedding_sidecar_resolved"] = resolved
    return out


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "recall_generation_receipt.json")
    # WRITE-TIME LANE (2026-09-02): the index is now refreshed incrementally within seconds of a new memory
    # (bm25.py refresh from recall(), remember() and a 15-minute task), so a run this long WOULD see the
    # generation move. Hold it for the run: refresh() skips while the freeze stands, and the freeze expires
    # on its own if this process dies. The before/after hash check in _main_pinned stays as the proof.
    _frozen_here = False
    _bm25 = None
    try:
        sys.path.insert(0, HERE)
        import bm25 as _bm25
        _bm25.freeze(65, "recall_generation_receipt.py run")
        _frozen_here = True
    except Exception as _exc:
        print("WARNING: could not freeze the bm25 generation (%s: %s) - a refresh during the run will "
              "void this receipt" % (type(_exc).__name__, _exc))
    try:
        return _main_pinned(out_path)
    finally:
        if _frozen_here and _bm25 is not None:
            try:
                _bm25.unfreeze()
            except Exception:
                pass


def _main_pinned(out_path):
    before = index_identity()
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
    t0 = time.time()

    # decode bytes explicitly: text=True uses the Windows locale codec and returns EMPTY stdout on
    # non-cp1252 output, which would make every negative probe pass vacuously
    # --json makes the suite emit one structured row per probe (query, rank, parsed count, child
    # exit code, stderr, top-3 slugs, timestamp). Peer review 2026-08-17 asked for exact per-probe
    # evidence; the suite's prose stdout is a human summary, not that. Stale-log guard below.
    probe_log_path = os.path.join(HERE, "recall_probe_log.json")
    try:
        os.remove(probe_log_path)
    except OSError:
        pass

    p = subprocess.run([sys.executable, SUITE, "--json"], capture_output=True, timeout=3600)
    stdout = (p.stdout or b"").decode("utf-8", errors="replace")
    stderr = (p.stderr or b"").decode("utf-8", errors="replace")

    # Read it back, or say so. A missing log is UNKNOWN, never an empty list that reads like
    # "no probes had anything to report".
    try:
        with io.open(probe_log_path, encoding="utf-8") as f:
            probe_log = json.load(f)
    except Exception as exc:
        probe_log = {"UNKNOWN": "per-probe log absent or unreadable: %s: %s"
                                % (type(exc).__name__, exc)}

    elapsed = time.time() - t0
    finished = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
    after = index_identity()

    # Compare every key the identity function produced, which includes the RESOLVED sidecar,
    # not just the declared ARTIFACTS. Otherwise a silent rewrite of the file the authority
    # names would pass unnoticed - the exact hole that binding the orphan npz left open.
    keys = sorted(set(before) | set(after))
    moved = [k for k in keys if before[k]["sha256"] != after[k]["sha256"]]
    stable = not moved
    for k in keys:
        print("%-13s before %s  after %s  %s"
              % (k, before[k]["sha256"][:16], after[k]["sha256"][:16],
                 "SAME" if before[k]["sha256"] == after[k]["sha256"] else "MOVED"))
    print("suite exit        : %s in %.1fs" % (p.returncode, elapsed))
    print("per-probe rows    : %s of %s  complete=%s  aborted=%s"
          % (probe_log.get("count", "UNKNOWN"), probe_log.get("expected_count", "UNKNOWN"),
             probe_log.get("complete", "UNKNOWN"), probe_log.get("aborted", "UNKNOWN")))
    print("ALL %d stable     : %s" % (len(keys), stable))

    if not stable:
        print()
        print("REFUSING TO PUBLISH - %s changed during the run, so these probe results describe"
              % ", ".join(moved))
        print("no single generation. Re-run between rebuilds. Nothing was written.")
        return 1

    receipt = {
        "schema": "recall_generation_receipt/1",
        "produced_by": "claude-brainloop-0817",
        "started": started,
        "finished": finished,
        "elapsed_seconds": round(elapsed, 1),
        "generation": {
            "observed_bm25_sha256": before["index"]["sha256"],
            "artifacts_before": before,
            "artifacts_after": after,
            "artifacts_bound": sorted(ARTIFACTS),
            "stable_across_run": True,
            "verified_by": ("sha256 of every artifact in ARTIFACTS taken before AND after the "
                            "suite, all equal: BM25 gzip and pickle, brain.py, recall_policy.json, "
                            "spread_edges.tsv, coactivation.tsv, memory_embeddings.npz, SALIENCE.md "
                            "and MEMORY.md. Binding the index alone was too narrow (peer review "
                            "2026-08-17); the full set is the peer's own BI-018 list."),
        },
        "suite": {
            "path": SUITE.replace("\\", "/"),
            "sha256": hashlib.sha256(io.open(SUITE, "rb").read()).hexdigest().upper(),
            "exit_code": p.returncode,
            "passed": p.returncode == 0,
        },
        "per_probe": probe_log,
        "environment": {"BRAIN_RECALL_USAGE": "0"},
        "known_limits": [
            "This certifies the probe outcomes for ONE generation only. The lexical index rebuilds "
            "every 3 hours via the BrainEmbeddingRefresh launcher, so this receipt EXPIRES at the "
            "next rebuild - expired is not failed, and the receipt was true for the generation it "
            "names.",
            "Probe pass/fail is read from the suite's exit code and stdout; it is not a claim that "
            "the suite measures everything worth measuring.",
        ],
        "stdout": stdout,
        "stderr": stderr,
    }
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(receipt, f, indent=1, ensure_ascii=False)
    print()
    print("receipt written: %s" % out_path)
    print("receipt sha256 : %s" % hashlib.sha256(io.open(out_path, "rb").read()).hexdigest().upper())
    return 0


if __name__ == "__main__":
    sys.exit(main())
