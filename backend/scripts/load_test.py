"""Load test — the throughput/latency numbers for the README.

Stdlib-only (threads + urllib) so it runs anywhere without a Locust install.
Drives the *real* pipeline: POST /demo/ask through the full deterministic
spine (rails → retrieval → reasoner → verifier → gates) on the keyless demo
patient, no API key involved — so the numbers measure CareLine's own cost,
not the LLM provider's variance. LLM-path latency is additive on top of this
(see the cost report for the per-call estimate).

    cd backend
    uvicorn careline.api.app:create_app --factory --port 8010 &
    python -m scripts.load_test --url http://127.0.0.1:8010 \
        --markdown evals/reports/load-test.md

Owner: Naresh (scope ``services``).
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

_QUESTIONS = [
    "What is the dose of my paracetamol?",
    "Can I eat spicy food tonight?",
    "When is my follow-up review?",
    "Am I allergic to penicillin?",
    "Who won the cricket match?",  # redirect path
]


def _one_request(url: str, question: str) -> tuple[int, float]:
    payload = json.dumps({"question": question}).encode()
    req = urllib.request.Request(
        url + "/demo/ask", data=payload, headers={"Content-Type": "application/json"}
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    return status, (time.perf_counter() - start) * 1000.0


def run(url: str, *, total: int, concurrency: int) -> dict:
    wall_start = time.perf_counter()
    results: list[tuple[int, float]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(_one_request, url, _QUESTIONS[i % len(_QUESTIONS)])
            for i in range(total)
        ]
        for f in futures:
            results.append(f.result())
    wall = time.perf_counter() - wall_start

    latencies = sorted(ms for _status, ms in results)
    ok = sum(1 for status, _ in results if 200 <= status < 300)
    pct = lambda q: latencies[min(len(latencies) - 1, int(q * len(latencies)))]
    return {
        "requests": total,
        "concurrency": concurrency,
        "ok": ok,
        "errors": total - ok,
        "rps": round(total / wall, 1),
        "latency_ms_p50": round(pct(0.50), 1),
        "latency_ms_p95": round(pct(0.95), 1),
        "latency_ms_p99": round(pct(0.99), 1),
        "latency_ms_max": round(latencies[-1], 1),
        "latency_ms_mean": round(statistics.mean(latencies), 1),
        "wall_seconds": round(wall, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8010")
    parser.add_argument("--total", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--markdown", help="write the results table here")
    args = parser.parse_args()

    # Warm-up (first hit builds caches/app state).
    _one_request(args.url, "hello")

    r = run(args.url, total=args.total, concurrency=args.concurrency)
    lines = [
        "# Load test — deterministic spine (keyless)",
        "",
        f"*Endpoint:* `POST /demo/ask` (full rails→gates pipeline, offline twins)",
        f"*Requests:* {r['requests']} at concurrency {r['concurrency']} · "
        f"OK {r['ok']} / errors {r['errors']} · wall {r['wall_seconds']}s",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Throughput | **{r['rps']} req/s** |",
        f"| Latency p50 | {r['latency_ms_p50']} ms |",
        f"| Latency p95 | {r['latency_ms_p95']} ms |",
        f"| Latency p99 | {r['latency_ms_p99']} ms |",
        f"| Latency max | {r['latency_ms_max']} ms |",
        f"| Mean latency | {r['latency_ms_mean']} ms |",
        "",
        "LLM-path requests add the provider's latency on top (not measured here —",
        "keyless by design; see cost_report.py for the per-call cost estimate).",
    ]
    table = "\n".join(lines) + "\n"
    if args.markdown:
        from pathlib import Path

        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(table, encoding="utf-8")
    print(json.dumps(r, indent=2))
    print(table)
    return 0 if r["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
