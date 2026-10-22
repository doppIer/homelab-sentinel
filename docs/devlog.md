# Devlog

Build log for the AWS Agent Experience Hackathon (Strands Decider 2B, pattern `#policy`). Numbers come from `docs/benchmarks.md`; raw experiment output is in `docs/data/`.

Hardware: Dell T1700 SFF, Intel i5-4590 (2014, 4 cores, AVX2, no bf16), 16 GB RAM, CPU only, 17 Docker containers already running.

## Day 1, Monday 19 October

- Repo created (private until submission).
- Strands Decider 2B (`StrandsAgents/strands-decider-2B-qwen3.5-v1-2610`) served locally on CPU. Warm latency about 3.8 s, cold 7.0 s. The published 115 ms is on an RTX 3090.
- First finding: a raw log line (`df: 97% used ... Growing 2G/day`) was classified `info` at 0.28. The same facts as computed fields (`estimated_full_in=2 days`) gave `action_needed` at 0.56. Collectors pre-compute fields.
- Decider upcasts the model to fp32 on this CPU (about 8 GB RSS). Starting a second copy by mistake swap-thrashed the server for 10 minutes; no OOM kill fired because swap was free. Decider moved to a systemd unit with `MemoryMax` and `MemorySwapMax=0`, so it dies instead of freezing the host.
- Bedrock: a brand-new AWS account returned `Operation not allowed` for every model. Moved to the existing account under a dedicated IAM user that can only invoke Bedrock.
- Collector written: Docker events (healthcheck `exec_*` noise filtered at the source) and Netdata alarm transitions, with burst coalescing.

## Day 2, Tuesday 20 October

- Telegram notifications with a per-service cooldown; escalations are never suppressed.
- First live event (`docker restart mympd`): the coalescer kept only the last event, lost the exit code, and Decider answered `info` at 0.18. Bursts are now summarized.
- Question design, measured on 7 fixtures:
  - 4-way severity choice (noise / info / action_needed / critical): right direction, confidence 0.21-0.56, every event escalated.
  - Ordinal score: everything squeezed into 1.4-1.9.
  - Yes/no questions: factual ones sharp (failure, down: 0.80-0.90), judgment ones wrong (`human` = 0.16 for a service that was down). Each extra question costs about 4 s on CPU.
  - Adopted: one factual question, "What happened on this server?" (routine, planned_change, recovered_failure, ongoing_outage, resource_risk). Confidence 0.61-0.96. Severity lives in a routes table in code.
- Docker's own words misled Decider: a clean restart arrives as `kill` then `die`. Four wordings tested; short facts plus one `meaning=` field won (0.33 -> 0.60). Stripping context entirely flipped the answer to `recovered_failure`.
- Live, the same restart scored 0.46 instead of 0.60 (only difference: the `:latest` image tag). A threshold cannot separate right from wrong at that level, so low confidence now takes the safer of the top-two routes. Bedrock is reserved for `ongoing_outage` or P(outage) >= 0.25.
- Strands agent on Bedrock, gated by Decider:
  - `LogTriageGate` (InterventionHandler, `before_invocation`) denies everything that is not an outage. Denied invocations make zero model calls (verified with a fake model).
  - `BedrockBudget` (`before_model_call`) caps model calls per hour, so a bug cannot drain the backup account's spend cap.
  - `KnownContainers` (`before_tool_call`) refuses tool calls on containers that do not exist.
  - Read-only tools; secrets redacted before logs reach Bedrock.
  - If Bedrock fails, the outage alert still goes out with the error.
- First live analysis (`scripts/demo.sh crash`): Decider 0.83 `ongoing_outage` in 3.9 s, Sonnet 4.6 found the cause from the container logs and status in about 4.8 s for about $0.014.
- Model A/B on crash and OOM: both Haiku 4.5 and Sonnet 4.6 diagnosed correctly, but Haiku's OOM fix command fails (it raises `--memory` without `--memory-swap`). Analysis stays on Sonnet; the daily digest uses Haiku.
- Anthropic models on Bedrock needed a use-case form and a one-time AWS Marketplace subscription (temporary `aws-marketplace:Subscribe` on the IAM user, removed afterwards).
- Memory: int8 dynamic quantization of every Linear outside the LoRA adapters, plus a bf16 embedding table. 7.7 GB -> 4.0 GB, slightly faster, same top answer on 7/7 fixtures, lower confidence (the safer-route rule absorbs it). Deployed as `deploy/strands-decider-int8.service`. The server got about 3.5 GB of RAM back.

## Day 2, evening

- Daily digest: last 24 h of decisions, dropped events left out, outages first, Haiku 4.5 writes a plain-text Telegram report at 21:00 Riga time (systemd timer). A quiet day makes no Bedrock call. First real digest: 937 + 208 tokens.
- Sentinel runs as `homelab-sentinel.service` (Restart=always, MemoryMax=1G, about 70 MB in practice).
- Lifeboat backups as a third source, read through `systemctl show` (no restic password, no root): failed run, no success within RPO + 1 h, recovery. `backup_logs` tool reads only lifeboat-backup* journals.
- Decider put a failed backup in `routine` (0.15): none of the five categories said "nothing is down, but data is unprotected". Added `data_at_risk`; all 10 fixtures now land in the expected category, 4 of 10 go to Bedrock.
- An unsure recovery (runner-up `data_at_risk`) was sent to Bedrock by the safer-route rule. Hesitation is now capped at notify.
- First live backup alert: correct diagnosis, but it suggested a timer that does not exist (our collector said `timer=inactive` for a missing unit) and invented a config path. Fixed the collector, grounded the prompt in the real lifeboat paths, and cut any preamble before `Cause:`.

## Day 4, Thursday 22 October

- Traffic report (`sentinel/report.py`) over the decision log since the final policy went live (Tue 17:28 UTC). About 40 hours, zero real events: nothing crashed, restarted, alarmed or failed a backup. The 21:00 digest on Wednesday saw zero events and made no Bedrock call.
- Checked that zero meant quiet and not broken: `docker restart mympd` reached the log 18 s later as `planned_change` 0.72, route digest.
- Found three orphaned `docker events` processes left by earlier manual runs. The child now gets SIGTERM when its parent dies (`PR_SET_PDEATHSIG`).
- `scripts/scenario.sh`: a 10-minute live run (planned restarts and a stop/start on real containers, then crash, OOM and failed backup) followed by the report for exactly that window.
- Scenario result: 9 events in 11 minutes. 5 handled by Decider alone, 4 to Bedrock, $0.087 actual vs $0.195 if all had gone to Bedrock. All three real failures escalated and diagnosed correctly. One false escalation: `docker stop homarr` (ongoing_outage 0.59); Sonnet confirmed a clean intentional stop for $0.025. dashdot ignored SIGTERM and was SIGKILLed at the stop timeout; Decider called it `recovered_failure` (notify).
- README, setup guide, demo script and article.
- Review before going public found four gaps, all fixed with tests:
  - Secret redaction missed names with underscores (`AWS_SECRET_ACCESS_KEY`, `POSTGRES_PASSWORD`, `DB_PASS`), passwords inside URLs and Basic auth. Redaction now covers those plus known token shapes (AWS key ids, GitHub, OpenAI, Slack, Telegram), runs before tool output is cut, and runs again on everything sent to Telegram.
  - A category missing from the routes table raised `KeyError`; it now goes to `unknown_route` (notify). If Decider itself fails without Bedrock configured, the event is sent unclassified instead of dropped.
  - Escalations had no Telegram cooldown, so a crash loop would alert on every burst. Now once per 15 minutes per service, with the number of suppressed repeats in the next alert.
