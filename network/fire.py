"""Runtime firing engine: leaky activation buffer + one-hop spreading activation +
k-of-n IDF-gated pattern completion over stored episodes.

Neuroscience shape: leaky integrate(-and-decay) activation, k-winners-take-all
propagation (bounded cascade), CA3/Hopfield-style completion by weighted overlap,
habituation (inhibition of return) on retrievals. All state is in-process; callers
persist what they need.

Library use:
    eng = FireEngine(NetStore("net.sqlite3"))
    eng.observe(["tool:Edit", "kw:backtest"])       # per event
    hits = eng.complete()                            # [] or [(prompt_node, score), ...]
"""
import math, time
from collections import defaultdict

DECAY_HALFLIFE_EVENTS = 6.0     # buffer half-life, in observe() calls
SPREAD_FRACTION = 0.35          # activation pushed to neighbors (one hop)
K_WINNERS = 32                  # only the top-K active nodes propagate / match
K_MIN_OVERLAP = 5               # distinct pattern nodes required to complete
IDF_SPECIFICITY_QUANTILE = 0.5  # overlap counts only nodes above the pattern's median idf
FIRE_THRESHOLD = 0.35           # cosine threshold (calibrate from shadow ledger)
COOLDOWN_S = 900.0              # habituation per retrieved prompt


