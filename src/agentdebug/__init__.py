"""AgentDebugX public API.

Public product flow:

* ``ingest``   - capture or normalize traces
* ``diagnose`` - produce findings and root-cause summaries
* ``inspect``  - view traces and reports
* ``act``      - attribution, recovery, and sharing

The PyPI distribution is ``agentdebugx`` while the import path remains
``agentdebug``.
"""

import sys

import agentdebug.core as _core_pkg
import agentdebug.runtime as _core_events_mod
import agentdebug.runtime as _core_llm_mod
import agentdebug.schema as _core_models_mod
import agentdebug.runtime.plugins as _core_plugins_pkg
import agentdebug.runtime as _core_storage_mod
import agentdebug.schema as _core_taxonomy_mod
import agentdebug.diagnose as _diagnose_pkg
import agentdebug.diagnose.attribute as _diagnose_attribute_pkg
import agentdebug.diagnose.attribute.attribution as _diagnose_attribution_mod
import agentdebug.diagnose.attribute.deep_memory as _diagnose_deep_memory_mod
import agentdebug.diagnose.profiles.deepdebug as _diagnose_deep_mod
import agentdebug.diagnose.detect as _diagnose_detect_pkg
import agentdebug.diagnose.detect.analyzers as _diagnose_analyzers_mod
import agentdebug.diagnose.detect.detectors as _diagnose_detectors_mod
import agentdebug.diagnose.detect.judge as _diagnose_judges_mod
import agentdebug.diagnose.pipeline as _diagnose_pipeline_mod
import agentdebug.diagnose.recover as _diagnose_recover_pkg
import agentdebug.diagnose.recover.recovery as _diagnose_recovery_mod
import agentdebug.diagnose.actions as _diagnose_actions_pkg
import agentdebug.diagnose.detect.rules as _diagnose_rules_pkg
import agentdebug.diagnose.detect.rules.agenterrorbench as _diagnose_rule_agenterrorbench_mod
import agentdebug.diagnose.detect.rules.base as _diagnose_rule_base_mod
import agentdebug.diagnose.detect.rules.core as _diagnose_rule_core_mod
import agentdebug.diagnose.detect.rules.registry as _diagnose_rule_registry_mod
import agentdebug.ingest as _ingest_pkg
import agentdebug.ingest.adapters as _ingest_adapters_pkg
import agentdebug.ingest.adapters.base as _ingest_adapter_base_mod
import agentdebug.ingest.adapters.crewai as _ingest_adapter_crewai_mod
import agentdebug.ingest.adapters.importers as _ingest_adapter_importers_mod
import agentdebug.ingest.adapters.langgraph as _ingest_adapter_langgraph_mod
import agentdebug.ingest.adapters.openai_agents as _ingest_adapter_openai_agents_mod
import agentdebug.ingest.adapters.otel as _ingest_adapter_otel_mod
import agentdebug.ingest.adapters.raw as _ingest_adapter_raw_mod
import agentdebug.ingest.recorder as _ingest_recorder_mod
import agentdebug.ingest.instrumentation as _ingest_instrumentation_mod
import agentdebug.inspect as _inspect_pkg
import agentdebug.inspect.traceback as _inspect_traceback_mod
import agentdebug.inspect.ui as _inspect_ui_pkg
import agentdebug.inspect.ui.server as _inspect_ui_server_mod
import agentdebug.hub as _hub_pkg
import agentdebug.hub.backend_base as _hub_backend_base_mod
import agentdebug.hub.backends as _hub_backends_mod
import agentdebug.hub.bundle as _hub_bundle_mod
import agentdebug.hub.scrub as _hub_scrub_mod
import agentdebug.integrations as _integrations_pkg
import agentdebug.integrations.claude_skill as _claude_skill_mod
import agentdebug.integrations.debug_skill as _debug_skill_mod
import agentdebug.integrations.openhands as _openhands_mod
import agentdebug.rerun as _rerun_pkg
import agentdebug.runtime as _runtime_pkg
import agentdebug.schema as _schema_pkg

