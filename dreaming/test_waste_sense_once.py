"""Controls for two waste_sense.py additions: the standing paragraphs once per context, and the PowerShell parser
helper recognised as harness plumbing.

 W1 the first block to a session goes whole
 W2 the next block keeps the census lines (header, pid rows) and replaces the standing paragraphs with one note
 W3 after a compaction of the session the block goes whole again; a second session starts whole
 W4 the parser helper's -EncodedCommand line is plumbing; an ordinary encoded command and a plain line are not

    python test_waste_sense_once.py      (under a second; ALL PASS at the end; writes only to a temp folder)
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import base64
import datetime
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.pop("ONCE_PER_CONTEXT_OFF", None)
import once_per_context as O
import waste_sense as W

results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def block(n, pid):
    return "\n".join(["[waste-sense] judge your idle workers. This session runs %d live process(es) under it." % n,
                      "  - pid %d python.exe, 3 min: python worker.py" % pid,
                      "  For each process above ask: producing, for whom?",
                      "  Do not stop it to free resources; report it instead.",
                      "  When the task is done, end with a short report."])


tmp = tempfile.mkdtemp(prefix="waste_once_")
try:
    O.STATE = os.path.join(tmp, "opc.json")
    O.COMPACTION_INDEX = os.path.join(tmp, "INDEX.jsonl")
    open(O.COMPACTION_INDEX, "w", encoding="utf-8").close()
    a = block(2, 101)
    check(W._once("s1", a) == a, "W1 the first block goes whole")
    b = block(3, 202)
    got = W._once("s1", b).split("\n")
    check(got[0] == b.split("\n")[0] and got[1] == b.split("\n")[1] and len(got) == 3
          and "unchanged" in got[2] and "producing, for whom" not in "\n".join(got),
          "W2 the census lines go, the standing paragraphs become one note (%d lines)" % len(got))
    with open(O.COMPACTION_INDEX, "a", encoding="utf-8") as f:
        f.write(json.dumps({"session_id": "s1",
                            "at": datetime.datetime.fromtimestamp(time.time() + 1).isoformat()}) + "\n")
    check(W._once("s1", b) == b and W._once("s2", b) == b,
          "W3 after a compaction, and for a second session, the block goes whole")
    parser = ("$s=[Console]::In.ReadToEnd(); $t=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s)); "
              "[System.Management.Automation.Language.Parser]::ParseInput($t,[ref]$null,[ref]$null)")
    enc = base64.b64encode(parser.encode("utf-16-le")).decode()
    other = base64.b64encode("Get-ChildItem".encode("utf-16-le")).decode()
    check(W._harness_parser("powershell.exe -NoProfile -NonInteractive -NoLogo -EncodedCommand " + enc)
          and not W._harness_parser("powershell.exe -EncodedCommand " + other)
          and not W._harness_parser("python worker.py"),
          "W4 the parser helper is plumbing; other commands are not")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("ALL PASS" if all(results) else "SOME FAILED")
sys.exit(0 if all(results) else 1)
