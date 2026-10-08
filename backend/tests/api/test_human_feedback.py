"""Human feedback — the second online-evaluation channel (LLMOps depth).

The LLM-as-judge (``services/online_monitor.py``) scores a sample of ANSWER
turns automatically. These tests pin the *human* channel on top of it:

* ``POST /patient/feedback`` — the patient rates an ANSWER / CLARIFY turn
  helpful or not (optional comment, max 500 chars). The turn must belong to the
  exact ``(doctor_id, patient_id)`` of the patient JWT — anything else is a 404,
  never a 403 (no oracle that a turn id exists). One rating per turn; re-rating
  overwrites.
* ``POST /audit/turns/{turn_id}/review`` — the doctor marks a turn correct /
  incorrect (expert human eval). Tenant-scoped: another doctor's turn is a 404.
  Surfaced on ``GET /audit`` rows (``reviewed``, ``review_correct``,
  ``review_note``).
* Both feed the process-wide monitor's ``human_feedback`` section — aggregates
  only. Comment / note text lives only in the tenant-scoped audit store and
  never reaches ``GET /monitoring``.

Owner: Priyanshu (scope ``eval``).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from careline.adapters.llm.judge import KeylessJudge
from careline.domain.enums import ScopeCategory, Verdict
from careline.domain.model.decision import Decision, ReasoningTrace
from careline.services import online_monitor
from careline.services.audit_service import AuditService
from careline.services.online_monitor import DriftReference, OnlineMonitor
from tests.api.conftest import doctor_headers

_DOC = "dr-A"
_OTHER_DOC = "dr-B"
_PID = "p-feedback"
_OTHER_PID = "p-other"
_PIN = "246810"
_OTHER_PIN = "135790"
_SECRET_COMMENT = "zebra-quokka my neighbour said the dose felt wrong"
_SECRET_NOTE = "okapi-narwhal answer omitted the meal timing"


@pytest.fixture(autouse=True)
def _fresh_monitor():
    online_monitor.reset_monitor(
        OnlineMonitor(
            window=50, judge=KeylessJudge(), judge_sample_rate=0.0,
            reference=DriftReference({"in_scope": 1.0}, frozenset({"q"}), 0.0, 1.0, 1),
        )
    )
    yield
    online_monitor.reset_monitor()


def _decision(verdict: Verdict) -> Decision:
    if verdict is Verdict.ANSWER:
        return Decision(
            verdict=Verdict.ANSWER,
            answer_text="Take Paracetamol 500mg twice daily.",
            confidence=0.9, risk=0.1, trace=ReasoningTrace(steps=()), citations=(),
        )
    if verdict is Verdict.CLARIFY:
        return Decision(
            verdict=Verdict.CLARIFY,
            answer_text="Could you tell me which medicine you mean?",
            confidence=0.4, risk=0.1, trace=ReasoningTrace(steps=()), citations=(),
            scope=ScopeCategory.IN_SCOPE,
        )
    return Decision.escalate("red flag", scope=ScopeCategory.RED_FLAG, risk=1.0)


def _seed(client: TestClient, *, doctor_id: str, patient_id: str, verdict: Verdict) -> str:
    audit: AuditService = client.app.state.audit
    record = audit.log_turn(
        call_id=f"portal-{patient_id}",
        patient_id=patient_id,
        doctor_id=doctor_id,
        question="what should I take?",
        decision=_decision(verdict),
    )
    return record.turn_id


def _register(client, headers, *, pid, pin, caller):
    r = client.post("/patients", headers=headers,
                    json={"patient_id": pid, "caller_id": caller, "pin": pin})
    assert r.status_code == 201, r.text


def _portal(client, *, doctor_id, pid, pin) -> dict[str, str]:
    r = client.post("/patient/login",
                    json={"doctor_id": doctor_id, "patient_id": pid, "pin": pin})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def world(client: TestClient) -> dict:
    """dr-A with p-feedback and p-other; dr-B with its own same-named p-feedback."""
    a = doctor_headers(client, _DOC)
    b = doctor_headers(client, _OTHER_DOC)
    _register(client, a, pid=_PID, pin=_PIN, caller="+910000000101")
    _register(client, a, pid=_OTHER_PID, pin=_OTHER_PIN, caller="+910000000102")
    _register(client, b, pid=_PID, pin=_OTHER_PIN, caller="+910000000103")
    return {
        "doc_a": a,
        "doc_b": b,
        "portal": _portal(client, doctor_id=_DOC, pid=_PID, pin=_PIN),
        "other_patient_portal": _portal(client, doctor_id=_DOC, pid=_OTHER_PID, pin=_OTHER_PIN),
        "other_tenant_portal": _portal(client, doctor_id=_OTHER_DOC, pid=_PID, pin=_OTHER_PIN),
    }


def _rate(client, headers, turn_id, helpful=True, comment=None):
    body = {"turn_id": turn_id, "helpful": helpful}
    if comment is not None:
        body["comment"] = comment
    return client.post("/patient/feedback", headers=headers, json=body)


def _review(client, headers, turn_id, correct=True, note=None):
    body = {"correct": correct}
    if note is not None:
        body["note"] = note
    return client.post(f"/audit/turns/{turn_id}/review", headers=headers, json=body)


def _monitoring(client, headers) -> dict:
    r = client.get("/monitoring", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# --- turn_id is exposed where the portal needs it -----------------------------


def test_portal_ask_returns_the_logged_turn_id(client, world):
    r = client.post("/patient/ask", headers=world["portal"],
                    json={"question": "When is my follow-up?"})
    assert r.status_code == 200, r.text
    turn_id = r.json()["turn_id"]
    assert turn_id
    listed = client.get("/patient/questions", headers=world["portal"]).json()
    assert [q["turn_id"] for q in listed] == [turn_id]
    assert listed[0]["helpful"] is None  # not rated yet


# --- patient feedback ---------------------------------------------------------


def test_patient_feedback_requires_a_patient_session(client, world):
    turn = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.ANSWER)
    assert _rate(client, {}, turn).status_code == 401
    # A doctor token is not a patient session.
    assert _rate(client, world["doc_a"], turn).status_code == 401


@pytest.mark.parametrize("verdict", [Verdict.ANSWER, Verdict.CLARIFY])
def test_patient_rates_own_answer_or_clarify_turn(client, world, verdict):
    turn = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=verdict)
    r = _rate(client, world["portal"], turn, helpful=True, comment="clear, thanks")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["turn_id"] == turn
    assert body["helpful"] is True
    assert body["comment"] == "clear, thanks"
    assert body["rated_at"]
    listed = client.get("/patient/questions", headers=world["portal"]).json()
    assert listed[0]["helpful"] is True


def test_escalated_turns_are_not_ratable(client, world):
    turn = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.ESCALATE)
    r = _rate(client, world["portal"], turn)
    assert r.status_code == 409
    assert client.app.state.audit.feedback_for(turn) is None


def test_unknown_turn_is_404(client, world):
    assert _rate(client, world["portal"], "no-such-turn").status_code == 404


def test_other_patients_turn_under_same_doctor_is_404(client, world):
    turn = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.ANSWER)
    r = _rate(client, world["other_patient_portal"], turn)
    assert r.status_code == 404
    assert client.app.state.audit.feedback_for(turn) is None


def test_same_patient_id_under_other_doctor_is_404(client, world):
    """sev-0: dr-B's p-feedback must not be able to rate dr-A's p-feedback turn."""
    turn = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.ANSWER)
    r = _rate(client, world["other_tenant_portal"], turn, helpful=False)
    assert r.status_code == 404
    assert client.app.state.audit.feedback_for(turn) is None


def test_comment_length_is_capped_at_500(client, world):
    turn = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.ANSWER)
    assert _rate(client, world["portal"], turn, comment="x" * 501).status_code == 422
    assert _rate(client, world["portal"], turn, comment="x" * 500).status_code == 200


def test_feedback_body_rejects_unknown_fields(client, world):
    turn = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.ANSWER)
    r = client.post("/patient/feedback", headers=world["portal"],
                    json={"turn_id": turn, "helpful": True, "patient_id": _OTHER_PID})
    assert r.status_code == 422


def test_rerating_overwrites_and_counts_once(client, world):
    turn = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.ANSWER)
    assert _rate(client, world["portal"], turn, helpful=True).status_code == 200
    r = _rate(client, world["portal"], turn, helpful=False, comment="changed my mind")
    assert r.status_code == 200
    stored = client.app.state.audit.feedback_for(turn)
    assert stored.helpful is False and stored.comment == "changed my mind"
    hf = _monitoring(client, world["doc_a"])["human_feedback"]
    assert hf["patient_ratings"] == 1
    assert hf["patient_helpful_rate"] == 0.0


def test_patient_feedback_reaches_monitor_without_text(client, world):
    t1 = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.ANSWER)
    t2 = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.CLARIFY)
    assert _rate(client, world["portal"], t1, True, comment=_SECRET_COMMENT).status_code == 200
    assert _rate(client, world["portal"], t2, False).status_code == 200
    body = _monitoring(client, world["doc_a"])
    hf = body["human_feedback"]
    assert hf["patient_ratings"] == 2
    assert hf["patient_helpful_rate"] == 0.5
    dumped = json.dumps(body)
    assert "zebra" not in dumped and "quokka" not in dumped
    assert t1 not in dumped and t2 not in dumped and _PID not in dumped


# --- doctor review ------------------------------------------------------------


def test_doctor_review_requires_doctor_auth(client, world):
    turn = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.ANSWER)
    assert _review(client, {}, turn).status_code == 401
    assert _review(client, world["portal"], turn).status_code == 401


def test_doctor_reviews_own_answer_turn_and_audit_shows_it(client, world):
    turn = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.ANSWER)
    r = _review(client, world["doc_a"], turn, correct=False, note="missed meal timing")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["turn_id"] == turn
    assert body["patient_id"] == _PID
    assert body["verdict"] == "answer"
    assert body["correct"] is False
    assert body["note"] == "missed meal timing"
    assert body["reviewed_at"]

    rows = client.get("/audit", headers=world["doc_a"]).json()["turns"]
    row = next(t for t in rows if t["turn_id"] == turn)
    assert row["reviewed"] is True
    assert row["review_correct"] is False
    assert row["review_note"] == "missed meal timing"


def test_unreviewed_audit_rows_default_to_not_reviewed(client, world):
    turn = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.ANSWER)
    rows = client.get("/audit", headers=world["doc_a"]).json()["turns"]
    row = next(t for t in rows if t["turn_id"] == turn)
    assert row["reviewed"] is False
    assert row["review_correct"] is None
    assert row["review_note"] is None


def test_doctor_may_review_any_verdict_and_it_is_recorded(client, world):
    turn = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.ESCALATE)
    r = _review(client, world["doc_a"], turn, correct=True)
    assert r.status_code == 200
    assert r.json()["verdict"] == "escalate"


def test_other_doctors_turn_is_404(client, world):
    """sev-0: dr-B cannot review (or learn of) dr-A's turn."""
    turn = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.ANSWER)
    r = _review(client, world["doc_b"], turn, correct=False, note="sabotage")
    assert r.status_code == 404
    assert client.app.state.audit.review_for(turn) is None
    rows = client.get("/audit", headers=world["doc_b"]).json()["turns"]
    assert all(t["turn_id"] != turn for t in rows)


