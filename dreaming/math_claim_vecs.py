"""math_claim_vecs.py - whether the math store's claim vectors cover its claims, and adding the ones they lack.

    python math_claim_vecs.py status [--store PATH]     one line; exit 1 when the vectors do not match the store
    python math_claim_vecs.py extend [--store PATH]     embed the claims the file lacks and re-embed the ones whose text
                                                        changed; a file that records no store digests is left alone
    python math_claim_vecs.py migrate [--store PATH]    once, for a file that records no store digests: check every row
                                                        against its claim's current text and record what was found
    python math_claim_vecs.py rebuild [--out PATH]      re-embed every legacy row from its claim's current text (with
                                                        --out, into a separate file, the live one untouched)
    python math_claim_vecs.py seal [--store PATH]       write the SHA-256 manifest for the file as it is now (after a
                                                        restore by hand, or once for a file written before manifests)

The math hook's meaning channel keeps one vector per claim in claim_embeddings.npz beside the store. Nothing refreshed
the file after an ingest: a claim filed today had no vector until someone ran the builder by hand, so the meaning channel
could not name it, and the hook's agreement check (the words' first card must also be the meaning's first claim) picked
its winner from whatever claims the file held. When the new claim is the right answer and an older one leads on the
words and wins on meaning among the rest, the two agree on the older card and the gate yields to it (scheduled audit
account01/B09 r31, 29 Sep 2026, the store-binding caveat of R68).

coverage() names the current claims without a vector and the vectors that name no node of the store; the hook's agreement
check counts nothing while either list is non-empty (math_sense._claim_vectors_cover_store holds the same rule, kept in
the hook so it has no import to lose). extend() embeds the missing claims the way the live file was extended on 26 Sep -
the claim's body through the brain's ONNX bge-small (query_embed.embed), which reproduces those 390 vectors to cosine
1.000000 - and replaces the file only after writing the whole new one. The math transport calls extend() on every run, as
it rebuilds the prefilter index.

THE TEXT IS BOUND TOO (29 Sep, scheduled audits account03/B12 r56 and account04/B09 r75). Ids alone let a file made from
one store pass against another store that holds the same ids with other text. The file records, per row, the store's
digest of the node the vector goes with (node_digests), and a row whose digest differs from the store's counts as a claim
without a current vector: extend() re-embeds it, and the hook names nobody until it has. A file that records no digests
is unbound; the hook names nobody over it, and extend() leaves it for migrate().

migrate() binds such a file without taking it on trust: every row is re-embedded from its claim's current text (the
body, or the title when the body is empty) and compared with the stored vector. A row reproduced at cosine VERIFY_COS or
more is recorded as made from that text. A row that is not keeps its vector and is recorded as legacy, with its cosine
to the text, because the text it was made from is unknown: on 29 Sep the live file had 6,229 such rows, the 15 Sep
build's, which record no recipe and which no candidate text of their claims reproduces (cosines 0.681-0.9972 with the
body; nine candidate texts tried on a sample of 40). The digests bind every row to the store from then on; row_source
says which rows were shown to come from their text.

rebuild() re-embeds the legacy rows from their claims' current text, so that the file holds no vector of unrecorded
text (scheduled audits account04/B06 r76 and account04/B14 r8: a legacy row carried its node's current digest, and the
hook's check read the digest and not row_source, so a vector of unknown text passed as bound). Since then a legacy row
counts as a row without a vector of its current text: status() reports it, the hook names nobody while one is in the
file, and extend() re-embeds it as it re-embeds a row whose text changed.

EVERY FILE WRITTEN HERE CARRIES ITS SHA-256 (29 Sep, scheduled audits account03/B11 r90 and account04/B17 r74). The loader
used to tell one file from another by its inode, mtime and size and by each zip member's CRC-32, and a CRC-32 can be forced:
two valid files with different vectors kept every field equal, so a long-lived process kept the old vectors while a fresh
one read the new. The loader now keys on the SHA-256 of the bytes it reads, and every writer here publishes that digest
beside the file, as claim_embeddings.npz.sha256 in sha256sum's format; bytes the manifest does not name are refused by
every process, fresh or long-lived. The manifest is replaced first and the file second, and if the file cannot be replaced
the old manifest is put back, so a crash in between leaves a new manifest over the old file: the channel stays closed,
status says so, and the next extend() writes the file again and heals it. A dated copy gets its own manifest. To restore
one by hand, copy the file and its .sha256 together, or run `seal` after copying the file.
"""
import argparse
import hashlib
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.environ.get("MATHBRAIN_STORE", os.path.expanduser(r"~/math-brain-build/local/population/brain.sqlite3"))
FILE_NAME = "claim_embeddings.npz"
# The file's composition when it can be extended here: one vector per row of the claims table, embedded by its body.
# A full build of build_claim_vecs.py embeds title + body of every current node instead; mixing the two recipes in one
# file would score half the claims on a different text, so such a file is reported, not extended.
EXTENDABLE_RULES = ("", "body, every claim of the claims table (missing-only extension)")
REPLACE_TRIES = 40          # the hook holds the file open for a few tens of ms while it loads; Windows refuses a replace then
MANIFEST_SUFFIX = ".sha256"  # beside the file: "<SHA-256 of its bytes>  claim_embeddings.npz", sha256sum's format
VERIFY_COS = 0.9999         # a stored vector counts as made from a text when re-embedding the text reproduces it this well
MODEL_NAME = "BAAI/bge-small-en-v1.5"  # the encoder query_embed runs and math_brain.semantic checks the file's "model" for


