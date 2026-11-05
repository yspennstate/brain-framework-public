#!/usr/bin/env python3
"""Verified loading, corpus seqlock checks, and atomic pointer publication for embeddings.

Vectors and activation metadata live in immutable content-addressed NPZ generations. A tiny authority
pointer is the sole commit point; the nightly coverage observer is deliberately not authoritative.
Consumers hash and parse the same byte snapshot, validate current rows, and fail closed.
"""
import contextlib
import hashlib
import io
import json
import math
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone


OBSERVER_SCHEMA = "embedding-coverage-status/v2"
AUTHORITY_SCHEMA = "embedding-current/v2"
RUNTIME_SCHEMA_VERSION = 3
# Backward name retained only for observer-report callers.
STATUS_SCHEMA = OBSERVER_SCHEMA
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class VerifiedEmbeddings:
    """Three-item legacy-compatible view with exact per-row content identities attached."""

    def __init__(self, index, matrix, bias, text_hashes):
        self.index = index
        self.matrix = matrix
        self.bias = bias
        self.text_hashes = text_hashes

    def __iter__(self):
        return iter((self.index, self.matrix, self.bias))

    def __len__(self):
        return 3

    def __getitem__(self, item):
        return (self.index, self.matrix, self.bias)[item]


class PackedFloat32Matrix:
    """Read-only float32 rows from a verified NPY payload, without importing NumPy.

    ``spread_recall`` is a fresh command process for every prompt.  Importing NumPy solely
    to load a 384-column matrix cost roughly 190 ms on the live hook.  The immutable NPZ is
    already content-hash bound by its authority pointer, so the hot reader can safely parse
    its simple NPY arrays with the standard library and retain the same row-level freshness
    checks.  This class exposes only the two operations the hook actually needs.
    """

    def __init__(self, raw, rows, columns):
        if sys.byteorder != "little":
            raise ValueError("packed float32 hot reader requires little-endian host")
        self._raw = raw
        self._values = raw.cast("f")
        self.shape = (int(rows), int(columns))

    def _check_row(self, row):
        if isinstance(row, bool) or not isinstance(row, int):
            raise IndexError("embedding row is not an integer")
        if row < 0 or row >= self.shape[0]:
            raise IndexError("embedding row is out of range")
        return row * self.shape[1]

    def normalized_centroid(self, rows):
        """Return the unit centroid of selected rows, matching the hook's NumPy operation."""
        rows = list(rows)
        if not rows:
            return None
        starts = [self._check_row(row) for row in rows]
        columns = self.shape[1]
        inverse = 1.0 / float(len(starts))
        centroid = [
            sum(self._values[start + column] for start in starts) * inverse
            for column in range(columns)
        ]
        norm_squared = sum(value * value for value in centroid)
        if not math.isfinite(norm_squared) or norm_squared < 0.0:
            return None
        denominator = math.sqrt(norm_squared) + 1e-9
        return tuple(value / denominator for value in centroid)

    def row_dot(self, row, vector):
        """Dot one verified row with a same-width vector."""
        start = self._check_row(row)
        if len(vector) != self.shape[1]:
            raise ValueError("embedding vector width mismatch")
        value = sum(
            self._values[start + column] * vector[column]
            for column in range(self.shape[1])
        )
        if not math.isfinite(value):
            raise ValueError("non-finite embedding dot product")
        return value

    def active_bias(self, active_rows):
        """Return ``(all_finite, bias)`` over exactly the rows active for this load.

        Column-strided ``sum`` runs in C and covers every float, so NaN/Inf payloads still
        fail closed without paying a million-iteration Python loop.  Rows rejected by the
        source-freshness mask are subtracted before the mean is formed.
        """
        active = set(active_rows)
        row_count, columns = self.shape
        if not active:
            return True, 0.0
        if any(
                isinstance(row, bool) or not isinstance(row, int)
                or row < 0 or row >= row_count
                for row in active):
            return False, 0.0
        totals = [
            sum(self._values[column::columns])
            for column in range(columns)
        ]
        if any(not math.isfinite(value) for value in totals):
            return False, 0.0
        inactive = set(range(row_count)) - active
        for row in inactive:
            start = row * columns
            for column in range(columns):
                totals[column] -= self._values[start + column]
        inverse = 1.0 / float(len(active))
        value = sum((total * inverse) ** 2 for total in totals)
        if not math.isfinite(value):
            return False, 0.0
        return True, value if 0.0 <= value < 0.95 else 0.0