from agentdebug.diagnose.actions import (
    AllAtOnceAttributor,
    ReferenceAttributor,
    AttributionBudget,
    AttributionResult,
    Attributor,
    AutoManualRules,
    BinarySearchAttributor,
    Blame,
    Bundle,
    BundleManifest,
    CompensationSpec,
    Compensator,
    CorrectedAction,
    CounterfactualAttributor,
    CriticRecoverer,
    DeepDebugRecovery,
    DEFAULT_REDACTIONS,
    DEFAULT_VERIFIERS,
    EnsembleAttributor,
    FixProposal,
    GitHubBackend,
    HeuristicAttributor,
    HubBackend,
    HuggingFaceBackend,
    LocalHubBackend,
    Recoverer,
    ReflexionSuggestion,
    SBFLAttributor,
    SagaRollback,
    ScrubReport,
    Scrubber,
    SelfRefineLoop,
    StepByStepAttributor,
    VerifierSpec,
    backend_from_spec,
    build_manifest,
    pack_bundle,
    parse_spec,
    scrub_trajectory,
    unpack_bundle,
)
from agentdebug.core import (
    AgentEvent,
    AgentTrajectory,
    Artifact,
    BusEvent,
    CompletionResult,
    TokenUsage,
    DEFAULT_BUS,
    DiagnosticAuditEntry,
    DiagnosticReport,
    EmbeddingClient,
    EventBus,
    EventSubscription,
    EventType,
    FailureFinding,
    FailureMode,
    JsonlTraceStore,
    LLMClient,
    Modality,
    OpenAICompatClient,
    SEED_FAILURE_MODES,
    SQLiteTraceStore,
    TraceStore,
    get_failure_mode,
)
from agentdebug.diagnose import (
    Detector,
    DetectorConfig,
    FailureObservation,
    HeuristicAnalyzer,
    LLMJudgeAnalyzer,
    RepeatedStateDetector,
    RepeatedToolCallDetector,
    StepCountLimitDetector,
    TaxonomyInducer,
    TaxonomyProposal,
    TopicDriftDetector,
    collect_observations,
    default_detectors,
    run_detectors,
)
from agentdebug.diagnose.pipeline import DiagnosePipeline, DiagnosePipelineResult
from agentdebug.ingest import (
    AgentDebug,
    ConversionError,
    TraceSession,
    convert_file,
    convert_payload,
    detect_payload_format,
    write_converted_trajectory,
)
from agentdebug.inspect import CascadeFrame, build_app, build_cascade, format_traceback, serve
from agentdebug.runtime.plugins import (
    PluginProvider,
    PluginSpec,
    PluginType,
    list_plugins,
    register_action_plugin,
    register_analysis_plugin,
    register_ingest_plugin,
    register_inspect_plugin,
    register_plugin,
)
from agentdebug.diagnose.detect.rules import (
    EventRule,
    RuleMatch,
    TrajectoryRule,
    available_rule_packs,
    load_event_rules,
    load_trajectory_rules,
    resolve_rule_pack_names,
)

__all__ = [
    'AgentDebug',
    'AgentEvent',
    'AgentTrajectory',
    'AllAtOnceAttributor',
    'ReferenceAttributor',
    'Artifact',
    'AttributionBudget',
    'AttributionResult',
    'Attributor',
    'AutoManualRules',
    'BinarySearchAttributor',
    'Blame',
    'Bundle',
    'BundleManifest',
    'BusEvent',
    'CascadeFrame',
    'CompensationSpec',
    'Compensator',
    'CompletionResult',
    'TokenUsage',
    'ConversionError',
    'CorrectedAction',
    'CounterfactualAttributor',
    'CriticRecoverer',
    'DeepDebugRecovery',
    'DEFAULT_BUS',
    'DEFAULT_REDACTIONS',
    'DEFAULT_VERIFIERS',
    'Detector',
    'DetectorConfig',
    'DiagnosticAuditEntry',
    'DiagnosticReport',
    'DiagnosePipeline',
    'DiagnosePipelineResult',
    'EmbeddingClient',
    'EnsembleAttributor',
    'EventBus',
    'EventRule',
    'EventSubscription',
    'EventType',
    'FailureFinding',
    'FailureMode',
    'FixProposal',
    'GitHubBackend',
    'FailureObservation',
    'HeuristicAnalyzer',
    'HeuristicAttributor',
    'HubBackend',
    'HuggingFaceBackend',
    'JsonlTraceStore',
    'LLMClient',
    'LLMJudgeAnalyzer',
    'TaxonomyInducer',
    'TaxonomyProposal',
    'collect_observations',
    'LocalHubBackend',
    'Modality',
    'OpenAICompatClient',
    'PluginProvider',
    'PluginSpec',
    'PluginType',
    'Recoverer',
    'ReflexionSuggestion',
    'RepeatedStateDetector',
    'RepeatedToolCallDetector',
    'RuleMatch',
    'SBFLAttributor',
    'SEED_FAILURE_MODES',
    'SagaRollback',
    'ScrubReport',
    'Scrubber',
    'SelfRefineLoop',
    'SQLiteTraceStore',
    'StepByStepAttributor',
    'StepCountLimitDetector',
    'TopicDriftDetector',
    'TraceSession',
    'TraceStore',
    'TrajectoryRule',
    'VerifierSpec',
    'available_rule_packs',
    'backend_from_spec',
    'build_app',
    'build_cascade',
    'build_manifest',
    'convert_file',
    'convert_payload',
    'default_detectors',
    'detect_payload_format',
    'format_traceback',
    'get_failure_mode',
    'list_plugins',
    'load_event_rules',
    'load_trajectory_rules',
    'pack_bundle',
    'parse_spec',
    'register_action_plugin',
    'register_analysis_plugin',
    'register_ingest_plugin',
    'register_inspect_plugin',
    'register_plugin',
    'resolve_rule_pack_names',
    'run_detectors',
    'scrub_trajectory',
    'serve',
    'unpack_bundle',
    'write_converted_trajectory',
]

