#!/usr/bin/env python3
"""Build the offline semantic sidecar consumed by ``spread_recall``.

The production model is BAAI/bge-small-en-v1.5. Run the build where the model fits in memory; on a
small or busy machine, build elsewhere and copy the NPZ.  The online hook only loads the resulting ~2 MB NPZ and performs dot
products, so no model is needed at recall time.

The model-output staging schema v2 is deliberately self-auditing::

    slugs: array[str]
    vecs: float32 [N, D], finite and L2-normalized
    model: str
    text_sha256: array[str]   # hash of the exact canonical text embedded for each row
    built_at: str             # UTC ISO-8601
    schema_version: int       # 2

The Windows publisher adds digest-bound local activation metadata and emits runtime schema v3. The
staging hashes make incremental refresh exact rather than mtime-based.  With
``--incremental-from``, rows are reused only when model, slug, and canonical-text hash all
match; new or changed rows alone are encoded, and deleted rows disappear.  A legacy v1
sidecar has no hashes, so the first v2 refresh correctly performs one full rebuild.

Writes are staged and atomic: a fully validated temporary NPZ is fsynced and only then replaces
``--out``. Direct output to the fixed live pathname is refused. Production activation must go
through ``publish_memory_embeddings.py``, which creates an immutable content-addressed generation
and atomically commits its authority pointer. See ``EMBEDDING_PUBLICATION.md``.

Usage::

    python build_memory_embeddings.py [memory_dir]
    python build_memory_embeddings.py [memory_dir] \
        --incremental-from memory_embeddings.npz --out memory_embeddings.next.npz
    python build_memory_embeddings.py [memory_dir] \
        --incremental-from memory_embeddings.npz --plan-only

Actual BGE encoding is box-gated.  Planning, coverage checks, and the fake-encoder
regression are laptop-safe and do not import sentence-transformers.
"""
import argparse
import hashlib
import io
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone


MEM_DEFAULT = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
DR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(DR, "memory_embeddings.npz")
STAGED_OUT = os.path.join(DR, "memory_embeddings.next.npz")
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
SCHEMA_VERSION = 2

# Special pages are brain state/indexes rather than memories.  Keep this set in lockstep
# with the local LSA builder so both sidecars cover the same semantic corpus.
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES", "CONSCIENCE", "CURIOSITY",
        "SALIENCE", "GROWTH", "RECENT_BRAIN", "STATS", "RECONSOLIDATION_QUEUE"}
FM = re.compile(r"^---\n.*?\n---\n", re.S)


class EncoderUnavailable(RuntimeError):
    """The heavy sentence encoder is unavailable in this environment."""


def _text_for(body):
    """Return the exact text embedded for a memory.

    The description plus the first ~2,000 prose characters carries the claim while avoiding
    giant-memory domination.  This contract is shared with the LSA baseline.
    """
    desc = ""
    m = re.search(r'description:\s*"?(.+?)"?\s*\n', body)
    if m:
        desc = m.group(1).strip() + ". "
    prose = FM.sub("", body).strip()
    return (desc + prose)[:2000]


