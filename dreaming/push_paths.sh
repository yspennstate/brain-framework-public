#!/usr/bin/env bash
# Push exactly the named paths onto origin/main of a SHARED clone without touching its working tree or branch.
# The tip is read ONCE (memory a_temp_index_push_that_reads_the_tip_twice_reverts_a_peer_2026_09_24); the new tree
# must differ from its parent in exactly the named paths; nothing is pushed unless the secret scan prints clean.
# The push holds the remote to the tip it built on (--force-with-lease): a remote rewound after the scan would otherwise
# make commits reachable that the scan never covered (28 Sep, account02/B06 r63/r64); a refusal rebuilds on the new tip.
# usage: push_paths.sh REPO "commit message" path1 [path2 ...]
set -u
REPO="$1"; MSG="$2"; shift 2
HERE=$(cd "$(dirname "$0")" && pwd)   # before the cd: $0 may be relative to the caller's directory
cd "$REPO" || exit 3
export GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never PYTHONIOENCODING=utf-8
ATTEMPTS=${PUSH_PATHS_ATTEMPTS:-8}
SIG0=""   # the diff (modes, blob ids, paths) of the commit that passed the removal check and the scan
# Fetch into a ref no other agent writes (28 Oct): peers fetch the same shared clone at once, and git refuses to move
# refs/remotes/origin/main when its old value changed under it ("incorrect old value provided"), so every attempt could
# fail at the fetch before the push race began (memory a_shared_clones_origin_main_is_a_contended_ref_fetch_into_your_own).
PRIV="refs/push_paths/$$/main"
IDX=""
trap 'rm -f "$IDX"; git update-ref -d "$PRIV" 2>/dev/null' EXIT
trap 'exit 143' TERM INT   # without it a `timeout` kill skips the EXIT trap and leaves refs/push_paths/<pid> behind (29 Oct)
# Time budget (31 Oct): an attempt on this repo can take minutes (fetch, read-tree, scan), and callers wrap the
# script in `timeout`; with 12 attempts the 1500 s timeout killed it three times before the API fallback ever ran.
# Once a scanned commit exists and the budget is spent, stop racing the tip and go to the fallback.
T_START=$(date +%s)
# A path the repo ignores is refused (1 Nov): update-index --add stages it whatever .gitignore says, and a memory mirror
# that listed every memory handed this script the one memory kept off GitHub on purpose (it holds a live bot key); only
# the secret scan stopped it. check-ignore reads the clone's own index, so a TRACKED file matching a pattern still pushes.
if [ "${ALLOW_IGNORED:-0}" != "1" ]; then
  IGN=$(printf '%s\n' "$@" | git check-ignore --stdin 2>/dev/null | head -3 | tr '\n' ' ')
  if [ -n "$IGN" ]; then echo "REFUSED: ${IGN}is in .gitignore (set ALLOW_IGNORED=1 if intended)"; exit 3; fi