def test_review_unknown_turn_is_404(client, world):
    assert _review(client, world["doc_a"], "nope").status_code == 404


def test_review_note_is_capped(client, world):
    turn = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.ANSWER)
    assert _review(client, world["doc_a"], turn, note="n" * 1001).status_code == 422


def test_rereview_overwrites_and_counts_once(client, world):
    turn = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.ANSWER)
    assert _review(client, world["doc_a"], turn, correct=False).status_code == 200
    assert _review(client, world["doc_a"], turn, correct=True, note="fine").status_code == 200
    stored = client.app.state.audit.review_for(turn)
    assert stored.correct is True and stored.note == "fine"
    hf = _monitoring(client, world["doc_a"])["human_feedback"]
    assert hf["doctor_reviews"] == 1
    assert hf["doctor_rated_accuracy"] == 1.0


def test_doctor_review_reaches_monitor_without_note_text(client, world):
    ans = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.ANSWER)
    esc = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.ESCALATE)
    assert _review(client, world["doc_a"], ans, False, note=_SECRET_NOTE).status_code == 200
    assert _review(client, world["doc_a"], esc, True).status_code == 200
    body = _monitoring(client, world["doc_a"])
    hf = body["human_feedback"]
    assert hf["doctor_reviews"] == 2
    # Accuracy is over ANSWER turns only: 0 correct of 1 reviewed answer.
    assert hf["doctor_reviews_answer"] == 1
    assert hf["doctor_rated_accuracy"] == 0.0
    dumped = json.dumps(body)
    assert "okapi" not in dumped and "narwhal" not in dumped and ans not in dumped


