"""membrane.py - the step from the brain graph (the subconscious) into an agent's context (the conscious).

His 26 Sep voice note: "The brain is a network in the background, which is subconscious ... a subconscious graph that is
feeding in information to the conscious, to the context window." This module is that feed, as a library. It knows nothing
about where the graph came from (the capture and the graph are claude-9209cf81's); it takes nodes with a class and a use
history, typed weighted edges, and the nodes in play right now, and returns the few items that should reach the context.

The mathematics (NODE_CLASSES.md, sections 3 and 5; the measurements behind version 2 are in REVIEW.md):

  size        B_i = ln( sum_k (t - t_k)^(-d) ), 0 < d < 1   ACT-R base-level learning (Anderson and Schooler 1991). The newest
                                                          `keep` uses are summed exactly and the older ones by Petrov's (2006)
                                                          hybrid approximation, as if spread evenly between the oldest kept
                                                          use and the first. A use later than `now` is ignored; a node never
                                                          used gets the base level of one use `never_used_age_s` ago.
  association S_ji = ln( P(i | j) / P(i) ) / -ln P(i, j),   the normalised PMI (Bouma 2009), floored at 0. P(i | j) is j's
              floored at 0                                edge to i over j's whole outgoing weight (the graph store's true row
                                                          sum when it caps a row), P(i) i's share of all incoming weight. The
                                                          plain PMI (assoc_mode="pmi") is ACT-R's rational form; its ratio
                                                          does not fade when an edge's evidence decays, and it favours rare
                                                          targets, both measured in REVIEW.md, so the normalised form is the
                                                          default.
  spreading   A_i = size_weight * B_i + sum_j W_j S_ji + hop2 * sum_j (a_j / sum a) S_ji
                                                          W_j = 1/n shared over the n seed nodes; the second hop runs from the
                                                          nodes the first hop lit, and only when hop2 > 0. size_weight is
                                                          small on purpose: at full weight the base level decided the ranking
                                                          and the context barely moved it (REVIEW.md, section 5).
  ignition    only actionable classes, A_i >= threshold, at most k items a call, a refractory period per item unless its
              activation rose by more than a margin (global workspace ignition, Dehaene and Changeux 2011; his 2 Sep rule:
              fire less, be more confident when it fires). The threshold is to be calibrated on held-out fires for a target
              precision (prequential.py, surfacing_study); the default is only a starting point.
  hint        each item names the seed nodes and edge types that lit it, and the calibrated precision of fires like it.

Version 2, 26 Sep 2026, from the prequential evaluation (REVIEW.md, prequential.py; synthetic worlds with planted structure,
seed 7, 600 sessions, 1,935 queries): version 1 weighted the base level in full (MRR 0.409; on mistakes 0.257, on the fix memory
read after a mistake 0.278), let hop2 = 0 still enter every two-hop node on its base level, started a never-used node above one
used ten times a day before, counted a use after `now` as one second old, and recomputed each row's sum for every pair. NPMI
with the base level at 0.02, chosen on the first half of the stream and again on three further worlds, scores MRR 0.592 on the
second half against 0.428. Those are synthetic worlds; the weight and the form stay parameters until the real-stream study
(claude-9209cf81's walk-forward test on the capture) says otherwise.

What the real stream says so far (STUDY.md, ten test nights, 26 Sep): for memory reads node size is most of the signal - the
ACT-R arm (size plus the mean PMI over the context, close to version 1's balance) is best at hit@10 34.6 per cent against size
alone at 33.6 - while for places the learned edges win (the next project +8.2 points over the best simple arm). So the 0.02
weight is a synthetic-world choice with no real-data support for memories; size_weight_by_class sets a weight per node class
(for example {"mem": 1.0}) until the real-capture arms for this membrane say which weight each class wants.

    g = Graph()
    g.add_node("mistake:bash_backslash", "mistake", uses=[t1, t2])
    g.add_edge("tool:Bash", "mistake:bash_backslash", 3.0, "led_to")
    m = Membrane(g, k=2, threshold=0.2)
    items = m.step(["tool:Bash", "file:~/x.py"], now)
"""
import bisect
import collections
import math

