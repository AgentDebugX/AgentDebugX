"""Seed GUI failure taxonomy for AgentDebugX.

Registers the 31-subtype OSWorld/CUA GUI error taxonomy (P/G/R/S/IF) as
``FailureMode`` objects keyed by ``gui.<code_lower>``. The source definitions
live in ``agentdebug.gui.taxonomy``; this module mirrors ``core/taxonomy.py``'s
``_mode``/``SEED_*`` shape and generates one entry per code rather than
hand-writing 31 dicts.

The GUI import stays inside ``_load_gui_taxonomy`` so it never happens at
``import agentdebug`` time — only when this module is imported.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from agentdebug.schema.models import FailureMode

_SOURCE = 'CUA / OSWorld GUI taxonomy v2'


def _load_gui_taxonomy() -> Tuple[Dict[str, str], Dict[str, str]]:
    """Import the GUI taxonomy maps from the package-owned GUI surface."""
    from agentdebug.gui.taxonomy import SUBTYPE_DEFINITIONS, SUBTYPE_TO_CATEGORY

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
    definitions, subtype_to_category = _load_gui_taxonomy()
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
