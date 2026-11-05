"""Semantic retrieval channel for the math brain.

WHY THIS EXISTS. Until now every channel in `retrieval` was surface form: lexical
terms, math symbols, formula fingerprints. `retrieve` drops any node whose sum over
those three is zero before it picks seeds, so a claim that answers the question in
different words was never a candidate at any weighting. The 2026-08-22 labelled-set
study measured that ceiling at a fifth of the gold claims.

HOW IT SCORES. Claim vectors are prebuilt (bge-small-en-v1.5, 384-dim, L2-normalised)
and the question is encoded at query time, so similarity is a dot product. Raw cosine
is useless as an additive score - bge puts off-topic English at 0.6-0.8, so adding it
would hand every node in the store a near-constant and turn the seed filter off. The
channel therefore reports how far a claim stands out from the corpus for THIS question:
(cos - mean) / stdev over all claims, floored at zero, and only for the top K.

WHAT THE SCORE IS NOT. It is relative prominence, not relevance and not confidence. Any
spread of cosines puts some claim above the mean, so a question with no close claim still
lifts its most similar ones, and adding a constant to every cosine or scaling them all
leaves every score unchanged (interactive audits IA-20260929-052554-03 and MB-02). An
abstention on "nothing is close" would need a threshold on the raw cosine calibrated on
held-out answerable and unanswerable questions; none is applied here. standout_details()
returns the raw cosine beside each score, and lane_status() says why a call returned
nothing: disabled, no artifact, an invalid artifact, an encoder failure, no spread, or no
eligible claim standing out.

COST, AND WHY IT IS OFF BY DEFAULT. The encoder costs about five seconds to load in a
cold process. The card-serving hook is a fresh process per fire, so switching this on
globally would put five seconds in front of every hook. It is opt-in by environment
variable until measured; the evaluation turns it on.

CPU ONLY. The owner's 2026-09-06 law reserves this laptop's GPU for film work. This is
inference rather than training, and it is pinned to CPU regardless.
"""
from __future__ import annotations

import hashlib
import io
import os
import threading

try:
    import numpy as np
except ModuleNotFoundError as exc:
    if exc.name != "numpy":
        raise
    # The standard-library core must remain usable without the optional lane.
    np = None

# The vectors live beside the store being served (MATHBRAIN_STORE, as the hook reads it), so a sandbox or a copy of the
# store never scores against another store's cards; MATH_BRAIN_CLAIM_VECS still overrides.
# The default store is the one in this checkout's local/population folder (three levels up from
# this file is the checkout root), so no home directory appears in the source.
_CHECKOUT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARTIFACT = os.environ.get("MATH_BRAIN_CLAIM_VECS") or os.path.join(
    os.path.dirname(os.environ.get("MATHBRAIN_STORE")
                    or os.path.join(_CHECKOUT, "local", "population", "brain.sqlite3")),
    "claim_embeddings.npz",
)
DREAMING = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")
MODEL_NAME = "BAAI/bge-small-en-v1.5"
# bge asks for this prefix on the query side only; claims were embedded bare.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

TOP_K = int(os.environ.get("MATH_BRAIN_SEMANTIC_TOPK", "50"))
WEIGHT = float(os.environ.get("MATH_BRAIN_SEMANTIC_WEIGHT", "1.0"))

_lock = threading.Lock()
_vecs: np.ndarray | None = None
_ids: np.ndarray | None = None
_gen: tuple | None = None      # the bytes the vectors were read from: (their SHA-256, the digest the manifest named)
_digests: np.ndarray | None = None  # per row, the digest of the claim text it was made from
_sources: np.ndarray | None = None  # per row, how the vector was made (ROW_SOURCES), when the file records it
# body / title: embedded from the claim's body (its title when the body is empty) and shown to reproduce it; "title body":
# a full build of build_claim_vecs.py; legacy: a vector no text of its claim reproduces, whose text is unknown
ROW_SOURCES = ("body", "title", "title body", "legacy")
_model = None
_status: tuple[str, str] = ("NOT_LOADED", "")


def lane_status() -> tuple[str, str]:
    """(state, detail) of the channel in this process.

    States: DISABLED, NOT_LOADED, DEPENDENCY_UNAVAILABLE, NO_ARTIFACT, INVALID_ARTIFACT,
    READY, ENCODER_FAILURE, NO_SPREAD, NO_MATCH. An empty result from standout_scores means
    that no claim stood out only when this says NO_MATCH
    (interactive audit IA-20260929-MB-03)."""
    if not enabled():
        return ("DISABLED", "MATH_BRAIN_SEMANTIC is not on")
    return _status


