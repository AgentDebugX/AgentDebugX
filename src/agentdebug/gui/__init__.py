"""GUI / computer-use agent (CUA) root-cause analysis surface.

This subpackage owns the OSWorld/CUA trajectory format and the ReAct
backward-tracing RCA engine that :class:`agentdebug.diagnose.gui_rca.GuiRcaAnalyzer`
drives. It is package-owned rather than resolved off ``sys.path``, so GUI RCA
behaves the same in an installed wheel as it does in a source checkout.

Layers, and the extra each one needs:

``taxonomy`` ``config`` ``prompts`` ``trajectory`` ``ingester`` ``dispatch``
``agent`` ``rca`` ``tagger`` ``output`` ``tools`` ``utils``
    The RCA main path. Requires only ``pydantic``; ``pillow`` (the ``gui``
    extra) is imported lazily by :mod:`agentdebug.gui.trajectory` when
    screenshots are decoded.

``memory`` ``evolving.alignment``
    Lesson and episodic memory, and taxonomy alignment. Needs the
    ``gui-memory`` extra (``langchain``, ``chromadb``, ``scikit-learn``).

``together_adapter`` ``openai_adapter`` ``perplexity_adapter`` ``discuss``
``pipeline`` ``evolving`` ``vis`` ``__main__``
    Provider clients, the batch pipeline, and the Streamlit annotation app.
    Need the ``gui-app`` extra (``anthropic``, ``openai``, ``together``,
    ``streamlit``, ``pandas``).

Attribute access below is lazy, and no submodule is imported at package import
time, so ``import agentdebug.gui`` stays free of every optional dependency.
"""

from __future__ import annotations

_LAZY_ATTRS = {
    'ALL_SUBTYPES': 'agentdebug.gui.taxonomy',
    'SUBTYPE_DEFINITIONS': 'agentdebug.gui.taxonomy',
    'SUBTYPE_TO_CATEGORY': 'agentdebug.gui.taxonomy',
    'TAXONOMY_CATEGORIES': 'agentdebug.gui.taxonomy',
    'TAXONOMY_DEFINITIONS': 'agentdebug.gui.taxonomy',
    'DebuggerConfig': 'agentdebug.gui.config',
    'load_config': 'agentdebug.gui.config',
    'IngestionResult': 'agentdebug.gui.ingester',
    'Step': 'agentdebug.gui.ingester',
    'load_trajectory': 'agentdebug.gui.trajectory',
    'load_normalized_trajectory': 'agentdebug.gui.trajectory',
    'RCAResult': 'agentdebug.gui.rca',
    'StepSummary': 'agentdebug.gui.rca',
    'run_rca': 'agentdebug.gui.rca',
    'run_react_loop': 'agentdebug.gui.agent',
    'TaxonomyTag': 'agentdebug.gui.tagger',
    'tag_from_rca': 'agentdebug.gui.tagger',
    'soft_tag_candidates': 'agentdebug.gui.tagger',
    'build_output': 'agentdebug.gui.output',
    'print_summary': 'agentdebug.gui.output',
}

__all__ = sorted(_LAZY_ATTRS)


def __getattr__(name: str):
    module_name = _LAZY_ATTRS.get(name)
    if module_name is None:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    from importlib import import_module

    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY_ATTRS))
