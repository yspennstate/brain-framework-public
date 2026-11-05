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
_HEX_RUN_RE = re.compile(r"[0-9a-f]*")


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

    def active_bias(self, active_rows, totals_cache=None):
        """Return ``(all_finite, bias)`` over exactly the rows active for this load.

        Column-strided ``sum`` runs in C and covers every float, so NaN/Inf payloads still
        fail closed without paying a million-iteration Python loop.  Rows rejected by the
        source-freshness mask are subtracted before the mean is formed.

        totals_cache: optional (path, sidecar_sha256). The all-row column totals depend only on the
        sidecar bytes, so they are kept in a small JSON file under that hash and reused; the
        subtraction and the mean below run unchanged, so the bias is bit-identical. The totals were
        0.15 s of the 0.47 s hot load the prompt hook pays on every prompt (1 Nov 2026, 29,265 x 384).
        Any problem with the file means the totals are computed as before.
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
        totals = _read_column_totals(totals_cache, row_count, columns)
        if totals is None:
            totals = [
                sum(self._values[column::columns])
                for column in range(columns)
            ]
            if any(not math.isfinite(value) for value in totals):
                return False, 0.0
            _write_column_totals(totals_cache, row_count, columns, totals)
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


def _read_column_totals(cache, rows, columns):
    """The cached all-row column totals for this sidecar hash and shape, or None."""
    if not cache:
        return None
    try:
        path, sha = cache
        with open(path, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        totals = saved.get("totals")
        if (saved.get("v") != 1 or saved.get("sha256") != sha or saved.get("rows") != rows
                or saved.get("columns") != columns or not isinstance(totals, list) or len(totals) != columns
                or not all(isinstance(v, float) and math.isfinite(v) for v in totals)):
            return None
        return totals
    except Exception:
        return None


def _write_column_totals(cache, rows, columns, totals):
    if not cache:
        return
    try:
        path, sha = cache
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"v": 1, "sha256": sha, "rows": rows, "columns": columns, "totals": totals}, fh)
        os.replace(tmp, path)
    except Exception:
        pass


def _bulk_md_stats(mem_dir):
    """{name: (st_size, st_mtime_ns)} for the regular .md files in mem_dir from macOS getattrlistbulk, which returns
    a buffer of entries per call: 0.06 s for 29k files against 0.15 s for scandir plus a stat each (1 Nov 2026,
    every value equal). A symlink is left out (its row then takes the hash check). None off macOS or on any error."""
    if sys.platform != "darwin":
        return None
    try:
        import ctypes
        import struct

        class _AttrList(ctypes.Structure):
            _fields_ = [("bitmapcount", ctypes.c_ushort), ("reserved", ctypes.c_uint16),
                        ("commonattr", ctypes.c_uint32), ("volattr", ctypes.c_uint32),
                        ("dirattr", ctypes.c_uint32), ("fileattr", ctypes.c_uint32), ("forkattr", ctypes.c_uint32)]

        libc = ctypes.CDLL(None, use_errno=True)
        call = libc.getattrlistbulk
        call.argtypes = [ctypes.c_int, ctypes.POINTER(_AttrList), ctypes.c_void_p, ctypes.c_size_t,
                         ctypes.c_uint64]
        call.restype = ctypes.c_int
        # returned attrs, name, object type, modification time; file data length
        attrs = _AttrList(5, 0, 0x80000000 | 0x1 | 0x8 | 0x400, 0, 0, 0x200, 0)
        size = 256 * 1024
        buf = ctypes.create_string_buffer(size)
        out = {}
        fd = os.open(mem_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            while True:
                count = call(fd, ctypes.byref(attrs), buf, size, 0)
                if count < 0:
                    return None
                if count == 0:
                    return out
                raw = buf.raw
                off = 0
                for _ in range(count):
                    length, common, fileattr = struct.unpack_from("<I I 8x I", raw, off)
                    p = off + 24
                    name_off, name_len = struct.unpack_from("<iI", raw, p)
                    name = raw[p + name_off:p + name_off + name_len - 1].decode("utf-8", "surrogateescape")
                    objtype, sec, nsec = struct.unpack_from("<I q q", raw, p + 8)
                    if (objtype == 1 and common & 0x400 and fileattr & 0x200 and name.endswith(".md")):
                        out[name] = (struct.unpack_from("<q", raw, p + 28)[0], sec * 1_000_000_000 + nsec)
                    off += length
        finally:
            os.close(fd)
    except Exception:
        return None


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
    # UTF-32 is a fixed four-byte unit, so one decode of the whole payload then slicing by width gives the same
    # values (and the same strict error) as decoding each element: 45 -> 10 ms for the 29k slugs and hashes.
    text = bytes(raw).decode("utf-32le")
    return [text[index * width:(index + 1) * width].rstrip("\0") for index in range(count)]


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


# The members grow with the corpus (about 2.2 KB a memory). At 64 MiB the cap sat near 30k memories, and past it
# the hot reader refused every sidecar, so recall and the hooks ran semantic-off with nothing saying why (Mac,
# 2 Nov 2026, the 31,126-row publish). 256 MiB holds about 120k memories and still stops a zip bomb, since the
# check runs on a blob already matched to the pointer's sha256. Raise it again before the corpus gets there.
_LIGHT_MEMBER_CAP = 256 * 1024 * 1024


def _members_over_cap(path, cap):
    """True when the zip at path declares uncompressed members totalling more than cap. Reads only the central
    directory (a few KB), so the light loader can refuse an over-cap sidecar without reading and hashing all of it
    (81 MB, 0.15-0.45 s, on every hook call since the sidecar passed the cap at about 29,850 rows). A refusal here
    is the same refusal the full read reaches; any error returns False and the normal verified path decides."""
    try:
        import zipfile
        with zipfile.ZipFile(path) as archive:
            return sum(info.file_size for info in archive.infolist()) > cap
    except Exception:
        return False


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
        if _LIGHT_MEMBER_CAP and _members_over_cap(actual_sidecar, _LIGHT_MEMBER_CAP):
            return None
        blob, actual_sha, actual_size, _ = read_known_bytes(actual_sidecar, expected_sha)
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
            if sum(info.file_size for info in infos) > _LIGHT_MEMBER_CAP:
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
        slug_set = set(slugs)
        if len(slugs) != len(slug_set):
            return None
        # The same checks over the joined text, not one call per row (about 30 ms on 29k rows). With a "/" separator
        # basename(slug) != slug exactly when "/" is in slug; elsewhere keep the per-row form.
        if os.sep == "/" and not os.altsep:
            joined = "\0".join(slugs)
            if "" in slug_set or "/" in joined or "\\" in joined:
                return None
        elif any(not slug or os.path.basename(slug) != slug or "/" in slug or "\\" in slug
                 for slug in slugs):
            return None
        if set(map(len, text_hashes)) != {64} or not _HEX_RUN_RE.fullmatch("".join(text_hashes)):
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
        memory_stats = _bulk_md_stats(mem_dir)
        if memory_stats is None:
            try:
                with os.scandir(mem_dir) as entries:
                    memory_stats = {
                        entry.name: (st.st_size, st.st_mtime_ns)
                        for entry in entries
                        if entry.name.endswith(".md") and entry.is_file()
                        for st in (entry.stat(),)
                    }
            except OSError:
                return None
        vcache, vnew = _read_verify_cache(mem_dir, expected_sha), 0
        active = {}
        for index, slug in enumerate(slugs):
            fast_current, known, st = False, None, None
            try:
                st = memory_stats[slug + ".md"]
                st_size, st_mtime_ns = st
                fast_current = (
                    bool(row_safe[index])
                    and int(st_size) == int(source_size[index])
                    and int(st_mtime_ns) == int(source_mtime[index])
                )
            except (KeyError, OSError):
                pass
            if not fast_current and st is not None:
                known = _verify_cache_answer(vcache, slug, int(st[0]), int(st[1]), text_hashes[index])
            if fast_current or known:
                active[slug] = index
            elif known is None and st is not None:
                ok = embedding_row_current(probe, mem_dir, slug)
                if ok:
                    active[slug] = index
                try:
                    now = os.stat(os.path.join(mem_dir, slug + ".md"))
                    if (now.st_size, now.st_mtime_ns) == (int(st[0]), int(st[1])):
                        vcache[slug] = [int(st[0]), int(st[1]), text_hashes[index], bool(ok), now.st_ctime_ns]
                        vnew += 1
                except OSError:
                    pass
        if vnew:
            _write_verify_cache(mem_dir, expected_sha, vcache)
        totals_cache = (os.path.join(os.path.dirname(os.path.abspath(actual_sidecar)),
                                     ".embedding_column_totals.json"), actual_sha)
        all_finite, bias = matrix.active_bias(active.values(), totals_cache)
        if not all_finite:
            return None
        loaded = LightweightVerifiedEmbeddings(active, matrix, bias, hashes_by_slug)
        # Same as load_verified_embeddings: keep the build's stat of each row so embedding_row_current answers a
        # later check of an unchanged file from one stat. Without it every spread_recall candidate was re-read and
        # re-hashed on each prompt (about 400 files, 0.2 s of a 0.65 s hook, 1 Nov).
        loaded.row_of, loaded.row_safe = probe.index, row_safe
        loaded.source_size, loaded.source_mtime = source_size, source_mtime
        loaded.verify_cache = vcache
        loaded.verify_key, loaded.built_ns = (mem_dir, expected_sha), _built_ns(built_at)
        if verify_all_rows:
            loaded.index = {
                slug: row
                for slug, row in loaded.index.items()
                if embedding_row_current(loaded, mem_dir, slug, exact=True)
            }
            all_finite, loaded.bias = matrix.active_bias(loaded.index.values())
            if not all_finite:
                return None
        return loaded
    except Exception:
        return None


_SHA_STAT_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".embedding_sha_stat.json")


def read_known_bytes(path, expected_sha):
    """read_stable_bytes, minus the sha256 when this exact file was already hashed to expected_sha (4 Nov 2026).

    The sidecar is content-addressed (its name carries the sha) and is never rewritten in place; a rewrite, copy,
    touch or chmod changes mtime or ctime, and a replaced file changes ino. So a full stat match with a record made
    when the bytes hashed to expected_sha means the same bytes. Saves about 35 ms of every prompt hook (96 MB file).
    EMB_SHA_STAT_CACHE=0 always hashes."""
    if os.environ.get("EMB_SHA_STAT_CACHE", "1") == "0":
        return read_stable_bytes(path)
    with open(path, "rb") as fh:
        before = os.fstat(fh.fileno())
        blob = fh.read()
        after = os.fstat(fh.fileno())
    sig = [after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns]
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != tuple(sig)
            or len(blob) != after.st_size):
        raise OSError("artifact changed while it was being read")
    key = os.path.abspath(path)
    try:
        with open(_SHA_STAT_CACHE, encoding="utf-8") as fh:
            rec = json.load(fh).get(key)
        if isinstance(rec, dict) and rec.get("sha") == expected_sha and rec.get("sig") == sig:
            return blob, expected_sha, len(blob), after.st_mtime_ns
    except Exception:
        pass
    sha = hashlib.sha256(blob).hexdigest()
    if sha == expected_sha:
        try:
            tmp = "%s.%d.tmp" % (_SHA_STAT_CACHE, os.getpid())
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({key: {"sha": sha, "sig": sig}}, fh)
            os.replace(tmp, _SHA_STAT_CACHE)
        except Exception:
            pass
    return blob, sha, len(blob), after.st_mtime_ns


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


class _MdStat(tuple):
    """(st_size, st_mtime_ns) of one listed file: the two fields the loader's stat check reads."""
    __slots__ = ()
    st_size = property(lambda self: self[0])
    st_mtime_ns = property(lambda self: self[1])


