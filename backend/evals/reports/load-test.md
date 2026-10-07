# Load test — deterministic spine (keyless)

*Endpoint:* `POST /demo/ask` (full rails→gates pipeline, offline twins)
*Requests:* 300 at concurrency 10 · OK 300 / errors 0 · wall 0.53s

| Metric | Value |
|---|---|
| Throughput | **571.2 req/s** |
| Latency p50 | 16.1 ms |
| Latency p95 | 28.8 ms |
| Latency p99 | 42.1 ms |
| Latency max | 48.3 ms |
| Mean latency | 17.2 ms |

LLM-path requests add the provider's latency on top (not measured here —
keyless by design; see cost_report.py for the per-call cost estimate).
