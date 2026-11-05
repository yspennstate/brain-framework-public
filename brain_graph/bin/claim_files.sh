#!/usr/bin/env bash
# Claim (or assert) each file named on the command line on the agent mesh, one resource per file, the Windows path built
# with sed so no backslash passes through a command string. Inline loops of the form ...\\$n.py claimed the literal
# resource '...$n.py' (the Bash tool collapses a double backslash before the shell runs, and bash then reads \$ as an
# escaped dollar) - 27 Sep 2026, claude-825b1305; memory the_bash_tool_collapses_double_backslashes_2026_09_14.
#   MESH_AGENT=<your mesh id> bash claim_files.sh claim|assert PURPOSE FILE...     (FILE as /c/Users/... or C:/Users/...)
#   MESH_AGENT=<your mesh id> bash claim_files.sh run PURPOSE FILE... -- COMMAND [ARG...]
#   MESH_AGENT=<your mesh id> bash claim_files.sh release FILE...     (no purpose; exit 3 unless every one was released)
# Exit 3 when any claim is refused or any assert fails: stop, as the protocol says.
#
# run claims every FILE, asserts every FILE, and only then runs COMMAND (its exit code is the script's). If any claim is
# refused or any assert fails, COMMAND never runs. It exists because 'claim ...; write' does not stop on a refusal:
# twice on 27 Sep (15:4x a memory write under another agent's index claim; 16:2x a run-log append while this agent's
# own registration had lapsed) the refusal printed and the write after the ';' ran anyway.
set -u
A="${MESH_AGENT:?set MESH_AGENT to your agent mesh id}"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    M="python -X utf8 ~/ai-memories-and-functionality/12_cognitive_architecture/agent_mesh/agent_mesh.py"
    winpath() { printf '%s' "$1" | sed -e 's#^/\([a-zA-Z]\)/#\1:/#' -e 's#/#\\#g'; };;
  *)  # 29 Oct (Mac): the mesh every agent here uses, and POSIX paths unchanged (the sed made /Users/x into \Users\x)
    M="python3 -X utf8 $HOME/brain/brain-framework/mesh/agent_mesh.py"
    winpath() { printf '%s' "$1"; };;
esac
TTL="${MESH_TTL:-2400}"
ACT="${1:-}"
# Until 28 Sep there was no release action: "release FILE" read FILE as the purpose, found no files and printed
# "release: 0 files, rc=0" - a no-op that looked like success, so the claims stayed held until they expired.
case "$ACT" in
  release)
    shift; n=0
    for f in "$@"; do
      win=$(winpath "$f")
      out=$($M release --agent "$A" --resource "file:$win" 2>&1) && echo "$out" | grep -q '"released": [1-9]' && n=$((n+1))
    done
    echo "release: $n of $# released"
    [ "$n" -eq "$#" ] && exit 0 || exit 3;;
  claim|assert|run) ;;
  *) echo "unknown action '$ACT' (claim, assert, run or release); nothing done"; exit 2;;
esac
# Until 28 Sep 10:2x "assert FILE" read FILE as the purpose, asserted nothing and printed "assert: 0 files, rc=0": a check
# that looked green and checked nothing (found on this agent's own report edit; the claims were live, so no harm then).
# assert's purpose is now optional - a first argument that names an existing file is a file. claim and run refuse a
# purpose that names an existing file ("claim FILE1 FILE2" would have claimed FILE2 alone), and no action passes with
# zero files.
shift
if [ "$ACT" = "assert" ] && [ $# -ge 1 ] && [ -e "$1" ]; then
  PURPOSE=""
else
  PURPOSE="${1:-}"
  [ $# -ge 1 ] && shift
  if [ "$ACT" != "assert" ] && [ -n "$PURPOSE" ] && [ -e "$PURPOSE" ]; then
    echo "$ACT: the first argument is the purpose, and '$PURPOSE' names a file; nothing done"; exit 2
  fi
fi
FILES=()
CMD=()
if [ "$ACT" = "run" ]; then
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do FILES+=("$1"); shift; done
  if [ $# -eq 0 ]; then echo "run: no '--' before the command; nothing claimed, nothing run"; exit 2; fi
  shift
  CMD=("$@")
  if [ ${#CMD[@]} -eq 0 ] || [ ${#FILES[@]} -eq 0 ]; then echo "run: need at least one FILE and a COMMAND"; exit 2; fi
else
  FILES=("$@")
  if [ ${#FILES[@]} -eq 0 ]; then echo "$ACT: no files named; nothing done"; exit 2; fi
fi
rc=0
if [ "$ACT" = "claim" ] || [ "$ACT" = "run" ]; then
  for f in "${FILES[@]}"; do
    win=$(winpath "$f")
    # a refusal names its reason: a bare "REFUSED" once read as another agent's claim when it was this agent's own
    # lapsed registration (29 Sep, claude-825b1305)
    out=$($M claim --agent "$A" --resource "file:$win" --mode write --ttl "$TTL" --purpose "$PURPOSE" 2>&1) \
      || { echo "REFUSED $win: $(printf '%s' "$out" | tr '\r\n' '  ' | tr -s ' ' | cut -c1-240)"; rc=3; }
  done
fi
if [ "$ACT" = "assert" ] || { [ "$ACT" = "run" ] && [ $rc -eq 0 ]; }; then
  for f in "${FILES[@]}"; do
    win=$(winpath "$f")
    out=$($M assert --agent "$A" --resource "file:$win" 2>&1) \
      || { echo "ASSERT FAILED $win: $(printf '%s' "$out" | tr '\r\n' '  ' | tr -s ' ' | cut -c1-240)"; rc=3; }
  done
fi
if [ "$ACT" = "run" ]; then
  if [ $rc -ne 0 ]; then
    echo "run: a claim was refused or an assert failed; the command was NOT run (rc 3)"
    exit 3
  fi
  "${CMD[@]}"
  exit $?
fi
echo "$ACT: ${#FILES[@]} files, rc=$rc"
exit $rc
