"""GET /monitoring — doctor-authenticated snapshot of the online monitor.

Served by the real app factory (``create_app`` mounts the monitoring router).
Doctor login requires the per-deployment credential, set through the
environment exactly as ``tests/api/conftest.py`` does.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from careline.adapters.llm.judge import KeylessJudge
from careline.api.app import create_app
from careline.domain.enums import ScopeCategory
from careline.domain.model.decision import Decision
from careline.services import online_monitor
from careline.services.online_monitor import DriftReference, OnlineMonitor


_PASSWORD = "test-doctor-password-for-offline-suite"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("CARELINE_DOCTOR_PASSWORD", _PASSWORD)
    online_monitor.reset_monitor(
        OnlineMonitor(
            window=20, judge=KeylessJudge(), judge_sample_rate=0.0,
            reference=DriftReference({"red_flag": 1.0}, frozenset({"q"}), 0.0, 1.0, 1),
        )
    )
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    online_monitor.reset_monitor()


def _token(client, doctor="dr-A"):
    r = client.post("/auth/token", json={"doctor_id": doctor, "password": _PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_requires_doctor_auth(client):
    assert client.get("/monitoring").status_code == 401
    assert client.get("/monitoring", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_returns_snapshot(client):
    online_monitor.record(
        decision=Decision.escalate("x", scope=ScopeCategory.RED_FLAG, risk=1.0),
        question="q", latency_ms=4.0,
    )
    r = client.get("/monitoring", headers=_token(client))
    assert r.status_code == 200
    body = r.json()
    assert body["operational"]["requests_total"] == 1
    assert body["output"]["verdict_counts"]["escalate"] == 1
    for key in ("operational", "output", "quality", "drift", "cost", "alerts"):
        assert key in body
