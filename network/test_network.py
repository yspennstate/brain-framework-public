"""End-to-end test on a synthetic corpus. The pass must be able to fail both ways:
the engine must retrieve the right prompt when its pattern recurs, and must stay
SILENT on a near-miss context (fewer than k_min specific nodes shared).
"""
import io, json, os, sys, tempfile, time, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # run from any CWD
import build_graph, episodes, grow
from netstore import NetStore
from fire import FireEngine

PATTERN = ["deploy", "canary", "rollback", "kubectl", "helmchart"]
NOISE = ["alpha", "beta", "gamma_stat", "delta_val", "epsilon"]


def synth_events(path):
    """Type-A sessions: pattern context then prompt 'fix-canary'. Type-B sessions:
    noise context then prompt 'write-report'. Separate sessions, so neither
    prompt's context can leak into the other's episode. Filler windows keep the
    corpus large and varied enough that pattern keywords sit inside the df band."""
    t = 1_700_000_000.0
    rows = []
    for s in range(40):
        sid = f"s{s}"
        if s % 2 == 0:
            for tok in PATTERN:
                rows.append({"ts": t, "session": sid, "kind": "tool", "id": "Bash",
                             "text": f"running {tok} step now"})
                t += 10
            rows.append({"ts": t, "session": sid, "kind": "prompt", "id": "fix-canary",
                         "text": "please fix the canary deploy rollback"})
            rows.append({"ts": t + 1, "session": sid, "kind": "mem", "id": "deploy-playbook",
                         "text": "deploy canary rollback kubectl helmchart playbook"})
        else:
            for tok in NOISE:
                rows.append({"ts": t, "session": sid, "kind": "tool", "id": "Read",
                             "text": f"reading {tok} notes"})
                t += 10
            rows.append({"ts": t, "session": sid, "kind": "prompt", "id": "write-report",
                         "text": "write the weekly report"})
            rows.append({"ts": t + 1, "session": sid, "kind": "mem", "id": "report-template",
                         "text": "weekly report alpha beta gamma_stat delta_val template"})
        t += 600
        for f in range(4):
            rows.append({"ts": t, "session": sid, "kind": "tool", "id": "Read",
                         "text": f"browsing topic_{s}_{f} widget_{s}_{f} theme_{f} "
                                 f"filler_{(s * 7 + f) % 11} common_thread"})
            t += 200
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return t


