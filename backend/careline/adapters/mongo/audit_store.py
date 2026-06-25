"""Durable persistence for the audit trail (write-through, restart-safe).

The :class:`~careline.services.audit_service.AuditService` keeps an in-memory read
model — fast, synchronous, and the source every observability query reads from.
That model is rebuilt empty on every process start, which is why the audit trail
"disappeared" across restarts. This store is the durability seam: the service
*hydrates* it on startup and *writes through* on every log, so the in-memory model
is always a faithful, restart-survivable mirror of Mongo.

It is deliberately **synchronous** (pymongo, lazy-imported like the Motor client):
``AuditService.log_turn`` runs inside the synchronous question pipeline, so it
cannot ``await``. One small insert per turn against Atlas is well within budget for
the demo's volume, and keeping the seam sync means *zero* changes to the dozens of
existing synchronous call sites and query methods.

Three collections, each keyed by the record's own id so a write is an idempotent
upsert (which is also what makes DPDP redaction a plain overwrite):
``audit_turns`` · ``audit_calls`` · ``audit_events``.

Owner: Ruthwik (integration) — bridges Vinay's AuditService to Naga's Mongo layer.
"""

from __future__ import annotations

from typing import Any

from careline.services.audit_service import (
    AuditCallRecord,
    AuditEventRecord,
    AuditTurnRecord,
)

TURNS = "audit_turns"
CALLS = "audit_calls"
EVENTS = "audit_events"


def create_audit_store(uri: str, *, db_name: str = "careline") -> "MongoAuditStore":
    """Open a synchronous pymongo client for the audit store (fail-closed).

    Lazy-imports pymongo so the offline suite (no driver installed) never touches
    this path. ``tz_aware=True`` keeps datetimes timezone-aware on read, matching
    the records' aware ``logged_at`` timestamps.
    """
    try:
        from pymongo import MongoClient
    except ImportError as exc:  # pragma: no cover - only without pymongo
        raise RuntimeError(
            "pymongo is required for durable audit persistence; install the data extras"
        ) from exc
    client = MongoClient(uri, tz_aware=True)
    return MongoAuditStore(client[db_name], client=client)


class MongoAuditStore:
    """Sync write-through + hydrate for the three audit record types."""

    def __init__(self, database: Any, *, client: Any = None) -> None:
        self._turns = database[TURNS]
        self._calls = database[CALLS]
        self._events = database[EVENTS]
        self._client = client

    # --- write-through (upsert by the record's own id) -----------------------

    def save_turn(self, record: AuditTurnRecord) -> None:
        self._turns.replace_one({"_id": record.turn_id}, self._turn_doc(record), upsert=True)

    def save_call(self, record: AuditCallRecord) -> None:
        self._calls.replace_one({"_id": record.call_id}, self._call_doc(record), upsert=True)

    def save_event(self, record: AuditEventRecord) -> None:
        self._events.replace_one(
            {"_id": record.event_id}, self._event_doc(record), upsert=True
        )

    # --- hydrate (rebuild the in-memory read model on startup) ---------------

    def load(
        self,
    ) -> tuple[list[AuditTurnRecord], list[AuditCallRecord], list[AuditEventRecord]]:
        turns = [AuditTurnRecord(**_strip_id(d)) for d in self._turns.find()]
        calls = [AuditCallRecord(**_strip_id(d)) for d in self._calls.find()]
        events = [AuditEventRecord(**_strip_id(d)) for d in self._events.find()]
        return turns, calls, events

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    # --- doc shaping ---------------------------------------------------------

    @staticmethod
    def _turn_doc(record: AuditTurnRecord) -> dict[str, Any]:
        return {**record.model_dump(), "_id": record.turn_id}

    @staticmethod
    def _call_doc(record: AuditCallRecord) -> dict[str, Any]:
        return {**record.model_dump(), "_id": record.call_id}

    @staticmethod
    def _event_doc(record: AuditEventRecord) -> dict[str, Any]:
        return {**record.model_dump(), "_id": record.event_id}


def _strip_id(doc: dict[str, Any]) -> dict[str, Any]:
    """Drop Mongo's ``_id`` so the (``extra='forbid'``) records validate cleanly."""
    return {k: v for k, v in doc.items() if k != "_id"}


__all__ = ["MongoAuditStore", "create_audit_store", "TURNS", "CALLS", "EVENTS"]
