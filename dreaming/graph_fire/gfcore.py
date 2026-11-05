#!/usr/bin/env python3
"""gfcore.py - the graph-derived reflex-firing engine (runtime, fast).

The gut used to fire ONLY when a reflex's hand-written regex literally matched the action text - so
'heredoc' never woke the 'CRLF' scar, and a flattering number reported in prose woke nothing. This adds
the missing GRAPH path: build_anchors.py precomputed, per reflex, a graph-diffused {token: weight}
vector (spreading activation from the reflex's scar node over the association graph, projected onto
vocabulary). At runtime we score the live action's tokens against those vectors - so a situation that is
GRAPH-NEAR a scar fires the reflex even when its own words don't appear. It is a union WITH the regex
path (instinct_prime keeps its regex fires); this only ever ADDS recall.

Caution bias, as the owner asked ('fire faster and more, err on caution'):
  - the fire threshold sits deliberately low, and
  - a per-session DANGER accumulator (activation_buffer) lowers it further as fires/felt-errors pile up,
    so once a session is in trouble the whole gut gets touchier.
Conscious<->subconscious sharing: reflexes whose scar/anchor nodes are hot in the shared buffer (because
recall just surfaced them) get a priming boost - the deliberate layer primes the reflex layer.

Fail-safe by construction: every entry point is wrapped; any error -> [] (no fire), never a throw, never
a block. Forward-slash paths, stdlib only, no heavy load - the graph work already happened offline.
"""
import os, io, json, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)
try:
    from append_journal import append_text as _append_text
except Exception:
    _append_text = None
# prefer the per-reflex CALIBRATED model (calibrate.py, thresholds bounded to a false-alarm budget on real
# typical actions); fall back to the uniform-threshold model if calibration hasn't been run.
_CAL = os.path.join(HERE, "reflex_anchors_cal.json")
ANCH = _CAL if os.path.exists(_CAL) else os.path.join(HERE, "reflex_anchors.json")
SHADOW_LOG = os.path.join(HERE, "gf_shadow_fires.tsv")

# Caution-biased knobs (below spread_recall's precision-tuned ACT_FLOOR on purpose).
BASE_THRESHOLD = 1.05   # sum of matched (normalised) token weights needed to graph-fire a reflex
DANGER_GAIN = 0.16      # each unit of accumulated session danger lowers the threshold this much
MIN_THRESHOLD = 0.55    # never drop below this, however dangerous the session feels
MIN_MATCH = 2           # need >=2 distinct reflex tokens present (one generic token must not fire)
MAX_FIRE = 4            # cap simultaneous graph-fires surfaced

_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9]{2,}")
# same glue-word filter build_anchors used, so context tokens match the reflex vectors' vocabulary
_STOP = set("""the a an and or of to in on for by is are was were be been being it its this that these those
with without into onto from as at we you he she they them his her their our your my me not no yes do did done
if then else than so but scar gist method feedback project reference dream daydream 2026 2025 jan feb mar apr may
jun jul aug sep oct nov dec new old now day days before after check make sure look take run use get keep need want
first still just also see saw open said say tell point place change move find what which when where why how who into
out off down over under about around here there this that will would could should can may might must more most less
very really thing things one two three""".split())


def _toks(s):
    out = []
    for w in _WORD.findall(s or ""):
        w = w.lower()
        if w in _STOP or w.isdigit():
            continue
        out.append(w)
    return out


_MODEL = None


def _model():
    global _MODEL
    if _MODEL is None:
        try:
            _MODEL = json.load(io.open(ANCH, encoding="utf-8")).get("reflexes", {})
        except Exception:
            _MODEL = {}
    return _MODEL


HUB_FANOUT = 5   # a source or anchor node shared by more reflexes than this carries no specific evidence
_FANOUT = None


def _fanout():
    """How many reflexes name each node as their source or anchor. Measured 2026-09-02: of 83 reflexes in the
    model, 31 cite MISTAKES and 11 cite self_model_failure_modes as their source, so a deliberate recall that
    surfaced either hub primed forty-two reflexes at once - five graph fires in an hour, none on-domain, every
    one primed through a hub. A hub node does not prime; a scar's own memory (fan-out 1) does."""
    global _FANOUT
    if _FANOUT is None:
        fan = {}
        for m in _model().values():
            s = m.get("src")
            if s:
                fan[s] = fan.get(s, 0) + 1
            for a in (m.get("anchors") or {}):
                fan[a] = fan.get(a, 0) + 1
        _FANOUT = fan
    return _FANOUT


def _buffer_state(session):
    if not session:
        return {"nodes": {}, "danger": 0.0}
    try:
        sys.path.insert(0, HERE)
        import activation_buffer as ab
        return ab.read(session)
    except Exception:
        return {"nodes": {}, "danger": 0.0}