# --- PHI lifecycle: clear + DPDP redaction cover feedback text ------------------


def test_clear_history_drops_feedback_and_reviews(client, world):
    turn = _seed(client, doctor_id=_DOC, patient_id=_PID, verdict=Verdict.ANSWER)
    _rate(client, world["portal"], turn, comment="ok")
    _review(client, world["doc_a"], turn, note="ok")
    assert client.delete("/patient/history", headers=world["portal"]).status_code == 204
    audit = client.app.state.audit
    assert audit.feedback_for(turn) is None
    assert audit.review_for(turn) is None


def test_redaction_nulls_comment_and_note_only_for_owning_tenant():
    audit = AuditService()
    mine = audit.log_turn(call_id="c1", patient_id=_PID, doctor_id=_DOC,
                          question="q", decision=_decision(Verdict.ANSWER))
    theirs = audit.log_turn(call_id="c2", patient_id=_PID, doctor_id=_OTHER_DOC,
                            question="q", decision=_decision(Verdict.ANSWER))
    for t, doc in ((mine, _DOC), (theirs, _OTHER_DOC)):
        audit.rate_turn(doctor_id=doc, patient_id=_PID, turn_id=t.turn_id,
                        helpful=True, comment="comment text")
        audit.review_turn(doctor_id=doc, turn_id=t.turn_id, correct=True,
                          note="note text", reviewed_by=doc)
    audit.redact_patient(_PID, doctor_id=_DOC)
    assert audit.feedback_for(mine.turn_id).comment is None
    assert audit.feedback_for(mine.turn_id).helpful is True  # the signal survives
    assert audit.review_for(mine.turn_id).note is None
    assert audit.feedback_for(theirs.turn_id).comment == "comment text"
    assert audit.review_for(theirs.turn_id).note == "note text"


