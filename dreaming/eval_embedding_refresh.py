#!/usr/bin/env python3
"""Isolated, model-free regression for staged-v2 refresh and runtime-v3 publication.

Everything lives under ``tempfile.TemporaryDirectory``.  A deterministic fake encoder exercises
reuse/new/change/delete/model-mismatch behavior, the read-only coverage gate, and the load-bearing
atomicity invariant: an encoder failure must leave the prior production NPZ byte-identical.
"""
import hashlib
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from unittest import mock

import numpy as np

import build_memory_embeddings as builder
import build_memory_embeddings_local as local_builder
import eval_recall_signals as recall_signals
import embedding_artifact as artifact
import embedding_coverage as coverage
import publish_memory_embeddings as publisher
import spread_recall as recall


MODEL = builder.DEFAULT_MODEL
DIM = 384


def _write_memory(path, slug, claim):
    body = ("---\nname: %s\ndescription: \"%s\"\n---\n\n%s\n" %
            (slug, claim, claim + " This prose is deliberately long enough for embedding."))
    with io.open(os.path.join(path, slug + ".md"), "w", encoding="utf-8") as f:
        f.write(body)


def _fake_factory(model_name):
    def encode(texts):
        rows = []
        for text in texts:
            seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
            rng = np.random.default_rng(seed)
            row = rng.standard_normal(DIM).astype(np.float32)
            row /= np.linalg.norm(row)
            rows.append(row)
        return np.vstack(rows).astype(np.float32)
    return encode


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _slugs(path):
    with np.load(path, allow_pickle=False) as z:
        return [str(s) for s in z["slugs"]]


