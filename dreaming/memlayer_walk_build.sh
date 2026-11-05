#!/bin/zsh
# Builds the walk cache for graph_lane_store_memlayer once memory pressure is normal for 3 minutes running
# (claude-1ca628ae, 3 Nov 23:2x; the 22:43 kernel panic came from stacking heavy jobs under pressure).
L=~/.claude/dreaming/graph_lane_stage_memlayer/walk_build.log
ok=0
while [ $ok -lt 3 ]; do
  [ "$(sysctl -n kern.memorystatus_vm_pressure_level)" = "1" ] && ok=$((ok + 1)) || ok=0
  /bin/sleep 60
done
echo "$(date '+%F %T') pressure normal 3 min, building" >> $L
cd ~/.claude/dreaming
BRAIN_GRAPH_STORE=$HOME/.claude/dreaming/graph_lane_store_memlayer /usr/bin/time -l python3 graph_lane.py --build >> $L 2>&1
echo "$(date '+%F %T') build exit $?" >> $L
