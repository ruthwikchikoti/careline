"""Standalone demo API for the Live Agent Console (Ruthwik, scope ``graph``).

A tiny, auth-free, offline/keyless FastAPI app that runs a patient question through
the **real** multi-node LangGraph against a bundled demo patient and returns the
verdict + reasoning trace for the web console to visualise.

    cd backend
    pip install -e ".[api]"
    uvicorn careline.demo_server:app --reload     # http://localhost:8000

It deliberately bypasses auth/Mongo so the UI demo runs with zero setup; the
production path remains the authenticated ``/internal/run-question`` router. The
verdicts come from the same graph + gate chain as the rest of the system.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from careline.adapters.orchestration.graph import build_default_graph, resolve_llm_config
from careline.domain.enums import FactKind
from careline.domain.model.call_session import CallSession
from careline.domain.model.fact import Instruction, Medication
from careline.domain.model.patient import Patient
from careline.domain.model.temporal import Validity

# A fixed "now" so the demo patient's superseded facts behave deterministically.
_NOW = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
_PAST = datetime(2026, 1, 1, tzinfo=timezone.utc)
_SUPERSEDED = datetime(2026, 6, 1, tzinfo=timezone.utc)  # before _NOW → dropped from valid slice


def _demo_patient() -> Patient:
    """A patient with current + superseded facts, so the demo shows real routing."""
    return Patient(
        patient_id="demo-patient",
        doctor_id="demo-doctor",
        facts=(
            Medication(
                id="med-1",
                kind=FactKind.MEDICATION,
                validity=Validity(effective_from=_PAST),
                summary="Paracetamol 500mg twice daily for pain.",
                name="Paracetamol",
                dose="500mg",
                frequency="twice daily",
                approved_by="demo-doctor",
                approved_at=_PAST,
            ),
            Medication(
                id="med-2",
                kind=FactKind.MEDICATION,
                validity=Validity(effective_from=_PAST, superseded_at=_SUPERSEDED),
                summary="Amoxicillin 250mg thrice daily (discontinued).",
                name="Amoxicillin",
                dose="250mg",
                frequency="thrice daily",
                approved_by="demo-doctor",
                approved_at=_PAST,
            ),
            Instruction(
                id="instr-1",
                kind=FactKind.INSTRUCTION,
                validity=Validity(effective_from=_PAST),
                summary="Soft diet for 2 weeks post-surgery. Avoid spicy food.",
                text="Soft diet for 2 weeks post-surgery. Avoid spicy food.",
                approved_by="demo-doctor",
                approved_at=_PAST,
            ),
            Instruction(
                id="instr-2",
                kind=FactKind.INSTRUCTION,
                validity=Validity(effective_from=_PAST, superseded_at=_SUPERSEDED),
                summary="Liquid diet only for 48 hours post-surgery (expired).",
                text="Liquid diet only for 48 hours post-surgery.",
                approved_by="demo-doctor",
                approved_at=_PAST,
            ),
        ),
    )


# Build the graph once. Prefers a live LLM (OpenAI when OPENAI_API_KEY is set),
# falling back to the keyless heuristic twins offline.
_BACKEND = resolve_llm_config().backend.value
_graph = build_default_graph()
print(f"[careline.demo] reasoning backend: {_BACKEND}")


class AskIn(BaseModel):
    question: str


app = FastAPI(title="CareLine Demo API", description="Live Agent Console backend (demo).")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/demo/patient")
def demo_patient() -> dict:
    """The bundled patient's currently-valid facts (context shown in the console)."""
    patient = _demo_patient()
    valid = patient.valid_slice(_NOW)
    return {
        "patient_id": patient.patient_id,
        "doctor_id": patient.doctor_id,
        "backend": _BACKEND,
        "current_facts": [
            {"id": f.id, "kind": f.kind.value, "summary": f.summary} for f in valid.facts
        ],
    }


@app.post("/demo/ask")
def demo_ask(body: AskIn) -> dict:
    """Run one question through the real graph and return verdict + trace."""
    session = CallSession(
        call_id="web-demo",
        patient_id="demo-patient",
        doctor_id="demo-doctor",
        max_clarify_turns=2,
    )
    decision = _graph.run_question(
        question=body.question, patient=_demo_patient(), now=_NOW, session=session
    )
    return {
        "verdict": decision.verdict.value,
        "answer_text": decision.answer_text,
        "escalation_reason": decision.escalation_reason,
        "confidence": decision.confidence,
        "risk": decision.risk,
        "citations": list(decision.citations),
        "trace": [
            {
                "name": s.name,
                "status": s.status.value,
                "spec_section": s.spec_section,
                "detail": s.detail,
            }
            for s in decision.trace.steps
        ],
    }


__all__ = ["app"]