def _mutate_first_vector(blob):
    """Return a structurally valid NPZ whose first unit vector no longer has builder provenance."""
    with np.load(io.BytesIO(blob), allow_pickle=False) as z:
        arrays = {name: np.asarray(z[name]).copy() for name in z.files}
    vecs = arrays["vecs"]
    replacement = np.zeros(vecs.shape[1], dtype=np.float32)
    replacement[(int(np.argmax(np.abs(vecs[0]))) + 1) % vecs.shape[1]] = 1.0
    arrays["vecs"][0] = replacement
    out = io.BytesIO()
    np.savez_compressed(out, **arrays)
    return out.getvalue()


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    if not __debug__:
        raise RuntimeError("eval_embedding_refresh requires assertions; do not run with python -O")
    checks = 0
    with tempfile.TemporaryDirectory(prefix="embedding-refresh-eval-") as td:
        mem = os.path.join(td, "memory")
        os.mkdir(mem)
        prod = os.path.join(td, "memory_embeddings.npz")
        nxt = os.path.join(td, "memory_embeddings.next.npz")

        _write_memory(mem, "old", "The old memory records a stable semantic claim")
        first = builder.build_sidecar(mem, out=prod, encoder_factory=_fake_factory)
        assert first["encoded"] == 1 and first["reused"] == 0
        checks += 1

        _write_memory(mem, "new", "The new memory adds a distinct semantic observation")
        before = coverage.coverage_report(mem, prod, MODEL)
        assert not before["complete"] and before["missing"] == ["new"]
        checks += 1

        plan = builder.plan_refresh(mem, MODEL, prod)
        assert sorted(plan["reuse"]) == ["old"] and [r[0] for r in plan["encode"]] == ["new"]
        second = builder.build_sidecar(mem, out=nxt, incremental_from=prod,
                                       encoder_factory=_fake_factory, plan=plan)
        assert second["reused"] == 1 and second["encoded"] == 1
        assert coverage.coverage_report(mem, nxt, MODEL)["complete"]
        checks += 1

        with np.load(nxt, allow_pickle=False) as z:
            old_before = np.asarray(z["vecs"])[_slugs(nxt).index("old")].copy()
        _write_memory(mem, "old", "The old memory was corrected and now carries changed meaning")
        changed = builder.plan_refresh(mem, MODEL, nxt)
        assert sorted(changed["reuse"]) == ["new"] and [r[0] for r in changed["encode"]] == ["old"]
        builder.build_sidecar(mem, out=prod, incremental_from=nxt,
                              encoder_factory=_fake_factory, plan=changed)
        with np.load(prod, allow_pickle=False) as z:
            old_after = np.asarray(z["vecs"])[_slugs(prod).index("old")]
        assert not np.array_equal(old_before, old_after)
        checks += 1

        os.remove(os.path.join(mem, "old.md"))
        deleted = builder.plan_refresh(mem, MODEL, prod)
        assert deleted["dropped"] == ["old"] and sorted(deleted["reuse"]) == ["new"]
        builder.build_sidecar(mem, out=nxt, incremental_from=prod,
                              encoder_factory=_fake_factory, plan=deleted)
        assert _slugs(nxt) == ["new"] and coverage.coverage_report(mem, nxt, MODEL)["complete"]
        checks += 1

        wrong_model = builder.plan_refresh(mem, "different/model", nxt)
        assert not wrong_model["reuse"] and [r[0] for r in wrong_model["encode"]] == ["new"]
        assert "model changed" in wrong_model["reason"]
        checks += 1

        legacy = os.path.join(td, "legacy.npz")
        np.savez_compressed(legacy, slugs=np.asarray(["new"]),
                            vecs=np.ones((1, DIM), dtype=np.float32) / np.sqrt(DIM),
                            model=np.asarray(MODEL))
        legacy_plan = builder.plan_refresh(mem, MODEL, legacy)
        assert not legacy_plan["reuse"] and "legacy sidecar" in legacy_plan["reason"]
        checks += 1

        # Force an encode, fail before staging, and prove in-place production remains byte-identical.
        _write_memory(mem, "new", "A further edit must invoke the encoder before atomic promotion")
        byte_before = _sha(nxt)

        def failing_factory(_model):
            def fail(_texts):
                raise RuntimeError("intentional fake encoder failure")
            return fail

        try:
            builder.build_sidecar(mem, out=nxt, incremental_from=nxt,
                                  encoder_factory=failing_factory)
            raise AssertionError("failing encoder unexpectedly returned")
        except RuntimeError as exc:
            assert "intentional fake encoder failure" in str(exc)
        assert _sha(nxt) == byte_before
        checks += 1

        final = coverage.coverage_report(mem, nxt, MODEL)
        assert not final["complete"] and final["stale"] == ["new"]
        checks += 1

        # Observer publication is locked, model-gated, and deliberately cannot authorize consumers.
        status = os.path.join(td, ".embedding_status.json")
        authority = os.path.join(td, ".embedding_current.json")
        pub_lock = os.path.join(td, "publish.lock")
        mem_lock = os.path.join(td, "memory.lock")
        fixed_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        snapshot, observed = coverage.write_status(
            mem, nxt, MODEL, status, fixed_ts, authority, pub_lock, mem_lock
        )
        assert observed["stale"] == ["new"] and snapshot["schema"] == artifact.OBSERVER_SCHEMA
        assert snapshot["corpus_stat_sha256"] and not snapshot["runtime_safe"]
        assert not snapshot["complete"] and any(
            "authority" in problem for problem in snapshot["problems"]
        )
        assert artifact.load_verified_embeddings(nxt, status, mem, MODEL) is None
        checks += 2

        # One-time adoption safely migrates an incomplete fixed-name generation by masking stale rows.
        adopt_mem = os.path.join(td, "adopt-memory")
        os.mkdir(adopt_mem)
        _write_memory(adopt_mem, "changed", "This row will become stale before one-time adoption")
        _write_memory(adopt_mem, "sound", "This row stays valid through one-time adoption")
        adopt_live = os.path.join(td, "adopt-live.npz")
        builder.build_sidecar(adopt_mem, out=adopt_live, encoder_factory=_fake_factory)
        _write_memory(adopt_mem, "changed", "This row changed after the fixed generation was built")
        adopt_authority = os.path.join(td, ".adopt-current.json")
        adopt_status = os.path.join(td, ".adopt-status.json")
        with mock.patch.object(
            coverage, "_write_status_snapshot",
            side_effect=PermissionError("intentional observer sharing violation"),
        ):
            adopted = publisher.adopt_current(
                adopt_live, adopt_authority, adopt_status, adopt_mem, MODEL,
                os.path.join(td, "adopt-pub.lock"), os.path.join(td, "adopt-mem.lock")
            )
        adopt_loaded = artifact.load_verified_embeddings(
            adopt_live, adopt_authority, adopt_mem, MODEL
        )
        assert adopted["status"] == "adopted" and adopted["active"] == 1
        assert not adopted["observer_updated"]
        assert adopt_loaded is not None and set(adopt_loaded[0]) == {"sound"}
        assert adopt_loaded.bias == 0.0
        try:
            publisher.adopt_current(
                adopt_live, adopt_authority, adopt_status, adopt_mem, MODEL,
                os.path.join(td, "adopt-pub.lock"), os.path.join(td, "adopt-mem.lock")
            )
            raise AssertionError("second one-time adoption unexpectedly succeeded")
        except FileExistsError:
            pass
        checks += 2

        # Complete the changed corpus and exercise immutable generation + pointer publication.
        _write_memory(mem, "stable", "An unaffected memory must survive another row changing")
        complete = os.path.join(td, "complete.npz")
        builder.build_sidecar(mem, out=complete, incremental_from=nxt,
                              encoder_factory=_fake_factory)
        assert coverage.coverage_report(mem, complete, MODEL)["complete"]
        live = os.path.join(td, "live.npz")
        checks += 1

        real_io_open = builder.io.open

        def deny_one(path, *args, **kwargs):
            if (isinstance(path, (str, bytes, os.PathLike))
                    and os.path.basename(os.fspath(path)) == "stable.md"):
                raise PermissionError("intentional unreadable corpus fixture")
            return real_io_open(path, *args, **kwargs)

        with mock.patch.object(builder.io, "open", side_effect=deny_one):
            unreadable = coverage.coverage_report(mem, complete, MODEL)
            assert not unreadable["corpus_readable"]
            assert "corpus unreadable" in unreadable["problems"][0]
            try:
                publisher.publish(
                    complete, live, authority, status, mem, MODEL, pub_lock, mem_lock
                )
                raise AssertionError("lossy unreadable corpus unexpectedly published")
            except ValueError:
                pass
        assert not os.path.exists(authority)
        checks += 1

        bad = os.path.join(td, "bad.npz")
        artifact.atomic_replace_bytes(bad, b"not-an-npz")
        try:
            publisher.publish(
                bad, live, authority, status, mem, MODEL, pub_lock, mem_lock
            )
            raise AssertionError("invalid staged sidecar unexpectedly published")
        except ValueError:
            pass
        assert not os.path.exists(authority)
        checks += 1

        try:
            publisher.publish(
                complete, live, authority, authority, mem, MODEL, pub_lock, mem_lock
            )
            raise AssertionError("authority/observer path alias unexpectedly published")
        except ValueError as exc:
            assert "aliases" in str(exc)
        assert not os.path.exists(authority)
        checks += 1

        for fault in ("status", "verify", "interrupt"):
            try:
                publisher.publish(
                    complete, live, authority, status, mem, MODEL, pub_lock, mem_lock,
                    fault=fault
                )
                raise AssertionError("injected %s fault unexpectedly published" % fault)
            except (RuntimeError, KeyboardInterrupt) as exc:
                assert "injected" in str(exc)
            assert not os.path.exists(authority)
        checks += 3

        result = publisher.publish(
            complete, live, authority, status, mem, MODEL, pub_lock, mem_lock
        )
        assert result["status"] == "published"
        assert result["sidecar"] != live and os.path.exists(result["sidecar"])
        assert not os.path.exists(live)
        assert artifact.load_verified_embeddings(live, authority, mem, MODEL) is not None
        with np.load(result["sidecar"], allow_pickle=False) as z:
            assert int(z["schema_version"].item()) == publisher.PUBLISHED_SCHEMA
            assert {"source_size", "source_mtime_ns", "row_safe"}.issubset(z.files)
        current = artifact.read_stable_bytes(authority)[0]
        valid_authority = json.loads(current)
        old_globals = (recall.EMB, recall.EMB_AUTHORITY, recall.MEM, recall.EXPECTED_EMB_MODEL)
        try:
            recall.EMB, recall.EMB_AUTHORITY = live, authority
            recall.MEM, recall.EXPECTED_EMB_MODEL = mem, MODEL
            assert recall._load_embeddings() is not None
        finally:
            (recall.EMB, recall.EMB_AUTHORITY, recall.MEM,
             recall.EXPECTED_EMB_MODEL) = old_globals
        checks += 3

        # A touch-only metadata change falls off the stat fast path but exact fallback preserves it.
        stable_path = os.path.join(mem, "stable.md")
        stable_stat = os.stat(stable_path)
        os.utime(stable_path, ns=(stable_stat.st_atime_ns, stable_stat.st_mtime_ns + 1_000_000))
        touched = artifact.load_verified_embeddings(live, authority, mem, MODEL)
        assert touched is not None and "stable" in touched[0]
        touched_snapshot, _ = coverage.write_status(
            mem, live, MODEL, status, fixed_ts, authority, pub_lock, mem_lock
        )
        assert touched_snapshot["complete"] and touched_snapshot["runtime_safe"]
        os.utime(stable_path, ns=(stable_stat.st_atime_ns, stable_stat.st_mtime_ns))
        checks += 1

        # An interrupt raised by the atomic helper after the real pointer replace is committed, not
        # misreported as pre-commit failure.
        post_authority = os.path.join(td, ".post-current.json")
        post_status = os.path.join(td, ".post-status.json")
        real_atomic_replace = artifact.atomic_replace_bytes

        def replace_then_interrupt(path, payload, *args, **kwargs):
            result_value = real_atomic_replace(path, payload, *args, **kwargs)
            if os.path.abspath(path).casefold() == os.path.abspath(post_authority).casefold():
                raise KeyboardInterrupt("intentional exception after authority replace")
            return result_value

        with mock.patch.object(artifact, "atomic_replace_bytes", side_effect=replace_then_interrupt):
            post_result = publisher.publish(
                complete, live, post_authority, post_status, mem, MODEL,
                os.path.join(td, "post-pub.lock"), os.path.join(td, "post-mem.lock")
            )
        assert post_result["status"] == "published" and not post_result["commit_call_returned"]
        assert post_result["sidecar"] == result["sidecar"]
        assert artifact.load_verified_embeddings(live, post_authority, mem, MODEL) is not None

        # No fallible consumer call happens after commit; even an interrupted observer returns the
        # durable committed outcome and can be repaired independently.
        calls = []

        def counting_verifier(*args, **kwargs):
            calls.append(1)
            return artifact.load_verified_embeddings(*args, **kwargs)

        observer_cut_authority = os.path.join(td, ".observer-cut-current.json")
        with mock.patch.object(
            coverage, "_write_status_snapshot",
            side_effect=KeyboardInterrupt("intentional post-commit observer interruption"),
        ):
            observer_cut = publisher.publish(
                complete, live, observer_cut_authority, os.path.join(td, ".observer-cut.json"),
                mem, MODEL, os.path.join(td, "observer-cut-pub.lock"),
                os.path.join(td, "observer-cut-mem.lock"), verifier=counting_verifier
            )
        assert observer_cut["status"] == "published" and not observer_cut["observer_updated"]
        assert len(calls) == 1
        assert artifact.load_verified_embeddings(live, observer_cut_authority, mem, MODEL) is not None
        checks += 2

        # Observer overwrite cannot change authority or disable a valid consumer.
        coverage._write_status_snapshot(final, status, checked_at=fixed_ts)
        assert artifact.read_stable_bytes(authority)[0] == current
        assert artifact.load_verified_embeddings(live, authority, mem, MODEL) is not None
        checks += 1

        # Missing, malformed, observer, and digestless authorities all fail closed.
        os.remove(authority)
        assert artifact.load_verified_embeddings(live, authority, mem, MODEL) is None
        artifact.atomic_replace_bytes(authority, b"{not-json")
        assert artifact.load_verified_embeddings(live, authority, mem, MODEL) is None
        artifact.atomic_replace_bytes(authority, artifact.read_stable_bytes(status)[0])
        assert artifact.load_verified_embeddings(live, authority, mem, MODEL) is None
        digestless = dict(valid_authority)
        digestless.pop("sidecar_sha256")
        artifact.atomic_replace_bytes(
            authority, (json.dumps(digestless, sort_keys=True, separators=(",", ":")) + "\n").encode()
        )
        assert artifact.load_verified_embeddings(live, authority, mem, MODEL) is None
        artifact.atomic_replace_bytes(authority, current)
        checks += 4

        tampered = dict(valid_authority)
        tampered["sidecar_sha256"] = "0" * 64
        artifact.atomic_replace_bytes(
            authority, (json.dumps(tampered, sort_keys=True, separators=(",", ":")) + "\n").encode()
        )
        tampered_snapshot, _ = coverage.write_status(
            mem, live, MODEL, status, fixed_ts, authority, pub_lock, mem_lock
        )
        assert not tampered_snapshot["complete"] and not tampered_snapshot["runtime_safe"]
        assert artifact.load_verified_embeddings(live, authority, mem, MODEL) is None
        artifact.atomic_replace_bytes(authority, current)
        checks += 1

        # Pointer JSON is deliberately minimal: adding former row-mask/manifest policy fields makes
        # the whole pointer invalid instead of letting mutable metadata suppress memories.
        for field, value in (
            ("unsafe_slugs", ["stable"]),
            ("corpus_manifest", {"stable": [1, 1]}),
        ):
            pointer_tamper = dict(valid_authority)
            pointer_tamper[field] = value
            artifact.atomic_replace_bytes(
                authority,
                (json.dumps(pointer_tamper, sort_keys=True, separators=(",", ":")) + "\n").encode(),
            )
            assert artifact.load_verified_embeddings(live, authority, mem, MODEL) is None
            pointer_snapshot, _ = coverage.write_status(
                mem, live, MODEL, status, fixed_ts, authority, pub_lock, mem_lock
            )
            assert not pointer_snapshot["complete"] and not pointer_snapshot["runtime_safe"]
        artifact.atomic_replace_bytes(authority, current)
        checks += 2

        # Pointer syntax and digest are insufficient: an authority naming a legacy schema-v2 NPZ is
        # observer-red and consumer-rejected until the Windows publisher stamps runtime schema v3.
        v2_blob, v2_digest, v2_size, _ = artifact.read_stable_bytes(complete)
        artifact.atomic_replace_bytes(live, v2_blob)
        with np.load(io.BytesIO(v2_blob), allow_pickle=False) as z:
            v2_built_at = str(z["built_at"].item())
        v2_pointer = dict(valid_authority)
        v2_pointer.update({
            "sidecar": os.path.abspath(live),
            "sidecar_sha256": v2_digest,
            "sidecar_size": v2_size,
            "built_at": v2_built_at,
        })
        artifact.atomic_replace_bytes(
            authority, (json.dumps(v2_pointer, sort_keys=True, separators=(",", ":")) + "\n").encode()
        )
        assert artifact.load_verified_embeddings(live, authority, mem, MODEL) is None
        v2_snapshot, _ = coverage.write_status(
            mem, live, MODEL, status, fixed_ts, authority, pub_lock, mem_lock
        )
        assert not v2_snapshot["complete"] and not v2_snapshot["runtime_safe"]
        os.remove(live)
        artifact.atomic_replace_bytes(authority, current)
        checks += 1

        # Rogue mutation or truncation of an immutable generation is rejected by exact digest.
        generation = result["sidecar"]
        original_generation = artifact.read_stable_bytes(generation)[0]

        # A digest-invalid active generation is never a reusable incremental source. Otherwise a
        # rogue vector could be laundered into a newly valid generation without model encoding.
        artifact.atomic_replace_bytes(generation, _mutate_first_vector(original_generation))
        assert artifact.load_verified_embeddings(live, authority, mem, MODEL) is None
        try:
            builder.plan_refresh(
                mem, MODEL, generation, incremental_binding=valid_authority
            )
            raise AssertionError("digest-invalid authority generation was reused")
        except ValueError as exc:
            assert "authority-bound incremental source" in str(exc)
        artifact.atomic_replace_bytes(generation, original_generation)
        checks += 1

        artifact.atomic_replace_bytes(generation, original_generation[:-17])
        assert artifact.load_verified_embeddings(live, authority, mem, MODEL) is None
        artifact.atomic_replace_bytes(generation, original_generation)
        assert artifact.load_verified_embeddings(live, authority, mem, MODEL) is not None
        checks += 1

        # Per-row masking preserves unaffected vectors, and additions do not disable old rows.
        new_path = os.path.join(mem, "new.md")
        original_body = artifact.read_stable_bytes(new_path)[0]
        original_stat = os.stat(new_path)
        _write_memory(mem, "new", "The corpus changed after the embedding authority committed")
        after_edit = artifact.load_verified_embeddings(live, authority, mem, MODEL)
        assert after_edit is not None and "new" not in after_edit[0] and "stable" in after_edit[0]
        _write_memory(mem, "added", "A newly added row has no vector but old vectors remain valid")
        after_add = artifact.load_verified_embeddings(live, authority, mem, MODEL)
        assert after_add is not None and "stable" in after_add[0]
        checks += 2

        # Same-size content with a restored mtime evades stat masking, but exact participant hashing
        # still refuses to let that stale vector influence semantic scoring.
        altered = bytearray(original_body)
        pivot = altered.find(b"further")
        assert pivot >= 0
        altered[pivot:pivot + 7] = b"changed"
        artifact.atomic_replace_bytes(new_path, bytes(altered))
        os.utime(new_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        stat_view = artifact.load_verified_embeddings(live, authority, mem, MODEL)
        assert stat_view is not None and "new" in stat_view[0]
        assert not artifact.embedding_row_current(stat_view, mem, "new")
        inconsistent_snapshot, inconsistent_report = coverage.write_status(
            mem, live, MODEL, status, fixed_ts, authority, pub_lock, mem_lock
        )
        assert inconsistent_report["activation_inconsistent"] == ["new"]
        assert not inconsistent_snapshot["runtime_safe"] and not inconsistent_snapshot["complete"]
        artifact.atomic_replace_bytes(new_path, original_body)
        os.utime(new_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        checks += 1

        # Direct writes to the canonical live slot are structurally refused.
        try:
            builder.build_sidecar(mem, out=builder.OUT, incremental_from=complete,
                                  encoder_factory=_fake_factory)
            raise AssertionError("builder unexpectedly allowed direct live output")
        except ValueError as exc:
            assert "refusing direct live" in str(exc)
        checks += 1

        # Windows junctions and POSIX directory symlinks cannot bypass either live-output refusal.
        real_dir = os.path.join(td, "live-real")
        alias_dir = os.path.join(td, "live-alias")
        os.mkdir(real_dir)
        try:
            os.symlink(real_dir, alias_dir, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            made = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", alias_dir, real_dir],
                capture_output=True, text=True, creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
                check=False,
            )
            if made.returncode:
                raise RuntimeError("could not create junction fixture: %s" % made.stderr)
        live_target = os.path.join(real_dir, "memory_embeddings.npz")
        alias_target = os.path.join(alias_dir, "memory_embeddings.npz")
        assert builder.same_file_target(live_target, alias_target)
        with mock.patch.object(builder, "OUT", live_target):
            try:
                builder.build_sidecar(mem, out=alias_target, encoder_factory=_fake_factory)
                raise AssertionError("junction alias bypassed BGE live-output refusal")
            except ValueError as exc:
                assert "refusing direct live" in str(exc)
        with mock.patch.object(local_builder, "LIVE_OUT", live_target), mock.patch.object(
            sys, "argv", ["build_memory_embeddings_local.py", "--out", alias_target]
        ):
            assert local_builder.main() == 2
        checks += 1

        # A staged sidecar from an unapproved model can never become authority.
        wrong = os.path.join(td, "wrong-model.npz")
        builder.build_sidecar(mem, model_name="unapproved/model", out=wrong,
                              encoder_factory=_fake_factory)
        authority_before = artifact.read_stable_bytes(authority)[0]
        try:
            publisher.publish(wrong, live, authority, status, mem, MODEL, pub_lock, mem_lock)
            raise AssertionError("unapproved model unexpectedly published")
        except ValueError:
            pass
        assert artifact.read_stable_bytes(authority)[0] == authority_before
        checks += 1

        # Production consumers must resolve authority rather than loading the canonical NPZ directly.
        # Power the causal recall gate past its formerly untested reporting branch. It must identify
        # the exact authority receipt and fail closed rather than substituting an LSA file.
        signal_trace = os.path.join(td, "powered-retrievals.tsv")
        signal_slugs = ["signal_%02d" % i for i in range(20)]
        signal_events = []
        epoch = 0
        for i in range(35):
            a = signal_slugs[i % len(signal_slugs)]
            b = signal_slugs[(i * 7 + 3) % len(signal_slugs)]
            if a == b:
                b = signal_slugs[(i + 1) % len(signal_slugs)]
            signal_events.extend(((a, epoch, "pre-%02d" % i), (b, epoch + 1, "pre-%02d" % i)))
            epoch += 2
        import itertools
        for i, (a, b) in enumerate(list(itertools.combinations(signal_slugs[:6], 2))):
            signal_events.extend(((a, epoch, "future-%02d" % i),
                                  (b, epoch + 1, "future-%02d" % i)))
            epoch += 2
        with io.open(signal_trace, "w", encoding="utf-8", newline="") as fh:
            fh.write("slug\tepoch\tkind\tsession\r\n")
            for slug, event_epoch, session in signal_events:
                fh.write("%s\t%d\tread\t%s\r\n" % (slug, event_epoch, session))
        rng = np.random.default_rng(42)
        signal_matrix = rng.standard_normal((len(signal_slugs), 8)).astype(np.float32)
        signal_matrix /= np.linalg.norm(signal_matrix, axis=1, keepdims=True)
        signal_loaded = ({slug: i for i, slug in enumerate(signal_slugs)}, signal_matrix, 0.0)
        signal_receipt = {"sidecar_sha256": "a" * 64, "model": MODEL}
        with mock.patch.object(recall_signals, "TRACE", signal_trace), mock.patch.object(
            recall_signals.embedding_artifact, "load_verified_embeddings", return_value=signal_loaded
        ), mock.patch.object(
            recall_signals.embedding_artifact, "resolve_authority_sidecar",
            return_value=("generation.npz", signal_receipt),
        ):
            signal_output = io.StringIO()
            with contextlib.redirect_stdout(signal_output):
                recall_signals.main()
        rendered_signal = signal_output.getvalue()
        assert "content embedding (authority:aaaaaaaaaaaa)" in rendered_signal
        assert "AUROC  graph + 0.5*embedding blend" in rendered_signal
        with mock.patch.object(recall_signals, "TRACE", signal_trace), mock.patch.object(
            recall_signals.embedding_artifact, "load_verified_embeddings", return_value=None
        ):
            try:
                recall_signals.main()
                raise AssertionError("causal BGE gate silently ran without authority")
            except SystemExit as exc:
                assert "verified active BGE authority" in str(exc)
        checks += 2

        for name in ("dream.py", "build_orphan_edges.py", "build_series_edges.py",
                     "suggest_orphan_links.py"):
            source = io.open(os.path.join(os.path.dirname(__file__), name),
                             encoding="utf-8").read()
            assert "load_verified_embeddings" in source
            assert "np.load(EMB" not in source
            assert '_np.load(os.path.join(_dr, "memory_embeddings.npz")' not in source
        checks += 1

    print("eval_embedding_refresh: PASS (%d isolated checks; fake encoder, no model/network)" % checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
