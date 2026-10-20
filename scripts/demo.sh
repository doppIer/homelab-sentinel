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
  backup)
    sudo systemctl reset-failed lifeboat-backup-demo.service >/dev/null 2>&1
    sudo systemd-run --unit=lifeboat-backup-demo --description="homelab-lifeboat restic backup (demo)" -p Type=oneshot \
      sh -c 'echo "using parent snapshot 3f2a91c0"; echo "Fatal: unable to save snapshot: s3.PutObject: Access Denied (403): the write-only key no longer matches the bucket policy" >&2; exit 1'
    echo "demo backup failed on purpose; sentinel polls every 2 minutes"
    ;;
  clean)
    docker rm -f sentinel-demo-api sentinel-demo-worker >/dev/null 2>&1
    sudo systemctl reset-failed lifeboat-backup-demo.service >/dev/null 2>&1
    echo "demo containers and demo backup unit removed"
    ;;
  *)
    echo "usage: scripts/demo.sh restart|crash|oom|backup|clean"
    ;;
esac
