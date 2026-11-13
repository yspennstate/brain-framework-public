# Capture at file boundaries

The graph capture reads session transcripts while their writers are still appending to them. A reader
that saves its current file size as progress can lose or double events, and it can save that size
safely only when two things hold: the size ends a complete record, and the scan read no bytes past
the same point.

On 3 October the Codex capture failed six of ten file-boundary controls. A session header split
across two writes left zero events after the header was completed. A second command split the same
way left five events instead of ten. A record appended during the scan produced fifteen events
instead of ten, with the second file counted twice. These counts came from the saved rows, not only
from the test runner.

The capture now pins the last complete newline, scans only up to it and saves that exact offset. If
a source changes inside its pinned boundary, that scan fails and is retried. All ten boundary
controls pass, both against the candidate and in a fresh process importing the installed code. Four
more controls also pass: the 881 completed events of a real rollout are unchanged, two retries after
a lost journal add no duplicates, and a source shortened during a scan commits no unstable events.

The code is `framework/brain_graph/bin/extract_codex_events.py` and the controls are
`test_codex_capture_boundaries.py` beside it:

    python test_codex_capture_boundaries.py --implementation extract_codex_events.py --run-dir <new folder>

This checks capture mechanics only. It does not show better graph suggestions, prevented mistakes or
better mathematical answers, and events lost by earlier captures were not reconstructed.

The check carries over to any incremental reader. Finish an interrupted record in a second pass,
append a record during the first pass, and compare event identities and counts with a single pass
over the complete bytes. A file that exists, a zero exit code and a cursor at the end of the file
prove nothing on their own.
