"""Extraction agent — transcript to drafted facts (NR-3).

Owns the Track A extraction use-case: given a consented consultation with a
transcript, invoke the :class:`~careline.domain.ports.extraction.Extractor` port,
materialise unapproved domain facts, and attach them to the consultation draft.
On :class:`~careline.domain.ports.reasoning.ReasonerUnavailable` the service
persists **nothing** — fail-closed, no partial draft.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from careline.domain.enums import FactKind
from careline.domain.model.fact import (
    Allergy,
    Diagnosis,
    Fact,
    FollowUp,
    Instruction,
    Medication,
    Observation,
)
from careline.domain.model.temporal import Validity
from careline.domain.ports.extraction import Extractor
from careline.services.audit_service import AuditEventKind, AuditService
from careline.services.consultation_service import (
    ConsentViolation,
    ConsultationNotFound,
    ConsultationService,
)


class ExtractedFactDTO(BaseModel):
    """Flat wire shape for one extracted fact before domain materialisation."""

    model_config = ConfigDict(extra="forbid")

    kind: FactKind
    summary: str
    name: str | None = None
    dose: str | None = None
    frequency: str | None = None
    route: str | None = None
    text: str | None = None
    condition: str | None = None
    code: str | None = None
    metric: str | None = None
    value: str | None = None
    unit: str | None = None
    substance: str | None = None
    reaction: str | None = None
    severity: str | None = None
    scheduled_for: datetime | None = None
    with_whom: str | None = None


class ExtractedRecord(BaseModel):
    """Structured output of the Extraction agent for one consultation."""

    model_config = ConfigDict(extra="forbid")

    consultation_id: str
    extracted_at: datetime
    facts: tuple[ExtractedFactDTO, ...] = ()

    def to_facts(self, *, now: datetime) -> tuple[Fact, ...]:
        """Materialise DTOs into unapproved domain facts with open validity."""
        validity = Validity(effective_from=now)
        facts: list[Fact] = []
        for index, dto in enumerate(self.facts):
            fact_id = f"{self.consultation_id}-fact-{index:03d}"
            facts.append(_materialise_fact(dto, fact_id=fact_id, validity=validity))
        return tuple(facts)


def _materialise_fact(
    dto: ExtractedFactDTO, *, fact_id: str, validity: Validity
) -> Fact:
    base = {
        "id": fact_id,
        "validity": validity,
        "summary": dto.summary,
        "approved_by": None,
        "approved_at": None,
    }
    if dto.kind is FactKind.MEDICATION:
        if not dto.name:
            raise ValueError("medication extraction requires name")
        return Medication(
            **base,
            kind=FactKind.MEDICATION,
            name=dto.name,
            dose=dto.dose,
            frequency=dto.frequency,
            route=dto.route,
        )
    if dto.kind is FactKind.INSTRUCTION:
        text = dto.text or dto.summary
        return Instruction(**base, kind=FactKind.INSTRUCTION, text=text)
    if dto.kind is FactKind.DIAGNOSIS:
        if not dto.condition:
            raise ValueError("diagnosis extraction requires condition")
        return Diagnosis(
            **base,
            kind=FactKind.DIAGNOSIS,
            condition=dto.condition,
            code=dto.code,
        )
    if dto.kind is FactKind.OBSERVATION:
        if not dto.metric or not dto.value:
            raise ValueError("observation extraction requires metric and value")
        return Observation(
            **base,
            kind=FactKind.OBSERVATION,
            metric=dto.metric,
            value=dto.value,
            unit=dto.unit,
        )
    if dto.kind is FactKind.ALLERGY:
        if not dto.substance:
            raise ValueError("allergy extraction requires substance")
        return Allergy(
            **base,
            kind=FactKind.ALLERGY,
            substance=dto.substance,
            reaction=dto.reaction,
            severity=dto.severity,
        )
    if dto.kind is FactKind.FOLLOW_UP:
        return FollowUp(
            **base,
            kind=FactKind.FOLLOW_UP,
            scheduled_for=dto.scheduled_for,
            with_whom=dto.with_whom,
        )
    raise ValueError(f"unsupported fact kind: {dto.kind}")


_MEDICATION_RE = re.compile(
    r"(?i)(?:prescribed|prescribe|take|started on"
    r"|continue|keep|maintain|stay on)\s+"
    r"([A-Za-z][A-Za-z0-9-]*)"
    r"(?:\s+(\d+\s*mg))?"
    r"(?:\s+(twice daily|once daily|three times daily|every\s+\d+\s+hours))?"
)
_INSTRUCTION_RE = re.compile(
    r"(?i)(?:patient should|advised to|must|should"
    r"|follow|stick to|continue to|maintain)\s+(.+?)(?:\.|$)"
)
_REST_RE = re.compile(r"(?i)rest for\s+(.+?)(?:\.|$)")
_DIAGNOSIS_RE = re.compile(
    r"(?i)(?:diagnosed with|diagnosis of|diagnosis:)\s+(.+?)(?:\.|$)"
)
_OBSERVATION_RE = re.compile(
    r"(?i)(?:blood pressure|bp|temperature|temp|heart rate|pulse)"
    r"\s*(?:is|was|:)?\s*([\d./]+\s*(?:mmhg|bpm|°?c|f)?)"
)
_ALLERGY_RE = re.compile(r"(?i)(?:allergic to|allergy to)\s+(.+?)(?:\.|$)")
_FOLLOW_UP_RE = re.compile(
    r"(?i)(?:follow[- ]?up|follow up appointment|review)\s+(?:in|after)?\s*(.+?)(?:\.|$)"
)


class HeuristicExtractor(Extractor):
    """Offline Extractor: surfaces only what is explicitly present in the transcript."""

    def extract(
        self,
        *,
        transcript: str,
        consultation_id: str,
        now: datetime,
    ) -> ExtractedRecord:
        stripped = transcript.strip()
        if not stripped:
            return ExtractedRecord(
                consultation_id=consultation_id,
                extracted_at=now,
                facts=(),
            )

        facts: list[ExtractedFactDTO] = []
        seen: set[str] = set()

        for match in _MEDICATION_RE.finditer(stripped):
            name = match.group(1)
            key = f"med:{name.lower()}"
            if key in seen:
                continue
            seen.add(key)
            dose = match.group(2)
            frequency = match.group(3)
            parts = [name]
            if dose:
                parts.append(dose.strip())
            if frequency:
                parts.append(frequency.strip())
            summary = " ".join(parts)
            facts.append(
                ExtractedFactDTO(
                    kind=FactKind.MEDICATION,
                    summary=summary,
                    name=name,
                    dose=dose.strip() if dose else None,
                    frequency=frequency.strip() if frequency else None,
                )
            )

        for pattern in (_INSTRUCTION_RE, _REST_RE):
            for match in pattern.finditer(stripped):
                text = match.group(1).strip().rstrip(".")
                if not text:
                    continue
                key = f"instr:{text.lower()}"
                if key in seen:
                    continue
                seen.add(key)
                facts.append(
                    ExtractedFactDTO(
                        kind=FactKind.INSTRUCTION,
                        summary=text,
                        text=text,
                    )
                )

        for match in _DIAGNOSIS_RE.finditer(stripped):
            condition = match.group(1).strip().rstrip(".")
            key = f"dx:{condition.lower()}"
            if key in seen:
                continue
            seen.add(key)
            facts.append(
                ExtractedFactDTO(
                    kind=FactKind.DIAGNOSIS,
                    summary=condition,
                    condition=condition,
                )
            )

        for match in _OBSERVATION_RE.finditer(stripped):
            value = match.group(1).strip()
            metric = match.group(0).split()[0]
            key = f"obs:{metric.lower()}:{value.lower()}"
            if key in seen:
                continue
            seen.add(key)
            facts.append(
                ExtractedFactDTO(
                    kind=FactKind.OBSERVATION,
                    summary=f"{metric} {value}",
                    metric=metric,
                    value=value,
                )
            )

        for match in _ALLERGY_RE.finditer(stripped):
            substance = match.group(1).strip().rstrip(".")
            key = f"allergy:{substance.lower()}"
            if key in seen:
                continue
            seen.add(key)
            facts.append(
                ExtractedFactDTO(
                    kind=FactKind.ALLERGY,
                    summary=f"allergy to {substance}",
                    substance=substance,
                )
            )

        for match in _FOLLOW_UP_RE.finditer(stripped):
            detail = match.group(1).strip().rstrip(".")
            key = f"fu:{detail.lower()}"
            if key in seen:
                continue
            seen.add(key)
            facts.append(
                ExtractedFactDTO(
                    kind=FactKind.FOLLOW_UP,
                    summary=f"follow-up {detail}",
                    with_whom=detail,
                )
            )

        return ExtractedRecord(
            consultation_id=consultation_id,
            extracted_at=now,
            facts=tuple(facts),
        )


class ExtractionService:
    """Run transcript extraction and draft facts onto a consented consultation."""

    def __init__(
        self,
        *,
        extractor: Extractor,
        consultation_svc: ConsultationService,
        audit: AuditService | None = None,
    ) -> None:
        self._extractor = extractor
        self._consultation_svc = consultation_svc
        self._audit = audit

    async def extract(
        self,
        *,
        doctor_id: str,
        consultation_id: str,
        now: datetime | None = None,
    ):
        """Extract facts from the consultation transcript and attach as draft."""
        now = now or datetime.now(timezone.utc)
        consultation = await self._consultation_svc.get(
            doctor_id=doctor_id, consultation_id=consultation_id
        )
        if consultation is None:
            raise ConsultationNotFound(
                f"consultation {consultation_id!r} not found for doctor {doctor_id!r}"
            )
        if not consultation.is_processable:
            raise ConsentViolation(
                "cannot extract from a consultation without active consent"
            )

        if consultation.transcript is None or not consultation.transcript.strip():
            facts: tuple[Fact, ...] = ()
        else:
            record = self._extractor.extract(
                transcript=consultation.transcript,
                consultation_id=consultation_id,
                now=now,
            )
            facts = record.to_facts(now=now)

        updated = await self._consultation_svc.attach_facts(
            doctor_id=doctor_id,
            consultation_id=consultation_id,
            facts=facts,
            now=now,
        )
        if self._audit is not None:
            self._audit.log_event(
                AuditEventKind.SYSTEM,
                patient_id=consultation.patient_id,
                doctor_id=doctor_id,
                detail=f"{len(facts)} fact(s) drafted from transcript",
                logged_at=now,
            )
        return updated


__all__ = [
    "ExtractedFactDTO",
    "ExtractedRecord",
    "ExtractionService",
    "HeuristicExtractor",
]
