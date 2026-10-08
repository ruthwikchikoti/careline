"""New patient registrations must use a 6-digit numeric PIN (redteam seed/PIN).

FAILURE-ANALYSIS lists "6-digit PINs" as a brute-force mitigation, but the
register DTO accepted any 4–12 characters. The API now enforces ``^[0-9]{6}$`` for
NEW registrations (login still accepts whatever was registered earlier, so legacy
identities keep working).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _register(client: TestClient, headers: dict[str, str], pin: str, pid: str = "p-pin"):
    return client.post(
        "/patients",
        headers=headers,
        json={"patient_id": pid, "caller_id": "+910000000055", "pin": pin},
    )


@pytest.mark.parametrize("pin", ["1234", "12345", "1234567", "12a456", "12 456", "abcdef", "", "１２３４５６"])
def test_registration_rejects_a_pin_that_is_not_six_ascii_digits(client, authed_headers, pin):
    response = _register(client, authed_headers, pin)
    assert response.status_code == 422
    assert pin == "" or pin not in response.text  # never echo the submitted PIN


@pytest.mark.parametrize("pin", ["000000", "482913"])
def test_registration_accepts_six_digits(client, authed_headers, pin):
    assert _register(client, authed_headers, pin).status_code == 201
