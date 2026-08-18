from __future__ import annotations

import sys
import zipfile
from pathlib import Path


REQUIRED_FILES = {
    'agentdebug/__init__.py',
    'agentdebug/py.typed',
    # GUI/CUA: the RCA main path, plus one file from each optional-dependency
    # subpackage, so a packaging regression that drops a whole layer is caught.
    'agentdebug/gui/__init__.py',
    'agentdebug/gui/__main__.py',
    'agentdebug/gui/rca.py',
    'agentdebug/gui/taxonomy.py',
    'agentdebug/gui/tagger.py',
    'agentdebug/gui/output.py',
    'agentdebug/gui/tools/__init__.py',
    'agentdebug/gui/tools/lesson_explorer.py',
    'agentdebug/gui/utils/logger.py',
    'agentdebug/gui/eval/accuracy.py',
    'agentdebug/gui/memory/lesson_memory.py',
    'agentdebug/gui/pipeline/orchestrator.py',
    'agentdebug/gui/evolving/runner.py',
    'agentdebug/gui/vis/debugger_app.py',
    'agentdebug/gui/scripts/download_input_trajectory.py',
    'agentdebug/gui/config/debugger.example.json',
    'agentdebug/diagnose/component_manifests/detect/heuristic.json',
    'agentdebug/diagnose/detect/rules/packs/core/manifest.json',
    'agentdebug/integrations/agentdebug_skill/SKILL.md',
}

FORBIDDEN_SUFFIXES = ('.env', '.pyc')


def main() -> int:
    wheel_paths = [Path(value) for value in sys.argv[1:]]
    if len(wheel_paths) != 1:
        print('expected exactly one wheel path', file=sys.stderr)
        return 2

    wheel_path = wheel_paths[0]
    with zipfile.ZipFile(wheel_path) as wheel:
        names = set(wheel.namelist())

    missing = sorted(REQUIRED_FILES - names)
    forbidden = sorted(
        name for name in names if name.endswith(FORBIDDEN_SUFFIXES)
    )
    if missing or forbidden:
        if missing:
            print(f'missing required wheel files: {missing}', file=sys.stderr)
        if forbidden:
            print(f'forbidden wheel files: {forbidden}', file=sys.stderr)
        return 1

    print(f'validated {wheel_path.name}: {len(names)} files')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
