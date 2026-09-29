"""#2 — OpenAI Extractor adapter: structured mapping + fail-closed contract.

Keyless: a fake client is injected, so the real SDK is never imported or called.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from careline.adapters.llm.extraction_backend import OpenAIExtractor, _ExtractionDTO
from careline.domain.enums import FactKind
from careline.domain.ports.reasoning import ReasonerUnavailable
from careline.services.extraction_service import ExtractedFactDTO, ExtractedRecord

NOW = datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)


class _Response:
    def __init__(self, output_parsed):
        self.output_parsed = output_parsed


class _FakeResponses:
    def __init__(self, *, returns=None, raises=None):
        self._returns = returns
        self._raises = raises
        self.last_kwargs = None
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        if self._raises is not None:
            raise self._raises
        return _Response(self._returns)


class _FakeClient:
    def __init__(self, *, returns=None, raises=None):
        self.responses = _FakeResponses(returns=returns, raises=raises)


def _dto(*facts) -> _ExtractionDTO:
    return _ExtractionDTO(facts=list(facts))


class TestOpenAIExtractor:
    def test_maps_facts_into_extracted_record(self):
        parsed = _dto(
            ExtractedFactDTO(
                kind=FactKind.MEDICATION,
                summary="Continue paracetamol 500mg twice daily",
                name="paracetamol",
                dose="500mg",
                frequency="twice daily",
            ),
            ExtractedFactDTO(
                kind=FactKind.INSTRUCTION,
                summary="follow a soft diet",
                text="follow a soft diet",
            ),
        )
        extractor = OpenAIExtractor(client=_FakeClient(returns=parsed))
        record = extractor.extract(
            transcript="Continue paracetamol 500mg twice daily. Follow a soft diet.",
            consultation_id="c-1",
            now=NOW,
        )
        assert isinstance(record, ExtractedRecord)
        # consultation_id / extracted_at are stamped by the caller, not the model.
        assert record.consultation_id == "c-1"
        assert record.extracted_at == NOW
        assert len(record.facts) == 2
        # Natural phrasing the regex twin misses is now structured + materialisable.
        domain_facts = record.to_facts(now=NOW)
        assert {f.kind for f in domain_facts} == {FactKind.MEDICATION, FactKind.INSTRUCTION}

    def test_empty_transcript_returns_empty_record_without_calling_api(self):
        client = _FakeClient(raises=RuntimeError("must not be called"))
        record = OpenAIExtractor(client=client).extract(
            transcript="   ", consultation_id="c-2", now=NOW
        )
        assert record.facts == ()
        assert client.responses.calls == 0

    def test_uses_text_format_and_no_sampling_knobs(self):
        client = _FakeClient(returns=_dto())
        OpenAIExtractor(client=client).extract(
            transcript="something", consultation_id="c-3", now=NOW
        )
        kwargs = client.responses.last_kwargs
        assert kwargs["text_format"] is _ExtractionDTO
        for forbidden in ("temperature", "top_p"):
            assert forbidden not in kwargs

    def test_sdk_error_fails_closed(self):
        extractor = OpenAIExtractor(client=_FakeClient(raises=RuntimeError("boom")))
        with pytest.raises(ReasonerUnavailable):
            extractor.extract(transcript="x", consultation_id="c-4", now=NOW)

    def test_none_parse_fails_closed(self):
        extractor = OpenAIExtractor(client=_FakeClient(returns=None))
        with pytest.raises(ReasonerUnavailable):
            extractor.extract(transcript="x", consultation_id="c-5", now=NOW)
