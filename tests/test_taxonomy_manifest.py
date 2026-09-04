from __future__ import annotations

import json

import pytest

from agentdebug.schema.taxonomy import SEED_FAILURE_MODES
from agentdebug.taxonomy_manifest import (
    _model_dict,
    ABSTAIN_REASONS,
    ClassificationRequest,
    cohens_kappa,
    compile_classification_prompt,
    parse_classification_response,
    taxonomy_manifest,
)


def test_manifest_is_deterministic_and_covers_every_family() -> None:
    a, b = taxonomy_manifest(), taxonomy_manifest()
    assert a.fingerprint == b.fingerprint and len(a.fingerprint) == 64
    assert a.mode_ids() == sorted(a.mode_ids(), key=lambda m: (a.family_of(m), m))
    assert set(a.families) == {mode.family for mode in SEED_FAILURE_MODES.values()}
    assert len(a.modes) == len(SEED_FAILURE_MODES)
    for family in ('verification', 'multiagent', 'multimodal'):
        assert family in a.families, 'no family may be dropped from the reportable set'
    json.dumps(a.to_dict())
    assert a.source_revision['package'] == 'agentdebugx'


def test_fingerprint_changes_when_a_definition_changes() -> None:
    base = taxonomy_manifest()
    modes = dict(SEED_FAILURE_MODES)
    key = next(iter(modes))
    edited = _model_dict(modes[key])
    edited['description'] += ' (edited)'
    modes[key] = type(modes[key])(**edited)
    assert taxonomy_manifest(modes).fingerprint != base.fingerprint
    # a pure reordering of the input does not
    reordered = dict(reversed(list(SEED_FAILURE_MODES.items())))
    assert taxonomy_manifest(reordered).fingerprint == base.fingerprint


def test_duplicate_or_mismatched_keys_are_refused() -> None:
    modes = dict(SEED_FAILURE_MODES)
    key = next(iter(modes))
    modes['some.other.key'] = modes[key]
    with pytest.raises(ValueError, match='duplicate mode id'):
        taxonomy_manifest(modes)
    mismatched = dict(SEED_FAILURE_MODES)
    mismatched['renamed.key'] = mismatched.pop(key)
    with pytest.raises(ValueError, match='disagrees'):
        taxonomy_manifest(mismatched)


def test_prompt_pins_the_fingerprint_and_carries_the_window() -> None:
    manifest = taxonomy_manifest()
    request = ClassificationRequest(trace_uid='tr-1', task_statement='find the cd',
                                    outcome_text='failed', window='[step 0] look\n[step 1] done',
                                    window_complete=False, candidate_steps=[1])
    system, user = compile_classification_prompt(manifest, request)
    assert manifest.fingerprint in system
    for mode in manifest.modes:
        assert mode.mode_id in system
    assert 'earlier steps omitted' in user and 'CANDIDATE STEPS' in user and '[step 1] done' in user
    assert (system, user) == compile_classification_prompt(manifest, request), 'pure function'


@pytest.mark.parametrize('reply', [
    '{"mode_id": "memory.retrieval_failure", "decisive_step": 1, "evidence_quote": "look", '
    '"rationale": "it forgot", "confidence": 0.8, "abstain": false}',
    'Sure! Here is my answer:\n```json\n{"mode_id": "memory.retrieval_failure", "decisive_step": "1", '
    '"confidence": "0.8"}\n```\nHope this helps.',
])
def test_valid_replies_resolve_to_a_mode_and_family(reply: str) -> None:
    result = parse_classification_response(reply, taxonomy_manifest())
    assert not result.abstain
    assert result.mode_id == 'memory.retrieval_failure' and result.family == 'memory'
    assert result.decisive_step == 1 and result.confidence == pytest.approx(0.8)


def test_unknown_mode_malformed_and_unparsed_replies_abstain() -> None:
    manifest = taxonomy_manifest()
    unknown = parse_classification_response('{"mode_id": "planning.zzz", "confidence": 0.9}', manifest)
    assert unknown.abstain and unknown.abstain_reason == 'unknown_mode' and unknown.raw['mode_id'] == 'planning.zzz'
    malformed = parse_classification_response('no json at all', manifest)
    assert malformed.abstain and malformed.abstain_reason == 'malformed_response'
    unparsed = parse_classification_response('{"confidence": 0.5}', manifest)
    assert unparsed.abstain and unparsed.abstain_reason == 'unparsed'
    explicit = parse_classification_response(
        '{"abstain": true, "abstain_reason": "infrastructure_fault", "rationale": "tool died"}', manifest)
    assert explicit.abstain and explicit.abstain_reason == 'infrastructure_fault' and explicit.mode_id is None
    bad_reason = parse_classification_response('{"abstain": true, "abstain_reason": "because"}', manifest)
    assert bad_reason.abstain_reason == 'unparsed'
    assert all(reason in ABSTAIN_REASONS for reason in ('unknown_mode', 'malformed_response', 'unparsed'))


def test_confidence_is_clamped_and_duplicate_json_keys_take_the_last() -> None:
    manifest = taxonomy_manifest()
    result = parse_classification_response(
        '{"mode_id": "memory.hallucination", "confidence": 7, "mode_id": "memory.retrieval_failure"}', manifest)
    assert result.mode_id == 'memory.retrieval_failure' and result.confidence == 1.0


def test_cohens_kappa_matches_a_hand_computed_example() -> None:
    a = ['x', 'x', 'y', 'y', 'x', None]
    b = ['x', 'y', 'y', 'y', 'x', 'x']
    # over the 5 paired items: observed 4/5; p(x): a 3/5, b 2/5; p(y): a 2/5, b 3/5
    # expected = 0.24 + 0.24 = 0.48; kappa = (0.8 - 0.48) / 0.52
    assert cohens_kappa(a, b) == pytest.approx((0.8 - 0.48) / 0.52)
    assert cohens_kappa(['x', 'x'], ['x', 'x']) is None, 'undefined when expected agreement is 1'
    assert cohens_kappa([None], ['x']) is None
    with pytest.raises(ValueError):
        cohens_kappa(['x'], ['x', 'y'])
