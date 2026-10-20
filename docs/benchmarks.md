# Benchmarks

Hardware: Dell T1700 SFF, Intel i5-4590 (4C, AVX2, no bf16), 16 GB RAM, CPU only
Model: StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 (strands-decider 0.2.0, torch 2.14.1+cpu, fp32 on CPU)

| Fixture | Decision | Confidence | Warm latency |
|---|---|---|---|
| disk_raw (raw log line) | info (wrong) | 0.28 | ~3.9 s |
| disk_enriched (pre-computed fields) | action_needed | 0.56 | ~3.8 s |

Cold first request: ~7.0 s. Results identical across 3 runs.
Finding: pre-computing fields (estimated_full_in, threshold_breached) in the collector doubles confidence and fixes the decision.

## Under systemd (MemoryMax=10G, MemorySwapMax=0, Nice=10)

Resident memory: anon 7.7 GB + file cache 1.5 GB (reclaimable). disk_enriched: 3.70-3.74 s, action_needed 0.56, identical to the interactive run.

## Day 2: first live event

`docker restart mympd`, state built from the last event of the burst only:
`info` at confidence 0.18, escalated by the low-confidence rule. The exit code (143, SIGTERM) and kill signal were lost when the burst was merged.
Fix: summarize the whole burst (`steps=kill(signal 15)>die(exit 143)>...`, `outcome=running_again`, `graceful_stop=yes`). Compare with `python -m sentinel.bench tests/fixtures/docker_*.json`.

## Question design experiment v1 (`python -m sentinel.experiment tests/fixtures/*.json`)

One request per fixture, 6 questions: severity choice, severity score, 4 yes/no questions.

| Fixture | Expected | choice (conf) | score | failure | down | imminent | human |
|---|---|---|---|---|---|---|---|
| disk_enriched | action_needed | action_needed (0.56) | 1.88 | 0.39 | 0.28 | 0.48 | 0.50 |
| disk_raw | action_needed | info (0.28) | 1.60 | 0.18 | 0.12 | 0.27 | 0.23 |
| docker_app_down | critical | critical (0.41) | 1.74 | 0.80 | 0.90 | 0.42 | 0.16 |
| docker_oom_crashloop | critical | action_needed (0.22) | 1.76 | 0.70 | 0.32 | 0.39 | 0.24 |
| docker_restart_raw | noise/info | info (0.21) | 1.49 | 0.25 | 0.29 | 0.33 | 0.17 |
| docker_restart_summary | noise/info | info (0.21) | 1.42 | 0.32 | 0.12 | 0.18 | 0.17 |
| docker_unhealthy | action_needed/critical | critical (0.35) | 1.90 | 0.82 | 0.90 | 0.54 | 0.31 |

Latency: 26-29 s for 6 questions (about 4.3 s per question on this CPU; no batching win).

Findings:
1. Factual yes/no questions separate cleanly (failure, down: 0.80-0.90 vs 0.12-0.32). Judgment questions do not (`human` = 0.16 for a service that is down).
2. The ordinal score compresses everything into 1.4-1.9.
3. The 4-way severity choice points the right way but never gets confident, so every event escalated.
4. On CPU each extra question costs about as much as the first one, so ask few.

## Question design experiment v2 (`python -m sentinel.experiment --v2 tests/fixtures/*.json`)

3 questions per request: factual choice "What happened on this server?" plus failure/down yes/no. ~9.5 s per request.

| Fixture | Expected | what happened (conf) | failure | down |
|---|---|---|---|---|
| disk_enriched | resource_risk | resource_risk (0.96) | 0.39 | 0.28 |
| disk_raw | resource_risk | resource_risk (0.82) | 0.18 | 0.12 |
| docker_app_down | ongoing_outage | ongoing_outage (0.77) | 0.80 | 0.90 |
| docker_oom_crashloop | recovered_failure | recovered_failure (0.63) | 0.70 | 0.32 |
| docker_restart_raw | planned_change | planned_change (0.61) | 0.25 | 0.29 |
| docker_restart_summary (kill/die words) | planned_change | recovered_failure (0.33) | 0.32 | 0.12 |
| docker_unhealthy | ongoing_outage | ongoing_outage (0.90) | 0.82 | 0.90 |

Finding: the factual choice is confident (0.61-0.96) where the severity choice was not (0.21-0.56). The one miss was caused by our own summary using Docker's words `kill` and `die` for a clean restart; the collector now uses plain words for clean stops. Adopted: single factual question per event.

## How to describe a clean restart (`python -m sentinel.bench tests/fixtures/restart_variants/*.json`)

Same `docker restart mympd`, four wordings, question v2.

| Variant | State (after container/image) | Choice | Conf | Route |
|---|---|---|---|---|
| a_current | `steps=stop_requested(15)>exited_cleanly>stopped>started>restart_command outcome=running_again meaning=... exit_codes=143 graceful_stop=yes ...` | planned_change | 0.33 | escalate |
| b_compact | `action=restart stop=clean outcome=running_again starts_last_hour=1 ...` | planned_change | 0.41 | escalate |
| c_compact_meaning | b + `meaning=restarted on request` | planned_change | 0.60 | digest |
| d_minimal | `action=restart outcome=running_again` | recovered_failure | 0.46 | escalate |

Finding: short facts plus one explicit meaning field wins. Too little context (d) flips the answer: a bare "restart" reads like recovery from a crash. Adopted c for clean bursts; crash bursts keep the detailed steps because the evidence matters there (their fixtures are unchanged).
