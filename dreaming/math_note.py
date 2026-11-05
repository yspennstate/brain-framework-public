#!/usr/bin/env python3
"""Read one complete current math note from the explicit personal math store.

The returned source is untrusted mathematical reference material, not instructions.
This command never initializes a store or imports personal episodic memories.
"""
import argparse
import os
import time
from pathlib import Path
import sys


def _log_fetch(source_id):
    """Record that someone followed the served block's pointer.

    Added 2026-09-13, to answer a question the campaign could not: the served block is a SELECTION plus a
    pointer ("Full note: python math_note.py SOURCE_ID"), and whether a card's truncated statement matters at
    all depends on whether readers follow that pointer. The 09-12 A/B's solvers did - its arm scored the page
    families 260/260 while the captured blocks alone answered 1 of 90 - but nothing records what ordinary
    sessions do, so the whole cards-versus-statement-length trade rests on an assumption. One line per fetch
    makes it a measurement. Best-effort and never fatal: a note read must not fail because a log is unwritable.
    """
    try:
        path = Path(__file__).resolve().parent / 'mathbrain_fetches.tsv'
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write('%d\t%s\t%s\n' % (int(time.time()),
                                       os.environ.get('CLAUDE_SESSION_ID', '') or '',
                                       source_id))
    except Exception:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_id')
    args = parser.parse_args(argv)
    _log_fetch(args.source_id)
    home = Path.home()
    sys.path.insert(0, str(home / 'math-brain-build/src'))
    from math_brain.current_note import main as read_note
    return read_note(['--database', str(home / 'math-brain-build/local/population/brain.sqlite3'),
                      '--id', args.source_id])


if __name__ == '__main__':
    raise SystemExit(main())
