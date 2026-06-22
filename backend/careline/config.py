"""Application settings — env-backed config + safety threshold bridge (NR-1).

Reads ``CARELINE_*`` environment variables via pydantic-settings, bridges
threshold overrides into Vinay's frozen :class:`~careline.domain.thresholds.Thresholds`,
and enforces a production guard so misconfigured knobs cannot silently weaken
the fail-closed safety spine.

Owner: Naresh (scope ``api``).
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from careline.domain.thresholds import DEFAULT_THRESHOLDS, Thresholds

# Dev-only sentinels — must be replaced in production (see assert_prod_safe).
# Each is >= 32 bytes so HMAC-SHA256 / HS256 meet RFC 7518 key-length guidance.
_DEFAULT_JWT_SECRET = "dev-jwt-secret-change-in-production!!"
_DEFAULT_INTERNAL_API_KEY = "dev-internal-api-key-change-in-production!"
_DEFAULT_PIN_HMAC_SECRET = "dev-pin-hmac-secret-change-in-prod!!"
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
    mongo_uri: str | None = Field(
        default=None,
        description="MongoDB connection URI; when unset the API uses in-memory stores.",
    )

    @property
    def is_production(self) -> bool:
        """True when running in the production environment."""
        return self.environment is Environment.PRODUCTION

    def to_thresholds(self) -> Thresholds:
        """Build the frozen gate-chain thresholds from current settings."""
        return Thresholds(
            confidence_floor=self.confidence_floor,
            risk_ceiling=self.risk_ceiling,
            max_clarify_turns=self.max_clarify_turns,
        )

    def assert_prod_safe(self) -> None:
        """Reject production configs that weaken the default safety thresholds.

        Uncertainty must always resolve toward ESCALATE.  Lowering
        ``confidence_floor`` or raising ``risk_ceiling`` beyond the baked-in
        defaults would let the agent answer when it should clarify or escalate.
        ``max_clarify_turns`` is unconstrained — lowering it is always safer.
        """
        if not self.is_production:
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


def get_settings() -> Settings:
    """Load settings from the current environment (fresh instance per call)."""
    return Settings()


__all__ = ["Environment", "Settings", "get_settings"]
