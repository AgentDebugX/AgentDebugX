"""Automatic error-taxonomy induction.

The seed taxonomy (:data:`agentdebug.schema.SEED_FAILURE_MODES`) covers
19 well-known modes, but deployed agents fail in long-tail ways the seed set
does not name. This module closes that loop: it harvests failures the existing
taxonomy could *not* cleanly label, clusters the recurring ones, and proposes
new ``FailureMode`` nodes for human review --- without ever overwriting the
reviewed seed taxonomy.

Pipeline (see :class:`TaxonomyInducer`):

1. **Collect** --- gather :class:`FailureObservation` records from a corpus of
   diagnostic reports. The richest signal is a *novel-mode candidate*: a label
   the LLM judge wanted to use but that is not in the seed set (the judge now
   records these in ``report.metadata['novel_mode_candidates']`` instead of
   silently dropping them). Low-confidence and catch-all findings are also
   collected.
2. **Cluster** --- group similar observations. Deterministic lexical (token
   Jaccard) clustering by default; embedding-cosine clustering when an
   :class:`EmbeddingClient` is supplied.
3. **Propose** --- for each cluster whose support meets ``min_support``,
   synthesize one candidate ``FailureMode`` (id, name, family, description,
   signals, suggestions). An LLM does this when available; otherwise a
   deterministic heuristic builds the proposal from the cluster's common terms.
4. **Dedup** --- compare each proposal to the seed taxonomy (and to other
   proposals). A proposal too close to an existing mode is marked
   ``status='duplicate'`` and linked to its nearest neighbor; the rest are
   ``status='proposed'`` and are returned for review.

Nothing here mutates the seed taxonomy: induction is a *proposal* step gated on
human acceptance, mirroring how the store keeps generated labels separate from
reviewed ones.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agentdebug.runtime import EmbeddingClient, LLMClient, extract_json_block
from agentdebug.schema import (
    SEED_FAILURE_MODES,
    DiagnosticReport,
    FailureMode,
    new_id,
)

VALID_FAMILIES = (
    'memory',
    'reflection',
    'planning',
    'action',
    'system',
    'multiagent',
    'verification',
    'multimodal',
)

_STOPWORDS = {
    'the', 'a', 'an', 'and', 'or', 'of', 'to', 'in', 'on', 'at', 'for', 'is',
    'was', 'were', 'be', 'by', 'with', 'that', 'this', 'it', 'its', 'as', 'but',
    'agent', 'step', 'error', 'failed', 'failure', 'task', 'when', 'then',
}


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

@dataclass
class FailureObservation:
    """One failure the existing taxonomy did not cleanly capture."""

    text: str
    evidence: List[str] = field(default_factory=list)
    trace_id: Optional[str] = None
    agent_name: Optional[str] = None
    step_index: Optional[int] = None
    suggested_name: Optional[str] = None
    suggested_family: Optional[str] = None
    source: str = 'unspecified'

    def signature(self) -> str:
        parts = [self.text, self.suggested_name or '']
        parts.extend(self.evidence)
        return ' '.join(p for p in parts if p)


@dataclass
class TaxonomyProposal:
    """A candidate taxonomy node awaiting human review."""

    proposal_id: str
    mode: FailureMode
    support: int
    example_evidence: List[str] = field(default_factory=list)
    source_trace_ids: List[str] = field(default_factory=list)
    status: str = 'proposed'  # 'proposed' | 'duplicate'
    rationale: str = ''
    nearest_existing_mode_id: Optional[str] = None
    nearest_similarity: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        return {
            'proposal_id': self.proposal_id,
            'mode': self.mode.model_dump(),
            'support': self.support,
            'example_evidence': self.example_evidence,
            'source_trace_ids': self.source_trace_ids,
            'status': self.status,
            'rationale': self.rationale,
            'nearest_existing_mode_id': self.nearest_existing_mode_id,
            'nearest_similarity': round(self.nearest_similarity, 3),
        }


# --------------------------------------------------------------------------- #
# Collection
# --------------------------------------------------------------------------- #

def collect_observations(
    reports: Sequence[DiagnosticReport],
    *,
    low_confidence_threshold: float = 0.4,
    include_low_confidence: bool = True,
) -> List[FailureObservation]:
    """Harvest failures worth re-taxonomizing from a corpus of reports.

    Three signals are gathered: (1) novel-mode candidates the judge could not
    map to the seed set, (2) optionally, low-confidence findings, and (3)
    findings whose mode is missing from the seed taxonomy (defensive).
    """

    observations: List[FailureObservation] = []
    for report in reports:
        for cand in report.metadata.get('novel_mode_candidates', []) or []:
            if not isinstance(cand, dict):
                continue
            ev = cand.get('evidence') or []
            mode_id = str(cand.get('failure_mode_id') or '')
            family = cand.get('family')
            if family not in VALID_FAMILIES:
                # The judge's proposed id usually carries the family as a prefix
                # ("multiagent.deadlock"); recover it when no explicit field.
                prefix = mode_id.split('.', 1)[0] if '.' in mode_id else ''
                family = prefix if prefix in VALID_FAMILIES else None
            observations.append(
                FailureObservation(
                    text=str(cand.get('description') or mode_id or ''),
                    evidence=[str(e) for e in ev] if isinstance(ev, list) else [str(ev)],
                    trace_id=report.trace_id,
                    agent_name=cand.get('agent_name'),
                    step_index=cand.get('step_index'),
                    suggested_name=cand.get('name') or mode_id or None,
                    suggested_family=family,
                    source='judge_novel_mode',
                )
            )
        for finding in report.findings:
            mode = finding.failure_mode
            is_unknown = mode.mode_id not in SEED_FAILURE_MODES
            is_low = (
                include_low_confidence
                and finding.confidence is not None
                and finding.confidence < low_confidence_threshold
            )
            if not (is_unknown or is_low):
                continue
            observations.append(
                FailureObservation(
                    text=mode.description or mode.name or mode.mode_id,
                    evidence=list(finding.evidence),
                    trace_id=report.trace_id,
                    agent_name=finding.agent_name,
                    step_index=finding.step_index,
                    suggested_name=mode.name,
                    suggested_family=mode.family if mode.family in VALID_FAMILIES else None,
                    source='unknown_mode' if is_unknown else 'low_confidence',
                )
            )
    return observations


# --------------------------------------------------------------------------- #
# Text / similarity helpers
# --------------------------------------------------------------------------- #

def _tokens(text: str) -> set:
    words = re.findall(r'[a-z0-9]+', text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)


def _slugify(text: str, max_words: int = 3) -> str:
    toks = [w for w in re.findall(r'[a-z0-9]+', text.lower())
            if len(w) > 2 and w not in _STOPWORDS]
    if not toks:
        return 'pattern'
    return '_'.join(toks[:max_words])


# --------------------------------------------------------------------------- #
# Inducer
# --------------------------------------------------------------------------- #

class TaxonomyInducer:
    """Cluster residual failures and propose new taxonomy nodes."""

    def __init__(
        self,
        *,
        llm: Optional[LLMClient] = None,
        embedder: Optional[EmbeddingClient] = None,
        min_support: int = 2,
        cluster_threshold: float = 0.28,
        dedup_threshold: float = 0.6,
        max_proposals: int = 12,
        max_tokens: int = 1024,
    ) -> None:
        self.llm = llm
        self.embedder = embedder
        self.min_support = min_support
        self.cluster_threshold = cluster_threshold
        self.dedup_threshold = dedup_threshold
        self.max_proposals = max_proposals
        self.max_tokens = max_tokens

    # -- public API -------------------------------------------------------- #

    def induce(
        self, observations: Sequence[FailureObservation]
    ) -> List[TaxonomyProposal]:
        if not observations:
            return []
        clusters = self._cluster(list(observations))
        clusters = [c for c in clusters if len(c) >= self.min_support]
        clusters.sort(key=len, reverse=True)
        proposals: List[TaxonomyProposal] = []
        for cluster in clusters[: self.max_proposals]:
            proposal = self._propose(cluster)
            if proposal is not None:
                self._tag_duplicate(proposal, proposals)
                proposals.append(proposal)
        return proposals

    # -- clustering -------------------------------------------------------- #

    def _cluster(
        self, observations: List[FailureObservation]
    ) -> List[List[FailureObservation]]:
        if self.embedder is not None:
            return self._cluster_embedding(observations)
        return self._cluster_lexical(observations)

    def _cluster_lexical(
        self, observations: List[FailureObservation]
    ) -> List[List[FailureObservation]]:
        # Phase 1: pre-group by the label the judge proposed. When several
        # traces carry the same novel mode_id / suggested name, that exact-match
        # signal is stronger than any text similarity, and it survives complete
        # paraphrase of the surrounding evidence.
        buckets: Dict[str, List[FailureObservation]] = {}
        loners: List[FailureObservation] = []
        for obs in observations:
            key = _slugify(obs.suggested_name or '', max_words=4)
            if obs.suggested_name and key != 'pattern':
                buckets.setdefault(key, []).append(obs)
            else:
                loners.append(obs)

        clusters: List[List[FailureObservation]] = [list(v) for v in buckets.values()]
        cluster_tokens: List[set] = [
            set().union(*[_tokens(o.signature()) for o in c]) for c in clusters
        ]

        # Phase 2: lexical Jaccard agglomeration for the rest, also allowed to
        # merge into a label bucket if the wording overlaps enough.
        for obs in loners:
            toks = _tokens(obs.signature())
            best_i, best_sim = -1, 0.0
            for i, ctoks in enumerate(cluster_tokens):
                sim = _jaccard(toks, ctoks)
                if sim > best_sim:
                    best_i, best_sim = i, sim
            if best_i >= 0 and best_sim >= self.cluster_threshold:
                clusters[best_i].append(obs)
                cluster_tokens[best_i] = cluster_tokens[best_i] | toks
            else:
                clusters.append([obs])
                cluster_tokens.append(set(toks))
        return clusters

    def _cluster_embedding(
        self, observations: List[FailureObservation]
    ) -> List[List[FailureObservation]]:
        try:
            vectors = self.embedder.embed([o.signature() for o in observations])
        except Exception:
            return self._cluster_lexical(observations)
        if len(vectors) != len(observations):
            return self._cluster_lexical(observations)
        clusters: List[List[FailureObservation]] = []
        centroids: List[List[float]] = []
        # Embedding cosine runs hotter than lexical Jaccard; use a firmer cut.
        threshold = max(self.cluster_threshold, 0.6)
        for obs, vec in zip(observations, vectors):
            best_i, best_sim = -1, 0.0
            for i, cen in enumerate(centroids):
                sim = _cosine(vec, cen)
                if sim > best_sim:
                    best_i, best_sim = i, sim
            if best_i >= 0 and best_sim >= threshold:
                clusters[best_i].append(obs)
                cen = centroids[best_i]
                n = len(clusters[best_i])
                centroids[best_i] = [(c * (n - 1) + v) / n for c, v in zip(cen, vec)]
            else:
                clusters.append([obs])
                centroids.append(list(vec))
        return clusters

    # -- proposal synthesis ----------------------------------------------- #

    def _propose(
        self, cluster: List[FailureObservation]
    ) -> Optional[TaxonomyProposal]:
        if self.llm is not None:
            proposal = self._propose_llm(cluster)
            if proposal is not None:
                return proposal
        return self._propose_heuristic(cluster)

    def _propose_heuristic(
        self, cluster: List[FailureObservation]
    ) -> TaxonomyProposal:
        family = self._majority_family(cluster)
        # Distinctive terms across the cluster signatures.
        counts: Dict[str, int] = {}
        for obs in cluster:
            for tok in _tokens(obs.signature()):
                counts[tok] = counts.get(tok, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        top = [t for t, _ in ranked[:5]]
        slug = _slugify(' '.join(top)) or 'pattern'
        name = (cluster[0].suggested_name
                or (top[0].capitalize() + ' pattern' if top else 'Recurring failure'))
        description = (
            f'Recurring {family} failure observed across {len(cluster)} traces, '
            f'characterized by: {", ".join(top) if top else "shared evidence"}.'
        )
        evidence = self._collect_evidence(cluster)
        mode = FailureMode(
            mode_id=f'{family}.induced_{slug}',
            name=name,
            family=family,
            description=description,
            signals=top,
            suggestion_templates=[
                'Review the clustered traces and add a targeted rule or judge '
                'hint for this pattern.',
            ],
            source='AgentDebugX induced (heuristic)',
        )
        return TaxonomyProposal(
            proposal_id=new_id('proposal'),
            mode=mode,
            support=len(cluster),
            example_evidence=evidence[:5],
            source_trace_ids=self._trace_ids(cluster),
            rationale=(
                f'{len(cluster)} residual failures share the terms '
                f'{top}; no seed mode named this pattern.'
            ),
        )

    def _propose_llm(
        self, cluster: List[FailureObservation]
    ) -> Optional[TaxonomyProposal]:
        existing = '\n'.join(
            f'- {m.mode_id} ({m.family}): {m.name}'
            for m in SEED_FAILURE_MODES.values()
        )
        samples = '\n'.join(
            f'- {o.text}'
            + (f' | evidence: {"; ".join(o.evidence[:2])}' if o.evidence else '')
            for o in cluster[:8]
        )
        system = _INDUCE_PROMPT.format(families=', '.join(VALID_FAMILIES))
        user = (
            f'EXISTING TAXONOMY (do NOT duplicate these):\n{existing}\n\n'
            f'CLUSTER OF {len(cluster)} UNLABELED FAILURES:\n{samples}\n\n'
            'Propose ONE new failure mode that names this cluster.'
        )
        try:
            result = self.llm.complete(
                messages=[
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': user},
                ],
                max_tokens=self.max_tokens,
            )
        except Exception:
            return None
        parsed = extract_json_block(result.text)
        if not parsed:
            return None
        family = str(parsed.get('family') or '').strip().lower()
        if family not in VALID_FAMILIES:
            family = self._majority_family(cluster)
        name = str(parsed.get('name') or '').strip()
        description = str(parsed.get('description') or '').strip()
        if not name or not description:
            return None
        mode_id = str(parsed.get('mode_id') or '').strip().lower()
        if not mode_id or '.' not in mode_id:
            mode_id = f'{family}.induced_{_slugify(name)}'
        signals = parsed.get('signals') or []
        suggestions = parsed.get('suggestions') or parsed.get('suggestion_templates') or []
        mode = FailureMode(
            mode_id=mode_id,
            name=name,
            family=family,
            description=description,
            signals=[str(s) for s in signals][:6] if isinstance(signals, list) else [],
            suggestion_templates=(
                [str(s) for s in suggestions][:4] if isinstance(suggestions, list)
                else [str(suggestions)]
            ),
            source='AgentDebugX induced (LLM)',
        )
        return TaxonomyProposal(
            proposal_id=new_id('proposal'),
            mode=mode,
            support=len(cluster),
            example_evidence=self._collect_evidence(cluster)[:5],
            source_trace_ids=self._trace_ids(cluster),
            rationale=str(parsed.get('rationale') or
                          f'Synthesized from {len(cluster)} clustered failures.'),
        )

    # -- dedup ------------------------------------------------------------- #

    def _tag_duplicate(
        self, proposal: TaxonomyProposal, prior: List[TaxonomyProposal]
    ) -> None:
        prop_tokens = _tokens(' '.join([
            proposal.mode.name,
            proposal.mode.description,
            ' '.join(proposal.mode.signals),
            ' '.join(proposal.example_evidence),
        ]))
        best_id, best_sim = None, 0.0
        for seed in SEED_FAILURE_MODES.values():
            sim = _jaccard(
                prop_tokens, _tokens(f'{seed.name} {seed.description}')
            )
            if seed.family == proposal.mode.family:
                sim += 0.05  # same-family tie-break toward duplicate
            if sim > best_sim:
                best_id, best_sim = seed.mode_id, sim
        for other in prior:
            sim = _jaccard(
                prop_tokens, _tokens(f'{other.mode.name} {other.mode.description}')
            )
            if sim > best_sim:
                best_id, best_sim = other.mode.mode_id, sim
        proposal.nearest_existing_mode_id = best_id
        proposal.nearest_similarity = best_sim
        if best_sim >= self.dedup_threshold:
            proposal.status = 'duplicate'

    # -- small helpers ----------------------------------------------------- #

    @staticmethod
    def _majority_family(cluster: List[FailureObservation]) -> str:
        counts: Dict[str, int] = {}
        for obs in cluster:
            fam = obs.suggested_family
            if fam not in VALID_FAMILIES and obs.suggested_name and '.' in obs.suggested_name:
                prefix = obs.suggested_name.split('.', 1)[0]
                fam = prefix if prefix in VALID_FAMILIES else None
            if fam in VALID_FAMILIES:
                counts[fam] = counts.get(fam, 0) + 1
        if counts:
            return max(counts.items(), key=lambda kv: kv[1])[0]
        return 'planning'

    @staticmethod
    def _collect_evidence(cluster: List[FailureObservation]) -> List[str]:
        seen: List[str] = []
        for obs in cluster:
            for ev in obs.evidence:
                if ev and ev not in seen:
                    seen.append(ev)
            if not obs.evidence and obs.text and obs.text not in seen:
                seen.append(obs.text)
        return seen

    @staticmethod
    def _trace_ids(cluster: List[FailureObservation]) -> List[str]:
        out: List[str] = []
        for obs in cluster:
            if obs.trace_id and obs.trace_id not in out:
                out.append(obs.trace_id)
        return out


_INDUCE_PROMPT = """You are AgentDebugX-Taxonomist. You are given a cluster of
agent failures that an existing taxonomy could NOT label, plus the existing
taxonomy. Propose ONE new failure mode that names the shared root pattern.

Rules:
* The new mode must be GENERAL (reusable across tasks), not a one-off.
* It must NOT duplicate any existing mode. If the cluster is already covered,
  still return your closest new framing but keep it distinct.
* family MUST be one of: {families}.
* mode_id format: "<family>.<short_snake_case>".

Output ONLY a JSON object:
{{"mode_id":"...", "name":"...", "family":"...",
  "description":"<one or two sentences>",
  "signals":["<detectable cue>", ...],
  "suggestions":["<actionable fix>", ...],
  "rationale":"<why this is a distinct, reusable mode>"}}
"""
