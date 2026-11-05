"""Controls for codex_verdicts.py (28 Sep, claude-825b1305), on a temporary ledger, prompt history and rollout folder:

  1. a correction typed by him in a Codex session becomes a correction row keyed like the capture keys that session
  2. the same words in a Claude row (source "hook") are left to the Claude builder
  3. a Codex row of origin "automation" (a script's prompt) gives nothing
  4. a prompt of 4,000 characters or more gives nothing, as in X.his_verdicts
  5. the time is the ledger's local wall clock turned into an epoch (mktime), not read as UTC
  6. the Claude patterns decide the kind: a neutral ask gives no row
  7. --since keeps later rows only
  8. two sessions whose ids share their first 8 hex (started within the same minute) keep two keys
  9. a 12-character prefix two full ids share is settled by the prompt's text, and left unkeyed ("unconfirmed") when the
     text matches neither - never given to the first match
 10. a prefix no history row or rollout knows is left unkeyed ("unknown"), never keyed by its first 8 hex
 11. the key equals the one extract_codex_events.scan gives the same session's touches
 12. a session missing from the prompt history is found by its prompt's text inside its rollout
 13. a prefix shared by a history-backed id and a rollout-only id: each prompt goes to the id whose source holds its
     text, and a prompt in neither is left unkeyed
 14. a prefix with only one known id is not proof: a prompt whose text is under no id stays unkeyed ("unconfirmed")
Controls 1, 8, 10 and 11 fail on the file of 28 Sep 03:3x, which keyed a row by the first 8 hex of the ledger's prefix
(scheduled audit account02/B15, B15-R25-VERDICT-KEY-COLLAPSE-20260928); controls 12, 13 and 14 fail on the file of
04:3x, which took a prefix's only known id without its text (B15-R25-TEXT-RESOLVER-FALSE-UNIQUE-20260928).

    python test_codex_verdicts.py [PATH_TO_codex_verdicts.py]
"""
import calendar
import collections
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.abspath(sys.argv.pop(1)) if len(sys.argv) > 1 and sys.argv[1].endswith(".py") else \
    os.path.join(HERE, "codex_verdicts.py")
BIN = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "bin")
WRONG = "no, that is wrong, you did it again, fix the slide"

FULL_A = "01a0be54-1070-7abc-8def-0123456789ab"      # ledger prefix 01a0be54-107
FULL_B1 = "01a03333-1110-7aaa-8bbb-00000000b111"     # 01a03333-111
FULL_B2 = "01a03333-2220-7aaa-8bbb-00000000b222"     # 01a03333-222: same first 8 hex as B1
FULL_C1 = "01a04444-5550-7aaa-8bbb-00000000c111"     # C1 and C2 share all 12 characters of the ledger's prefix
FULL_C2 = "01a04444-5551-7aaa-8bbb-00000000c222"
FULL_D = "01a05555-6660-7aaa-8bbb-00000000d111"      # only in a rollout, which holds its prompt
FULL_E = "01a02222-1070-7aaa-8bbb-00000000e111"      # for --since
FULL_H = "01a07777-8880-7aaa-8bbb-00000000f111"      # H (in the history) and R (rollout only) share 01a07777-888
FULL_R = "01a07777-8881-7aaa-8bbb-00000000f222"
D_TEXT = "no, that is wrong, the voice cracks again"
H_TEXT = "no, that is wrong, the title is misspelled"
R_TEXT = "no, that is wrong, the picture repeats"