def text_sha256(text):
    """Stable content identity for the exact UTF-8 text handed to the encoder."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def same_file_target(left, right):
    """True when two spellings reach the same file, including Windows junction aliases."""
    a = os.path.normcase(os.path.realpath(os.path.abspath(left)))
    b = os.path.normcase(os.path.realpath(os.path.abspath(right)))
    if a == b:
        return True
    if os.path.exists(left) and os.path.exists(right):
        try:
            return os.path.samefile(left, right)
        except OSError:
            pass
    return False


COLLECT_CHURN_RETRIES = 5   # 2026-09-14: a fresh snapshot per attempt; fail closed only after five consecutive churns


def collect_memories(mem_dir=MEM_DEFAULT, limit=None, strict=True):
    """Collect a stable corpus snapshot in deterministic order, retrying the WHOLE snapshot when the corpus
    moves under it.

    An unreadable or concurrently changing memory is not absent. Production callers fail closed instead of
    silently shrinking the corpus and certifying a partial sidecar as complete. But at night several sessions
    write memories every few minutes, and one attempt over ~16k files takes long enough that "membership
    changed while it was collected" killed the 3-hourly refresh on 09-13 22:30 and 09-14 01:2x with no
    partial state to reconcile: every attempt is a fresh, complete snapshot or nothing. Five consecutive
    churns still fail closed with the original error."""
    last = None
    for attempt in range(COLLECT_CHURN_RETRIES if strict else 1):
        try:
            return _collect_memories_once(mem_dir, limit=limit, strict=strict)
        except OSError as exc:
            if not strict or "changed while" not in str(exc):
                raise
            last = exc
            time.sleep(2.0 * (attempt + 1))
    raise last


COLLECT_RECONCILE_ROUNDS = 8   # 2026-10-18: membership churn is reconciled by name, not by re-reading the corpus
CHANGED_READ_RETRIES = 3


def _eligible(mem_dir):
    return sorted(fn for fn in os.listdir(mem_dir) if fn.endswith(".md") and fn[:-3] not in SKIP)


def _read_row(mem_dir, fn, strict):
    """(slug, text, sha) for one memory read with no change under the read; None when too short or skipped."""
    path = os.path.join(mem_dir, fn)
    for attempt in range(CHANGED_READ_RETRIES if strict else 1):
        try:
            before = os.stat(path)
            body = io.open(path, encoding="utf-8", errors="replace").read()
            after = os.stat(path)
        except FileNotFoundError:
            return "gone"
        except Exception as exc:
            if strict:
                raise OSError("could not read corpus memory %s: %s" % (fn, exc)) from exc
            return None
        before_sig = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        after_sig = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if before_sig == after_sig:
            text = _text_for(body)
            return (fn[:-3], text, text_sha256(text)) if len(text) >= 40 else None
        if strict and attempt + 1 < CHANGED_READ_RETRIES:
            time.sleep(0.5)
    if strict:
        raise OSError("corpus memory changed while read: %s" % fn)
    return None


def _collect_memories_once(mem_dir=MEM_DEFAULT, limit=None, strict=True):
    """One snapshot attempt; see collect_memories for the retry contract.

    18 Oct 2026: one pass over ~28k files took ~9 min on a busy disk, and agents file a memory every few minutes, so
    almost every pass ended with "membership changed" and was thrown away whole (the 13:20 refresh read the corpus
    for 20+ min; embedding_coverage.py timed out at 600 s on every run that day). Now a pass that ends with a
    different membership reads only the names that arrived, drops the ones that left, and checks again; the result
    is still exactly the final listing's membership, every row read with no change under its read."""
    rows = {}
    eligible_names = _eligible(mem_dir)
    for fn in eligible_names:
        row = _read_row(mem_dir, fn, strict)
        if row == "gone":
            continue                      # removed mid-pass; the membership check below settles it
        if row:
            rows[fn] = row
        if limit and len(rows) >= limit:
            return [rows[k] for k in sorted(rows)]
    if strict and limit is None:
        for _ in range(COLLECT_RECONCILE_ROUNDS):
            after_names = _eligible(mem_dir)
            if after_names == eligible_names:
                break
            now = set(after_names)
            for fn in [k for k in rows if k not in now]:
                del rows[fn]
            for fn in sorted(now - set(eligible_names)):
                row = _read_row(mem_dir, fn, strict)
                if row and row != "gone":
                    rows[fn] = row
            eligible_names = after_names
        else:
            raise OSError("memory corpus membership changed while it was collected")
        missing = [fn for fn in eligible_names if fn not in rows and os.path.exists(os.path.join(mem_dir, fn))]
        for fn in missing:                # read as "gone" mid-pass but listed at the end: read it now
            row = _read_row(mem_dir, fn, strict)
            if row and row != "gone":
                rows[fn] = row
    return [rows[k] for k in sorted(rows)]


def _scalar(z, name, default=None):
    if name not in z.files:
        return default
    value = z[name]
    try:
        return value.item()
    except Exception:
        return default