class LightweightVerifiedEmbeddings(VerifiedEmbeddings):
    """Legacy-compatible verified view backed by :class:`PackedFloat32Matrix`."""

    def normalized_centroid(self, rows):
        return self.matrix.normalized_centroid(rows)

    def row_dot(self, row, vector):
        return self.matrix.row_dot(row, vector)


def _read_npy_payload(blob):
    """Parse one bounded, non-object NPY member and return its header plus raw data."""
    import ast
    import struct

    if not isinstance(blob, bytes) or len(blob) < 12 or blob[:6] != b"\x93NUMPY":
        raise ValueError("invalid NPY member")
    major, minor = blob[6], blob[7]
    if (major, minor) == (1, 0):
        header_size = struct.unpack_from("<H", blob, 8)[0]
        header_start = 10
        encoding = "latin1"
    elif major in (2, 3) and minor == 0:
        header_size = struct.unpack_from("<I", blob, 8)[0]
        header_start = 12
        encoding = "utf-8" if major == 3 else "latin1"
    else:
        raise ValueError("unsupported NPY version")
    if header_size < 1 or header_size > 65536:
        raise ValueError("invalid NPY header size")
    data_start = header_start + header_size
    if data_start > len(blob):
        raise ValueError("truncated NPY header")
    header = ast.literal_eval(blob[header_start:data_start].decode(encoding).strip())
    if not isinstance(header, dict) or set(header) != {"descr", "fortran_order", "shape"}:
        raise ValueError("invalid NPY header")
    if header.get("fortran_order") is not False:
        raise ValueError("Fortran-order NPY is unsupported")
    descr = header.get("descr")
    shape = header.get("shape")
    if not isinstance(descr, str) or not isinstance(shape, tuple) or len(shape) > 2:
        raise ValueError("invalid NPY dtype or shape")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
           for value in shape):
        raise ValueError("invalid NPY shape")
    return descr, shape, memoryview(blob)[data_start:]


def _npy_count(shape):
    count = 1
    for value in shape:
        count *= value
        if count > 10_000_000:
            raise ValueError("NPY array exceeds hot-reader bound")
    return count


def _decode_npy_unicode(descr, shape, raw):
    match = re.fullmatch(r"<U([1-9][0-9]{0,3})", descr)
    if not match:
        raise ValueError("unsupported NPY unicode dtype")
    width = int(match.group(1))
    count = _npy_count(shape)
    stride = width * 4
    if len(raw) != count * stride:
        raise ValueError("NPY unicode payload size mismatch")
    values = []
    for index in range(count):
        value = bytes(raw[index * stride:(index + 1) * stride])
        values.append(value.decode("utf-32le").rstrip("\0"))
    return values


def _decode_npy_i64(descr, shape, raw):
    if descr not in ("<i8", "|i8") or len(raw) != _npy_count(shape) * 8:
        raise ValueError("unsupported int64 NPY payload")
    if sys.byteorder != "little":
        raise ValueError("int64 NPY hot reader requires little-endian host")
    return raw.cast("q")


def _decode_npy_bool(descr, shape, raw):
    if descr not in ("|b1", "<?") or len(raw) != _npy_count(shape):
        raise ValueError("unsupported bool NPY payload")
    values = raw.cast("B")
    if any(value not in (0, 1) for value in values):
        raise ValueError("invalid bool NPY value")
    return values


