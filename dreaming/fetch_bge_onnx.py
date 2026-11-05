"""fetch_bge_onnx.py - put the semantic recall model where query_embed.py looks for it.

Semantic recall embeds each query with BAAI/bge-small-en-v1.5 (CLS pooling, unit norm, 512 tokens) through
onnxruntime and tokenizers, from dreaming/models/bge-small-en-v1.5/{model.onnx, tokenizer.json}. The weights are
not in this repository (133 MB). This script downloads the ONNX export and the tokenizer that the model's own
Hugging Face repository publishes, at a pinned revision, checks each file's size and sha256, and writes
EXPORT.json beside them.

    python fetch_bge_onnx.py              download into dreaming/models/bge-small-en-v1.5 (skips files already good)
    python fetch_bge_onnx.py --check      verify what is there, download nothing (exit 1 if a file is missing or wrong)
    python fetch_bge_onnx.py --dest DIR   another folder

Needs only the standard library to download; the parity line at the end needs numpy, onnxruntime and tokenizers.
Set HF_ENDPOINT to use a mirror.
"""
import argparse, hashlib, io, json, os, sys, time, urllib.request

REPO = "BAAI/bge-small-en-v1.5"
REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
FILES = {
    # name in the model folder: (path in the Hugging Face repo, size in bytes, sha256)
    "model.onnx": ("onnx/model.onnx", 133093490, "828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35"),
    "tokenizer.json": ("tokenizer.json", 711396, "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66"),
    "tokenizer_config.json": ("tokenizer_config.json", None, "9261e7d79b44c8195c1cada2b453e55b00aeb81e907a6664974b4d7776172ab3"),
}
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DEST = os.path.join(HERE, "models", "bge-small-en-v1.5")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def good(path, size, digest):
    if not os.path.isfile(path):
        return False, "missing"
    if size is not None and os.path.getsize(path) != size:
        return False, "size %d, expected %d" % (os.path.getsize(path), size)
    if digest is not None and sha256(path) != digest:
        return False, "sha256 differs"
    return True, "ok"


def download(url, path):
    tmp = path + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "fetch_bge_onnx/1"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as out:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    os.replace(tmp, path)


def parity(dest):
    """Embed two texts through the downloaded files; same text twice must agree, two different texts must not."""
    try:
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
    except ImportError as e:
        return "parity skipped (%s)" % e
    so = ort.SessionOptions()
    so.intra_op_num_threads = 2
    so.log_severity_level = 3
    sess = ort.InferenceSession(os.path.join(dest, "model.onnx"), so, providers=["CPUExecutionProvider"])
    tok = Tokenizer.from_file(os.path.join(dest, "tokenizer.json"))
    tok.enable_truncation(512)
    tok.enable_padding()

    def emb(t):
        e = tok.encode_batch([t])
        ids = np.array([x.ids for x in e], dtype=np.int64)
        att = np.array([x.attention_mask for x in e], dtype=np.int64)
        v = sess.run(["last_hidden_state"], {"input_ids": ids, "attention_mask": att,
                                             "token_type_ids": np.zeros_like(ids)})[0][0, 0, :]
        return v / np.linalg.norm(v)

    a, b, c = emb("how is a proof checked"), emb("how is a proof checked"), emb("a river on a map")
    ok = a.shape == (384,) and float(a @ b) > 0.9999 and float(a @ c) < 0.95
    return "parity %s: dim %d, same text %.5f, different text %.3f" % ("ok" if ok else "FAIL", a.shape[0], a @ b, a @ c)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", default=DEFAULT_DEST)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.dest, exist_ok=True)
    base = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    bad = 0
    for name, (src, size, digest) in FILES.items():
        path = os.path.join(a.dest, name)
        ok, why = good(path, size, digest)
        if not ok and not a.check:
            url = "%s/%s/resolve/%s/%s" % (base, REPO, REVISION, src)
            print("downloading %s (%s)" % (name, why))
            download(url, path)
            ok, why = good(path, size, digest)
        print("%-22s %s" % (name, why))
        bad += not ok
    if bad:
        return 1
    rec = {"model": REPO, "revision": REVISION, "source": "huggingface onnx/model.onnx",
           "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "pooling": "cls", "normalize": True, "max_length": 512,
           "sha256": {n: sha256(os.path.join(a.dest, n)) for n in FILES}}
    if not a.check:
        with io.open(os.path.join(a.dest, "EXPORT.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=1)
    print(parity(a.dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
