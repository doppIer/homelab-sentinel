# homelab-sentinel

A small local model guards the door to a big one. Strands Decider 2B reads every event on a homelab server and decides what happened. Routine events and planned changes never leave the house. Only outages reach Claude on Amazon Bedrock, which reads the logs and sends the cause and the fix to Telegram.

Built for the AWS Agent Experience Hackathon, decision pattern `#policy`.

> Draft. Full write-up, setup guide and demo video are coming before submission.

## How it works

```
docker events, Netdata alarms
        |
   collectors        pre-computed facts, bursts merged, tool jargon translated
        |
   Strands Agent
     ├─ LogTriageGate      Decider 2B, local: "What happened on this server?"
     │                     not an outage -> denied, zero model calls
     ├─ BedrockBudget      model calls per hour cap
     ├─ KnownContainers    tools only touch containers that exist
     └─ Claude Sonnet 4.6  read-only tools: logs, status, stats, host resources
        |
   Telegram              outage: cause, evidence, commands to run
                         notify / daily digest / drop for everything else
```

## Numbers so far

Measured on a 2014 Intel i5-4590, CPU only. Details in [docs/benchmarks.md](docs/benchmarks.md), story in [docs/devlog.md](docs/devlog.md).

| | |
|---|---|
| Decider decision, int8 | 3.1-3.7 s, 4.0 GB RAM, $0 |
| Outage analysis, Sonnet 4.6 | about 5 s, about $0.014 |
| Fixtures routed correctly | 7 of 7 |
| Fixtures that reached Bedrock | 2 of 7 (the two real outages) |

## Reusable decision pattern

[decision_patterns/log_triage](decision_patterns/log_triage): the question, the categories, the routing policy and a Strands `InterventionHandler` you can drop into your own agent.

## Layout

| Path | What |
|---|---|
| `decision_patterns/log_triage/` | the reusable pattern |
| `sentinel/collectors/` | Docker events, Netdata alarms |
| `sentinel/analyst.py`, `guards.py`, `tools.py` | the Strands agent, its guards and read-only tools |
| `sentinel/main.py` | the loop |
| `sentinel/bench.py`, `experiment.py` | measuring Decider on fixtures |
| `scripts/demo.sh` | restart / crash / oom scenarios |
| `scripts/decider_int8.py` | int8 Decider for small machines |
| `deploy/` | systemd units |
| `tests/` | unit tests, no AWS or GPU needed |
