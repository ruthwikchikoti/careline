"""Budget guard middleware — per-IP rate limit + hard daily cap for the public demo.

Every POST to a spend-bearing endpoint (/demo/ask, /internal/run-question) is a
paid LLM call on a public URL. This ASGI middleware enforces two independent
guards before the request reaches the router:

* **per-minute, per-IP** sliding window (``CARELINE_RATE_LIMIT_PER_MINUTE``),
* **hard daily cap** across the process (``CARELINE_DAILY_REQUEST_CAP``) — the
  belt that makes the ~$20 budget a guarantee rather than a hope. When the cap
  trips the demo endpoint answers 503 with a "daily demo cap reached" body:
  refusing to spend is the safe failure (no answer is safer than an unbillable
  one); the deterministic spine stays available for non-spend routes.

Both default to 0 = off (tests/dev); the deploy config turns them on. In-memory
counters only — single-process free-tier assumption, documented in the README.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

SPEND_PATH_PREFIXES = ("/demo/ask", "/internal/run-question", "/demo/")

_TOO_MANY = json.dumps(
    {"detail": "Rate limit reached — please wait a minute and try again."}
).encode()
_DAILY_CAP = json.dumps(
    {
        "detail": (
            "Daily demo cap reached — this public demo has a fixed LLM budget. "
            "Come back tomorrow, or run the repo locally with your own key."
        )
    }
).encode()


class BudgetGuardMiddleware:
    """Pure-ASGI middleware: per-IP minute window + process-wide daily cap."""

    def __init__(
        self,
        app,
        *,
        per_minute: int = 0,
        daily_cap: int = 0,
        spend_prefixes: tuple[str, ...] = ("/demo/ask", "/internal/run-question"),
    ) -> None:
        self.app = app
        self.per_minute = per_minute
        self.daily_cap = daily_cap
        self.spend_prefixes = spend_prefixes
        self._window: dict[str, deque[float]] = defaultdict(deque)
        self._daily_date: str = ""
        self._daily_count = 0

    def _is_spend(self, method: str, path: str) -> bool:
        return method == "POST" and any(path.startswith(p) for p in self.spend_prefixes)

    async def _reject(self, send, status: int, body: bytes) -> None:
        """Emit the rejection ourselves (the app is never invoked)."""
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"x-careline-demo", b"fictional-data"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path = scope.get("path", "")
        if not self._is_spend(method, path) or (not self.per_minute and not self.daily_cap):
            await self.app(scope, receive, send)
            return

        ip = (scope.get("client") or ("unknown", 0))[0]
        now = time.monotonic()

        # Daily cap (UTC date-keyed, process-wide).
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._daily_date:
            self._daily_date, self._daily_count = today, 0
        if self.daily_cap and self._daily_count >= self.daily_cap:
            await self._reject(send, 503, _DAILY_CAP)
            return

        # Per-IP sliding minute window.
        if self.per_minute:
            window = self._window[ip]
            while window and now - window[0] > 60.0:
                window.popleft()
            if len(window) >= self.per_minute:
                await self._reject(send, 429, _TOO_MANY)
                return
            window.append(now)

        self._daily_count += 1
        await self.app(scope, receive, send)
