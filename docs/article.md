# A 2B model on a 2014 CPU decides when my homelab gets to call Claude

Tags: `#strands-decider-2b` `#policy`

Code: https://github.com/doppIer/homelab-sentinel · Demo video: https://youtu.be/j9PYNzrp5UE

My home server is a Dell T1700 from 2014: an Intel i5-4590, 16 GB of RAM, no GPU, and 17 Docker containers. It produces a steady trickle of events. Containers restart after updates, Netdata complains about disk space, restic backups run every six hours. Almost none of it needs me. Once in a while something breaks and I want to know why, quickly, ideally with the command that fixes it.

A frontier model is good at the "why". Claude on Amazon Bedrock can read a container's logs and tell me the database is unreachable. But sending every event to it is wasteful, and most of the answers would be "this is fine". I wanted something small and local to look at each event first and decide whether the big model should see it at all.

That is what homelab-sentinel does. Strands Decider 2B runs locally on that old CPU and answers one question about every event. A small routing table in code turns the answer into an action. Only outages and failed backups reach Claude.

## The decision

The first version asked Decider how bad an event was: noise, info, action needed or critical. The answers pointed the right way and never got confident. On seven test events the confidence stayed between 0.21 and 0.56, so my low-confidence rule escalated everything, which defeated the point.

So I stopped asking about severity and asked about facts:

```python
QUESTION = "What happened on this server?"
CRITERIA = {
    "routine":           "normal background activity, nothing changed",
    "planned_change":    "an operator or an update intentionally restarted, stopped or changed something",
    "recovered_failure": "something crashed or failed but is running again",
    "ongoing_outage":    "a service is down or unhealthy right now",
    "resource_risk":     "disk, memory or CPU is running out or trending toward failure",
    "data_at_risk":      "a backup or other data protection job failed or is overdue; nothing is down yet, but data is unprotected",
}
```

On the same events, confidence went to 0.61-0.96. Severity moved into code, where I can read it and change it:

| Decider says | Route |
|---|---|
| `ongoing_outage`, `data_at_risk` | escalate: Claude Sonnet 4.6 investigates, Telegram gets cause, evidence and fix |
| anything else with P(`ongoing_outage`) >= 0.25 | escalate |
| confidence < 0.5 | the more cautious of the top two routes, at most notify |
| `recovered_failure`, `resource_risk` | notify: a short Telegram message |
| `planned_change` | digest: one Haiku 4.5 summary at 21:00 |
| `routine` | drop: decision log only |

This is the `#policy` pattern. Decider classifies; a policy I wrote decides what each class costs. If I decide tomorrow that a recovered crash deserves a root-cause analysis, I change one line in a dictionary. Nothing gets retrained or re-prompted.

Two rules in that table came from mistakes. The 0.25 outage threshold exists because a wrong answer with a real chance of being an outage should still be investigated. The "at most notify" cap exists because the first version of the unsure rule sent a recovered-backup test event to Bedrock just because Decider hesitated between two answers. Hesitation should get my attention, and that does not require a paid model call.

## Wiring it into a Strands agent

The gate is a Strands `InterventionHandler`. It runs Decider in `before_invocation`, before the agent makes its first model call, and denies the invocation unless the route is `escalate`:

```python
class LogTriageGate(InterventionHandler):
    name = "log-triage-gate"

    def __init__(self, triage, allow=("escalate",)):
        self.triage = triage
        self.allow = set(allow)
        self.last = None

    @property
    def on_error(self):
        return "proceed"

    def before_invocation(self, event, **kwargs):
        result = self.triage(last_user_text(event.messages))
        self.last = result
        if result.route in self.allow:
            return Proceed(reason=result.reason)
        return Deny(reason=f"{result.route}: {result.reason}")
```

A denied invocation makes zero model calls. I checked that with a scripted fake model in the tests, because the whole cost argument rests on it. `on_error` is `proceed`: if Decider is down, the event goes to Claude instead of disappearing.

The agent has two more interventions. `BedrockBudget` runs in `before_model_call` and caps model calls per hour, so a bug in my loop cannot run up a bill. `KnownContainers` runs in `before_tool_call` and refuses tool calls on container names that do not exist. The tools themselves are read-only (logs, status, stats, host resources, the backup journal) and secrets are redacted before any log line leaves the machine.

## What I learned about asking a small model

**Give it computed facts, not raw logs.** A raw `df` line with "growing 2G/day" came back as `info` at 0.28. The same facts as `estimated_full_in=2 days` came back right at 0.56. The collectors do the arithmetic now.

**Translate tool jargon.** Docker reports a clean `docker restart` as `kill` followed by `die`. Decider read that as a crash. Plain words got it to 0.33. Plain words plus one field that says what it means, `meaning=restarted on request`, got it to 0.60. Removing all context flipped it back to "recovered from a crash".

**Don't tune wording on one example.** The winning restart wording scored 0.60 on my fixture and 0.46 live, and the only difference was an image tag. Confidence near a threshold is noisy, and 0.46 showed up on right and wrong answers alike. Instead of tuning the prompt further, I made the policy handle uncertainty by taking the safer of the top two routes.

