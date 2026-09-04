"""A deterministic, fingerprinted manifest of the failure-mode taxonomy, and a
side-effect-free classification contract built on it.

Why a manifest and not the dict. A label written into a corpus is only
interpretable if the reader can recover the exact definitions the labeller
saw. ``SEED_FAILURE_MODES`` is a Python object that changes with the package;
:func:`taxonomy_manifest` freezes it into a JSON-serialisable document with a
content fingerprint, the package revision it came from, and the grouping from
leaf modes to reportable families. Two installs of the same package yield the
same fingerprint; any edit to a mode's text changes it, which is how a corpus
notices that its labels and its taxonomy drifted apart.

Why the contract is side-effect free. The manifest, the prompt compiler and
the parser know nothing about providers, budgets, retries or storage. A
consumer supplies the trajectory window, calls its own model client with the
compiled prompt, and hands the reply text back to :func:`parse_classification_response`.
Everything that costs money or writes to disk stays with the consumer, so the
same classification is reproducible from ``(fingerprint, prompt_sha256, reply)``.

Abstention is a first-class outcome. An infrastructure fault, a window that
does not contain the decisive step, or a reply that names an unknown mode are
returned as ``abstain=True`` with a reason, never forced into an agent-error
family.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from pydantic import BaseModel, Field

from agentdebug import __version__
from agentdebug.schema.taxonomy import SEED_FAILURE_MODES

MANIFEST_SCHEMA_VERSION = '1.0.0'

#: Reasons a classification may abstain instead of naming a mode.
ABSTAIN_REASONS = (
    'infrastructure_fault',   # tool/env/provider fault: no agent decision to classify
    'decisive_step_not_in_window',
    'ambiguous',              # two modes fit equally and the rationale cannot separate them
    'unknown_mode',           # the reply named a mode the manifest does not contain
    'malformed_response',     # no JSON object could be recovered from the reply
    'unparsed',               # a JSON object was found but its fields do not fit the contract
)


class ManifestMode(BaseModel):
    """One leaf mode as the labeller sees it."""

    mode_id: str
    name: str
    family: str
    description: str
    signals: List[str] = Field(default_factory=list)
    source: Optional[str] = None


class TaxonomyManifest(BaseModel):
    """The frozen taxonomy: modes, families, provenance and fingerprint."""

    schema_version: str
    taxonomy_version: str
    source_revision: Dict[str, Any]
    fingerprint: str
    families: List[str]
    modes: List[ManifestMode]

    def mode_ids(self) -> List[str]:
        return [mode.mode_id for mode in self.modes]

    def family_of(self, mode_id: str) -> Optional[str]:
        for mode in self.modes:
            if mode.mode_id == mode_id:
                return mode.family
        return None

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.model_dump())


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _fingerprint(modes: Sequence[ManifestMode]) -> str:
    payload = [mode.model_dump() for mode in sorted(modes, key=lambda m: m.mode_id)]
    return hashlib.sha256(_canonical_json(payload).encode('utf-8')).hexdigest()


def taxonomy_manifest(
    modes: Optional[Dict[str, Any]] = None,
    *,
    taxonomy_version: Optional[str] = None,
    source_revision: Optional[Dict[str, Any]] = None,
) -> TaxonomyManifest:
    """Freeze ``SEED_FAILURE_MODES`` (or ``modes``) into a fingerprinted manifest.

    ``taxonomy_version`` defaults to the package version, ``source_revision``
    to :func:`agentdebug.provenance.provenance` when importable. The
    fingerprint covers only the modes (id, name, family, description, signals,
    source), so it is stable across hosts and Python versions and changes
    exactly when a definition does. Modes are validated: ids unique, families
    non-empty, every family in ``families`` used by at least one mode.
    """
    raw = SEED_FAILURE_MODES if modes is None else modes
    listed: List[ManifestMode] = []
    seen: Set[str] = set()
    for key, mode in raw.items():
        data = mode.model_dump() if hasattr(mode, 'model_dump') else dict(mode)
        mode_id = data.get('mode_id') or key
        if mode_id in seen:
            raise ValueError(f'duplicate mode id in taxonomy: {mode_id!r}')
        if mode_id != key:
            raise ValueError(f'taxonomy key {key!r} disagrees with mode_id {mode_id!r}')
        seen.add(mode_id)
        listed.append(ManifestMode(
            mode_id=mode_id, name=str(data.get('name') or mode_id),
            family=str(data.get('family') or ''), description=str(data.get('description') or ''),
            signals=[str(s) for s in (data.get('signals') or [])], source=data.get('source'),
        ))
    if any(not mode.family for mode in listed):
        raise ValueError('every taxonomy mode needs a family')
    families = sorted({mode.family for mode in listed})
    if source_revision is None:
        try:
            from agentdebug.provenance import provenance

            source_revision = provenance()
        except Exception:  # pragma: no cover - provenance never raises, belt and braces
            source_revision = {'package': 'agentdebugx', 'version': __version__}
    listed.sort(key=lambda m: (m.family, m.mode_id))
    return TaxonomyManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        taxonomy_version=taxonomy_version or __version__,
        source_revision=dict(source_revision),
        fingerprint=_fingerprint(listed),
        families=families,
        modes=listed,
    )


# --------------------------------------------------------------------------- #
# Classification contract
# --------------------------------------------------------------------------- #


class ClassificationRequest(BaseModel):
    """What the judge is shown. The consumer renders the window; this only carries it."""

    trace_uid: str
    task_statement: str = ''
    outcome_text: str = ''
    window: str
    window_complete: bool = True
    candidate_steps: List[int] = Field(default_factory=list)


class ClassificationResult(BaseModel):
    """One judge verdict, or an explicit abstention.

    ``mode_id`` and ``family`` are ``None`` exactly when ``abstain`` is true;
    ``abstain_reason`` is then one of :data:`ABSTAIN_REASONS`. ``raw`` keeps
    the JSON object as parsed so a consumer can store the verdict verbatim.
    """

    mode_id: Optional[str] = None
    family: Optional[str] = None
    submode: Optional[str] = None
    decisive_step: Optional[int] = None
    evidence_quote: str = ''
    rationale: str = ''
    confidence: float = 0.0
    abstain: bool = False
    abstain_reason: Optional[str] = None
    raw: Dict[str, Any] = Field(default_factory=dict)



def _render_taxonomy(manifest: TaxonomyManifest) -> str:
    lines: List[str] = []
    for family in manifest.families:
        lines.append(f'## family: {family}')
        for mode in manifest.modes:
            if mode.family != family:
                continue
            signals = ', '.join(mode.signals) if mode.signals else '-'
            lines.append(f'- {mode.mode_id}: {mode.name}. {mode.description} Signals: {signals}')
    return '\n'.join(lines)


def compile_classification_prompt(
    manifest: TaxonomyManifest, request: ClassificationRequest,
) -> Tuple[str, str]:
    """Return ``(system, user)`` messages for one classification. Pure function.

    The system message carries the manifest fingerprint so a stored prompt
    hash pins the taxonomy it was compiled from.
    """
    system = (
        'You are auditing ONE failed trajectory of an LLM agent. Name the decisive error: '
        'the EARLIEST agent decision that made the failure unavoidable. Classify it into '
        'exactly one mode of the taxonomy below, or abstain.\n\n'
        f'TAXONOMY fingerprint={manifest.fingerprint} version={manifest.taxonomy_version}\n'
        f'{_render_taxonomy(manifest)}\n\n'
        'RULES\n'
        '1. Judge only from the window shown. If the decisive step is not in it, abstain with '
        'reason "decisive_step_not_in_window".\n'
        '2. If no agent decision is wrong on the evidence and the failure is a tool, environment '
        'or provider fault, abstain with reason "infrastructure_fault".\n'
        '3. If two modes fit equally and you cannot separate them, abstain with reason '
        '"ambiguous" and name both in the rationale.\n'
        '4. evidence_quote must be copied verbatim from the window.\n\n'
        'Answer with ONE JSON object and nothing else:\n'
        '{"mode_id": "<mode id or null>", "submode": "<short phrase or null>", '
        '"decisive_step": <integer step or null>, "evidence_quote": "<verbatim>", '
        '"rationale": "<one or two sentences>", "confidence": <0.0-1.0>, '
        '"abstain": <true|false>, "abstain_reason": "<one of '
        + ', '.join(ABSTAIN_REASONS[:3]) + ' or null>"}'
    )
    completeness = 'complete' if request.window_complete else 'earlier steps omitted, tail kept'
    hints = ''
    if request.candidate_steps:
        hints = '\nCANDIDATE STEPS (from a mechanical detector; not binding): ' + ', '.join(
            str(step) for step in request.candidate_steps)
    user = (
        f'TRACE {request.trace_uid}\n'
        f'TASK STATEMENT\n{request.task_statement or "(not provided)"}\n\n'
        f'OUTCOME\n{request.outcome_text or "(not provided)"}\n\n'
        f'TRAJECTORY ({completeness}){hints}\n{request.window}'
    )
    return system, user


_FENCE = re.compile(r'```(?:json)?\s*(\{.*?\})\s*```', re.S)


def _extract_object(text: str) -> Optional[Dict[str, Any]]:
    """The first JSON object in ``text``: fenced, bare, or embedded in prose."""
    if not text:
        return None
    candidates: List[str] = [m.group(1) for m in _FENCE.finditer(text)]
    stripped = text.strip()
    if stripped.startswith('{'):
        candidates.insert(0, stripped)
    start = text.find('{')
    while start >= 0:
        depth = 0
        for index in range(start, len(text)):
            char = text[index]
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:index + 1])
                    break
        start = text.find('{', start + 1)
        if len(candidates) > 8:
            break
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _abstain(reason: str, raw: Optional[Dict[str, Any]] = None,
             rationale: str = '') -> ClassificationResult:
    if reason not in ABSTAIN_REASONS:
        raise ValueError(f'unknown abstain reason {reason!r}')
    return ClassificationResult(abstain=True, abstain_reason=reason, rationale=rationale,
                                raw=raw or {})


def parse_classification_response(text: str, manifest: TaxonomyManifest) -> ClassificationResult:
    """Turn a judge reply into a :class:`ClassificationResult`; never raises.

    A reply without a JSON object abstains with ``malformed_response``; one
    whose fields do not fit abstains with ``unparsed``; one naming a mode the
    manifest lacks abstains with ``unknown_mode`` and keeps the offending id in
    ``raw``; an explicit ``"abstain": true`` is honoured with its reason
    (an unknown reason becomes ``unparsed``).
    """
    parsed = _extract_object(text)
    if parsed is None:
        return _abstain('malformed_response')
    try:
        if parsed.get('abstain') is True:
            reason = parsed.get('abstain_reason')
            if reason not in ABSTAIN_REASONS:
                reason = 'unparsed'
            return ClassificationResult(
                abstain=True, abstain_reason=str(reason),
                rationale=str(parsed.get('rationale') or ''),
                decisive_step=_int_or_none(parsed.get('decisive_step')),
                confidence=_float(parsed.get('confidence')), raw=parsed,
            )
        mode_id = parsed.get('mode_id')
        if not isinstance(mode_id, str) or not mode_id:
            return _abstain('unparsed', parsed, 'reply named no mode and did not abstain')
        family = manifest.family_of(mode_id)
        if family is None:
            return _abstain('unknown_mode', parsed, f'unknown mode {mode_id!r}')
        return ClassificationResult(
            mode_id=mode_id, family=family,
            submode=_str_or_none(parsed.get('submode')),
            decisive_step=_int_or_none(parsed.get('decisive_step')),
            evidence_quote=str(parsed.get('evidence_quote') or ''),
            rationale=str(parsed.get('rationale') or ''),
            confidence=_float(parsed.get('confidence')), raw=parsed,
        )
    except (TypeError, ValueError) as exc:
        return _abstain('unparsed', parsed, f'{type(exc).__name__}: {exc}')


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip('-').isdigit():
        return int(value.strip())
    return None


def _str_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float(value: Any) -> float:
    """A confidence in [0, 1]; anything unparseable is 0."""
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- #
# Agreement
# --------------------------------------------------------------------------- #


def cohens_kappa(labels_a: Sequence[Optional[str]],
                 labels_b: Sequence[Optional[str]]) -> Optional[float]:
    """Cohen's kappa between two labelers over the items both labelled.

    Items where either side is ``None`` (abstained / unlabelled) are excluded.
    Returns ``None`` when fewer than one paired item remains or when expected
    agreement is 1 (a single category on both sides), where kappa is undefined.
    """
    if len(labels_a) != len(labels_b):
        raise ValueError('label sequences must have the same length')
    pairs = [(a, b) for a, b in zip(labels_a, labels_b) if a is not None and b is not None]
    if not pairs:
        return None
    n = len(pairs)
    observed = sum(1 for a, b in pairs if a == b) / n
    categories = {a for a, _ in pairs} | {b for _, b in pairs}
    expected = 0.0
    for category in categories:
        pa = sum(1 for a, _ in pairs if a == category) / n
        pb = sum(1 for _, b in pairs if b == category) / n
        expected += pa * pb
    if expected >= 1.0:
        return None
    return (observed - expected) / (1.0 - expected)


__all__ = [
    'ABSTAIN_REASONS', 'MANIFEST_SCHEMA_VERSION', 'ClassificationRequest',
    'ClassificationResult', 'ManifestMode', 'TaxonomyManifest', 'cohens_kappa',
    'compile_classification_prompt', 'parse_classification_response', 'taxonomy_manifest',
]
