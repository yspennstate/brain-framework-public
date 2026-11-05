"""Widen split_the_population_before_naming_it to the population label its trigger missed (account01/B13 R27, 28 Sep,
claude-825b1305), and add one line to recall_before_you_register's gut. Controls run on the serialized tool input,
matched with re.I as the hook does; --write edits instincts_learned.json (backup first), --via-hook checks the compiled
entry through instinct_prime._fire.
    python widen_population_reflex.py [--write] [--via-hook]
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import json
import os
import re
import shutil
import sys
import time

DR = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")
LEARNED = os.path.join(DR, "instincts_learned.json")
RID = "split_the_population_before_naming_it"
ADD = r"|\b(?:claude|codex|agent|agents'?|model|worker|headless)\s+sessions\s+only\b|\bhook\s+receivers\b"
REG_LINE = (" If the study reads logged use, name its population by session kind in the registration (session_kinds: "
            "his / script / test / unknown) - 28 Sep, 'Claude sessions only' named none.")
J = lambda d: json.dumps(d, ensure_ascii=False)
POS = [
    J({"file_path": "C:\\x\\run.md", "content": "  Outcome, Claude sessions only (transcripts): after a unit's first refill"}),
    J({"file_path": "C:\\x\\run.md", "content": "the outcome is read on Codex sessions only, in the next 20 calls"}),
    J({"file_path": "C:\\x\\run.md", "content": "Population: all hook receivers that saw the line in the week"}),
    J({"file_path": "C:\\x\\r.md", "old_string": "a", "new_string": "measured on agent sessions only (no bench workers)"}),
    J({"file_path": "C:\\x\\r.md", "content": "the agents' searches of the week"}),                 # an old positive kept
]
NEG = [
    J({"file_path": "C:\\x\\a.md", "content": "Every Claude and Codex session receives them automatically"}),
    J({"file_path": "C:\\x\\a.md", "content": "in EVERY Claude session (hook_dispatch_list.json: UserPromptSubmit)"}),
    J({"file_path": "C:\\x\\a.md", "content": "Claude sessions get distress injected by the compute_sense hook"}),
    J({"file_path": "C:\\x\\a.md", "content": "read the Claude session's transcript for the timestamps"}),
    J({"file_path": "C:\\x\\a.md", "content": "this override holds for this session only"}),
    J({"command": "python session_kinds.py --stats 7", "description": "Count sessions by kind"}),
    J({"file_path": "C:\\x\\a.md", "content": "the hook receives the event and returns"}),
]


def check(match):
    miss = [t for t in POS if not match(t)]
    fire = [t for t in NEG if match(t)]
    print("%d of %d positives fire, %d of %d negatives silent" % (len(POS) - len(miss), len(POS), len(NEG) - len(fire), len(NEG)))
    for t in miss:
        print("   MISSED", t[:110])
    for t in fire:
        print("   FIRED ON", t[:110])
    return not miss and not fire


d = json.load(open(LEARNED, encoding="utf-8"))
e = [x for x in d if x.get("id") == RID][0]
old = e["trigger"]
new = old if ADD in old else old[:-1] + ADD + ")"
assert new.endswith(")") and new.startswith("(?i)(")
print("old trigger on the controls:")
check(lambda t: bool(re.search(old, t, re.I)))
print("new trigger on the controls:")
ok = check(lambda t: bool(re.search(new, t, re.I)))
if "--write" in sys.argv:
    if not ok:
        sys.exit("controls failed; nothing written")
    shutil.copy2(LEARNED, LEARNED + ".bak_popwiden_%s" % time.strftime("%Y%m%d_%H%M%S"))
    e["trigger"] = new
    e["controls"] = (e.get("controls", "") + " | 28 Sep (B13 R27): widened to 'X sessions only' and 'hook receivers'; "
                     "under re.I on the serialized tool input %d/%d positives (the lived 'Outcome, Claude sessions only' "
                     "line among them), %d/%d negatives silent ('Every Claude and Codex session', 'in EVERY Claude session', "
                     "'Claude sessions get', 'the Claude session's transcript', 'for this session only', a --stats call, "
                     "'the hook receives')" % (len(POS), len(POS), len(NEG), len(NEG))).lstrip(" |")
    r = [x for x in d if x.get("id") == "recall_before_you_register"][0]
    if REG_LINE.strip() not in r["gut"]:
        r["gut"] = r["gut"] + REG_LINE
    tmp = LEARNED + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, LEARNED)
    print("written")
if "--via-hook" in sys.argv:
    sys.path.insert(0, DR)
    import instinct_prime as IP
    only = [it for it in IP._load() if it["id"] == RID]
    print("compiled entries:", len(only))
    sys.exit(0 if (only and check(lambda t: bool(IP._fire(t, only)))) else 1)
