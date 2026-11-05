"""End-to-end test on a synthetic corpus. The pass must be able to fail both ways:
the engine must retrieve the right prompt when its pattern recurs, and must stay
SILENT on a near-miss context (fewer than k_min specific nodes shared).
"""
import io, json, os, tempfile, time, unittest

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
        else:
            for tok in NOISE:
                rows.append({"ts": t, "session": sid, "kind": "tool", "id": "Read",
                             "text": f"reading {tok} notes"})
                t += 10
            rows.append({"ts": t, "session": sid, "kind": "prompt", "id": "write-report",
                         "text": "write the weekly report"})
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
