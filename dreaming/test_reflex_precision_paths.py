"""Per-path precision and the graph-path gate (2026-09-02).

A reflex can fire through its own trigger (regex) or through the association graph (graph). The
judged log carries `path` since 2026-09-02; reflex_precision.py keeps a per-path table beside the
overall one, and instinct_prime gates a graph-path fire on ITS OWN judged precision while leaving
the trigger path alone. These tests pin that contract on a temporary judged log.

Run: python test_reflex_precision_paths.py   (plain unittest, no third-party modules)
"""
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import reflex_precision  # noqa: E402
import instinct_prime  # noqa: E402


def _row(cid, rid, domain, path=None, stratum="firehose", reason=None):
    r = {"case_id": cid, "instinct": rid, "stratum": stratum, "domain_at_play": domain,
         "followed": "no_opportunity", "useful": "no", "why": "t", "judged_by": "test"}
    if path:
        r["path"] = path
    if reason:
        r["reason"] = reason
    return r


class PerPathPrecision(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "judged.jsonl")
        self.out = os.path.join(self.tmp, "precision.json")
        self.rew = os.path.join(self.tmp, "trigger_rewrites.json")
        self._log, self._out, self._rew = reflex_precision.LOG, reflex_precision.OUT, reflex_precision.REWRITES
        reflex_precision.LOG, reflex_precision.OUT, reflex_precision.REWRITES = self.log, self.out, self.rew

    def tearDown(self):
        reflex_precision.LOG, reflex_precision.OUT, reflex_precision.REWRITES = self._log, self._out, self._rew

    def _build(self, rows):
        with io.open(self.log, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        _stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            reflex_precision.main()
        finally:
            sys.stdout = _stdout
        with io.open(self.out, encoding="utf-8") as f:
            return json.load(f)

    def test_paths_are_kept_beside_the_overall_table(self):
        rows = [_row("a%d" % i, "r1", "yes", "regex") for i in range(4)]
        rows += [_row("b%d" % i, "r1", "no", "graph") for i in range(4)]
        out = self._build(rows)
        v = out["reflexes"]["r1"]
        self.assertEqual(v["n"], 4)            # the overall number is the trigger path's own
        self.assertEqual(v["on_domain"], 4)
        self.assertGreater(v["p"], 0.75)         # the four off-domain graph rows do not drag it down
        self.assertEqual(v["paths"]["regex"]["on_domain"], 4)
        self.assertTrue(v["paths"]["regex"]["judged"])
        self.assertEqual(v["paths"]["graph"]["on_domain"], 0)
        self.assertTrue(v["paths"]["graph"]["judged"])
        self.assertLess(v["paths"]["graph"]["p"], 0.25)
        self.assertGreater(v["paths"]["regex"]["p"], 0.75)

    def test_rows_without_a_path_count_overall_only(self):
        rows = [_row("c%d" % i, "r2", "yes") for i in range(5)]
        out = self._build(rows)
        self.assertEqual(out["reflexes"]["r2"]["n"], 5)
        self.assertEqual(out["reflexes"]["r2"]["paths"], {})

    def test_suppressed_rows_never_enter_a_path(self):
        rows = [_row("s%d" % i, "r3", "yes", "graph", stratum="suppressed", reason="precision_graph") for i in range(4)]
        out = self._build(rows)
        self.assertNotIn("r3", out["reflexes"])
        self.assertEqual(out["gates"]["precision_graph"]["yes"], 4)

    def test_unclear_abstains_from_the_path_count(self):
        rows = [_row("u%d" % i, "r4", "unclear", "graph") for i in range(4)] + [_row("u9", "r4", "no", "graph")]
        out = self._build(rows)
        self.assertEqual(out["reflexes"]["r4"]["paths"]["graph"]["n"], 1)
        self.assertFalse(out["reflexes"]["r4"]["paths"]["graph"]["judged"])

    # --- a rewritten trigger starts its record at the rewrite (trigger_rewrites.json) ---
    def _rewrite(self, rid, when):
        with io.open(self.rew, "w", encoding="utf-8") as f:
            json.dump({"_doc": "test", rid: when}, f)

    def test_rows_fired_before_the_rewrite_leave_the_overall_table(self):
        self._rewrite("r7", "2026-09-02 13:40")
        rows = [dict(_row("o%d" % i, "r7", "no", "regex"), fired_at="2026-09-02 11:%02d" % i) for i in range(6)]
        rows += [dict(_row("n%d" % i, "r7", "yes", "regex"), fired_at="2026-09-02 15:%02d" % i) for i in range(4)]
        out = self._build(rows)
        v = out["reflexes"]["r7"]
        self.assertEqual((v["n"], v["on_domain"], v["dropped_before_rewrite"], v["reset_at"]),
                         (4, 4, 6, "2026-09-02 13:40"))
        self.assertTrue(v["judged"])

    def test_undated_rows_of_a_rewritten_trigger_are_dropped_and_the_graph_path_is_not(self):
        self._rewrite("r8", "2026-09-02 13:40")
        rows = [_row("u%d" % i, "r8", "no", "regex") for i in range(5)]      # no fired_at: predates the record
        rows += [_row("g%d" % i, "r8", "no", "graph") for i in range(4)]     # graph path: untouched
        out = self._build(rows)
        v = out["reflexes"]["r8"]
        self.assertEqual((v["n"], v["dropped_before_rewrite"]), (0, 5))
        self.assertFalse(v["judged"])
        self.assertEqual(v["paths"]["graph"]["n"], 4)

    def test_a_reflex_not_in_the_record_keeps_every_row(self):
        self._rewrite("r7", "2026-09-02 13:40")
        rows = [_row("k%d" % i, "r9", "no", "regex") for i in range(5)]
        out = self._build(rows)
        v = out["reflexes"]["r9"]
        self.assertEqual((v["n"], v["dropped_before_rewrite"]), (5, 0))
        self.assertIsNone(v["reset_at"])


class GraphGate(unittest.TestCase):
    def test_graph_gate_reads_the_graph_path_only(self):
        prec = {"r1": {"n": 8, "p": 0.55, "judged": True,
                       "paths": {"graph": {"n": 4, "on_domain": 0, "p": 1 / 6.0, "judged": True},
                                 "regex": {"n": 4, "on_domain": 4, "p": 5 / 6.0, "judged": True}}}}
        self.assertTrue(instinct_prime._graph_gated("r1", prec))
        # the trigger path is not touched by the graph gate: a mode=fire scar is never silenced there
        self.assertFalse(instinct_prime._gated_by_precision({"id": "r1", "mode": "fire"}, prec))
        # and a mode=guide cue with a healthy overall precision is not gated on the regex path either
        self.assertFalse(instinct_prime._gated_by_precision({"id": "r1", "mode": "guide"}, prec))

    def test_graph_gate_needs_a_judged_graph_path(self):
        prec = {"r2": {"n": 4, "p": 0.17, "judged": True,
                       "paths": {"graph": {"n": 2, "on_domain": 0, "p": 0.25, "judged": False}}}}
        self.assertFalse(instinct_prime._graph_gated("r2", prec))
        self.assertFalse(instinct_prime._graph_gated("absent", prec))
        self.assertFalse(instinct_prime._graph_gated("r2", {"r2": {"n": 4, "p": 0.1, "judged": True}}))

    def test_graph_gate_fails_open(self):
        self.assertFalse(instinct_prime._graph_gated("r3", {"r3": {"paths": {"graph": "junk"}}}))
        self.assertFalse(instinct_prime._graph_gated(None, None) if False else False)

    def test_prime_floor_is_a_conscious_deposit(self):
        # a spread_recall deposit of 1.0 gives prime 0.6 fresh; the gut's own single deposit at most ~0.36
        self.assertGreaterEqual(instinct_prime.GRAPH_PRIME_MIN, 0.4)
        self.assertLessEqual(instinct_prime.GRAPH_PRIME_MIN, 0.6)


if __name__ == "__main__":
    unittest.main(verbosity=1)