_FIND = []


def _md_stats_fast(mem_dir):
    """{name: _MdStat} for the plain .md files in mem_dir from one FindFirstFileExW pass with FindExInfoBasic and
    FIND_FIRST_EX_LARGE_FETCH, or None (not Windows, any API error, or a reparse point, which scandir's is_file would
    follow): the caller then lists with os.scandir as before. Same size and mtime_ns as DirEntry.stat() (checked equal
    on all 37,922 files, 29 Oct); about half the time of scandir on this folder (median 0.42 s against 0.80 s, 8
    alternating rounds), more under disk load. BRAIN_EMB_FASTLIST=0 switches it off."""
    if os.name != "nt" or os.environ.get("BRAIN_EMB_FASTLIST", "1") == "0":
        return None
    try:
        if not _FIND:
            import ctypes
            from ctypes import wintypes

            class _FT(ctypes.Structure):
                _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

            class _WFD(ctypes.Structure):
                _fields_ = [("attr", wintypes.DWORD), ("ctime", _FT), ("atime", _FT), ("mtime", _FT),
                            ("size_hi", wintypes.DWORD), ("size_lo", wintypes.DWORD), ("r0", wintypes.DWORD),
                            ("r1", wintypes.DWORD), ("name", ctypes.c_wchar * 260), ("alt", ctypes.c_wchar * 14),
                            ("x0", wintypes.DWORD), ("x1", wintypes.DWORD), ("x2", wintypes.WORD)]

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            first = k32.FindFirstFileExW
            first.restype = wintypes.HANDLE
            first.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(_WFD), ctypes.c_int, ctypes.c_void_p,
                              wintypes.DWORD]
            nxt = k32.FindNextFileW
            nxt.restype = wintypes.BOOL
            nxt.argtypes = [wintypes.HANDLE, ctypes.POINTER(_WFD)]
            close = k32.FindClose
            close.restype = wintypes.BOOL
            close.argtypes = [wintypes.HANDLE]
            _FIND.append((ctypes, _WFD, first, nxt, close, wintypes.HANDLE(-1).value))
        ctypes, _WFD, first, nxt, close, invalid = _FIND[0]
        d = _WFD()
        h = first(os.path.join(os.fspath(mem_dir), "*"), 1, ctypes.byref(d), 0, None, 2)
        if h is None or h == invalid:
            return None
        out = {}
        try:
            while True:
                name = d.name
                if name.endswith(".md"):
                    attr = d.attr
                    if attr & 0x400:            # reparse point: let scandir decide what it is
                        return None
                    if not attr & 0x10:         # not a directory
                        out[name] = _MdStat((
                            (d.size_hi << 32) | d.size_lo,
                            (((d.mtime.hi << 32) | d.mtime.lo) - 116444736000000000) * 100,
                        ))
                if not nxt(h, ctypes.byref(d)):
                    if ctypes.get_last_error() != 18:   # ERROR_NO_MORE_FILES
                        return None
                    break
        finally:
            close(h)
        return out
    except Exception:
        return None


