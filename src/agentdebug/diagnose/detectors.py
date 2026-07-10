"""Backward-compatible detector wrappers.

Cross-event deterministic checks now live in ``agentdebug.diagnose.rules`` as
trajectory rules, so ``HeuristicAnalyzer.analyze()`` can run single-event and
cross-event logic through the same rule-pack mechanism. This module keeps the
old detector API working for callers that import ``RepeatedToolCallDetector``
or ``run_detectors`` directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional, Protocol, cast

from agentdebug.core.models import (
    AgentTrajectory,
    EventType,
    FailureFinding,
    FailureMode,
    new_id,
)
from agentdebug.diagnose.rules.core import (
    RepeatedStateRule,
    RepeatedToolCallRule,
    StepCountLimitRule,
)
from agentdebug.core.taxonomy import SEED_FAILURE_MODES

LOG = logging.getLogger('agentdebug.detectors')


@dataclass
class DetectorConfig:
    """Tunables for the rule + anomaly detectors."""

    repeated_tool_call_threshold: int = 3
    repeated_state_window: int = 4
    repeated_state_threshold: int = 3
    step_count_limit: int = 50


class Detector(Protocol):
    id: str

    def detect(self, trajectory: AgentTrajectory) -> List[FailureFinding]:
        ...


# ---------------------------------------------------------------------------
# RepeatedToolCallDetector
# ---------------------------------------------------------------------------

class RepeatedToolCallDetector:
    """Compatibility wrapper for ``core.trajectory.repeated_tool_call``."""

    id = 'repeated_tool_call'

    def __init__(self, *, threshold: int = 3) -> None:
        self.threshold = threshold
        self._rule: RepeatedToolCallRule = RepeatedToolCallRule(threshold=threshold)

    def detect(self, trajectory: AgentTrajectory) -> List[FailureFinding]:
        return cast(List[FailureFinding], self._rule.detect(trajectory))


# ---------------------------------------------------------------------------
# RepeatedStateDetector
# ---------------------------------------------------------------------------

class RepeatedStateDetector:
    """Compatibility wrapper for ``core.trajectory.repeated_state``."""

    id = 'repeated_state'

    def __init__(self, *, window: int = 4, threshold: int = 3) -> None:
        self.window = window
        self.threshold = threshold
        self._rule: RepeatedStateRule = RepeatedStateRule(
            window=window,
            threshold=threshold,
        )

    def detect(self, trajectory: AgentTrajectory) -> List[FailureFinding]:
        return cast(List[FailureFinding], self._rule.detect(trajectory))


# ---------------------------------------------------------------------------
# StepCountLimitDetector
# ---------------------------------------------------------------------------

class StepCountLimitDetector:
    """Compatibility wrapper for ``core.trajectory.step_count_limit``."""

    id = 'step_count_limit'

    def __init__(self, *, max_steps: int = 50) -> None:
        self.max_steps = max_steps
        self._rule: StepCountLimitRule = StepCountLimitRule(max_steps=max_steps)

    def detect(self, trajectory: AgentTrajectory) -> List[FailureFinding]:
        return cast(List[FailureFinding], self._rule.detect(trajectory))


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def default_detectors(config: Optional[DetectorConfig] = None) -> List[Detector]:
    cfg = config or DetectorConfig()
    return [
        RepeatedToolCallDetector(threshold=cfg.repeated_tool_call_threshold),
        RepeatedStateDetector(
            window=cfg.repeated_state_window,
            threshold=cfg.repeated_state_threshold,
        ),
        StepCountLimitDetector(max_steps=cfg.step_count_limit),
    ]


def run_detectors(
    trajectory: AgentTrajectory,
    detectors: Optional[List[Detector]] = None,
) -> List[FailureFinding]:
    """Run a list of detectors over a trajectory and return merged findings."""
    detectors = detectors or default_detectors()
    out: List[FailureFinding] = []
    for d in detectors:
        try:
            out.extend(d.detect(trajectory))
        except Exception as exc:  # pragma: no cover - defensive
            LOG.warning('detector %s raised: %s', d.id, exc)
    return out


def _suggestion(mode: FailureMode) -> Optional[str]:
    if mode.suggestion_templates:
        return str(mode.suggestion_templates[0])
    return None


class TopicDriftDetector:
    """Embedding-based anomaly detector for goal drift.

    Embed the trajectory's goal once; embed each user-facing event payload
    (LLM_RESPONSE / PLAN / OBSERVATION outputs); flag any step whose cosine
    similarity with the goal drops below ``threshold``.

    Maps to ``FM-2.3 task_derailment`` (MAST) / ``planning.inefficient_plan``.
    Closes the anomaly family from doc 06 alongside the existing
    RepeatedToolCallDetector / RepeatedStateDetector.

    Skipped silently if the embedding client raises or returns no vectors —
    the rest of the detector pipeline is unaffected.
    """

    id = 'topic_drift'

    def __init__(
        self,
        embedding_client: Any,
        *,
        threshold: float = 0.35,
        max_events: int = 60,
    ) -> None:
        # embedding_client is duck-typed to EmbeddingClient to avoid an
        # import cycle (detectors.py is imported from agentdebug/__init__.py).
        self.embedding_client = embedding_client
        self.threshold = threshold
        self.max_events = max_events

    def detect(self, trajectory: AgentTrajectory) -> List[FailureFinding]:
        if not trajectory.goal:
            return []
        contextful = [
            e for e in trajectory.events
            if e.event_type in {
                EventType.LLM_RESPONSE, EventType.PLAN, EventType.OBSERVATION,
                EventType.LLM_RESPONSE.value, EventType.PLAN.value,
                EventType.OBSERVATION.value,
            }
            and e.output is not None and str(e.output).strip()
        ]
        if not contextful:
            return []
        contextful = contextful[-self.max_events:]
        texts = [trajectory.goal] + [str(e.output)[:1000] for e in contextful]
        try:
            vectors = self.embedding_client.embed(texts)
        except Exception as exc:  # pragma: no cover - defensive
            LOG.warning('topic_drift detector embed() failed: %s', exc)
            return []
        if not vectors or len(vectors) != len(texts):
            return []
        goal_vec = vectors[0]
        findings: List[FailureFinding] = []
        mode = SEED_FAILURE_MODES['planning.inefficient_plan']
        for evt, evt_vec in zip(contextful, vectors[1:]):
            sim = _cosine(goal_vec, evt_vec)
            if sim >= self.threshold:
                continue
            findings.append(FailureFinding(
                finding_id=new_id('finding'),
                failure_mode=mode,
                event_id=evt.event_id,
                agent_name=evt.agent_name,
                step_index=evt.step_index,
                confidence=None,
                evidence=[
                    f'goal/output cosine={sim:.3f} < threshold={self.threshold:.2f}',
                ],
                suggestion=_suggestion(mode),
                metadata={
                    'source': self.id,
                    'cosine_to_goal': round(sim, 4),
                    'threshold': self.threshold,
                },
            ))
        return findings


def _cosine(a: List[float], b: List[float]) -> float:
    import math
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


__all__ = [
    'Detector',
    'DetectorConfig',
    'RepeatedStateDetector',
    'RepeatedToolCallDetector',
    'StepCountLimitDetector',
    'TopicDriftDetector',
    'default_detectors',
    'run_detectors',
]
