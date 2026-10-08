"""Keyless extractor records a dose change (final evaluator finding
``redteam-keyless-supersede-not-recordable``).

At v7 the HeuristicExtractor found nothing in "Reduce Metformin to 500mg twice
daily. Stop the 1000mg dose." (fact_count 0, approve -> 400), so in keyless
mode the old 1000mg dose stayed current and supersession could only be shown
with seeded data. A dose-change verb (reduce / lower / decrease / increase /
raise / change / switch / adjust ... to <dose>) now yields ONE medication fact
carrying the NEW dose only; approval supersedes the old fact by drug name.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from careline.domain.enums import FactKind
from careline.services.extraction_service import HeuristicExtractor

_NOW = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)


def _meds(transcript: str):
    rec = HeuristicExtractor().extract(transcript=transcript, consultation_id="c", now=_NOW)
    return [f for f in rec.facts if f.kind is FactKind.MEDICATION]


@pytest.mark.parametrize(
    ("transcript", "name", "dose", "frequency"),
    [
        ("Reduce Metformin to 500mg twice daily. Stop the 1000mg dose.",
         "Metformin", "500mg", "twice daily"),
        ("Lower the metformin dose to 500 mg once daily.", "metformin", "500 mg", "once daily"),
        ("Increase Amlodipine to 10mg once daily.", "Amlodipine", "10mg", "once daily"),
        ("Raise the dose of Atorvastatin to 40mg at night.", "Atorvastatin", "40mg", "at night"),
        ("Change Paracetamol from 1000mg to 500mg three times daily.",
         "Paracetamol", "500mg", "three times daily"),
        ("Switch Levothyroxine to 75mcg once daily.", "Levothyroxine", "75mcg", "once daily"),
        ("Decrease Prednisolone to 5mg.", "Prednisolone", "5mg", None),
    ],
)
def test_dose_change_yields_one_medication_fact_with_the_new_dose(transcript, name, dose,
                                                                  frequency):
    meds = _meds(transcript)
    assert len(meds) == 1, meds
    m = meds[0]
    assert m.name == name and m.dose == dose and m.frequency == frequency
    assert "1000" not in m.summary  # never the old dose


@pytest.mark.parametrize(
    "transcript",
    [
        "Reduce it to 500mg.",                         # no drug named
        "Reduce stress and switch to a soft diet.",    # no dose
        "Switch warfarin to apixaban.",                # a drug switch, not a dose
    ],
)
def test_no_medication_fact_without_a_drug_and_a_dose(transcript):
    assert _meds(transcript) == []