def load_verified_embeddings_lightweight(
        sidecar, authority_path, mem_dir=None, expected_model=None,
        verify_all_rows=False):
    """Standard-library hot loader for the exact schema-v3 artifact.

    It verifies the same authority pointer, complete sidecar SHA-256, schema, model,
    source stat mask, and exceptional-row content hashes as ``load_verified_embeddings``.
    Any parse, integrity, freshness, or numeric problem fails closed to ``None``.
    """
    try:
        import zipfile

        actual_sidecar, status = resolve_authority_sidecar(sidecar, authority_path)
        if not mem_dir:
            return None
        expected_sha = status.get("sidecar_sha256")
        expected_size = status.get("sidecar_size")
        if not isinstance(expected_sha, str) or not _SHA256_RE.fullmatch(expected_sha):
            return None
        if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 1:
            return None
        _utc(status.get("committed_at"))
        if expected_model and status.get("model") != expected_model:
            return None
        blob, actual_sha, actual_size, _ = read_stable_bytes(actual_sidecar)
        if actual_sha != expected_sha or actual_size != expected_size:
            return None

        required = {
            "slugs", "vecs", "model", "built_at", "schema_version", "text_sha256",
            "source_size", "source_mtime_ns", "row_safe",
        }
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                return None
            if sum(info.file_size for info in infos) > 64 * 1024 * 1024:
                return None
            member_names = {name[:-4] for name in names if name.endswith(".npy")}
            if required - member_names:
                return None
            arrays = {
                name: _read_npy_payload(archive.read(name + ".npy"))
                for name in required
            }

        slug_descr, slug_shape, slug_raw = arrays["slugs"]
        slugs = _decode_npy_unicode(slug_descr, slug_shape, slug_raw)
        hash_descr, hash_shape, hash_raw = arrays["text_sha256"]
        text_hashes = _decode_npy_unicode(hash_descr, hash_shape, hash_raw)
        model = _decode_npy_unicode(*arrays["model"])
        built_at = _decode_npy_unicode(*arrays["built_at"])
        schema_descr, schema_shape, schema_raw = arrays["schema_version"]
        schema_values = _decode_npy_i64(schema_descr, schema_shape, schema_raw)
        size_descr, size_shape, size_raw = arrays["source_size"]
        source_size = _decode_npy_i64(size_descr, size_shape, size_raw)
        mtime_descr, mtime_shape, mtime_raw = arrays["source_mtime_ns"]
        source_mtime = _decode_npy_i64(mtime_descr, mtime_shape, mtime_raw)
        safe_descr, safe_shape, safe_raw = arrays["row_safe"]
        row_safe = _decode_npy_bool(safe_descr, safe_shape, safe_raw)
        vec_descr, vec_shape, vec_raw = arrays["vecs"]

        if len(model) != 1 or len(built_at) != 1 or len(schema_values) != 1:
            return None
        if int(schema_values[0]) != RUNTIME_SCHEMA_VERSION:
            return None
        if status.get("model") != model[0] or status.get("built_at") != built_at[0]:
            return None
        if len(slug_shape) != 1 or len(hash_shape) != 1:
            return None
        if (vec_descr != "<f4" or len(vec_shape) != 2 or vec_shape[0] != len(slugs)
                or vec_shape[1] < 1 or vec_shape[1] > 4096
                or len(vec_raw) != vec_shape[0] * vec_shape[1] * 4):
            return None
        row_count = len(slugs)
        if (row_count < 1 or len(text_hashes) != row_count
                or size_shape != (row_count,) or mtime_shape != (row_count,)
                or safe_shape != (row_count,)
                or len(source_size) != row_count or len(source_mtime) != row_count
                or len(row_safe) != row_count):
            return None
        if len(slugs) != len(set(slugs)):
            return None
        if any(not slug or os.path.basename(slug) != slug or "/" in slug or "\\" in slug
               for slug in slugs):
            return None
        if any(not isinstance(value, str) or not _SHA256_RE.fullmatch(value)
               for value in text_hashes):
            return None
        if any(int(value) < 0 for value in source_size) or any(
                int(value) < 0 for value in source_mtime):
            return None

        matrix = PackedFloat32Matrix(vec_raw, vec_shape[0], vec_shape[1])
        hashes_by_slug = dict(zip(slugs, text_hashes))
        probe = LightweightVerifiedEmbeddings(
            {slug: index for index, slug in enumerate(slugs)},
            matrix,
            0.0,
            hashes_by_slug,
        )
        try:
            with os.scandir(mem_dir) as entries:
                memory_stats = {
                    entry.name: entry.stat()
                    for entry in entries
                    if entry.name.endswith(".md") and entry.is_file()
                }
        except OSError:
            return None
        active = {}
        for index, slug in enumerate(slugs):
            fast_current = False
            try:
                stat = memory_stats[slug + ".md"]
                fast_current = (
                    bool(row_safe[index])
                    and int(stat.st_size) == int(source_size[index])
                    and int(stat.st_mtime_ns) == int(source_mtime[index])
                )
            except (KeyError, OSError):
                pass
            if fast_current or embedding_row_current(probe, mem_dir, slug):
                active[slug] = index
        all_finite, bias = matrix.active_bias(active.values())
        if not all_finite:
            return None
        loaded = LightweightVerifiedEmbeddings(active, matrix, bias, hashes_by_slug)
        if verify_all_rows:
            loaded.index = {
                slug: row
                for slug, row in loaded.index.items()
                if embedding_row_current(loaded, mem_dir, slug)
            }
            all_finite, loaded.bias = matrix.active_bias(loaded.index.values())
            if not all_finite:
                return None
        return loaded
    except Exception:
        return None


