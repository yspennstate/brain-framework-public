"""Priming on the graph path comes from a scar's own memory, never from a hub (2026-09-02).

A reflex whose source node is shared by many reflexes (the mistakes list, the self-model of failure modes)
must not be primed by a deposit on that node: one deposit would lower the bar for dozens of reflexes at
once. A reflex whose source is its own memory (fan-out one) is primed as before. Pinned here on a tiny
model with a throwaway session buffer.

Run: python test_graph_priming.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gfcore  # noqa: E402
import activation_buffer as ab  # noqa: E402

SESSION = "test-graph-priming-0902"


def _tiny_model():
    toks = {"alpha": 0.5, "beta": 0.5}
    m = {}
    for i in range(6):   # six reflexes share the hub as their source -> fan-out 6 > HUB_FANOUT
        m["hub_%d" % i] = {"src": "HUB_MEMORY", "tokens": dict(toks), "thr": 1.5, "charge": 0.6, "mode": "fire", "anchors": {}}
    m["own"] = {"src": "OWN_SCAR_MEMORY", "tokens": dict(toks), "thr": 1.5, "charge": 0.6, "mode": "fire", "anchors": {}}
    return m


class HubPriming(unittest.TestCase):
    def setUp(self):
        self._model, self._fan = gfcore._MODEL, gfcore._FANOUT
        gfcore._MODEL, gfcore._FANOUT = _tiny_model(), None
        ab.deposit(SESSION, {"HUB_MEMORY": 1.0, "OWN_SCAR_MEMORY": 1.0})

    def tearDown(self):
        gfcore._MODEL, gfcore._FANOUT = self._model, self._fan
        try:
            os.remove(ab._buf_path(SESSION))
        except Exception:
            pass

    def test_fanout_counts_sources(self):
        fan = gfcore._fanout()
        self.assertEqual(fan["HUB_MEMORY"], 6)
        self.assertEqual(fan["OWN_SCAR_MEMORY"], 1)

    def test_only_the_own_memory_primes(self):
        # score 1.0 sits under the bar of 1.5 for every reflex; only a prime can lift one over it
        fires = {f["id"]: f for f in gfcore.graph_fire("alpha beta", session=SESSION)}
        self.assertIn("own", fires)
        self.assertGreaterEqual(fires["own"]["primed"], 0.45)
        self.assertFalse(any(k.startswith("hub_") for k in fires), sorted(fires))

    def test_nothing_fires_unprimed(self):
        self.assertEqual(gfcore.graph_fire("alpha beta", session=None), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
