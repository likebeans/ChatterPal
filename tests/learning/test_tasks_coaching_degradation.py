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
@pytest.mark.parametrize("field", ["corrected", "explanation"])
@pytest.mark.parametrize(
    "claim",
    [
        "你的发音错误，准确率95",
        "你的流利度不足。",
        "Your pronunciation is perfect.",
        "The phoneme /θ/ is incorrect.",
        "Your fluency is poor.",
        "Accuracy: 95",
        "Your ability is 95%.",
        "Your rating is 8/10.",
    ],
)
def test_unsupported_text_advice_preserves_a_valid_reply(mode, field, claim):
    suggestion = {
        "original": "I go hiking.",
        "corrected": "I went hiking.",
        "explanation": "Use the past tense for last weekend.",
        "category": "grammar",
    }
    suggestion[field] = claim
    response = parse_coach_response(
        start(mode),
        "I go hiking.",
        json.dumps(
            {
                "reply": "Who did you go with?",
                "suggestions": [suggestion],
            },
            ensure_ascii=False,
        ),
    )
    assert response.reply == "Who did you go with?"
    assert response.suggestions == ()
    assert response.correction_status == (
        "unavailable" if mode == "immediate" else "deferred"
    )


@pytest.mark.parametrize(
    "corrected",
    [
        "Your accent is terrible.",
        "My accent improved perfectly after the workshop.",
        "I attended an accent workshop. Your pronunciation was poor.",
    ],
)
def test_an_accent_topic_cannot_authorize_new_acoustic_judgements(corrected):
    original = "Last weekend I attended an accent workshop."
    response = parse_coach_response(
        start(),
        original,
        json.dumps(
            {
                "reply": "Who did you go with?",
                "suggestions": [
                    {
                        "original": original,
                        "corrected": corrected,
                        "explanation": "Try a different expression.",
                        "category": "clarity",
                    }
                ],
            }
        ),
    )
    assert response.reply == "Who did you go with?"
    assert response.suggestions == ()
    assert response.correction_status == "unavailable"


@pytest.mark.parametrize(
    "original,corrected,explanation",
    [
        (
            "I attended a accent workshop.",
            "I attended an accent workshop. Your accent is terrible.",
            "Use an with this noun phrase.",
        ),
        (
            "I get 10% off yesterday.",
            "I got 95% off yesterday.",
            "Use the past tense for yesterday.",
        ),
        (
            "I get 10% off yesterday.",
            "Your accuracy is 10%.",
            "Try a different expression.",
        ),
        (
            "I get 10% off yesterday.",
            "Your accuracy is 10% off the expected level.",
            "Try a different expression.",
        ),
        (
            "I ate a apple.",
            "I ate an apple.",
            "Use an before a vowel sound. Your fluency is poor.",
        ),
        (
            "I ate a apple.",
            "I ate an apple.",
            "Use an before a vowel sound that you pronounced incorrectly.",
        ),
        (
            "I get 5% off yesterday.",
            "I got .5% off yesterday.",
            "Use the past tense for yesterday.",
        ),
        (
            "I get 10% off yesterday.",
            "I got -10% off yesterday.",
            "Use the past tense for yesterday.",
        ),
        (
            "I get 100% off yesterday.",
            "I got 1,100% off yesterday.",
            "Use the past tense for yesterday.",
        ),
        (
            "I got 5% off yesterday.",
            "I got .5% off yesterday.",
            "Keep the discount.",
        ),
        (
            "I got 10% off yesterday.",
            "I got -10% off yesterday.",
            "Keep the discount.",
        ),
        (
            "I ate a apple.",
            "I ate an apple.",
            "You mispronounce words whenever you use 'an' before a vowel sound.",
        ),
        (
            "I ate a apple.",
            "I ate an apple.",
            "Use an before a vowel sound; yours was too nasal.",
        ),
    ],
)
def test_ordinary_text_context_does_not_authorize_changed_facts_or_claims(
    original, corrected, explanation
):
    response = parse_coach_response(
        start(),
        original,
        json.dumps(
            {
                "reply": "How was it?",
                "suggestions": [
                    {
                        "original": original,
                        "corrected": corrected,
                        "explanation": explanation,
                        "category": "grammar",
                    }
                ],
            }
        ),
    )
    assert response.reply == "How was it?"
    assert response.suggestions == ()
    assert response.correction_status == "unavailable"


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
