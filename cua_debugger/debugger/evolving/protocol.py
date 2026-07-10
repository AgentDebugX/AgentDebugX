"""Evolving-taxonomy op enum and audit-trail entry.

The 5 ops and the audit-trail entry shape are locked in
.planning/phases/12-evolving-taxonomy-ablation/12-CONTEXT.md.
"""
from __future__ import annotations
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class EvolvingOp(str, Enum):
    REUSE = "REUSE"
    DISCOVER_APPEND = "DISCOVER_APPEND"
    EDIT_RENAME = "EDIT_RENAME"
    EDIT_SPLIT = "EDIT_SPLIT"
    EDIT_MERGE = "EDIT_MERGE"


class AuditEntry(BaseModel):
    op: str
    case_id: str
    timestamp: str  # ISO 8601, set by the runner
    taxonomy_state_before: dict
    taxonomy_state_after: dict
    reasoning: str
    op_args: dict = Field(default_factory=dict)

    def to_json(self) -> dict:
        return self.model_dump(mode="json")