def vector_path(store=STORE):
    return os.environ.get("MATH_BRAIN_CLAIM_VECS") or os.path.join(os.path.dirname(os.path.abspath(store)), FILE_NAME)


def universe(store=STORE):
    """(claims, superseded, digests): the claims table's ids, the targets of supersedes edges, and every node's stored
    digest by id; one snapshot."""
    con = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(store).replace(os.sep, "/"), uri=True, timeout=10)
    try:
        con.execute("BEGIN")
        claims = {r[0] for r in con.execute("select id from claims")}
        superseded = {r[0] for r in con.execute("select target_id from edges where kind = 'supersedes'")}
        digests = {r[0]: str(r[1]) for r in con.execute("select id, digest from nodes")}
        return claims, superseded, digests
    finally:
        con.close()


def check(ids, digests, store=STORE, sources=None):
    """{missing, phantom, drifted, legacy, duplicates, unbound}: current claims with no vector (a superseded claim needs
    none); vector ids naming no node of the store; rows whose recorded digest is not the store's (a superseded node's row
    is not judged); rows of unknown text (row_source legacy), whatever digest they carry; ids that name more than one row,
    superseded or not (account04/B06 r75: one claim, two voices); and whether the file records digests at all. Digests
    or sources that do not line up with the rows raise ValueError."""
    claims, superseded, node_digest = universe(store)
    have = [str(i) for i in ids]
    drifted, legacy = [], []
    if digests is not None:
        if len(digests) != len(have):
            raise ValueError("the file records %d store digests for %d vectors" % (len(digests), len(have)))
        drifted = sorted(i for i, d in zip(have, digests)
                         if i in node_digest and i not in superseded and node_digest[i] != str(d))
    if sources is not None:
        if len(sources) != len(have):
            raise ValueError("the file records %d row sources for %d vectors" % (len(sources), len(have)))
        legacy = sorted(i for i, s in zip(have, sources) if i in node_digest and str(s) == "legacy")
    return {"missing": sorted(claims - set(have) - superseded), "phantom": sorted(set(have) - set(node_digest)),
            "drifted": drifted, "legacy": legacy, "duplicates": _duplicates(have), "unbound": digests is None}


def _duplicates(ids):
    """The ids that name more than one row, sorted."""
    seen, dup = set(), set()
    for i in ids:
        (dup if i in seen else seen).add(i)
    return sorted(dup)


def coverage(ids, store=STORE, digests=None, sources=None):
    """(missing, phantom), a row made from other text, of unknown text, or whose id names another row too, counting as
    missing."""
    c = check(ids, digests, store, sources)
    return sorted(set(c["missing"]) | set(c["drifted"]) | set(c["legacy"]) | set(c["duplicates"])), c["phantom"]


