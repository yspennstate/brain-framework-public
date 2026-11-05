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

T_START = time.time()   # the budget counts from process start: imports and the sidecar load are part of run_bounded's 600 s

D = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")
OUT = os.path.join(D, "memory_embeddings_fresh.npz")
LOG = os.path.join(D, "embedding_refresh", "fresh_vectors.log")
MAX_ENCODE = 300     # newest first; a backlog beyond this waits for the sidecar refresh
MIN_TEXT = 40        # embedding_row_current's own floor
BUDGET_S = 480       # stop encoding here and write what is done (run_bounded kills the task at 600 s)
CHUNK = 4
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


CHECK_MARK = os.path.join(D, "embedding_refresh", "fresh_vectors_check.json")


def _check_key(reb, sr):
    """What the 3-row check depends on: the ONNX files, the live sidecar's sha256 (from the authority pointer, not
    sr.EMB, which is an old fixed name), the model id and onnxruntime's version."""
    import json
    key = {"model": sr.EXPECTED_EMB_MODEL}
    for name, p in (("onnx", os.path.join(reb.ONNX_DIR, "model.onnx")), ("tok", os.path.join(reb.ONNX_DIR, "tokenizer.json"))):
        st = os.stat(p)
        key[name] = [st.st_size, st.st_mtime_ns]
    try:
        with io.open(sr.EMB_AUTHORITY, encoding="utf-8") as fh:
            sha = json.load(fh).get("sidecar_sha256")
    except ValueError:
        sha = None
    if not sha:
        raise OSError("no sidecar sha256 in the authority pointer")
    key["sidecar_sha256"] = sha
    ort = sys.modules.get("onnxruntime")
    key["ort"] = getattr(ort, "__version__", "?")
    return key


