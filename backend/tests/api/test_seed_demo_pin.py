"""Demo seed PINs match the portal's 6-digit PIN field (SECURITY-2).

The patient login shows a 6-dot PIN field, but the seed generated 4-digit PINs —
10k combinations instead of 1M, and a UI that hinted at the wrong length. The seed
now generates 6-digit PINs and only accepts a ``CARELINE_DEMO_PIN`` of 4–6 digits.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import pytest

from scripts import seed_demo


def test_generated_pin_is_six_digits(monkeypatch):
    monkeypatch.delenv("CARELINE_DEMO_PIN", raising=False)
    pins = {seed_demo._demo_pin() for _ in range(50)}
    for pin, from_env in pins:
        assert from_env is False
        assert len(pin) == 6 and pin.isdigit(), pin
    assert len(pins) > 1  # random per run, never a published constant


@pytest.mark.parametrize("pin", ["1234", "12345", "123456", "000000"])
def test_env_pin_of_four_to_six_digits_is_accepted(monkeypatch, pin):
    monkeypatch.setenv("CARELINE_DEMO_PIN", f" {pin} ")
    assert seed_demo._demo_pin() == (pin, True)


@pytest.mark.parametrize("pin", ["123", "1234567", "12a456", "12 456", "abcdef"])
def test_env_pin_outside_four_to_six_digits_is_refused(monkeypatch, pin):
    monkeypatch.setenv("CARELINE_DEMO_PIN", pin)
    with pytest.raises(SystemExit):
        seed_demo._demo_pin()


# --- one PIN per patient (redteam-seed-shared-pin-all-patients) ----------------


def test_each_seeded_patient_gets_a_distinct_six_digit_pin(monkeypatch):
    """A PIN handed to one demo patient must not open any other patient."""
    monkeypatch.delenv("CARELINE_DEMO_PIN", raising=False)
    pins, from_env = seed_demo._patient_pins(list(seed_demo.PATIENTS_SEED))
    assert from_env is False
    assert set(pins) == set(seed_demo.PATIENTS_SEED)
    assert len(set(pins.values())) == len(pins)
    assert all(len(p) == 6 and p.isdigit() for p in pins.values())


def test_env_pin_derives_stable_distinct_per_patient_pins(monkeypatch):
    """CARELINE_DEMO_PIN seeds a reproducible table — still one PIN per patient."""
    monkeypatch.setenv("CARELINE_DEMO_PIN", "482913")
    first, from_env = seed_demo._patient_pins(list(seed_demo.PATIENTS_SEED))
    again, _ = seed_demo._patient_pins(list(seed_demo.PATIENTS_SEED))
    assert from_env is True
    assert first == again
    assert len(set(first.values())) == len(first)
    assert all(len(p) == 6 and p.isdigit() for p in first.values())


def test_patient_pins_never_collide_even_for_many_patients(monkeypatch):
    monkeypatch.setenv("CARELINE_DEMO_PIN", "1234")
    ids = [f"p{i}" for i in range(500)]
    pins, _ = seed_demo._patient_pins(ids)
    assert len(set(pins.values())) == len(ids)


def test_seed_audit_wipe_is_scoped_to_the_demo_tenant():
    """The seed must never delete another doctor's audit trail."""
    import inspect

    source = inspect.getsource(seed_demo.main)
    assert "delete_many({})" not in source