ACTIONABLE = frozenset({"mistake", "law", "mem", "reflex", "note", "dir", "tool", "code", "doc", "thm"})
YEAR_S = 365.0 * 86400.0


class Graph:
    """Nodes with a class and a use history; directed typed weighted edges (add both directions for a symmetric edge)."""

    def __init__(self):
        self.cls = {}
        self.uses = collections.defaultdict(list)     # node -> sorted use times (seconds)
        self.older = {}                               # node -> (count, first time) of uses dropped from the list
        self.out = collections.defaultdict(dict)      # j -> {i: (weight, type)}
        self.out_w = collections.defaultdict(float)   # j -> total outgoing weight (kept by add_edge)
        self.in_w = collections.defaultdict(float)    # i -> total incoming weight
        self.total_w = 0.0

    def add_node(self, node, cls, uses=(), keep=50):
        self.cls[node] = cls
        for t in uses:
            self.use(node, t, keep=keep)

    def use(self, node, t, keep=50):
        """Record one use, in time order; beyond `keep` recent uses the oldest fold into a count for the approximation."""
        u = self.uses[node]
        bisect.insort(u, t)
        while len(u) > keep:
            n_old, first = self.older.get(node, (0, u[0]))
            self.older[node] = (n_old + 1, min(first, u[0]))
            del u[0]

    def add_edge(self, j, i, w, etype="co"):
        if j == i or w <= 0:
            return
        old = self.out[j].get(i, (0.0, etype))[0]
        self.out[j][i] = (old + w, etype if old == 0 else self.out[j][i][1])
        self.out_w[j] += w
        self.in_w[i] += w
        self.total_w += w


