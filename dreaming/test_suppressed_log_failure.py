"""The show-or-hold trial's outcome log must not lose a row silently (dot D7CB6565's round-6 disposition, path 1):
_log_suppressed records a refused or failed append in EMIT_ERRORS as 'suppressed_log', and records nothing when the
write lands. Controls both ways, in a temporary directory. Run: python test_suppressed_log_failure.py"""
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ip_suppressed_test", os.path.join(HERE, "instinct_prime.py"))
ip = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ip)
tmp = tempfile.mkdtemp()
ip.EMIT_ERRORS = os.path.join(tmp, "emit.tsv")
ip.SUPPRESSED_LOG = os.path.join(tmp, "supp.tsv")
SID = "12345678-aaaa-bbbb-cccc-000000000000"
DATA = {"session_id": SID, "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "rm x"}}
fails = 0


def check(cond, what):
    global fails
    print(("PASS  " if cond else "FAIL  ") + what)
    fails += 0 if cond else 1


def emit_rows():
    if not os.path.exists(ip.EMIT_ERRORS):
        return []
    with open(ip.EMIT_ERRORS, encoding="utf-8", newline="\n") as f:
        return [l.rstrip("\n").split("\t") for l in f if l.strip()]


ip._SUPPRESSED[:] = [("rX", "backoff"), ("rY", "cap")]
ip._append_text = lambda path, text: False
ip._log_suppressed("PreToolUse", DATA)
e = emit_rows()
check(len(e) == 1 and e[0][1] == SID and e[0][2] == "suppressed_log" and e[0][3] == "rX,rY",
      "a journal that refuses the append leaves a suppressed_log row naming the session and reflexes (%s)" % e)
calls = []
ip._append_text = lambda path, text: calls.append(text) or True
ip._log_suppressed("PreToolUse", DATA)
check(len(emit_rows()) == 1 and len(calls) == 1 and "\trX\tbackoff\t" + SID in calls[0],
      "an accepted append records nothing in EMIT_ERRORS and carries the rows")
ip._append_text = None
ip.SUPPRESSED_LOG = tmp                                     # a directory: the plain append raises
ip._log_suppressed("PreToolUse", DATA)
check(len(emit_rows()) == 2 and emit_rows()[-1][2] == "suppressed_log", "a plain append that raises is recorded too")
ip.SUPPRESSED_LOG = os.path.join(tmp, "supp.tsv")
ip._log_suppressed("PreToolUse", DATA)
with open(ip.SUPPRESSED_LOG, encoding="utf-8") as f:
    body = f.read()
check(len(emit_rows()) == 2 and body.count("\n") == 2 and "\trY\tcap\t" + SID in body,
      "a plain append that lands writes both rows and records no failure")
ip._append_text = lambda path, text: False
ip._log_suppressed("PreToolUse", dict(DATA, session_id="not-a-uuid"))
ip._SUPPRESSED[:] = []
ip._log_suppressed("PreToolUse", DATA)
check(len(emit_rows()) == 2, "a non-uuid session or an empty buffer writes nothing and records nothing")
ip._SUPPRESSED[:] = [("rZ", "backoff")]
ip._log_suppressed("PreToolUse", None)
check(len(emit_rows()) == 2, "malformed hook data never raises out of the function")
print(("PASS" if not fails else "FAIL") + " - suppressed-log failure controls, %d failed" % fails)
sys.exit(1 if fails else 0)
