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
    assert set(versions) == {"reasoner", "verifier", "extractor", "judge", "red_flags"}
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
    assert policy["version"] == "v8"
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
    # v3 context layer must mirror the domain too.
    from careline.domain.rails.acute_concern import (
        ACUTE_TERM_PATTERNS,
        SUPPRESSIBLE_CONCEPTS,
        SUPPRESSIBLE_REGEX_CONCEPTS,
    )

    ctx = policy["context"]
    assert sorted(SUPPRESSIBLE_CONCEPTS) == ctx["history_suppressible_concepts"]
    assert sorted(SUPPRESSIBLE_REGEX_CONCEPTS) == ctx["history_suppressible_regex_concepts"]
    assert dict(ACUTE_TERM_PATTERNS) == {
        k: v for k, v in ctx["acute_concern"]["term_patterns"].items()
    }
    # v4 clause-scoped context markers + typo normalisation must mirror too.
    from careline.domain.rails.acute_concern import (
        DENIAL_MARKERS,
        HISTORY_MARKERS,
        HYPOTHETICAL_MARKERS,
        PRESENT_MARKERS,
    )
    from careline.domain.rails.red_flag import TYPO_CANONICAL_TOKENS

    assert list(HISTORY_MARKERS) == ctx["history_markers"]
    assert list(HYPOTHETICAL_MARKERS) == ctx["hypothetical_markers"]
    assert list(PRESENT_MARKERS) == ctx["present_markers"]
    assert list(DENIAL_MARKERS) == ctx["denial_markers"]
    assert list(TYPO_CANONICAL_TOKENS) == policy["typo_normalisation"]["canonical_tokens"]
    # v4 (round 2): non-human-subject segments + the structural symptom-report
    # layer must mirror the domain too.
    from careline.domain.rails.acute_concern import NON_HUMAN_SUBJECT_MARKERS
    from careline.domain.rails.symptom_report import (
        GENERAL_KNOWLEDGE_PATTERNS,
        MILD_QUALIFIERS,
        SOFT_SYMPTOM_PATTERNS,
        SUBJECT_PATTERNS,
        SYMPTOM_PATTERNS,
        SYMPTOM_REPORT_RISK,
    )

    assert list(NON_HUMAN_SUBJECT_MARKERS) == ctx["non_human_subject_markers"]
    sr = ctx["symptom_report"]
    assert list(SUBJECT_PATTERNS) == sr["subject_patterns"]
    assert dict(SYMPTOM_PATTERNS) == sr["symptom_patterns"]
    assert dict(SOFT_SYMPTOM_PATTERNS) == sr["soft_symptom_patterns"]
    assert list(MILD_QUALIFIERS) == sr["mild_qualifiers"]
    assert list(GENERAL_KNOWLEDGE_PATTERNS) == sr["general_knowledge_patterns"]
    assert SYMPTOM_REPORT_RISK == sr["risk"]
    # v5 (blind battery 1): third-party/media framing spans and the
    # transient-resolved markers (mild labels only) must mirror the domain too.
    from careline.domain.rails.acute_concern import (
        MILD_RESOLVABLE_LABELS,
        RESOLVED_MARKERS,
        THIRD_PARTY_FRAMING_MARKERS,
    )

    assert list(THIRD_PARTY_FRAMING_MARKERS) == ctx["third_party_framing_markers"]
    resolved = ctx["transient_resolved"]
    assert list(RESOLVED_MARKERS) == resolved["markers"]
    assert sorted(MILD_RESOLVABLE_LABELS) == resolved["mild_labels"]
    # v6 (round-4 red team): anaphoric present, the LLM-path body-state
    # backstop, de-obfuscation / fuzzy pair repair and the emergency line.
    from careline.domain.gates.chain import EMERGENCY_LINE
    from careline.domain.rails.acute_concern import ANAPHORIC_PRESENT_MARKERS
    from careline.domain.rails.normalise import LEET_MAP, TYPO_BIGRAMS, TYPO_FUZZY_PAIRS
    from careline.domain.rails.red_flag import _PATTERN_CONCEPTS
    from careline.domain.rails.symptom_report import (
        BODY_STATE_CONDITIONAL,
        BODY_STATE_PRESENT_PATTERNS,
        BODY_STATE_VOCABULARY,
    )

    assert policy["pattern_concepts"] == dict(_PATTERN_CONCEPTS)
    assert list(ANAPHORIC_PRESENT_MARKERS) == ctx["anaphoric_present"]["markers"]
    backstop = ctx["body_state_backstop"]
    assert BODY_STATE_VOCABULARY == backstop["vocabulary"]
    assert list(BODY_STATE_PRESENT_PATTERNS) == backstop["present_patterns"]
    assert BODY_STATE_CONDITIONAL == backstop["conditional"]
    typo = policy["typo_normalisation"]
    assert [list(p) for p in TYPO_FUZZY_PAIRS] == typo["fuzzy_pairs"]
    assert dict(TYPO_BIGRAMS) == typo["bigrams"]
    assert dict(LEET_MAP) == typo["deobfuscation"]["leet_map"]
    assert EMERGENCY_LINE == policy["redirect_emergency_line"]
    # v7 (final red team): informal-spelling repair and the answer-text
    # grounding vocabulary must mirror the domain too.
    from careline.domain.gates import grounding
    from careline.domain.rails.normalise import APOSTROPHE_LESS, DROPPED_G_WORDS

    informal = typo["informal_repair"]
    assert list(DROPPED_G_WORDS) == informal["dropped_g_words"]
    assert dict(APOSTROPHE_LESS) == informal["apostrophe_less"]
    ag = ctx["answer_grounding"]
    assert dict(grounding.UNIT_CANONICAL) == ag["units"]
    assert dict(grounding.FREQUENCY_WORDS) == ag["frequency_words"]
    assert dict(grounding.NUMBER_WORDS) == ag["number_words"]
    assert sorted(grounding.EXEMPT_NUMBERS) == ag["exempt_numbers"]
    assert sorted(grounding.DRUG_LEXICON) == ag["drug_lexicon"]
    assert dict(grounding.DRUG_SYNONYMS) == ag["drug_synonyms"]
    assert list(grounding.DRUG_SUFFIXES) == ag["drug_suffixes"]
    # v8 (final evaluator red team): grounding hardening vocabulary.
    assert dict(grounding._SCALE_WORDS) == ag["scale_words"]
    assert {k: [u, str(f)] for k, (u, f) in grounding._UNIT_SCALE.items()} == ag["unit_value_scale"]
    assert dict(grounding.CHANGE_WORDS) == ag["change_words"]
    pol = ag["polarity"]
    assert sorted(grounding._STOP_CUES) == pol["stop_cues"]
    assert sorted(grounding._TAKE_CUES) == pol["take_cues"]
    assert sorted(grounding._NEGATIONS) == pol["negations"]
    assert sorted(grounding._LIMIT_WORDS) == pol["limit_words"]
    assert sorted(grounding._OTHER_PRODUCT) == pol["other_product_before"]
    assert sorted(grounding._PRODUCT_SUFFIX) == pol["other_product_after"]


