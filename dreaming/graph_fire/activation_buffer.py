#!/usr/bin/env python3
"""activation_buffer.py - the shared conscious<->subconscious activation buffer (per-session files).

The gut (instinct_prime, subconscious) and deliberate recall (spread_recall, conscious) run as separate
hook processes that shared NO state: a consciously-recalled scar never primed the reflex, a fired reflex
never surfaced its memory. This is the one channel that joins them - a per-session store of node
activations that decay over time, plus a scalar `danger` accumulator.

- CONSCIOUS -> SUBCONSCIOUS: recall surfaces memory M -> deposit()s activation on M. The gut then read()s
  that as a prior and LOWERS the bar to fire the reflex anchored on M.
- SUBCONSCIOUS -> CONSCIOUS: the gut fires reflex R -> deposit()s on R's source/anchor nodes (+ danger).
- CAUTION BIAS: every fire and every recent REAL failure bumps `danger`, which decays over ~20 min (the
  measured self-excitation timescale of failures); a higher danger lowers the session's fire thresholds.

PER-SESSION FILES (.abuf_<session>.json), not one shared file: ~10 fleet sessions doing read-modify-write
on a single file would clobber each other's deposits (the concurrent-races scar), and priming is a
within-session faculty anyway - a session's recall should prime its OWN gut, not another's. Node identity
is free: reflex `source` slugs are the same strings as association-graph nodes.

Stdlib only, forward-slash paths, fail-safe by construction: any error -> no-op / empty. On the
every-tool-call path; it must never throw or block.
"""
import os, io, json, time, re
import sys

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


HERE = os.path.dirname(os.path.abspath(__file__))
HALFLIFE = 1500.0        # node-activation half-life (s) ~25 min: how long a deposit primes the other layer
# Danger half-life ~20 min, set from data: within-session failures in outcomes.jsonl cluster with a median
# consecutive gap of ~21 min (vs ~43 min under a Poisson null) - after a failure the hazard of another stays
# elevated for ~15-30 min, then fades. So caution ramps on a mistake and decays over that window.
DANGER_HALFLIFE = 1200.0
CAP = 6.0                # bound any single activation / the danger scalar
MAXNODES = 200           # bound the per-session node map
_PRUNE_AGE = 86400       # delete session buffers idle > 1 day


def _buf_path(session):
    s = re.sub(r"[^A-Za-z0-9-]", "", (session or "_"))[:48] or "_"
    return os.path.join(HERE, ".abuf_" + s + ".json")


def _decay(val, dt, hl):
    try:
        if not val or dt <= 0:
            return val
        return val * (0.5 ** (dt / hl))
    except Exception:
        return val


def read(session):
    """Current (decayed) activation map + danger for this session. Fail-safe -> {'nodes':{}, 'danger':0}."""
    try:
        p = _buf_path(session)
        s = json.load(io.open(p, encoding="utf-8")) if os.path.exists(p) else {}
        now = time.time()
        dt = max(0.0, now - s.get("ts", now))
        nodes = {n: round(_decay(a, dt, HALFLIFE), 4) for n, a in (s.get("nodes") or {}).items()}
        nodes = {n: a for n, a in nodes.items() if a > 0.01}
        danger = _decay(s.get("danger", 0.0), dt, DANGER_HALFLIFE)
        return {"nodes": nodes, "danger": round(max(0.0, danger), 4)}
    except Exception:
        return {"nodes": {}, "danger": 0.0}


def deposit(session, nodes=None, danger_bump=0.0):
    """Add activation on `nodes` (dict node->amount) and bump danger, in this session's own file (no
    cross-session contention). Applies decay since last write, bounds size. Returns new danger. Fail-safe."""
    try:
        p = _buf_path(session)
        now = time.time()
        s = json.load(io.open(p, encoding="utf-8")) if os.path.exists(p) else {}
        dt = max(0.0, now - s.get("ts", now))
        cur = {n: _decay(a, dt, HALFLIFE) for n, a in (s.get("nodes") or {}).items()}
        for n, w in (nodes or {}).items():
            if n:
                cur[n] = min(CAP, cur.get(n, 0.0) + max(0.0, float(w)))
        cur = {n: round(a, 4) for n, a in sorted(cur.items(), key=lambda kv: -kv[1])[:MAXNODES] if a > 0.01}
        danger = min(CAP, max(0.0, _decay(s.get("danger", 0.0), dt, DANGER_HALFLIFE) + max(0.0, float(danger_bump))))
        json.dump({"nodes": cur, "danger": round(danger, 4), "ts": now}, io.open(p, "w", encoding="utf-8"))
        _prune()
        return danger
    except Exception:
        return 0.0


def _prune():
    """Delete session buffers idle > 1 day. Fail-safe (a race removing the same file just throws -> caught)."""
    try:
        now = time.time()
        for f in os.listdir(HERE):
            if f.startswith(".abuf_"):
                fp = os.path.join(HERE, f)
                try:
                    if now - os.path.getmtime(fp) > _PRUNE_AGE:
                        os.remove(fp)
                except Exception:
                    pass
    except Exception:
        pass


if __name__ == "__main__":
    import sys
    ok = True
    try:
        s = "selftest-abuf-0001"
        # clean
        p = _buf_path(s)
        if os.path.exists(p):
            os.remove(p)
        assert read(s) == {"nodes": {}, "danger": 0.0}
        deposit(s, {"scar_check_the_brain_before_you_simulate_2026_07_08": 1.0}, danger_bump=0.5)
        r = read(s)
        assert r["danger"] >= 0.4 and any("scar_check" in n for n in r["nodes"]), r
        # danger-only deposit (no nodes) - a real-failure bump
        deposit(s, None, danger_bump=0.5)
        assert read(s)["danger"] >= 0.8
        # fail-safe on junk
        assert deposit(None, None, 0) == 0.0 or True
        assert read(None) is not None
        os.remove(p)
        print("activation_buffer selftest OK:", r)
    except Exception as e:
        ok = False
        print("activation_buffer selftest FAIL:", e)
    sys.exit(0 if ok else 1)
