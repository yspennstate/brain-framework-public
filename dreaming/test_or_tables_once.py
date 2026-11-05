#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import brain
import spread_recall as sr

NAMES = (("_archived_index_lines", brain), ("_tiers", brain), ("_corpus_slugs", brain), ("_load_salience", sr))


class OrTablesOnceTests(unittest.TestCase):
    def setUp(self):
        self.real = {n: getattr(m, n) for n, m in NAMES}
        self.calls = {n: 0 for n, _ in NAMES}

        def fake(name, value):
            def f():
                self.calls[name] += 1
                return value() if callable(value) else value
            return f
        brain._archived_index_lines = fake("_archived_index_lines", lambda: {"a": "- [a.md](a.md) - x"})
        brain._tiers = fake("_tiers", lambda: {"a": "conscious"})
        brain._corpus_slugs = fake("_corpus_slugs", lambda: ["a", "b"])
        sr._load_salience = fake("_load_salience", lambda: {"a": 1.0})
        self.fakes = {n: getattr(m, n) for n, m in NAMES}

    def tearDown(self):
        for n, m in NAMES:
            setattr(m, n, self.real[n])

    def test_each_table_is_read_once_and_every_caller_gets_its_own_copy(self):
        with brain._or_tables_once():
            for _ in range(3):
                idx = brain._archived_index_lines()
                idx["b"] = "added by a wording"          # _recall_body updates this dict in place
                brain._tiers()["b"] = "x"
                brain._corpus_slugs().append("c")
                sr._load_salience()["b"] = 2.0
            self.assertEqual(brain._archived_index_lines(), {"a": "- [a.md](a.md) - x"})
            self.assertEqual(brain._tiers(), {"a": "conscious"})
            self.assertEqual(brain._corpus_slugs(), ["a", "b"])
            self.assertEqual(sr._load_salience(), {"a": 1.0})
        self.assertEqual(self.calls, {n: 1 for n, _ in NAMES})

    def test_the_loaders_come_back_after_the_scope_even_on_error(self):
        with self.assertRaises(RuntimeError):
            with brain._or_tables_once():
                raise RuntimeError("wording failed")
        for n, m in NAMES:
            self.assertIs(getattr(m, n), self.fakes[n])
        brain._tiers()
        brain._tiers()
        self.assertEqual(self.calls["_tiers"], 2)    # outside the scope nothing is cached


if __name__ == "__main__":
    unittest.main()
