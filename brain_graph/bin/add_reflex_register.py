"""Test, then add, the reflex recall_before_you_register.

It fires when a study is being registered: an entry line that opens with REGISTERED or PRE-REGISTERED (uppercase, as the
run logs write registrations), or a lowercase "pre-registered (" that opens a parenthesis of its own (the one lived
registration in that form). Result lines cite "(pre-registered 11:4x)" and stay silent; so do evidence notes
("registered 05:3x in ...") and a grep for the word. The hook matches json.dumps(tool_input), so every control runs on the
serialized tool input of a Write, an Edit or a Bash heredoc, as the hook sees it.
usage: python add_reflex_register.py [--write] [--via-hook]
"""
import json
import os
import re
import shutil
import sys
import time

DR = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")
LEARNED = os.path.join(DR, "instincts_learned.json")
RID = "recall_before_you_register"
# the hook matches with re.I, so the case the run logs use to mark a registration is held by a scoped (?-i:...)
TRIGGER = (r'(?-i:(?:(?:^|\\n|\n|(?<!\\)")[ \t]*(?:-[ \t]+)?(?:\d\d:\d[\dx](?:-\d\d:\d[\dx])?[ \t]+)?(?:PRE-)?REGISTERED\b'
           r'|\bpre-registered \((?!\d)))')
ENTRY = {
    "id": RID,
    "mode": "fire",
    "validity": "high",
    "trigger": TRIGGER,
    "gut": "You are registering a study. First ask whether it has already been measured: run brain.py recall with the "
           "question in plain words and grep the run log for its key terms. If an earlier study answers it, say so in "
           "the registration (a replication, or what is different) instead of starting over. A context reset's summary "
           "does not carry every study of the session it summarizes.",
    "sting": "28 Sep 07:1x: the 'answer arm' (does the agent's last answer name the order he will restate) was registered "
             "and run as new; it was the same log's 00:3x reply-orders study, run by the same agent six hours earlier and "
             "left out of the summary written at a context reset. About forty minutes and a duplicate memory; the result "
             "reproduced within one case. brain.py recall on the question returns the 00:3x study's memory third.",
    "source": "MISTAKES 2026-09-28 07:1x; memory a_reply_check_catches_only_the_mistakes_the_reply_admits_2026_09_28",
    "added": "2026-09-28 07:4x",
    "controls": "",
}
J = lambda d: json.dumps(d, ensure_ascii=False)
LOG = os.path.expanduser
POS = [  # the lived registration forms of the run logs, each as the tool input that writes it
    J({"file_path": "C:\\tmp\\entry.md", "content": "REGISTERED 28 Sep 07:1x, before any code: the answer arm (his 27 Sep idea)\n"}),
    J({"file_path": LOG, "old_string": "x", "new_string": "x\n- 00:3x PRE-REGISTERED, before any number (his orders at the reply)"}),
    J({"file_path": LOG, "old_string": "x", "new_string": "- 21:5x PRE-REGISTERED (GRA-03, before any number): replay the R7C rows"}),
    J({"file_path": "C:\\tmp\\e.md", "content": "- 09:42  PRE-REGISTERED before any result: the rewrite tool at equal lines\n"}),
    J({"file_path": "C:\\tmp\\e.md", "content": "prior text\n  PRE-REGISTERED placebo run (their proposal): arm placebo\n"}),
    J({"file_path": "C:\\tmp\\e.md", "content": "- 22:3x cand75 pre-registered (arm_patches/patch_cand75.py: six conditions)"}),
    J,
    J({"file_path": "C:\\tmp\\e.md", "content": "- 11:5x-12:0x PRE-REGISTERED (the lag rule): on the split\n"}),
]
NEG = [
    J({"file_path": LOG, "old_string": "x", "new_string": "- 13:1x  CORRECTIONS RERUN RESULT (pre-registered 13:0x): 2,272 moments"}),
    J({"file_path": LOG, "old_string": "x", "new_string": "- 11:5x-12:0x  Error channel scored (pre-registered 11:4x): 4,968"}),
    J,
    J({"file_path": "C:\\m\\x.md", "content": "Registered before code in THREE_DAY_RUN_2026-09-27.md (07:1x); failed both.\n"}),
    J,
    J({"command": "grep -rhoE \".{0,30}\\bREGISTERED\\b.{0,90}\" study/*.md", "description": "Collect registrations"}),
    J({"file_path": LOG}),
    J({"file_path": "C:\\x\\hook.py", "old_string": "a", "new_string": "the hook registers his ask under your agent id"}),
    J({"file_path": "C:\\x\\s.json", "content": "{\"REGISTERED_AT\": 1, \"registered\": true}"}),
    J({"file_path": "C:\\x\\e.md", "content": "- 07:3x The answer arm against its registration: FAIL on both conditions\n"}),
    J({"file_path": "C:\\x\\e.md", "content": "- 02:0x UNREGISTERED lanes were set aside\n"}),
]


def check(match):
    bad = []
    for t in POS:
        if not match(t):
            bad.append(("MISSED", t[:110]))
    for t in NEG:
        if match(t):
            bad.append(("FIRED ON", t[:110]))
    print("%d of %d positives fire, %d of %d negatives silent" % (
        len(POS) - sum(b[0] == "MISSED" for b in bad), len(POS), len(NEG) - sum(b[0] == "FIRED ON" for b in bad), len(NEG)))
    for b in bad:
        print("  ", *b)
    return not bad


rx = re.compile(TRIGGER, re.I)   # as the hook compiles it
ok = check(lambda t: bool(rx.search(t)))
if "--write" in sys.argv:
    if not ok:
        sys.exit("controls failed; nothing written")
    d = json.load(open(LEARNED, encoding="utf-8"))
    assert isinstance(d, list)
    have = [x for x in d if x.get("id") == RID]
    shutil.copy2(LEARNED, LEARNED + ".bak_register_%s" % time.strftime("%Y%m%d_%H%M%S"))
    ENTRY["controls"] = ("trigger under re.I, as the hook compiles it, on the serialized tool input: %d/%d positives (the lived registration lines of the run "
                         "logs as Write, Edit and Bash-heredoc inputs: REGISTERED with a date, PRE-REGISTERED after a time, "
                         "with and without a comma or parenthesis, indented, a time range, the one lowercase 'cand75 "
                         "pre-registered (') and %d/%d negatives silent (two result lines citing '(pre-registered hh:mm)', an "
                         "evidence note, a capitalized 'Registered' in a memory body, two greps for the word, a Read of the "
                         "log, 'registers his ask', JSON keys, a result entry, UNREGISTERED); the compiled entry through "
                         "instinct_prime._fire gives the same; replayed over the real tool inputs of 24-28 Sep (79,164 "
                         "in 2,885 transcripts, as the hook serializes them): 31 fires, 29 of them registrations "
                         "(another agent's three among them, and the 07:1x answer-arm registration this reflex is for), "
                         "one restamp of a registration, one the reflex's own source" % (len(POS), len(POS), len(NEG), len(NEG)))
    if have:
        have[0]["trigger"] = TRIGGER
        have[0]["controls"] = ENTRY["controls"]
    else:
        d.append(ENTRY)
    tmp = LEARNED + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, LEARNED)
    print("written:", len(d), "reflexes")
if "--via-hook" in sys.argv:
    sys.path.insert(0, DR)
    import instinct_prime as IP
    only = [it for it in IP._load() if it["id"] == RID]
    print("compiled entries:", len(only))
    sys.exit(0 if (only and check(lambda t: bool(IP._fire(t, only)))) else 1)