def _query_encoder():
    """The brain's ONNX bge-small (query_embed.py): the same weights, CLS pooling and normalisation as the
    sentence-transformers model the channel was written for, at about 1 s cold and 11 ms warm here against 21-55 s."""
    import sys
    if DREAMING not in sys.path:
        sys.path.insert(0, DREAMING)
    import query_embed
    return query_embed


def enabled() -> bool:
    """MATH_BRAIN_SEMANTIC: '1' on, '0' off (the default), 'auto' on when the ONNX query encoder and this store's claim
    vectors are both present. The ONNX path removed the cost that kept it off (5-26 s of sentence-transformers load in
    front of every hook), and on held-out questions it served the right card slightly more often; on his own 480 math
    prompts (24 Sep shadow) it moved junk above the right card on pasted documents about as often as it silenced junk
    on vague ones, so it stays off until a scoped version wins on both."""
    mode = os.environ.get("MATH_BRAIN_SEMANTIC", "0")
    if mode == "1":
        return True
    if mode != "auto":
        return False
    try:
        return os.path.exists(ARTIFACT) and _query_encoder().available()
    except Exception:
        return False


# The writers publish the SHA-256 of the file they wrote beside it, in sha256sum's format: "<hex>  claim_embeddings.npz".
MANIFEST_SUFFIX = ".sha256"


def _read_manifest() -> str | None:
    """The SHA-256 the writer published for the vector file, or None when there is no manifest beside it. A manifest that
    does not hold one SHA-256 raises: the file's integrity record is damaged, so the file cannot be vouched for."""
    try:
        with open(ARTIFACT + MANIFEST_SUFFIX, "rb") as fh:
            raw = fh.read(4096)
    except FileNotFoundError:
        return None
    words = raw.decode("ascii", "replace").split()
    digest = words[0].lower() if words else ""
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise RuntimeError("the digest manifest beside the claim vectors does not hold a SHA-256")
    return digest


def _snapshot() -> tuple[bytes, tuple]:
    """The vector file's bytes, read once from one open file, and their generation: (the SHA-256 of those bytes, the
    digest the manifest names or None). Whatever the loader keeps is parsed from these same bytes."""
    with open(ARTIFACT, "rb") as fh:
        data = fh.read()
    return data, (hashlib.sha256(data).hexdigest(), _read_manifest())


def _generation() -> tuple:
    """The generation of the vector file as it is now: (the SHA-256 of its bytes, the digest its manifest names)."""
    return _snapshot()[1]


def artifact_identity() -> dict:
    """What the vectors in use were read from: {"sha256": the SHA-256 of the file's bytes, "manifest": "match" when the
    writer's manifest named those bytes, "absent" when there was none}; {} when no vectors are loaded. A receipt that
    names the semantic channel's treatment records this, not a timestamp, a row count or the zip's CRCs."""
    if _vecs is None or _gen is None:
        return {}
    sha, named = _gen
    return {"sha256": sha, "manifest": "match" if named else "absent"}


