"""Package-boundary tests for the GUI / CUA surface (PKG-01, PKG-02, PKG-03).

The GUI stack is owned by ``agentdebug.gui`` rather than resolved off
``sys.path`` from a sibling checkout. These tests pin the three properties that
make it worth doing: nothing is imported off ``sys.path``, the RCA main path
imports on a core install, and ``import agentdebug`` stays cheap.

The layers behind the ``gui-memory`` and ``gui-app`` extras are exercised by
``tests/gui``, which skips them when the extra is absent.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentdebug.diagnose.gui_rca import GuiRcaAnalyzer
from agentdebug.ingest.adapters.osworld import convert_osworld_dir
from agentdebug.schema import Modality

GUI_PACKAGE = Path(__file__).resolve().parents[1] / 'src' / 'agentdebug' / 'gui'
SRC_PACKAGE = Path(__file__).resolve().parents[1] / 'src' / 'agentdebug'

# Everything the GUI surface must NOT need in order to import.
BLOCKED_MODULES = (
    'anthropic',
    'chromadb',
    'langchain_core',
    'langchain_chroma',
    'langchain_openai',
    'openai',
    'pandas',
    'sklearn',
    'streamlit',
    'together',
)


def _run_python(body: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, '-c', body],
        capture_output=True,
        text=True,
        timeout=120,
    )


# ---------------------------------------------------------------------------
# Import isolation
# ---------------------------------------------------------------------------


def test_gui_rca_imports_without_optional_dependencies():
    """``agentdebug.gui.rca`` must import on a core install.

    The blocker runs regardless of what happens to be installed in the dev
    environment, so this keeps holding once someone installs the extras.
    """
    script = f'''
import sys

BLOCKED = {BLOCKED_MODULES!r}


class _Blocker:
    def find_module(self, name, path=None):
        return self.find_spec(name, path)

    def find_spec(self, name, path=None, target=None):
        root = name.split('.')[0]
        if root in BLOCKED:
            raise ImportError('blocked by test: ' + name)
        return None


sys.meta_path.insert(0, _Blocker())

import agentdebug.gui.rca
import agentdebug.gui.agent
import agentdebug.gui.tools
import agentdebug.gui.dispatch
import agentdebug.gui.ingester
import agentdebug.gui.trajectory
import agentdebug.gui.together_adapter
import agentdebug.gui.output
import agentdebug.gui.tagger
import agentdebug.gui.utils
import agentdebug.gui.eval
import agentdebug.gui.evolving
import agentdebug.gui.pipeline
import agentdebug.gui.scripts.download_input_trajectory
import agentdebug.inspect.discussion
import agentdebug.inspect.ui.discussion_store
from agentdebug.gui import build_output, tag_from_rca, soft_tag_candidates
from agentdebug.runtime.gui_taxonomy import list_gui_failure_modes
from agentdebug.runtime.llm_channel import _load_gui_converters
from agentdebug.diagnose.gui_rca import _load_gui_rca

_load_gui_converters()
_load_gui_rca()
assert len(list_gui_failure_modes()) == 31
print('OK')
'''
    result = _run_python(script)
    assert result.returncode == 0, result.stderr
    assert 'OK' in result.stdout


def test_heavy_gui_layers_fail_with_an_actionable_message():
    """The optional layers must name the extra to install, not just explode.

    ``agentdebug.gui.tools.__getattr__`` in particular must not recurse: it is
    reached through ``from .tools import lesson_explorer``, which probes the
    package with ``hasattr`` before falling back to the import system.
    """
    script = f'''
import sys

BLOCKED = {BLOCKED_MODULES!r}


class _Blocker:
    def find_module(self, name, path=None):
        return self.find_spec(name, path)

    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in BLOCKED:
            raise ImportError('blocked by test: ' + name)
        return None


sys.meta_path.insert(0, _Blocker())

from agentdebug.gui.dispatch import _load_lesson_explorer

try:
    _load_lesson_explorer()
except RecursionError:
    raise AssertionError('lazy tools lookup recursed')
except ImportError as exc:
    assert 'gui-memory' in str(exc), exc
else:
    raise AssertionError('expected ImportError with the extras blocked')
print('OK')
'''
    result = _run_python(script)
    assert result.returncode == 0, result.stderr
    assert 'OK' in result.stdout


def test_importing_agentdebug_does_not_import_gui():
    """Core import isolation: ``import agentdebug`` must stay off the GUI path."""
    script = (
        'import sys, agentdebug\n'
        "leaked = sorted(m for m in sys.modules if m.startswith('agentdebug.gui'))\n"
        "assert not leaked, leaked\n"
        "print('OK')\n"
    )
    result = _run_python(script)
    assert result.returncode == 0, result.stderr
    assert 'OK' in result.stdout


def test_gui_rule_pack_does_not_eagerly_import_gui_taxonomy():
    script = (
        'import sys\n'
        'from agentdebug.diagnose.detect.rules.packs import gui as pack\n'
        "assert 'agentdebug.gui.taxonomy' not in sys.modules\n"
        'from agentdebug.diagnose.detect.rules.packs.gui.rules import list_pack_modes\n'
        'assert len(list_pack_modes()) == 31\n'
        "print('OK')\n"
    )
    result = _run_python(script)
    assert result.returncode == 0, result.stderr
    assert 'OK' in result.stdout


def test_no_syspath_mutation_anywhere_in_the_package():
    """The whole point of PKG-01: nothing resolves imports off a checkout path."""
    offenders = []
    for path in SRC_PACKAGE.rglob('*.py'):
        source = path.read_text(encoding='utf-8')
        if 'sys.path.insert' in source or 'sys.path.append' in source:
            offenders.append(str(path.relative_to(SRC_PACKAGE)))
    assert offenders == []


def test_gui_package_does_not_import_a_top_level_debugger_module():
    """``agentdebug.gui`` owns the code outright.

    The old ``debugger.*`` tree is gone, so any such import would only resolve
    off a stray checkout on ``sys.path`` - the exact failure mode the migration
    removed.
    """
    offenders = []
    for path in GUI_PACKAGE.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module] if node.module and not node.level else []
            else:
                continue
            for name in names:
                if name == 'debugger' or name.startswith('debugger.'):
                    rel = path.relative_to(GUI_PACKAGE)
                    offenders.append(f'{rel}:{node.lineno} -> {name}')
    assert offenders == []


def test_gui_runtime_paths_stay_outside_the_installed_package():
    """Defaults must be writable from a wheel, so never inside site-packages."""
    import agentdebug.gui as gui_pkg
    from agentdebug.gui import config
    from agentdebug.gui.memory import episode_memory

    package_root = Path(gui_pkg.__file__).resolve().parent
    candidates = [episode_memory._DATA_FILE, config._USER_CONFIG]
    resolved = config.resolve_config_file()
    if resolved is not None:
        candidates.append(resolved)

    for candidate in candidates:
        assert package_root not in Path(candidate).resolve().parents, candidate


def test_packaged_example_config_matches_the_builtin_defaults():
    """The shipped example is documentation for ``_DEFAULTS``; keep them in sync."""
    from agentdebug.gui import config

    example_path = Path(config.__file__).resolve().parent / 'config' / 'debugger.example.json'
    example = json.loads(example_path.read_text(encoding='utf-8'))

    unknown = sorted(set(example) - set(config._DEFAULTS))
    assert unknown == [], f'example config has keys the loader ignores: {unknown}'
    mismatched = {
        key: (value, config._DEFAULTS[key])
        for key, value in example.items()
        if config._DEFAULTS[key] != value
    }
    assert mismatched == {}


# ---------------------------------------------------------------------------
# Python 3.9 compatibility of the moved code
# ---------------------------------------------------------------------------


def _pep604_annotations(tree: ast.AST) -> list[int]:
    """Return line numbers of ``X | Y`` unions used in annotation position.

    ``pyproject.toml`` declares ``python = ">=3.9"``, and pydantic resolves
    field annotations at class-creation time, so PEP 604 unions would raise
    ``TypeError`` on 3.9 even with ``from __future__ import annotations``.
    """
    hits: list[int] = []

    def walk_annotation(node: ast.AST) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
                hits.append(sub.lineno)

    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and node.annotation is not None:
            walk_annotation(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                walk_annotation(node.returns)
            args = node.args
            all_args = list(args.args) + list(args.kwonlyargs) + list(args.posonlyargs)
            for extra in (args.vararg, args.kwarg):
                if extra is not None:
                    all_args.append(extra)
            for arg in all_args:
                if arg.annotation is not None:
                    walk_annotation(arg.annotation)
    return hits


@pytest.mark.parametrize(
    'path',
    sorted(GUI_PACKAGE.rglob('*.py')),
    ids=lambda p: p.relative_to(GUI_PACKAGE).as_posix(),
)
def test_gui_package_is_python39_compatible(path: Path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    assert _pep604_annotations(tree) == []


@pytest.mark.parametrize(
    'path',
    sorted(GUI_PACKAGE.rglob('*.py')),
    ids=lambda p: p.relative_to(GUI_PACKAGE).as_posix(),
)
def test_gui_package_compiles_on_the_lowest_supported_python(path: Path):
    """Catch grammar-level 3.10+ syntax, which the union scan above cannot see.

    ``X | Y`` is a plain ``BinOp`` and parses on every version, so it needs the
    dedicated scan. Newer *grammar* — match statements, parenthesized context
    managers, ``except*`` — is rejected by ``feature_version`` instead.
    """
    source = path.read_text(encoding='utf-8')
    ast.parse(source, filename=str(path), feature_version=(3, 9))


# ---------------------------------------------------------------------------
# OSWorld ingest
# ---------------------------------------------------------------------------


@pytest.fixture
def osworld_dir(tmp_path: Path) -> Path:
    traj_dir = tmp_path / 'chrome' / 'task-0001'
    traj_dir.mkdir(parents=True)
    (traj_dir / 'screenshot_1.png').write_bytes(b'\x89PNG\r\n\x1a\n')
    lines = [
        {
            'step_num': 1,
            'action': {
                'action_type': 'tool_use',
                'command': 'click(100, 200)',
                'input': {'x': 100, 'y': 200},
            },
            'reasoning': 'click the toolbar button',
            'screenshot_file': 'screenshot_1.png',
            'reward': 0,
            'done': False,
            'info': {},
        },
        {
            'step_num': 2,
            'action': {'action_type': 'tool_use', 'command': 'type("hello")'},
            'reasoning': 'type the query',
            'reward': 0,
            'done': False,
            'info': {'error': 'element not found'},
        },
    ]
    with open(traj_dir / 'traj.jsonl', 'w', encoding='utf-8') as handle:
        for line in lines:
            handle.write(json.dumps(line) + '\n')
    (traj_dir / 'result.txt').write_text('0.0', encoding='utf-8')
    return traj_dir


def test_convert_osworld_dir_builds_ir(osworld_dir: Path):
    traj = convert_osworld_dir(osworld_dir)

    assert traj.framework == 'osworld'
    assert traj.metadata['source_format'] == 'osworld'
    assert len(traj.events) == 2
    assert [e.step_index for e in traj.events] == [1, 2]
    assert traj.events[1].error == 'element not found'


def test_convert_osworld_dir_records_resolved_source_dir(osworld_dir: Path):
    """``gui_rca`` reconstructs the on-disk root from this, so it must be absolute."""
    traj = convert_osworld_dir(osworld_dir)

    source_dir = Path(traj.metadata['source_dir'])
    assert source_dir.is_absolute()
    assert source_dir == osworld_dir.resolve()


def test_convert_osworld_dir_attaches_screenshots(osworld_dir: Path):
    traj = convert_osworld_dir(osworld_dir)

    artifacts = traj.events[0].artifacts
    assert len(artifacts) == 1
    assert artifacts[0].modality == Modality.IMAGE
    assert artifacts[0].media_type == 'image/png'
    assert artifacts[0].metadata['visual_role'] == 'after'
    assert traj.events[1].artifacts == []


def test_convert_osworld_dir_rejects_missing_directory(tmp_path: Path):
    from agentdebug.ingest.adapters.importers import ConversionError

    with pytest.raises(ConversionError):
        convert_osworld_dir(tmp_path / 'does-not-exist')


# ---------------------------------------------------------------------------
# RCAResult -> DiagnosticReport mapping
# ---------------------------------------------------------------------------


class _FakeStepSummary:
    def __init__(self, step_num: int) -> None:
        self.step_num = step_num

    def model_dump(self, mode: str = 'python') -> dict:
        return {'step_num': self.step_num, 'summary_source': 'debugger_inspected'}


class _FakeRCAResult:
    def __init__(self, taxonomy_tag: str = 'G2') -> None:
        self.root_error_step = 2
        self.taxonomy_tag = taxonomy_tag
        self.evidence = 'clicked the wrong toolbar icon'
        self.correction = 'click the Save icon instead'
        self.confidence = 0.82
        self.thinking_trace = ['considered step 1', 'considered step 2']
        self.per_step_summaries = [_FakeStepSummary(1), _FakeStepSummary(2)]


def test_map_result_produces_taxonomy_tagged_finding(osworld_dir: Path):
    traj = convert_osworld_dir(osworld_dir)
    analyzer = GuiRcaAnalyzer(channel=object(), model='test-model')

    report = analyzer._map_result(_FakeRCAResult(), traj)

    assert report.trace_id == traj.trace_id
    assert report.root_cause_step_index == 2
    assert report.root_cause_event_id == traj.events[1].event_id
    assert report.root_cause_agent == 'agent'

    (finding,) = report.findings
    assert finding.failure_mode.mode_id == 'gui.g2'
    assert finding.confidence == 0.82
    assert finding.evidence == ['clicked the wrong toolbar icon']
    assert finding.suggestion == 'click the Save icon instead'
    assert finding.metadata['taxonomy_tag'] == 'G2'
    assert finding.metadata['source'] == 'gui_rca'
    assert 'unmapped_taxonomy_tag' not in finding.metadata


def test_map_result_serializes_step_summaries(osworld_dir: Path):
    traj = convert_osworld_dir(osworld_dir)
    analyzer = GuiRcaAnalyzer(channel=object(), model='test-model')

    report = analyzer._map_result(_FakeRCAResult(), traj)

    summaries = report.metadata['per_step_summaries']
    assert summaries == [
        {'step_num': 1, 'summary_source': 'debugger_inspected'},
        {'step_num': 2, 'summary_source': 'debugger_inspected'},
    ]
    assert report.metadata['analyzer'] == 'gui_rca'
    assert report.metadata['model'] == 'test-model'
    assert len(report.metadata['thinking_trace']) == 2


def test_map_result_falls_back_for_unknown_taxonomy_tag(osworld_dir: Path):
    traj = convert_osworld_dir(osworld_dir)
    analyzer = GuiRcaAnalyzer(channel=object(), model='test-model')

    report = analyzer._map_result(_FakeRCAResult(taxonomy_tag='ZZ9'), traj)

    (finding,) = report.findings
    assert finding.failure_mode.mode_id == 'gui.unknown'
    assert finding.metadata['unmapped_taxonomy_tag'] == 'ZZ9'


def test_resolve_osworld_root_prefers_recorded_source_dir(osworld_dir: Path):
    traj = convert_osworld_dir(osworld_dir)
    analyzer = GuiRcaAnalyzer(channel=object(), model='test-model')

    assert analyzer._resolve_osworld_root(traj) == osworld_dir.resolve()


def test_resolve_osworld_root_falls_back_to_screenshot_parent(osworld_dir: Path):
    traj = convert_osworld_dir(osworld_dir)
    traj.metadata.pop('source_dir')
    analyzer = GuiRcaAnalyzer(channel=object(), model='test-model')

    assert analyzer._resolve_osworld_root(traj) == osworld_dir.resolve()


def test_resolve_osworld_root_errors_without_any_evidence(osworld_dir: Path):
    traj = convert_osworld_dir(osworld_dir)
    traj.metadata.pop('source_dir')
    for event in traj.events:
        event.artifacts.clear()
    analyzer = GuiRcaAnalyzer(channel=object(), model='test-model')

    with pytest.raises(ValueError, match='cannot resolve osworld_root'):
        analyzer._resolve_osworld_root(traj)
