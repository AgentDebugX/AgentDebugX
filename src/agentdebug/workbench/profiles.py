"""One observable registry for coherent analysis profiles."""

from __future__ import annotations

from typing import Optional

from .models import ConfigurationSource, ResolvedPipeline

PROFILES = {
    'quick': {'diagnoser': 'heuristic', 'attributor': 'none', 'recovery': 'none'},
    'standard': {'diagnoser': 'heuristic', 'attributor': 'heuristic', 'recovery': 'reflexion'},
    'deep': {'diagnoser': 'deep', 'attributor': 'none', 'recovery': 'deepdebug'},
    'gui': {'diagnoser': 'gui-rca', 'attributor': 'none', 'recovery': 'reflexion'},
}
LLM_DIAGNOSERS = {'judge', 'deep', 'gui-rca'}
LLM_ATTRIBUTORS = {'all_at_once', 'step_by_step', 'binary_search', 'counterfactual'}
LLM_RECOVERIES = {'self_refine'}
DIAGNOSERS = {'heuristic', 'judge', 'deep', 'gui-rca'}
ATTRIBUTORS = {'none', 'heuristic', *LLM_ATTRIBUTORS}
RECOVERIES = {
    'none', 'deepdebug', 'reflexion', 'critic', 'self_refine',
    'auto_manual', 'saga_rollback',
}


def resolve_pipeline(
    profile: str,
    *,
    format_override: Optional[str] = None,
    diagnoser_override: Optional[str] = None,
    attributor_override: Optional[str] = None,
    recovery_override: Optional[str] = None,
) -> ResolvedPipeline:
    if profile not in PROFILES:
        raise ValueError(f'unknown profile {profile!r}; expected: {", ".join(PROFILES)}')
    preset = PROFILES[profile]
    values = {
        'diagnoser': diagnoser_override or preset['diagnoser'],
        'attributor': attributor_override or preset['attributor'],
        'recovery': recovery_override or preset['recovery'],
    }
    for label, allowed in (
        ('diagnoser', DIAGNOSERS),
        ('attributor', ATTRIBUTORS),
        ('recovery', RECOVERIES),
    ):
        if values[label] not in allowed:
            raise ValueError(
                f'unknown {label} {values[label]!r}; expected: {", ".join(sorted(allowed))}'
            )
    # Overrides may reduce cost, but may not smuggle LLM work into a local profile.
    llm_required = (
        values['diagnoser'] in LLM_DIAGNOSERS
        or values['attributor'] in LLM_ATTRIBUTORS
        or values['recovery'] in LLM_RECOVERIES
    )
    if values['diagnoser'] == 'deep' and values['attributor'] != 'none':
        raise ValueError('deep diagnosis performs attribution internally; attributor must be none')
    if values['diagnoser'] == 'deep' and values['recovery'] not in {'none', 'deepdebug'}:
        raise ValueError('deep diagnosis is compatible only with none or deepdebug recovery')
    if values['diagnoser'] == 'gui-rca' and values['attributor'] != 'none':
        raise ValueError('gui-rca currently requires attributor none')
    return ResolvedPipeline(
        profile=profile,
        input_format=ConfigurationSource(value=format_override or 'auto', source='override' if format_override else 'default'),
        diagnoser=ConfigurationSource(value=values['diagnoser'], source='override' if diagnoser_override else 'profile'),
        attributor=ConfigurationSource(value=values['attributor'], source='override' if attributor_override else 'profile'),
        recovery=ConfigurationSource(value=values['recovery'], source='override' if recovery_override else 'profile'),
        llm_required=llm_required,
    )
