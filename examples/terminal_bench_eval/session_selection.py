"""Select a Seed trial's primary Claude native session and copy it to an
immutable, hashed diagnostic-input path.

Implements Option A from docs/architecture.md: after a Seed trial's hidden
verifier confirms an agent failure, the outer loop selects the one primary
Claude session JSONL (excluding subagent transcripts), copies it to a stable
path, and records its SHA-256. rerun-deep and rerun-skill then diagnose that
exact byte-identical copy, never the live session a resumed Claude
conversation is still appending to.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path


class NoPrimarySessionError(RuntimeError):
    """Zero or more than one candidate primary session was found."""


class IncompleteSessionError(RuntimeError):
    """A session file contains a line that is not a complete JSON record.

    diagnose's JSONL reader does not skip an incomplete trailing line
    (docs/architecture.md), so a session must be rejected outright rather
    than silently truncated at the first bad line.
    """


def find_primary_session(agent_dir: Path) -> Path:
    """Return the one primary Claude native session under ``agent_dir``.

    Native sessions live under ``agent_dir/sessions/**/*.jsonl``; subagent
    transcripts live below a ``subagents/`` directory and are excluded.
    """
    sessions_dir = agent_dir / 'sessions'
    candidates = sorted(
        path
        for path in sessions_dir.rglob('*.jsonl')
        if 'subagents' not in path.relative_to(sessions_dir).parts
    )
    if len(candidates) != 1:
        raise NoPrimarySessionError(
            f'expected exactly one primary session under {sessions_dir}, '
            f'found {len(candidates)}: {[str(c) for c in candidates]}'
        )
    return candidates[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_complete_jsonl(path: Path) -> int:
    """Raise if any non-blank line is not complete JSON; return record count."""
    record_count = 0
    with path.open('r', encoding='utf-8') as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                raise IncompleteSessionError(
                    f'{path}:{line_no}: malformed JSON record: {exc}'
                ) from exc
            record_count += 1
    return record_count


@dataclass(frozen=True)
class DiagnosticInput:
    """An immutable, hashed copy of a Seed trial's primary session."""

    path: Path
    source_path: Path
    sha256: str
    record_count: int

    def to_metadata(self) -> dict:
        return {
            'path': str(self.path),
            'source_path': str(self.source_path),
            'sha256': self.sha256,
            'record_count': self.record_count,
        }


def copy_diagnostic_input(session_path: Path, dest_dir: Path, *, name: str) -> DiagnosticInput:
    """Copy ``session_path`` to an immutable ``dest_dir/<name>/<session-id>.jsonl``.

    The copy keeps the source's own ``<session-id>.jsonl`` filename rather
    than being renamed to ``name``: harbor's Claude Code agent validates
    ``--load-trajectory`` by parsing the filename stem as a UUID (see
    ``ClaudeCode._validate_native_load_trajectory``), so a diagnostic input
    named e.g. ``raman-fitting.seed.jsonl`` is rejected outright. ``name``
    instead becomes a subdirectory, keeping the copy associated with its
    task/method without touching the filename harbor inspects.

    The copy is written to a temp file and atomically renamed into place,
    then made read-only, so a resumed conversation appending to its own
    seeded session can never mutate this diagnostic copy. Refuses a
    partially-written session (see IncompleteSessionError) so rerun-deep and
    rerun-skill never diagnose a truncated trajectory.
    """
    record_count = _validate_complete_jsonl(session_path)

    target_dir = dest_dir / name
    target_dir.mkdir(parents=True, exist_ok=True)
    dest_path = target_dir / session_path.name
    temp_path = dest_path.with_suffix('.jsonl.tmp')
    shutil.copyfile(session_path, temp_path)
    temp_path.replace(dest_path)
    dest_path.chmod(0o444)

    diagnostic_input = DiagnosticInput(
        path=dest_path,
        source_path=session_path.resolve(),
        sha256=_sha256(dest_path),
        record_count=record_count,
    )
    metadata_path = target_dir / 'diagnostic-input.json'
    metadata_path.write_text(
        json.dumps(diagnostic_input.to_metadata(), indent=2) + '\n', encoding='utf-8'
    )
    return diagnostic_input
