#!/usr/bin/env python3
"""Isolated transaction regression for coactivation_end.py; never touches the live graph."""

from __future__ import print_function

import io
import math
import os
import tempfile
import time

import coactivation_end as coact
import sys


STAMP = "2099-01-02T03:04:05"
HEADER = "a\tb\tweight\n"
# The decay is by elapsed time since the table was last rewritten (2026-09-26). This much age is exactly
# one step of the old per-fold rule (a factor of 0.98), so the fixture's original numbers still hold.
OLD_STEP_S = coact.HALF_LIFE_DAYS * 86400.0 * math.log(1 / 0.98, 2)


def _write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _age(path, seconds, now=None):
    t = (time.time() if now is None else now) - seconds
    os.utime(path, (t, t))


def _bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _weights(path):
    out = {}
    with io.open(path, encoding="utf-8") as f:
        next(f, None)
        for line in f:
            p = line.rstrip("\n").split("\t")
            # skip the exactly-once "#applied ... receipt" rows fold_targets now stores in the tsv
            # (mirror coactivation_end._load_decayed_weights, which recognizes APPLIED_TAG). Without
            # this the helper hits float("receipt") - the 2026-07-11 nonfatal dream-component crash.
            if len(p) == 3 and p[0] != coact.APPLIED_TAG:
                out[(p[0], p[1])] = float(p[2])
    return out