def _load_artifact() -> None:
    """Read the claim vectors, again whenever the artifact's bytes have changed since they were read.

    The vectors used to be read once per process and kept, so a caller that imports this module and runs many prompts
    (an evaluator, a replay) kept scoring against the first file it saw after the store's vectors were rebuilt, while a
    fresh process scored against the new ones (account03/B11 r70, B11-R31-SEMTOP-CONTROL-MISSES-SEMANTIC-GENERATION-
    20260928: top claim A before a valid rebuild, still A in the reused module, C in a fresh one). A file that fails to
    load leaves nothing behind, so the channel fails closed until a good file is in place, as a fresh process would.

    THE GENERATION IS THE SHA-256 OF THE BYTES. Every call reads the file once and hashes what it read; the vectors are
    parsed from those same bytes when the digest differs from the one they were read from, so a digest never names
    vectors from another file (account04/B11 r4, B11-R31-SEMANTIC-LOAD-TOCTOU-20260929-01, where a replace between a
    stat and the read cached new bytes under the old identity). The key used to be the file's (inode, mtime, size) and
    then also each zip member's (name, CRC-32, size) (account03/B01 r74, a rewrite through a hard link with the old mtime
    put back). CRC-32 can be forced: four bytes solve for any CRC, and account03/B11 r90 and account04/B17 r74
    (B11-R77-SEMANTIC-GENERATION-CRC32-COLLISION-20260929-R90, B17-R74-CRC32-COLLISION-PRESERVES-SEMANTIC-CACHE-
    GENERATION-20260929) built two valid files with different vectors, a different top claim and every field of that key
    equal, so a reused module kept the first file's answer while a fresh one read the second's. Reading and hashing the
    file costs about 10 ms per call on the 17.5 MB live file, against 0.07 ms for the old key; the parse it replaces on
    a fresh process's first call costs 13 ms.

    When the writer published a manifest (ARTIFACT + ".sha256"), bytes it does not name are refused, in this process
    and in a fresh one alike: the file changed after its writer published it."""
    global _vecs, _ids, _gen, _status, _digests, _sources
    if np is None:
        _vecs = _ids = _gen = _digests = _sources = None
        _status = ("DEPENDENCY_UNAVAILABLE", "optional semantic channel requires NumPy")
        raise RuntimeError(_status[1])
    try:
        data, gen = _snapshot()
    except FileNotFoundError:
        _vecs = _ids = _gen = _digests = _sources = None
        _status = ("NO_ARTIFACT", "no claim vectors beside the store")
        raise
    except Exception as exc:
        _vecs = _ids = _gen = _digests = _sources = None
        _status = ("INVALID_ARTIFACT", str(exc))
        raise
    if _vecs is not None and _gen == gen:
        return
    with _lock:
        if _vecs is not None and _gen == gen:
            return
        sha, named = gen
        try:
            if named is not None and named != sha:
                raise RuntimeError(
                    "the claim vectors' SHA-256 is %s..., their manifest names %s...: the file is not the one its "
                    "writer published" % (sha[:12], named[:12])
                )
            # No pickle: an object array in the file would run code while it loads
            # (account04/B17 r14, B17-R24-SEMANTIC-NPZ-ALLOW-PICKLE-CODE-EXEC-20260929). The
            # vectors are float32 and every other field is a fixed-width string, so nothing
            # legitimate needs pickle.
            with np.load(io.BytesIO(data), allow_pickle=False) as fields:
                vectors = np.asarray(fields["vecs"], dtype=np.float32)
                identifiers = np.asarray(fields["slugs"])
                digests = np.asarray(fields["node_digests"]) if "node_digests" in fields.files else None
                sources = np.asarray(fields["row_source"]) if "row_source" in fields.files else None
                built_with = str(fields["model"]) if "model" in fields else ""
            if identifiers.dtype.kind != "U" or identifiers.ndim != 1:
                raise RuntimeError(
                    f"claim identifiers must be a 1-D string array, found dtype {identifiers.dtype} "
                    f"shape {identifiers.shape!r}"
                )
            if MODEL_NAME.split("/")[-1] not in built_with:
                raise RuntimeError(
                    "claim vectors were built with %r, this channel encodes with %r"
                    % (built_with, MODEL_NAME)
                )
            # A NaN or infinite coordinate made every question's spread non-finite, so the channel
            # returned nothing for every question while available() said it was healthy
            # (IA-20260929-MB-03).
            if vectors.ndim != 2 or vectors.shape[1] == 0:
                raise RuntimeError(
                    f"claim vectors must be a 2-D array, found shape {vectors.shape!r}"
                )
            if vectors.shape[0] != identifiers.shape[0]:
                raise RuntimeError(
                    "vector/identifier mismatch: %d vectors, %d ids"
                    % (vectors.shape[0], identifiers.shape[0])
                )
            if not np.isfinite(vectors).all():
                bad = int((~np.isfinite(vectors)).any(axis=1).sum())
                raise RuntimeError(f"{bad} claim vector(s) hold a NaN or infinite coordinate")
            if len(set(identifiers.tolist())) != identifiers.shape[0]:
                raise RuntimeError("claim identifiers are not unique")
            # One store digest per row, when the file records them: what lets a caller check
            # that a vector was made from the text its claim holds now, not only that the claim
            # exists (account03/B12 r56,
            # B12-R56-ID-ONLY-COVERAGE-CERTIFIES-UNBOUND-VECTOR-TEXT-20260929).
            if digests is not None and (
                digests.dtype.kind != "U" or digests.shape != identifiers.shape
            ):
                raise RuntimeError(
                    "node digests must be one string per claim, found dtype "
                    f"{digests.dtype} shape {digests.shape!r}"
                )
            # How each row was made, when the file records it: a legacy row carries its claim's digest but
            # no text of the claim reproduces it, so a caller must be able to tell it from a row that does
            # (account04/B06 r76, B06-R76-LEGACY-DIGEST-MIGRATION-OPENS-UNVERIFIED-ROWS-20260929; account04/B14
            # r8). The values are a closed set, and a file whose sources are not is refused.
            if sources is not None and (
                sources.dtype.kind != "U" or sources.shape != identifiers.shape
                or not set(sources.tolist()) <= set(ROW_SOURCES)
            ):
                raise RuntimeError(
                    "row sources must be one of %s per claim, found dtype %s shape %r"
                    % ("/".join(ROW_SOURCES), sources.dtype, sources.shape)
                )
        except Exception as exc:
            _vecs = _ids = _gen = _digests = _sources = None
            _status = ("INVALID_ARTIFACT", str(exc))
            raise
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        _vecs, _ids, _gen, _digests, _sources = vectors / norms, identifiers, gen, digests, sources
        _status = ("READY", "%d claim vectors of dimension %d, SHA-256 %s (%s)" % (
            vectors.shape[0], vectors.shape[1], sha[:12],
            "their manifest names these bytes" if named else "no manifest beside them"))


