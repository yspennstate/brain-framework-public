"""prompt_archive keeps each record on one physical line, and apply_prompt_classes can call the writer it uses.

json.dumps(ensure_ascii=False) leaves U+2028, U+2029 and U+0085 raw and str.splitlines() breaks on each, so a pasted
review with display math once came back as 58 unparseable fragments. The Mac's copy lacked dumps_line while its
apply_prompt_classes.py called it on every row, so the rewrite raised AttributeError before writing anything.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import prompt_archive as pa  # noqa: E402


class PromptArchiveLineBreakTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = (pa.ARCHIVE_DIR, pa.LEDGER)
        pa.ARCHIVE_DIR = self.tmp
        pa.LEDGER = os.path.join(self.tmp, "PROMPTS.jsonl")

    def tearDown(self):
        pa.ARCHIVE_DIR, pa.LEDGER = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_separators_stay_on_one_line(self):
        rec = {"text": "a b c\x85d", "class_model": "work"}
        pa.append_line(rec)
        pa.append_line({"text": "second"})
        raw = open(pa.LEDGER, encoding="utf-8").read()
        lines = raw.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0]), rec)

    def test_apply_prompt_classes_finds_its_writer(self):
        import apply_prompt_classes  # noqa: F401
        self.assertTrue(callable(getattr(pa, "dumps_line", None)))

    def test_headless_claude_is_automation(self):
        saved = os.environ.get("CLAUDE_CODE_ENTRYPOINT")
        try:
            os.environ["CLAUDE_CODE_ENTRYPOINT"] = "sdk-cli"
            self.assertTrue(pa._headless_session({}, "claude"))
            os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"
            self.assertFalse(pa._headless_session({}, "claude"))
        finally:
            if saved is None:
                os.environ.pop("CLAUDE_CODE_ENTRYPOINT", None)
            else:
                os.environ["CLAUDE_CODE_ENTRYPOINT"] = saved


if __name__ == "__main__":
    unittest.main()
