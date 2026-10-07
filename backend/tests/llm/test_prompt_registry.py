"""Registry integrity + policy sync — the versioning contract.

The release story only holds if the manifest is honest: every artifact file
must hash to its recorded value (tamper detection), the red-flag policy
artifact must match the pure domain constant exactly (the domain stays free
of file I/O; this test is what makes the artifact authoritative), and every
registered artifact must load with a well-formed stamp. If any of these fail,
the eval gate is measuring an unversioned system and must not pass.
"""

from __future__ import annotations

import re

import pytest
import yaml

from careline.adapters.llm import prompts as prompt_constants
from careline.adapters.llm.prompt_registry import (
    Artifact,
    RegistryError,
    active_versions,
    load_policy,
    load_prompt,
    reload,
)
from careline.domain.rails.red_flag import RED_FLAG_PATTERNS

_STAMP_RE = re.compile(r"^[a-z_]+@v\d+\+[0-9a-f]{12}$")


def test_all_registered_artifacts_load_and_stamp():
    versions = active_versions()
    assert set(versions) == {"reasoner", "verifier", "extractor", "red_flags"}
    for name, stamp in versions.items():
        assert _STAMP_RE.match(stamp), f"malformed stamp for {name}: {stamp}"


def test_prompts_match_artifacts_byte_for_byte():
    assert prompt_constants.REASONER_SYSTEM_PROMPT == load_prompt("reasoner").text
    assert prompt_constants.VERIFIER_SYSTEM_PROMPT == load_prompt("verifier").text
    assert prompt_constants.EXTRACTOR_SYSTEM_PROMPT == load_prompt("extractor").text


def test_red_flag_policy_artifact_matches_domain_constant():
    from careline.domain.rails.emergency_phrases import (
        PHRASE_LIBRARY,
        SEMANTIC_THRESHOLD,
    )

    policy = yaml.safe_load(load_policy("red_flags").text)
    assert policy["version"] == "v2"
    assert tuple(policy["patterns"]) == tuple(RED_FLAG_PATTERNS), (
        "policies/red-flags YAML and domain RED_FLAG_PATTERNS have drifted — "
        "update both together and bump the policy version in the manifest"
    )
    assert policy["patterns"], "empty red-flag policy is a safety incident"
    semantic = policy["semantic"]
    assert semantic["threshold"] == SEMANTIC_THRESHOLD
    assert {k: list(v) for k, v in PHRASE_LIBRARY.items()} == semantic["phrases"], (
        "policies/red-flags YAML and domain PHRASE_LIBRARY have drifted — "
        "update both together and bump the policy version in the manifest"
    )


def test_policy_artifact_hash_matches_manifest():
    # load_policy raises on a manifest/file hash mismatch; loading is the check.
    art = load_policy("red_flags")
    assert isinstance(art, Artifact)
    assert art.kind == "policies"  # the manifest section name


def test_unknown_prompt_fails_closed():
    with pytest.raises(RegistryError):
        load_prompt("nonexistent")


def test_reload_clears_caches():
    reload()
    assert active_versions()["red_flags"].startswith("red_flags@")
