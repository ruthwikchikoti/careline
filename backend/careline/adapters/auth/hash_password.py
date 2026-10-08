"""Per-doctor password hashes + a tiny CLI to mint them (SECURITY-2).

``CARELINE_DOCTOR_CREDENTIALS`` maps each doctor id to its own password hash::

    CARELINE_DOCTOR_CREDENTIALS="dr-asha:pbkdf2_sha256$600000$<salt-hex>$<hash-hex>,dr-x:..."

Supported hash formats (``$``-delimited, so they never collide with the ``,`` and
``:`` separators of the env value):

* ``pbkdf2_sha256$<iterations>$<salt-hex>$<hash-hex>`` — salted, slow; what the
  CLI generates (default 600k iterations, the OWASP 2023 floor for PBKDF2-SHA256);
* ``sha256$<hex>`` — plain SHA-256, accepted for compatibility only (unsalted,
  fast: prefer pbkdf2).

Every compare is constant-time (:func:`hmac.compare_digest`) and an unparseable
hash never verifies (fail closed). Generate an entry with::

    python -m careline.adapters.auth.hash_password --doctor-id dr-asha
    # prompts for the password twice, prints  dr-asha:pbkdf2_sha256$...

(``--stdin`` reads one line from stdin instead of prompting.) In a ``.env`` file
the ``$`` characters are literal; in docker-compose YAML escape each as ``$$``.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import secrets
import sys
from collections.abc import Sequence

PBKDF2_PREFIX = "pbkdf2_sha256"
SHA256_PREFIX = "sha256"
DEFAULT_ITERATIONS = 600_000
_SALT_BYTES = 16
_MIN_ITERATIONS = 1_000
_MAX_ITERATIONS = 10_000_000


def _is_hex(value: str) -> bool:
    if not value or len(value) % 2:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


def _parse(encoded: str) -> tuple[str, int, bytes, bytes] | None:
    """Split a stored hash into (scheme, iterations, salt, digest), or ``None``."""
    parts = encoded.strip().split("$")
    if parts[0] == PBKDF2_PREFIX and len(parts) == 4:
        _, iterations, salt_hex, digest_hex = parts
        if not iterations.isdigit():
            return None
        n = int(iterations)
        if not (_MIN_ITERATIONS <= n <= _MAX_ITERATIONS):
            return None
        if not (_is_hex(salt_hex) and _is_hex(digest_hex)):
            return None
        return PBKDF2_PREFIX, n, bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
    if parts[0] == SHA256_PREFIX and len(parts) == 2:
        digest_hex = parts[1]
        if len(digest_hex) != 64 or not _is_hex(digest_hex):
            return None
        return SHA256_PREFIX, 0, b"", bytes.fromhex(digest_hex)
    return None


def is_supported_hash(encoded: str) -> bool:
    """True when ``encoded`` is a well-formed hash in a supported format."""
    return _parse(encoded) is not None


def hash_password(
    password: str, *, iterations: int = DEFAULT_ITERATIONS, salt: bytes | None = None
) -> str:
    """Return a salted ``pbkdf2_sha256$...`` hash for ``password``."""
    if not password:
        raise ValueError("password must not be empty")
    if not (_MIN_ITERATIONS <= iterations <= _MAX_ITERATIONS):
        raise ValueError(f"iterations must be in [{_MIN_ITERATIONS}, {_MAX_ITERATIONS}]")
    salt = salt if salt is not None else secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"{PBKDF2_PREFIX}${iterations}${salt.hex()}${digest.hex()}"


def _derive(password: str, scheme: str, iterations: int, salt: bytes) -> bytes:
    if scheme == PBKDF2_PREFIX:
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return hashlib.sha256(password.encode()).digest()


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of ``password`` against a stored hash (fail closed)."""
    parsed = _parse(encoded or "")
    if parsed is None:
        return False
    scheme, iterations, salt, expected = parsed
    return hmac.compare_digest(_derive(password, scheme, iterations, salt), expected)


def burn_equivalent_work(encoded: str, password: str) -> None:
    """Spend the same hashing work as verifying against ``encoded``, discard result.

    Used for unknown doctor ids so response timing does not reveal which ids
    have a credential configured.
    """
    parsed = _parse(encoded or "")
    if parsed is None:
        return
    scheme, iterations, salt, expected = parsed
    hmac.compare_digest(_derive(password, scheme, iterations, salt), expected)


def parse_credentials(raw: str | None) -> dict[str, str]:
    """Parse ``"id:hash,id:hash"`` into ``{doctor_id: hash}``.

    Raises :class:`ValueError` on any malformed entry, unsupported hash, or
    duplicate doctor id — a half-parsed credential table must stop startup
    rather than silently drop a doctor (or worse, accept a plaintext value).
    Empty entries (stray commas/whitespace) are ignored.
    """
    table: dict[str, str] = {}
    if not raw:
        return table
    for chunk in raw.split(","):
        entry = chunk.strip()
        if not entry:
            continue
        doctor_id, sep, encoded = entry.partition(":")
        doctor_id, encoded = doctor_id.strip(), encoded.strip()
        if not sep or not doctor_id or not encoded:
            raise ValueError(
                "CARELINE_DOCTOR_CREDENTIALS entries must look like 'doctor_id:<hash>'"
            )
        if not is_supported_hash(encoded):
            raise ValueError(
                f"CARELINE_DOCTOR_CREDENTIALS entry for {doctor_id!r} is not a supported "
                "hash (generate one with `python -m careline.adapters.auth.hash_password`)"
            )
        if doctor_id in table:
            raise ValueError(f"CARELINE_DOCTOR_CREDENTIALS lists {doctor_id!r} twice")
        table[doctor_id] = encoded
    return table


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: print a hash (or a ``doctor_id:hash`` entry) for one password."""
    parser = argparse.ArgumentParser(
        prog="python -m careline.adapters.auth.hash_password",
        description="Generate a CARELINE_DOCTOR_CREDENTIALS password hash.",
    )
    parser.add_argument("--doctor-id", help="prefix the output with 'doctor_id:'")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument(
        "--stdin", action="store_true", help="read the password from one line of stdin"
    )
    args = parser.parse_args(argv)

    if args.stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("Doctor password: ")
        if getpass.getpass("Repeat password: ") != password:
            print("error: passwords do not match", file=sys.stderr)
            return 2
    if not password:
        print("error: password must not be empty", file=sys.stderr)
        return 2
    try:
        encoded = hash_password(password, iterations=args.iterations)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"{args.doctor_id}:{encoded}" if args.doctor_id else encoded)
    return 0


__all__ = [
    "DEFAULT_ITERATIONS",
    "burn_equivalent_work",
    "hash_password",
    "is_supported_hash",
    "main",
    "parse_credentials",
    "verify_password",
]


if __name__ == "__main__":
    raise SystemExit(main())
