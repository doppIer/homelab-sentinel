# Benchmarks

Hardware: Dell T1700 SFF, Intel i5-4590 (4C, AVX2, no bf16), 16 GB RAM, CPU only
Model: StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 (strands-decider 0.2.0, torch 2.14.1+cpu, fp32 on CPU)

| Fixture | Decision | Confidence | Warm latency |
|---|---|---|---|
| disk_raw (raw log line) | info (wrong) | 0.28 | ~3.9 s |
| disk_enriched (pre-computed fields) | action_needed | 0.56 | ~3.8 s |

Cold first request: ~7.0 s. Results identical across 3 runs.
Finding: pre-computing fields (estimated_full_in, threshold_breached) in the collector doubles confidence and fixes the decision.