def _load_reusable(path, expected_model, authority_binding=None):
    """Load reusable v2 rows.

    Returns ``(rows, old_slugs, dim, reason)``.  Any ambiguity degrades to a full rebuild;
    stale or corrupt vectors are never silently carried forward.
    """
    import numpy as np

    if not path:
        return {}, set(), None, "no incremental sidecar requested"
    if not os.path.exists(path):
        return {}, set(), None, "incremental sidecar does not exist"
    try:
        if authority_binding is not None:
            import embedding_artifact
            blob, digest, size, _ = embedding_artifact.read_stable_bytes(path)
            if (digest != authority_binding.get("sidecar_sha256")
                    or size != authority_binding.get("sidecar_size")):
                raise ValueError("active incremental generation failed authority digest binding")
            source = io.BytesIO(blob)
        else:
            source = path
        with np.load(source, allow_pickle=False) as z:
            if not {"slugs", "vecs", "model"}.issubset(set(z.files)):
                return {}, set(), None, "incremental sidecar is missing core arrays"
            slugs = [str(s) for s in z["slugs"]]
            vecs = np.asarray(z["vecs"]).copy()
            model = str(_scalar(z, "model", ""))
            hashes = [str(h) for h in z["text_sha256"]] if "text_sha256" in z.files else None
            schema = int(_scalar(z, "schema_version", 1))
            built_at = str(_scalar(z, "built_at", ""))
    except Exception as exc:
        if authority_binding is not None:
            raise ValueError("authority-bound incremental source is invalid: %s" % exc) from exc
        return {}, set(), None, "incremental sidecar unreadable: %s" % exc

    if authority_binding is not None and (
            model != authority_binding.get("model") or built_at != authority_binding.get("built_at")):
        raise ValueError("active incremental generation failed authority metadata binding")

    old_slugs = set(slugs)
    if model != expected_model:
        return {}, old_slugs, None, "model changed (%s -> %s); full rebuild" % (model, expected_model)
    if schema < SCHEMA_VERSION or hashes is None:
        return {}, old_slugs, None, "legacy sidecar has no v2 content hashes; full rebuild"
    if len(slugs) != len(set(slugs)):
        return {}, old_slugs, None, "incremental sidecar contains duplicate slugs; full rebuild"
    if vecs.ndim != 2 or vecs.shape[0] != len(slugs) or len(hashes) != len(slugs):
        return {}, old_slugs, None, "incremental sidecar arrays are misaligned; full rebuild"
    if vecs.dtype != np.float32 or not np.isfinite(vecs).all():
        return {}, old_slugs, None, "incremental vectors are invalid; full rebuild"
    norms = np.linalg.norm(vecs, axis=1)
    if len(norms) and not np.allclose(norms, 1.0, atol=1e-3, rtol=1e-3):
        return {}, old_slugs, None, "incremental vectors are not unit-normalized; full rebuild"

    rows = {slug: (hashes[i], vecs[i].copy()) for i, slug in enumerate(slugs)}
    return rows, old_slugs, int(vecs.shape[1]), "v2 sidecar is reusable"


def plan_refresh(mem_dir=MEM_DEFAULT, model_name=DEFAULT_MODEL, incremental_from=None, limit=None,
                 incremental_binding=None):
    """Create a pure, model-free incremental plan used by production and regression tests."""
    corpus = collect_memories(mem_dir, limit=limit)
    if not corpus:
        raise ValueError("no memories with enough text to embed")
    old_rows, old_slugs, old_dim, reason = _load_reusable(
        incremental_from, model_name, incremental_binding
    )
    current = {slug for slug, _, _ in corpus}
    reuse, encode = {}, []
    for slug, text, digest in corpus:
        old = old_rows.get(slug)
        if old is not None and old[0] == digest:
            reuse[slug] = old[1]
        else:
            encode.append((slug, text, digest))
    return {
        "corpus": corpus,
        "reuse": reuse,
        "encode": encode,
        "dropped": sorted(old_slugs - current),
        "old_dim": old_dim,
        "reason": reason,
        "model": model_name,
    }


