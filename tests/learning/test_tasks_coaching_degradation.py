"""Malformed optional advice must not destroy an otherwise usable coach reply."""

import json
from datetime import datetime, timezone

import pytest

from chatterpal.learning.catalog import TaskCatalog
from chatterpal.learning.coaching import build_coach_messages, parse_coach_response
from chatterpal.learning.sessions import TaskSession


def start(mode="immediate", task_id="weekend"):
    return TaskSession.start(
        session_id="session-1",
        task=TaskCatalog().get_task(task_id),
        correction_mode=mode,
        now=datetime(2026, 10, 24, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("mode", ["immediate", "after_session"])
@pytest.mark.parametrize("suggestions", [None, {}, "not-an-array", 123, [None]])
def test_invalid_advice_container_preserves_a_valid_reply(mode, suggestions):
    response = parse_coach_response(
        start(mode),
        "I go hiking.",
        json.dumps(
            {
                "reply": "Who did you go with?",
                "suggestions": suggestions,
            }
        ),
    )
    assert response.reply == "Who did you go with?"
    assert response.suggestions == ()
    assert response.correction_status == (
        "unavailable" if mode == "immediate" else "deferred"
    )


@pytest.mark.parametrize("mode", ["immediate", "after_session"])
def test_missing_optional_advice_preserves_a_valid_reply(mode):
    response = parse_coach_response(
        start(mode),
        "I go hiking.",
        '{"reply":"Who did you go with?"}',
    )
    assert response.reply == "Who did you go with?"
    assert response.correction_status == (
        "unavailable" if mode == "immediate" else "deferred"
    )


@pytest.mark.parametrize("task_id", ["restaurant-order", "hotel-check-in"])
def test_prompt_contains_a_rule_for_unauthored_scenario_facts(task_id):
    instruction = build_coach_messages(start(task_id=task_id), "Can you help?")[0][
        "content"
    ]
    assert "If a fact is missing" in instruction
    assert "do not invent" in instruction


def test_restaurant_role_contains_facts_for_the_examples_questions():
    role = TaskCatalog().get_task("restaurant-order").coach_role
    assert "洋葱可以去掉" in role
    assert "vegetable pasta 默认加奶酪" in role
    assert "tomato soup 含奶油" in role
    assert "需要向厨房核实" in role


def test_hotel_role_contains_concrete_fictional_wifi_information():
    role = TaskCatalog().get_task("hotel-check-in").coach_role
    assert "PracticeGuest" in role
    assert "无密码" in role
    assert "虚构" in role


def test_weekend_transfer_question_keeps_the_pinned_weekend_time_scope():
    task = TaskCatalog().get_task("weekend")
    assert all(
        "weekend" in question.prompt.lower() for question in task.transfer_questions
    )
