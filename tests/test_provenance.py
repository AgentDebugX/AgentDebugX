from __future__ import annotations

import json
import subprocess
from pathlib import Path

import agentdebug.provenance as provenance_module
from agentdebug import __version__
from agentdebug.provenance import provenance


def test_provenance_reports_version_and_is_json() -> None:
    info = provenance()
    json.dumps(info)
    assert info['package'] == 'agentdebugx'
    assert info['version'] == __version__
    assert set(info) == {'package', 'version', 'git_sha', 'git_dirty', 'python', 'platform'}


def test_git_fields_track_a_checkout(tmp_path: Path) -> None:
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    pkg = tmp_path / 'pkg'
    pkg.mkdir()
    (pkg / 'x.py').write_text('x = 1\n')
    env = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't',
           'GIT_COMMITTER_EMAIL': 't@t'}
    subprocess.run(['git', '-C', str(tmp_path), 'add', '-A'], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'commit', '-q', '-m', 'init'], check=True, env=env)
    clean = provenance(pkg)
    assert clean['git_sha'] and len(clean['git_sha']) == 40
    assert clean['git_dirty'] is False
    (pkg / 'x.py').write_text('x = 2\n')
    assert provenance(pkg)['git_dirty'] is True


def test_outside_a_checkout_the_git_fields_are_none(tmp_path: Path) -> None:
    info = provenance(tmp_path)
    assert info['git_sha'] is None and info['git_dirty'] is None


def test_module_is_exported() -> None:
    assert provenance_module.provenance is provenance