def test_policy_artifact_hash_matches_manifest():
    # load_policy raises on a manifest/file hash mismatch; loading is the check.
    art = load_policy("red_flags")
    assert isinstance(art, Artifact)
    assert art.kind == "policies"  # the manifest section name


def test_unknown_prompt_fails_closed():
    with pytest.raises(RegistryError):
        load_prompt("nonexistent")


def test_manifest_without_hashes_fails_closed(tmp_path, monkeypatch):
    """A manifest entry missing sha256_12 must not silently disable tamper checks."""
    import shutil

    import careline.adapters.llm.prompt_registry as reg

    manifest = yaml.safe_load((reg._BACKEND_ROOT / "prompts" / "manifest.yaml").read_text())
    del manifest["prompts"]["reasoner"]["sha256_12"]
    fake_root = tmp_path
    (fake_root / "prompts" / "reasoner").mkdir(parents=True)
    shutil.copy(
        reg._BACKEND_ROOT / "prompts" / "reasoner" / "v1.md",
        fake_root / "prompts" / "reasoner" / "v1.md",
    )
    (fake_root / "prompts" / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    monkeypatch.setattr(reg, "_BACKEND_ROOT", fake_root)
    monkeypatch.setattr(
        reg, "_MANIFEST_PATH", fake_root / "prompts" / "manifest.yaml"
    )
    reg.reload()
    try:
        with pytest.raises(RegistryError, match="no sha256_12"):
            reg.load_prompt("reasoner")
    finally:
        monkeypatch.undo()
        reg.reload()


def test_reload_clears_caches():
    reload()
    assert active_versions()["red_flags"].startswith("red_flags@")
