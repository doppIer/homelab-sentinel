#!/usr/bin/env bash
set -u
case "${1:-}" in
  restart)
    docker restart mympd
    ;;
  crash)
    docker rm -f sentinel-demo-api >/dev/null 2>&1
    docker run --name sentinel-demo-api alpine:3.20 sh -c 'echo "api starting"; echo "FATAL: could not connect to database at db:5432: connection refused" >&2; exit 1'
    ;;
  oom)
    docker rm -f sentinel-demo-worker >/dev/null 2>&1
    docker run --name sentinel-demo-worker --memory 64m --memory-swap 64m alpine:3.20 sh -c 'echo "loading index into memory"; head -c 500m /dev/zero | tail'
    ;;
  clean)
    docker rm -f sentinel-demo-api sentinel-demo-worker >/dev/null 2>&1
    echo "demo containers removed"
    ;;
  *)
    echo "usage: scripts/demo.sh restart|crash|oom|clean"
    ;;
esac