fi
for attempt in $(seq 1 "$ATTEMPTS"); do
  if [ -n "${SIG0:-}" ] && [ $(( $(date +%s) - T_START )) -ge "${PUSH_PATHS_BUDGET:-480}" ]; then
    echo "time budget spent after $((attempt - 1)) attempt(s); going to the API fallback"; break
  fi
  # --refmap= : without it git ALSO updates refs/remotes/origin/main opportunistically and fails the same way (28 Oct 23:4x)
  git fetch -q --no-tags --refmap= origin "+refs/heads/main:$PRIV" || { echo "fetch failed"; exit 3; }
  P=$(git rev-parse "$PRIV") || exit 3
  # A retry rebuilds on a tip a peer just pushed. If that push touched a path we are about to write whole, our copy
  # would overwrite their change (24 Oct 22:3x: a README rebuilt on a peer's commit put back two test rows they had
  # just updated; ALLOW_REMOVALS was set for an intended one-line edit, so the removal check let it through).
  [ -z "${P0:-}" ] && P0="$P"
  if [ "$P" != "$P0" ]; then
    MOVED=$(git diff --name-only "$P0" "$P" -- "$@")
    if [ -n "$MOVED" ]; then echo "REFUSED: a peer changed $MOVED since this push began ($P0 -> $P); merge their change into your copy first"; exit 3; fi
  fi
  # Fast retry (28 Oct): a rejection used to redo the index, the removal check and the full scan - minutes on a busy
  # box - while Codex accounts move main about once a minute, so all three attempts lost the race (fetch alone 22 s,
  # read-tree 7 s). After one commit has passed the checks, a retry puts THAT commit onto the new tip at tree level
  # (merge-tree, no index) and pushes it only if its diff against the new tip is byte-identical (modes, blob ids,
  # paths) to the diff that was checked and scanned. Anything else falls through to the full rebuild below.
  if [ -n "$SIG0" ]; then
    T=$(git merge-tree --write-tree --merge-base="$PB" "$P" "$C0" 2>/dev/null | head -1)
    if [ -n "$T" ] && [ "$(git diff-tree -r "$P^{tree}" "$T")" = "$SIG0" ]; then
      C=$(git commit-tree "$T" -p "$P" -m "$MSG") || exit 3
      if git push -q --force-with-lease="refs/heads/main:$P" origin "$C:refs/heads/main"; then
        echo "pushed $C onto $P (fast retry $attempt: same diff as the scanned commit $C0)"
        git ls-remote origin refs/heads/main | cut -c1-12
        exit 0
      fi
      echo "push rejected on attempt $attempt (the tip moved); fast retry on the new tip"
      continue
    fi
    echo "fast retry not possible on $P; full rebuild"
  fi
  IDX=$(mktemp); rm -f "$IDX"
  # the EXIT trap set above removes $IDX too: an early exit used to leave a 30 MB index in Temp (34 found 22 Oct, 1.0 GB)
  export GIT_INDEX_FILE="$IDX"
  git read-tree "$P" || { echo "read-tree failed"; exit 3; }
  # Staged in three git calls for the whole list (31 Oct): one hash-object, ls-tree and update-index per path cost
  # ~25 s a file on the loaded box, so a 150-file push spent an hour here before it reached the scan.
  for p in "$@"; do [ -f "$p" ] || { echo "missing path $p"; exit 3; }; done
  mapfile -t BL < <(printf '%s\n' "$@" | git hash-object -w --stdin-paths)
  [ "${#BL[@]}" -eq "$#" ] || { echo "hash-object gave ${#BL[@]} blob(s) for $# path(s)"; exit 3; }
  declare -A MODES=()
  while IFS= read -r -d '' rec; do meta=${rec%%$'\t'*}; MODES["${rec#*$'\t'}"]=${meta%% *}; done < <(git ls-tree -z "$P" -- "$@")
  i=0
  for p in "$@"; do printf '%s %s\t%s\0' "${MODES[$p]:-100644}" "${BL[$i]}" "$p"; i=$((i + 1)); done \
    | git update-index -z --add --index-info || exit 3   # keeps each tracked path's mode
  # --missing-ok (2 Nov): the shared clone is partial, so the default existence check fetched absent blobs from the
  # remote one by one - 15+ min (a mirror push sat here 1.5 h); every blob here comes from origin/main or hash-object -w
  T=$(git write-tree --missing-ok) || exit 3
  unset GIT_INDEX_FILE; rm -f "$IDX"
  CHANGED=$(git diff-tree -r --name-only "$P^{tree}" "$T" | sort)
  WANT=$(printf '%s\n' "$@" | sort)
  if [ -z "$CHANGED" ]; then echo "nothing to push: every path already matches origin/main ($P)"; exit 0; fi
  EXTRA=$(comm -23 <(echo "$CHANGED") <(echo "$WANT"))
  if [ -n "$EXTRA" ]; then echo "REFUSED: the tree changes paths not named: $EXTRA"; exit 3; fi
  SIG=$(git diff-tree -r "$P^{tree}" "$T")
  # A shared file whose working copy lacks lines someone pushed from another clone would silently lose them (09-24
  # 04:4x: five ledger rows). Refuse any push that REMOVES lines unless ALLOW_REMOVALS=1; checked by exact set
  # comparison in python (sort/comm disagree on non-ASCII lines and reported 337 phantom removals).
  if [ "${ALLOW_REMOVALS:-0}" != "1" ]; then
    # one python for every path (31 Oct; it was one per path); prints the first path that loses lines, or nothing
    REMOVED=$(python -c "
import subprocess, sys
def blob(spec):
    r = subprocess.run(['git', 'show', spec], capture_output=True)
    return set(r.stdout.decode('utf-8', 'replace').splitlines()) if r.returncode == 0 else set()
for p in sys.argv[3:]:
    n = len(blob(sys.argv[1] + ':' + p) - blob(sys.argv[2] + ':' + p))
    if n:
        print(str(n) + ' ' + p); break
" "$P" "$T" "$@")
    if [ -n "$REMOVED" ]; then echo "REFUSED: ${REMOVED#* } would lose ${REMOVED%% *} line(s) that origin/main has (set ALLOW_REMOVALS=1 if intended)"; exit 3; fi
  fi
  C=$(git commit-tree "$T" -p "$P" -m "$MSG") || exit 3
  SCAN=$(python "$HOME/.claude/dreaming/scan_secrets.py" --range "$P..$C" 2>&1); SRC=$?
  echo "$SCAN" | tail -2
  if [ $SRC -ne 0 ] || ! echo "$SCAN" | grep -qx "secret scan clean"; then echo "REFUSED: scan not clean (rc $SRC)"; exit 2; fi
  SIG0="$SIG"; C0="$C"; PB="$P"
  if git push -q --force-with-lease="refs/heads/main:$P" origin "$C:refs/heads/main"; then
    echo "pushed $C onto $P; changed: $(echo $CHANGED)"
    git ls-remote origin refs/heads/main | cut -c1-12
    exit 0
  fi
  echo "push rejected on attempt $attempt (the tip moved); rebuilding on the new tip"
  sleep 3
done
# API fallback (29 Oct 00:3x): on a repo whose main moves every minute the whole-tip race can be lost on every attempt
# (7 of 7, 28 Oct 23:5x-29 Oct 00:2x, 25 minutes). GitHub's contents API checks each FILE's blob, not the tip, so one
# call lands it (pictures_board.py went up as 797f28eb that way). Used only when a commit passed the removal check and
# the secret scan, the files on disk are still the scanned blobs, and the repo has no push CI (or the message says
# [skip ci]). One commit per path; a path a peer changed since the scanned tip is refused by GitHub (409).
if [ -n "$SIG0" ] && [ "${PUSH_PATHS_API:-1}" = "1" ]; then
  if [ -n "$(git ls-tree "$PB" .github/workflows 2>/dev/null)" ] && ! printf '%s' "$MSG" | grep -q '\[skip ci\]'; then
    echo "API fallback skipped: the repo has push CI and the message lacks [skip ci]"; echo "gave up after $ATTEMPTS attempts"; exit 3
  fi
  SLUG=$(git remote get-url origin | sed -E 's#^(https://github.com/|git@github.com:)##; s#\.git$##')
  export PP_TOK=$(printf 'protocol=https\nhost=github.com\n\n' | GCM_INTERACTIVE=never git credential fill 2>/dev/null | sed -n 's/^password=//p')
  [ -n "$PP_TOK" ] || { echo "API fallback: no credential"; echo "gave up after $ATTEMPTS attempts"; exit 3; }
  # One hash-object and one ls-tree for the whole list (1 Nov): two git calls per path took 15+ minutes for 150 paths.
  mapfile -t DH < <(printf '%s\n' "$@" | git hash-object --stdin-paths)
  mapfile -t CH < <(for p in "$@"; do printf '%s\n' "$p"; done | python -c "
import subprocess, sys
ps = sys.stdin.read().splitlines()
out = subprocess.run(['git', 'ls-tree', '-z', sys.argv[1], '--'] + ps, capture_output=True).stdout.decode('utf-8')
m = {}
for r in filter(None, out.split(chr(0))):
    meta, p = r.split(chr(9), 1); m[p] = meta.split()[2]
sys.stdout.buffer.write((chr(10).join(m.get(p, '-') for p in ps) + chr(10)).encode())
" "$C0")
  [ "${#DH[@]}" -eq "$#" ] && [ "${#CH[@]}" -eq "$#" ] || { echo "API fallback REFUSED: could not hash the paths"; exit 3; }
  i=0; for p in "$@"; do
    if [ "${DH[$i]}" != "${CH[$i]}" ]; then echo "API fallback REFUSED: $p changed on disk since the scan"; exit 3; fi
    i=$((i+1))
  done
  # Batched route first (1 Nov): one commit for every path through the Git Data API, the scanned blobs read from C0,
  # a lost race retried in about two seconds on the new tip (the contents API below is one commit per path - 875 for
  # a memory mirror). Exit 4 = the API is unusable here, so fall through to the contents API; 0 landed; else refused.
  if [ -f "$HERE/push_paths_gitdata.py" ]; then
    python "$HERE/push_paths_gitdata.py" "$SLUG" "$PB" "$C0" "$MSG" "$@"; GRC=$?
    if [ $GRC -eq 0 ]; then unset PP_TOK; git ls-remote origin refs/heads/main | cut -c1-12; exit 0; fi
    if [ $GRC -ne 4 ]; then unset PP_TOK; exit 3; fi
  fi
  # A path that already matches the base costs no call (2 Nov): the list holds every NAMED path, and each PUT counts
  # against the REST quota every agent on this token shares. CH holds the scanned blob of each path, in the same order.
  i=-1
  for p in "$@"; do
    i=$((i+1))
    OLD=$(git rev-parse -q --verify "$PB:$p" 2>/dev/null || true)
    if [ -n "$OLD" ] && [ "$OLD" = "${CH[$i]}" ]; then continue; fi
    python -c "
import os, sys, json, base64, urllib.request, urllib.parse, urllib.error
slug, path, old, msg = sys.argv[1:5]
body = {'message': msg, 'content': base64.b64encode(open(path, 'rb').read()).decode(), 'branch': 'main'}
if old: body['sha'] = old
url = 'https://api.github.com/repos/%s/contents/%s' % (slug, urllib.parse.quote(path))
q = urllib.request.Request(url, data=json.dumps(body).encode(), method='PUT',
    headers={'Authorization': 'token ' + os.environ['PP_TOK'], 'Accept': 'application/vnd.github+json'})
try:
    d = json.load(urllib.request.urlopen(q, timeout=180))
    print('pushed %s by the contents API: commit %s blob %s' % (path, d['commit']['sha'], d['content']['sha']))
except urllib.error.HTTPError as e:
    print('API fallback REFUSED for %s: http %s %s' % (path, e.code, e.read()[:200].decode('utf-8', 'replace'))); sys.exit(3)
" "$SLUG" "$p" "$OLD" "$MSG" || { unset PP_TOK; exit 3; }
  done
  unset PP_TOK
  exit 0
fi
echo "gave up after $ATTEMPTS attempts"; exit 3
