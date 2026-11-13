"""Controls for the math hook's relay skip: relayed agent messages, notifications and picture batches get no cards.

 K1 a relayed agent message is skipped
 K2 a task notification and a system reminder are skipped
 K3 picture-maker and picture-review heads are skipped, either article
 K4 an agent-launched mathematics prompt is not skipped
 K5 a question that only mentions an agent message later on is not skipped
 K6 MATHBRAIN_RELAY_SKIP=0 turns the skip off
 K7 _main_body silences a relay before any work, with the note skip_relay_or_picture_batch

    python test_relay_skip.py      (a second or two; ALL PASS at the end)
Runs under a temporary home, so nothing of the user's is read or written.
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import importlib
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="relay_skip_")
for k in ("HOME", "USERPROFILE"):
    os.environ[k] = TMP
os.environ.pop("MATHBRAIN_SHADOW", None)
os.makedirs(os.path.join(TMP, ".claude", "dreaming"), exist_ok=True)
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def load(**env):
    os.environ.pop("MATHBRAIN_RELAY_SKIP", None)
    os.environ.update(env)
    import math_sense
    importlib.reload(math_sense)
    return math_sense


def skips(M, prompt):
    rx = getattr(M, "_RELAY_OR_PICTURE_HEAD", None)
    return bool(getattr(M, "RELAY_SKIP", False) and rx is not None and rx.match(prompt))


try:
    M = load()
    check(skips(M, "<agent-message from=x>done with the merge</agent-message>"), "K1 a relayed agent message")
    check(skips(M, "  <task-notification>job finished</task-notification>")
          and skips(M, "﻿<system-reminder>x</system-reminder>"), "K2 a notification and a reminder")
    check(skips(M, "You are the picture maker for a history film. Draw five frames.")
          and skips(M, "You are a picture maker for three documentaries")
          and skips(M, "You are checking pictures drawn for the documentary X"), "K3 picture heads")
    check(not skips(M, "You are auditing a numbered mathematical argument. Step 3 uses the lemma."),
          "K4 an agent-launched mathematics prompt keeps its cards")
    check(not skips(M, "Is the bound sharp? <agent-message> came later"), "K5 the head must open the prompt")
    M0 = load(MATHBRAIN_RELAY_SKIP="0")
    check(not skips(M0, "<agent-message>x</agent-message>"), "K6 MATHBRAIN_RELAY_SKIP=0 turns it off")
    M = load()
    notes = []
    real = M._silence_note
    M._silence_note = lambda data, why, *a, **k: notes.append(why)
    try:
        r = M._main_body({"prompt": "<agent-message>merge pushed</agent-message>", "session_id": "s1"}, [])
    except Exception as e:
        r = e
    M._silence_note = real
    check(r is None and notes[:1] == ["skip_relay_or_picture_batch"], "K7 _main_body silences it first: %r" % notes[:2])
finally:
    os.environ.pop("MATHBRAIN_RELAY_SKIP", None)
    shutil.rmtree(TMP, ignore_errors=True)
print("ALL PASS" if all(results) else "FAILED %d of %d" % (results.count(False), len(results)))
sys.exit(0 if all(results) else 1)
