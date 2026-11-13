"""math_note.py logs each fetch with the session and the read's outcome.

A fake math_brain.current_note under a temporary HOME answers one id and refuses the rest. The row for a good id
must end in ok, the row for a refused id in failed:<code>, and the session column must carry
CLAUDE_CODE_SESSION_ID, the variable the Claude Code tool shell actually sets.
Run: python3 test_math_note_fetch_log.py (exit 0 = pass)
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

FAKE = '''import sys
def main(argv):
    i = argv[argv.index('--id') + 1]
    if i == 'good-id':
        print('note body')
        return 0
    print('no such note', file=sys.stderr)
    raise SystemExit(2)
'''


def run():
    tmp = tempfile.mkdtemp(prefix='mnlog_')
    try:
        pkg = os.path.join(tmp, 'home', 'math-brain-build', 'src', 'math_brain')
        os.makedirs(pkg)
        open(os.path.join(pkg, '__init__.py'), 'w').close()
        with open(os.path.join(pkg, 'current_note.py'), 'w') as f:
            f.write(FAKE)
        tool = os.path.join(tmp, 'tool')
        os.makedirs(tool)
        shutil.copy2(os.path.join(HERE, 'math_note.py'), tool)
        # USERPROFILE too: on Windows Path.home() reads it, not HOME.
        env = dict(os.environ, HOME=os.path.join(tmp, 'home'), USERPROFILE=os.path.join(tmp, 'home'),
                   CLAUDE_CODE_SESSION_ID='sess-7KDP-2210')
        env.pop('CLAUDE_SESSION_ID', None)
        codes = []
        for sid in ('good-id', 'bad-id'):
            p = subprocess.run([sys.executable, os.path.join(tool, 'math_note.py'), sid], env=env,
                               capture_output=True, text=True)
            codes.append(p.returncode)
        rows = [line.rstrip('\n').split('\t')
                for line in open(os.path.join(tool, 'mathbrain_fetches.tsv'), encoding='utf-8')]
        fails = []
        if codes != [0, 2]:
            fails.append('exit codes %r, want [0, 2]' % codes)
        if len(rows) != 2:
            fails.append('%d rows, want 2' % len(rows))
        else:
            if rows[0][1:] != ['sess-7KDP-2210', 'good-id', 'ok']:
                fails.append('good row %r' % rows[0][1:])
            if rows[1][1:] != ['sess-7KDP-2210', 'bad-id', 'failed:2']:
                fails.append('bad row %r' % rows[1][1:])
        return fails
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    # Discovery is not execution: a help request exits before any work.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__ or os.path.basename(__file__))
        raise SystemExit(0)
    fails = run()
    for f in fails:
        print('FAIL', f)
    print('PASS' if not fails else 'FAILED')
    sys.exit(1 if fails else 0)
