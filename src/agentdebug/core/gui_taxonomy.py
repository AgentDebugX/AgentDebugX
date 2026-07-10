"""Seed GUI failure taxonomy for AgentDebugX.

Registers the 31-subtype OSWorld/CUA GUI error taxonomy (P/G/R/S/IF) as
``FailureMode`` objects keyed by ``gui.<code_lower>``. The source definitions
live in the vendored CUA tree (``cua_debugger/debugger/taxonomy.py``); this
module mirrors ``core/taxonomy.py``'s ``_mode``/``SEED_*`` shape and generates
one entry per code rather than hand-writing 31 dicts.

The CUA import is guarded and lazy (mirroring
``ingest/adapters/osworld.py``) so it never happens at ``import agentdebug``
time — only when this module is imported, and it surfaces a clear error if the
vendored source tree is absent.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from agentdebug.core.models import FailureMode

_SOURCE = 'CUA / OSWorld GUI taxonomy v2'


def _load_cua_taxonomy() -> Tuple[Dict[str, str], Dict[str, str]]:
    """Import the vendored CUA taxonomy maps lazily; fail with a clear error.

    Resolves ``<repo>/cua_debugger`` onto ``sys.path`` at call time (only when
    the directory exists and is not already present) and imports the raw
    definition maps. Never imports ``debugger`` at module top level.
    """
    cua_root = Path(__file__).resolve().parents[3] / 'cua_debugger'
    if cua_root.is_dir() and str(cua_root) not in sys.path:
        sys.path.insert(0, str(cua_root))
    try:
        from debugger.taxonomy import SUBTYPE_DEFINITIONS, SUBTYPE_TO_CATEGORY
    except ImportError as exc:
        raise ImportError(
            'GUI taxonomy requires the vendored CUA source tree (cua_debugger) '
            'on sys.path. Add cua_debugger/ to your PYTHONPATH.'
        ) from exc
    return SUBTYPE_DEFINITIONS, SUBTYPE_TO_CATEGORY


def _mode(
    mode_id: str,
    name: str,
    family: str,
    description: str,
    signals: List[str],
    suggestions: List[str],
    source: str,
) -> FailureMode:
    return FailureMode(
        mode_id=mode_id,
        name=name,
        family=family,
        description=description,
        signals=signals,
        suggestion_templates=suggestions,
        source=source,
    )


def _title(definition: str) -> str:
    """Return the title portion of a subtype definition (text before the em-dash)."""
    return definition.split('\u2014', 1)[0].strip()


def _build_seed() -> Tuple[Dict[str, FailureMode], Dict[str, str], Dict[str, str]]:
    definitions, subtype_to_category = _load_cua_taxonomy()
    seed: Dict[str, FailureMode] = {}
    code_to_mode_id: Dict[str, str] = {}
    mode_id_to_code: Dict[str, str] = {}
    for code, definition in definitions.items():
        mode_id = f'gui.{code.lower()}'
        seed[mode_id] = _mode(
            mode_id,
            _title(definition),
            subtype_to_category[code],
            definition,
            [code],
            [],
            _SOURCE,
        )
        code_to_mode_id[code] = mode_id
        mode_id_to_code[mode_id] = code
    return seed, code_to_mode_id, mode_id_to_code


SEED_GUI_FAILURE_MODES, CODE_TO_MODE_ID, MODE_ID_TO_CODE = _build_seed()


def get_gui_failure_mode(mode_id: str) -> Optional[FailureMode]:
    return SEED_GUI_FAILURE_MODES.get(mode_id)


def list_gui_failure_modes() -> List[FailureMode]:
    return list(SEED_GUI_FAILURE_MODES.values())


def gui_failure_mode_for_code(code: str) -> Optional[FailureMode]:
    """Resolve a raw taxonomy code (e.g. ``"P1"``) to its registered FailureMode."""
    mode_id = CODE_TO_MODE_ID.get(code)
    return SEED_GUI_FAILURE_MODES.get(mode_id) if mode_id else None