class Membrane:
    def __init__(self, graph, k=2, threshold=0.2, d=0.5, hop2=0.5, refractory_s=1800.0, rise=0.5,
                 actionable=ACTIONABLE, precision=None, size_weight=0.02, assoc_mode="npmi", never_used_age_s=YEAR_S,
                 size_weight_by_class=None, spread="twohop", ppr_alpha=0.5, ppr_eps=1e-5, seed_specificity=False):
        if not 0.0 < d < 1.0:
            raise ValueError("the base-level decay d must lie strictly between 0 and 1 (Petrov's approximation divides by 1 - d)")
        if assoc_mode not in ("npmi", "pmi"):
            raise ValueError("assoc_mode is 'npmi' or 'pmi'")
        if spread not in ("twohop", "ppr"):
            raise ValueError("spread is 'twohop' or 'ppr'")
        if not 0.0 < ppr_alpha <= 1.0:
            raise ValueError("ppr_alpha, the restart probability, lies in (0, 1]")
        self.spread, self.ppr_alpha, self.ppr_eps, self.seed_specificity = spread, ppr_alpha, ppr_eps, seed_specificity
        self.calib = {}                              # class -> (slope, intercept, n) of a logistic fit, see calibrate()
        self.g = graph
        self.k, self.threshold, self.d, self.hop2 = k, threshold, d, hop2
        self.refractory_s, self.rise = refractory_s, rise
        self.actionable = actionable
        self.precision = precision or {}            # class -> measured precision of its fires at this threshold
        self.size_weight, self.assoc_mode = size_weight, assoc_mode
        self.size_weight_by_class = dict(size_weight_by_class or {})   # node class -> its own weight on the base level
        self.never_used_b = -d * math.log(max(float(never_used_age_s), 1.0))
        self.last_served = {}                        # node -> (time, activation) when it last reached the context

    def base_level(self, node, now):
        """ACT-R base level from the recent uses, plus Petrov's hybrid approximation for the older ones; uses after `now`
        do not count, and a node with none gets the level of one use never_used_age_s ago."""
        recent = [t for t in self.g.uses.get(node, ()) if t <= now]
        s = 0.0
        for t in recent:
            s += max(now - t, 1.0) ** (-self.d)
        n_old, first = self.g.older.get(node, (0, None))
        if n_old and first is not None and first <= now:
            newest_old = recent[0] if recent else now
            span_old = max(newest_old - first, 1.0)
            age = max(now - newest_old, 1.0)
            # the n_old older uses, spread evenly over span_old and ending `age` ago
            s += n_old * ((age + span_old) ** (1 - self.d) - age ** (1 - self.d)) / ((1 - self.d) * span_old)
        return math.log(s) if s > 0 else self.never_used_b

    def row_sum(self, j):
        """j's whole outgoing weight: the graph store's true row sum when it capped the row, else the graph's own."""
        tot = getattr(self.g, "out_sum", {}).get(j)
        if tot:
            return tot
        tot = getattr(self.g, "out_w", {}).get(j)
        if tot:
            return tot
        return sum(v[0] for v in self.g.out.get(j, {}).values())

    def assoc(self, j, i, tot=None):
        """S_ji, floored at 0: the normalised PMI of edge j -> i (or the plain PMI with assoc_mode 'pmi'), and its type."""
        out = self.g.out.get(j)
        if not out or i not in out or self.g.total_w <= 0:
            return 0.0, None
        w, etype = out[i]
        tot = tot or self.row_sum(j)
        if tot <= 0:
            return 0.0, etype
        p_i = self.g.in_w[i] / self.g.total_w
        if p_i <= 0:
            return 0.0, etype
        pmi = math.log((w / tot) / p_i)
        if pmi <= 0:
            return 0.0, etype
        if self.assoc_mode == "pmi":
            return pmi, etype
        p_ij = w / self.g.total_w
        return (pmi / -math.log(p_ij) if p_ij < 1.0 else 0.0), etype

    def _push(self, source):
        """Approximate personalised PageRank from one node by local push (Andersen, Chung and Lang 2006): restart probability
        ppr_alpha, a step from j to i with probability w_ji / row_sum(j) (the graph store's true sum, so the mass meant for a
        target outside a capped subgraph leaks instead of piling onto the kept ones), residuals pushed until each is under
        ppr_eps. A walk that reaches a node with no outgoing row restarts at the source (the usual treatment of dangling
        nodes), so a sink such as a mistake node keeps only its restart share instead of absorbing the rest of the walk.
        Returns {node: probability}; the values sum to at most 1."""
        a, eps = self.ppr_alpha, self.ppr_eps
        p = collections.defaultdict(float)
        r = collections.defaultdict(float)
        r[source] = 1.0
        queue = collections.deque([source])
        queued = {source}
        pushes = 0
        while queue and pushes < 200000:
            u = queue.popleft()
            queued.discard(u)
            ru = r[u]
            if ru < eps:
                continue
            pushes += 1
            r[u] = 0.0
            out = self.g.out.get(u)
            tot = self.row_sum(u) if out else 0.0
            p[u] += a * ru
            if not out or tot <= 0:
                r[source] += (1.0 - a) * ru                    # a dead end: the walk restarts at the source
                if r[source] >= eps and source not in queued:
                    queue.append(source)
                    queued.add(source)
                continue
            spill = (1.0 - a) * ru / tot
            for v, (w, _t) in out.items():
                r[v] += spill * w
                if r[v] >= eps and v not in queued:
                    queue.append(v)
                    queued.add(v)
        for u, ru in r.items():                            # what is left under the threshold settles where it is
            if ru > 0:
                p[u] += a * ru
        return p

    def _lift(self, i, mass):
        """How far the walk from the seed visits i above i's share of all incoming weight, as a fraction of the most it
        could: log(mass / P(i)) / -log P(i), floored at 0 - the walk's analogue of the normalised PMI, on the same 0..1
        scale."""
        if self.g.total_w <= 0 or mass <= 0:
            return 0.0
        p_i = self.g.in_w.get(i, 0.0) / self.g.total_w
        if not 0.0 < p_i < 1.0:
            return 0.0
        return max(0.0, math.log(mass / p_i)) / -math.log(p_i)

    def activation_ppr(self, seed, now):
        """Spread by personalised PageRank with restart on the seed (the random walk HippoRAG runs over its graph,
        Gutierrez et al. 2024; Haveliwala 2002): it reaches past two hops when the evidence runs that far and settles on
        cyclic structure, which a fixed two-hop sum cannot. Each seed node walks on its own (PageRank is linear in the
        restart vector), so an item's hint can still name the seed nodes that lit it. The restart weights are 1/n, or with
        seed_specificity 1/log(2 + fan) normalised, so a hub seed restarts less (HippoRAG's node specificity).
        ppr_alpha = 0.5 is HippoRAG's damping, a literature value not measured here.

        Each seed's walk is read as where it goes when it is not at its own seed - its mass over 1 - P(seed), mass that
        leaked out of a capped subgraph included - and lifted against P(i) on its own; the lifts are then weighted by the
        restart weights and summed, as the two-hop mode weights each seed's association. On a seed whose neighbours have no
        rows of their own this is the one-hop conditional P(i | seed). Lifting the MIXED walk instead (the first version,
        6e37576f) moves every log by about -log(n): with the 20-40 seeds of a real session it left only rare nodes above
        zero (claude-9209cf81, 26 Sep: tools hit@10 0.017 against 0.57 for two hops; test_membrane.py's 20-seed control)."""
        seed = [s for s in dict.fromkeys(seed) if s in self.g.cls or s in self.g.out]
        if not seed:
            return {}, {}
        seedset = set(seed)
        if self.seed_specificity:
            raw = {s: 1.0 / math.log(2.0 + len(self.g.out.get(s, {}))) for s in seed}
        else:
            raw = {s: 1.0 for s in seed}
        z = sum(raw.values())
        spread = collections.defaultdict(float)
        why = collections.defaultdict(list)
        for s in seed:
            ws = raw[s] / z
            walk = self._push(s)
            away = 1.0 - walk.get(s, 0.0)
            if away <= 0:
                continue
            for i, m in walk.items():
                if i in seedset or m <= 0:
                    continue
                lift = self._lift(i, m / away)
                if lift <= 0:
                    continue
                spread[i] += ws * lift
                why[i].append((ws * lift, s, "ppr"))
        act = {}
        for i, sp in spread.items():
            wb = self.size_weight_by_class.get(self.g.cls.get(i, i.split(":", 1)[0]), self.size_weight)
            act[i] = wb * self.base_level(i, now) + sp
        return act, why

    def calibrate(self, records, l2=1.0, iters=50):
        """Fit, per node class, P(a fire of this class was right) = 1 / (1 + exp(-(slope * A + intercept))) on held-out
        fires - ACT-R's retrieval function (Anderson and Lebiere 1998) with its threshold and noise read off the data. records
        is an iterable of (class, activation, right). Newton's method with a small ridge so a class whose fires are all
        right or all wrong does not run off to infinity. Stores self.calib[class] = (slope, intercept, n)."""
        by = collections.defaultdict(list)
        for c, a, y in records:
            by[c].append((float(a), 1.0 if y else 0.0))
        for c, rows in by.items():
            w0, w1 = 0.0, 0.0                              # intercept, slope
            for _ in range(iters):
                g0 = g1 = 0.0
                h00 = h01 = h11 = 0.0
                for a, y in rows:
                    q = 1.0 / (1.0 + math.exp(-(w1 * a + w0)))
                    g0 += q - y
                    g1 += (q - y) * a
                    s = q * (1.0 - q)
                    h00 += s
                    h01 += s * a
                    h11 += s * a * a
                g0 += l2 * w0
                g1 += l2 * w1
                h00 += l2
                h11 += l2
                det = h00 * h11 - h01 * h01
                if abs(det) < 1e-12:
                    break
                d0 = (h11 * g0 - h01 * g1) / det
                d1 = (-h01 * g0 + h00 * g1) / det
                w0 -= d0
                w1 -= d1
                if abs(d0) + abs(d1) < 1e-9:
                    break
            self.calib[c] = (w1, w0, len(rows))
        return self.calib

    def right_rate(self, cls, a):
        """The calibrated chance that a fire of this class at activation a is right, or None without a fit."""
        fit = self.calib.get(cls)
        if not fit:
            return None
        slope, icpt, _n = fit
        return 1.0 / (1.0 + math.exp(-(slope * a + icpt)))

    def activation(self, seed, now):
        """Spread from the seed: one hop, then (hop2 > 0) a damped second hop from what the first lit - or, with
        spread='ppr', personalised PageRank (activation_ppr). Returns (A, why)."""
        if self.spread == "ppr":
            return self.activation_ppr(seed, now)
        seed = [s for s in dict.fromkeys(seed) if s in self.g.cls or s in self.g.out]
        if not seed:
            return {}, {}
        seedset = set(seed)
        w_seed = 1.0 / len(seed)                                   # ACT-R: source activation shared over the sources
        spread = collections.defaultdict(float)
        why = collections.defaultdict(list)
        for j in seed:
            out = self.g.out.get(j)
            if not out:
                continue
            tot = self.row_sum(j)
            for i in out:
                s, etype = self.assoc(j, i, tot)
                if s > 0:
                    spread[i] += w_seed * s
                    why[i].append((s * w_seed, j, etype))
        if self.hop2 > 0:
            first = {i: a for i, a in spread.items() if i not in seedset}
            tot1 = sum(first.values()) or 1.0
            for j, a_j in first.items():                           # second hop, damped, weighted by first-hop share
                out = self.g.out.get(j)
                if not out:
                    continue
                tot = self.row_sum(j)
                for i in out:
                    if i in seedset:
                        continue
                    s, etype = self.assoc(j, i, tot)
                    c = self.hop2 * (a_j / tot1) * s
                    if c > 0:
                        spread[i] += c
                        why[i].append((c, j, etype))
        act = {}
        for i, sp in spread.items():
            if i in seedset or sp <= 0:
                continue
            wb = self.size_weight_by_class.get(self.g.cls.get(i, i.split(":", 1)[0]), self.size_weight)
            act[i] = wb * self.base_level(i, now) + sp
        return act, why

    def step(self, seed, now):
        """What reaches the context now: at most k actionable items above threshold, outside their refractory period."""
        act, why = self.activation(seed, now)
        cands = []
        for i, a in sorted(act.items(), key=lambda kv: -kv[1]):
            c = self.g.cls.get(i, i.split(":", 1)[0])
            if c not in self.actionable or a < self.threshold:
                continue
            last = self.last_served.get(i)
            if last and now - last[0] < self.refractory_s and a < last[1] + self.rise:
                continue                                           # refractory: served lately and not risen enough
            top = sorted(why[i], reverse=True)[:3]
            hint = "lit by " + "; ".join("%s (%s)" % (j, et) for _, j, et in top)
            q = self.right_rate(c, a)
            p = q if q is not None else self.precision.get(c)
            if q is not None:
                tail = "; fires like this, at this strength, were right %.0f%% of the time (%d judged)" % (100 * q, self.calib[c][2])
            elif p is not None:
                tail = "; fires like this were right %.0f%% of the time" % (100 * p)
            else:
                tail = "; unjudged"
            cands.append({"node": i, "class": c, "activation": round(a, 3), "hint": hint,
                          "precision": p, "hint_line": hint + tail})
            if len(cands) == self.k:
                break
        for c in cands:
            self.last_served[c["node"]] = (now, c["activation"])
        return cands
