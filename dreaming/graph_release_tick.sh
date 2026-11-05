#!/bin/zsh
# graph_release_tick.sh - keep the full brain graph at the newest published release, on a schedule.
# Run it from launchd or cron every 6 h; a check kept in a session loop dies with that session and the store falls a
# release behind without anyone noticing.
#   1. dry run of brain_release_check.py; nothing to do -> exit 0
#   2. free disk must cover this job's peak (download + unpacked store, ~1.5 GiB) plus the gate of the most important
#      neighbouring job: a floor that protects only its own job lets it starve the next one
#   3. the swap rebuilds the graph walk, so it can run under a heavy-job lock (GRAPH_TICK_LOCK_RUN names a wrapper
#      called as `<wrapper> <label> <command...>`); brain_release_check.py takes its own exclusive mesh claim on
#      memory:graph_lane_store and keeps the old store as graph_lane_store.prev_<tag> (brain data is never deleted)
#   4. the floor is checked again once the lock is held (--locked): the wait can be an hour of other jobs writing
#      (one measured wait went from 14.0 GiB free when it queued to 11.96 five minutes later)
# GRAPH_TICK_MIN_FREE_GIB overrides the floor (default 9); GRAPH_TICK_DRY=1 stops before the lock.
D=${0:A:h}
LOG=$D/logs/graph_release_tick.log
FLOOR=${GRAPH_TICK_MIN_FREE_GIB:-9}
mkdir -p $D/logs
say() { print -r -- "$(date '+%Y-%m-%d %H:%M:%S') $*" >> $LOG }
free_gib() { python3 -c "import shutil;print('%.2f' % (shutil.disk_usage('$D').free / 2**30))" }
if [[ $1 == --locked ]]; then
  free=$(free_gib)
  if (( free < FLOOR )); then
    say "lock taken but free $free GiB < $FLOOR GiB now: not applying, lock handed back"
    exit 0
  fi
  say "lock taken, free $free GiB: applying"
  exec nice -n 10 python3 -s $D/brain_release_check.py --apply
fi
out=$(nice -n 10 python3 -s $D/brain_release_check.py 2>&1)
pub=$(print -r -- "$out" | sed -n 's/^published: \([^ ]*\).*/\1/p')
have=$(print -r -- "$out" | sed -n 's/^installed: \([^ ]*\).*/\1/p')
if [[ -z $pub ]]; then
  say "no published release read: $(print -r -- $out | tail -1)"
  exit 0
fi
if [[ $pub == $have ]] || print -r -- "$out" | grep -q "up to date"; then
  say "up to date ($have)"
  exit 0
fi
free=$(free_gib)
if (( free < FLOOR )); then
  say "newer $pub (have $have) held: free $free GiB < $FLOOR GiB"
  exit 0
fi
if [[ ${GRAPH_TICK_DRY:-0} == 1 ]]; then
  say "newer $pub (have $have), free $free GiB: would apply (dry)"
  exit 0
fi
if [[ -n ${GRAPH_TICK_LOCK_RUN:-} && -x ${GRAPH_TICK_LOCK_RUN} ]]; then
  say "newer $pub (have $have), free $free GiB: waiting for the heavy lock"
  $GRAPH_TICK_LOCK_RUN "graph release swap $pub" /bin/zsh $D/graph_release_tick.sh --locked >> $LOG 2>&1
else
  say "newer $pub (have $have), free $free GiB: applying"
  /bin/zsh $D/graph_release_tick.sh --locked >> $LOG 2>&1
fi
rc=$?
say "apply finished rc=$rc; installed now: $(python3 -c "import json;print(json.load(open('$D/graph_lane_store/INSTALLED_RELEASE.json')).get('tag'))" 2>&1)"
exit $rc
