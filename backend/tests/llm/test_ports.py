"""SR-1 — the reasoning ports: abstractness and the fail-closed contract."""

from __future__ import annotations

import pytest

from careline.domain.ports.reasoning import Reasoner, ReasonerUnavailable, Verifier


def test_reasoner_is_abstract():
    with pytest.raises(TypeError):
        Reasoner()  # type: ignore[abstract]


def test_verifier_is_abstract():
    with pytest.raises(TypeError):
        Verifier()  # type: ignore[abstract]


def test_reasoner_unavailable_is_a_runtimeerror():
    # A bare `except Exception` in a caller must still trip the safe path.
    assert issubclass(ReasonerUnavailable, RuntimeError)


def test_concrete_subclass_must_implement_propose():
    class Half(Reasoner):
        pass

    with pytest.raises(TypeError):
        Half()  # type: ignore[abstract]
