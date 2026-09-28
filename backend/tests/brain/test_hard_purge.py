"""HardPurgeJob unit tests (NR-7).

Pins retention-window purge of DPDP-erased patient skeletons.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from careline.services.hard_purge import HardPurgeJob

_NOW = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)


class _PurgeRepo:
    def __init__(self) -> None:
        self.calls: list[datetime] = []
        self._purged = 2

    async def hard_delete_erased_before(self, *, cutoff: datetime) -> int:
        self.calls.append(cutoff)
        return self._purged


class _RepoWithoutPurge:
    async def get(self, *, doctor_id: str, patient_id: str):
        return None


def _run(coro):
    return asyncio.run(coro)


def test_hard_purge_deletes_records_past_cutoff():
    repo = _PurgeRepo()
    job = HardPurgeJob(patient_repo=repo, retention_days=365)
    result = _run(job.run(now=_NOW))
    assert result.purged == 2
    assert len(repo.calls) == 1
    expected_cutoff = _NOW - timedelta(days=365)
    assert repo.calls[0] == expected_cutoff


def test_hard_purge_skips_when_repo_lacks_method():
    job = HardPurgeJob(patient_repo=_RepoWithoutPurge(), retention_days=30)
    result = _run(job.run(now=_NOW))
    assert result.purged == 0


def test_hard_purge_respects_retention_window():
    repo = _PurgeRepo()
    job = HardPurgeJob(patient_repo=repo, retention_days=90)
    _run(job.run(now=_NOW))
    cutoff = repo.calls[0]
    assert cutoff == _NOW - timedelta(days=90)
