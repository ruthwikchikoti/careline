"""Load test — the throughput/latency numbers for the README.

Stdlib-only (threads + urllib) so it runs anywhere without a Locust install.
Drives the *real* pipeline: POST /demo/ask through the full deterministic
spine (rails → retrieval → reasoner → verifier → gates) on the keyless demo
patient, no API key involved — so the numbers measure CareLine's own cost,
not the LLM provider's variance. LLM-path latency is additive on top of this
(see the cost report for the per-request estimate).

A run is meaningful by default: it keeps going until BOTH ``--total``
requests (default 2000) are sent AND ``--duration`` seconds (default 20) have
elapsed, after a warm-up. The report records the target (local or remote),
concurrency, and the load generator's host/CPU/Python so a number is never
quoted without its conditions. 429s from the budget guard are counted
separately from errors (they are the rate limiter working, not a failure).

    cd backend
    uvicorn careline.api.app:create_app --factory --port 8010 &
    python -m scripts.load_test --markdown evals/reports/load-test.md
    python -m scripts.load_test --url https://careline.example.org --concurrency 20 \
        --duration 60 --header "Authorization: Bearer <token>"
    CARELINE_LOAD_TEST_URL=https://... python -m scripts.load_test

Owner: Naresh (scope ``services``).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

_QUESTIONS = [
    "What is the dose of my paracetamol?",
    "Can I eat spicy food tonight?",
    "When is my follow-up review?",
    "Am I allergic to penicillin?",
    "Who won the cricket match?",  # redirect path
    "I have severe chest pain right now",  # red-flag rail path
]


def _one_request(url: str, path: str, question: str, headers: dict[str, str]) -> tuple[int, float]:
    payload = json.dumps({"question": question}).encode()
    req = urllib.request.Request(
        url.rstrip("/") + path,
        data=payload,
        headers={"Content-Type": "application/json", **headers},
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except Exception:  # connection refused / timeout / reset
        status = 0
    return status, (time.perf_counter() - start) * 1000.0


def run(
    url: str,
    *,
    total: int,
    duration_s: float,
    concurrency: int,
    path: str = "/demo/ask",
    headers: dict[str, str] | None = None,
) -> dict:
    """Send until >= ``total`` requests AND >= ``duration_s`` seconds have passed."""
    headers = headers or {}
    results: list[tuple[int, float]] = []
    lock = threading.Lock()
    counter = {"sent": 0}
    wall_start = time.perf_counter()

    def worker() -> None:
        while True:
            with lock:
                elapsed = time.perf_counter() - wall_start
                if counter["sent"] >= total and elapsed >= duration_s:
                    return
                i = counter["sent"]
                counter["sent"] += 1
            r = _one_request(url, path, _QUESTIONS[i % len(_QUESTIONS)], headers)
            with lock:
                results.append(r)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for _ in range(concurrency):
            pool.submit(worker)
    wall = time.perf_counter() - wall_start

    latencies = sorted(ms for status, ms in results if 200 <= status < 300)
    ok = len(latencies)
    throttled = sum(1 for status, _ in results if status == 429)
    pct = lambda q: latencies[min(len(latencies) - 1, int(q * len(latencies)))] if latencies else 0.0
    return {
        "target": url,
        "target_is_remote": urllib.parse.urlparse(url).hostname not in ("127.0.0.1", "localhost"),
        "path": path,
        "requests": len(results),
        "concurrency": concurrency,
        "min_requests": total,
        "min_duration_s": duration_s,
        "ok": ok,
        "throttled_429": throttled,
        "errors": len(results) - ok - throttled,
        "rps": round(len(results) / wall, 1) if wall else 0.0,
        "ok_rps": round(ok / wall, 1) if wall else 0.0,
        "latency_ms_p50": round(pct(0.50), 1),
        "latency_ms_p95": round(pct(0.95), 1),
        "latency_ms_p99": round(pct(0.99), 1),
        "latency_ms_max": round(latencies[-1], 1) if latencies else 0.0,
        "latency_ms_mean": round(statistics.mean(latencies), 1) if latencies else 0.0,
        "wall_seconds": round(wall, 2),
    }


def host_info() -> dict:
    """Load-generator conditions (no hostname — it can identify a person's machine)."""
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
    }


def render(r: dict, host: dict) -> str:
    where = "remote" if r["target_is_remote"] else "local (same host as the generator)"
    lines = [
        "# Load test — deterministic spine (keyless)",
        "",
        f"*When:* {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"*Endpoint:* `POST {r['path']}` on `{r['target']}` ({where}) — full "
        "rails→gates pipeline, offline twins",
        f"*Load:* {r['requests']} requests at concurrency {r['concurrency']} over "
        f"{r['wall_seconds']} s (minimums: {r['min_requests']} requests and "
        f"{r['min_duration_s']} s) · OK {r['ok']} / 429 {r['throttled_429']} / "
        f"errors {r['errors']}",
        f"*Generator host:* {host['platform']} · {host['machine']} · "
        f"{host['cpu_count']} logical CPUs · Python {host['python']}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Throughput (all responses) | **{r['rps']} req/s** |",
        f"| Throughput (2xx only) | {r['ok_rps']} req/s |",
        f"| Latency p50 | {r['latency_ms_p50']} ms |",
        f"| Latency p95 | {r['latency_ms_p95']} ms |",
        f"| Latency p99 | {r['latency_ms_p99']} ms |",
        f"| Latency max | {r['latency_ms_max']} ms |",
        f"| Mean latency | {r['latency_ms_mean']} ms |",
        "",
        "Latency percentiles are over 2xx responses only. A local run shares CPUs",
        "between the generator and the server, so it understates server capacity",
        "and overstates nothing about network. LLM-path requests add the",
        "provider's latency on top (not measured here — keyless by design; see",
        "cost_report.py for the per-request cost estimate).",
    ]
    return "\n".join(lines) + "\n"


def _parse_headers(values: list[str]) -> dict[str, str]:
    out = {}
    for raw in values or []:
        name, sep, value = raw.partition(":")
        if not sep:
            raise SystemExit(f"--header must be 'Name: value', got {raw!r}")
        out[name.strip()] = value.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url", "--base-url", dest="url",
        default=os.environ.get("CARELINE_LOAD_TEST_URL", "http://127.0.0.1:8010"),
        help="base URL of the target (local or remote); env CARELINE_LOAD_TEST_URL",
    )
    parser.add_argument("--path", default="/demo/ask")
    parser.add_argument("--total", type=int, default=2000, help="minimum requests")
    parser.add_argument("--duration", type=float, default=20.0, help="minimum seconds")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=20, help="unmeasured warm-up requests")
    parser.add_argument("--header", action="append", default=[], help="'Name: value' (repeat)")
    parser.add_argument("--markdown", help="write the results table here")
    parser.add_argument("--json", dest="json_out", help="write the raw results here")
    args = parser.parse_args(argv)
    headers = _parse_headers(args.header)

    for i in range(max(0, args.warmup)):  # first hits build caches/app state
        _one_request(args.url, args.path, _QUESTIONS[i % len(_QUESTIONS)], headers)

    r = run(args.url, total=args.total, duration_s=args.duration,
            concurrency=args.concurrency, path=args.path, headers=headers)
    host = host_info()
    table = render(r, host)
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(table, encoding="utf-8")
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps({**r, "host": host}, indent=2) + "\n",
                                       encoding="utf-8")
    print(json.dumps({**r, "host": host}, indent=2))
    print(table)
    return 0 if r["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
