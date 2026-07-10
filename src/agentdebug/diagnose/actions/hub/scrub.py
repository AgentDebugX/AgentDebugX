"""Regex-based PII/secret scrubbing.

Intentionally conservative — false positives are preferable to false negatives
when shipping a trace to an external Hub. A scrub run that catches your
``sk-...`` token but also clobbers a benign string starting with ``sk-`` is
the right tradeoff.

This is a v0.1 scrubber. For higher recall, install
`presidio-analyzer <https://microsoft.github.io/presidio/>`_ and write a
custom :class:`Scrubber` that delegates to it.

A scrub run **mutates the trajectory** (string fields and nested dict / list
strings). Callers can grab a copy of the trajectory first if they want to
keep the unscrubbed version locally.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Pattern, Tuple

from agentdebug.core.models import AgentEvent, AgentTrajectory

SCRUBBER_VERSION = '0.1.0'


def _re(pattern: str) -> Pattern[str]:
    return re.compile(pattern)


DEFAULT_REDACTIONS: List[Tuple[str, Pattern[str], str]] = [
    # API keys
    ('openai_key', _re(r'sk-[A-Za-z0-9_\-]{20,}'), '<redacted:openai_key>'),
    ('anthropic_key', _re(r'sk-ant-[A-Za-z0-9_\-]{20,}'), '<redacted:anthropic_key>'),
    ('aws_key', _re(r'AKIA[0-9A-Z]{16}'), '<redacted:aws_access_key>'),
    ('aws_secret', _re(r'(?<![A-Za-z0-9])[A-Za-z0-9/+]{40}(?![A-Za-z0-9])'),
     '<redacted:aws_secret_or_b64_40>'),
    ('google_api_key', _re(r'AIza[0-9A-Za-z_\-]{35}'), '<redacted:google_api_key>'),
    ('gh_token', _re(r'gh[pousr]_[A-Za-z0-9]{36,}'), '<redacted:github_token>'),
    ('pypi_token', _re(r'pypi-[A-Za-z0-9_\-]{20,}'), '<redacted:pypi_token>'),
    ('bearer_token', _re(r'(?i)Bearer\s+[A-Za-z0-9_\-\.=]{20,}'),
     'Bearer <redacted:bearer>'),
    # PII
    ('email', _re(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'),
     '<redacted:email>'),
    ('phone_e164', _re(r'(?<![\w])\+\d{8,15}(?![\w])'),
     '<redacted:phone>'),
    ('ssn_us', _re(r'\b\d{3}-\d{2}-\d{4}\b'), '<redacted:ssn>'),
    ('credit_card', _re(r'\b(?:\d[ -]?){13,19}\b'), '<redacted:cc>'),
    # URLs that embed credentials
    ('url_credentials',
     _re(r'(https?://)[^\s/@]+:[^\s/@]+@'),
     r'\1<redacted:url_credentials>@'),
]


@dataclass
class ScrubReport:
    """What the scrubber changed, for audit."""

    fields_visited: int = 0
    replacements: Dict[str, int] = field(default_factory=dict)

    def total(self) -> int:
        return sum(self.replacements.values())


class Scrubber:
    """Recursive in-place scrubber over JSON-shaped trajectory fields."""

    def __init__(
        self,
        redactions: List[Tuple[str, Pattern[str], str]] = DEFAULT_REDACTIONS,
        *,
        extra_redactions: List[Tuple[str, Pattern[str], str]] | None = None,
    ) -> None:
        self.redactions = redactions + (extra_redactions or [])

    def scrub_text(self, text: str, report: ScrubReport) -> str:
        out = text
        for name, pattern, replacement in self.redactions:
            new, n = pattern.subn(replacement, out)
            if n:
                report.replacements[name] = report.replacements.get(name, 0) + n
                out = new
        return out

    def scrub_value(self, value: Any, report: ScrubReport) -> Any:
        report.fields_visited += 1
        if value is None:
            return value
        if isinstance(value, str):
            return self.scrub_text(value, report)
        if isinstance(value, dict):
            return {k: self.scrub_value(v, report) for k, v in value.items()}
        if isinstance(value, list):
            return [self.scrub_value(v, report) for v in value]
        if isinstance(value, tuple):
            return tuple(self.scrub_value(v, report) for v in value)
        return value

    def scrub_event(self, event: AgentEvent, report: ScrubReport) -> AgentEvent:
        if event.input is not None:
            event.input = self.scrub_value(event.input, report)
        if event.output is not None:
            event.output = self.scrub_value(event.output, report)
        if event.error is not None and isinstance(event.error, str):
            event.error = self.scrub_text(event.error, report)
        event.metadata = {
            k: self.scrub_value(v, report) for k, v in event.metadata.items()
        }
        return event

    def scrub_trajectory(self, trajectory: AgentTrajectory) -> ScrubReport:
        report = ScrubReport()
        if trajectory.goal is not None:
            trajectory.goal = self.scrub_text(trajectory.goal, report)
        trajectory.metadata = {
            k: self.scrub_value(v, report) for k, v in trajectory.metadata.items()
        }
        for event in trajectory.events:
            self.scrub_event(event, report)
        return report


def scrub_trajectory(
    trajectory: AgentTrajectory,
    *,
    extra_redactions: List[Tuple[str, Pattern[str], str]] | None = None,
) -> ScrubReport:
    """Convenience wrapper: build a default Scrubber and run it in place."""
    return Scrubber(extra_redactions=extra_redactions).scrub_trajectory(trajectory)


__all__ = [
    'DEFAULT_REDACTIONS',
    'SCRUBBER_VERSION',
    'ScrubReport',
    'Scrubber',
    'scrub_trajectory',
]