def _load_model():
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is None:
            os.environ["CUDA_VISIBLE_DEVICES"] = ""
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(MODEL_NAME, device="cpu")
    return _model


def available() -> bool:
    try:
        _load_artifact()
        return True
    except Exception:
        return False


def standout_scores(query: str, node_ids) -> dict[str, float]:
    """Return {node_id: standout} for the top K claims, zero-floored.

    `standout` is the question's cosine to the claim expressed in standard
    deviations above the corpus mean for that same question, so the number means
    the same thing from one question to the next. It ranks claims against each other
    for one question; it does not say that any of them is relevant (see the module note).

    KNOWN WEAKNESS: the top K is taken before `node_ids` is applied, so when a
    superseded or guard-excluded claim lands in the top K the caller receives fewer
    than K candidates. It only ever weakens the channel, never mis-ranks it. The
    order should be reversed once the paired measurement it was found during is done.
    """
    details = standout_details(query, node_ids)
    return {node_id: score for node_id, (score, _cosine) in details.items()}


def standout_details(query: str, node_ids) -> dict[str, tuple[float, float]]:
    """{node_id: (standout, raw cosine)} for the claims standout_scores returns.

    lane_status() says why the result is empty."""
    global _status
    _load_artifact()
    assert _vecs is not None and _ids is not None
    vec = None
    failure = ""
    try:
        vec = _query_encoder().embed(QUERY_PREFIX + (query or ""))
    except Exception as exc:
        vec, failure = None, f"{type(exc).__name__}: {exc}"
    if vec is not None:
        question = np.asarray(vec, dtype=np.float32)
    elif os.environ.get("MATH_BRAIN_SEMANTIC_INPROCESS", "0") == "1":
        encoded = _load_model().encode(
            [QUERY_PREFIX + (query or "")],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        question = np.asarray(encoded[0], dtype=np.float32)
    else:
        _status = ("ENCODER_FAILURE", failure or "the query encoder returned nothing")
        return {}
    if question.shape != (_vecs.shape[1],) or not np.isfinite(question).all():
        _status = ("ENCODER_FAILURE",
                   f"the question vector has shape {question.shape!r} or a non-finite value; "
                   f"the claim vectors have dimension {_vecs.shape[1]}")
        return {}

    cosines = _vecs @ question
    mean = float(cosines.mean())
    spread = float(cosines.std())
    if not np.isfinite(spread) or spread <= 1e-9:
        _status = ("NO_SPREAD", "every claim is equally similar to the question")
        return {}
    standout = (cosines - mean) / spread

    keep = min(TOP_K, standout.shape[0])
    top = np.argpartition(-standout, keep - 1)[:keep] if keep < standout.shape[0] else np.arange(keep)

    wanted = set(node_ids) if node_ids is not None else None
    out: dict[str, tuple[float, float]] = {}
    for index in top:
        value = float(standout[index])
        if value <= 0.0:
            continue
        identifier = str(_ids[index])
        if wanted is not None and identifier not in wanted:
            continue
        out[identifier] = (WEIGHT * value, float(cosines[index]))
    if out:
        _status = ("READY", f"{len(out)} claims stand out")
    else:
        _status = ("NO_MATCH", f"no eligible claim among the top {keep} stands out")
    return out