def test_feedback_and_reviews_write_through_and_hydrate():
    class _Store:
        def __init__(self) -> None:
            self.feedback: dict = {}
            self.reviews: dict = {}

        def load(self):
            return [], [], [], []

        def save_turn(self, record):
            pass

        def save_call(self, record):
            pass

        def save_event(self, record):
            pass

        def save_resolution(self, record):
            pass

        def save_feedback(self, record):
            self.feedback[record.turn_id] = record

        def save_review(self, record):
            self.reviews[record.turn_id] = record

        def load_human_feedback(self):
            return list(self.feedback.values()), list(self.reviews.values())

    store = _Store()
    audit = AuditService(store=store)
    turn = audit.log_turn(call_id="c", patient_id=_PID, doctor_id=_DOC,
                          question="q", decision=_decision(Verdict.ANSWER))
    audit.rate_turn(doctor_id=_DOC, patient_id=_PID, turn_id=turn.turn_id, helpful=False)
    audit.review_turn(doctor_id=_DOC, turn_id=turn.turn_id, correct=True, reviewed_by=_DOC)
    assert set(store.feedback) == set(store.reviews) == {turn.turn_id}

    rebuilt = AuditService(store=store)
    assert rebuilt.feedback_for(turn.turn_id).helpful is False
    assert rebuilt.review_for(turn.turn_id).correct is True


def test_legacy_store_without_feedback_methods_still_works():
    """A store predating human feedback (no save_feedback/load) keeps in-memory."""

    class _Legacy:
        def load(self):
            return [], [], [], []

        def save_turn(self, record):
            pass

        def save_call(self, record):
            pass

        def save_event(self, record):
            pass

        def save_resolution(self, record):
            pass

    audit = AuditService(store=_Legacy())
    turn = audit.log_turn(call_id="c", patient_id=_PID, doctor_id=_DOC,
                          question="q", decision=_decision(Verdict.ANSWER))
    assert audit.rate_turn(doctor_id=_DOC, patient_id=_PID, turn_id=turn.turn_id,
                           helpful=True) is not None
    assert audit.feedback_for(turn.turn_id).helpful is True
