# Decision pattern: log triage (`#policy`)

Before any LLM sees an infrastructure event, Strands Decider 2B classifies **what happened**. A small routing policy in code turns that into an action. Only outages and the events Decider is unsure about reach Amazon Bedrock.

## The question

Ask about facts, not severity.

```python
QUESTION = "What happened on this server?"
CRITERIA = {
    "routine": "normal background activity, nothing changed",
    "planned_change": "an operator or an update intentionally restarted, stopped or changed something",
    "recovered_failure": "something crashed or failed but is running again",
    "ongoing_outage": "a service is down or unhealthy right now",
    "resource_risk": "disk, memory or CPU is running out or trending toward failure",
    "data_at_risk": "a backup or other data protection job failed or is overdue; nothing is down yet, but data is unprotected",
}
```

## The routing policy

| Decider says | Route |
|---|---|
| `ongoing_outage`, `data_at_risk` | escalate to Bedrock for root cause |
| anything else, but P(`ongoing_outage`) >= `outage_risk` (default 0.25) | escalate to Bedrock |
| confidence below `unsure_below` (default 0.5) | the more cautious route of the top two answers, capped at notify |
| `recovered_failure`, `resource_risk` | notify the operator |
| `planned_change` | daily digest |
| `routine` | drop |

Bedrock is paid for one thing: an outage or unprotected data that Decider names outright, or an outage that is plausible (P >= 0.25). Hesitation never buys a Bedrock call. When Decider wavers between two answers (say `planned_change` vs `recovered_failure`), the policy takes the more cautious of the two, at most a notification. Severity lives in this table, which you can change without retraining or re-prompting anything.

## Use it

```python
from decision_patterns.log_triage import LogTriage

triage = LogTriage("http://127.0.0.1:8765")
result = triage("source=netdata alarm=disk_space_usage status=WARNING used=97% estimated_full_in=2 days")
print(result.route, result.choice, result.confidence, result.reason)
```

To fit your own stack, keep the shape and change the categories and the `routes` table:

```python
LogTriage(url, criteria=my_categories, routes=my_routes, unsure_below=0.5, outage_risk=0.25, unknown_route="notify")
```

If Decider ever answers with a category that is not in `routes`, the event goes to `unknown_route` instead of raising, so nothing is lost.

## Wire it into a Strands agent

`LogTriageGate` is a Strands `InterventionHandler`. It runs Decider in `before_invocation`, before the agent's first model call. Anything that is not routed to `escalate` is denied there, so the expensive model is never called for it.

```python
from strands import Agent
from strands.models import BedrockModel
from decision_patterns.log_triage import LogTriage, LogTriageGate

gate = LogTriageGate(LogTriage("http://127.0.0.1:8765"))
agent = Agent(
    model=BedrockModel(model_id="eu.anthropic.claude-sonnet-4-6"),
    tools=[...],                      # read-only tools for the investigation
    system_prompt="You are the on-call engineer...",
    interventions=[gate],
)

result = agent("source=docker container=api steps=start>die(exit 1) outcome=down crash_reason=application error")
print(gate.last.choice, gate.last.route, gate.last.reason)
```

- Denied invocation: zero model calls (tested with a scripted model in `tests/test_agent.py`). Use `gate.last.route` to send it to your notifier, digest or the bin.
- `on_error` is `proceed`: if Decider is unreachable, the event goes to the big model instead of being lost.
- `LogTriageGate(triage, allow=("escalate", "notify"))` widens the gate if you want the agent on notify events too.

homelab-sentinel adds two more interventions next to the gate: `BedrockBudget` (`before_model_call`, a per-hour cap) and `KnownContainers` (`before_tool_call`, refuses tools on containers that do not exist). See `sentinel/guards.py`.

## What we learned building it

Measured on a 2014 Intel i5-4590, CPU only. Full tables in `docs/benchmarks.md`.

1. **Ask what happened, not how bad it is.** A 4-way severity question (noise / info / action_needed / critical) pointed the right way but never got confident (0.21-0.56), so every event escalated. The factual question above, on the same events, reached 0.61-0.96.
2. **Give pre-computed fields, not raw log lines.** `estimated_full_in=2 days` beats `Growing 2G/day` with 4.1G free.
3. **Translate tool jargon, then say what it means.** Docker reports a clean `docker restart` as `kill` then `die`, and Decider read it as a crash. Plain words alone were not enough (0.33). Short facts plus one meaning field were: `action=restart stop=clean outcome=running_again meaning=restarted on request` gave `planned_change` at 0.60. Stripping everything (`action=restart outcome=running_again`) flipped it back to `recovered_failure`. Crashes keep the detailed steps, because there the evidence matters.
4. **Do not tune wording on one example; route uncertainty instead.** The winning restart wording scored 0.60 on the fixture and 0.46 live, where the only difference was the `:latest` image tag. Confidence near a threshold is noisy, and 0.46 showed up on right and wrong answers alike. So low confidence picks the safer of the top two routes instead of escalating.
5. **One question per event on CPU.** Each extra question cost about 4 s on this machine. Factual yes/no questions (`failure`, `down`) were sharp (0.80-0.90) but the single `what happened` choice carried the same information in one pass.
6. **When nothing fits, Decider picks the blandest answer.** A failed backup came back `routine` at 0.15, because none of the five categories said "nothing is down, but data is unprotected". A factual sixth category, `data_at_risk`, fixed it (0.86). If an important event lands in `routine` with low confidence, a category is missing.
