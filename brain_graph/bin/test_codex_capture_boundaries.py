"""Exercise the real incremental Codex capture at file-write boundaries.

The reference is a single uninterrupted run over complete bytes. All fixtures and
results stay in the supplied run directory; no brain or test evidence is deleted.
These tests run no agent and execute none of the commands inside the fixtures.
"""
import argparse
import collections
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

SID = '01a0beef-aaaa-7000-9000-0000abcdef12'
STAMP = '2026-10-02T21:10:00Z'


def line(kind, payload):
    return (json.dumps({'timestamp': STAMP, 'type': kind, 'payload': payload},
                       ensure_ascii=False) + '\n').encode('utf-8')


META = line('session_meta', {'id': SID, 'cwd': 'C:/Users/user',
                             'source': 'vscode', 'originator': 'codex-tui'})


def command(name='alpha'):
    return line('event_msg', {'type': 'item_completed', 'item': {
        'type': 'CommandExecution', 'id': name,
        'command': 'python C:/Users/user/clean_fixture/' + name + '.py',
        'cwd': 'C:/Users/user', 'status': 'completed', 'exit_code': 0,
    }})


def load_capture(path, tag):
    sys.path.insert(0, str(Path(path).parent))
    spec = importlib.util.spec_from_file_location(tag, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Boundaries(unittest.TestCase):
    impl = None
    run_dir = None

    def setUp(self):
        self.case = self.run_dir / self._testMethodName
        self.case.mkdir(parents=True, exist_ok=False)

    def setup_capture(self, label, raw):
        d = self.case / label
        day = d / 'root/2026/10/02'
        day.mkdir(parents=True)
        p = day / 'rollout.jsonl'
        p.write_bytes(raw)
        return d, p

    def capture(self, d):
        args = ['capture', '--days', '4000', '--out', str(d / 'events.jsonl'),
                '--state', str(d / 'state.json')]
        with patch.object(self.impl, 'ROOT', str(d / 'root')), \
                patch.object(self.impl.ct, 'polite', lambda: None), \
                patch.object(sys, 'argv', args), contextlib.redirect_stdout(io.StringIO()):
            self.impl.main()

    def events(self, d):
        p = d / 'events.jsonl'
        return collections.Counter(p.read_bytes().splitlines()) if p.exists() else collections.Counter()

    def cursor(self, d, p):
        st = json.loads((d / 'state.json').read_text(encoding='utf-8'))
        return st.get(str(p), {}).get('offset', 0)

    def reference(self, raw):
        d, _ = self.setup_capture('reference', raw)
        self.capture(d)
        got = self.events(d)
        self.assertGreater(sum(got.values()), 0, 'reference must actually reach the parser')
        return got

    def complete_later(self, initial, rest, expected):
        want = self.reference(expected)
        d, p = self.setup_capture('incremental', initial)
        self.capture(d)
        with p.open('ab') as stream:
            stream.write(rest)
        self.capture(d)
        self.assertEqual(want, self.events(d))
        self.assertEqual(len(expected), self.cursor(d, p))

    def test_partial_json_completed_next_run(self):
        tail = command('beta')
        self.complete_later(META + command() + tail[:71], tail[71:], META + command() + tail)

    def test_valid_json_without_newline_is_deferred(self):
        raw = META + command()
        d, p = self.setup_capture('incremental', raw[:-1])
        self.capture(d)
        self.assertEqual(len(META), self.cursor(d, p))
        self.assertFalse(self.events(d))
        with p.open('ab') as stream:
            stream.write(b'\n')
        self.capture(d)
        self.assertEqual(self.reference(raw), self.events(d))

    def test_partial_session_header_is_retried(self):
        self.complete_later(META[:60], META[60:] + command(), META + command())

    def test_utf8_character_split_is_retried(self):
        tail = command('caf\u00e9')
        split = tail.index('\u00e9'.encode('utf-8')) + 1
        self.complete_later(META + tail[:split], tail[split:], META + tail)

    def test_unchanged_partial_tail_keeps_cursor(self):
        d, p = self.setup_capture('incremental', META + command()[:70])
        self.capture(d)
        self.capture(d)
        self.assertEqual(len(META), self.cursor(d, p))
        self.assertFalse(self.events(d))

    def test_append_during_scan_is_captured_once(self):
        original, addition = META + command(), command('beta')
        want = self.reference(original + addition)
        d, p = self.setup_capture('incremental', original)
        scan = self.impl.scan
        added = False

        def append_then_scan(path, *args, **kwargs):
            nonlocal added
            if Path(path) == p and not added:
                with p.open('ab') as stream:
                    stream.write(addition)
                added = True
            return scan(path, *args, **kwargs)

        with patch.object(self.impl, 'scan', append_then_scan):
            self.capture(d)
        self.assertEqual(len(original), self.cursor(d, p))
        self.capture(d)
        self.assertEqual(want, self.events(d))

    def test_completed_records_unchanged_by_repeat(self):
        raw = META + command() + command('beta')
        want = self.reference(raw)
        d, p = self.setup_capture('incremental', raw)
        self.capture(d)
        self.capture(d)
        self.assertEqual(want, self.events(d))
        self.assertEqual(len(raw), self.cursor(d, p))

    def test_scan_failure_does_not_commit_events_or_cursor(self):
        raw = META + command()
        want = self.reference(raw)
        d, p = self.setup_capture('incremental', raw)
        scan = self.impl.scan

        def fail_after_scan(*args, **kwargs):
            scan(*args, **kwargs)
            raise RuntimeError('injected scan failure')

        with patch.object(self.impl, 'scan', fail_after_scan):
            self.capture(d)
        self.assertEqual(0, self.cursor(d, p))
        self.assertFalse(self.events(d))
        self.capture(d)
        self.assertEqual(want, self.events(d))

    def test_empty_file_remains_retryable(self):
        d, p = self.setup_capture('incremental', b'')
        self.capture(d)
        p.write_bytes(META + command())
        self.capture(d)
        self.assertEqual(self.reference(META + command()), self.events(d))

    def test_truncated_file_resets_header_and_cursor(self):
        raw = META + command('long_name_before_rotation')
        d, p = self.setup_capture('incremental', raw)
        self.capture(d)
        before = self.events(d)
        after = META + command('z')
        p.write_bytes(after)
        self.capture(d)
        self.assertEqual(before + self.reference(after), self.events(d))
        self.assertEqual(len(after), self.cursor(d, p))


class Receipts(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = []

    def startTest(self, test):
        self.started = time.monotonic()
        super().startTest(test)

    def record(self, test, verdict, detail=''):
        self.rows.append({'case': test.id(), 'verdict': verdict, 'detail': detail,
                          'elapsed_seconds': time.monotonic() - self.started})

    def addSuccess(self, test):
        self.record(test, 'PASS')
        super().addSuccess(test)

    def addFailure(self, test, err):
        self.record(test, 'FAIL', self._exc_info_to_string(err, test))
        super().addFailure(test, err)

    def addError(self, test, err):
        self.record(test, 'ERROR', self._exc_info_to_string(err, test))
        super().addError(test, err)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--implementation', type=Path, required=True)
    ap.add_argument('--run-dir', type=Path, required=True)
    args = ap.parse_args()
    args.run_dir.mkdir(parents=True, exist_ok=False)
    Boundaries.impl = load_capture(args.implementation, 'capture_under_test')
    Boundaries.run_dir = args.run_dir
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Boundaries)
    result = unittest.TextTestRunner(verbosity=2, resultclass=Receipts).run(suite)
    receipt = {'as_of': datetime.now(timezone.utc).isoformat(),
               'implementation_sha256': hashlib.sha256(args.implementation.read_bytes()).hexdigest(),
               'tests_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'tests_run': result.testsRun, 'passed': result.wasSuccessful(),
               'cases': result.rows,
               'scope': 'Incremental capture mechanics; not graph relevance or downstream reasoning.'}
    (args.run_dir / 'RESULTS.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
