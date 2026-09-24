"""Internal API key verification for service-to-service calls (NR-5).

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import hmac

from careline.adapters.auth.principals import InternalPrincipal


class KeyInvalid(ValueError):
    """Raised when the internal API key is missing or incorrect."""


def verify_internal_key(provided: str, expected: str) -> InternalPrincipal:
    """Constant-time compare of the internal API key."""
    if not hmac.compare_digest(provided, expected):
        raise KeyInvalid("invalid internal API key")
    return InternalPrincipal()


__all__ = ["KeyInvalid", "verify_internal_key"]