def _read(path):
    import numpy as np
    # no pickle: every field of this file is a float, bool or fixed-width string array (account04/B17 r14)
    with open(path, "rb") as fh:
        data = np.load(fh, allow_pickle=False)
        return {k: data[k] for k in data.files}


def _claim_text(title, body):
    """(text, source) a claim is embedded from: its body, or its title when the body is empty (one claim of 29 Sep keeps
    its whole statement in the title; an empty body made extend() refuse every run until someone fixed it)."""
    return (body, "body") if (body or "").strip() else ((title or "").strip(), "title")


def _texts(store, ids):
    """{id: (text, source, digest)}: the text each claim is embedded from (_claim_text), read in the same row as its
    digest, so the digest a file records is always the digest of the text that was embedded or compared (account04/B09
    r75, case 4)."""
    ids = list(ids)
    if not ids:
        return {}
    con = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(store).replace(os.sep, "/"), uri=True, timeout=10)
    try:
        con.execute("BEGIN")
        out = {}
        for k in range(0, len(ids), 500):
            part = ids[k:k + 500]
            out.update({i: _claim_text(t, b) + (str(g),) for i, t, b, g in con.execute(
                "select id, title, body, digest from nodes where id in (%s)" % ",".join("?" * len(part)), part)})
        return out
    finally:
        con.close()


