#!/bin/zsh
# NOT RUNNING. Starting it detached was refused on 3 Nov 06:5x as persistence, like launchd before it; it waits on
# the owner's word. Until then the session cron, or an agent running bin/live_sidecar.py memories by hand, does this.
# Hourly refresh of the live link sidecar's memory rows, as a detached loop. It replaces claude-00000002's session
# cron b5b65158, which fires only while that session sits idle and so went 01:52 -> 06:53 on 3 Nov without a run
# (456 memories changed, 1,010 links missing from the walk). launchd stays refused, so this dies with a reboot.
# Each pass takes an exclusive claim on memory:brain_graph/live and skips the hour if another agent holds it.
#   nohup nice -n 15 ~/.claude/brain_graph/bin/sidecar_hourly.sh > /dev/null 2>&1 &
M=$HOME/brain/brain-framework/mesh/agent_mesh.py
A=mac-sidecar-refresh
LOG=$HOME/.claude/brain_graph/sidecar_hourly.log
cd $HOME/.claude/brain_graph
while true; do
  python3 $M hello --agent $A --runtime detached --task "hourly link-sidecar refresh" --ttl 5400 > /dev/null 2>&1
  if python3 $M claim --agent $A --resource memory:brain_graph/live --mode exclusive --purpose "hourly link-sidecar refresh" --ttl 900 > /dev/null 2>&1; then
    out=$(nice -n 15 python3 bin/live_sidecar.py memories 2>&1 | tail -1)
    echo "$(date '+%F %T') memories $out" >> $LOG
    python3 $M release --agent $A --resource memory:brain_graph/live > /dev/null 2>&1
  else
    echo "$(date '+%F %T') claim held by another agent; skipped" >> $LOG
  fi
  /bin/sleep 3600
done
