#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.."
START=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
echo "scenario start $START (about 10 minutes)"
step() { echo "$(date +%H:%M:%S) == $1"; }
exists() { docker inspect "$1" >/dev/null 2>&1 || { echo "   skip: no container named $1"; return 1; }; }
planned() {
  local action=$1 name=$2
  exists "$name" || return 0
  step "planned $action: $name"
  docker "$action" "$name" >/dev/null
  sleep 40
}

for c in ${SCENARIO_RESTART:-mympd dozzle dashdot}; do planned restart "$c"; done
STOPPER=${SCENARIO_STOP:-homarr}
if exists "$STOPPER"; then planned stop "$STOPPER"; planned start "$STOPPER"; fi
step "crash: api cannot reach its database"; scripts/demo.sh crash >/dev/null 2>&1; sleep 60
step "oom: worker over its 64 MB limit"; scripts/demo.sh oom >/dev/null 2>&1; sleep 60
step "backup: restic gets S3 403"; scripts/demo.sh backup >/dev/null 2>&1; sleep 150
scripts/demo.sh clean >/dev/null 2>&1
step "done"
.venv/bin/python -m sentinel.report --since "$START" | tee "logs/scenario-$(date +%Y%m%d-%H%M).md"