def _validate_sidecar(path, expected_slugs, expected_hashes, expected_model):
    """Raise if a staged v2 artifact is not exactly safe to promote."""
    import numpy as np

    with np.load(path, allow_pickle=False) as z:
        required = {"slugs", "vecs", "model", "text_sha256", "built_at", "schema_version"}
        missing = required - set(z.files)
        if missing:
            raise ValueError("staged sidecar missing arrays: %s" % sorted(missing))
        slugs = [str(s) for s in z["slugs"]]
        hashes = [str(h) for h in z["text_sha256"]]
        vecs = np.asarray(z["vecs"])
        model = str(_scalar(z, "model", ""))
        built_at = str(_scalar(z, "built_at", ""))
        schema = int(_scalar(z, "schema_version", 0))

    if schema != SCHEMA_VERSION:
        raise ValueError("staged sidecar schema is not v2")
    if model != expected_model:
        raise ValueError("staged sidecar model mismatch")
    if not built_at:
        raise ValueError("staged sidecar has no built_at timestamp")
    if slugs != list(expected_slugs) or hashes != list(expected_hashes):
        raise ValueError("staged sidecar does not exactly match the current corpus")
    if len(slugs) != len(set(slugs)):
        raise ValueError("staged sidecar contains duplicate slugs")
    if vecs.dtype != np.float32 or vecs.ndim != 2 or vecs.shape[0] != len(slugs) or vecs.shape[1] < 1:
        raise ValueError("staged vector matrix has the wrong shape or dtype")
    if not np.isfinite(vecs).all():
        raise ValueError("staged vector matrix contains NaN/inf")
    norms = np.linalg.norm(vecs, axis=1)
    if len(norms) and not np.allclose(norms, 1.0, atol=1e-3, rtol=1e-3):
        raise ValueError("staged vectors are not L2-normalized")
    return int(vecs.shape[1])


def _atomic_write(out, slugs, vecs, model_name, hashes):
    """Write, validate, fsync, then atomically promote a sidecar."""
    import numpy as np

    out = os.path.abspath(out)
    out_dir = os.path.dirname(out)
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".%s." % os.path.basename(out), suffix=".npz", dir=out_dir)
    os.close(fd)
    try:
        np.savez_compressed(
            tmp,
            slugs=np.asarray(slugs),
            vecs=np.asarray(vecs, dtype=np.float32),
            model=np.asarray(model_name),
            text_sha256=np.asarray(hashes),
            built_at=np.asarray(datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")),
            schema_version=np.asarray(SCHEMA_VERSION, dtype=np.int64),
        )
        dim = _validate_sidecar(tmp, slugs, hashes, model_name)
        # Windows' CRT rejects fsync on a read-only descriptor; r+b grants the writable handle
        # fsync requires without changing the already-validated bytes.
        with open(tmp, "r+b") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, out)
        return dim
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def build_sidecar(mem_dir=MEM_DEFAULT, model_name=DEFAULT_MODEL, out=OUT, incremental_from=None,
                  limit=None, encoder_factory=None, plan=None, verbose=False):
    """Execute an incremental plan and atomically write a v2 sidecar.

    ``encoder_factory(model_name)`` must return a callable accepting ``list[str]`` and returning
    a normalized ``float32 [N, D]`` array.  Tests inject a tiny deterministic fake; production
    supplies sentence-transformers on the box.
    """
    import numpy as np

    if same_file_target(out, OUT):
        raise ValueError(
            "refusing direct live embedding output; build a staged NPZ and use "
            "publish_memory_embeddings.py"
        )

    plan = plan or plan_refresh(mem_dir, model_name, incremental_from, limit)
    todo = plan["encode"]
    if verbose:
        print("embedding plan: %d reuse, %d encode, %d drop (%s)" %
              (len(plan["reuse"]), len(todo), len(plan["dropped"]), plan["reason"]))

    fresh = None
    if todo:
        if encoder_factory is None:
            raise EncoderUnavailable("no encoder factory supplied for %d new/changed rows" % len(todo))
        encoder = encoder_factory(model_name)
        fresh = np.asarray(encoder([text for _, text, _ in todo]))
        if fresh.dtype != np.float32:
            fresh = fresh.astype(np.float32)
        if fresh.ndim != 2 or fresh.shape[0] != len(todo) or fresh.shape[1] < 1:
            raise ValueError("encoder returned %r for %d texts" % (fresh.shape, len(todo)))
        if not np.isfinite(fresh).all():
            raise ValueError("encoder returned NaN/inf")

    dim = plan["old_dim"]
    if fresh is not None:
        if dim is not None and fresh.shape[1] != dim:
            raise ValueError("encoder dimension %d does not match reusable dimension %d" %
                             (fresh.shape[1], dim))
        dim = int(fresh.shape[1])
    if dim is None:
        raise ValueError("could not determine embedding dimension")

    fresh_by_slug = {slug: fresh[i] for i, (slug, _, _) in enumerate(todo)} if fresh is not None else {}
    slugs, hashes, vectors = [], [], []
    for slug, _, digest in plan["corpus"]:
        slugs.append(slug)
        hashes.append(digest)
        vectors.append(plan["reuse"].get(slug, fresh_by_slug.get(slug)))
    if any(v is None for v in vectors):
        raise ValueError("incremental assembly left a memory without a vector")
    matrix = np.vstack(vectors).astype(np.float32, copy=False)
    written_dim = _atomic_write(out, slugs, matrix, model_name, hashes)
    return {
        "out": os.path.abspath(out),
        "total": len(slugs),
        "reused": len(plan["reuse"]),
        "encoded": len(todo),
        "dropped": len(plan["dropped"]),
        "dim": written_dim,
        "model": model_name,
    }


