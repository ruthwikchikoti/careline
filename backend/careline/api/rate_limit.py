"""Budget guard middleware — per-IP rate limit + hard daily cap for the public demo.

Every POST to a spend-bearing endpoint (/demo/ask, /internal/run-question,
/patient/ask, and /consultations/{id}/extract — the LLM extractor) is a paid
LLM call on a public URL. This ASGI middleware enforces
independent guards before the request reaches the router:

* **per-minute, per-IP** sliding window (``CARELINE_RATE_LIMIT_PER_MINUTE``) on
  the spend routes,
* a **separate** per-minute, per-IP window for the login routes (/patient/login,
  /auth/token; ``CARELINE_LOGIN_RATE_LIMIT_PER_MINUTE``, defaulting to the spend
  value). Separate counters, so six demo asks never lock a user out of signing
  in and logins never eat the spend budget; logins never count toward the daily
  cap either (brute-force lockout lives in :mod:`careline.api.login_throttle`),
* **hard daily cap** across the process (``CARELINE_DAILY_REQUEST_CAP``) — the
  belt that makes the ~$20 budget a guarantee rather than a hope. When the cap
  trips the demo endpoint answers 503 with a "daily demo cap reached" body:
  refusing to spend is the safe failure (no answer is safer than an unbillable
  one); the deterministic spine stays available for non-spend routes.

Both default to 0 = off (tests/dev); the deploy config turns them on. In-memory
counters only — single-process free-tier assumption, documented in the README.

The per-IP key comes from :func:`~careline.api.client_ip.client_ip_from_scope`:
the rightmost *trusted* X-Forwarded-For hop (``CARELINE_TRUSTED_PROXY_HOPS``),
never the client-controlled leftmost entry, so rotating a spoofed XFF value does
not mint a fresh bucket (REVIEW-5).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from careline.api.client_ip import client_ip_from_scope

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
    """Pure-ASGI middleware: per-IP minute windows (spend / login) + daily cap."""

    def __init__(
        self,
        app,
        *,
        per_minute: int = 0,
        daily_cap: int = 0,
        spend_prefixes: tuple[str, ...] = (
            "/demo/ask",
            "/internal/run-question",
            "/patient/ask",
        ),
        # Spend routes with a path parameter, matched as full paths.
        spend_patterns: tuple[str, ...] = (r"/consultations/[^/]+/extract/?",),
        limit_only_prefixes: tuple[str, ...] = ("/patient/login", "/auth/token"),
        trusted_proxy_hops: int = 0,
        login_per_minute: int | None = None,
    ) -> None:
        self.app = app
        self.per_minute = per_minute
        self.daily_cap = daily_cap
        self.spend_prefixes = spend_prefixes
        self._spend_patterns = tuple(re.compile(p) for p in spend_patterns)
        self.limit_only_prefixes = limit_only_prefixes
        self.trusted_proxy_hops = trusted_proxy_hops
        # Login window: its own limit (None = mirror the spend limit) and its own
        # counters, so the two budgets never drain each other.
        self.login_per_minute = per_minute if login_per_minute is None else login_per_minute
        self._window: dict[str, deque[float]] = defaultdict(deque)
        self._login_window: dict[str, deque[float]] = defaultdict(deque)
        self._daily_date: str = ""
        self._daily_count = 0

    def _is_spend(self, method: str, path: str) -> bool:
        return method == "POST" and (
            any(path.startswith(p) for p in self.spend_prefixes)
            or any(p.fullmatch(path) for p in self._spend_patterns)
        )

    def _is_limit_only(self, method: str, path: str) -> bool:
        return method == "POST" and any(
            path.startswith(p) for p in self.limit_only_prefixes
        )

    @staticmethod
    def _admit(
        table: dict[str, deque[float]], limit: int, ip: str, now: float
    ) -> bool:
        """Per-IP sliding minute window: record and admit, or refuse."""
        window = table[ip]
        while window and now - window[0] > 60.0:
            window.popleft()
        if len(window) >= limit:
            return False
        window.append(now)
        return True

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
        spend = self._is_spend(method, path)
        limit_only = not spend and self._is_limit_only(method, path)
        if limit_only:
            # Login routes: their own per-IP window — not spend, never counted daily.
            if self.login_per_minute:
                ip = client_ip_from_scope(scope, trusted_hops=self.trusted_proxy_hops)
                if not self._admit(
                    self._login_window, self.login_per_minute, ip, time.monotonic()
                ):
                    await self._reject(send, 429, _TOO_MANY)
                    return
            await self.app(scope, receive, send)
            return
        if not spend or (not self.per_minute and not self.daily_cap):
            await self.app(scope, receive, send)
            return

        ip = client_ip_from_scope(scope, trusted_hops=self.trusted_proxy_hops)
        now = time.monotonic()

        # Daily cap (UTC date-keyed, process-wide).
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._daily_date:
            self._daily_date, self._daily_count = today, 0
        if self.daily_cap and self._daily_count >= self.daily_cap:
            await self._reject(send, 503, _DAILY_CAP)
            return

        # Per-IP sliding minute window.
        if self.per_minute and not self._admit(self._window, self.per_minute, ip, now):
            await self._reject(send, 429, _TOO_MANY)
            return

        # The daily cap counts only requests the app actually accepted
        # (status < 400): rejected/invalid POSTs spend nothing, so they must
        # not darken the demo budget.
        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start" and message["status"] < 400:
                self._daily_count += 1
            await send(message)

        await self.app(scope, receive, send_wrapper)
