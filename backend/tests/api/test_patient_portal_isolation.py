"""sev-0: the patient portal never crosses tenants (REVIEW-1).

Reproduces the verified cross-tenant leak: two doctors can register the *same*
``patient_id`` string, and the portal used to resolve login, question history and
"clear history" by ``patient_id`` alone — so ``dr-evil``'s ``p2`` could read (and
wipe) ``dr-asha``'s ``p2`` thread. Every patient-scoped audit query is now keyed by
``(doctor_id, patient_id)`` and portal login is tenant-scoped (the body names the
clinic/doctor id). The anonymous ``/demo/ask`` tenant (``demo-doctor`` /
``demo-patient``) is reserved and unreachable from any portal session.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from careline.domain.model.decision import Decision
from careline.services.audit_service import AuditService
from tests.api.conftest import doctor_headers

_ASHA = "dr-asha"
_EVIL = "dr-evil"
_PID = "p2"
_ASHA_PIN = "246810"
_EVIL_PIN = "1111"
_ASHA_QUESTION = "Can I take my tablet with food?"


def _register(client: TestClient, headers: dict[str, str], *, pid: str, pin: str, caller: str):
    return client.post(
        "/patients",
        headers=headers,
        json={"patient_id": pid, "caller_id": caller, "pin": pin},
    )


def _portal_login(client: TestClient, *, doctor_id: str, pid: str, pin: str):
    return client.post(
        "/patient/login", json={"doctor_id": doctor_id, "patient_id": pid, "pin": pin}
    )


def _portal_headers(client: TestClient, *, doctor_id: str, pid: str, pin: str) -> dict[str, str]:
    response = _portal_login(client, doctor_id=doctor_id, pid=pid, pin=pin)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["doctor_id"] == doctor_id
    assert body["patient_id"] == pid
    return {"Authorization": f"Bearer {body['access_token']}"}


@pytest.fixture()
def two_tenants(client: TestClient) -> dict[str, dict[str, str]]:
    """dr-evil registers p2/1111 FIRST, then dr-asha registers her own p2."""
    evil = doctor_headers(client, _EVIL)
    asha = doctor_headers(client, _ASHA)
    assert _register(client, evil, pid=_PID, pin=_EVIL_PIN, caller="+910000000001").status_code == 201
    assert _register(client, asha, pid=_PID, pin=_ASHA_PIN, caller="+910000000002").status_code == 201

    asha_portal = _portal_headers(client, doctor_id=_ASHA, pid=_PID, pin=_ASHA_PIN)
    ask = client.post("/patient/ask", headers=asha_portal, json={"question": _ASHA_QUESTION})
    assert ask.status_code == 200, ask.text
    mine = client.get("/patient/questions", headers=asha_portal).json()
    assert [q["question"] for q in mine] == [_ASHA_QUESTION]
    return {"asha_portal": asha_portal, "asha": asha, "evil": evil}


# --- the reproduced leak ------------------------------------------------------


def test_other_tenant_same_patient_id_cannot_read_question_history(client, two_tenants):
    evil_portal = _portal_headers(client, doctor_id=_EVIL, pid=_PID, pin=_EVIL_PIN)
    seen = client.get("/patient/questions", headers=evil_portal)
    assert seen.status_code == 200
    assert seen.json() == []
    assert _ASHA_QUESTION not in seen.text


def test_other_tenant_same_patient_id_cannot_wipe_history(client, two_tenants):
    evil_portal = _portal_headers(client, doctor_id=_EVIL, pid=_PID, pin=_EVIL_PIN)
    wipe = client.delete("/patient/history", headers=evil_portal)
    assert wipe.status_code == 204
    still = client.get("/patient/questions", headers=two_tenants["asha_portal"]).json()
    assert [q["question"] for q in still] == [_ASHA_QUESTION]


def test_own_history_clear_still_works(client, two_tenants):
    wipe = client.delete("/patient/history", headers=two_tenants["asha_portal"])
    assert wipe.status_code == 204
    assert client.get("/patient/questions", headers=two_tenants["asha_portal"]).json() == []


def test_evil_pin_does_not_open_ashas_patient(client, two_tenants):
    """A PIN is only valid for the identity under the named doctor."""
    response = _portal_login(client, doctor_id=_ASHA, pid=_PID, pin=_EVIL_PIN)
    assert response.status_code == 401


def test_login_is_tenant_scoped_and_requires_a_doctor_id(client, two_tenants):
    legacy = client.post("/patient/login", json={"patient_id": _PID, "pin": _EVIL_PIN})
    assert legacy.status_code == 422
    assert "access_token" not in legacy.text


def test_unknown_doctor_and_wrong_pin_are_the_same_401(client, two_tenants):
    unknown = _portal_login(client, doctor_id="dr-nobody", pid=_PID, pin=_ASHA_PIN)
    wrong = _portal_login(client, doctor_id=_ASHA, pid=_PID, pin="000000")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_doctor_resolution_reaches_only_the_owning_tenants_patient(client, two_tenants):
    """Asha's reply to her p2's escalation never appears on evil's p2 thread."""
    ask = client.post(
        "/patient/ask",
        headers=two_tenants["asha_portal"],
        json={"question": "I have crushing chest pain right now"},
    )
    assert ask.status_code == 200
    assert ask.json()["verdict"] == "escalate"
    turns = client.get("/escalations", headers=two_tenants["asha"]).json()["escalations"]
    turn_id = turns[0]["turn_id"]
    reply = client.post(
        f"/escalations/{turn_id}/resolve",
        headers=two_tenants["asha"],
        json={"reply": "Please call emergency services now."},
    )
    assert reply.status_code in (200, 201), reply.text

    evil_portal = _portal_headers(client, doctor_id=_EVIL, pid=_PID, pin=_EVIL_PIN)
    assert "emergency services" not in client.get("/patient/questions", headers=evil_portal).text


