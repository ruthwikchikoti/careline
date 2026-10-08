"""Login brute-force lockout — patient PIN and doctor credential (REVIEW-4).

A 4-digit PIN has 10k combinations; without attempt limiting the portal login is
an online brute-force target. Failures are counted per account
(``(doctor_id, patient_id)`` for patients, ``doctor_id`` for doctors) *and* per
client IP; crossing either threshold locks further attempts out with a 429 for
the lockout window — even with the correct secret. This is independent of the
spend-bearing budget guard (which is off in the test app).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from careline.api.app import create_app
from careline.api.login_throttle import LoginThrottle
from tests.api.conftest import TEST_DOCTOR_PASSWORD, doctor_headers

_DOC = "dr-asha"
_PIN = "246810"


def _register(client: TestClient, pid: str, *, caller: str) -> None:
    headers = doctor_headers(client, _DOC)
    r = client.post(
        "/patients", headers=headers, json={"patient_id": pid, "caller_id": caller, "pin": _PIN}
    )
    assert r.status_code == 201


def _login(client: TestClient, pid: str, pin: str, *, ip: str | None = None):
    headers = {"X-Forwarded-For": ip} if ip else {}
    return client.post(
        "/patient/login",
        json={"doctor_id": _DOC, "patient_id": pid, "pin": pin},
        headers=headers,
    )


@pytest.fixture()
def throttled_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("CARELINE_LOGIN_MAX_FAILURES", "5")
    monkeypatch.setenv("CARELINE_LOGIN_LOCKOUT_SECONDS", "900")
    monkeypatch.setenv("CARELINE_LOGIN_MAX_FAILURES_PER_IP", "8")
    monkeypatch.setenv("CARELINE_TRUSTED_PROXY_HOPS", "1")
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client


def test_five_wrong_pins_lock_the_account_even_for_the_right_pin(throttled_client):
    _register(throttled_client, "p1", caller="+911")
    for _ in range(5):
        assert _login(throttled_client, "p1", "000000", ip="198.51.100.1").status_code == 401
    locked = _login(throttled_client, "p1", _PIN, ip="198.51.100.1")
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    assert "access_token" not in locked.text


def test_account_lockout_follows_the_account_across_ips(throttled_client):
    _register(throttled_client, "p1", caller="+911")
    for i in range(5):
        _login(throttled_client, "p1", "000000", ip=f"198.51.100.{i + 1}")
    assert _login(throttled_client, "p1", _PIN, ip="203.0.113.99").status_code == 429


def test_lockout_is_per_account_not_global(throttled_client):
    _register(throttled_client, "p1", caller="+911")
    _register(throttled_client, "p2", caller="+912")
    for _ in range(5):
        _login(throttled_client, "p1", "000000", ip="198.51.100.1")
    ok = _login(throttled_client, "p2", _PIN, ip="198.51.100.2")
    assert ok.status_code == 200


def test_success_resets_the_account_failure_count(throttled_client):
    _register(throttled_client, "p1", caller="+911")
    for _ in range(4):
        _login(throttled_client, "p1", "000000", ip="198.51.100.1")
    assert _login(throttled_client, "p1", _PIN, ip="198.51.100.1").status_code == 200
    for _ in range(4):
        _login(throttled_client, "p1", "000000", ip="198.51.100.1")
    assert _login(throttled_client, "p1", _PIN, ip="198.51.100.1").status_code == 200


def test_per_ip_limit_stops_spraying_across_patient_ids(throttled_client):
    """One IP guessing across many (even unknown) patient ids is locked out."""
    for i in range(8):
        assert _login(throttled_client, f"ghost-{i}", "000000", ip="198.51.100.7").status_code == 401
    _register(throttled_client, "p1", caller="+911")
    assert _login(throttled_client, "p1", _PIN, ip="198.51.100.7").status_code == 429
    # A different client is unaffected.
    assert _login(throttled_client, "p1", _PIN, ip="198.51.100.8").status_code == 200


def test_doctor_login_is_locked_out_after_repeated_bad_passwords(throttled_client):
    for _ in range(5):
        r = throttled_client.post(
            "/auth/token",
            json={"doctor_id": _DOC, "password": "guess"},
            headers={"X-Forwarded-For": "198.51.100.3"},
        )
        assert r.status_code == 401
    locked = throttled_client.post(
        "/auth/token",
        json={"doctor_id": _DOC, "password": TEST_DOCTOR_PASSWORD},
        headers={"X-Forwarded-For": "198.51.100.3"},
    )
    assert locked.status_code == 429


def test_lockout_on_by_default(client: TestClient):
    """Default settings lock out after 5 failures (no env needed)."""
    headers = doctor_headers(client, _DOC)
    client.post("/patients", headers=headers, json={"patient_id": "p9", "caller_id": "+919", "pin": _PIN})
    for _ in range(5):
        _login(client, "p9", "000000")
    assert _login(client, "p9", _PIN).status_code == 429


# --- unit: the throttle itself (fake clock) -----------------------------------


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_throttle_lockout_expires_after_the_window():
    clock = _Clock()
    throttle = LoginThrottle(max_failures=5, lockout_seconds=900, max_failures_per_ip=50, clock=clock)
    for _ in range(5):
        assert throttle.retry_after(account="a", ip="1.1.1.1") is None
        throttle.record_failure(account="a", ip="1.1.1.1")
    wait = throttle.retry_after(account="a", ip="1.1.1.1")
    assert wait is not None and 0 < wait <= 900
    clock.t += 901
    assert throttle.retry_after(account="a", ip="1.1.1.1") is None


def test_throttle_old_failures_age_out():
    clock = _Clock()
    throttle = LoginThrottle(max_failures=5, lockout_seconds=900, max_failures_per_ip=50, clock=clock)
    for _ in range(4):
        throttle.record_failure(account="a", ip="1.1.1.1")
    clock.t += 901  # stale failures no longer count toward a lockout
    throttle.record_failure(account="a", ip="1.1.1.1")
    assert throttle.retry_after(account="a", ip="1.1.1.1") is None
