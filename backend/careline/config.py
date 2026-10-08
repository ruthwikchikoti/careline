"""Application settings — env-backed config + safety threshold bridge (NR-1).

Reads ``CARELINE_*`` environment variables via pydantic-settings, bridges
threshold overrides into Priyanshu's frozen :class:`~careline.domain.thresholds.Thresholds`,
and enforces a production guard so misconfigured knobs cannot silently weaken
the fail-closed safety spine.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

import os

from enum import Enum

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from careline.adapters.auth.hash_password import parse_credentials
from careline.domain.thresholds import DEFAULT_THRESHOLDS, Thresholds

# Dev-only sentinels — must be replaced in production (see assert_prod_safe).
# Each is >= 32 bytes so HMAC-SHA256 / HS256 meet RFC 7518 key-length guidance.
_DEFAULT_JWT_SECRET = "dev-jwt-secret-change-in-production!!"
_DEFAULT_INTERNAL_API_KEY = "dev-internal-api-key-change-in-production!"
_DEFAULT_PIN_HMAC_SECRET = "dev-pin-hmac-secret-change-in-prod!!"
# Dev-only shared doctor login credential (REVIEW-2). It is the explicit
# dev/local-demo fallback only: it opens *every* doctor id, so any hardened deploy
# (production / public demo) refuses to start while it is set at all and requires
# per-doctor hashes in CARELINE_DOCTOR_CREDENTIALS instead (SECURITY-2).
_DEFAULT_DOCTOR_PASSWORD = "careline-dev-doctor-password"
_MIN_SECRET_BYTES = 32


class Environment(str, Enum):
    """Deployment environment — drives production-only safety guards."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """CareLine runtime configuration loaded from environment variables.

    Threshold mirror fields default to :data:`~careline.domain.thresholds.DEFAULT_THRESHOLDS`
    so the offline suite passes without any ``.env`` file.  Use
    :meth:`to_thresholds` to hand a frozen :class:`~careline.domain.thresholds.Thresholds`
    to the gate chain.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CARELINE_",
        extra="ignore",
    )

    environment: Environment = Environment.DEVELOPMENT
    public_demo: bool = Field(
        default=False,
        description=(
            "CARELINE_PUBLIC_DEMO: a publicly reachable demo deploy. Keeps the "
            "/demo/* console mounted (non-production) but applies the production "
            "secret guard at startup (REVIEW-6)."
        ),
    )

    confidence_floor: float = Field(
        default=DEFAULT_THRESHOLDS.confidence_floor,
        ge=0.0,
        le=1.0,
    )
    risk_ceiling: float = Field(
        default=DEFAULT_THRESHOLDS.risk_ceiling,
        ge=0.0,
        le=1.0,
    )
    max_clarify_turns: int = Field(
        default=DEFAULT_THRESHOLDS.max_clarify_turns,
        ge=0,
    )
    jwt_secret: str = Field(default=_DEFAULT_JWT_SECRET, min_length=_MIN_SECRET_BYTES)
    jwt_ttl_seconds: int = Field(default=3600, ge=60)
    internal_api_key: str = Field(
        default=_DEFAULT_INTERNAL_API_KEY, min_length=_MIN_SECRET_BYTES
    )
    pin_hmac_secret: str = Field(
        default=_DEFAULT_PIN_HMAC_SECRET, min_length=_MIN_SECRET_BYTES
    )
    doctor_password: str = Field(
        default=_DEFAULT_DOCTOR_PASSWORD,
        min_length=1,
        description=(
            "Shared dev/local-demo doctor password (CARELINE_DOCTOR_PASSWORD) — "
            "a fallback used only when CARELINE_DOCTOR_CREDENTIALS is unset and the "
            "deploy is not hardened. It opens any doctor id, so production / public "
            "demo refuse it."
        ),
    )
    doctor_credentials: str | None = Field(
        default=None,
        description=(
            "Per-doctor login hashes (CARELINE_DOCTOR_CREDENTIALS): comma-separated "
            "'doctor_id:<hash>' entries; generate a hash with "
            "`python -m careline.adapters.auth.hash_password`. When set, only these "
            "ids can sign in, each with its own password; required in production / "
            "public demo."
        ),
    )
    doctor_ids: str | None = Field(
        default=None,
        description=(
            "Optional comma-separated allowlist of doctor ids that may sign in "
            "(CARELINE_DOCTOR_IDS). Empty = any id with the credential."
        ),
    )
    mongo_uri: str | None = Field(
        default=None,
        description="MongoDB connection URI; when unset the API uses in-memory stores.",
    )
    allowed_origins: str | None = Field(
        default=None,
        description=(
            "Comma-separated CORS origins for the public deploy "
            "(e.g. 'https://careline.onrender.com'). Empty = localhost dev default."
        ),
    )
    rate_limit_per_minute: int = Field(
        default=0,
        ge=0,
        description=(
            "Per-IP POST limit per minute on spend-bearing endpoints "
            "(demo ask / internal run-question / patient ask). 0 = off (dev "
            "default; the public deploy sets it)."
        ),
    )
    login_rate_limit_per_minute: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Per-IP POST limit per minute on the login routes (/auth/token, "
            "/patient/login) — its OWN window, separate from the spend window, so "
            "demo asks never lock a user out of signing in and logins never eat "
            "the spend budget. Unset = same value as rate_limit_per_minute; 0 = off."
        ),
    )
    daily_request_cap: int = Field(
        default=0,
        ge=0,
        description=(
            "Hard process-wide cap on spend-bearing requests per UTC day — the "
            ">$20 budget guard. 0 = off (dev default; the public deploy sets it)."
        ),
    )
    trusted_proxy_hops: int = Field(
        default=0,
        ge=0,
        le=5,
        description=(
            "Number of trusted reverse proxies that APPEND to X-Forwarded-For "
            "(1 on Render). Per-IP guards key on the N-th entry from the right; "
            "0 = ignore XFF and use the socket peer (dev/tests)."
        ),
    )
    login_max_failures: int = Field(
        default=5,
        ge=1,
        description="Failed logins per account before lockout (patient PIN / doctor).",
    )
    login_max_failures_per_ip: int = Field(
        default=20,
        ge=1,
        description="Failed logins per client IP (any account) before lockout.",
    )
    login_lockout_seconds: int = Field(
        default=900,
        ge=1,
        description="Lockout window after too many failed logins (seconds).",
    )

    @field_validator("doctor_credentials")
    @classmethod
    def _validate_doctor_credentials(cls, value: str | None) -> str | None:
        # Fail closed at load: a malformed table must stop startup, never be
        # half-applied (parse_credentials raises ValueError on any bad entry).
        parse_credentials(value)
        return value

    @property
    def is_production(self) -> bool:
        """True when running in the production environment."""
        return self.environment is Environment.PRODUCTION

    @property
    def requires_hardened_config(self) -> bool:
        """True for production and for a public demo — any internet-facing deploy."""
        return self.is_production or self.public_demo

    @property
    def doctor_id_allowlist(self) -> frozenset[str]:
        """Parsed ``doctor_ids`` allowlist (empty = no allowlist configured)."""
        if not self.doctor_ids:
            return frozenset()
        return frozenset(d.strip() for d in self.doctor_ids.split(",") if d.strip())

    @property
    def uses_default_doctor_password(self) -> bool:
        """True while the published dev doctor credential is still configured."""
        return self.doctor_password == _DEFAULT_DOCTOR_PASSWORD

    @property
    def doctor_credential_map(self) -> dict[str, str]:
        """Parsed ``doctor_credentials`` — ``{doctor_id: hash}`` (empty = unset)."""
        return parse_credentials(self.doctor_credentials)

    def to_thresholds(self) -> Thresholds:
        """Build the frozen gate-chain thresholds from current settings."""
        return Thresholds(
            confidence_floor=self.confidence_floor,
            risk_ceiling=self.risk_ceiling,
            max_clarify_turns=self.max_clarify_turns,
        )

    def assert_prod_safe(self) -> None:
        """Reject internet-facing configs that are unsafe to serve.

        Runs for production *and* public-demo mode (:attr:`requires_hardened_config`).
        Uncertainty must always resolve toward ESCALATE.  Lowering
        ``confidence_floor`` or raising ``risk_ceiling`` beyond the baked-in
        defaults would let the agent answer when it should clarify or escalate.
        ``max_clarify_turns`` is unconstrained — lowering it is always safer.
        The published dev-default secrets are refused, since anyone reading the
        repo could forge tokens with them. Doctor sign-in must use per-doctor
        hashes (``doctor_credentials``); the shared ``doctor_password`` — one
        secret that opens every doctor id — is refused outright (SECURITY-2).
        """
        if not self.requires_hardened_config:
            return

        if self.confidence_floor < DEFAULT_THRESHOLDS.confidence_floor:
            raise ValueError(
                "confidence_floor cannot be below the safe default "
                f"({DEFAULT_THRESHOLDS.confidence_floor}) in production"
            )

        if self.risk_ceiling > DEFAULT_THRESHOLDS.risk_ceiling:
            raise ValueError(
                "risk_ceiling cannot be above the safe default "
                f"({DEFAULT_THRESHOLDS.risk_ceiling}) in production"
            )

        # Langfuse traces carry a salted patient hash; the public default salt
        # would make those hashes reversible by anyone who reads the repo.
        from careline.adapters.observability.langfuse_tracer import langfuse_credentials

        if langfuse_credentials() is not None and os.environ.get(
            "CARELINE_TRACE_SALT", "careline-trace"
        ) in ("", "careline-trace"):
            raise ValueError(
                "CARELINE_TRACE_SALT must be set to a deployment secret when Langfuse "
                "tracing is enabled in production/public-demo mode"
            )

        if self.jwt_secret == _DEFAULT_JWT_SECRET:
            raise ValueError("jwt_secret must be changed from the dev default in production")

        if len(self.jwt_secret.encode()) < _MIN_SECRET_BYTES:
            raise ValueError(
                f"jwt_secret must be at least {_MIN_SECRET_BYTES} bytes in production"
            )

        if self.internal_api_key == _DEFAULT_INTERNAL_API_KEY:
            raise ValueError(
                "internal_api_key must be changed from the dev default in production"
            )

        if len(self.internal_api_key.encode()) < _MIN_SECRET_BYTES:
            raise ValueError(
                f"internal_api_key must be at least {_MIN_SECRET_BYTES} bytes in production"
            )

        if self.pin_hmac_secret == _DEFAULT_PIN_HMAC_SECRET:
            raise ValueError(
                "pin_hmac_secret must be changed from the dev default in production"
            )

        if len(self.pin_hmac_secret.encode()) < _MIN_SECRET_BYTES:
            raise ValueError(
                f"pin_hmac_secret must be at least {_MIN_SECRET_BYTES} bytes in production"
            )

        if not self.doctor_credential_map:
            raise ValueError(
                "doctor_credentials must be set (CARELINE_DOCTOR_CREDENTIALS, "
                "per-doctor 'doctor_id:<hash>' entries) in production / public demo — "
                "the shared CARELINE_DOCTOR_PASSWORD is a dev-only fallback"
            )

        if not self.uses_default_doctor_password:
            raise ValueError(
                "doctor_password (the shared CARELINE_DOCTOR_PASSWORD) is a dev/demo-"
                "only fallback that opens every doctor id — unset it in production / "
                "public demo and use CARELINE_DOCTOR_CREDENTIALS"
            )


def get_settings() -> Settings:
    """Load settings from the current environment (fresh instance per call)."""
    return Settings()


__all__ = ["Environment", "Settings", "get_settings"]
