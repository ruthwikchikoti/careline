"""Seed realistic demo data into Mongo Atlas under the ``dr-asha`` account.

Run once to populate the database the web UI reads, so the Patients list, the
Live-Console patient picker, and the patient-record screens all have real,
clinically-coherent data to show — instead of an empty database.

    cd backend && source .venv/bin/activate
    python -m scripts.seed_demo            # seeds under dr-asha (the login default)

Idempotent: it first clears any existing ``dr-asha`` facts/patients/consultations
(this is the demo tenant, so a clean reset is intended), then inserts a fixed set
of patients with approved, currently-valid facts plus a couple of superseded ones
so the history timeline has something to show.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from careline.adapters.mongo import MongoPatientRepository, create_client, ensure_indexes
from careline.adapters.mongo.audit_store import CALLS, EVENTS, RESOLUTIONS, TURNS
from careline.adapters.mongo.client import CONSULTATIONS, FACTS, PATIENTS
from careline.config import get_settings
from careline.domain.model.fact import (
    Allergy,
    Diagnosis,
    FollowUp,
    Instruction,
    Medication,
    Observation,
)
from careline.domain.model.patient import PatientIdentity
from careline.domain.model.temporal import Validity
from careline.services.patient_lookup_service import hash_pin

DOCTOR_ID = "dr-asha"
NOW = datetime.now(timezone.utc)
PAST = NOW - timedelta(days=30)
OLDER = NOW - timedelta(days=120)
SUPERSEDED = NOW - timedelta(days=10)  # closed before now → drops into history
SOON = NOW + timedelta(days=14)


def _v(effective_from: datetime = PAST, superseded_at: datetime | None = None) -> Validity:
    return Validity(effective_from=effective_from, superseded_at=superseded_at)


def _approved(**kw: object) -> dict[str, object]:
    return {"approved_by": DOCTOR_ID, "approved_at": PAST, **kw}


# patient_id -> (caller_id, [facts]). Each patient is a clinically-coherent
# post-consultation record rich enough to practise a real spread of questions:
# straight answers (dose/diet/follow-up/allergy), a superseded fact (staleness
# safety), and cross-condition / red-flag escalations driven by the question.
PATIENTS_SEED: dict[str, tuple[str, list]] = {
    # Showcase: day-5 post laparoscopic appendectomy.
    "ravi-kumar": (
        "+91-90000-11111",
        [
            Diagnosis(id="ravi-dx-1", validity=_v(OLDER), summary="Laparoscopic appendectomy — post-operative day 5.",
                      condition="Post-appendectomy recovery", code="K35", **_approved(approved_at=OLDER)),
            Medication(id="ravi-med-1", validity=_v(), summary="Paracetamol 500mg twice daily for post-op pain.",
                       name="Paracetamol", dose="500mg", frequency="twice daily", **_approved()),
            Medication(id="ravi-med-2", validity=_v(OLDER, SUPERSEDED),
                       summary="Amoxicillin 250mg thrice daily (course completed, discontinued).",
                       name="Amoxicillin", dose="250mg", frequency="thrice daily", **_approved(approved_at=OLDER)),
            Instruction(id="ravi-ins-1", validity=_v(), summary="Soft diet for 2 weeks post-surgery; avoid spicy food.",
                        text="Soft diet for 2 weeks post-surgery; avoid spicy food.", **_approved()),
            Instruction(id="ravi-ins-2", validity=_v(), summary="Keep the incision clean and dry; change the dressing daily.",
                        text="Keep the incision clean and dry; change the dressing daily.", **_approved()),
            Allergy(id="ravi-alg-1", validity=_v(), summary="Penicillin allergy — causes rash.",
                    substance="Penicillin", reaction="rash", severity="moderate", **_approved()),
            Observation(id="ravi-obs-1", validity=_v(), summary="Temperature 37.6°C at discharge.",
                        metric="Temperature", value="37.6", unit="°C", **_approved()),
            FollowUp(id="ravi-fu-1", validity=_v(), summary="Post-op review in 2 weeks with Dr. Asha.",
                     scheduled_for=SOON, with_whom="Dr. Asha", **_approved()),
        ],
    ),
    # Type-2 diabetes — good for cross-condition ("sweets after surgery with my diabetes?").
    "meera-shah": (
        "+91-90000-22222",
        [
            Diagnosis(id="meera-dx-1", validity=_v(OLDER), summary="Type-2 diabetes mellitus.",
                      condition="Type-2 diabetes mellitus", code="E11", **_approved(approved_at=OLDER)),
            Medication(id="meera-med-1", validity=_v(), summary="Metformin 500mg twice daily with meals.",
                       name="Metformin", dose="500mg", frequency="twice daily", **_approved()),
            Medication(id="meera-med-2", validity=_v(), summary="Glimepiride 1mg once before breakfast.",
                       name="Glimepiride", dose="1mg", frequency="once daily", route="before breakfast", **_approved()),
            Instruction(id="meera-ins-1", validity=_v(), summary="Low-sugar diet; check blood glucose each morning.",
                        text="Low-sugar diet; check blood glucose each morning.", **_approved()),
            Observation(id="meera-obs-1", validity=_v(), summary="HbA1c 7.8% (last lab).",
                        metric="HbA1c", value="7.8", unit="%", **_approved()),
            FollowUp(id="meera-fu-1", validity=_v(), summary="Endocrinology review in 3 weeks.",
                     scheduled_for=SOON, with_whom="Endocrinology", **_approved()),
        ],
    ),
    # Hypertension + lipids — three cardiac medications.
    "arjun-nair": (
        "+91-90000-33333",
        [
            Diagnosis(id="arjun-dx-1", validity=_v(OLDER), summary="Essential hypertension.",
                      condition="Essential hypertension", code="I10", **_approved(approved_at=OLDER)),
            Diagnosis(id="arjun-dx-2", validity=_v(OLDER), summary="Hyperlipidaemia.",
                      condition="Hyperlipidaemia", code="E78.5", **_approved(approved_at=OLDER)),
            Medication(id="arjun-med-1", validity=_v(), summary="Atorvastatin 20mg once at night.",
                       name="Atorvastatin", dose="20mg", frequency="once daily", route="at night", **_approved()),
            Medication(id="arjun-med-2", validity=_v(), summary="Aspirin 75mg once daily after breakfast.",
                       name="Aspirin", dose="75mg", frequency="once daily", **_approved()),
            Medication(id="arjun-med-3", validity=_v(), summary="Amlodipine 5mg once daily in the morning.",
                       name="Amlodipine", dose="5mg", frequency="once daily", route="morning", **_approved()),
            Instruction(id="arjun-ins-1", validity=_v(), summary="Low-salt diet; 30 min brisk walk daily.",
                        text="Low-salt diet; 30 min brisk walk daily.", **_approved()),
            Observation(id="arjun-obs-1", validity=_v(), summary="Blood pressure 138/88 mmHg.",
                        metric="Blood pressure", value="138/88", unit="mmHg", **_approved()),
            FollowUp(id="arjun-fu-1", validity=_v(), summary="Cardiology review in 2 weeks.",
                     scheduled_for=SOON, with_whom="Cardiology", **_approved()),
        ],
    ),
    # Knee osteoarthritis — orthopaedic recovery.
    "priya-iyer": (
        "+91-90000-44444",
        [
            Diagnosis(id="priya-dx-1", validity=_v(OLDER), summary="Right knee osteoarthritis.",
                      condition="Right knee osteoarthritis", code="M17", **_approved(approved_at=OLDER)),
            Medication(id="priya-med-1", validity=_v(), summary="Ibuprofen 400mg as needed for knee pain (max thrice daily).",
                       name="Ibuprofen", dose="400mg", frequency="as needed", **_approved()),
            Instruction(id="priya-ins-1", validity=_v(), summary="Physiotherapy three times a week; ice the knee after exercise.",
                        text="Physiotherapy three times a week; ice the knee after exercise.", **_approved()),
            Observation(id="priya-obs-1", validity=_v(), summary="Blood pressure 130/85 mmHg.",
                        metric="Blood pressure", value="130/85", unit="mmHg", **_approved()),
            FollowUp(id="priya-fu-1", validity=_v(), summary="Orthopaedic review in 2 weeks.",
                     scheduled_for=SOON, with_whom="Orthopaedics", **_approved()),
        ],
    ),
    # Asthma — reliever + preventer inhaler, dust allergy.
    "sanjay-rao": (
        "+91-90000-55555",
        [
            Diagnosis(id="sanjay-dx-1", validity=_v(OLDER), summary="Mild persistent asthma.",
                      condition="Mild persistent asthma", code="J45", **_approved(approved_at=OLDER)),
            Medication(id="sanjay-med-1", validity=_v(), summary="Salbutamol inhaler, 2 puffs when breathless.",
                       name="Salbutamol", dose="2 puffs", frequency="as needed", route="inhaled", **_approved()),
            Medication(id="sanjay-med-2", validity=_v(), summary="Budesonide inhaler, 1 puff twice daily (preventer).",
                       name="Budesonide", dose="1 puff", frequency="twice daily", route="inhaled", **_approved()),
            Instruction(id="sanjay-ins-1", validity=_v(), summary="Avoid smoke and dust; use inhaler before exertion.",
                        text="Avoid smoke and dust; use inhaler before exertion.", **_approved()),
            Allergy(id="sanjay-alg-1", validity=_v(), summary="Dust-mite allergy — triggers wheezing.",
                    substance="Dust mites", reaction="wheezing", severity="moderate", **_approved()),
            FollowUp(id="sanjay-fu-1", validity=_v(), summary="Pulmonology review in 4 weeks.",
                     scheduled_for=SOON, with_whom="Pulmonology", **_approved()),
        ],
    ),
}


async def main() -> None:
    settings = get_settings()
    if not settings.mongo_uri:
        raise SystemExit("CARELINE_MONGO_URI is not set — point it at your Atlas database first.")

    client = create_client(settings.mongo_uri)
    db = client["careline"]
    await ensure_indexes(db)
    repo = MongoPatientRepository(db)

    # clean reset of the demo tenant so re-running doesn't duplicate
    for col in (FACTS, PATIENTS, CONSULTATIONS):
        res = await db[col].delete_many({"doctor_id": DOCTOR_ID})
        print(f"cleared {res.deleted_count:>3} from {col}")
    # wipe the audit trail entirely — a clean slate for a fresh demo/practice run
    # (questions, calls, events, doctor replies). Restart the API afterwards so its
    # in-memory read model re-hydrates from the now-empty collections.
    for col in (TURNS, CALLS, EVENTS, RESOLUTIONS):
        res = await db[col].delete_many({})
        print(f"cleared {res.deleted_count:>3} from {col}")

    total_facts = 0
    for patient_id, (caller_id, facts) in PATIENTS_SEED.items():
        await repo.upsert_identity(identity=PatientIdentity(
            patient_id=patient_id, doctor_id=DOCTOR_ID, caller_id=caller_id,
            pin_hmac=hash_pin(pin="1234", secret=settings.pin_hmac_secret),
        ))
        await repo.add_facts(doctor_id=DOCTOR_ID, patient_id=patient_id, facts=tuple(facts))
        current = sum(1 for f in facts if f.validity.superseded_at is None)
        total_facts += len(facts)
        print(f"  seeded {patient_id:14} caller {caller_id}  ·  {current} current / {len(facts)} total facts")

    print(f"\nDone. {len(PATIENTS_SEED)} patients, {total_facts} facts under '{DOCTOR_ID}' (PIN 1234).")
    print("Log in as 'dr-asha' in the web UI to see them.")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