**When nothing fits, the model picks the blandest answer.** My first backup-failure test came back `routine` at 0.15. None of my five categories described "nothing is down, but data is unprotected". Adding `data_at_risk` as a sixth category fixed it at 0.86. Now, if an important event lands in `routine` with low confidence, I look for a missing category before touching anything else.

**On a CPU, ask one question.** Each extra question cost about four seconds on this machine. Factual yes/no questions were sharp, but the single "what happened" choice carried the same information in one pass.

## Running a 2B model on a ten-year-old CPU

This CPU has no bf16 support, so Decider upcasts to fp32 and takes 7.7 GB of RAM. On a 16 GB server that already runs everything else, that is a lot. On the first day I started a second copy by mistake and the server swap-thrashed until I power-cycled it. Since then Decider runs under systemd with `MemoryMax` and `MemorySwapMax=0`, so it gets killed instead of freezing the host.

Then I quantized it. Every linear layer outside the LoRA adapters became a dynamic int8 layer, and the 509M-parameter embedding table went to bf16 with a hook that casts lookups back to fp32. Resident memory went from 7.7 GB to 4.0 GB and latency from 3.7-4.4 s to 3.1-3.7 s. All seven test events kept the same top answer. Confidence dropped a little, and the unsure rule absorbs that: the one route that changed moved toward caution.

## Numbers

| | |
|---|---|
| Decider decision | 3.1-3.7 s on the i5-4590, 4.0 GB RAM, $0 |
| Test events in the expected category | 10 of 10 |
| Test events sent to Bedrock | 4 of 10: the two outages and the two backup problems |
| Outage analysis with Sonnet 4.6 | about 5 s, $0.014-0.026 |
| About 40 hours of real traffic | 0 events, 0 Bedrock calls; the evening digest skipped Bedrock too |
| 10-minute live scenario | 9 events, 5 handled by Decider alone, $0.087 actual vs $0.195 if every event had gone to Bedrock |

The 40 quiet hours are a real result. Nothing on the server crashed, restarted or ran out of space, so the gate had nothing to look at and the bill was zero. To see the whole pipeline under load, I ran a ten-minute scenario on the live server: three restarts and a stop/start of real containers, then a crashing container, an out-of-memory container and a failing backup.

| Event | Decider (top two) | Route |
|---|---|---|
| `docker restart mympd` | planned_change 0.76 | digest |
| `docker restart dozzle` | planned_change 0.46, recovered_failure 0.36 | notify (unsure) |
| `docker restart dashdot` | recovered_failure 0.50 | notify |
| `docker stop homarr` | ongoing_outage 0.59 | escalate, Bedrock |
| `docker start homarr` | routine 0.50 | digest |
| container that cannot reach its database | ongoing_outage 0.60 | escalate, Bedrock |
| container over its 64 MB limit | ongoing_outage 0.46 | escalate, Bedrock |
| Netdata `mem.oom_kill` alarm (from the same OOM) | resource_risk 0.68 | notify |
| restic backup fails with S3 403 | data_at_risk 0.87 | escalate, Bedrock |

All three real failures reached Sonnet, and all three diagnoses were right: the unreachable database at `db:5432`, the 64 MB limit (and that the host itself had 7.7 GB free), and the S3 write key in the backup config. The two harmless restarts and the start stayed local.

Two results surprised me, and I left both in. dashdot did not exit on SIGTERM, so Docker killed it with SIGKILL after the stop timeout. Decider called that a recovered failure. I think that is fair, since the container does not shut down cleanly, and it cost a Telegram message, not a model call.

`docker stop homarr` went to Bedrock. From the event alone, a container I stopped on purpose looks exactly like a service that is down, and Decider cannot know whether I want it to stay down. Sonnet read the logs and replied that it was an intentional, clean stop with exit code 0, which cost 2.5 cents. That is the one false escalation in the run. I would rather pay that than teach the gate to ignore stopped containers.

I also compared Haiku 4.5 and Sonnet 4.6 for the outage analysis. Both found the causes. Haiku's fix for the out-of-memory case raised `--memory` without `--memory-swap`, which Docker rejects for that container. The analysis is the message I copy commands from at 3 a.m., so it stays on Sonnet. The digest only summarizes and suggests nothing to run, so it uses Haiku.

## Use the pattern

The decision is packaged on its own in `decision_patterns/log_triage`: the question, the categories, the routing policy and the `InterventionHandler`. To use it for your own infrastructure, keep the shape and replace the categories and the routes table:

```python
from decision_patterns.log_triage import LogTriage, LogTriageGate

triage = LogTriage("http://127.0.0.1:8765", criteria=my_categories, routes=my_routes)
agent = Agent(model=BedrockModel(model_id="eu.anthropic.claude-sonnet-4-6"),
              tools=my_read_only_tools, interventions=[LogTriageGate(triage)])
```
