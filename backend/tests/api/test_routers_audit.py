"""Audit and eval router tests (NR-6).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_audit_turns_requires_auth(client: TestClient):
    response = client.get("/audit/turns")
    assert response.status_code == 401


def test_audit_calls_requires_auth(client: TestClient):
    response = client.get("/audit/calls")
    assert response.status_code == 401


def test_audit_events_requires_auth(client: TestClient):
    response = client.get("/audit/events")
    assert response.status_code == 401


def test_eval_run_requires_auth(client: TestClient):
    response = client.post("/eval/run")
    assert response.status_code == 401


def test_audit_endpoints_return_empty_lists_when_no_data(
    client: TestClient, authed_headers: dict[str, str]
):
    turns = client.get("/audit/turns", headers=authed_headers)
    calls = client.get("/audit/calls", headers=authed_headers)
    events = client.get("/audit/events", headers=authed_headers)

    assert turns.status_code == 200
    assert calls.status_code == 200
    assert events.status_code == 200
    assert turns.json() == []
    assert calls.json() == []
    assert events.json() == []


def test_eval_run_returns_scenario_results(client: TestClient, authed_headers: dict[str, str]):
    response = client.post("/eval/run", headers=authed_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] >= 1
    assert payload["passed"] >= 0
    assert isinstance(payload["digest"], str)
    assert len(payload["scenarios"]) == payload["total"]
    assert all("name" in scenario for scenario in payload["scenarios"])
    assert all("verdict" in scenario for scenario in payload["scenarios"])
    assert all("passed" in scenario for scenario in payload["scenarios"])

    events = client.get("/audit/events", headers=authed_headers)
    assert events.status_code == 200
    assert any(event.get("kind") == "eval" for event in events.json())