__version__ = '0.5.1'

_COMPAT_MODULE_ALIASES = {
    'agentdebug.models': _core_models_mod,
    'agentdebug.storage': _core_storage_mod,
    'agentdebug.taxonomy': _core_taxonomy_mod,
    'agentdebug.events': _core_events_mod,
    'agentdebug.llm': _core_llm_mod,
    'agentdebug.recorder': _ingest_recorder_mod,
    'agentdebug.instrumentation': _ingest_instrumentation_mod,
    'agentdebug.analyzers': _diagnose_analyzers_mod,
    'agentdebug.detectors': _diagnose_detectors_mod,
    'agentdebug.judges': _diagnose_judges_mod,
    'agentdebug.pipeline': _diagnose_pipeline_mod,
    'agentdebug.deep': _diagnose_deep_mod,
    'agentdebug.deep_memory': _diagnose_deep_memory_mod,
    'agentdebug.attribution': _diagnose_attribution_mod,
    'agentdebug.recovery': _diagnose_recovery_mod,
    'agentdebug.traceback': _inspect_traceback_mod,
    'agentdebug.adapters': _ingest_adapters_pkg,
    'agentdebug.adapters.base': _ingest_adapter_base_mod,
    'agentdebug.adapters.crewai': _ingest_adapter_crewai_mod,
    'agentdebug.adapters.importers': _ingest_adapter_importers_mod,
    'agentdebug.adapters.langgraph': _ingest_adapter_langgraph_mod,
    'agentdebug.adapters.openai_agents': _ingest_adapter_openai_agents_mod,
    'agentdebug.adapters.otel': _ingest_adapter_otel_mod,
    'agentdebug.adapters.raw': _ingest_adapter_raw_mod,
    'agentdebug.rules': _diagnose_rules_pkg,
    'agentdebug.rules.base': _diagnose_rule_base_mod,
    'agentdebug.rules.core': _diagnose_rule_core_mod,
    'agentdebug.rules.registry': _diagnose_rule_registry_mod,
    'agentdebug.rules.agenterrorbench': _diagnose_rule_agenterrorbench_mod,
    'agentdebug.hub': _hub_pkg,
    'agentdebug.hub.backend_base': _hub_backend_base_mod,
    'agentdebug.hub.backends': _hub_backends_mod,
    'agentdebug.hub.bundle': _hub_bundle_mod,
    'agentdebug.hub.scrub': _hub_scrub_mod,
    'agentdebug.integrations': _integrations_pkg,
    'agentdebug.integrations.claude_skill': _claude_skill_mod,
    'agentdebug.integrations.debug_skill': _debug_skill_mod,
    'agentdebug.integrations.openhands': _openhands_mod,
    'agentdebug.ui': _inspect_ui_pkg,
    'agentdebug.ui.server': _inspect_ui_server_mod,
    'agentdebug.core': _core_pkg,
    'agentdebug.schema': _schema_pkg,
    'agentdebug.runtime': _runtime_pkg,
    'agentdebug.ingest': _ingest_pkg,
    'agentdebug.diagnose': _diagnose_pkg,
    'agentdebug.diagnose.detect': _diagnose_detect_pkg,
    'agentdebug.diagnose.attribute': _diagnose_attribute_pkg,
    'agentdebug.diagnose.recover': _diagnose_recover_pkg,
    'agentdebug.inspect': _inspect_pkg,
    'agentdebug.rerun': _rerun_pkg,
    'agentdebug.act': _diagnose_actions_pkg,
    'agentdebug.plugins': _core_plugins_pkg,
}

for _name, _module in _COMPAT_MODULE_ALIASES.items():
    sys.modules.setdefault(_name, _module)