# --- reserved anonymous-demo ids ---------------------------------------------


@pytest.mark.parametrize("pid", ["demo-patient", " Demo-Patient "])
def test_registration_rejects_the_reserved_demo_patient_id(client, authed_headers, pid):
    response = _register(client, authed_headers, pid=pid, pin="123456", caller="+910000000009")
    assert response.status_code == 400
    assert "reserved" in response.json()["detail"]


def test_anonymous_demo_turns_are_never_visible_through_the_portal(client):
    ask = client.post("/demo/ask", json={"question": "Can I take paracetamol for a headache?"})
    assert ask.status_code == 200
    audit: AuditService = client.app.state.audit
    assert any(t.patient_id == "demo-patient" for t in audit.turns)

    # No portal login can ever land on the demo tenant...
    login = _portal_login(client, doctor_id="demo-doctor", pid="demo-patient", pin="1234")
    assert login.status_code == 401
    # ...and even a validly-signed token naming a reserved id is refused.
    for doctor_id in ("demo-doctor", _ASHA):
        forged = client.app.state.auth_svc.issue_patient_token(
            patient_id="demo-patient", doctor_id=doctor_id
        )
        seen = client.get("/patient/questions", headers={"Authorization": f"Bearer {forged}"})
        assert seen.status_code == 401


# --- service-level: every patient-scoped audit query is tenant-keyed ----------


_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _two_tenant_audit(store: Any = None) -> AuditService:
    audit = AuditService(store=store)
    for doctor_id in (_ASHA, _EVIL):
        audit.log_turn(
            call_id=f"call-{doctor_id}",
            patient_id=_PID,
            doctor_id=doctor_id,
            question=f"question from {doctor_id}",
            decision=Decision.answer("ok", confidence=0.9),
            logged_at=_NOW,
        )
    return audit


def test_turns_for_patient_is_keyed_by_doctor_and_patient():
    audit = _two_tenant_audit()
    turns = audit.turns_for_patient(doctor_id=_ASHA, patient_id=_PID)
    assert [t.doctor_id for t in turns] == [_ASHA]
    with pytest.raises(TypeError):
        audit.turns_for_patient(_PID)  # type: ignore[call-arg]  # unscoped call no longer exists


