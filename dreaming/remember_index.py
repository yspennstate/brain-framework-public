"""Await a bounded BM25 refresh and verify the saved targets' indexed tokens.

This is an indexing-content receipt, never a ranking or semantic-recall receipt.
The child is awaited: detached console flags do not establish process lifetime.
"""
from collections import Counter
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def _verify(bm, slugs, mem_dir, index_path):
    before = bm._index_signature(index_path)
    payload = bm._load_payload(index_path)
    matched = []
    for slug in slugs:
        path = Path(mem_dir) / (slug + '.md')
        first = hashlib.sha256(path.read_bytes()).digest()
        tokens = bm.read_doc(mem_dir, slug)
        last = hashlib.sha256(path.read_bytes()).digest()
        if first != last:
            return False, 'source_changed'
        if not tokens:
            return False, 'empty_or_unindexable_source'
        expected = dict(Counter(tokens))
        if (payload['tf'].get(slug) == expected
                and payload['dl'].get(slug) == len(tokens)):
            matched.append(slug)
    if before != bm._index_signature(index_path):
        return False, 'index_changed'
    return len(matched) == len(slugs), 'content_mismatch'


def _detach_refresh(bm):
    """On a timed-out awaited refresh, hand the publish to a detached windowless `bm25.py refresh` so the work
    the killed child had done is redone and PUBLISHED instead of waiting for the 15-minute task (2026-09-14:
    every remember() that night returned DEFERRED_TIMEOUT under render load). The receipt stays DEFERRED_TIMEOUT -
    nothing is verified here - and the reason names the detached pid; the recall read path already merges a
    fresh memory in memory whatever the index says."""
    try:
        pid = bm.spawn_detached_refresh()
        return 'detached refresh pid %s' % pid if pid else 'detached refresh not spawned'
    except Exception as exc:
        return 'detached refresh failed: %s' % type(exc).__name__


def ensure_indexed(slugs, *, mem_dir=None, index_path=None,
                   timeout_seconds=12.0, max_attempts=3):
    """Return VERIFIED_CURRENT_BM25_CONTENT or a truthful DEFERRED_* state.

Explicit paths are for isolated fixtures. A freeze is respected. Refresh
failures never undo the already completed memory write. No background child
is left behind when this function returns.
    """
    import bm25 as bm
    slugs = tuple(dict.fromkeys(slugs))
    if not slugs or any(not re.fullmatch(r'[a-z0-9][a-z0-9_-]{2,80}', s)
                        for s in slugs):
        raise ValueError('valid nonempty target slugs required')
    if not 0 < timeout_seconds <= 30 or not 1 <= max_attempts <= 3:
        raise ValueError('bounded timeout and attempts required')
    mem_dir = os.fspath(mem_dir if mem_dir is not None else bm.MEM)
    custom_without_index = (index_path is None and
                            (bool(os.environ.get('BRAIN_MEM_DIR')) or
                             Path(mem_dir).resolve() != Path(bm.MEM).resolve()))
    index_path = os.fspath(index_path if index_path is not None else bm.INDEX)
    start = time.monotonic()
    deadline = start + timeout_seconds
    attempts = 0

    def receipt(status, reason=''):
        return {'status': status, 'reason': reason, 'targets': list(slugs),
                'attempts': attempts, 'elapsed_seconds': time.monotonic() - start,
                'scope': 'Current BM25 token content only; ranking not verified.'}

    # A fixture/custom corpus must never refresh the owner's global index and
    # make its other memories look deleted. Require an explicit paired index.
    if custom_without_index:
        return receipt('DEFERRED_CUSTOM_CORPUS_INDEX_REQUIRED')

    while True:
        try:
            valid, reason = _verify(bm, slugs, mem_dir, index_path)
        except FileNotFoundError:
            # AN ABSENT INDEX IS NOT A READBACK FAILURE. _verify opens the index to read the
            # target's tokens back; on a machine whose first memory this is there is no index to
            # open, and returning here reported "indexing pending" with attempts 0 - nothing was
            # ever tried, and the memory stayed unrecallable with nothing to say so. That is
            # precisely the state a refresh exists to fix, so fall through to it.
            valid, reason = False, 'no_index_yet'
        except Exception as exc:
            return receipt('DEFERRED_READBACK_ERROR', type(exc).__name__)
        if valid:
            return receipt('VERIFIED_CURRENT_BM25_CONTENT')
        if reason in {'source_changed', 'empty_or_unindexable_source'}:
            return receipt('DEFERRED_SOURCE', reason)
        frozen, _why = bm.freeze_state(index_path)
        if frozen:
            return receipt('DEFERRED_FROZEN')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return receipt('DEFERRED_TIMEOUT', _detach_refresh(bm))
        if attempts >= max_attempts:
            return receipt('DEFERRED_NOT_INDEXED', reason)
        attempts += 1
        code = ('import sys; import bm25; '
                'bm25.refresh(mem_dir=sys.argv[1], out=sys.argv[2], quiet=True,'
                ' build_if_absent=True)')
        flags = 0
        if sys.platform == 'win32':
            flags = (subprocess.CREATE_NO_WINDOW
                     | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        try:
            result = subprocess.run(
                [sys.executable, '-B', '-c', code, mem_dir, index_path],
                cwd=str(Path(bm.__file__).resolve().parent),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=flags,
                timeout=remaining, check=False)
        except subprocess.TimeoutExpired:
            return receipt('DEFERRED_TIMEOUT', _detach_refresh(bm))
        except OSError as exc:
            return receipt('DEFERRED_CHILD_ERROR', type(exc).__name__)
        if result.returncode:
            return receipt('DEFERRED_CHILD_ERROR', 'nonzero_exit')
        # A refresh can legitimately skip under contention. Success is checked
        # from the target content at the top of the loop, never from exit zero.
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.05, remaining))
