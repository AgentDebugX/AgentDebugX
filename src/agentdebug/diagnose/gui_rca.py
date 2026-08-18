"""GUI Root-Cause Analysis analyzer.

Drives the ``run_rca`` ReAct backward-tracing pipeline from
:mod:`agentdebug.gui` through the AgentDebugX LLM channel
(:class:`CoreLLMChannel` -> ``core/llm.py``, satisfying RCA-03) and maps the
resulting ``RCAResult`` onto a standard :class:`DiagnosticReport` over the
AgentDebugX IR (RCA-01 / RCA-02).

Following D-01/D-02 we do NOT reimplement the ReAct logic — the channel adapter
is the only swapped component. ``run_rca`` / ``RCAResult`` / ``IngestionResult``
are reached behind a lazy import (mirroring ``ingest/adapters/osworld.py``) so
``import agentdebug`` never pulls in the RCA engine.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional, Tuple

from agentdebug.runtime.gui_taxonomy import gui_failure_mode_for_code
from agentdebug.schema import (
    AgentTrajectory,
    DiagnosticReport,
    FailureFinding,
    FailureMode,
    Modality,
)

_SOURCE = 'CUA / OSWorld GUI taxonomy v2'


def _load_gui_rca() -> Tuple[Any, Any, Any]:
    """Import the GUI RCA surface lazily from the package-owned GUI package."""
    from agentdebug.gui.ingester import IngestionResult
    from agentdebug.gui.rca import RCAResult, run_rca

    return run_rca, RCAResult, IngestionResult


class GuiRcaAnalyzer:
    """Analyzer that backward-traces a failed OSWorld trajectory to one root
    error step via vendored ``run_rca`` and emits a ``DiagnosticReport``."""

    def __init__(
        self,
        channel: Any,
        model: str,
        *,
        timeout: int = 600,
        verbose: bool = False,
    ) -> None:
        self.channel = channel
        self.model = model
        self.timeout = timeout
        self.verbose = verbose

    def analyze(self, trajectory: AgentTrajectory) -> DiagnosticReport:
        run_rca, _RCAResult, IngestionResult = _load_gui_rca()
        osworld_root = self._resolve_osworld_root(trajectory)
        ingestion_result = IngestionResult.from_directory(osworld_root)
        # Standard RCA path only (D-10 / Deferred): no lessons, no lesson_table.
        # The infeasible branch is automatic via IngestionResult.is_infeasible.
        result = run_rca(
            ingestion_result=ingestion_result,
            model=self.model,
            client=self.channel,
            osworld_root=osworld_root,
            few_shots=[],
            verbose=self.verbose,
            timeout=self.timeout,
        )
        return self._map_result(result, trajectory)

    def _resolve_osworld_root(self, trajectory: AgentTrajectory) -> Path:
        """Resolve the on-disk trajectory directory for CUA's screenshot tools.

        Prefers the ingest-recorded ``metadata['source_dir']`` (T-03-04:
        trajectory-owned, ``Path.resolve()``-d at ingest); falls back to the
        common parent of the screenshot ``Artifact`` URIs.
        """
        source_dir = (trajectory.metadata or {}).get('source_dir')
        if source_dir:
            return Path(source_dir)

        image_dirs: List[str] = []
        for event in trajectory.events:
            for artifact in event.artifacts:
                if artifact.modality == Modality.IMAGE:
                    image_dirs.append(str(Path(artifact.uri).resolve().parent))
        if image_dirs:
            return Path(os.path.commonpath(image_dirs))

        raise ValueError(
            'cannot resolve osworld_root: trajectory metadata lacks '
            "'source_dir' and has no screenshot artifacts."
        )

    def _map_result(
        self, result: Any, trajectory: AgentTrajectory
    ) -> DiagnosticReport:
        """Map an ``RCAResult`` (D-09) onto a ``DiagnosticReport`` with one
        primary taxonomy-tagged ``FailureFinding``.

        Kept independent of ``run_rca`` so it is unit-testable with a synthetic
        result object.
        """
        root_step = result.root_error_step

        # step_index -> (event_id, agent_name) over the IR (CUA step <-> event 1:1).
        root_event_id: Optional[str] = None
        root_agent: Optional[str] = None
        for event in trajectory.events:
            if event.step_index == root_step:
                root_event_id = event.event_id
                root_agent = event.agent_name
                break

        taxonomy_tag = result.taxonomy_tag
        failure_mode = gui_failure_mode_for_code(taxonomy_tag)
        unmapped_tag: Optional[str] = None
        if failure_mode is None:
            unmapped_tag = taxonomy_tag
            failure_mode = FailureMode(
                mode_id='gui.unknown',
                name='Unrecognized GUI failure tag',
                family='unknown',
                description=(
                    f'RCA returned taxonomy tag {taxonomy_tag!r}, which is not '
                    'in the registered GUI taxonomy.'
                ),
                signals=[str(taxonomy_tag)],
                source=_SOURCE,
            )

        finding_metadata: dict = {'taxonomy_tag': taxonomy_tag, 'source': 'gui_rca'}
        if unmapped_tag is not None:
            finding_metadata['unmapped_taxonomy_tag'] = unmapped_tag

        evidence = getattr(result, 'evidence', None)
        correction = getattr(result, 'correction', None)
        finding = FailureFinding(
            failure_mode=failure_mode,
            event_id=root_event_id,
            agent_name=root_agent,
            step_index=root_step,
            confidence=result.confidence,
            evidence=[evidence] if evidence else [],
            suggestion=correction,
            metadata=finding_metadata,
        )

        report = DiagnosticReport(
            trace_id=trajectory.trace_id,
            task_id=trajectory.task_id,
            findings=[finding],
            suggestions=[correction] if correction else [],
            metadata={
                'analyzer': 'gui_rca',
                'source': 'gui_rca',
                'model': self.model,
                'per_step_summaries': self._serialize_per_step(
                    getattr(result, 'per_step_summaries', None) or []
                ),
                'thinking_trace': list(getattr(result, 'thinking_trace', None) or []),
            },
        )
        report.root_cause_step_index = root_step
        report.root_cause_event_id = root_event_id
        report.root_cause_agent = root_agent
        report.summary = (
            f'Likely root cause ({taxonomy_tag}): {failure_mode.name} '
            f'at step {root_step}.'
        )
        return report

    @staticmethod
    def _serialize_per_step(items: Any) -> List[Any]:
        """Serialize CUA ``StepSummary`` objects (pydantic) for report metadata."""
        out: List[Any] = []
        for item in items:
            dumper = getattr(item, 'model_dump', None)
            if callable(dumper):
                out.append(dumper(mode='json'))
            elif isinstance(item, dict):
                out.append(item)
            else:
                out.append(str(item))
        return out


__all__ = ['GuiRcaAnalyzer']
