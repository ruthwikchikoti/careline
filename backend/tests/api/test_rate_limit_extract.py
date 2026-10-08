"""The LLM-spending extract route is spend-guarded (final evaluator finding
``redteam-extract-not-rate-limited``).

At v7 ``POST /consultations/{id}/extract`` (which calls the LLM extractor
whenever a key is set) sat outside the per-IP rate limit and the daily cap.
It now counts as a spend route; the other consultation routes (create,
consent, approve) and GETs do not.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from careline.api.app import create_app
from careline.api.rate_limit import BudgetGuardMiddleware
from careline.config import Settings
from tests.api.conftest import doctor_headers


def test_extract_is_a_spend_route_and_others_are_not():
    mw = BudgetGuardMiddleware(app=None)
    assert mw._is_spend("POST", "/consultations/abc-123/extract")
    assert mw._is_spend("POST", "/consultations/abc-123/extract/")
    assert not mw._is_spend("GET", "/consultations/abc-123/extract")
    assert not mw._is_spend("POST", "/consultations")
    assert not mw._is_spend("POST", "/consultations/abc-123/consent")
    assert not mw._is_spend("POST", "/consultations/abc-123/approve")


def test_extract_hits_the_rate_limit():
    with TestClient(create_app(settings=Settings(rate_limit_per_minute=2))) as client:
        headers = doctor_headers(client, "dr-rl")
        statuses = [
            client.post("/consultations/nope/extract", headers=headers).status_code
            for _ in range(3)
        ]
        assert statuses[2] == 429, statuses


def test_extract_counts_toward_the_daily_cap():
    with TestClient(create_app(settings=Settings(daily_request_cap=1))) as client:
        assert client.post("/demo/ask", json={"question": "q"}).status_code != 503
        headers = doctor_headers(client, "dr-cap")
        assert client.post("/consultations/nope/extract", headers=headers).status_code == 503
