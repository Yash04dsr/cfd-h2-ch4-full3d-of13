#!/usr/bin/env bash
echo "=== AWS EC2 OpenFOAM v13 Full-3D Monitor ($(date -u '+%Y-%m-%d %H:%M UTC')) ==="
tmux ls 2>/dev/null || echo "(no active tmux sessions)"
echo ""
for dir in ~/cfd_full3d_of13/validation_vattenfall ~/cfd_full3d_of13/cases/*; do
  [ -f "$dir/log.solver" ] || continue
  name=$(basename "$dir")
  endT=$(grep -E "^endTime" "$dir/system/controlDict" | awk '{print $2}' | tr -d ';')
  curT=$(grep -a "^Time = " "$dir/log.solver" | tail -n 1 | awk '{print $3}' | tr -d 's')
  co=$(grep -a "^Courant Number" "$dir/log.solver" | tail -n 1 | awk '{print $6}')
  clk=$(grep -a "^ExecutionTime" "$dir/log.solver" | tail -n 1 | awk '{print $7}')
  done_flag=$(grep -a "^End$" "$dir/log.solver" | tail -n 1)
  state="RUNNING"
  [ -n "$done_flag" ] && state="DONE"
  printf "%-24s | %-7s | t=%-10s / %-6s s | maxCo=%-8s | clock=%ss\n" "$name" "$state" "${curT:-0}" "$endT" "${co:--}" "${clk:-0}"
done
