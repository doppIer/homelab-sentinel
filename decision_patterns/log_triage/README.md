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
| confidence below `unsure_below` (default 0.5) | the more cautious route of the top two answers |
| `recovered_failure`, `resource_risk` | notify the operator |
| `planned_change` | daily digest |
| `routine` | drop |

Bedrock is paid for one thing: outages, or events where an outage is plausible. When Decider hesitates between two cheap answers (say `planned_change` vs `recovered_failure`), the policy takes the more cautious of the two (a notification instead of a digest line) without calling the big model. Severity lives in this table, which you can change without retraining or re-prompting anything.

## Use it

```python
from decision_patterns.log_triage import LogTriage

triage = LogTriage("http://127.0.0.1:8765")
result = triage("source=netdata alarm=disk_space_usage status=WARNING used=97% estimated_full_in=2 days")
print(result.route, result.choice, result.confidence, result.reason)
```

To fit your own stack, keep the shape and change the categories and the `routes` table:

```python
LogTriage(url, criteria=my_categories, routes=my_routes, unsure_below=0.5, outage_risk=0.25)
```

## What we learned building it

Measured on a 2014 Intel i5-4590, CPU only. Full tables in `docs/benchmarks.md`.

1. **Ask what happened, not how bad it is.** A 4-way severity question (noise / info / action_needed / critical) pointed the right way but never got confident (0.21-0.56), so every event escalated. The factual question above, on the same events, reached 0.61-0.96.
2. **Give pre-computed fields, not raw log lines.** `estimated_full_in=2 days` beats `Growing 2G/day` with 4.1G free.
3. **Translate tool jargon, then say what it means.** Docker reports a clean `docker restart` as `kill` then `die`, and Decider read it as a crash. Plain words alone were not enough (0.33). Short facts plus one meaning field were: `action=restart stop=clean outcome=running_again meaning=restarted on request` gave `planned_change` at 0.60. Stripping everything (`action=restart outcome=running_again`) flipped it back to `recovered_failure`. Crashes keep the detailed steps, because there the evidence matters.
4. **Do not tune wording on one example; route uncertainty instead.** The winning restart wording scored 0.60 on the fixture and 0.46 live, where the only difference was the `:latest` image tag. Confidence near a threshold is noisy, and 0.46 showed up on right and wrong answers alike. So low confidence picks the safer of the top two routes instead of escalating.
5. **One question per event on CPU.** Each extra question cost about 4 s on this machine. Factual yes/no questions (`failure`, `down`) were sharp (0.80-0.90) but the single `what happened` choice carried the same information in one pass.