def _take_lock(path):
    """An exclusive, non-blocking OS lock on path + ".lock", or None when another run holds it. The OS drops the lock
    with its holder, so a crashed run leaves nothing to clear; the lock file itself stays."""
    fd = os.open(path + ".lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


def _drop_lock(fd):
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, 0)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _replace(tmp, path):
    for k in range(REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if k == REPLACE_TRIES - 1:
                raise
            time.sleep(0.25)


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def manifest_digest(path):
    """The SHA-256 the manifest beside path names, None when there is none; ValueError when it holds no SHA-256."""
    try:
        with open(path + MANIFEST_SUFFIX, "rb") as fh:
            words = fh.read(4096).decode("ascii", "replace").split()
    except FileNotFoundError:
        return None
    digest = words[0].lower() if words else ""
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("%s%s does not hold a SHA-256" % (path, MANIFEST_SUFFIX))
    return digest


def _write_manifest(path, digest):
    tmp = "%s%s.tmp%d" % (path, MANIFEST_SUFFIX, os.getpid())
    with open(tmp, "w", encoding="ascii", newline="\n") as fh:
        fh.write("%s  %s\n" % (digest, os.path.basename(path)))
    _replace(tmp, path + MANIFEST_SUFFIX)


def _publish(tmp, path):
    """Replace path with the finished file tmp, its SHA-256 manifest first. A failed replace puts the old manifest back
    (or removes the new one when there was none), so the manifest never names a file that is not there, except for
    the moment between the two replaces and after a crash in it, when the loader refuses the old file until the next
    write."""
    digest = _sha256_file(tmp)
    try:
        before = manifest_digest(path)
    except ValueError:
        before = None
    _write_manifest(path, digest)
    try:
        _replace(tmp, path)
    except BaseException:
        if before is None:
            try:
                os.remove(path + MANIFEST_SUFFIX)
            except OSError:
                pass
        else:
            _write_manifest(path, before)
        raise
    return digest


def _encoder(embed):
    if embed is not None:
        return embed
    sys.path.insert(0, HERE)
    import query_embed
    return query_embed.embed


def _embedded(embed, text, dim):
    """The vector of text as the encoder returns it, or None when it cannot be embedded or comes back malformed."""
    import numpy as np
    v = embed(text)
    if v is None:
        return None
    v = np.asarray(v, dtype=np.float32).reshape(-1)
    if v.shape[0] != dim or not np.isfinite(v).all() or not np.any(v):
        return None
    return v


def _write(path, out, checks):
    """Keep a dated copy of the file before its first rewrite of the day, write the new file beside it, read it back,
    and replace the old one only if every check holds. checks(back) -> True when the new file reads back as intended."""
    backup = "%s.bak_%s" % (path, time.strftime("%Y%m%d", time.gmtime()))
    if not os.path.exists(backup):
        with open(path, "rb") as src, open(backup + ".tmp", "wb") as dst:
            dst.write(src.read())
        _publish(backup + ".tmp", backup)
    import numpy as np
    tmp = "%s.tmp%d.npz" % (path, os.getpid())
    np.savez(tmp, **out)
    if not checks(_read(tmp)):
        return "the new file did not read back as intended; left at %s" % tmp
    _publish(tmp, path)
    return ""


def extend(store=STORE, path=None, embed=None):
    """Bring the vector file into line with the store; returns (changed, total, note). note is "" when all is well.

    Embeds the current claims the file lacks and re-embeds the rows whose claim's digest changed, both by body, and
    records each such row's store digest. Nothing is written when nothing needs it, when the file is not there (a first
    build is a deliberate step), when it records no store digests (migrate() binds it), when a vector names a node the
    store lacks, when its recipe is not the claims-table one, or when a claim cannot be embedded. A dated copy of the file
    is kept before its first rewrite of the day."""
    path = path or vector_path(store)
    if not os.path.exists(path):
        return 0, 0, "no claim vectors at %s; a first build is build_claim_vecs.py's job" % path
    # One run at a time: two runs that read the same file would each write a replacement built from it, and the later
    # replace would drop the earlier one's rows (account04/B09 r75, case 7). The second run reads the first's file on
    # its next call.
    fd = _take_lock(path)
    if fd is None:
        return 0, 0, "another run holds %s.lock; not extended this time" % path
    try:
        return _extend_locked(store, path, embed)
    finally:
        _drop_lock(fd)


def _extend_locked(store, path, embed):
    import numpy as np
    fields = _read(path)
    ids = [str(x) for x in fields["slugs"]]
    dup = _duplicates(ids)
    if dup:
        # a map from id to row would keep one of the rows and drop the other unseen (account04/B06 r75)
        return 0, len(ids), "%d id(s) name more than one row (e.g. %s); not extended; a full build replaces the file" % (
            len(dup), dup[0])
    if "node_digests" not in fields:
        return 0, len(ids), ("the file records no store digests; not extended until python math_claim_vecs.py migrate "
                             "has checked its rows against the store")
    recorded = [str(x) for x in fields["node_digests"]]
    if len(recorded) != len(ids):
        return 0, len(ids), ("the file records %d store digests for %d vectors; not extended (a full build with "
                             "build_claim_vecs.py replaces it)" % (len(recorded), len(ids)))
    claims, superseded, node_digest = universe(store)
    phantom = sorted(set(ids) - set(node_digest))
    if phantom:
        return 0, len(ids), "%d vector(s) name no node of the store (e.g. %s); not extended" % (len(phantom), phantom[0])
    missing = sorted(claims - set(ids) - superseded)
    # a row of unknown text is re-embedded like a row whose text changed: the channel stays closed while one is in the
    # file, so it is not left for someone to notice (account04/B06 r76)
    src_in = [str(s) for s in fields["row_source"]] if "row_source" in fields else None
    drifted = [i for k, (i, d) in enumerate(zip(ids, recorded))
               if (i not in superseded and node_digest[i] != d) or (src_in is not None and src_in[k] == "legacy")]
    if not missing and not drifted:
        return 0, len(ids), ""
    todo = missing + drifted
    rule = str(fields["text_rule"]) if "text_rule" in fields else ""
    if rule not in EXTENDABLE_RULES:
        return 0, len(ids), ("%d claim(s) need a vector, and the file's recipe is %r, not the claims-table one; "
                             "rebuild with build_claim_vecs.py --incremental" % (len(todo), rule))
    embed = _encoder(embed)
    texts = _texts(store, todo)
    dim = fields["vecs"].shape[1]
    made = {}
    for cid in todo:
        v = _embedded(embed, texts.get(cid, ("", "", ""))[0], dim)
        if v is None:
            return 0, len(ids), "claim %s could not be embedded (no text, no encoder or a bad vector); nothing written" % cid
        made[cid] = v
    old = np.asarray(fields["vecs"], dtype=np.float32)
    rows = old.copy()
    pos = {cid: k for k, cid in enumerate(ids)}
    for cid in drifted:
        rows[pos[cid]] = made[cid]
    out = dict(fields)
    out["slugs"] = np.array(ids + missing)
    out["vecs"] = np.vstack([rows] + ([np.stack([made[c] for c in missing])] if missing else []))
    # a re-embedded or added row records the digest read with the text it was made from; a kept row keeps its own,
    # which equals the store's or the row would have been re-embedded
    digests_out = [texts[i][2] if i in made else recorded[pos[i]] for i in ids + missing]
    out["node_digests"] = np.array(digests_out)
    if "row_source" in fields:
        src = [str(s) for s in fields["row_source"]]
        cos = [float(c) for c in fields["row_body_cos"]] if "row_body_cos" in fields else [1.0] * len(ids)
        for cid in drifted:
            src[pos[cid]], cos[pos[cid]] = texts[cid][1], 1.0
        out["row_source"] = np.array(src + [texts[c][1] for c in missing])
        out["row_body_cos"] = np.array(cos + [1.0] * len(missing), dtype=np.float32)
    bodies = {c: texts[c][0] for c in made}         # the texts embedded: bodies, or titles where a body is empty
    if "text_hashes" in fields:
        hashes = [str(h) for h in fields["text_hashes"]]
        for cid in drifted:
            hashes[pos[cid]] = hashlib.sha256(bodies[cid].encode("utf-8")).hexdigest()[:16]
        out["text_hashes"] = np.array(hashes + [hashlib.sha256(bodies[c].encode("utf-8")).hexdigest()[:16]
                                                for c in missing])
    prev_sha = str(fields.get("text_sha256", ""))
    out["text_sha256"] = np.array("ext:" + hashlib.sha256(
        (prev_sha + "\n" + "\n".join(bodies[c] for c in todo)).encode("utf-8")).hexdigest()[:16])
    what = []
    if missing:
        what.append("+%d claims by body" % len(missing))
    if drifted:
        what.append("%d re-embedded by body" % len(drifted))
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out["built_at"] = np.array("%s; %s %s" % (str(fields.get("built_at", "")), ", ".join(what), stamp))
    kept = [k for k, cid in enumerate(ids) if cid not in set(drifted)]
    note = _write(path, out, lambda back: (
        [str(x) for x in back["slugs"]] == ids + missing and np.array_equal(back["vecs"][kept], old[kept])
        and [str(x) for x in back["node_digests"]] == digests_out))
    return (0 if note else len(todo)), len(ids) + (0 if note else len(missing)), note


def migrate(store=STORE, path=None, embed=None):
    """Bind a file that records no store digests by checking every row against its claim's current body; returns
    (from_body, legacy, total, note). A file that already records them is left as it is (extend() keeps it current).
    Nothing is written when a vector names a node the store lacks, when the recipe is not the claims-table one, or
    when a body cannot be embedded."""
    path = path or vector_path(store)
    if not os.path.exists(path):
        return 0, 0, 0, "no claim vectors at %s" % path
    fd = _take_lock(path)
    if fd is None:
        return 0, 0, 0, "another run holds %s.lock; not migrated this time" % path
    try:
        return _migrate_locked(store, path, embed)
    finally:
        _drop_lock(fd)


def _migrate_locked(store, path, embed):
    import numpy as np
    fields = _read(path)
    ids = [str(x) for x in fields["slugs"]]
    dup = _duplicates(ids)
    if dup:
        return 0, 0, len(ids), "%d id(s) name more than one row (e.g. %s); not migrated" % (len(dup), dup[0])
    if "node_digests" in fields:
        return 0, 0, len(ids), ""
    rule = str(fields["text_rule"]) if "text_rule" in fields else ""
    if rule not in EXTENDABLE_RULES:
        return 0, 0, len(ids), ("the file's recipe is %r, not the claims-table one; a full build with "
                                "build_claim_vecs.py records its own digests" % rule)
    texts = _texts(store, ids)
    phantom = sorted(set(ids) - set(texts))
    if phantom:
        return 0, 0, len(ids), "%d vector(s) name no node of the store (e.g. %s); not migrated" % (len(phantom), phantom[0])
    embed = _encoder(embed)
    old = np.asarray(fields["vecs"], dtype=np.float32)
    unit = old / np.maximum(np.linalg.norm(old, axis=1, keepdims=True), 1e-12)
    dim = old.shape[1]
    source, cosines = [], []
    for k, cid in enumerate(ids):
        text, how, _g = texts[cid]
        if not text.strip():    # nothing to compare with: kept, as a vector of unrecorded text
            source.append("legacy")
            cosines.append(float("nan"))
            continue
        v = _embedded(embed, text, dim)
        if v is None:           # a text that will not embed means the encoder failed, not the row
            return 0, 0, len(ids), "claim %s could not be embedded (no encoder or a bad vector); nothing written" % cid
        c = float(unit[k] @ (v / float(np.linalg.norm(v))))
        source.append(how if c >= VERIFY_COS else "legacy")
        cosines.append(c)
    digests = [texts[c][2] for c in ids]
    out = dict(fields)
    out["node_digests"] = np.array(digests)
    out["row_source"] = np.array(source)
    out["row_body_cos"] = np.array(cosines, dtype=np.float32)
    from_body = len(ids) - source.count("legacy")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out["built_at"] = np.array("%s; store digests recorded, %d rows reproduced from their text, %d legacy %s" % (
        str(fields.get("built_at", "")), from_body, len(ids) - from_body, stamp))
    note = _write(path, out, lambda back: (
        [str(x) for x in back["slugs"]] == ids and np.array_equal(back["vecs"], old)
        and [str(x) for x in back["node_digests"]] == digests and [str(x) for x in back["row_source"]] == source))
    return (0, 0, len(ids), note) if note else (from_body, len(ids) - from_body, len(ids), "")


def rebuild(store=STORE, path=None, embed=None, out=None):
    """Re-embed every row the file records as legacy from its claim's current text (the body, or the title when the body
    is empty), so that every row is shown to come from its text; returns (rebuilt, total, note). The other rows are kept
    as they are. With out, the rebuilt file is written there and the live file is left alone (a measurement comes before
    it replaces the live one); without, the live file is replaced under the lock, after a dated copy, as extend() does."""
    path = path or vector_path(store)
    if not os.path.exists(path):
        return 0, 0, "no claim vectors at %s" % path
    fd = _take_lock(path)
    if fd is None:
        return 0, 0, "another run holds %s.lock; not rebuilt this time" % path
    try:
        return _rebuild_locked(store, path, embed, out)
    finally:
        _drop_lock(fd)


def _rebuild_locked(store, path, embed, out_path):
    import numpy as np
    fields = _read(path)
    ids = [str(x) for x in fields["slugs"]]
    dup = _duplicates(ids)
    if dup:
        return 0, len(ids), "%d id(s) name more than one row (e.g. %s); not rebuilt" % (len(dup), dup[0])
    if "node_digests" not in fields or "row_source" not in fields:
        return 0, len(ids), "the file does not record which rows came from their text; python math_claim_vecs.py migrate first"
    recorded = [str(x) for x in fields["node_digests"]]
    source = [str(s) for s in fields["row_source"]]
    if not (len(recorded) == len(source) == len(ids)):
        return 0, len(ids), "the file's digests or row sources do not line up with its %d vectors" % len(ids)
    legacy = [i for i, s in zip(ids, source) if s == "legacy"]
    if not legacy:
        return 0, len(ids), ""
    texts = _texts(store, legacy)
    gone = sorted(set(legacy) - set(texts))
    if gone:
        return 0, len(ids), "%d legacy row(s) name no node of the store (e.g. %s); not rebuilt" % (len(gone), gone[0])
    embed = _encoder(embed)
    old = np.asarray(fields["vecs"], dtype=np.float32)
    dim = old.shape[1]
    made = {}
    for cid in legacy:
        text = texts[cid][0]
        v = _embedded(embed, text, dim) if text.strip() else None
        if v is None:
            return 0, len(ids), "claim %s could not be embedded (no text, no encoder or a bad vector); nothing written" % cid
        made[cid] = v
    pos = {cid: k for k, cid in enumerate(ids)}
    rows = old.copy()
    digests = list(recorded)
    cos = [float(c) for c in fields["row_body_cos"]] if "row_body_cos" in fields else [1.0] * len(ids)
    for cid, v in made.items():
        k = pos[cid]
        rows[k], digests[k], source[k], cos[k] = v, texts[cid][2], texts[cid][1], 1.0
    out = dict(fields)
    out["vecs"] = rows
    out["node_digests"] = np.array(digests)
    out["row_source"] = np.array(source)
    out["row_body_cos"] = np.array(cos, dtype=np.float32)
    if "text_hashes" in fields:
        hashes = [str(h) for h in fields["text_hashes"]]
        for cid in made:
            hashes[pos[cid]] = hashlib.sha256(texts[cid][0].encode("utf-8")).hexdigest()[:16]
        out["text_hashes"] = np.array(hashes)
    out["text_sha256"] = np.array("reb:" + hashlib.sha256((str(fields.get("text_sha256", "")) + "\n" + "\n".join(
        texts[c][0] for c in legacy)).encode("utf-8")).hexdigest()[:16])
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out["built_at"] = np.array("%s; %d legacy rows re-embedded from their text %s" % (
        str(fields.get("built_at", "")), len(made), stamp))
    kept = [k for k, cid in enumerate(ids) if cid not in made]

    def checks(back):
        return ([str(x) for x in back["slugs"]] == ids and np.array_equal(back["vecs"][kept], old[kept])
                and [str(x) for x in back["node_digests"]] == digests
                and "legacy" not in {str(s) for s in back["row_source"]})

    if out_path:
        tmp = "%s.tmp%d.npz" % (out_path, os.getpid())
        np.savez(tmp, **out)
        if not checks(_read(tmp)):
            return 0, len(ids), "the rebuilt file did not read back as intended; left at %s" % tmp
        _publish(tmp, out_path)
        return len(made), len(ids), ""
    note = _write(path, out, checks)
    return (0 if note else len(made)), len(ids), note


def seal(store=STORE, path=None):
    """Write the SHA-256 manifest for the vector file as it is now; returns (digest, note). For a file written before
    manifests existed, or one restored by hand without its manifest. It vouches only that these are the bytes in place:
    run status first, since a manifest over a file changed by someone else makes that change the published one."""
    path = path or vector_path(store)
    if not os.path.exists(path):
        return "", "no claim vectors at %s" % path
    fd = _take_lock(path)
    if fd is None:
        return "", "another run holds %s.lock; not sealed this time" % path
    try:
        try:
            _read(path)
        except Exception as exc:
            return "", "%s cannot be read (%s); not sealed" % (path, exc)
        digest = _sha256_file(path)
        _write_manifest(path, digest)
        return digest, ""
    finally:
        _drop_lock(fd)


def _manifest_line(path):
    """(ok, words) on the manifest beside path: ok is False only when a manifest names other bytes or is damaged."""
    try:
        named = manifest_digest(path)
    except ValueError as exc:
        return False, str(exc)
    digest = _sha256_file(path)
    if named is None:
        return True, "SHA-256 %s, no manifest beside it (python math_claim_vecs.py seal)" % digest[:12]
    if named != digest:
        return False, ("SHA-256 %s but its manifest names %s: the file changed after its writer published it, and "
                       "every process refuses it" % (digest[:12], named[:12]))
    return True, "SHA-256 %s, named by its manifest" % digest[:12]


def status(store=STORE):
    """(ok, line) for the transport's status and hook_liveness."""
    import numpy as np
    path = vector_path(store)
    try:
        fields = _read(path)
    except FileNotFoundError:
        return False, "claim vectors: none at %s" % path
    except Exception as exc:
        return False, "claim vectors: %s cannot be read (%s)" % (path, exc)
    sealed, sealed_words = _manifest_line(path)
    if not sealed:
        return False, ("claim vectors: %s; the hook's meaning channel stays closed until the writer publishes again "
                       "or seal records the file as it is" % sealed_words)
    # math_brain.semantic refuses a file whose "model" field does not name the encoder it queries with; extend() copies
    # the field from the file it extends and never writes it, so a file first built from a seed without one passed here
    # while the channel raised on every load (Mac, 30 Oct 2026: "cover all current claims" over a closed channel)
    model = str(fields["model"]) if "model" in fields else ""
    if MODEL_NAME.split("/")[-1] not in model:
        return False, ("claim vectors: the file's model field is %r, not %s; math_brain.semantic refuses it and the "
                       "hook's meaning channel stays closed" % (model, MODEL_NAME))
    ids = [str(x) for x in fields["slugs"]]
    recorded = [str(x) for x in fields["node_digests"]] if "node_digests" in fields else None
    sources = [str(s) for s in fields["row_source"]] if "row_source" in fields else None
    try:
        c = check(ids, recorded, store, sources)
    except ValueError as exc:
        return False, "claim vectors: %s; the hook's agreement check stays closed" % exc
    if not (c["missing"] or c["phantom"] or c["drifted"] or c["legacy"] or c["duplicates"] or c["unbound"]):
        return True, "claim vectors: cover all current claims, text bound (%d vectors; %s)" % (len(ids), sealed_words)
    age_h = (time.time() - os.path.getmtime(path)) / 3600.0
    parts = []
    if c["missing"]:
        parts.append("%d current claim(s) have no vector" % len(c["missing"]))
    if c["drifted"]:
        parts.append("%d vector(s) were made from text the store no longer holds" % len(c["drifted"]))
    if c["legacy"]:
        src = np.asarray(sources)
        cos = np.asarray(fields["row_body_cos"], dtype=np.float32)[src == "legacy"] if "row_body_cos" in fields else None
        cos = None if cos is None else cos[np.isfinite(cos)]
        parts.append("%d vector(s) of unknown text (legacy%s)" % (
            len(c["legacy"]), "" if cos is None or not cos.size else ", cosine %.4f-%.4f with their claim's text"
            % (float(cos.min()), float(cos.max()))))
    if c["phantom"]:
        parts.append("%d vector(s) name no node of the store" % len(c["phantom"]))
    if c["duplicates"]:
        parts.append("%d id(s) name more than one row (e.g. %s)" % (len(c["duplicates"]), c["duplicates"][0]))
    if c["unbound"]:
        parts.append("the file records no store digests")
    fix = ("a full build (build_claim_vecs.py)" if c["duplicates"] else
           "python math_claim_vecs.py %s" % ("migrate" if c["unbound"] else "extend"))
    return False, ("claim vectors: %s (file %.1fh old); the hook's agreement check stays closed until %s"
                   % (", ".join(parts), age_h, fix))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=("status", "extend", "migrate", "rebuild", "seal"))
    ap.add_argument("--store", default=STORE)
    ap.add_argument("--out", default=None, help="rebuild: write the rebuilt file here and leave the live one alone")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        ok, line = status(a.store)
        print(line)
        return 0 if ok else 1
    if a.cmd == "seal":
        digest, note = seal(a.store)
        print("claim vectors: %s" % (note or "manifest written, SHA-256 %s" % digest))
        return 1 if note else 0
    if a.cmd == "rebuild":
        try:
            import psutil
            psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        except Exception:
            pass
        rebuilt, total, note = rebuild(a.store, out=a.out)
        print("claim vectors: %d legacy row(s) re-embedded from their text, %d in the file%s%s"
              % (rebuilt, total, (" -> %s" % a.out) if a.out and not note else "", (" - " + note) if note else ""))
        return 1 if note else 0
    if a.cmd == "migrate":
        try:
            import psutil
            psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        except Exception:
            pass
        from_body, legacy, total, note = migrate(a.store)
        print("claim vectors: %d rows reproduced from their text, %d legacy, %d in the file%s"
              % (from_body, legacy, total, (" - " + note) if note else ""))
        return 1 if note else 0
    changed, total, note = extend(a.store)
    print("claim vectors: %d row(s) changed, %d in the file%s" % (changed, total, (" - " + note) if note else ""))
    return 1 if note else 0


if __name__ == "__main__":
    sys.exit(main())