class TestNetwork(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.ev = os.path.join(self.dir, "events.jsonl")
        self.db = os.path.join(self.dir, "net.sqlite3")
        self.t_end = synth_events(self.ev)
        build_graph.main(["--db", self.db, "--events", self.ev])
        episodes.main(["--db", self.db, "--events", self.ev])

    def _engine(self):
        st = NetStore(self.db)
        eng = FireEngine(st, now=lambda: self.t_end + 10_000)
        return st, eng

    def test_pattern_completes_to_prompt(self):
        st, eng = self._engine()
        for tok in PATTERN:
            eng.observe([f"kw:{tok}", "tool:Bash"])
        hits = eng.complete()
        self.assertTrue(hits, "engine stayed silent on the exact stored pattern")
        self.assertEqual(hits[0][0], "prompt:fix-canary")
        st.close()

    def test_near_miss_stays_silent(self):
        st, eng = self._engine()
        # two pattern tokens + unrelated noise: below k_min specific overlap
        eng.observe(["kw:deploy", "tool:Bash"])
        eng.observe(["kw:canary"])
        eng.observe(["kw:alpha", "kw:beta"])
        hits = [h for h in eng.complete() if h[0] == "prompt:fix-canary"]
        self.assertFalse(hits, f"fired on a near-miss: {hits}")
        st.close()

    def test_habituation_cooldown(self):
        st, eng = self._engine()
        for tok in PATTERN:
            eng.observe([f"kw:{tok}", "tool:Bash"])
        first = eng.complete()
        self.assertTrue(first)
        again = eng.complete()
        self.assertFalse([h for h in again if h[0] == first[0][0]],
                         "cooldown did not suppress an immediate refire")
        st.close()

    def test_completion_returns_content(self):
        st, eng = self._engine()
        for tok in PATTERN:
            eng.observe([f"kw:{tok}", "tool:Bash"])
        hits = eng.complete_with_content()
        self.assertTrue(hits)
        pn, score, k, content = hits[0]
        self.assertEqual(pn, "prompt:fix-canary")
        self.assertIn("canary", (content or "").lower())
        st.close()

    def test_hopfield_retrieval_concentrates_and_stays_silent(self):
        # the exact stored pattern must retrieve its attractor with high softmax weight;
        # a near-miss (too few specific nodes) must not fire (concentration + k_min gate).
        st, eng = self._engine()
        for tok in PATTERN:
            eng.observe([f"kw:{tok}", "tool:Bash"])
        hits = eng.complete_hopfield()
        self.assertTrue(hits, "hopfield retrieval stayed silent on the exact pattern")
        self.assertEqual(hits[0][0], "prompt:fix-canary")
        self.assertGreaterEqual(hits[0][1], 0.30)  # concentrated weight
        st2 = NetStore(self.db)
        eng2 = FireEngine(st2, now=lambda: self.t_end + 10_000)
        eng2.observe(["kw:deploy", "tool:Bash"])
        eng2.observe(["kw:alpha", "kw:beta"])
        self.assertFalse([h for h in eng2.complete_hopfield() if h[0] == "prompt:fix-canary"],
                         "hopfield fired on a near-miss")
        st2.close()
        st.close()

    def test_recall_memories_is_topical(self):
        # seeding the PATTERN topic must surface deploy-playbook ABOVE report-template
        # (topic-level memory retrieval - the validated use, vs prompt prediction)
        st, eng = self._engine()
        hits = eng.recall_memories([f"kw:{t}" for t in PATTERN], top=5)
        self.assertTrue(hits, "recall surfaced no memories for the seeded topic")
        ranks = {nid: a for nid, a in hits}
        self.assertIn("mem:deploy-playbook", ranks)
        self.assertGreater(ranks["mem:deploy-playbook"],
                           ranks.get("mem:report-template", 0.0),
                           "off-topic memory outranked the on-topic one")
        st.close()

    def test_polysemy_gate_fires(self):
        # Direct, controlled test of the min_anchors gate, isolated from the build/spread
        # pipeline: insert two seed keywords and two memories with KNOWN co-edges - one
        # memory reachable from BOTH seeds, one (the polysemy decoy) from ONE seed only.
        # The gate at min_anchors=2 must keep the two-anchor memory and drop the decoy.
        # (The gate is also verified on the live graph, where it filters the off-topic
        # GPU-"network" memory from the association-"network" topic.)
        d = tempfile.mkdtemp()
        st = NetStore(os.path.join(d, "gate.sqlite3"))
        for nid, kind in [("kw:s1", "kw"), ("kw:s2", "kw"),
                          ("mem:both", "mem"), ("mem:decoy", "mem")]:
            st.upsert_node(nid, kind, df_inc=5)
        # co-edges (both directions): both<->s1, both<->s2, decoy<->s1 ONLY
        for a, b, w in [("kw:s1", "mem:both", 3.0), ("kw:s2", "mem:both", 3.0),
                        ("kw:s1", "mem:decoy", 5.0)]:
            for x, y in ((a, b), (b, a)):
                st.db.execute("INSERT INTO edges(src,dst,kind,w,n,updated) VALUES(?,?,?,?,1,0)",
                              (x, y, "co", w))
        st.commit()
        seed = ["kw:s1", "kw:s2"]
        loose = {n for n, _ in FireEngine(st).recall_memories(
            seed, top=10, min_activation=0.0, min_anchors=1)}
        self.assertEqual(loose, {"mem:both", "mem:decoy"}, "spread did not reach both memories")
        gated = {n for n, _ in FireEngine(st).recall_memories(
            seed, top=10, min_activation=0.0, min_anchors=2)}
        self.assertNotIn("mem:decoy", gated, "polysemy gate did not filter the single-anchor decoy")
        self.assertIn("mem:both", gated, "gate wrongly filtered the two-anchor memory")
        st.close()

    def test_local_ppr_is_topical(self):
        import ppr_local
        st = NetStore(self.db)
        ppr = ppr_local.local_ppr(st, [f"kw:{t}" for t in PATTERN], alpha=0.15, eps=1e-4)
        mems = dict(ppr_local.rank_memories(ppr, top=5))
        self.assertIn("mem:deploy-playbook", mems)
        self.assertGreater(mems["mem:deploy-playbook"],
                           mems.get("mem:report-template", 0.0),
                           "PPR: off-topic memory outranked the on-topic one")
        # ACL invariant: PPR mass is bounded and positive
        self.assertGreater(sum(ppr.values()), 0.0)
        self.assertLessEqual(sum(ppr.values()), 1.0 + 1e-6)
        st.close()

    def test_grow_is_idempotent_and_additive(self):
        st = NetStore(self.db)
        before = st.db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        st.close()
        # re-folding the SAME events adds nothing (water mark holds)
        grow.main(["--db", self.db, "--events", self.ev])
        st = NetStore(self.db)
        same = st.db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        st.close()
        self.assertEqual(before, same, "re-growing identical events added nodes")
        # genuinely new vocabulary in TWO separate sessions (>= DF_MIN windows) adds nodes
        extra = os.path.join(self.dir, "extra.jsonl")
        with io.open(extra, "w", encoding="utf-8", newline="\n") as fh:
            t = self.t_end + 10_000
            for s in range(4):   # >= DF_MIN (3) windows so the new keywords clear the band
                for tok in ("telemetry", "quasar", "isotope", "meridian", "flux"):
                    fh.write(json.dumps({"ts": t, "session": f"snew{s}", "kind": "tool",
                                         "id": "Read", "text": f"reading {tok} notes"}) + "\n")
                    t += 10
                t += 5_000
        grow.main(["--db", self.db, "--events", self.ev, extra])
        st = NetStore(self.db)
        after = st.db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        st.close()
        self.assertGreater(after, same, "grow did not add a node for new vocabulary")


if __name__ == "__main__":
    unittest.main(verbosity=2)