def user_line(text):
    return json.dumps({"timestamp": "2026-09-28T01:00:00.000Z", "type": "response_item",
                       "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}})


def key(full):
    return "x" + full.replace("-", "")[-7:]


def load():
    sys.path.insert(0, os.path.dirname(TARGET))
    sys.path.insert(0, BIN)
    spec = importlib.util.spec_from_file_location("cv_under_test", TARGET)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class CodexVerdicts(unittest.TestCase):
    def setUp(self):
        self.cv = load()
        self.tmp = tempfile.mkdtemp()
        self.cv.LEDGER = os.path.join(self.tmp, "PROMPTS.jsonl")
        self.cv.OUT = os.path.join(self.tmp, "events_verdicts_codex.jsonl")
        self.cv.HISTORY = os.path.join(self.tmp, "history.jsonl")
        self.cv.ROLLOUTS = os.path.join(self.tmp, "sessions")
        os.makedirs(os.path.join(self.cv.ROLLOUTS, "2026", "09", "28"))
        hist = [(FULL_A, WRONG), (FULL_B1, WRONG), (FULL_B2, WRONG), (FULL_E, WRONG), (FULL_H, H_TEXT),
                (FULL_C1, "no, that is wrong, the map is upside down"), (FULL_C2, "no, that is wrong, the caption is late")]
        with io.open(self.cv.HISTORY, "w", encoding="utf-8") as fh:
            for sid, text in hist:
                fh.write(json.dumps({"session_id": sid, "ts": 1790000000, "text": text}) + "\n")
        for full, text in ((FULL_D, D_TEXT), (FULL_R, R_TEXT)):
            with io.open(os.path.join(self.cv.ROLLOUTS, "2026", "09", "28", "rollout-2026-09-28T01-00-00-%s.jsonl" % full),
                         "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "session_meta", "payload": {"id": full}}) + "\n" + user_line(text) + "\n")

    def ledger(self, rows):
        with io.open(self.cv.LEDGER, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def run_main(self, *args):
        old = sys.argv
        sys.argv = ["codex_verdicts.py"] + list(args)
        try:
            self.cv.main()
        finally:
            sys.argv = old
        with io.open(self.cv.OUT, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]

    @staticmethod
    def row(ts, prefix, text=WRONG, source="hook-codex", origin="owner"):
        return {"ts": ts, "session": prefix, "source": source, "origin": origin, "text": text}

    def test_1_to_6(self):
        self.ledger([
            self.row("2026-09-20T13:55:11", "01a0be54-107"),
            self.row("2026-09-20T14:00:00", "825b1305-b79", source="hook"),
            self.row("2026-09-20T14:05:00", "01a0be55-107", origin="automation"),
            self.row("2026-09-20T14:10:00", "01a0be56-107", text=WRONG + " " + ("x" * 4000), source="backfill-codex"),
            self.row("2026-09-20T14:15:00", "01a0be57-107", text="open the report for me in chrome please"),
        ])
        self.assertTrue(self.cv.verdicts_of(WRONG), "control sentence must be a verdict under the Claude patterns")
        rows = self.run_main()
        self.assertEqual(sorted(set(r["session"] for r in rows)), [key(FULL_A)])     # 1, 2, 3, 4, 6
        self.assertEqual(rows[0]["session_id"], FULL_A)
        self.assertIn("correction", [r["id"] for r in rows])
        want = time.mktime(time.strptime("2026-09-20T13:55:11", "%Y-%m-%dT%H:%M:%S"))
        self.assertAlmostEqual(rows[0]["ts"], want, places=0)                        # 5
        utc_reading = calendar.timegm(time.strptime("2026-09-20T13:55:11", "%Y-%m-%dT%H:%M:%S"))
        if time.localtime(want).tm_gmtoff != 0:                                      # 5, negative arm
            self.assertGreater(abs(rows[0]["ts"] - utc_reading), 60, "the ledger's local time was read as UTC")
        self.assertTrue(all(r["runtime"] == "codex" for r in rows))

    def test_7_since(self):
        self.ledger([self.row("2026-08-20T10:00:00", "01a0be54-107", source="backfill-codex"),
                     self.row("2026-09-20T10:00:00", "01a02222-107")])
        rows = self.run_main("--since", "2026-09-01")
        self.assertEqual(sorted(set(r["session"] for r in rows)), [key(FULL_E)])

    def test_8_same_first_eight(self):
        self.ledger([self.row("2026-09-21T10:00:00", "01a03333-111"), self.row("2026-09-21T10:00:30", "01a03333-222")])
        rows = self.run_main()
        self.assertEqual(sorted(r["session"] for r in rows), sorted([key(FULL_B1), key(FULL_B2)]))

    def test_9_shared_prefix(self):
        self.ledger([self.row("2026-09-22T10:00:00", "01a04444-555", text="no, that is wrong, the caption is late"),
                     self.row("2026-09-22T10:05:00", "01a04444-555", text="no, that is wrong, the sound is off")])
        rows = sorted(self.run_main(), key=lambda r: r["ts"])
        self.assertEqual((rows[0]["session"], rows[0]["resolved"]), (key(FULL_C2), "history text"))
        self.assertEqual((rows[1]["session"], rows[1]["session_id"], rows[1]["resolved"]), ("", "", "unconfirmed"))

    def test_10_unknown_prefix(self):
        self.ledger([self.row("2026-09-23T10:00:00", "01a06666-777")])
        rows = self.run_main()
        self.assertEqual((rows[0]["session"], rows[0]["resolved"], rows[0]["session_prefix"]), ("", "unknown", "01a06666-777"))

    def test_11_same_key_as_the_capture(self):
        sys.path.insert(0, BIN)
        import extract_codex_events as E
        p = os.path.join(self.tmp, "rollout.jsonl")
        with io.open(p, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"timestamp": "2026-09-28T01:00:00.000Z", "type": "session_meta",
                                 "payload": {"id": FULL_A, "cwd": os.path.expanduser("~"), "originator": "codex_exec",
                                             "source": "exec"}}) + "\n")
            fh.write(json.dumps({"timestamp": "2026-09-28T01:00:05.000Z", "type": "event_msg",
                                 "payload": {"type": "item_completed",
                                             "item": {"type": "CommandExecution", "command": "git status",
                                                      "cwd": os.path.expanduser("~")}}}) + "\n")
        got = []
        E.scan(p, 0, {}, lambda ts, s, *rest: got.append(s), collections.Counter())
        self.assertTrue(got, "the capture emitted nothing for the synthetic rollout")
        self.assertEqual(set(got), {self.cv.capture_key(FULL_A)})

    def test_12_rollout_text(self):
        self.ledger([self.row("2026-09-24T10:00:00", "01a05555-666", text=D_TEXT)])
        rows = self.run_main()
        self.assertEqual((rows[0]["session"], rows[0]["resolved"]), (key(FULL_D), "rollout text"))

    def test_13_history_id_and_rollout_id_share_a_prefix(self):
        self.ledger([self.row("2026-09-25T10:00:00", "01a07777-888", text=H_TEXT),
                     self.row("2026-09-25T10:01:00", "01a07777-888", text=R_TEXT),
                     self.row("2026-09-25T10:02:00", "01a07777-888", text="no, that is wrong, the map is late")])
        rows = sorted(self.run_main(), key=lambda r: r["ts"])
        self.assertEqual([(r["session"], r["resolved"]) for r in rows],
                         [(key(FULL_H), "history text"), (key(FULL_R), "rollout text"), ("", "unconfirmed")])

    def test_14_one_known_id_is_not_proof(self):
        self.ledger([self.row("2026-09-26T10:00:00", "01a0be54-107", text="no, that is wrong, the chart is empty"),
                     self.row("2026-09-26T10:05:00", "01a05555-666", text="no, that is wrong, the chart is empty")])
        rows = self.run_main()
        self.assertEqual([(r["session"], r["session_id"], r["resolved"]) for r in rows], [("", "", "unconfirmed")] * 2)


if __name__ == "__main__":
    print("testing", TARGET)
    unittest.main(verbosity=1)