def _box_encoder_factory(model_name):
    """Load the heavy model only after planning proves rows actually need encoding."""
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:
        raise EncoderUnavailable(
            "sentence-transformers is not importable here; pip install sentence-transformers, or run the "
            "encode step on a machine that has it and copy the NPZ"
        ) from exc
    model = SentenceTransformer(model_name)

    def encode(texts):
        return model.encode(texts, batch_size=64, convert_to_numpy=True, normalize_embeddings=True,
                            show_progress_bar=False).astype("float32")
    return encode


def _print_plan(plan):
    print("embedding plan: %d total; %d reuse, %d encode, %d drop" %
          (len(plan["corpus"]), len(plan["reuse"]), len(plan["encode"]), len(plan["dropped"])))
    print("  %s" % plan["reason"])
    if plan["encode"]:
        print("  encode sample: %s" % ", ".join(row[0] for row in plan["encode"][:8]))
    if plan["dropped"]:
        print("  drop sample: %s" % ", ".join(plan["dropped"][:8]))


def _args(argv):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("memory_dir", nargs="?", default=MEM_DEFAULT)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--limit", type=int)
    p.add_argument("--out", default=STAGED_OUT,
                   help="staged output only (default: %(default)s); live output is refused")
    p.add_argument("--incremental-from", dest="incremental_from")
    p.add_argument("--plan-only", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = _args(sys.argv[1:] if argv is None else argv)
    incremental_from = args.incremental_from
    incremental_binding = None
    authority_path = os.path.join(DR, ".embedding_current.json")
    try:
        import embedding_artifact
        if os.path.exists(authority_path):
            active_path, active_binding = embedding_artifact.resolve_authority_sidecar(
                OUT, authority_path
            )
            requested = os.path.abspath(incremental_from) if incremental_from else None
            if requested is None or requested.casefold() in {
                    os.path.abspath(OUT).casefold(), os.path.abspath(active_path).casefold()}:
                incremental_from = active_path
                incremental_binding = active_binding
    except Exception as exc:
        print("build_memory_embeddings: active authority could not be resolved: %s" % exc)
        return 2
    try:
        plan = plan_refresh(
            args.memory_dir, args.model, incremental_from, args.limit, incremental_binding
        )
    except Exception as exc:
        print("build_memory_embeddings: plan failed: %s" % exc)
        return 2
    _print_plan(plan)
    if args.plan_only:
        return 0
    try:
        report = build_sidecar(
            args.memory_dir,
            model_name=args.model,
            out=args.out,
            incremental_from=incremental_from,
            limit=args.limit,
            encoder_factory=_box_encoder_factory,
            plan=plan,
        )
    except EncoderUnavailable as exc:
        print("build_memory_embeddings: %s." % exc)
        print("  Production BGE generation is box-gated; no sidecar was written or replaced.")
        return 2
    except Exception as exc:
        print("build_memory_embeddings: refresh failed: %s" % exc)
        print("  Atomic promotion did not occur; the prior sidecar (if any) is intact.")
        return 2
    print("wrote %(out)s: %(total)d vectors x %(dim)d (%(model)s); "
          "reused=%(reused)d encoded=%(encoded)d dropped=%(dropped)d" % report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
