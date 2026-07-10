import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from debugger.memory.distill import Lesson, distill_lesson


def test_lesson_dataclass_round_trip():
    lesson = Lesson(
        title="Toggle requires wait",
        distilled_lesson="In Chrome settings, after clicking a toggle, wait for animation.",
        trigger_condition="Chrome settings page; clicking toggle without wait",
        taxonomy_tag="S2",
        app_id="chrome",
        failed_action="click(toggle); get_state(toggle)",
        corrected_action="click(toggle); wait_until(state_changed); get_state(toggle)",
        distinguishing_feature="Toggle mid-animation; DOM returns stale value",
        confusion_set=["G2", "G1"],
        evidence="Step 4 screenshot shows toggle mid-animation",
        episodic_refs=["ep-1"],
    )
    # ``lesson_to_dict`` was replaced by the instance method ``Lesson.to_dict()``
    # when the Lesson model migrated to Pydantic.
    d = lesson.to_dict()
    assert d["taxonomy_tag"] == "S2"
    assert d["confusion_set"] == ["G2", "G1"]
    assert d["episodic_refs"] == ["ep-1"]
    assert "created_at" in d


def _make_client(text: str):
    block = SimpleNamespace(type="text", text=text)
    response = SimpleNamespace(content=[block], stop_reason="end_turn")
    client = MagicMock()
    client.messages.create.return_value = response
    return client


def test_distill_lesson_parses_json_response():
    ec = {
        "error_step": 4,
        "window": [3, 5],
        "steps": [
            {"step_num": 3, "action_code": "click(toggle)", "reasoning": "toggle the setting"},
            {"step_num": 4, "action_code": "get_state(toggle)", "reasoning": "verify"},
            {"step_num": 5, "action_code": "scroll(down)", "reasoning": "look elsewhere"},
        ],
    }
    rca = {
        "root_error_step": 4,
        "taxonomy_tag": "S2",
        "evidence": "toggle mid-animation",
        "correction": "wait for animation",
        "confidence": 0.8,
    }
    intention = "What it did: read toggle state immediately. What it changed: stale read."

    payload = {
        "title": "Wait for toggle animation",
        "distilled_lesson": "In Chrome settings, when the agent reads toggle state immediately after clicking, it should wait for the animation first.",
        "trigger_condition": "Chrome settings; toggle just clicked",
        "failed_action": "click(toggle); get_state(toggle)",
        "corrected_action": "click(toggle); wait_until(state_changed); get_state(toggle)",
        "distinguishing_feature": "Screenshot shows toggle mid-animation; DOM returns stale value",
        "confusion_set": ["G2", "G1"],
        "evidence": "Step 4 screenshot shows mid-animation frame",
        "taxonomy_tag": "S2",
    }
    client = _make_client(json.dumps(payload))

    lesson = distill_lesson(
        ec_t=ec,
        rca=rca,
        intention=intention,
        client=client,
        model="fake-model",
        app_id="chrome",
        episodic_ref="ep-uuid-1",
    )

    assert lesson.taxonomy_tag == "S2"
    assert lesson.confusion_set == ["G2", "G1"]
    assert lesson.app_id == "chrome"
    assert lesson.episodic_refs == ["ep-uuid-1"]
    assert "wait" in lesson.corrected_action.lower()


def test_distill_lesson_handles_fenced_json():
    """Some models wrap JSON in ```json ... ``` even when told not to."""
    payload = {
        "title": "x", "distilled_lesson": "x", "trigger_condition": "x",
        "failed_action": "x", "corrected_action": "x",
        "distinguishing_feature": "x", "confusion_set": [], "evidence": "x",
        "taxonomy_tag": "P1",
    }
    fenced = "```json\n" + json.dumps(payload) + "\n```"
    client = _make_client(fenced)
    lesson = distill_lesson(
        ec_t={"error_step": 0, "window": [0, 0], "steps": []},
        rca={"root_error_step": 0, "taxonomy_tag": "P1", "evidence": "", "correction": "", "confidence": 0.5},
        intention="",
        client=client,
        model="fake-model",
    )
    assert lesson.taxonomy_tag == "P1"
