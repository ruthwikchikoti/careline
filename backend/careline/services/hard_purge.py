"""Hard purge of DPDP-erased patient skeletons past retention (NR-7).

After soft-delete, clinical text is nulled but skeleton rows remain for compliance.
This job permanently removes those skeletons once they exceed ``retention_days``.

The repository must optionally implement ``hard_delete_erased_before``; repos
without it are skipped safely (offline in-memory path).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class PurgeResult:
    """Outcome of one hard-purge run."""

    purged: int


@runtime_checkable
class _HardPurgeCapable(Protocol):
    async def hard_delete_erased_before(self, *, cutoff: datetime) -> int: ...


class HardPurgeJob:
    """Delete DPDP-erased patient skeletons older than the retention window."""

    def __init__(self, *, patient_repo: Any, retention_days: int = 365) -> None:
        self._patient_repo = patient_repo
        self._retention_days = retention_days

    async def run(self, *, now: datetime) -> PurgeResult:
        """Purge skeleton records whose erasure timestamp is before ``cutoff``."""
        if not isinstance(self._patient_repo, _HardPurgeCapable):
            return PurgeResult(purged=0)

        cutoff = now - timedelta(days=self._retention_days)
        purged = await self._patient_repo.hard_delete_erased_before(cutoff=cutoff)
        return PurgeResult(purged=purged)


__all__ = ["HardPurgeJob", "PurgeResult"]