def checked_encoder(emb, mem, builder, reb, sr):
    """The refresh's ONNX encoder, after it reproduces three of the sidecar's own rows; else None.

    The check runs once per model file and sidecar file (claude-68ceaf54, 31 Oct 2026): on a starved box the encoder's
    load and check took 170-233 s of the 480 s budget, and the same encoder on the same sidecar gives the same answer,
    so a passed check is kept in fresh_vectors_check.json and a run whose key matches skips it. A new sidecar (every
    three hours), a new export or a new onnxruntime changes the key and the check runs again.
    Returns (encoder or None, why, seconds per row from the last timed encode or None)."""
    import json
    enc = reb._onnx_encoder()
    try:
        key = _check_key(reb, sr)
    except (OSError, AttributeError):    # LocalEncoder has no ONNX files to key on: check every run
        key = None
    mark = {}
    try:
        with io.open(CHECK_MARK, encoding="utf-8") as fh:
            mark = json.load(fh)
    except (OSError, ValueError):
        pass
    if key is not None and mark.get("key") == key:
        return enc, "onnx export, check skipped: same model and sidecar files as the check at %s (worst cosine %.7f)" % (
            mark.get("at", "?"), mark.get("worst", 0.0)), mark.get("row_s")
    slugs = sorted(emb.index)
    probe, texts = [], []
    for s in [slugs[len(slugs) // 4], slugs[len(slugs) // 2], slugs[3 * len(slugs) // 4]]:
        got = read_text(builder, os.path.join(mem, s + ".md"))
        if got and got[1] == emb.text_hashes.get(s):
            probe.append(s)
            texts.append(got[0])
    if len(probe) < 3:
        return None, "fewer than three readable sidecar rows to check it against", None
    tc = time.time()
    vecs = enc(texts)
    row_s = (time.time() - tc) / len(texts)
    mat = emb.matrix
    worst = min(float((mat @ vecs[i].astype(mat.dtype))[emb.index[s]]) for i, s in enumerate(probe))
    if not worst >= 0.9999:
        return None, "it disagrees with the sidecar (cosine %.6f)" % worst, None
    if key is not None:
        try:
            ea_bytes = json.dumps({"key": key, "worst": worst, "row_s": round(row_s, 2),
                                   "at": time.strftime("%Y-%m-%d %H:%M:%S")}).encode("utf-8")
            with io.open(CHECK_MARK + ".tmp", "wb") as fh:
                fh.write(ea_bytes)
            os.replace(CHECK_MARK + ".tmp", CHECK_MARK)
        except OSError:
            pass
    return enc, "onnx export, checked on 3 sidecar rows (worst cosine %.7f)" % worst, row_s


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
    # Ordinary memories first, newest first within each class (claude-68ceaf54, 31 Oct 2026). On a loaded box a run
    # encodes 4 to 12 rows while about 20 arrive, most of them compaction anchors, so newest-first left the older
    # ordinary memories dark until the three-hourly sidecar caught up: at 20:1x, 45 of 59 waiting memories had no
    # current vector. Agents recall memories far more than compaction anchors, so those go first.
    todo.sort(key=lambda t: (not t[1].startswith("compaction_"), t[0]), reverse=True)
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
        t_enc0 = time.time()
        try:
            enc, why, row_s = checked_encoder(emb, sr.MEM, builder, reb, sr)
        except builder.EncoderUnavailable as exc:
            enc, why, row_s = None, str(exc), None
        t_enc = time.time() - t_enc0
        if enc is None:
            log("encoder unusable (%s); overlay left as it is. %s" % (why, msg))
            return 3
        # Encode in chunks and stop at the budget: the task's run_bounded cap is 600 s, and on 18 Oct a CPU-starved
        # 300-row encode never finished, so every run was killed with nothing written and the backlog only grew
        # (58 kills, 08:22-12:32). Rows encoded before the budget are written; the next run reuses them via prev.
        # A chunk of 16 took over 180 s at 12:5x (the encoder's own load and check took 113 s), so chunks are 4 and
        # a chunk starts only when the last one's time still fits under the budget.
        # A run killed at the 600 s cap used to write nothing and log nothing (20:00, 21:14 and 21:30 on 31 Oct), so
        # the phases are logged as soon as the encoder is ready and the overlay is written after every chunk: a kill
        # now costs at most the chunk in flight.
        log("encoder ready: imports %.1fs, sidecar load %.1fs, scan %.1fs, encoder load+check %.1fs; %d to encode" % (
            t0 - T_START, t_load, t_enc0 - t0 - t_load, t_enc, need))
        idx = [i for i, r in enumerate(rows) if r[3] is None]
        done, last, chunk_s = 0, 0.0, []
        j = 0
        while j < len(idx):
            # Each chunk is sized to the time left, by the per-row time of the last timed encode (the check's, for the
            # first chunk), with a 1.5x margin; a chunk that cannot fit a single row ends the run. At 22:20 on 31 Oct a
            # fixed chunk of 4 was refused by 2 s and the run encoded nothing although 3 rows fitted.
            n = min(CHUNK, len(idx) - j)
            if row_s:
                n = min(n, int((BUDGET_S - (time.time() - T_START)) / (1.5 * row_s)))
            elif time.time() - T_START > BUDGET_S:
                n = 0
            if n < 1:
                break
            part = idx[j:j + n]
            j += n
            tc = time.time()
            vecs = enc([rows[i][2] for i in part])
            last = time.time() - tc
            row_s = last / len(part)
            chunk_s.append(round(last, 1))
            for k, i in enumerate(part):
                rows[i][3] = vecs[k]
            done += len(part)
            write_overlay(out, [r for r in rows if r[3] is not None], emb, sr, ea, np)
        if done < len(idx):
            rows = [r for r in rows if r[3] is not None]
            msg += "; budget %ds reached after %d of %d encodes, the rest wait for the next run" % (BUDGET_S, done, len(idx))
        msg += "; encoder " + why
        # Phase timings (probe9 follow-up, 31 Oct): where the 480 s goes when a run encodes only 4 to 12 rows.
        msg += "; phases: before encoder %.1fs, encoder load+check %.1fs, chunks %s" % (t_enc0 - t0, t_enc, chunk_s)
    write_overlay(out, rows, emb, sr, ea, np)
    log("wrote %s (%d rows): %s; %.1fs" % (os.path.basename(out), len(rows), msg, time.time() - T_START))
    return 0


def write_overlay(out, rows, emb, sr, ea, np):
    dim = int(emb.matrix.shape[1])
    mat = np.vstack([r[3] for r in rows]).astype(np.float32) if rows else np.zeros((0, dim), dtype=np.float32)
    buf = io.BytesIO()
    np.savez(buf, slugs=np.array([r[0] for r in rows], dtype="U"), text_sha256=np.array([r[1] for r in rows], dtype="U64"),
             vectors=mat, model=np.array(sr.EXPECTED_EMB_MODEL), schema=np.array(SCHEMA),
             written_at=np.array(time.strftime("%Y-%m-%dT%H:%M:%S%z")))
    ea.atomic_replace_bytes(out, buf.getvalue())


if __name__ == "__main__":
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    sys.exit(main())
