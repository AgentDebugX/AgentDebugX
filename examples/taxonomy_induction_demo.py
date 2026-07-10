"""Induce new error-taxonomy nodes from a corpus of failed runs.

The seed taxonomy names 19 well-known failure modes, but deployed agents fail in
long-tail ways. This demo shows the induction loop end to end:

1. Build a small corpus of diagnostic reports whose failures the seed taxonomy
   does NOT cleanly name (here we attach them as judge "novel-mode candidates";
   in production these come from the live LLM judge automatically).
2. ``collect_observations`` harvests those residual failures.
3. ``TaxonomyInducer`` clusters them and proposes new ``FailureMode`` nodes.

Run offline (deterministic, no LLM)::

    PYTHONPATH=src python examples/taxonomy_induction_demo.py

Add an LLM to synthesize cleaner mode names/descriptions::

    export AGENTDEBUG_LLM_BASE_URL='https://.../v1'
    export AGENTDEBUG_LLM_API_KEY='sk-...'
    export AGENTDEBUG_LLM_MODEL='gemini-3-flash'
    PYTHONPATH=src python examples/taxonomy_induction_demo.py
"""

from __future__ import annotations

import json
import os

from agentdebug import TaxonomyInducer, collect_observations
from agentdebug.llm import OpenAICompatClient
from agentdebug.models import DiagnosticReport

# A corpus of reports. Each carries judge "novel-mode candidates" -- failures
# the seed taxonomy could not label. Two recurring patterns are seeded
# (deadlock, budget overrun) plus one-off noise that must NOT become a mode.
_CORPUS = [
    ('multiagent.deadlock', 'multiagent',
     'two agents each wait for the other to act; neither proceeds',
     'planner waits on critic; critic waits on planner'),
    ('multiagent.deadlock', 'multiagent',
     'circular wait: worker A blocked on B while B is blocked on A',
     'A: waiting for B; B: waiting for A'),
    ('multiagent.deadlock', 'multiagent',
     'mutual blocking handoff, run stalls with no progress',
     'both agents idle, each expecting the other'),
    ('planning.budget_overrun', 'planning',
     'agent ignored the explicit $5 spend cap and kept calling paid tools',
     'spent $7.40 against a $5 cap'),
    ('planning.budget_overrun', 'planning',
     'exceeded the stated token/cost budget without stopping',
     'budget=5000 tokens, used 9200'),
    ('reflection.singleton_glitch', 'reflection',
     'a unique one-off rendering artifact unlikely to recur',
     'odd unicode in one log line'),
]


def build_corpus() -> list:
    reports = []
    for i, (mode_id, family, desc, evidence) in enumerate(_CORPUS):
        reports.append(DiagnosticReport(
            trace_id=f'trace_{i}',
            metadata={'novel_mode_candidates': [{
                'failure_mode_id': mode_id,
                'family': family,
                'description': desc,
                'evidence': [evidence],
                'step_index': i,
            }]},
        ))
    return reports


def main() -> int:
    reports = build_corpus()
    observations = collect_observations(reports)
    print(f'collected {len(observations)} residual failure observations '
          f'from {len(reports)} reports\n')

    llm = None
    base_url = os.environ.get('AGENTDEBUG_LLM_BASE_URL')
    if base_url and os.environ.get('AGENTDEBUG_LLM_API_KEY'):
        llm = OpenAICompatClient(
            base_url=base_url,
            api_key=os.environ['AGENTDEBUG_LLM_API_KEY'],
            model=os.environ.get('AGENTDEBUG_LLM_MODEL', 'gemini-3-flash'),
            default_max_tokens=2048, timeout=120.0,
        )
        print('mode: LLM-synthesized proposals\n')
    else:
        print('mode: deterministic heuristic proposals '
              '(set AGENTDEBUG_LLM_* for LLM synthesis)\n')

    inducer = TaxonomyInducer(llm=llm, min_support=2)
    proposals = inducer.induce(observations)

    print(f'=== {len(proposals)} taxonomy proposal(s) '
          f'(min_support=2 drops the one-off) ===')
    for p in proposals:
        print(f'\n[{p.status}] {p.mode.mode_id}  (support={p.support}, '
              f'family={p.mode.family})')
        print(f'  name: {p.mode.name}')
        print(f'  desc: {p.mode.description}')
        if p.mode.signals:
            print(f'  signals: {p.mode.signals}')
        print(f'  nearest seed: {p.nearest_existing_mode_id} '
              f'({p.nearest_similarity:.2f})')
        print(f'  from traces: {p.source_trace_ids}')

    # Machine-readable proposals are ready to write to a review queue.
    out = os.environ.get('PROPOSALS_OUT')
    if out:
        with open(out, 'w', encoding='utf-8') as f:
            json.dump([p.to_dict() for p in proposals], f, indent=2)
        print(f'\nwrote {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
