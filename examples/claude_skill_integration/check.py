#!/usr/bin/env python3
"""Evaluate a stream-json Claude Code transcript against a scenario spec.

Usage:
    python3 check.py scenarios.json <scenario_id> <transcript.stream.json>
           [--out artifacts/<id>.summary.md]

Exits 0 if all assertions pass, 1 if any fail, 2 on usage/parse error.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

GROUND_TRUTH_PHRASES = [
    r'\bthe root cause is\b', r'\bdefinitely\b', r'\bcertainly\b',
    r'\bproven\b', r'\bconfirmed root cause\b',
]


def _extract_bash_commands(lines: list[str]) -> list[str]:
    """Return all Bash command strings found in a stream-json transcript."""
    cmds: list[str] = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        _collect_bash(obj, cmds)
    return cmds


def _collect_bash(obj: object, out: list[str]) -> None:
    if isinstance(obj, list):
        for item in obj:
            _collect_bash(item, out)
        return
    if not isinstance(obj, dict):
        return
    # Direct tool_use event
    if obj.get('type') == 'tool_use' and obj.get('name') == 'Bash':
        cmd = (obj.get('input') or {}).get('command', '')
        if cmd:
            out.append(cmd)
        return
    # Nested inside content arrays (assistant messages)
    for key in ('content', 'message', 'delta'):
        if key in obj:
            _collect_bash(obj[key], out)


def _extract_final_answer(lines: list[str]) -> str:
    """Return the final answer text from the transcript.

    Prefers the 'result' string from a top-level result event (success runs).
    Falls back to the text from the last assistant message that had a text block.
    """
    # Pass 1: result event with a string result field (successful runs)
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get('type') == 'result' and isinstance(obj.get('result'), str):
            return obj['result']

    # Pass 2: last assistant message that produced text
    last_text = ''
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get('type') == 'assistant':
            parts: list[str] = []
            _collect_text(obj, parts)
            if parts:
                last_text = ' '.join(parts)
    return last_text


def _collect_text(obj: object, out: list[str]) -> None:
    if isinstance(obj, list):
        for item in obj:
            _collect_text(item, out)
        return
    if not isinstance(obj, dict):
        return
    # Final result field
    if 'result' in obj and isinstance(obj['result'], str):
        out.append(obj['result'])
        return
    if obj.get('type') == 'text' and isinstance(obj.get('text'), str):
        out.append(obj['text'])
        return
    for key in ('content', 'message', 'delta'):
        if key in obj:
            _collect_text(obj[key], out)


def _check_skill_fires(cmds: list[str], raw_lines: list[str]) -> bool:
    if any('agentdebug' in c for c in cmds):
        return True
    # Also count an explicit Skill tool invocation as the skill firing
    for line in raw_lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _has_skill_call(obj):
            return True
    return False


def _has_skill_call(obj: object) -> bool:
    if isinstance(obj, list):
        return any(_has_skill_call(x) for x in obj)
    if not isinstance(obj, dict):
        return False
    if obj.get('type') == 'tool_use' and obj.get('name') == 'Skill':
        return True
    for key in ('content', 'message', 'delta'):
        if key in obj and _has_skill_call(obj[key]):
            return True
    return False


def _check_must_run(patterns: list[str], cmds: list[str]) -> list[tuple[str, bool]]:
    results = []
    for pat in patterns:
        matched = any(re.search(pat, c) for c in cmds)
        results.append((pat, matched))
    return results


def _check_ordered(patterns: list[str], cmds: list[str]) -> tuple[bool, str]:
    """Check that each pattern matches a command that appears AFTER the previous match."""
    last_idx = -1
    for pat in patterns:
        found = next((i for i, c in enumerate(cmds) if re.search(pat, c)), None)
        if found is None or found <= last_idx:
            return False, f'pattern "{pat}" not found after index {last_idx}'
        last_idx = found
    return True, ''


def _check_must_not_run(patterns: list[str], cmds: list[str]) -> list[tuple[str, bool]]:
    results = []
    for pat in patterns:
        matched = any(re.search(pat, c) for c in cmds)
        results.append((pat, matched))  # True = bad (it ran when it shouldn't)
    return results


def _check_no_direct_deep_skip(cmds: list[str]) -> bool:
    """Fail if `deep` ran but `diagnose` or `judge` did NOT run before it."""
    deep_idx = next((i for i, c in enumerate(cmds) if re.search(r'agentdebug (act )?deep\b', c)), None)
    if deep_idx is None:
        return True  # deep didn't run at all — fine
    diag_before = any(re.search(r'agentdebug (diagnose|analyze)\b', c) for c in cmds[:deep_idx])
    judge_before = any(re.search(r'agentdebug (act )?judge\b', c) for c in cmds[:deep_idx])
    return diag_before and judge_before


def evaluate(scenario: dict, transcript_path: Path) -> tuple[list[dict], bool, list[str], str]:
    """Return (assertion_results, all_pass)."""
    content = transcript_path.read_text(encoding='utf-8').strip()
    if content.startswith('['):
        lines = [json.dumps(o) for o in json.loads(content)]
    else:
        lines = content.splitlines()
    cmds = _extract_bash_commands(lines)
    answer = _extract_final_answer(lines)

    expect = scenario.get('expect', {})
    results: list[dict] = []

    def _r(name: str, passed: bool, detail: str = '') -> None:
        results.append({'assertion': name, 'passed': passed, 'detail': detail})

    # skill_fires
    if expect.get('skill_fires'):
        ok = _check_skill_fires(cmds, lines)
        _r('skill_fires', ok, '' if ok else 'no agentdebug command or Skill invocation found in transcript')

    # must_run_regex
    for pat, ok in _check_must_run(expect.get('must_run_regex', []), cmds):
        _r(f'must_run: {pat}', ok, '' if ok else f'no command matched /{pat}/')

    # ordered
    if expect.get('ordered'):
        ok, detail = _check_ordered(expect['ordered'], cmds)
        _r('ordered', ok, detail)

    # must_not_run_regex
    for pat, ran in _check_must_not_run(expect.get('must_not_run_regex', []), cmds):
        _r(f'must_not_run: {pat}', not ran, '' if not ran else f'disallowed command matched /{pat}/')

    # no_direct_deep_skip (escalation)
    if expect.get('no_direct_deep_skip'):
        ok = _check_no_direct_deep_skip(cmds)
        _r('no_direct_deep_skip', ok,
           '' if ok else 'deep ran without prior diagnose+judge (skipped escalation steps)')

    # answer_not_contains
    if expect.get('answer_not_contains'):
        for phrase in expect['answer_not_contains']:
            found = phrase.lower() in answer.lower()
            _r(f'answer_not_contains: {phrase!r}', not found,
               '' if not found else f'forbidden phrase found in answer')

    all_pass = all(r['passed'] for r in results)
    return results, all_pass, cmds, answer


def write_summary(scenario: dict, results: list[dict], all_pass: bool,
                  cmds: list[str], answer: str, out_path: Path) -> None:
    lines = [
        f'# {scenario["id"]} — {"PASS" if all_pass else "FAIL"}',
        '',
        f'**Intent:** {scenario["intent"]}',
        f'**Prompt:** {scenario["prompt"]}',
        '',
        '## Commands Claude ran (in order)',
        '',
    ]
    if cmds:
        for i, c in enumerate(cmds, 1):
            lines.append(f'{i}. `{c[:200]}`')
    else:
        lines.append('*(no Bash commands recorded)*')

    lines += ['', '## Assertions', '']
    for r in results:
        mark = '✓' if r['passed'] else '✗'
        detail = f' — {r["detail"]}' if r['detail'] else ''
        lines.append(f'- {mark} `{r["assertion"]}`{detail}')

    # Soft ground truth for LLM scenarios
    fixture = scenario.get('fixture', {})
    gt = fixture.get('ground_truth') if isinstance(fixture, dict) else None
    if gt:
        lines += ['', '## Ground-truth anchor (soft — not hard-asserted)', '']
        for k, v in gt.items():
            lines.append(f'- **{k}:** {v}')

    lines += [
        '',
        '## Answer excerpt (first 600 chars)',
        '',
        f'```\n{answer[:600]}\n```',
    ]
    out_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    args = sys.argv[1:]
    if len(args) < 3:
        print('Usage: check.py scenarios.json <scenario_id> <transcript.stream.json> [--out path]',
              file=sys.stderr)
        return 2

    scenarios_file, scenario_id, transcript_file = args[0], args[1], args[2]
    out_flag_idx = args.index('--out') if '--out' in args else -1
    out_path = Path(args[out_flag_idx + 1]) if out_flag_idx >= 0 else None

    scenarios = json.loads(Path(scenarios_file).read_text(encoding='utf-8'))
    scenario = next((s for s in scenarios if s['id'] == scenario_id), None)
    if scenario is None:
        print(f'error: scenario {scenario_id!r} not found', file=sys.stderr)
        return 2

    transcript_path = Path(transcript_file)
    if not transcript_path.exists():
        print(f'error: transcript file not found: {transcript_file}', file=sys.stderr)
        return 2

    results, all_pass, cmds, answer = evaluate(scenario, transcript_path)

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        write_summary(scenario, results, all_pass, cmds, answer, out_path)

    # Always print one-line verdict + any failures
    status = 'PASS' if all_pass else 'FAIL'
    print(f'{status}  {scenario_id}')
    for r in results:
        if not r['passed']:
            detail = f' ({r["detail"]})' if r['detail'] else ''
            print(f'  FAIL  {r["assertion"]}{detail}')

    return 0 if all_pass else 1


if __name__ == '__main__':
    sys.exit(main())
