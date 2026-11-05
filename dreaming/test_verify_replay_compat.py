"""Exercise replay accounting against hooks on authored temporary JSONL input.

No main(), _mods(), directory discovery, or real session reads are permitted.
Supply inspected modules with --ops-source and --replay-source. The imported
replay's home-based defaults are never used.
"""
import argparse
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
OPS_SOURCE = REPLAY_SOURCE = None
RISK = 'Dividing both sides by $x-1$ gives $y=2$.'
JUST = 'Since $z$ is nonzero, its reciprocal exists.'
OLD = 'For every $n$ there exists $m$ with $m > n$.'
NEW = 'There exists $m$ such that for every $n$ we have $m > n$.'
EXCHANGE = 'We interchange the limit and the integral.'
FINITE = r'We checked for $n \le 1000$ that the bound holds for all $n$.'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReplayCompatibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Under pytest nobody passes the two sources, and this test never picks them itself.
        if OPS_SOURCE is None or REPLAY_SOURCE is None:
            raise unittest.SkipTest('run as a script with --ops-source and --replay-source')

    def run_fixture(self, tool, inp):
        result, _actual, _summary = self.run_calls([(tool, inp)])
        return result[0], result[1], result[3]

    def run_calls(self, calls):
        ops = load(OPS_SOURCE, 'ops_synthetic_replay')
        replay = load(REPLAY_SOURCE, 'replay_synthetic_test')
        def forbidden(*args, **kwargs):
            raise AssertionError('No replay discovery/main/general scan may run')
        replay.main = replay._mods = replay.scan = forbidden
        with tempfile.TemporaryDirectory(prefix='verify_replay_synthetic_') as tmp:
            path = Path(tmp) / 'synthetic.jsonl'
            records = [{'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': tool, 'input': inp}]}}
                       for tool, inp in calls]
            path.write_text(''.join(json.dumps(record, separators=(',', ':')) + '\n' for record in records))
            ops.STATE = str(Path(tmp) / 'state.json')
            ops.LOG = str(Path(tmp) / 'log.jsonl')
            with patch.dict(sys.modules, {'verify_ops': ops}), patch.object(sys, 'path', list(sys.path)):
                result = replay.scan_ops(str(path))
            self.assertFalse(Path(ops.STATE).exists())
            self.assertFalse(Path(ops.LOG).exists())
            # The original three-tuple used global session dedup in main after
            # scan_ops had already capped each call. Reproduce that accounting
            # for before/after acceptance comparisons without calling main.
            if len(result) == 3:
                result = (*result, list(dict.fromkeys(result[2])))
            if hasattr(replay, 'ops_summary'):
                summary = replay.ops_summary([result])
            else:
                summary = {'math_file_writes': result[1], 'fires_raw': {}, 'cards_after_dedup': {}}
                for field, entries in [('fires_raw', result[2]), ('cards_after_dedup', result[3])]:
                    for op, _ in entries:
                        summary[field][op] = summary[field].get(op, 0) + 1
            actual = []
            for tool, inp in calls:
                out = ops.decide({'tool_name': tool, 'tool_input': inp, 'session_id': 'synthetic-replay-agreement'})
                actual += [(op, inp['file_path']) for op in re.findall(r'\[verify-op ([a-z]+)\]', out)]
            return result, actual, summary

    def test_write_count_and_operation(self):
        self.assertEqual(self.run_fixture('Write', {'file_path': 'proof.tex', 'content': RISK}),
                         ('synthetic', 1, [('divide', 'proof.tex')]))

    def test_edit_swap_count(self):
        self.assertEqual(self.run_fixture('Edit', {'file_path': 'proof.tex', 'old_string': OLD, 'new_string': NEW}),
                         ('synthetic', 1, [('witness', 'proof.tex')]))

    def test_multiedit_count_remains_one_without_cross_justification(self):
        self.assertEqual(self.run_fixture('MultiEdit', {'file_path': 'proof.tex', 'edits': [
            {'new_string': RISK}, {'new_string': JUST}]}), ('synthetic', 1, [('divide', 'proof.tex')]))

    def test_multiedit_no_cross_pair_witness(self):
        self.assertEqual(self.run_fixture('MultiEdit', {'file_path': 'proof.tex', 'edits': [
            {'old_string': OLD, 'new_string': 'Let $a=1$.'},
            {'old_string': 'Let $b=1$.', 'new_string': NEW}]}), ('synthetic', 1, []))

    def test_multiedit_empty_still_counted_once(self):
        self.assertEqual(self.run_fixture('MultiEdit', {'file_path': 'proof.tex', 'edits': []}), ('synthetic', 1, []))

    def test_multiedit_duplicate_operation_counted_once(self):
        self.assertEqual(self.run_fixture('MultiEdit', {'file_path': 'proof.tex', 'edits': [
            {'new_string': RISK}, {'new_string': RISK}]}), ('synthetic', 1, [('divide', 'proof.tex')]))

    def test_multiedit_retains_two_card_limit(self):
        self.assertEqual(self.run_fixture('MultiEdit', {'file_path': 'proof.tex', 'edits': [
            {'new_string': RISK}, {'new_string': 'We interchange the limit and the integral.'},
            {'new_string': r'We checked for $n \le 1000$ that the bound holds for all $n$.'}]}),
            ('synthetic', 1, [('divide', 'proof.tex'), ('exchange', 'proof.tex')]))

    def test_quiet_write_still_counted_once(self):
        self.assertEqual(self.run_fixture('Write', {'file_path': 'proof.tex', 'content': 'Let $x=1$.'}), ('synthetic', 1, []))

    def test_reverse_call_order_does_not_invent_unemitted_third_card(self):
        calls = [('MultiEdit', {'file_path': 'proof.tex', 'edits': [
            {'new_string': text} for text in [RISK, EXCHANGE, FINITE]]}),
            ('Write', {'file_path': 'proof.tex', 'content': RISK + '\n' + EXCHANGE})]
        result, actual, summary = self.run_calls(calls)
        expected = [('divide', 'proof.tex'), ('exchange', 'proof.tex')]
        self.assertEqual(result[3], expected)
        self.assertEqual(actual, expected)
        self.assertEqual(summary['cards_after_dedup'], {'divide': 1, 'exchange': 1})
        self.assertEqual(summary['fires_raw'], {'divide': 2, 'exchange': 2, 'finite': 1})

    def test_omitted_third_card_stays_eligible_for_later_write(self):
        calls = [('MultiEdit', {'file_path': 'proof.tex', 'edits': [
            {'new_string': text} for text in [RISK, EXCHANGE, FINITE]]}),
            ('Write', {'file_path': 'proof.tex', 'content': FINITE})]
        result, actual, summary = self.run_calls(calls)
        expected = [('divide', 'proof.tex'), ('exchange', 'proof.tex'), ('finite', 'proof.tex')]
        self.assertEqual(result[3], expected)
        self.assertEqual(actual, expected)
        self.assertEqual(summary['cards_after_dedup'], {'divide': 1, 'exchange': 1, 'finite': 1})
        self.assertEqual(summary['fires_raw'], {'divide': 1, 'exchange': 1, 'finite': 2})


