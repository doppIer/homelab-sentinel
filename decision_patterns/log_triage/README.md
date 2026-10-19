# Decision pattern: log triage (`#policy`)

Classify an incoming infrastructure event into one of four severities with Strands Decider 2B before any LLM sees it. Only the uncertain or dangerous events reach Amazon Bedrock.

## The question

```python
QUESTION = "How should a homelab operator treat this event?"
CRITERIA = {
    "noise": "routine log line, nothing to do",
    "info": "worth a line in the daily summary, no action",
    "action_needed": "operator should fix it within a day",
    "critical": "service down, data at risk, or failure within hours",
}
```

## The routing policy

| Decider says | Route |
|---|---|
| `critical` | escalate to Bedrock |
| confidence below `escalate_below` (default 0.5) | escalate to Bedrock |
| `noise` or `info`, but P(action_needed) + P(critical) >= `risk_mass` (default 0.35) | escalate to Bedrock |
| `action_needed` | notify the operator |
| `info` | daily digest |
| `noise` | drop |

The risk-mass rule exists because missing a critical event costs more than a false alarm. A confident `info` with a fat tail on the dangerous side still goes to the big model.

## Use it

```python
from decision_patterns.log_triage import LogTriage

triage = LogTriage("http://127.0.0.1:8765")
result = triage("source=netdata alarm=disk_space_usage status=WARNING value=97% estimated_full_in=2 days")
print(result.route, result.choice, result.confidence, result.reason)
```

## Lesson learned

Give Decider pre-computed fields, not raw log lines. On our hardware the raw line `df: /dev/sda1 97% used ... Growing 2G/day` was classified `info` at confidence 0.28. The same facts as `used=97% estimated_full_in=2 days threshold_breached=yes` gave `action_needed` at 0.56. See `docs/benchmarks.md`.
