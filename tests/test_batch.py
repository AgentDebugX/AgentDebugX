from __future__ import annotations

import json

from agentdebug.batch import expand_batch_input, run_batch_diagnose, run_batch_ingest
from agentdebug.diagnose.detect import HeuristicAnalyzer


def _messages(content: str) -> dict:
    return {'messages': [{'role': 'user', 'content': content}]}


def test_expand_batch_directory_recursively_reads_json_files(tmp_path) -> None:
    source = tmp_path / 'input'
    nested = source / 'nested'
    nested.mkdir(parents=True)
    (source / 'one.json').write_text(json.dumps(_messages('one')), encoding='utf-8')
    (nested / 'two.json').write_text(json.dumps(_messages('two')), encoding='utf-8')
    (source / 'ignored.txt').write_text('{}', encoding='utf-8')

    records = expand_batch_input(source)

    assert [record.record_id for record in records] == ['nested__two', 'one']


def test_batch_ingest_treats_each_jsonl_line_as_independent(tmp_path) -> None:
    source = tmp_path / 'input.jsonl'
    source.write_text(
        '\n'.join(
            [
                json.dumps(_messages('first')),
                '{invalid-json}',
                json.dumps(_messages('third')),
            ]
        ),
        encoding='utf-8',
    )
    output = tmp_path / 'output'

    summary = run_batch_ingest(source, output)

    assert summary.total == 3
    assert summary.succeeded == 2
    assert summary.failed == 1
    assert summary.items[1].line_number == 2
    assert summary.items[1].status == 'failed'
    assert len(list(output.glob('*.trajectory.json'))) == 2
    saved = json.loads((output / 'batch-summary.json').read_text(encoding='utf-8'))
    assert saved['failed'] == 1


def test_batch_diagnose_writes_independent_trajectories_and_reports(tmp_path) -> None:
    source = tmp_path / 'input'
    source.mkdir()
    (source / 'one.json').write_text(json.dumps(_messages('one')), encoding='utf-8')
    (source / 'bad.json').write_text('{bad}', encoding='utf-8')
    output = tmp_path / 'output'

    summary = run_batch_diagnose(
        source,
        output,
        HeuristicAnalyzer().analyze,
    )

    assert summary.total == 2
    assert summary.succeeded == 1
    assert summary.failed == 1
    item = next(item for item in summary.items if item.status == 'succeeded')
    assert item.trajectory_path is not None
    assert item.report_path is not None
    assert (output / 'trajectories' / 'one.trajectory.json').exists()
    assert (output / 'reports' / 'one.report.json').exists()
