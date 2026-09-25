"""Telephony escalation sink (owner: Priyanshu)."""

from careline.adapters.telephony.stub import (  # noqa: F401
    EscalationPayload,
    TelephonyPort,
    TelephonyStub,
)

__all__ = ["EscalationPayload", "TelephonyPort", "TelephonyStub"]