class FireEngine:
    def __init__(self, store, threshold=FIRE_THRESHOLD, k_min=K_MIN_OVERLAP,
                 now=time.time):
        self.st = store
        self.threshold = threshold
        self.k_min = k_min
        self.now = now
        self.act = defaultdict(float)    # node id -> activation (injections + spread)
        self.direct = defaultdict(float)  # node id -> activation from DIRECT observation only;
                                          # evidence for the k-of-n gate is external input,
                                          # never the network's own recurrent completion
        self.last_fired = {}             # prompt_node -> ts
        self._nbr_cache = {}             # nid -> top co-neighbors; a node is queried at most
                                         # once per engine (spreading activation over a large
                                         # graph must not hit the DB per step - that made the
                                         # eval ~5M queries/replay and would tax a live hook)
        self._decay = 0.5 ** (1.0 / DECAY_HALFLIFE_EVENTS)
        self._episodes = None           # lazy cache: list of (eid, prompt, pattern, norm, cutoff)

    # ---- observation ----
    def observe(self, node_ids):
        for nid in self.act:
            self.act[nid] *= self._decay
        for nid in self.direct:
            self.direct[nid] *= self._decay
        for nid in node_ids:
            self.act[nid] += 1.0
            self.direct[nid] += 1.0
        self._spread()
        # drop the long tails so the buffers stay sparse
        if len(self.act) > 4 * K_WINNERS:
            keep = sorted(self.act.items(), key=lambda kv: -kv[1])[:4 * K_WINNERS]
            self.act = defaultdict(float, keep)
        if len(self.direct) > 4 * K_WINNERS:
            keep = sorted(self.direct.items(), key=lambda kv: -kv[1])[:4 * K_WINNERS]
            self.direct = defaultdict(float, keep)

    def _neighbors(self, nid):
        nbrs = self._nbr_cache.get(nid)
        if nbrs is None:
            nbrs = self.st.neighbors(nid, kind="co", limit=8)
            self._nbr_cache[nid] = nbrs
        return nbrs

    def _spread(self):
        winners = sorted(self.act.items(), key=lambda kv: -kv[1])[:K_WINNERS]
        pushes = defaultdict(float)
        for nid, a in winners:
            if a <= 0.05:
                continue
            nbrs = self._neighbors(nid)
            tot = sum(w for _, w in nbrs) or 1.0
            for dst, w in nbrs:
                pushes[dst] += a * SPREAD_FRACTION * (w / tot)
        for dst, p in pushes.items():
            self.act[dst] += p

    # ---- pattern completion ----
    def _load_episodes(self, before_ts=None):
        eps = []
        for eid, ets, pn, pat, norm in self.st.episodes_before(before_ts):
            idfs = sorted(pat.values())
            cutoff = idfs[len(idfs) // 2] if idfs else 0.0
            eps.append((eid, ets, pn, pat, norm, cutoff))
        self._episodes = eps

    def complete(self, top=3, before_ts=None):
        """Return prompts whose stored pattern the current buffer completes."""
        if self._episodes is None:
            self._load_episodes(before_ts)
        active = dict(sorted(self.act.items(), key=lambda kv: -kv[1])[:K_WINNERS])
        if not active:
            return []
        a_norm = math.sqrt(sum(v * v for v in active.values())) or 1.0
        now = self.now()
        hits = []
        for eid, ets, pn, pat, norm, cutoff in self._episodes:
            if now - self.last_fired.get(pn, 0.0) < COOLDOWN_S:
                continue
            dot, k_specific = 0.0, 0
            for nid, w in pat.items():
                a = active.get(nid)
                if a is None:
                    continue
                dot += a * w
                # evidence gate: only directly observed, above-median-idf pattern
                # nodes count; recurrent spread scores but never testifies
                if w >= cutoff and self.direct.get(nid, 0.0) > 0.2:
                    k_specific += 1
            if k_specific < self.k_min:
                continue
            score = dot / (a_norm * norm)
            if score >= self.threshold:
                hits.append((pn, round(score, 4), k_specific))
        hits.sort(key=lambda h: -h[1])
        hits = hits[:top]
        for pn, _, _ in hits:
            self.last_fired[pn] = now
        return hits

    def complete_with_content(self, top=3, before_ts=None):
        """Like complete(), but each hit carries the retrievable information of the
        node the cascade reached: (node_id, score, k_specific, content). This is the
        'a big enough signal fires the node and you get its information' step."""
        out = []
        for pn, score, k in self.complete(top=top, before_ts=before_ts):
            out.append((pn, score, k, self.st.content(pn)))
        return out

    def recall_memories(self, seed_nodes, top=8, steps=3, min_activation=0.05,
                        min_anchors=2):
        """Topic-level memory retrieval: seed the buffer with a situation's nodes
        (keywords, tools, files), spread activation a few steps, and return the most
        activated memory nodes. This is the network's VALIDATED retrieval value - held-out
        measurement showed single-episode prompt->prompt completion fires ~0%, but seeding
        by topic reliably surfaces topically relevant memories. Use this to answer 'what do
        I know that bears on the current situation', not 'what prompt comes next'.

        POLYSEMY PRECISION GATE (min_anchors): a memory must connect to at least this many
        DISTINCT seed nodes to surface. A single polysemous keyword (e.g. "network" meaning
        both the association-network and a GPU network) can pull an off-topic memory through
        one strong edge; requiring >=2 distinct on-topic anchors filters that noise while
        keeping genuinely on-topic memories (which connect through several seed terms).
        Verified on the live graph: this drops the GPU-network memory from the brain-topic
        recall while keeping the mistake/dream/reflex memories.

        Returns [(mem_node_id, activation), ...].
        """
        seeds = list(seed_nodes)
        for _ in range(max(1, steps)):
            self.observe(seeds)
        # count distinct seed nodes each memory is directly reachable from (co-edges)
        anchors = defaultdict(set)
        if min_anchors > 1:
            for s in set(seeds):
                for dst, _w in self.st.neighbors(s, kind="co", limit=64):
                    if dst.startswith("mem:"):
                        anchors[dst].add(s)
        mems = [(nid, a) for nid, a in self.act.items()
                if nid.startswith("mem:") and a >= min_activation
                and (min_anchors <= 1 or len(anchors.get(nid, ())) >= min_anchors)]
        mems.sort(key=lambda x: -x[1])
        return mems[:top]

    def complete_hopfield(self, top=3, before_ts=None, beta=8.0, concentration=0.30):
        """Modern-Hopfield associative retrieval (Ramsauer et al. 2020): instead of a
        hard cosine threshold on the single best episode, weight every stored episode by
        softmax(beta * cosine(buffer, pattern)) and fire when the retrieval CONCENTRATES
        - the top softmax weight exceeds `concentration`. beta is the inverse temperature
        (sharpness); as evidence accumulates the distribution sharpens toward one attractor.

        Smoother than complete(): a situation that resembles a cluster of past episodes
        still retrieves the nearest attractor even when no single cosine clears a fixed
        threshold. The same evidence gate applies - k_min directly-observed specific nodes -
        so it cannot fire on recurrent spread alone.

        Returns [(prompt_node, softmax_weight, k_specific), ...] for the concentrated hits.
        """
        if self._episodes is None:
            self._load_episodes(before_ts)
        active = dict(sorted(self.act.items(), key=lambda kv: -kv[1])[:K_WINNERS])
        if not active:
            return []
        a_norm = math.sqrt(sum(v * v for v in active.values())) or 1.0
        now = self.now()
        sims, meta = [], []
        for eid, ets, pn, pat, norm, cutoff in self._episodes:
            if now - self.last_fired.get(pn, 0.0) < COOLDOWN_S:
                continue
            dot, k_specific = 0.0, 0
            for nid, w in pat.items():
                a = active.get(nid)
                if a is None:
                    continue
                dot += a * w
                if w >= cutoff and self.direct.get(nid, 0.0) > 0.2:
                    k_specific += 1
            cos = dot / (a_norm * norm)
            sims.append(cos)
            meta.append((pn, k_specific))
        if not sims:
            return []
        # numerically stable softmax over episode similarities
        m = max(sims)
        exps = [math.exp(beta * (s - m)) for s in sims]
        z = sum(exps) or 1.0
        weights = [e / z for e in exps]
        ranked = sorted(zip(weights, meta), key=lambda x: -x[0])
        hits = []
        for wgt, (pn, k_specific) in ranked[:top]:
            if wgt >= concentration and k_specific >= self.k_min:
                hits.append((pn, round(wgt, 4), k_specific))
        for pn, _, _ in hits:
            self.last_fired[pn] = now
        return hits
