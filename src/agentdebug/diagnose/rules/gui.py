"""GUI taxonomy rule pack.

Per D-08, GUI RCA is LLM-assigned rather than keyword-matched, so this pack
carries no deterministic rules — its role is to register/surface the 31 GUI
``FailureMode``s (see ``core/gui_taxonomy.py``) as a selectable rule pack. The
``build_*`` factories mirror ``core.py``'s exported names so the registry
branch resolves, and both return empty rule lists.
"""

from __future__ import annotations

from typing import List

from agentdebug.core.gui_taxonomy import list_gui_failure_modes
from agentdebug.core.models import FailureMode
from agentdebug.diagnose.rules.base import EventRule, TrajectoryRule


def build_event_rules() -> List[EventRule]:
    return []


def build_trajectory_rules() -> List[TrajectoryRule]:
    return []


def list_pack_modes() -> List[FailureMode]:
    """Return the GUI FailureModes this pack surfaces (for discoverability)."""
    return list_gui_failure_modes()