for reverse in [False, True]:
    def case(self, reverse=reverse):
        texts = [RISK, EXCHANGE, FINITE]
        if reverse:
            texts.reverse()
        calls = [('Write', {'file_path': 'proof.tex', 'content': RISK + '\n' + EXCHANGE}),
                 ('MultiEdit', {'file_path': 'proof.tex', 'edits': [{'new_string': text} for text in texts]})]
        result, actual, summary = self.run_calls(calls)
        expected = [('divide', 'proof.tex'), ('exchange', 'proof.tex'), ('finite', 'proof.tex')]
        self.assertEqual(result[3], expected)
        self.assertEqual(actual, expected)
        self.assertEqual(summary['math_file_writes'], 2)
        self.assertEqual(summary['cards_after_dedup'], {'divide': 1, 'exchange': 1, 'finite': 1})
        self.assertEqual(summary['fires_raw'], {'divide': 2, 'exchange': 2, 'finite': 1})
    setattr(ReplayCompatibility, f'test_cooldown_precedes_cap_{"reverse" if reverse else "forward"}', case)


def main():
    global OPS_SOURCE, REPLAY_SOURCE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ops-source', type=Path, required=True)
    parser.add_argument('--replay-source', type=Path, required=True)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    OPS_SOURCE, REPLAY_SOURCE = args.ops_source.resolve(), args.replay_source.resolve()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ReplayCompatibility))
    if args.json:
        args.json.write_text(json.dumps({'tests': result.testsRun, 'failures': len(result.failures),
            'errors': len(result.errors), 'passed': result.testsRun - len(result.failures) - len(result.errors),
            'failed_tests': [test.id().split('.')[-1] for test, _ in result.failures + result.errors]}, indent=2) + '\n')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