def graph_fire(text, session=None, thr_mult=1.0):
    """Rank the reflexes the graph model fires on `text`, caution-biased by the session danger
    accumulator and primed by the shared buffer. thr_mult scales the per-reflex threshold: PreToolUse
    (fires per tool call, nagging is the cost) uses 1.0; the Stop/prose surface (fires once a turn, a
    wrong number reaching the owner is the worst case) passes <1 to fire readier. Pure. Fail-safe -> []."""
    try:
        model = _model()
        if not model or not text:
            return []
        ctoks = set(_toks(text))
        if not ctoks:
            return []
        st = _buffer_state(session)
        danger = st.get("danger", 0.0)
        prior = st.get("nodes", {})
        fires = []
        for rid, m in model.items():
            if m.get("mode") == "carry":
                continue  # carried notes never auto-fire
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
            if nmatch < MIN_MATCH:
                continue
            # conscious priming: a scar the DELIBERATE layer just surfaced (hot in the shared buffer)
            # LOWERS this reflex's firing bar - the two layers share evidence. Targeted, unlike the
            # session-wide danger accumulator. Scaled to matter against the calibrated thresholds (~1.3-3+).
            prime = 0.0
            src = m.get("src")
            fan = _fanout()
            if src and src in prior and fan.get(src, 1) <= HUB_FANOUT:
                prime += min(0.7, 0.6 * prior[src])
            for n in m.get("anchors", {}):
                if n in prior and fan.get(n, 1) <= HUB_FANOUT:
                    prime += min(0.3, 0.2 * prior[n])
            prime = min(1.0, prime)
            eff = score
            # per-reflex calibrated threshold (m['thr']), relaxed by the session danger accumulator (caution
            # that builds) AND by conscious priming (a just-recalled scar). Falls back to BASE_THRESHOLD.
            thr_r = max(MIN_THRESHOLD, m.get("thr", BASE_THRESHOLD) * thr_mult - DANGER_GAIN * danger - prime)
            if eff >= thr_r:
                fires.append({"id": rid, "score": round(eff, 3), "nmatch": nmatch,
                              "thr": round(thr_r, 3), "primed": round(prime, 3),
                              "charge": m.get("charge", 0.6), "validity": m.get("validity"),
                              "mode": m.get("mode", "fire"), "src": src})
        # rank: affective charge leads (a burn over a paper-cut), nudged by match strength
        fires.sort(key=lambda f: (0.5 * f["charge"] + f["score"]), reverse=True)
        return fires[:MAX_FIRE]
    except Exception:
        return []


def note_fires(session, fires, extra_danger=0.0):
    """Deposit the fired reflexes' scar/anchor nodes + a danger bump into the shared buffer, so the
    conscious layer sees what the gut just fired and the session gets more cautious. extra_danger carries
    a REAL failure signal (a recent outcomes.jsonl error) - failures self-excite (measured ~20-min
    clustering), so an actual failure should ramp caution even with no reflex fire. Fail-safe."""
    try:
        if not session or (not fires and extra_danger <= 0):
            return 0.0
        sys.path.insert(0, HERE)
        import activation_buffer as ab
        model = _model()
        nodes = {}
        for f in fires:
            src = f.get("src")
            if src:
                nodes[src] = nodes.get(src, 0.0) + 0.6 * float(f.get("charge", 0.6))
            for n in (model.get(f["id"], {}) or {}).get("anchors", {}):
                nodes[n] = nodes.get(n, 0.0) + 0.15
        # danger ramps on REAL failures (extra_danger, a recent outcomes.jsonl error), NOT on ordinary
        # caution-fires - else a scar-dense session inflates danger and over-fires via the danger channel.
        # The node deposits (for cross-layer priming) still happen every fire; only danger is failure-gated.
        return ab.deposit(session, nodes, danger_bump=float(extra_danger or 0.0))
    except Exception:
        return 0.0


def shadow_log(session, text, fires):
    """Append what WOULD have fired (shadow mode), for eval before going live. Fail-safe, secret-masked."""
    try:
        # strip TAB + CR + LF (same CR-split fix as instinct_prime's fire-log: a bare 0x0D from a Windows
        # CRLF snippet splits the row under universal-newline readers - 01619e69 found 3 such rows here).
        snip = re.sub(r"sk-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----",
                      "<masked>", text or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")[:120]
        row = "%.0f\t%s\t%s\t%s\n" % (time.time(), (session or "")[:12],
                                        ",".join(f["id"] for f in fires) or "-", snip)
        if _append_text is not None:
            _append_text(SHADOW_LOG, row)
    except Exception:
        pass


def _selftest():
    m = _model()
    print("model reflexes:", len(m))
    cases = [
        ("the sim backtest earns about $999k per year on abc", "sim number -> sim_flatters/risk/sim_sign"),
        ("that inverse fund's carry is basically just the expense ratio, negligible", "leveraged etf -> leveraged_etf_is_not_linear"),
        ("filter the days by regime where vix < 15 then measure the edge", "lookahead"),
        ("grep -n def main foo.py", "benign read -> should be quiet"),
        ("", "empty -> []"),
        (None, "None -> [] (fail-safe)"),
    ]
    for txt, note in cases:
        fires = graph_fire(txt, session=None)
        print("  [%s] %-45s -> %s" % ("ok" if isinstance(fires, list) else "ERR", note,
                                      [(f["id"], f["score"]) for f in fires[:4]]))
    # fail-safe on junk
    for junk in [123, {"x": 1}, "x" * 50000]:
        try:
            graph_fire(junk, None)
        except Exception as e:
            print("  FAIL-SAFE BREACH on", type(junk), e)
            return 1
    print("selftest OK (fail-safe held)")
    return 0


def main():
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    # else: read text from stdin, print fires as JSON (used by the shadow eval)
    try:
        txt = sys.stdin.read()
    except Exception:
        txt = ""
    sess = None
    for a in sys.argv[1:]:
        if a.startswith("--session="):
            sess = a.split("=", 1)[1]
    print(json.dumps(graph_fire(txt, sess)))


if __name__ == "__main__":
    main()
