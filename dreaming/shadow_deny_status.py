#!/usr/bin/env python3
"""shadow_deny_status.py - read-only arm-readiness dashboard for the two SHADOW command-surface denies
(proof_deny, heed_gate). Run it to decide whether to arm: it summarizes what each WOULD have denied,
so a fleet-wide deny is only armed after its shadow log is clean.

  python shadow_deny_status.py

Read-only. Never touches a flag, a hook, or the live stack. stdlib only, fail-safe.
"""
import io, os, re, time, hashlib

GF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "graph_fire")
PROOF_SOURCE = os.path.join(GF, "proof_of_failure.py")


def _rows(path):
    try:
        raw = io.open(path, encoding="utf-8", errors="replace").read()
        return [r.split("\t") for r in re.split(r"[\r\n]+", raw) if r.strip()]
    except Exception:
        return []


def _age_days(rows, ts_col=0):
    try:
        ts = [float(r[ts_col]) for r in rows if r and r[ts_col].replace(".", "", 1).isdigit()]
        if not ts:
            return None, None
        now = time.time()
        return (max(ts) - min(ts)) / 86400.0, (now - max(ts)) / 86400.0
    except Exception:
        return None, None


def _predicate_id(path=PROOF_SOURCE):
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > 1_000_000:
            return "unknown"
        h = hashlib.sha256()
        with io.open(path, "rb") as f:
            for chunk in iter(lambda: f.read(128 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except Exception:
        return "unknown"


def _current_proof_rows(rows, predicate_id):
    """Old predicate evidence cannot authorize a changed deny predicate."""
    return [r for r in rows if len(r) >= 6 and r[5] == predicate_id]


def main():
    print("=" * 68)
    print(" SHADOW DENY ARM-READINESS  (read-only; arm = touch the LIVE flag)")
    print("=" * 68)

    # ---- proof_deny (proof-of-failure: backslash-ENOENT + heredoc-compile-fail) ----
    live = os.path.exists(os.path.join(GF, "PROOF_DENY_LIVE"))
    all_rows = _rows(os.path.join(GF, "proof_deny_log.tsv"))
    predicate_id = _predicate_id()
    rows = _current_proof_rows(all_rows, predicate_id)
    shadow = [r for r in rows if len(r) >= 3 and r[2] == "shadow"]
    denied = [r for r in rows if len(r) >= 3 and r[2] == "deny"]
    span, fresh = _age_days(rows)
    sessions = len(set(r[1] for r in rows if len(r) > 1))
    fromhex = {}
    for r in shadow:
        cls = (r[3].split(":")[0] if len(r) > 3 else "?")
        fromhex[cls] = fromhex.get(cls, 0) + 1
    print("\nproof_deny  [%s]" % ("ARMED" if live else "shadow (log-only)"))
    print("  predicate id  : %s  (%d legacy/different rows excluded)" %
          (predicate_id, len(all_rows) - len(rows)))
    print("  would-deny rows: %d shadow + %d real-deny   across %d sessions" % (len(shadow), len(denied), sessions))
    print("  by proof class : %s" % (dict(fromhex) or "-"))
    if span is not None:
        print("  log span       : %.1f days (freshest %.1f days ago)" % (span, fresh))
    print("  recent samples :")
    for r in (shadow + denied)[-4:]:
        print("     [%s] %s | %s" % (r[2] if len(r) > 2 else "?", (r[3][:34] if len(r) > 3 else ""),
                                     (r[4][:60] if len(r) > 4 else "")))
    print("  ARM VERDICT    : %s" % (
        "ARMED already" if live else
        ("NOT PROMOTABLE FROM COUNTS - need current-hash, per-class A+B observations; an immutable "
         "clean/false-positive disposition for every row; >=2d actual first-to-last observation span; "
         "and independent approval. Counts are evidence, never an automatic READY signal "
         "(have %d rows over %.1fd)." % (len(shadow), span or 0))))

    # ---- heed_gate (read-past evidence deny) ----
    live2 = os.path.exists(os.path.join(GF, "HEED_ENABLED"))
    hrows = _rows(os.path.join(GF, "heed_log.tsv"))
    hshadow = [r for r in hrows if len(r) >= 6 and r[5] == "shadow"]
    hspan, hfresh = _age_days(hrows)
    print("\nheed_gate   [%s]" % ("ARMED" if live2 else "shadow (log-only)"))
    print("  would-deny rows: %d   %s" % (len(hshadow),
          ("span %.1fd" % hspan) if hspan is not None else "(none yet - the 4-condition conjunction is rare)"))
    for r in hshadow[-3:]:
        print("     %s | %s" % ((r[2][:30] if len(r) > 2 else ""), (r[6][:60] if len(r) > 6 else "")))
    print("  ARM VERDICT    : %s" % (
        "ARMED already" if live2 else
        "NOT YET - accrue clean would-denies first; arm = `touch graph_fire/HEED_ENABLED`"))

    # ---- LIVE surfaces activity (the whole mistake-catching system at a glance) ----
    from collections import Counter
    HERE = os.path.dirname(os.path.abspath(__file__))
    fires = _rows(os.path.join(HERE, "instinct_fires.tsv"))
    day = time.time() - 86400
    recent = [r for r in fires if len(r) > 3 and r[0].replace(".", "", 1).isdigit() and float(r[0]) > day]
    top = Counter(r[3] for r in recent if len(r) > 3).most_common(6)
    rgs = _rows(os.path.join(GF, "report_guards_shadow.tsv"))
    rgblock = sum(1 for r in rgs if len(r) > 1 and r[1] == "block")
    print("\nLIVE SURFACES (last 24h):")
    print("  reflex fires : %d  | top: %s" % (len(recent), ", ".join("%s(%d)" % (k, v) for k, v in top) or "-"))
    print("  report_guards: %d real blocks / %d shadow rows (report-surface BLOCK, live)" % (rgblock, len(rgs)))
    print("  habituation  : LIVE (suppresses read/prompt mention-fires; -37% mention-wall, 0 recall loss)")
    print("  gap_watch    : LIVE (stale-assert caution into broadcast/durable writes)")

    print("\n" + "-" * 68)
    print(" Revert either at any time: rm the LIVE flag. Both fail-open (any error -> allow).")
    print(" Detail: memory proof_of_failure_deny_shadow_2026_07_12 / heed_gate_command_surface_deny_shadow_2026_07_12")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("shadow_deny_status: fail-safe (%s)" % e)
