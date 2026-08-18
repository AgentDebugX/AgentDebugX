import json
from pathlib import Path

import pytest

from examples.terminal_bench_eval.resume_experiment import (
    SEED_ELIGIBLE_FAILURE,
    SEED_EXCLUSION,
    SEED_RESOLVED,
    SeedConfigMismatch,
    TaskSpec,
    build_resume_run_args,
    build_seed_run_args,
    check_seed_config_matches,
    classify_seed_result,
    diagnostic_input_name,
    parse_tasks_config,
    read_tasks_file,
    rerun_notice_text,
)
from examples.terminal_bench_eval.session_selection import DiagnosticInput


def test_classify_seed_result_resolved() -> None:
    record = {'reward': 1.0, 'resolved': True, 'errored': False}
    assert classify_seed_result(record) == SEED_RESOLVED


def test_classify_seed_result_eligible_failure() -> None:
    record = {'reward': 0.0, 'resolved': False, 'errored': False}
    assert classify_seed_result(record) == SEED_ELIGIBLE_FAILURE


def test_classify_seed_result_excludes_an_errored_trial_even_with_reward_zero() -> None:
    record = {'reward': 0.0, 'resolved': False, 'errored': True}
    assert classify_seed_result(record) == SEED_EXCLUSION


def test_classify_seed_result_excludes_a_missing_reward() -> None:
    record = {'reward': None, 'resolved': False, 'errored': False}
    assert classify_seed_result(record) == SEED_EXCLUSION


def test_rerun_notice_carries_no_diagnosis_content() -> None:
    text = rerun_notice_text()
    assert 'root cause' not in text.lower()
    assert 'did not pass the hidden verifier' in text
    assert 'authorized' in text.lower()


def test_diagnostic_input_name_strips_prefix_and_slashes() -> None:
    assert diagnostic_input_name('terminal-bench/sqlite-db-truncate') == 'sqlite-db-truncate'
    assert diagnostic_input_name('a/b/c') == 'a_b_c'


def test_read_tasks_file_adds_prefix_and_skips_blanks_and_comments(tmp_path: Path) -> None:
    tasks_file = tmp_path / 'tasks.txt'
    tasks_file.write_text(
        'sqlite-db-truncate\n'
        '\n'
        '# a comment\n'
        'terminal-bench/raman-fitting\n'
    )

    specs = read_tasks_file(tasks_file)

    assert specs == [
        TaskSpec(task='terminal-bench/sqlite-db-truncate'),
        TaskSpec(task='terminal-bench/raman-fitting'),
    ]


def test_parse_tasks_config_supports_bare_names_and_seed_reuse(tmp_path: Path) -> None:
    config = tmp_path / 'tasks.yaml'
    config.write_text(
        'tasks:\n'
        '  - sqlite-db-truncate\n'
        '  - task: terminal-bench/raman-fitting\n'
        '    seed_trial_dir: /jobs/2026/raman-fitting__abc\n'
    )

    specs = parse_tasks_config(config)

    assert specs == [
        TaskSpec(task='terminal-bench/sqlite-db-truncate'),
        TaskSpec(task='terminal-bench/raman-fitting', seed_trial_dir='/jobs/2026/raman-fitting__abc'),
    ]


def test_build_seed_run_args_runs_one_task_at_a_time() -> None:
    args = build_seed_run_args('terminal-bench/raman-fitting', jobs_dir='jobs', sif_cache_dir='sif')

    assert args[:3] == ['run', '--arm', 'seed']
    assert args[args.index('--task') + 1] == 'terminal-bench/raman-fitting'
    assert args[args.index('--n-concurrent') + 1] == '1'
    assert '--model' not in args


def test_build_seed_run_args_passes_through_model_effort_and_installer() -> None:
    args = build_seed_run_args(
        'terminal-bench/raman-fitting',
        jobs_dir='jobs', sif_cache_dir='sif',
        model='anthropic/claude-sonnet-5', effort='medium',
        claude_installer_config='examples/terminal_bench_eval/claude_installer.yaml',
    )

    assert args[args.index('--model') + 1] == 'anthropic/claude-sonnet-5'
    assert args[args.index('--effort') + 1] == 'medium'
    assert args[args.index('--claude-installer-config') + 1] == (
        'examples/terminal_bench_eval/claude_installer.yaml'
    )


