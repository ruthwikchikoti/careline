"""Login brute-force throttle — per-account and per-IP lockout (REVIEW-4).

Portal PINs are short, so an unthrottled ``/patient/login`` is an online
brute-force target (and ``/auth/token`` guards a shared doctor credential). This
in-memory throttle counts *failed* attempts on two independent keys:

* **per account** — ``patient:<doctor_id>:<patient_id>`` or ``doctor:<doctor_id>``;
  ``max_failures`` failures lock that account out for ``lockout_seconds``, from
  any IP (an attacker cannot rotate addresses to keep guessing one PIN);
* **per IP** — ``max_failures_per_ip`` failures from one client lock that IP out,
  so spraying a few guesses across many patient ids is stopped too.

While locked, every attempt — even with the correct secret — gets 429, so the
lock cannot be used as an oracle. A success clears the account's count (not the
IP's). Failures older than the lockout window age out. It is independent of the
spend-bearing :mod:`careline.api.rate_limit` guard, and in-memory only (the same
single-process assumption as the budget guard — documented limitation).

Trade-off (documented): a per-account lockout lets someone deliberately lock a
known patient out for the window; that is the safe failure for a clinical
record (refuse rather than risk disclosure).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException, Request

from careline.api.client_ip import client_ip_from_scope

# Prune idle entries once the tables grow past this, to bound memory under abuse.
_PRUNE_THRESHOLD = 10_000


@dataclass
class _Entry:
    failures: int = 0
    last_failure: float = 0.0
    locked_until: float = 0.0


class LoginThrottle:
    """Failed-login counter with lockout on account and client-IP keys."""

    def __init__(
        self,
        *,
        max_failures: int = 5,
        lockout_seconds: int = 900,
        max_failures_per_ip: int = 20,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self.max_failures_per_ip = max_failures_per_ip
        self._clock = clock
        self._accounts: dict[str, _Entry] = {}
        self._ips: dict[str, _Entry] = {}

    def retry_after(self, *, account: str, ip: str) -> int | None:
        """Seconds until a locked account/IP may retry, or ``None`` if allowed."""
        now = self._clock()
        waits = [
            entry.locked_until - now
            for entry in (self._accounts.get(account), self._ips.get(f"ip:{ip}"))
            if entry is not None and entry.locked_until > now
        ]
        if not waits:
            return None
        return max(1, math.ceil(max(waits)))

    def record_failure(self, *, account: str, ip: str) -> None:
        """Count one failed attempt against both keys; lock on threshold."""
        now = self._clock()
        self._bump(self._accounts, account, self.max_failures, now)
        self._bump(self._ips, f"ip:{ip}", self.max_failures_per_ip, now)
        if len(self._accounts) + len(self._ips) > _PRUNE_THRESHOLD:
            self._prune(now)

    def record_success(self, *, account: str) -> None:
        """A correct secret clears the account's failure count."""
        self._accounts.pop(account, None)

    def _bump(self, table: dict[str, _Entry], key: str, limit: int, now: float) -> None:
        entry = table.setdefault(key, _Entry())
        if now - entry.last_failure > self.lockout_seconds:
            entry.failures = 0  # stale failures age out
        entry.failures += 1
        entry.last_failure = now
        if entry.failures >= limit:
            entry.locked_until = now + self.lockout_seconds
            entry.failures = 0

    def _prune(self, now: float) -> None:
        for table in (self._accounts, self._ips):
            stale = [
                k
                for k, e in table.items()
                if e.locked_until <= now and now - e.last_failure > self.lockout_seconds
            ]
            for k in stale:
                del table[k]


_LOCKED_DETAIL = "Too many failed sign-in attempts — try again later."


def guard_login(request: Request, *, account: str) -> str:
    """Refuse (429 + ``Retry-After``) while the account or client IP is locked.

    Returns the resolved client IP so the caller can record the outcome.
    """
    settings = request.app.state.settings
    ip = client_ip_from_scope(request.scope, trusted_hops=settings.trusted_proxy_hops)
    wait = request.app.state.login_throttle.retry_after(account=account, ip=ip)
    if wait is not None:
        raise HTTPException(
            status_code=429, detail=_LOCKED_DETAIL, headers={"Retry-After": str(wait)}
        )
    return ip


def record_login(request: Request, *, account: str, ip: str, ok: bool) -> None:
    """Record a login outcome against the throttle."""
    throttle: LoginThrottle = request.app.state.login_throttle
    if ok:
        throttle.record_success(account=account)
    else:
        throttle.record_failure(account=account, ip=ip)


__all__ = ["LoginThrottle", "guard_login", "record_login"]
