#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.."
START=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
echo "scenario start $START (about 10 minutes)"
step() { echo "$(date +%H:%M:%S) == $1"; }

step "planned restart: mympd"; docker restart mympd >/dev/null; sleep 40
step "planned restart: dozzle"; docker restart dozzle >/dev/null; sleep 40
step "planned stop: homarr"; docker stop homarr >/dev/null; sleep 40
step "planned start: homarr"; docker start homarr >/dev/null; sleep 40
step "planned restart: dashdot"; docker restart dashdot >/dev/null; sleep 40
step "crash: api cannot reach its database"; scripts/demo.sh crash >/dev/null 2>&1; sleep 60
step "oom: worker over its 64 MB limit"; scripts/demo.sh oom >/dev/null 2>&1; sleep 60
step "backup: restic gets S3 403"; scripts/demo.sh backup >/dev/null 2>&1; sleep 150
scripts/demo.sh clean >/dev/null 2>&1
step "done"
.venv/bin/python -m sentinel.report --since "$START" | tee "logs/scenario-$(date +%Y%m%d-%H%M).md"