def _diagnostic_input(tmp_path: Path) -> DiagnosticInput:
    return DiagnosticInput(
        path=tmp_path / 'seed.jsonl',
        source_path=tmp_path / 'source.jsonl',
        sha256='a' * 64,
        record_count=3,
    )


def test_build_resume_run_args_loads_the_diagnostic_input_and_treatment(tmp_path: Path) -> None:
    diagnostic_input = _diagnostic_input(tmp_path)
    instruction_path = tmp_path / 'r0-notice.md'

    args = build_resume_run_args(
        method='rerun',
        task='terminal-bench/sqlite-db-truncate',
        diagnostic_input=diagnostic_input,
        instruction_path=instruction_path,
        jobs_dir='jobs',
        sif_cache_dir='sif',
    )

    assert args[:3] == ['run', '--arm', 'rerun']
    assert args[args.index('--load-trajectory') + 1] == str(diagnostic_input.path)
    assert args[args.index('--extra-instruction-path') + 1] == str(instruction_path)
    assert args.count('--n-concurrent') == 1
    assert args[args.index('--n-concurrent') + 1] == '1'


def test_build_resume_run_args_passes_through_model_effort_and_installer(tmp_path: Path) -> None:
    diagnostic_input = _diagnostic_input(tmp_path)
    instruction_path = tmp_path / 'advice.md'

    args = build_resume_run_args(
        method='rerun-deep',
        task='terminal-bench/sqlite-db-truncate',
        diagnostic_input=diagnostic_input,
        instruction_path=instruction_path,
        jobs_dir='jobs',
        sif_cache_dir='sif',
        model='anthropic/claude-sonnet-5',
        effort='medium',
        claude_installer_config='examples/terminal_bench_eval/claude_installer.yaml',
    )

    assert args[args.index('--model') + 1] == 'anthropic/claude-sonnet-5'
    assert args[args.index('--effort') + 1] == 'medium'
    assert args[args.index('--claude-installer-config') + 1] == (
        'examples/terminal_bench_eval/claude_installer.yaml'
    )


def test_build_resume_run_args_omits_optional_flags_when_unset(tmp_path: Path) -> None:
    diagnostic_input = _diagnostic_input(tmp_path)
    instruction_path = tmp_path / 'advice.md'

    args = build_resume_run_args(
        method='rerun',
        task='terminal-bench/sqlite-db-truncate',
        diagnostic_input=diagnostic_input,
        instruction_path=instruction_path,
        jobs_dir='jobs',
        sif_cache_dir='sif',
    )

    assert '--model' not in args
    assert '--effort' not in args
    assert '--claude-installer-config' not in args


def _write_config_json(trial_dir: Path, *, model: str, effort: str, sha256: str) -> None:
    trial_dir.mkdir(parents=True, exist_ok=True)
    (trial_dir / 'config.json').write_text(json.dumps({
        'agent': {
            'model_name': model,
            'kwargs': {'reasoning_effort': effort, 'claude_artifact_sha256': sha256},
        },
    }))


def test_check_seed_config_matches_accepts_an_identical_config(tmp_path: Path) -> None:
    trial_dir = tmp_path / 'trial'
    _write_config_json(trial_dir, model='anthropic/claude-sonnet-5', effort='medium', sha256='a' * 64)

    check_seed_config_matches(
        trial_dir, model='anthropic/claude-sonnet-5', effort='medium',
        claude_installer_config=None,
    )  # must not raise


def test_check_seed_config_matches_rejects_a_different_model(tmp_path: Path) -> None:
    trial_dir = tmp_path / 'trial'
    _write_config_json(trial_dir, model='anthropic/claude-haiku-4-5', effort='medium', sha256='a' * 64)

    with pytest.raises(SeedConfigMismatch, match='model'):
        check_seed_config_matches(
            trial_dir, model='anthropic/claude-sonnet-5', effort='medium',
            claude_installer_config=None,
        )


def test_check_seed_config_matches_rejects_a_different_effort(tmp_path: Path) -> None:
    trial_dir = tmp_path / 'trial'
    _write_config_json(trial_dir, model='anthropic/claude-sonnet-5', effort='low', sha256='a' * 64)

    with pytest.raises(SeedConfigMismatch, match='effort'):
        check_seed_config_matches(
            trial_dir, model='anthropic/claude-sonnet-5', effort='medium',
            claude_installer_config=None,
        )
