"""Contracts for root-cause selection policies.

Two things need guarding here. The first is that extracting ``_select_root``
into a named function did not change what it returns --- a silent shift in the
default would invalidate every result the library has already produced. The
second is that the alternative policy is genuinely an alternative: it has to
disagree with the default when the detector's confidences disagree with its
step ordering, and agree with it when they don't.
"""

from __future__ import annotations

from typing import Optional

import pytest

from agentdebug.diagnose.detect.selection import (
    earliest_finding,
    get_selector,
    most_confident_finding,
)
from agentdebug.schema import SEED_FAILURE_MODES, FailureFinding, new_id

MODE = next(iter(SEED_FAILURE_MODES.values()))


def finding(step: Optional[int], confidence: float) -> FailureFinding:
    return FailureFinding(
        finding_id=new_id('finding'),
        failure_mode=MODE,
        event_id=f'evt_{step}',
        agent_name='agent',
        step_index=step,
        confidence=confidence,
    )


def test_both_policies_return_none_on_an_empty_list():
    assert earliest_finding([]) is None
    assert most_confident_finding([]) is None


def test_earliest_takes_the_lowest_step():
    findings = [finding(9, 0.9), finding(2, 0.1), finding(5, 0.5)]

    assert earliest_finding(findings).step_index == 2


def test_earliest_breaks_ties_on_confidence():
    findings = [finding(3, 0.2), finding(3, 0.8)]

    assert earliest_finding(findings).confidence == pytest.approx(0.8)


def test_earliest_sorts_missing_steps_last():
    findings = [finding(None, 0.99), finding(7, 0.1)]

    assert earliest_finding(findings).step_index == 7


def test_confident_takes_the_highest_confidence_even_when_late():
    findings = [finding(2, 0.1), finding(64, 0.95), finding(5, 0.5)]

    assert most_confident_finding(findings).step_index == 64


def test_confident_breaks_ties_on_the_earliest_step():
    """A flat confidence distribution must not become an arbitrary pick.

    When the detector cannot rank its own findings, this degrades to the
    historical policy rather than inventing a preference.
    """
    findings = [finding(9, 0.8), finding(3, 0.8), finding(6, 0.8)]

    assert most_confident_finding(findings).step_index == 3


def test_confident_degrades_to_earliest_when_confidence_is_uniform():
    findings = [finding(step, 0.5) for step in (11, 4, 7, 2)]

    assert most_confident_finding(findings) is earliest_finding(findings)


def test_the_policies_disagree_when_position_and_confidence_disagree():
    findings = [finding(1, 0.2), finding(40, 0.9)]

    assert earliest_finding(findings).step_index == 1
    assert most_confident_finding(findings).step_index == 40


def test_lookup_by_name():
    assert get_selector('earliest') is earliest_finding
    assert get_selector('confident') is most_confident_finding
    with pytest.raises(ValueError):
        get_selector('nonsense')
