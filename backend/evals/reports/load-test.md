# Load test — deterministic spine (keyless)

*When:* 2026-10-08T17:23:40+05:30
*Endpoint:* `POST /demo/ask` on `http://127.0.0.1:8743` (local (same host as the generator)) — full rails→gates pipeline, offline twins
*Load:* 2838 requests at concurrency 10 over 25.08 s (minimums: 2000 requests and 25.0 s) · OK 2838 / 429 0 / errors 0
*Server:* `uvicorn careline.api.app:create_app --factory` (uvicorn 0.49.0, 1 worker, 127.0.0.1:8743), keyless (no LLM keys, no Mongo), rate limit and daily cap off
*Host CPU:* 13th Gen Intel(R) Core(TM) i5-13500H
*Generator host:* Linux-6.14.0-37-generic-x86_64-with-glibc2.41 · x86_64 · 16 logical CPUs · Python 3.13.3

| Metric | Value |
|---|---|
| Throughput (all responses) | **113.1 req/s** |
| Throughput (2xx only) | 113.1 req/s |
| Latency p50 | 77.2 ms |
| Latency p95 | 161.1 ms |
| Latency p99 | 200.7 ms |
| Latency max | 258.1 ms |
| Mean latency | 88.2 ms |

Latency percentiles are over 2xx responses only. A local run shares CPUs
between the generator and the server, so it understates server capacity
and overstates nothing about network. LLM-path requests add the
provider's latency on top (not measured here — keyless by design; see
cost_report.py for the per-request cost estimate).
