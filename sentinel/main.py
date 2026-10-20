import argparse
import json
import os
import queue
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

from decision_patterns.log_triage import LogTriage
from sentinel.coalesce import Coalescer
from sentinel.collectors.docker_events import DockerEvents
from sentinel.collectors.netdata import NetdataAlarms
from sentinel.notify import Telegram

ICONS = {"drop": ".", "digest": "i", "notify": "!", "escalate": "!!"}


def pump(source, inbox):
    for event in source.stream():
        inbox.put(event)


def record(path, event, result):
    row = {
        "ts": event.ts,
        "source": event.source,
        "subject": event.subject,
        "kind": event.kind,
        "state": event.state,
        "choice": result.choice,
        "confidence": round(result.confidence, 4),
        "probabilities": result.probabilities,
        "route": result.route,
        "reason": result.reason,
        "latency_ms": round(result.latency_ms, 1),
    }
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-docker", action="store_true")
    parser.add_argument("--no-netdata", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    triage = LogTriage(
        os.getenv("DECIDER_URL", "http://127.0.0.1:8765"),
        unsure_below=float(os.getenv("UNSURE_BELOW", "0.5")),
        outage_risk=float(os.getenv("OUTAGE_RISK", "0.25")),
    )
    log_path = Path(os.getenv("DECISION_LOG", "logs/decisions.jsonl"))
    log_path.parent.mkdir(parents=True, exist_ok=True)

    sources = []
    summarizers = {}
    if not args.no_docker:
        docker = DockerEvents()
        sources.append(docker)
        summarizers["docker"] = docker.summarize
    if not args.no_netdata:
        sources.append(NetdataAlarms(os.getenv("NETDATA_URL", "http://127.0.0.1:19999"), int(os.getenv("NETDATA_INTERVAL", "60"))))

    inbox = queue.Queue()
    for source in sources:
        threading.Thread(target=pump, args=(source, inbox), daemon=True).start()

    coalescer = Coalescer(window=float(os.getenv("COALESCE_SECONDS", "15")), summarizers=summarizers)
    telegram = Telegram.from_env()
    print(f"sentinel up: {len(sources)} sources, telegram={'on' if telegram else 'off'}, decisions -> {log_path}", flush=True)

    while True:
        try:
            coalescer.add(inbox.get(timeout=1))
        except queue.Empty:
            pass
        for event in coalescer.ready():
            try:
                result = triage(event.state)
            except Exception as e:
                print(f"[decider error] {type(e).__name__}: {e} | {event.state}", flush=True)
                continue
            record(log_path, event, result)
            stamp = time.strftime("%H:%M:%S", time.localtime(event.ts))
            print(f"{stamp} [{ICONS[result.route]:>2}] {result.route:<8} {result.choice:<13} {result.confidence:.2f} {result.latency_ms:>6.0f}ms {event.source}/{event.subject} {event.kind} | {result.reason}", flush=True)
            if telegram:
                try:
                    telegram.send_decision(event, result)
                except Exception as e:
                    print(f"[telegram error] {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    main()
