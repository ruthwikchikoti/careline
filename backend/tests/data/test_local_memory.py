"""LocalMemoryProvider tests (NG-2).

The offline Layer-2 twin: it retrieves only within a patient's namespace, ranks by
token overlap, returns nothing safely, and forgets a namespace on erasure. The
cross-patient case is the Layer-2 face of the sev-0 rule.
"""

import asyncio

from careline.adapters.memory.local import LocalMemoryProvider
from careline.adapters.memory.seed import SEED_DOCTOR_ID, SEED_NOW, SEED_PATIENT_ID, seed_patient


def _indexed() -> LocalMemoryProvider:
    provider = LocalMemoryProvider()
    vs = seed_patient().valid_slice(SEED_NOW)
    asyncio.run(
        provider.index(doctor_id=SEED_DOCTOR_ID, patient_id=SEED_PATIENT_ID, slice=vs)
    )
    return provider


def _retrieve(provider, *, doctor_id=SEED_DOCTOR_ID, patient_id=SEED_PATIENT_ID, query, k=5):
    return asyncio.run(
        provider.retrieve(doctor_id=doctor_id, patient_id=patient_id, query=query, k=k)
    )


def test_retrieves_relevant_fact():
    hits = _retrieve(_indexed(), query="can I eat spicy food on my diet?")
    assert hits[0].fact_id == "instr-1"  # the soft-diet instruction


def test_only_indexes_valid_facts():
    # The discontinued antibiotic is not in the valid slice, so it is never indexed.
    hits = _retrieve(_indexed(), query="amoxicillin antibiotic")
    assert all(h.fact_id != "med-2" for h in hits)


def test_unknown_query_returns_empty():
    assert _retrieve(_indexed(), query="quantum chromodynamics") == ()


def test_cross_patient_retrieval_returns_nothing():
    # Another patient under the same doctor has an empty namespace — no leak.
    hits = _retrieve(_indexed(), patient_id="patient-Z", query="diet")
    assert hits == ()


def test_cross_doctor_retrieval_returns_nothing():
    hits = _retrieve(_indexed(), doctor_id="dr-OTHER", query="diet")
    assert hits == ()


def test_forget_clears_namespace():
    provider = _indexed()
    asyncio.run(provider.forget(doctor_id=SEED_DOCTOR_ID, patient_id=SEED_PATIENT_ID))
    assert _retrieve(provider, query="diet") == ()


def test_k_limits_results():
    hits = _retrieve(_indexed(), query="diet food pain review allergy", k=1)
    assert len(hits) <= 1
