"""
Integration test for the new ``lesson_table`` parameter on ``run_rca`` (§8).

We don't want to hit a real LLM, so this test builds a fake Anthropic-style
client that captures the ``system`` kwarg on every ``messages.create`` call
and immediately returns a ``tool_use`` block calling ``finish``.  The
assertion: the captured system prompt contains the lesson table
*between* the base RCA system prompt and the user message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List

import pytest

from debugger.ingester import IngestionResult, Step
from debugger.rca import RCA_SYSTEM_PROMPT, run_rca


# ---------------------------------------------------------------------------
# Test doubles for Anthropic-style messages.create
# ---------------------------------------------------------------------------


@dataclass
class _FakeToolUseBlock:
    """Minimal tool_use block matching what agent.py reads off the response."""

    name: str
    input: dict
    type: str = "tool_use"
    id: str = "toolu_test"


@dataclass
class _FakeAnthropicResponse:
    content: List[Any]
    stop_reason: str = "tool_use"


class _FakeMessages:
    """Captures every ``create`` call and emits a single ``finish`` tool_use."""

    def __init__(self) -> None:
        self.captured_systems: List[str] = []

    def create(self, **kwargs):
        self.captured_systems.append(kwargs.get("system", ""))
        finish_block = _FakeToolUseBlock(
            name="finish",
            input={
                "root_error_step":     1,
                "taxonomy_tag":        "P1",
                "evidence":            "evidence text",
                "correction":          "correction text",
                "confidence":          0.5,
                "per_step_summaries":  [],
            },
        )
        return _FakeAnthropicResponse(content=[finish_block])


class _FakeAnthropicClient:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


# ---------------------------------------------------------------------------
# IngestionResult factory — minimal valid record
# ---------------------------------------------------------------------------


def _build_ingestion(*, task_id: str = "task-test") -> IngestionResult:
    step = Step(
        step_num=1,
        action_code="click(...)",
        reasoning="",
        llm_tool_use="",
        error="something failed",
        reward=0.0,
        done=False,
        action_type="click",
        screenshot_path=None,
    )
    return IngestionResult(
        status="failure",
        trajectory=[step],
        terminal_step=1,
        error_msg="failure reason",
        task_id=task_id,
        instruction="do a thing",
        fmt="v2",
        is_infeasible=False,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


_TABLE_MARKER = "<!-- LESSON_TABLE_TEST_MARKER -->"
_LESSON_TABLE = (
    "<table>\n"
    f"{_TABLE_MARKER}\n"
    "<tr><th>X</th></tr>\n"
    "</table>"
)


class TestLessonTableInjection:
    def test_lesson_table_inserted_into_system_prompt(self, tmp_path: Path) -> None:
        client = _FakeAnthropicClient()
        ingestion = _build_ingestion()

        run_rca(
            ingestion,
            model="dummy-model",
            client=client,
            osworld_root=tmp_path,
            verbose=False,
            lesson_table=_LESSON_TABLE,
        )

        captured = client.messages.captured_systems[0]
        # Table content must appear in the system prompt.
        assert _TABLE_MARKER in captured
        # Table must appear *after* the base RCA system prompt header.
        rca_signature = "RCA Mode: Find the Single Root Error Step"
        assert rca_signature in captured
        assert captured.index(rca_signature) < captured.index(_TABLE_MARKER)
        # And before the trailing "initialization context" reminder.
        reminder = "*initialization context*"
        assert reminder in captured
        assert captured.index(_TABLE_MARKER) < captured.index(reminder)

    def test_no_lesson_table_leaves_system_prompt_clean(self, tmp_path: Path) -> None:
        client = _FakeAnthropicClient()
        ingestion = _build_ingestion()

        run_rca(
            ingestion,
            model="dummy-model",
            client=client,
            osworld_root=tmp_path,
            verbose=False,
            lesson_table=None,
        )

        captured = client.messages.captured_systems[0]
        assert "## Lesson Reference Table" not in captured
        assert "*initialization context*" not in captured

    def test_user_content_unchanged_when_table_passed(self, tmp_path: Path) -> None:
        """The injection lives in *system*, not user. Confirm user message
        does not accidentally carry the table."""
        client = _FakeAnthropicClient()
        ingestion = _build_ingestion()

        # Patch messages.create to also record the user content
        captured_messages = []
        original_create = client.messages.create

        def _wrapper(**kwargs):
            captured_messages.append(kwargs.get("messages"))
            return original_create(**kwargs)

        client.messages.create = _wrapper  # type: ignore[assignment]

        run_rca(
            ingestion,
            model="dummy-model",
            client=client,
            osworld_root=tmp_path,
            verbose=False,
            lesson_table=_LESSON_TABLE,
        )

        # First message is the initial user content; the table must not be in it.
        first_user_msg = captured_messages[0][0]
        user_content = first_user_msg["content"]
        if isinstance(user_content, str):
            assert _TABLE_MARKER not in user_content
        else:
            # If it's a list of blocks, none should contain the marker.
            for block in user_content:
                text = getattr(block, "text", None) or (
                    block.get("text") if isinstance(block, dict) else ""
                )
                assert _TABLE_MARKER not in (text or "")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
