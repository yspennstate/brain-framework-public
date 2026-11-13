"""The owner hold must stop both entry paths before subprocess or store mutation."""
import contextlib, datetime, importlib.util, io, json, os
from pathlib import Path
from unittest import mock
import unittest

LIVE = Path(os.environ.get('BRAIN_GRAPH_BIN', os.path.dirname(os.path.abspath(__file__)))) / 'doc_expand.py'
spec=importlib.util.spec_from_file_location('held_doc_expand',LIVE)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

@unittest.skipUnless(hasattr(mod, 'OWNER_CLAUDE_HOLD_UNTIL'), 'this copy of doc_expand carries no launch hold')
class HoldTests(unittest.TestCase):
    def test_expiry_exactly_matches_owner_local_time(self):
        until=datetime.datetime.fromisoformat('2026-10-15T23:45:00+03:00').timestamp()
        self.assertEqual(mod.OWNER_CLAUDE_HOLD_UNTIL,until)
        self.assertTrue(mod.owner_claude_hold(until-0.001))
        self.assertFalse(mod.owner_claude_hold(until))
        self.assertFalse(mod.owner_claude_hold(until+1))

    def test_direct_ask_cannot_launch_claude_under_hold(self):
        with mock.patch.object(mod.time,'time',return_value=mod.OWNER_CLAUDE_HOLD_UNTIL-1), mock.patch.object(mod.subprocess,'run',side_effect=AssertionError('child launch forbidden')), mock.patch.object(mod.os,'makedirs',side_effect=AssertionError('directory write forbidden')):
            result=mod.ask([('fake','fake memory')])
        self.assertEqual(result[:2],({},None))
        self.assertIn('owner hold',result[2])

    def test_scheduled_entry_does_not_touch_output_or_launch(self):
        capture=io.StringIO()
        with mock.patch.object(mod.time,'time',return_value=mod.OWNER_CLAUDE_HOLD_UNTIL-1), mock.patch.object(mod.sys,'argv',['doc_expand.py','--new','--out','must-not-create.jsonl']), mock.patch.object(mod,'take_lock',side_effect=AssertionError('lock write forbidden')), mock.patch.object(mod,'_run',side_effect=AssertionError('work forbidden')), mock.patch.object(mod.os,'makedirs',side_effect=AssertionError('directory write forbidden')), mock.patch.object(mod.subprocess,'run',side_effect=AssertionError('child launch forbidden')), contextlib.redirect_stdout(capture):
            old=mod.OUT
            mod.main()
            self.assertEqual(mod.OUT,old)
        self.assertIn('no output changed',capture.getvalue())

if __name__=='__main__':
    unittest.main(verbosity=2)
