"""Evolving-taxonomy ablation protocol (Phase 12).

Defines the op schema, taxonomy state object, prompts, and tool schemas
used when the debugger model must DISCOVER a taxonomy from an empty start.
"""
from .protocol import EvolvingOp, AuditEntry
from .state import TaxonomyState
from .case_loader import load_case_set, shuffle_for_seed, resolve_traj_dirs

__all__ = [
    "EvolvingOp", "AuditEntry", "TaxonomyState",
    "load_case_set", "shuffle_for_seed", "resolve_traj_dirs",
]
