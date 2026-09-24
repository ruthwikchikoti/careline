"""Authentication adapters — JWT and internal API key (NR-5).

Owner: Naresh (scope ``api``).
"""

from careline.adapters.auth.internal_key import KeyInvalid, verify_internal_key
from careline.adapters.auth.jwt import TokenInvalid, decode_doctor_token, encode_doctor_token
from careline.adapters.auth.principals import DoctorPrincipal, InternalPrincipal

__all__ = [
    "DoctorPrincipal",
    "InternalPrincipal",
    "KeyInvalid",
    "TokenInvalid",
    "decode_doctor_token",
    "encode_doctor_token",
    "verify_internal_key",
]
