"""Per-doctor credentials — CARELINE_DOCTOR_CREDENTIALS (SECURITY-2).

One shared ``CARELINE_DOCTOR_PASSWORD`` meant anyone holding it could sign in as
*every* doctor id — a cross-tenant credential. Each doctor now has their own
salted hash (``doctor_id:<pbkdf2_sha256$...>``); the shared password survives only
as an explicit dev/local-demo fallback and is refused in production / public-demo
mode. Failures still count per doctor id AND per client IP across all doctor ids.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from careline.adapters.auth import hash_password as hp
from careline.api.app import create_app
from careline.config import Settings
from careline.services.auth_service import AuthService
from tests.api.conftest import TEST_DOCTOR_PASSWORD

# Low iteration count keeps the offline suite fast; the CLI default is >= 600k.
_ITER = 1_000
_ASHA_PW = "asha-correct-horse-battery"
_X_PW = "x-another-long-password"


def _creds() -> str:
    return (
        f"dr-asha:{hp.hash_password(_ASHA_PW, iterations=_ITER)},"
        f"dr-x:{hp.hash_password(_X_PW, iterations=_ITER)}"
    )


# --- hashing primitives -------------------------------------------------------


def test_hash_round_trip_and_salted():
    a = hp.hash_password("pw-one-long-enough", iterations=_ITER)
    b = hp.hash_password("pw-one-long-enough", iterations=_ITER)
    assert a.startswith("pbkdf2_sha256$")
    assert a != b  # random salt per hash
    assert hp.verify_password("pw-one-long-enough", a)
    assert not hp.verify_password("pw-one-long-enougH", a)


def test_plain_sha256_hash_is_accepted():
    import hashlib

    encoded = "sha256$" + hashlib.sha256(b"legacy-password").hexdigest()
    assert hp.verify_password("legacy-password", encoded)
    assert not hp.verify_password("other", encoded)


@pytest.mark.parametrize(
    "encoded",
    ["", "plaintext", "pbkdf2_sha256$x$00$00", "md5$abc", "sha256$nothex", "pbkdf2_sha256$1000$$"],
)
def test_malformed_hash_never_verifies(encoded):
    """Fail closed: an unparseable stored hash is a refusal, never a crash/accept."""
    assert hp.verify_password("anything", encoded) is False
    assert hp.is_supported_hash(encoded) is False


def test_cli_prints_a_credential_entry(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("cli-password-long\n"))
    code = hp.main(["--stdin", "--doctor-id", "dr-asha", "--iterations", str(_ITER)])
    assert code == 0
    line = capsys.readouterr().out.strip()
    doctor_id, encoded = line.split(":", 1)
    assert doctor_id == "dr-asha"
    assert hp.verify_password("cli-password-long", encoded)


def test_cli_refuses_an_empty_password(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    assert hp.main(["--stdin"]) != 0


def test_parse_credentials_rejects_malformed_entries():
    with pytest.raises(ValueError):
        hp.parse_credentials("dr-asha")  # no hash
    with pytest.raises(ValueError):
        hp.parse_credentials("dr-asha:plaintext-password")  # not a supported hash
    good = hp.hash_password("p" * 16, iterations=_ITER)
    with pytest.raises(ValueError):
        hp.parse_credentials(f"dr-asha:{good},dr-asha:{good}")  # duplicate id
    assert hp.parse_credentials(f" dr-asha : {good} ,, ") == {"dr-asha": good}


def test_malformed_credentials_env_fails_settings_load(monkeypatch):
    monkeypatch.setenv("CARELINE_DOCTOR_CREDENTIALS", "dr-asha:not-a-hash")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


# --- AuthService: per-doctor verification ------------------------------------


def _svc(monkeypatch, **env: str) -> AuthService:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return AuthService(settings=Settings(_env_file=None))


def test_each_doctor_signs_in_only_with_their_own_password(monkeypatch):
    svc = _svc(monkeypatch, CARELINE_DOCTOR_CREDENTIALS=_creds())
    assert svc.verify_doctor_credentials(doctor_id="dr-asha", password=_ASHA_PW)
    assert svc.verify_doctor_credentials(doctor_id="dr-x", password=_X_PW)
    # Cross-tenant: dr-x's password never opens dr-asha (and vice versa).
    assert not svc.verify_doctor_credentials(doctor_id="dr-asha", password=_X_PW)
    assert not svc.verify_doctor_credentials(doctor_id="dr-x", password=_ASHA_PW)
    # Unknown doctor id: refused even with a valid password for someone else.
    assert not svc.verify_doctor_credentials(doctor_id="dr-evil", password=_ASHA_PW)


def test_shared_password_is_ignored_once_per_doctor_credentials_exist(monkeypatch):
    """The shared fallback must not be a backdoor next to per-doctor hashes."""
    svc = _svc(
        monkeypatch,
        CARELINE_DOCTOR_CREDENTIALS=_creds(),
        CARELINE_DOCTOR_PASSWORD="shared-fallback-password",
    )
    assert not svc.verify_doctor_credentials(
        doctor_id="dr-asha", password="shared-fallback-password"
    )
    assert not svc.verify_doctor_credentials(
        doctor_id="dr-new", password="shared-fallback-password"
    )


def test_reserved_demo_doctor_refused_even_with_a_credential_entry(monkeypatch):
    entry = f"demo-doctor:{hp.hash_password('demo-pass-long', iterations=_ITER)}"
    svc = _svc(monkeypatch, CARELINE_DOCTOR_CREDENTIALS=entry)
    assert not svc.verify_doctor_credentials(doctor_id="demo-doctor", password="demo-pass-long")


def test_shared_password_is_still_the_dev_fallback(monkeypatch):
    monkeypatch.delenv("CARELINE_DOCTOR_CREDENTIALS", raising=False)
    svc = _svc(monkeypatch, CARELINE_DOCTOR_PASSWORD="dev-shared-password")
    assert svc.verify_doctor_credentials(doctor_id="dr-any", password="dev-shared-password")


def test_shared_password_never_verifies_in_hardened_mode(monkeypatch):
    """Even if startup checks were bypassed, the verifier itself refuses the fallback."""
    monkeypatch.delenv("CARELINE_DOCTOR_CREDENTIALS", raising=False)
    svc = _svc(
        monkeypatch,
        CARELINE_PUBLIC_DEMO="true",
        CARELINE_DOCTOR_PASSWORD="dev-shared-password",
    )
    assert not svc.verify_doctor_credentials(doctor_id="dr-any", password="dev-shared-password")


# --- HTTP: /auth/token --------------------------------------------------------


@pytest.fixture()
def cred_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("CARELINE_DOCTOR_CREDENTIALS", _creds())
    monkeypatch.setenv("CARELINE_LOGIN_MAX_FAILURES", "5")
    monkeypatch.setenv("CARELINE_LOGIN_MAX_FAILURES_PER_IP", "8")
    monkeypatch.setenv("CARELINE_TRUSTED_PROXY_HOPS", "1")
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client


def _token(client: TestClient, doctor_id: str, password: str, ip: str = "198.51.100.1"):
    return client.post(
        "/auth/token",
        json={"doctor_id": doctor_id, "password": password},
        headers={"X-Forwarded-For": ip},
    )


def test_token_endpoint_uses_per_doctor_credentials(cred_client):
    ok = _token(cred_client, "dr-asha", _ASHA_PW)
    assert ok.status_code == 200 and ok.json()["access_token"]
    assert _token(cred_client, "dr-asha", _X_PW).status_code == 401
    # The conftest's shared test password no longer opens any account.
    assert _token(cred_client, "dr-asha", TEST_DOCTOR_PASSWORD).status_code == 401


def test_per_ip_lockout_counts_failures_across_all_doctor_ids(cred_client):
    """Spraying one guess per doctor id from one IP still trips the per-IP lock."""
    for i in range(8):
        assert _token(cred_client, f"dr-ghost-{i}", "guess", ip="198.51.100.9").status_code == 401
    locked = _token(cred_client, "dr-asha", _ASHA_PW, ip="198.51.100.9")
    assert locked.status_code == 429
    # Another client is unaffected.
    assert _token(cred_client, "dr-asha", _ASHA_PW, ip="198.51.100.10").status_code == 200
