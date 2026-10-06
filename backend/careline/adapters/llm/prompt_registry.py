"""Versioned prompt & policy registry — the release artifact layer.

Prompts and safety policies are *releases*, not code constants. They live as
versioned files under ``backend/prompts/`` and ``backend/policies/`` with a
manifest that records each artifact's version and content hash. Every Brain
run, eval report, and trace stamps the active versions (``name@version+hash``)
so any score or incident can be traced to the exact prompt/policy that
produced it — and a tampered file (hash != manifest) fails loudly instead of
silently changing agent behaviour.

Fail closed: a missing artifact, an unparseable manifest, or a hash mismatch
raises immediately. A silently-drifting prompt is a safety incident, not a
convenience.

Owner: Srujan (scope ``llm``), consumed by adapters, eval gate, and CI.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

# backend/ — three levels up from careline/adapters/llm/prompt_registry.py
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_MANIFEST_PATH = _BACKEND_ROOT / "prompts" / "manifest.yaml"

_PROMPT_NAMES = ("reasoner", "verifier", "extractor")


class RegistryError(RuntimeError):
    """Raised when the prompt/policy registry is missing, inconsistent, or tampered."""


@dataclass(frozen=True)
class Artifact:
    name: str
    kind: str  # "prompt" | "policy"
    version: str
    text: str
    sha256_12: str

    @property
    def stamp(self) -> str:
        """Traceable identity, e.g. ``reasoner@v1+a1b2c3d4e5f6``."""
        return f"{self.name}@{self.version}+{self.sha256_12}"


def _sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@lru_cache(maxsize=1)
def _manifest() -> dict:
    if not _MANIFEST_PATH.is_file():
        raise RegistryError(f"prompt manifest missing: {_MANIFEST_PATH}")
    try:
        return yaml.safe_load(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - corrupt manifest
        raise RegistryError(f"prompt manifest unparseable: {exc}") from exc


def _load(kind: str, name: str) -> Artifact:
    section = _manifest().get(kind, {})
    if name not in section:
        raise RegistryError(f"{name} not registered under '{kind}' in manifest")
    entry = section[name]
    path = _BACKEND_ROOT / entry["file"]
    if not path.is_file():
        raise RegistryError(f"registered artifact missing on disk: {path}")
    text = path.read_text(encoding="utf-8")
    actual = _sha12(text)
    recorded = entry.get("sha256_12")
    if recorded and recorded != actual:
        raise RegistryError(
            f"{name}: file hash {actual} != manifest hash {recorded} — the artifact "
            "changed without a manifest update; bump the version and re-record the hash"
        )
    return Artifact(
        name=name, kind=kind, version=entry["version"], text=text, sha256_12=actual
    )


def load_prompt(name: str) -> Artifact:
    """Load a versioned prompt artifact (``reasoner`` | ``verifier`` | ``extractor``)."""
    if name not in _PROMPT_NAMES:
        raise RegistryError(f"unknown prompt '{name}' (expected one of {_PROMPT_NAMES})")
    return _load("prompts", name)


def load_policy(name: str) -> Artifact:
    """Load a versioned policy artifact (e.g. ``red_flags``)."""
    return _load("policies", name)


def active_versions() -> dict[str, str]:
    """The active stamp of every registered artifact, e.g.
    ``{"reasoner": "reasoner@v1+a1b2...", ...}`` — attach to traces/eval reports."""
    out: dict[str, str] = {}
    for kind, loader, names in (
        ("prompts", load_prompt, _PROMPT_NAMES),
        ("policies", load_policy, tuple(_manifest().get("policies", {}))),
    ):
        for n in names:
            out[n] = loader(n).stamp
    return out


def reload() -> None:
    """Clear caches — used by tests that swap manifests and by the shadow runner."""
    _manifest.cache_clear()


__all__ = [
    "Artifact",
    "RegistryError",
    "load_prompt",
    "load_policy",
    "active_versions",
    "reload",
]
