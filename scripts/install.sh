#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")/.."
pkill -f "sentinel.main" || true
sudo cp deploy/homelab-sentinel.service deploy/sentinel-digest.service deploy/sentinel-digest.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now homelab-sentinel.service sentinel-digest.timer
systemctl --no-pager status homelab-sentinel | grep -E "Active|Memory"
systemctl --no-pager list-timers sentinel-digest.timer | head -2
