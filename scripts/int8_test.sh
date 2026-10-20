#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.."
case "${1:-}" in
  start)
    sudo systemctl stop strands-decider
    sudo systemd-run --unit=decider-int8 --uid="$(id -u)" --gid="$(id -g)" \
      -p WorkingDirectory="$PWD" -p MemoryMax=10G -p MemorySwapMax=0 -p Nice=10 \
      -E HF_HUB_OFFLINE=1 -E HOME="$HOME" \
      "$PWD/.venv/bin/python" "$PWD/scripts/decider_int8.py" 8766 "${@:2}"
    echo "started; follow with: journalctl -u decider-int8 -f"
    ;;
  mem)
    systemctl status decider-int8 --no-pager | grep -E "Active|Memory"
    grep -E "^(anon|file) " /sys/fs/cgroup/system.slice/decider-int8.service/memory.stat | awk '{printf "%s %.2f GB\n", $1, $2/1073741824}'
    ;;
  stop)
    sudo systemctl stop decider-int8
    sudo systemctl start strands-decider
    echo "int8 stopped, fp32 decider started again"
    ;;
  *)
    echo "usage: scripts/int8_test.sh start [--embed-bf16]|mem|stop"
    ;;
esac
