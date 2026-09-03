from __future__ import annotations

import json

import pytest

from agentdebug.runtime import JsonlTraceStore, SQLiteTraceStore
from agentdebug.schema import (
    AgentTrajectory,
    DiagnosticReport,
    model_to_json,
    report_from_json,
)


@pytest.mark.parametrize('store_kind', ['jsonl', 'sqlite'])
def test_trace_store_round_trip(
    tmp_path,
    failed_trajectory: AgentTrajectory,
    store_kind: str,
) -> None:
    if store_kind == 'jsonl':
        store = JsonlTraceStore(str(tmp_path / 'traces.jsonl'))
    else:
        store = SQLiteTraceStore(str(tmp_path / 'traces.sqlite'))

    store.save_trajectory(failed_trajectory)

    assert store.load_trajectory(failed_trajectory.trace_id) == failed_trajectory
    assert failed_trajectory.trace_id in store.list_traces()


def test_jsonl_store_returns_latest_duplicate(tmp_path) -> None:
    store = JsonlTraceStore(str(tmp_path / 'traces.jsonl'))
    store.save_trajectory(AgentTrajectory(trace_id='same', goal='old'))
    store.save_trajectory(AgentTrajectory(trace_id='same', goal='new'))

    loaded = store.load_trajectory('same')

    assert loaded is not None
    assert loaded.goal == 'new'
    assert store.list_traces() == ['same', 'same']


def test_sqlite_store_upserts_duplicate_trace(tmp_path) -> None:
    store = SQLiteTraceStore(str(tmp_path / 'traces.sqlite'))
    store.save_trajectory(AgentTrajectory(trace_id='same', goal='old'))
    store.save_trajectory(AgentTrajectory(trace_id='same', goal='new'))

    loaded = store.load_trajectory('same')

    assert loaded is not None
    assert loaded.goal == 'new'
    assert store.list_traces() == ['same']


def test_sqlite_store_saves_and_filters_reports(
    tmp_path,
    diagnostic_report: DiagnosticReport,
) -> None:
    store = SQLiteTraceStore(str(tmp_path / 'traces.sqlite'))
    other = report_from_json(model_to_json(diagnostic_report))
    other.report_id = 'other'
    other.trace_id = 'other-trace'
    store.save_report(diagnostic_report)
    store.save_report(other)

    reports = store.list_reports(diagnostic_report.trace_id)

    assert [report.report_id for report in reports] == ['report_test']
    assert len(store.list_reports()) == 2


@pytest.mark.parametrize('store_kind', ['jsonl', 'sqlite'])
def test_report_exact_lookup(tmp_path, diagnostic_report, store_kind) -> None:
    store = (
        JsonlTraceStore(str(tmp_path / 'traces.jsonl'))
        if store_kind == 'jsonl'
        else SQLiteTraceStore(str(tmp_path / 'traces.sqlite'))
    )
    store.save_report(diagnostic_report)
    assert store.load_report(diagnostic_report.trace_id, diagnostic_report.report_id) == diagnostic_report
    assert store.load_report(diagnostic_report.trace_id, 'missing') is None


def test_empty_stores_return_no_results(tmp_path) -> None:
    jsonl = JsonlTraceStore(str(tmp_path / 'missing.jsonl'))
    sqlite = SQLiteTraceStore(str(tmp_path / 'empty.sqlite'))

    assert jsonl.list_traces() == []
    assert jsonl.load_trajectory('missing') is None
    assert sqlite.list_traces() == []
    assert sqlite.load_trajectory('missing') is None


def test_jsonl_store_surfaces_corrupt_rows(tmp_path) -> None:
    path = tmp_path / 'traces.jsonl'
    path.write_text('{not-json}\n', encoding='utf-8')
    store = JsonlTraceStore(str(path))

    with pytest.raises(json.JSONDecodeError):
        store.list_traces()
