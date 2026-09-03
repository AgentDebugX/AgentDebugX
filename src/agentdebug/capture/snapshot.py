"""Consistent reading of complete JSONL transcript records."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

from agentdebug.capture.contracts import TranscriptSnapshot


class SnapshotError(ValueError):
    """Raised when committed transcript bytes are not valid JSONL objects."""


def read_complete_jsonl(path: Path) -> TranscriptSnapshot:
    resolved = path.expanduser().resolve(strict=True)
    with resolved.open('rb') as handle:
        content = handle.read()

    complete_size = content.rfind(b'\n') + 1
    complete = content[:complete_size]
    tail = content[complete_size:]
    records: List[Dict[str, Any]] = []
    last_record = b''
    for line_number, raw_line in enumerate(complete.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SnapshotError(
                f'malformed committed JSONL record at line {line_number}'
            ) from exc
        if not isinstance(value, dict):
            raise SnapshotError(
                f'committed JSONL record at line {line_number} is not an object'
            )
        records.append(value)
        last_record = raw_line

    return TranscriptSnapshot(
        path=resolved,
        complete_bytes=complete,
        complete_size=len(complete),
        content_sha256=hashlib.sha256(complete).hexdigest(),
        last_record_sha256=hashlib.sha256(last_record).hexdigest(),
        records=records,
        ignored_tail_bytes=len(tail),
    )