def read_stable_bytes(path):
    """Read one stable descriptor snapshot and return bytes, sha256, size, and mtime_ns."""
    with open(path, "rb") as fh:
        before = os.fstat(fh.fileno())
        blob = fh.read()
        after = os.fstat(fh.fileno())
    before_sig = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_sig = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_sig != after_sig or len(blob) != after.st_size:
        raise OSError("artifact changed while it was being read")
    return blob, hashlib.sha256(blob).hexdigest(), len(blob), after.st_mtime_ns


def _utc(value):
    if not isinstance(value, str) or not value:
        raise ValueError("missing UTC timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def load_json_object(path):
    blob, _, _, _ = read_stable_bytes(path)
    status = json.loads(blob.decode("utf-8"))
    if not isinstance(status, dict):
        raise ValueError("status is not an object")
    return status


def corpus_stat_manifest(mem_dir, slugs):
    """Return per-row metadata used to mask only changed/deleted vectors at load time."""
    rows = []
    for slug in sorted(set(slugs)):
        if (not isinstance(slug, str) or not slug or os.path.basename(slug) != slug
                or "/" in slug or "\\" in slug):
            raise ValueError("unsafe memory slug in corpus manifest")
        path = os.path.join(mem_dir, slug + ".md")
        st = os.stat(path)
        rows.append((slug, st.st_size, st.st_mtime_ns))
    digest = hashlib.sha256()
    manifest = {}
    for slug, size, mtime_ns in rows:
        digest.update(slug.encode("utf-8", errors="surrogatepass"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(mtime_ns).encode("ascii"))
        digest.update(b"\n")
        manifest[slug] = [int(size), int(mtime_ns)]
    return manifest, digest.hexdigest()


def _allowed_generation(canonical, actual, digest):
    canonical = os.path.abspath(canonical)
    actual = os.path.abspath(actual)
    if os.path.dirname(canonical).casefold() != os.path.dirname(actual).casefold():
        return False
    if actual.casefold() == canonical.casefold():
        return True
    stem, ext = os.path.splitext(os.path.basename(canonical))
    return os.path.basename(actual).casefold() == ("%s.%s%s" % (stem, digest, ext)).casefold()


def resolve_authority_sidecar(canonical, authority_path):
    """Resolve one strict, minimal pointer to the canonical file or its digest-named sibling.

    Row activation policy deliberately does not live in this mutable JSON.  Consumers derive it
    from the digest-bound NPZ text hashes and current memory bytes, so editing a pointer cannot
    suppress or resurrect individual memories.
    """
    status = load_json_object(authority_path)
    required = {
        "schema", "committed_at", "sidecar", "sidecar_sha256", "sidecar_size", "model",
        "built_at",
    }
    digest = status.get("sidecar_sha256")
    actual = str(status.get("sidecar") or "")
    size = status.get("sidecar_size")
    if (set(status) != required or status.get("schema") != AUTHORITY_SCHEMA
            or not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest)
            or isinstance(size, bool) or not isinstance(size, int) or size < 1
            or not isinstance(status.get("model"), str) or not status["model"]
            or not isinstance(status.get("built_at"), str) or not status["built_at"]
            or not _allowed_generation(canonical, actual, digest)):
        raise ValueError("embedding authority does not name an allowed generation")
    _utc(status.get("committed_at"))
    _utc(status.get("built_at"))
    return os.path.abspath(actual), status


def load_verified_embeddings(sidecar, authority_path, mem_dir=None, expected_model=None,
                             verify_all_rows=False):
    """Return a slug index, float32 matrix, and cosine bias, or fail closed to None.

    The publication-host stat snapshot and row-safety mask live inside the digest-bound schema-v3
    NPZ, never in mutable pointer JSON. Hot-path callers stat each row and exact-hash only semantic
    participants; offline/release callers can request an exact hash of every active row.
    """
    try:
        import numpy as np

        actual_sidecar, status = resolve_authority_sidecar(sidecar, authority_path)
        if not mem_dir:
            return None
        expected_sha = status.get("sidecar_sha256")
        expected_size = status.get("sidecar_size")
        if not isinstance(expected_sha, str) or not _SHA256_RE.fullmatch(expected_sha):
            return None
        if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 1:
            return None
        _utc(status.get("committed_at"))
        if expected_model and status.get("model") != expected_model:
            return None
        blob, actual_sha, actual_size, _ = read_stable_bytes(actual_sidecar)
        if actual_sha != expected_sha or actual_size != expected_size:
            return None
        with np.load(io.BytesIO(blob), allow_pickle=False) as z:
            required = {
                "slugs", "vecs", "model", "built_at", "schema_version", "text_sha256",
                "source_size", "source_mtime_ns", "row_safe",
            }
            if required - set(z.files):
                return None
            slugs = [str(s) for s in z["slugs"]]
            vecs = np.asarray(z["vecs"])
            model = str(z["model"].item())
            built_at = str(z["built_at"].item())
            schema_version = int(z["schema_version"].item())
            text_hashes = [str(h) for h in z["text_sha256"]]
            source_size = np.asarray(z["source_size"])
            source_mtime = np.asarray(z["source_mtime_ns"])
            row_safe = np.asarray(z["row_safe"])

        if (schema_version != RUNTIME_SCHEMA_VERSION or status.get("model") != model
                or status.get("built_at") != built_at):
            return None
        if (vecs.dtype != np.float32 or vecs.ndim != 2 or vecs.shape[0] != len(slugs)
                or vecs.shape[1] < 1 or len(text_hashes) != len(slugs)):
            return None
        if len(slugs) != len(set(slugs)) or not np.isfinite(vecs).all():
            return None
        if (source_size.dtype != np.int64 or source_mtime.dtype != np.int64
                or row_safe.dtype != np.bool_ or source_size.shape != (len(slugs),)
                or source_mtime.shape != (len(slugs),) or row_safe.shape != (len(slugs),)):
            return None
        hashes_by_slug = dict(zip(slugs, text_hashes))
        probe = VerifiedEmbeddings(
            {slug: i for i, slug in enumerate(slugs)}, vecs, 0.0, hashes_by_slug
        )
        try:
            with os.scandir(mem_dir) as entries:
                memory_stats = {
                    entry.name: entry.stat()
                    for entry in entries
                    if entry.name.endswith(".md") and entry.is_file()
                }
        except OSError:
            return None
        active = {}
        for i, slug in enumerate(slugs):
            fast_current = False
            try:
                st = memory_stats[slug + ".md"]
                fast_current = (
                    bool(row_safe[i]) and int(st.st_size) == int(source_size[i])
                    and int(st.st_mtime_ns) == int(source_mtime[i])
                )
            except (KeyError, OSError):
                pass
            # A benign touch, restored stale row, or conservative adoption mask must not suppress
            # a current memory. Exact-hash only these exceptional rows; the normal hot path is stat-only.
            if fast_current or embedding_row_current(probe, mem_dir, slug):
                active[slug] = i
        def active_bias(index):
            if not index:
                return 0.0
            mean = vecs[list(index.values())].mean(axis=0)
            value = float(mean @ mean)
            return value if math.isfinite(value) and 0.0 <= value < 0.95 else 0.0

        loaded = VerifiedEmbeddings(
            active, vecs, active_bias(active), hashes_by_slug
        )
        if verify_all_rows:
            loaded.index = {
                slug: row for slug, row in loaded.index.items()
                if embedding_row_current(loaded, mem_dir, slug)
            }
            loaded.bias = active_bias(loaded.index)
        return loaded
    except Exception:
        return None


def embedding_row_current(loaded, mem_dir, slug):
    """Hash one semantic participant's canonical text; false on any race or mismatch."""
    try:
        import build_memory_embeddings as builder

        expected = loaded.text_hashes.get(slug)
        if not isinstance(expected, str) or not _SHA256_RE.fullmatch(expected):
            return False
        if os.path.basename(slug) != slug or "/" in slug or "\\" in slug:
            return False
        path = os.path.join(mem_dir, slug + ".md")
        before = os.stat(path)
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
        after = os.stat(path)
        before_sig = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        after_sig = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if before_sig != after_sig:
            return False
        text = builder._text_for(body)
        return len(text) >= 40 and builder.text_sha256(text) == expected
    except Exception:
        return False


def atomic_replace_bytes(path, payload, replace_timeout=5.0):
    """Fsync bytes in the destination directory, replace atomically, then sync that directory."""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        raise OSError("destination directory does not exist: %s" % parent)
    fd, tmp = tempfile.mkstemp(prefix=".%s." % os.path.basename(path), suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fd = None
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        deadline = time.monotonic() + max(0.0, float(replace_timeout))
        while True:
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        if os.name != "nt":
            dir_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        return path
    finally:
        if fd is not None:
            os.close(fd)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


@contextlib.contextmanager
def file_lock(path, timeout=30.0):
    """Cross-platform advisory lock on a stable one-byte lockfile."""
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + timeout
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("timed out waiting for embedding lock")
                    time.sleep(0.05)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("timed out waiting for embedding lock")
                    time.sleep(0.05)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
