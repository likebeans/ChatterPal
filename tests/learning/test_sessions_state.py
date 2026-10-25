"""Pure B2 state transitions; persistence and operations belong to R0/A2."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from chatterpal.learning.catalog import TaskCatalog
from chatterpal.learning.sessions import SessionConflict, TaskSession

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def start(task_id="weekend", session_id="session-1", **kwargs):
    return TaskSession.start(
        session_id=session_id,
        task=TaskCatalog().get_task(task_id),
        correction_mode=kwargs.pop("correction_mode", "after_session"),
        now=NOW,
        **kwargs,
    )


def append(session, text="I went hiking.", reply="Who did you go with?", **kwargs):
    return session.append_turn(
        expected_revision=session.revision,
        user_turn_id=f"user-{session.revision}",
        assistant_turn_id=f"coach-{session.revision}",
        text=text,
        reply=reply,
        input_source="text",
        now=NOW + timedelta(seconds=session.revision + 1),
        **kwargs,
    )


def test_start_pins_content_and_correction_preference_without_mutating_catalog():
    session = start(correction_mode="immediate")
    assert session.status == "active"
    assert session.revision == 0
    assert session.correction_mode == "immediate"
    assert session.turns == ()
    assert session.opening == "What did you do over the weekend?"
    snapshot = session.to_snapshot()
    snapshot["task"]["content_version"] = "2"
    snapshot["task"]["steps"][0]["question"] = "Changed in later catalog"
    assert session.task.content_version == "1"
    assert session.task.steps[0].question == session.opening


def test_round_is_a_single_immutable_revision_with_paired_messages():
    session = start()
    updated = append(session)
    assert session.revision == 0 and session.turns == ()
    assert updated.revision == 1
    assert len(updated.turns) == 1
    turn = updated.turns[0]
    assert (turn.user_turn_id, turn.assistant_turn_id) == ("user-0", "coach-0")
    assert (turn.text, turn.reply) == ("I went hiking.", "Who did you go with?")
    assert turn.source == "text"
    assert turn.audio_status == "unavailable"
    assert updated.status == "active"


@pytest.mark.parametrize("field,value", [("text", " "), ("reply", ""), ("reply", None)])
def test_partial_or_failed_round_does_not_create_user_only_history(field, value):
    session = start()
    args = {"text": "I went hiking.", "reply": "Who did you go with?"}
    args[field] = value
    with pytest.raises(ValueError):
        append(session, **args)
    assert session.revision == 0
    assert session.turns == ()
    assert session.status == "active"


def test_stale_revision_and_duplicate_turn_ids_are_rejected():
    session = append(start())
    with pytest.raises(SessionConflict):
        session.append_turn(
            expected_revision=0,
            user_turn_id="user-next",
            assistant_turn_id="coach-next",
            text="With friends.",
            reply="How was it?",
            input_source="text",
            now=NOW,
        )
    with pytest.raises(ValueError):
        session.append_turn(
            expected_revision=1,
            user_turn_id="user-0",
            assistant_turn_id="coach-new",
            text="With friends.",
            reply="How was it?",
            input_source="text",
            now=NOW,
        )
    with pytest.raises(ValueError):
        session.append_turn(
            expected_revision=1,
            user_turn_id="coach-0",
            assistant_turn_id="coach-new",
            text="With friends.",
            reply="How was it?",
            input_source="text",
            now=NOW,
        )


def test_tts_unavailable_keeps_text_and_exact_machine_transcript():
    session = start()
    updated = session.append_turn(
        expected_revision=0,
        user_turn_id="user-audio",
        assistant_turn_id="coach-audio",
        text="  I go hiking yesterday.  ",
        reply="Who went with you?",
        input_source="audio",
        audio_status="unavailable",
        now=NOW,
    )
    assert updated.turns[0].text == "  I go hiking yesterday.  "
    assert updated.turns[0].source == "audio"
    assert updated.turns[0].audio_status == "unavailable"
    assert updated.turns[0].reply == "Who went with you?"


def test_hint_use_is_recorded_and_requires_current_revision():
    session = start()
    hinted = session.use_hint(
        expected_revision=0,
        hint_id="hint-1",
        kind="sentence_starter",
        criterion_id="activity",
        now=NOW,
    )
    assert hinted.revision == 1
    assert len(hinted.hints) == 1
    assert hinted.hints[0].text == "Last weekend, I ..."
    assert hinted.hints[0].source_revision == 0
    assert hinted.turns == ()
    with pytest.raises(SessionConflict):
        hinted.use_hint(
            expected_revision=0,
            hint_id="hint-2",
            kind="simpler_question",
            criterion_id="activity",
            now=NOW,
        )


def test_finishing_with_pending_round_or_without_user_turn_is_rejected():
    with pytest.raises(SessionConflict):
        start().finish(expected_revision=0, has_pending_turn=False, now=NOW)
    session = append(start())
    with pytest.raises(SessionConflict):
        session.finish(expected_revision=1, has_pending_turn=True, now=NOW)
    assert session.status == "active"
    assert session.revision == 1


def test_finish_pins_a_snapshot_but_does_not_claim_goals_are_met():
    session = append(start())
    completed = session.finish(expected_revision=1, has_pending_turn=False, now=NOW)
    assert completed.status == "completed"
    assert completed.revision == 2
    assert completed.finished.source_revision == 1
    assert completed.finished.turn_count == 1
    assert completed.finished.condition_results == tuple(
        (criterion.criterion_id, "unverified") for criterion in completed.task.criteria
    )
    assert completed.finished.goal_status == "unverified"
    with pytest.raises(SessionConflict):
        append(completed)
    with pytest.raises(SessionConflict):
        completed.finish(expected_revision=2, has_pending_turn=False, now=NOW)
    with pytest.raises(SessionConflict):
        completed.use_hint(
            expected_revision=2,
            hint_id="hint-late",
            kind="sentence_starter",
            criterion_id="activity",
            now=NOW,
        )


def test_json_snapshot_restores_state_without_loading_current_catalog():
    session = append(start())
    session = session.use_hint(
        expected_revision=1,
        hint_id="hint-1",
        kind="simpler_question",
        criterion_id="feeling",
        now=NOW,
    ).finish(expected_revision=2, has_pending_turn=False, now=NOW)
    saved = json.loads(json.dumps(session.to_snapshot(), ensure_ascii=False))
    restored = TaskSession.from_snapshot(saved)
    assert restored == session
    assert restored.to_snapshot() == saved


def test_session_changes_do_not_affect_a_different_session():
    first = start(session_id="first")
    second = start(session_id="second")
    updated = append(first)
    assert second.to_snapshot() == start(session_id="second").to_snapshot()
    assert updated.session_id == "first"
    assert second.revision == 0


@pytest.mark.parametrize("invalid_now", [datetime(2026, 10, 24), "2026-10-24"])
def test_persistent_time_requires_timezone_aware_datetime(invalid_now):
    with pytest.raises(ValueError):
        TaskSession.start(
            session_id="session-1",
            task=TaskCatalog().get_task("weekend"),
            correction_mode="after_session",
            now=invalid_now,
        )


def test_utc_normalization_preserves_the_instant():
    offset_now = NOW.astimezone(timezone(timedelta(hours=8)))
    session = TaskSession.start(
        session_id="session-1",
        task=TaskCatalog().get_task("weekend"),
        correction_mode="after_session",
        now=offset_now,
    )
    assert session.created_at == NOW
    assert session.to_snapshot()["created_at"].endswith("Z")
