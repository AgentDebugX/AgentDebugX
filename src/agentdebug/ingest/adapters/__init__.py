"""Framework and offline import adapters.

The base Protocol and status dataclass are always available. Concrete
adapters live in sibling modules and are imported lazily by name so that
``import agentdebug.adapters`` never pulls in optional framework deps.

Runtime adapters observe live framework events. Offline import adapters convert
already-exported trace files into ``AgentTrajectory`` for post-hoc analysis.
"""

from agentdebug.core.plugins.registry import register_ingest_plugin
from agentdebug.ingest.adapters.base import AdapterStatus, FrameworkAdapter
from agentdebug.ingest.adapters.importers import (
    ConversionError,
    convert_file,
    convert_payload,
    detect_payload_format,
    write_converted_trajectory,
)

__all__ = [
    'AdapterStatus',
    'ConversionError',
    'FrameworkAdapter',
    'convert_file',
    'convert_payload',
    'detect_payload_format',
    'write_converted_trajectory',
]

register_ingest_plugin(
    'offline.importers',
    'Offline Importers',
    version='1',
    capabilities=[
        'agenttrajectory',
        'messages',
        'message_list',
        'conversations',
        'event_list',
        'webshop_pages',
        'openai_agents_spans',
        'crewai_events',
        'langgraph_callbacks',
        'openclaw',
        'claude_code',
        'hermes',
        'osworld',
        'gaia_odr',
    ],
    source_module='agentdebug.ingest.adapters.importers',
)
