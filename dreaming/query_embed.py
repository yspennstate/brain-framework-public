"""query_embed.py - embed a QUERY with the memory sidecar's own model (BAAI/bge-small-en-v1.5) in about a second.

The sidecar (memory_embeddings.npz) was built with sentence-transformers: CLS pooling, L2-normalised, max 512 tokens.
Importing sentence-transformers costs ~55 s on this machine (measured 2026-09-13), which no deliberate recall can pay,
so the same weights run here through onnxruntime + tokenizers (models/bge-small-en-v1.5, exported by
campaign_2026-09-12_ultracode_mathbrain/lanes/m7_brain/export_bge_onnx.py; parity cosines 1.0 on five texts).

    embed(text) -> numpy float32 vector of dim 384, unit norm, or None when anything is missing (fail-open: the
    caller carries on without a query vector, exactly as before this module existed).

    python query_embed.py --selftest
"""
import io, json, os, sys, time

DR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(DR, "models", "bge-small-en-v1.5")
_STATE = {}


def available():
    return os.path.exists(os.path.join(MODEL_DIR, "model.onnx")) and os.path.exists(os.path.join(MODEL_DIR, "tokenizer.json"))


def _session():
    if "sess" in _STATE:
        return _STATE["sess"], _STATE["tok"]
    import onnxruntime as ort
    from tokenizers import Tokenizer
    so = ort.SessionOptions()
    so.intra_op_num_threads = 2          # a query is one short sequence; never grab the machine
    so.log_severity_level = 3
    sess = ort.InferenceSession(os.path.join(MODEL_DIR, "model.onnx"), so, providers=["CPUExecutionProvider"])
    tok = Tokenizer.from_file(os.path.join(MODEL_DIR, "tokenizer.json"))
    tok.enable_truncation(512)
    tok.enable_padding()
    _STATE["sess"], _STATE["tok"] = sess, tok
    return sess, tok


def embed(text):
    """Unit-norm float32 vector for one text, or None."""
    try:
        if not text or not text.strip() or not available():
            return None
        import numpy as np
        sess, tok = _session()
        enc = tok.encode_batch([text])
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        att = np.array([e.attention_mask for e in enc], dtype=np.int64)
        hs = sess.run(["last_hidden_state"], {"input_ids": ids, "attention_mask": att, "token_type_ids": np.zeros_like(ids)})[0]
        v = hs[0, 0, :].astype("float32")
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else None
    except Exception:
        return None


def _selftest():
    t0 = time.time()
    a = embed("which research box did we turn off and where did its stuff go")
    t1 = time.time()
    b = embed("which research box did we turn off and where did its stuff go")
    c = embed("what is the beta constant in the simulations")
    t2 = time.time()
    if a is None or b is None or c is None:
        print("SELFTEST FAIL: embed returned None (model dir %s available=%s)" % (MODEL_DIR, available()))
        return 1
    import numpy as np
    same = float(a @ b)
    diff = float(a @ c)
    ok = a.shape == (384,) and abs(float(np.linalg.norm(a)) - 1.0) < 1e-4 and same > 0.9999 and diff < 0.95
    print("selftest: dim %s norm %.5f same-text cos %.5f different-text cos %.3f | first call %.2fs (session load), next two %.2fs -> %s"
          % (a.shape, float(np.linalg.norm(a)), same, diff, t1 - t0, t2 - t1, "OK" if ok else "FAIL"))
    exp = os.path.join(MODEL_DIR, "EXPORT.json")
    if os.path.exists(exp):
        print("export record:", json.dumps({k: v for k, v in json.load(io.open(exp, encoding="utf-8")).items() if k != "parity_cosines"}))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(__doc__)
