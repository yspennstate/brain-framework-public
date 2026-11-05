"""Meaning vectors for the memories the sidecar does not cover yet (R13, claude-1789621d, 13 Oct 2026).

The sidecar (memory_embeddings.npz) is rebuilt every three hours by BrainEmbeddingRefresh. A memory written in
between has no current row, so brain.py's query-meaning lane scores it on its words alone, and its own session's
older compaction nodes, which do have rows, can pass it (R11: 83 fresh nodes asked in their own description, hit@1
39 -> 32). This writes a small overlay beside the sidecar, memory_embeddings_fresh.npz: one row per memory with no
current sidecar row, encoded by the ONNX export the refresh uses (checked against three sidecar rows, cosine >= 0.9999,
or nothing is written) and keyed by the sha256 of the same canonical text (build_memory_embeddings._text_for), so
brain.py judges an overlay row current exactly as it judges a sidecar row (embedding_artifact.embedding_row_current).
A row the next sidecar covers drops out of the next overlay; an unchanged row keeps its vector without re-encoding.

    python fresh_vectors.py               write the overlay (task BrainFreshVectors15m, windowless, BelowNormal)
    python fresh_vectors.py --dry         count what it would encode; write nothing
    python fresh_vectors.py --out PATH    write somewhere else (tests)
"""
import sys
if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)
import io
import os
import sys
import time

D = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")
OUT = os.path.join(D, "memory_embeddings_fresh.npz")
LOG = os.path.join(D, "embedding_refresh", "fresh_vectors.log")
MAX_ENCODE = 300     # newest first; a backlog beyond this waits for the sidecar refresh
MIN_TEXT = 40        # embedding_row_current's own floor
SCHEMA = 1


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with io.open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def read_text(builder, path):
    """The canonical text and its sha256, or None when the file moved under the read or is too short."""
    try:
        before = os.stat(path)
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
        after = os.stat(path)
    except OSError:
        return None
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        return None
    text = builder._text_for(body)
    if len(text) < MIN_TEXT:
        return None
    return text, builder.text_sha256(text)


def checked_encoder(emb, mem, builder, reb):
    """The refresh's ONNX encoder, after it reproduces three of the sidecar's own rows; else None."""
    enc = reb._onnx_encoder()
    slugs = sorted(emb.index)
    probe, texts = [], []
    for s in [slugs[len(slugs) // 4], slugs[len(slugs) // 2], slugs[3 * len(slugs) // 4]]:
        got = read_text(builder, os.path.join(mem, s + ".md"))
        if got and got[1] == emb.text_hashes.get(s):
            probe.append(s)
            texts.append(got[0])
    if len(probe) < 3:
        return None, "fewer than three readable sidecar rows to check it against"
    vecs = enc(texts)
    mat = emb.matrix
    worst = min(float((mat @ vecs[i].astype(mat.dtype))[emb.index[s]]) for i, s in enumerate(probe))
    if not worst >= 0.9999:
        return None, "it disagrees with the sidecar (cosine %.6f)" % worst
    return enc, "onnx export, checked on 3 sidecar rows (worst cosine %.7f)" % worst


class LocalEncoder:
    """The sidecar builder's own sentence-transformers model, for an install without an ONNX export.

    The faster encoder lives in embedding_refresh/refresh_embeddings_box.py, which is specific to the machine that
    builds the reference sidecar and is not shipped. Without it this run uses the same model the sidecar was built
    with (build_memory_embeddings._box_encoder_factory). checked_encoder still requires it to reproduce three
    sidecar rows to cosine 0.9999 before anything is written, so a different model or version is refused."""

    def __init__(self, builder, model_name):
        self.builder, self.model_name = builder, model_name

    def _onnx_encoder(self):
        return self.builder._box_encoder_factory(self.model_name)


def pick_encoder_source(builder, model_name):
    """refresh_embeddings_box when it is installed, else the local sentence-transformers model."""
    try:
        return __import__("refresh_embeddings_box")
    except ImportError:
        return LocalEncoder(builder, model_name)


def main():
    dry = "--dry" in sys.argv
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else OUT
    for p in (D, os.path.join(D, "embedding_refresh")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import numpy as np
    import build_memory_embeddings as builder
    import embedding_artifact as ea
    import spread_recall as sr
    t0 = time.time()
    emb = ea.load_verified_embeddings(sr.EMB, sr.EMB_AUTHORITY, sr.MEM, sr.EXPECTED_EMB_MODEL)
    if not emb or not emb.index:
        log("no verified sidecar; overlay left as it is")
        return 2
    t_load = time.time() - t0
    todo = []
    with os.scandir(sr.MEM) as it:
        for e in it:
            if not e.name.endswith(".md") or e.name[:-3] in builder.SKIP or e.name[:-3] in emb.index:
                continue
            try:
                todo.append((e.stat().st_mtime_ns, e.name[:-3]))
            except OSError:
                continue
    todo.sort(reverse=True)                       # newest first
    prev = {}
    try:
        with np.load(out, allow_pickle=False) as z:
            if str(z["model"].item()) == sr.EXPECTED_EMB_MODEL and int(z["schema"].item()) == SCHEMA:
                for s, h, v in zip(z["slugs"], z["text_sha256"], z["vectors"]):
                    prev[(str(s), str(h))] = np.asarray(v, dtype=np.float32)
    except Exception:
        pass
    rows, need = [], 0
    for _m, s in todo:
        got = read_text(builder, os.path.join(sr.MEM, s + ".md"))
        if not got:
            continue
        v = prev.get((s, got[1]))
        if v is None:
            if need >= MAX_ENCODE:
                continue
            need += 1
        rows.append([s, got[1], got[0], v])
    msg = "%d memories without a current sidecar row: %d rows, %d reused, %d to encode (sidecar %d rows, load %.1fs)" % (
        len(todo), len(rows), len(rows) - need, need, len(emb.index), t_load)
    if dry:
        log("dry: " + msg)
        return 0
    if need:
        reb = pick_encoder_source(builder, sr.EXPECTED_EMB_MODEL)
        try:
            enc, why = checked_encoder(emb, sr.MEM, builder, reb)
        except builder.EncoderUnavailable as exc:
            enc, why = None, str(exc)
        if enc is None:
            log("encoder unusable (%s); overlay left as it is. %s" % (why, msg))
            return 3
        idx = [i for i, r in enumerate(rows) if r[3] is None]
        vecs = enc([rows[i][2] for i in idx])
        for k, i in enumerate(idx):
            rows[i][3] = vecs[k]
        msg += "; encoder " + why
    dim = int(emb.matrix.shape[1])
    mat = np.vstack([r[3] for r in rows]).astype(np.float32) if rows else np.zeros((0, dim), dtype=np.float32)
    buf = io.BytesIO()
    np.savez(buf, slugs=np.array([r[0] for r in rows], dtype="U"), text_sha256=np.array([r[1] for r in rows], dtype="U64"),
             vectors=mat, model=np.array(sr.EXPECTED_EMB_MODEL), schema=np.array(SCHEMA),
             written_at=np.array(time.strftime("%Y-%m-%dT%H:%M:%S%z")))
    ea.atomic_replace_bytes(out, buf.getvalue())
    log("wrote %s: %s; %.1fs" % (os.path.basename(out), msg, time.time() - t0))
    return 0


if __name__ == "__main__":
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    sys.exit(main())
