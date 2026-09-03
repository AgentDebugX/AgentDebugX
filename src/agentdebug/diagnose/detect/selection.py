"""Policies for picking one root cause out of many findings.

A detector usually returns several findings; a report names one root cause.
The policy that reduces the list to one has, until now, been a sort key inlined
in each analyzer --- ``LLMJudgeAnalyzer._select_root`` and
``TrajDebugAttributor._rank`` both take the earliest flagged step. That choice
is invisible from outside and impossible to vary, which matters more than it
looks: it is a positional prior, and it is applied regardless of whether the
detector had any positional evidence.

Making it a named function does three things. It becomes swappable, it becomes
testable in isolation, and --- most usefully --- an experiment can attribute a
change in accuracy to the *selector* rather than to the detection it sits on
top of.

A note on what is deliberately absent. It would be easy to add a selector tuned
to where ground-truth errors sit in a particular benchmark. That would measure
the label distribution, not the method, so every policy here uses only signals
the detector itself produced.
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

from agentdebug.schema import FailureFinding, confidence_or_default

RootSelector = Callable[[Sequence[FailureFinding]], Optional[FailureFinding]]


def earliest_finding(
    findings: Sequence[FailureFinding],
) -> Optional[FailureFinding]:
    """The earliest flagged step, breaking ties on higher confidence.

    The historical default, preserved exactly. The reasoning behind it is that
    an agent run is a chain, so the first mistake is the one that caused the
    rest --- which holds when the detector fires rarely, and stops holding when
    it fires on almost every step, because then "earliest flagged" converges on
    "earliest step" no matter what happened.
    """
    if not findings:
        return None
    return sorted(
        findings,
        key=lambda finding: (
            finding.step_index is None,
            finding.step_index if finding.step_index is not None else 10**9,
            -confidence_or_default(finding.confidence),
        ),
    )[0]


def most_confident_finding(
    findings: Sequence[FailureFinding],
) -> Optional[FailureFinding]:
    """The finding the detector was most sure of, breaking ties on earliest.

    Uses the one discriminating signal the detector actually emits about its
    own output. Where confidences are flat this degrades gracefully to
    :func:`earliest_finding`, which is the honest failure mode: if the detector
    cannot rank its findings, no selector can do it for free.
    """
    if not findings:
        return None
    return sorted(
        findings,
        key=lambda finding: (
            -confidence_or_default(finding.confidence),
            finding.step_index is None,
            finding.step_index if finding.step_index is not None else 10**9,
        ),
    )[0]


SELECTORS = {
    'earliest': earliest_finding,
    'confident': most_confident_finding,
}


def get_selector(name: str) -> RootSelector:
    """Look a policy up by name, for callers configured from a string."""
    try:
        return SELECTORS[name]
    except KeyError:
        raise ValueError(
            f'unknown root selector {name!r}; expected one of {sorted(SELECTORS)}'
        ) from None


__all__ = [
    'RootSelector',
    'SELECTORS',
    'earliest_finding',
    'get_selector',
    'most_confident_finding',
]
