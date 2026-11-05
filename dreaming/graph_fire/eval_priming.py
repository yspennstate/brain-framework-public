#!/usr/bin/env python3
"""eval_priming.py - measure the LIVE-EFFECT of conscious->subconscious priming on the firing surface.

The blind spot this closes: calibrate.py sets each reflex's threshold on the RAW token score, and
eval_real.nag() measures nag the same way - neither exercises the shared activation buffer. But
production gfcore.graph_fire() lowers each reflex's bar by a priming term read from the session buffer
(a scar the deliberate layer just recalled). So the real, deployed nag/recall differ from the calibrated
numbers, and by how much was never measured. This replays real held-out actions + real corrections
through the ACTUAL buffer-primed path, using the real .abuf_* buffers as priors, and reports union
(regex OR graph) nag and mistake-recall under three conditions:

  COLD    - no buffer (= what calibrate.py / eval_real.nag see)
  PRIMED  - the real live buffers as priors (= what sessions actually experience: the deployed behaviour)
  FIXED   - hub srcs de-weighted by 1/share_count, so a scar shared by 20 reflexes (MISTAKES) stops
            priming all 20 at once; a uniquely-sourced scar still primes its own reflex fully

Why FIXED exists: priming is meant to be TARGETED (recall surfaced THIS scar -> prime THIS reflex), but
MISTAKES is the src of ~20 reflexes and is hot in almost every live buffer (self-deposited by note_fires),
so it primes 20 reflexes whenever any one fires - non-targeted, and it grows with session length. FIXED is
the candidate correction; this tool measures whether it is worth the recall it costs.

Read-only, stdlib, forward-slash paths, fail-safe (any error on a row -> that row counts as no-fire, never
throws). No heavy compute - pure token dot-products over a bounded sample.
"""
import os, io, sys, json, collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gfcore
import activation_buffer as ab
try:
    import eval_real as er
except Exception:
    er = None

HUB_SET = None  # computed from the model: src -> share_count


def _share_counts(model):
    c = collections.Counter()
    for m in model.values():
        s = m.get("src")
        if s:
            c[s] += 1
    return c


def _graph_fire(text, model, share, prior, mode):
    """Reproduce gfcore.graph_fire's scoring for one action under a given prior, returning the fired ids.
    mode: 'cold' (ignore prior), 'primed' (gfcore's live rule), 'fixed' (hub src/anchor de-weighted 1/share)."""
    out = set()
    try:
        ctoks = set(gfcore._toks(text))
        if not ctoks:
            return out
        for rid, m in model.items():
            if m.get("mode") == "carry":
                continue
            toks = m.get("tokens", {})
            if not toks:
                continue
            score = 0.0
            nmatch = 0
            for t in ctoks:
                w = toks.get(t)
                if w:
                    score += w
                    nmatch += 1
            if nmatch < gfcore.MIN_MATCH:
                continue
            prime = 0.0
            if mode != "cold":
                src = m.get("src")
                if src and src in prior:
                    base = min(0.7, 0.6 * prior[src])
                    prime += base if mode == "primed" else base / max(1, share.get(src, 1))
                for n in m.get("anchors", {}):
                    if n in prior:
                        ab_ = min(0.3, 0.2 * prior[n])
                        prime += ab_ if mode == "primed" else ab_ / max(1, share.get(n, 1))
                prime = min(1.0, prime)
            thr = max(gfcore.MIN_THRESHOLD, m.get("thr", gfcore.BASE_THRESHOLD) - prime)
            if score >= thr:
                out.add(rid)
    except Exception:
        return out
    return out


def _real_buffers(limit=12):
    """The most-primed real live buffers (worst case for the hub effect), as {session: prior_nodes}."""
    priors = []
    try:
        for f in os.listdir(HERE):
            if not f.startswith(".abuf_"):
                continue
            if any(t in f for t in ("deadbeef", "selftest", "zztest")):
                continue
            sess = f[len(".abuf_"):-len(".json")]
            st = ab.read(sess)
            nodes = st.get("nodes", {})
            if nodes:
                priors.append((len(nodes), sess, nodes))
    except Exception:
        pass
    priors.sort(reverse=True)
    return [(s, n) for _, s, n in priors[:limit]]


def _regex(text):
    if er is not None:
        try:
            return er.fire_regex(text)
        except Exception:
            return set()
    return set()


def run(sample_buffers=12, verbose=True):
    model = gfcore._model()
    share = _share_counts(model)
    if er is None:
        print("eval_real not importable - cannot load the real corpora; abort (fail-safe).")
        return
    typ = er.load_typical()[1::2]                 # held-out half (calibration used the even half)
    mist = er.positives_mistakes()
    def mtext(x):
        return x["text"] if isinstance(x, dict) else (x[0] if isinstance(x, (list, tuple)) else x)
    typ_txt = [r["text"] for r in typ]
    mist_txt = [mtext(r) for r in mist]
    buffers = _real_buffers(sample_buffers)
    print("eval_priming: %d held-out typical actions | %d real corrections | %d real buffers sampled"
          % (len(typ_txt), len(mist_txt), len(buffers)))
    print("hub share counts: MISTAKES=%d self_model=%d (a src shared by N reflexes primes all N at once)"
          % (share.get("MISTAKES", 0), share.get("self_model_failure_modes_2026_07_07", 0)))

    def measure(prior, mode):
        nag = sum(1 for t in typ_txt if (_regex(t) | _graph_fire(t, model, share, prior, mode)))
        rec = sum(1 for t in mist_txt if (_regex(t) | _graph_fire(t, model, share, prior, mode)))
        return nag / max(1, len(typ_txt)), rec / max(1, len(mist_txt))

    # COLD once; PRIMED / FIXED averaged over the sampled real buffers
    cn, cr = measure({}, "cold")
    pn = pr = fn = fr = 0.0
    for sess, prior in buffers:
        a, b = measure(prior, "primed"); pn += a; pr += b
        c, d = measure(prior, "fixed"); fn += c; fr += d
    k = max(1, len(buffers))
    pn, pr, fn, fr = pn / k, pr / k, fn / k, fr / k
    print("\n  condition   UNION-nag   mistake-recall")
    print("  COLD        %6.1f%%      %6.1f%%   (calibrated baseline; what eval_real.nag sees)" % (100*cn, 100*cr))
    print("  PRIMED      %6.1f%%      %6.1f%%   (DEPLOYED: real buffers - the true live behaviour)" % (100*pn, 100*pr))
    print("  FIXED       %6.1f%%      %6.1f%%   (1/share hub de-weight - candidate)" % (100*fn, 100*fr))
    print("\n  priming's live effect vs calibrated : nag %+.1f pp, recall %+.1f pp" % (100*(pn-cn), 100*(pr-cr)))
    print("  FIXED vs PRIMED                     : nag %+.1f pp, recall %+.1f pp" % (100*(fn-pn), 100*(fr-pr)))
    print("  => FIXED gives back %.1f pp of nag for %.1f pp of recall vs deployed." % (100*(pn-fn), 100*(pr-fr)))


def _selftest():
    model = gfcore._model()
    share = _share_counts(model)
    for junk in ["", None, 123, "x" * 20000]:
        try:
            _graph_fire(junk, model, share, {"MISTAKES": 6.0}, "primed")
        except Exception as e:
            print("FAIL-SAFE BREACH:", e); return 1
    print("eval_priming selftest OK (fail-safe held); model reflexes:", len(model))
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    n = 12
    for a in sys.argv[1:]:
        if a.startswith("--buffers="):
            n = int(a.split("=", 1)[1])
    run(sample_buffers=n)