def _multi_fixture(root, reverse_targets=False, now=None):
    os.makedirs(root)
    tsv = os.path.join(root, "coactivation.tsv")
    audit = os.path.join(root, "coactivation_folds.log")
    focused = os.path.join(root, "focused.log")
    survey = os.path.join(root, "survey.log")
    singleton = os.path.join(root, "singleton.log")
    _write(tsv, HEADER + "weak_a\tweak_b\t0.510\nold_a\told_b\t2.000\n"
           "a\tliteral_edge\t1.000\n")
    _write(focused, "old_b\nold_a\nold_a\n")
    _write(survey, "\n".join("s%d" % i for i in reversed(range(8))) + "\n")
    _write(singleton, "alone\nalone\n")
    # One pinned clock for the age and the fold (28 Sep): weights are now stored at full precision, so the
    # milliseconds between aging the table and the fold's own clock read would show in the last digits and the
    # two fixtures below would differ by them. With %.3f they were rounded away.
    _age(tsv, OLD_STEP_S, now)
    targets = [focused, survey, singleton]
    if reverse_targets:
        targets.reverse()
    result = coact.fold_targets(targets, tsv=tsv, foldlog=audit, stamp=STAMP, now=now)
    return result, tsv, audit, targets


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    checks = 0
    with tempfile.TemporaryDirectory(prefix="coactivation-decay-eval-") as td:
        # A no-pair invocation consumes readable empty/singleton logs but must not even rewrite
        # the graph. Preserve a historical v1 audit row and append one v2 invocation summary.
        no_pair = os.path.join(td, "no-pair")
        os.makedirs(no_pair)
        tsv = os.path.join(no_pair, "coactivation.tsv")
        audit = os.path.join(no_pair, "coactivation_folds.log")
        singleton = os.path.join(no_pair, "singleton.log")
        empty = os.path.join(no_pair, "empty.log")
        _write(tsv, HEADER + "old_a\told_b\t1.000\n")
        _write(audit, "2026-07-09T12:00:00\town\tlegacy-sessi\t2\n")
        _write(singleton, "only\nonly\n")
        _write(empty, "")
        before = _bytes(tsv)
        result = coact.fold_targets(
            [singleton, empty], tsv=tsv, foldlog=audit, stamp=STAMP
        )
        assert result == {
            "result": "no-pair", "targets": 2, "foldable": 0, "pairs": 0, "removed": 2,
        }
        assert _bytes(tsv) == before
        assert not os.path.exists(singleton) and not os.path.exists(empty)
        assert io.open(audit, encoding="utf-8").read().splitlines() == [
            "2026-07-09T12:00:00\town\tlegacy-sessi\t2",
            STAMP + "\tv2\tno-pair\t2\t0\t0",
        ]
        checks += 1

        # Two foldable logs plus one singleton produce one decay, 29 pair updates, and one audit
        # row. The eight-memory survey is focus-weighted to 6/8; the weak old edge is pruned.
        pinned = time.time()
        result_a, tsv_a, audit_a, logs_a = _multi_fixture(os.path.join(td, "multi-a"), now=pinned)
        weights = _weights(tsv_a)
        assert result_a == {
            "result": "decay", "targets": 3, "foldable": 2, "pairs": 29, "removed": 3,
        }
        # full-precision weights: the decay factor is 0.98 to about 1e-16, no longer rounded to three places
        assert abs(weights[("old_a", "old_b")] - 2.960) < 1e-9  # one decay, then +1 focused
        assert abs(weights[("a", "literal_edge")] - 0.980) < 1e-9  # exact header row only; slug `a` survives
        assert ("weak_a", "weak_b") not in weights  # 0.510 * 0.98 < 0.5
        survey_pairs = [v for (a, b), v in weights.items() if a.startswith("s") and b.startswith("s")]
        assert len(survey_pairs) == 28 and set(survey_pairs) == {0.750}
        assert len(weights) == 30 and not any(os.path.exists(path) for path in logs_a)
        assert io.open(audit_a, encoding="utf-8").read().strip() == (
            STAMP + "\tv2\tdecay\t3\t2\t29"
        )
        checks += 1

        # Target ordering and input slug ordering cannot perturb the promoted EDGES. (Raw bytes now
        # differ BY DESIGN: each fold stores an exactly-once receipt that hashes the source log's inode
        # (see coactivation_end._receipt), which differs between the multi-a and multi-b temp dirs. So
        # the meaningful invariant is edge-equality, not byte-equality - compare weights, which skip receipts.)
        result_b, tsv_b, _audit_b, _logs_b = _multi_fixture(
            os.path.join(td, "multi-b"), reverse_targets=True, now=pinned
        )
        assert result_b == result_a and _weights(tsv_b) == _weights(tsv_a)
        checks += 1

        # Promotion failure is transactional: prior graph bytes and every source log survive,
        # the staged temp is cleaned, and the audit truthfully records a failed invocation.
        failed = os.path.join(td, "failed")
        os.makedirs(failed)
        fail_tsv = os.path.join(failed, "coactivation.tsv")
        fail_audit = os.path.join(failed, "coactivation_folds.log")
        fail_log = os.path.join(failed, "foldable.log")
        _write(fail_tsv, HEADER + "old_a\told_b\t1.000\n")
        _write(fail_log, "new_a\nnew_b\n")
        prior = _bytes(fail_tsv)
        real_replace = coact.os.replace
        try:
            def fail_replace(_src, _dst):
                raise OSError("intentional promotion failure")
            coact.os.replace = fail_replace
            failed_result = coact.fold_targets(
                [fail_log], tsv=fail_tsv, foldlog=fail_audit, stamp=STAMP
            )
        finally:
            coact.os.replace = real_replace
        assert failed_result == {
            "result": "failed", "targets": 1, "foldable": 1, "pairs": 1, "removed": 0,
        }
        assert _bytes(fail_tsv) == prior and os.path.exists(fail_log)
        assert not os.path.exists("%s.tmp.%d" % (fail_tsv, os.getpid()))
        assert io.open(fail_audit, encoding="utf-8").read().strip() == (
            STAMP + "\tv2\tfailed\t1\t1\t1"
        )
        checks += 1

        # If any selected log cannot be read, consume nothing and report the foldable work that
        # was planned before failing closed. A vanished target models the filesystem race directly.
        unreadable = os.path.join(td, "unreadable")
        os.makedirs(unreadable)
        unreadable_tsv = os.path.join(unreadable, "coactivation.tsv")
        unreadable_audit = os.path.join(unreadable, "coactivation_folds.log")
        readable_log = os.path.join(unreadable, "readable.log")
        missing_log = os.path.join(unreadable, "vanished.log")
        _write(unreadable_tsv, HEADER + "old_a\told_b\t1.000\n")
        _write(readable_log, "new_a\nnew_b\n")
        prior = _bytes(unreadable_tsv)
        unreadable_result = coact.fold_targets(
            [readable_log, missing_log], tsv=unreadable_tsv,
            foldlog=unreadable_audit, stamp=STAMP,
        )
        assert unreadable_result == {
            "result": "failed", "targets": 2, "foldable": 1, "pairs": 1, "removed": 0,
        }
        assert _bytes(unreadable_tsv) == prior and os.path.exists(readable_log)
        assert io.open(unreadable_audit, encoding="utf-8").read().strip() == (
            STAMP + "\tv2\tfailed\t2\t1\t1"
        )
        checks += 1

        # THE 25 SEP BUG: fifty sessions ending within seconds must not erode a link. Under the old
        # per-fold rule this edge fell to 0.98**50 = 0.36 and the floor deleted it; now it keeps ~1.0.
        burst = os.path.join(td, "burst")
        os.makedirs(burst)
        burst_tsv = os.path.join(burst, "coactivation.tsv")
        burst_audit = os.path.join(burst, "coactivation_folds.log")
        _write(burst_tsv, HEADER + "keep_a\tkeep_b\t1.000\n")
        for i in range(50):
            log = os.path.join(burst, "s%02d.log" % i)
            _write(log, "n%02d_a\nn%02d_b\n" % (i, i))
            r = coact.fold_targets([log], tsv=burst_tsv, foldlog=burst_audit, stamp=STAMP)
            assert r["result"] == "decay", r
        kept = _weights(burst_tsv).get(("keep_a", "keep_b"))
        assert kept is not None and kept >= 0.999, kept
        checks += 1

        # And disuse over TIME still fades a link: 0.9 of a half-life leaves 0.5 ** 0.9 = 0.536 of it,
        # and a 0.900 link falls to 0.482, under the floor.
        idle = os.path.join(td, "idle")
        os.makedirs(idle)
        idle_tsv = os.path.join(idle, "coactivation.tsv")
        idle_audit = os.path.join(idle, "coactivation_folds.log")
        idle_log = os.path.join(idle, "later.log")
        _write(idle_tsv, HEADER + "p\tq\t1.000\nr\ts\t0.900\n")
        _write(idle_log, "later_a\nlater_b\n")
        _age(idle_tsv, 0.9 * coact.HALF_LIFE_DAYS * 86400.0)
        coact.fold_targets([idle_log], tsv=idle_tsv, foldlog=idle_audit, stamp=STAMP)
        idle_w = _weights(idle_tsv)
        assert 0.535 <= idle_w[("p", "q")] <= 0.537, idle_w
        assert ("r", "s") not in idle_w and idle_w[("later_a", "later_b")] == 1.0
        checks += 1

    print("eval_coactivation_decay: PASS (%d isolated transaction checks)" % checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
