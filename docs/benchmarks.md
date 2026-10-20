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
