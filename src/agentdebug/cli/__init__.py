"""AgentDebugX command-line package."""

from collections.abc import Sequence
from typing import Optional


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Load parser assembly lazily so service modules can reuse CLI helpers."""
    from agentdebug.cli.main import main as _main

    return _main(argv)

__all__ = ['main']