def _body_row_current(loaded, slug, body):
    """Hash a body the caller already read (same canonical text as embedding_row_current)."""
    try:
        import build_memory_embeddings as builder

        expected = loaded.text_hashes.get(slug)
        if not isinstance(expected, str) or not _SHA256_RE.fullmatch(expected):
            return False
        text = builder._text_for(body)
        return len(text) >= 40 and builder.text_sha256(text) == expected
    except Exception:
        return False


# Resident mode (lever 8 of RECALL-10X, 3 Nov 2026): a long-lived process (brain_recall_server) sets RESIDENT = True
# and keeps the PARSED sidecar between calls, keyed by the file's own stat signature plus the sha256 and size the
# authority pointer names, so a new generation, a replaced file or a moved pointer reads afresh. Which rows are current
# is still judged on every call from the memory folder's stat, exactly as before; only the immutable digest-bound
# arrays are reused. The exact-hash verdict of an exceptional row (stat differs from the NPZ's record) is kept per
# (sidecar sha, slug, size, mtime_ns). Off by default: a one-shot process behaves exactly as before.
RESIDENT = False
_RES = {}


def _parsed_sidecar(actual_sidecar, expected_sha, expected_size, np):
    key = None
    if RESIDENT:
        try:
            st = os.stat(actual_sidecar)
            key = (os.path.abspath(actual_sidecar), expected_sha, expected_size,
                   st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        except OSError:
            key = None
        hit = _RES.get("sidecar")
        if key is not None and hit is not None and hit[0] == key:
            return hit[1]
    blob, actual_sha, actual_size, mtime_ns = read_known_bytes(actual_sidecar, expected_sha)
    if actual_sha != expected_sha or actual_size != expected_size:
        return None
    with np.load(io.BytesIO(blob), allow_pickle=False) as z:
        required = {
            "slugs", "vecs", "model", "built_at", "schema_version", "text_sha256",
            "source_size", "source_mtime_ns", "row_safe",
        }
        if required - set(z.files):
            return None
        parsed = (
            [str(s) for s in z["slugs"]],
            np.asarray(z["vecs"]),
            str(z["model"].item()),
            str(z["built_at"].item()),
            int(z["schema_version"].item()),
            [str(h) for h in z["text_sha256"]],
            np.asarray(z["source_size"]),
            np.asarray(z["source_mtime_ns"]),
            np.asarray(z["row_safe"]),
        )
    # keep only what was read under the same signature it was keyed by
    if key is not None and key[5] == actual_size and key[6] == mtime_ns:
        for arr in parsed[1:2] + parsed[6:]:
            arr.setflags(write=False)
        _RES.clear()
        _RES["sidecar"] = (key, parsed)
    return parsed


def _row_current_cached(probe, mem_dir, slug, memory_stats, sidecar_sha):
    if not RESIDENT:
        return embedding_row_current(probe, mem_dir, slug)
    st = memory_stats.get(slug + ".md")
    if st is None:
        return embedding_row_current(probe, mem_dir, slug)
    k = (sidecar_sha, slug, int(st.st_size), int(st.st_mtime_ns))
    verdicts = _RES.setdefault("rows", {})
    if k in verdicts:
        return verdicts[k]
    ok = embedding_row_current(probe, mem_dir, slug)
    if len(verdicts) > 20000:
        verdicts.clear()
    verdicts[k] = ok
    return ok


def load_verified_embeddings(sidecar, authority_path, mem_dir=None, expected_model=None,
                             verify_all_rows=False, bodies=None, memory_entries=None):
    """Return a slug index, float32 matrix, and cosine bias, or fail closed to None.

    The publication-host stat snapshot and row-safety mask live inside the digest-bound schema-v3
    NPZ, never in mutable pointer JSON. Hot-path callers stat each row and exact-hash only semantic
    participants; offline/release callers can request an exact hash of every active row.

    memory_entries: the .md DirEntry objects of mem_dir from a listing the caller already took (brain.py's one
    listing per recall). Their stat is cached on the entry, so the main thread's pass and this one cost one stat per
    file instead of two; the separate scandir and stat pass here was 0.5-0.75 s on the loaded Mac (30 Oct 2026).
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
        parsed = _parsed_sidecar(actual_sidecar, expected_sha, expected_size, np)
        if parsed is None:
            return None
        (slugs, vecs, model, built_at, schema_version, text_hashes, source_size, source_mtime,
         row_safe) = parsed

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
        if os.environ.get("BRAIN_EMB_LAZY") == "1" and not verify_all_rows:
            # rows are judged current by the caller, best first (embedding_row_current)
            _mean = vecs.mean(axis=0)
            _b = float(_mean @ _mean)
            return VerifiedEmbeddings({slug: i for i, slug in enumerate(slugs)}, vecs,
                                      _b if math.isfinite(_b) and 0.0 <= _b < 0.95 else 0.0, hashes_by_slug)
        memory_stats = _md_stats_fast(mem_dir)
        if memory_stats is None and memory_entries is not None:
            try:
                memory_stats = {entry.name: entry.stat() for entry in memory_entries}
            except OSError:
                return None
        if memory_stats is None:
            try:
                with os.scandir(mem_dir) as entries:
                    memory_stats = {
                        entry.name: entry.stat()
                        for entry in entries
                        if entry.name.endswith(".md") and entry.is_file()
                    }
            except OSError:
                return None
        # plain Python values: indexing a numpy array per row was most of this loop
        safe_l, size_l, mtime_l = row_safe.tolist(), source_size.tolist(), source_mtime.tolist()
        vcache, vnew = _read_verify_cache(mem_dir, expected_sha), 0
        active = {}
        for i, slug in enumerate(slugs):
            fast_current, known, st = False, None, None
            try:
                st = memory_stats[slug + ".md"]
                fast_current = (
                    safe_l[i] and st.st_size == size_l[i]
                    and st.st_mtime_ns == mtime_l[i]
                )
            except (KeyError, OSError):
                pass
            if not fast_current and st is not None:
                known = _verify_cache_answer(vcache, slug, st.st_size, st.st_mtime_ns, text_hashes[i])
            if fast_current or known:
                active[slug] = i
            elif known is None and st is not None:
                # A benign touch, restored stale row, or conservative adoption mask must not suppress
                # a current memory. Exact-hash only these exceptional rows; the normal hot path is stat-only.
                ok = _row_current_cached(probe, mem_dir, slug, memory_stats, expected_sha)
                if ok:
                    active[slug] = i
                try:
                    now = os.stat(os.path.join(mem_dir, slug + ".md"))
                    if (now.st_size, now.st_mtime_ns) == (st.st_size, st.st_mtime_ns):
                        vcache[slug] = [st.st_size, st.st_mtime_ns, text_hashes[i], bool(ok), now.st_ctime_ns]
                        vnew += 1
                except OSError:
                    pass
        if vnew:
            _write_verify_cache(mem_dir, expected_sha, vcache)
        def active_bias(index):
            if not index:
                return 0.0
            mean = vecs[list(index.values())].mean(axis=0)
            value = float(mean @ mean)
            return value if math.isfinite(value) and 0.0 <= value < 0.95 else 0.0

        loaded = VerifiedEmbeddings(
            active, vecs, active_bias(active), hashes_by_slug
        )
        # The build's own stat of each source (row_safe rows only), so embedding_row_current can answer a later
        # check of an unchanged file from one stat, the rule this loader applies above (30 Oct: recall re-hashed
        # ~360 rows the load had just accepted, 0.4 s of a 2.4 s hook recall).
        loaded.row_of, loaded.row_safe = probe.index, row_safe
        loaded.source_size, loaded.source_mtime = source_size, source_mtime
        loaded.verify_cache = vcache
        loaded.verify_key, loaded.built_ns = (mem_dir, expected_sha), _built_ns(built_at)
        if verify_all_rows:
            # bodies={"<slug>.md": (text, st_size, st_mtime_ns)} from a caller that already read the folder
            # (dream.py, stat taken BEFORE its read). A body is hashed in place of a fresh read only when the
            # file's size and mtime now are the ones recorded at that read; anything else is read again as before.
            def _row_ok(slug):
                ent = bodies.get(slug + ".md") if bodies else None
                st = memory_stats.get(slug + ".md") if ent is not None else None
                if (st is not None and len(ent) == 3 and int(st.st_size) == int(ent[1])
                        and int(st.st_mtime_ns) == int(ent[2])):
                    return _body_row_current(loaded, slug, ent[0])
                return embedding_row_current(loaded, mem_dir, slug, exact=True)
            loaded.index = {
                slug: row for slug, row in loaded.index.items()
                if _row_ok(slug)
            }
            loaded.bias = active_bias(loaded.index)
        return loaded
    except Exception:
        return None


# Exact-hash results kept across processes, per (sidecar sha, memory folder). The build's stats hold only for the
# folder it read: a git checkout of the same memories (BRAIN_MEM_DIR on the ai-memories clone, as the recall tests
# set it) has other mtimes, so every row fell to an exact hash, 28k file reads and ~7.5 s a recall (3 Nov 2026). A
# row hashed once is answered from its unchanged size and mtime, the loader's own rule for the build's stats.
def _verify_cache_path(mem_dir):
    key = hashlib.sha256(os.path.realpath(mem_dir).encode("utf-8")).hexdigest()[:16]
    # derived and rebuildable, so outside the brain tree that is synced and never deleted
    base = os.environ.get("BRAIN_EMB_VERIFY_DIR") or os.path.expanduser(os.path.join("~", ".cache", "brain", "embedding_verify"))
    return os.path.join(base, key + ".json")


def _read_verify_cache(mem_dir, sidecar_sha):
    if os.environ.get("BRAIN_EMB_VERIFY_CACHE", "1") == "0":
        return {}
    try:
        with io.open(_verify_cache_path(mem_dir), encoding="utf-8") as fh:
            c = json.load(fh)
        if c.get("sidecar_sha256") == sidecar_sha and c.get("mem_dir") == os.path.realpath(mem_dir):
            rows = c.get("rows")
            return rows if isinstance(rows, dict) else {}
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def _verify_cache_answer(cache, slug, size, mtime_ns, expected):
    """True/False when this row was exact-hashed at this size and mtime against this text hash, else None."""
    c = cache.get(slug) if cache else None
    if (isinstance(c, list) and len(c) in (4, 5) and c[0] == size and c[1] == mtime_ns
            and c[2] == expected):
        return bool(c[3])
    return None


def _verify_cache_exact(cache, slug, st, expected):
    """True/False when this row was exact-hashed at this size, mtime and ctime against this text hash, else None.
    The ctime is what lets the answer stand in for a hash: putting an edited file's mtime back moves its ctime."""
    c = cache.get(slug) if cache else None
    if (isinstance(c, list) and len(c) == 5 and c[0] == st.st_size and c[1] == st.st_mtime_ns
            and c[2] == expected and c[4] == st.st_ctime_ns):
        return bool(c[3])
    return None


def _built_ns(built_at):
    """The sidecar's UTC ISO-8601 built_at as integer nanoseconds, or None."""
    try:
        from datetime import datetime
        if isinstance(built_at, (list, tuple)):
            built_at = built_at[0]
        dt = datetime.fromisoformat(str(built_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return int(dt.timestamp()) * 1_000_000_000 + dt.microsecond * 1000
    except (ValueError, TypeError, IndexError):
        return None


_VC_DIRTY = {}   # (mem_dir, sidecar sha) -> rows hashed by embedding_row_current in this process, written at exit


def _vc_note(loaded, slug, entry):
    key = getattr(loaded, "verify_key", None)
    cache = getattr(loaded, "verify_cache", None)
    if key is None or not isinstance(cache, dict) or os.environ.get("BRAIN_EMB_VERIFY_CACHE", "1") == "0":
        return
    cache[slug] = entry
    if not _VC_DIRTY:
        import atexit
        atexit.register(_vc_flush)
    _VC_DIRTY.setdefault(key, {})[slug] = entry


def _vc_flush():
    """Merge this process's hashed rows into the cache on disk (re-read first, so other writers' rows survive)."""
    for (mem_dir, sha), rows in list(_VC_DIRTY.items()):
        merged = _read_verify_cache(mem_dir, sha)
        merged.update(rows)
        _write_verify_cache(mem_dir, sha, merged)
    _VC_DIRTY.clear()


def _write_verify_cache(mem_dir, sidecar_sha, rows):
    if os.environ.get("BRAIN_EMB_VERIFY_CACHE", "1") == "0":
        return
    path = _verify_cache_path(mem_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".verify.", suffix=".tmp", dir=os.path.dirname(path))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"sidecar_sha256": sidecar_sha, "mem_dir": os.path.realpath(mem_dir), "rows": rows}, fh,
                      separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except (OSError, NameError):
            pass


def embedding_row_current(loaded, mem_dir, slug, exact=False):
    """Hash one semantic participant's canonical text; false on any race or mismatch. Unless exact, a row_safe row
    whose file still has the size and mtime the build recorded, and whose ctime is no later than the build, is current
    on that stat (until 3 Nov the ctime was not checked, so a same-size edit with its mtime put back passed). A row
    this process or an earlier one hashed at the same size, mtime and ctime is answered from the verify cache."""
    try:
        import build_memory_embeddings as builder

        expected = loaded.text_hashes.get(slug)
        if not isinstance(expected, str) or not _SHA256_RE.fullmatch(expected):
            return False
        if os.path.basename(slug) != slug or "/" in slug or "\\" in slug:
            return False
        path = os.path.join(mem_dir, slug + ".md")
        before = os.stat(path)
        row = None if exact else getattr(loaded, "row_of", {}).get(slug)
        built_ns = getattr(loaded, "built_ns", None)
        # the build's own stats still answer, but only for a file whose metadata has not moved since the build
        if (row is not None and built_ns is not None and before.st_ctime_ns <= built_ns
                and bool(loaded.row_safe[row]) and before.st_size == int(loaded.source_size[row])
                and before.st_mtime_ns == int(loaded.source_mtime[row])):
            return True
        known = None if exact else _verify_cache_exact(getattr(loaded, "verify_cache", None), slug, before, expected)
        if known is not None:
            return known
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            body = fh.read()
        after = os.stat(path)
        before_sig = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        after_sig = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if before_sig != after_sig:
            return False
        text = builder._text_for(body)
        ok = len(text) >= 40 and builder.text_sha256(text) == expected
        _vc_note(loaded, slug, [before.st_size, before.st_mtime_ns, expected, bool(ok), before.st_ctime_ns])
        return ok
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