def test_resolutions_for_patient_is_keyed_by_doctor_and_patient():
    audit = _two_tenant_audit()
    for t in audit.turns:
        audit.resolve_escalation(turn_id=t.turn_id, reply_text=f"reply {t.doctor_id}", resolved_by=t.doctor_id)
    replies = audit.resolutions_for_patient(doctor_id=_EVIL, patient_id=_PID)
    assert [r.doctor_id for r in replies] == [_EVIL]


def test_clear_patient_only_clears_the_owning_tenant():
    class _Store:
        def __init__(self) -> None:
            self.deleted: list[dict[str, str]] = []

        def load(self):
            return [], [], [], []

        def save_turn(self, record):  # noqa: D401 - store double
            pass

        def save_call(self, record):
            pass

        def save_event(self, record):
            pass

        def save_resolution(self, record):
            pass

        def delete_patient(self, *, doctor_id: str, patient_id: str) -> None:
            self.deleted.append({"doctor_id": doctor_id, "patient_id": patient_id})

    store = _Store()
    audit = _two_tenant_audit(store)
    removed = audit.clear_patient(doctor_id=_EVIL, patient_id=_PID)
    assert removed == 1
    assert [t.doctor_id for t in audit.turns] == [_ASHA]
    assert store.deleted == [{"doctor_id": _EVIL, "patient_id": _PID}]


def test_redact_patient_only_redacts_the_owning_tenant():
    audit = _two_tenant_audit()
    count = audit.redact_patient(_PID, doctor_id=_EVIL)
    assert count == 1
    by_doctor = {t.doctor_id: t for t in audit.turns}
    assert by_doctor[_EVIL].redacted and by_doctor[_EVIL].question is None
    assert not by_doctor[_ASHA].redacted
    assert by_doctor[_ASHA].question == f"question from {_ASHA}"


# --- adapter-level: Mongo identity lookup + durable clear are tenant-scoped ----


class _FakeAsyncCollection:
    """Just enough of a Motor collection for ``find_one`` equality filters."""

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    async def find_one(self, flt: dict[str, Any]):
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                return d
        return None


class _FakeSyncCollection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def delete_many(self, flt: dict[str, Any]) -> None:
        self.docs[:] = [d for d in self.docs if not all(d.get(k) == v for k, v in flt.items())]


def test_mongo_identity_lookup_is_tenant_scoped():
    from careline.adapters.mongo.repositories import MongoPatientRepository

    docs = [
        {"_id": f"{_EVIL}:{_PID}", "doctor_id": _EVIL, "patient_id": _PID, "caller_id": "c1", "pin_hmac": "evil"},
        {"_id": f"{_ASHA}:{_PID}", "doctor_id": _ASHA, "patient_id": _PID, "caller_id": "c2", "pin_hmac": "asha"},
    ]
    db = {"facts": _FakeAsyncCollection([]), "patients": _FakeAsyncCollection(docs)}
    repo = MongoPatientRepository(db)
    asha = asyncio.run(repo.find_identity(doctor_id=_ASHA, patient_id=_PID))
    assert asha is not None and asha.pin_hmac == "asha"
    assert asyncio.run(repo.find_identity(doctor_id="dr-nobody", patient_id=_PID)) is None
    assert not hasattr(repo, "find_by_patient_id")  # the unscoped lookup is gone


def test_mongo_audit_store_delete_is_tenant_scoped():
    from careline.adapters.mongo.audit_store import MongoAuditStore

    turns = [
        {"_id": "t1", "doctor_id": _ASHA, "patient_id": _PID},
        {"_id": "t2", "doctor_id": _EVIL, "patient_id": _PID},
    ]
    resolutions = [
        {"_id": "t1", "doctor_id": _ASHA, "patient_id": _PID},
        {"_id": "t2", "doctor_id": _EVIL, "patient_id": _PID},
    ]
    db = {
        "audit_turns": _FakeSyncCollection(turns),
        "audit_calls": _FakeSyncCollection([]),
        "audit_events": _FakeSyncCollection([]),
        "audit_resolutions": _FakeSyncCollection(resolutions),
    }
    store = MongoAuditStore(db)
    store.delete_patient(doctor_id=_EVIL, patient_id=_PID)
    assert [d["_id"] for d in turns] == ["t1"]
    assert [d["_id"] for d in resolutions] == ["t1"]
