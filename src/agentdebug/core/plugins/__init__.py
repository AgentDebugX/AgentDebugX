"""AgentDebugX plugin registry."""

from agentdebug.core.plugins.registry import (
    list_plugins,
    register_action_plugin,
    register_analysis_plugin,
    register_ingest_plugin,
    register_inspect_plugin,
    register_plugin,
)
from agentdebug.core.plugins.types import PluginProvider, PluginSpec, PluginType

# Import the staged facades once so built-in plugins register themselves.
import agentdebug.diagnose.actions  # noqa: F401,E402
import agentdebug.diagnose  # noqa: F401,E402
import agentdebug.ingest  # noqa: F401,E402
import agentdebug.inspect  # noqa: F401,E402
import agentdebug.diagnose.rules  # noqa: F401,E402

__all__ = [
    'PluginProvider',
    'PluginSpec',
    'PluginType',
    'list_plugins',
    'register_action_plugin',
    'register_analysis_plugin',
    'register_ingest_plugin',
    'register_inspect_plugin',
    'register_plugin',
]
