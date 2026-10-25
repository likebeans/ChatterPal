"""Reject snapshots that bypass invariants enforced by normal B2 transitions."""

from datetime import datetime, timezone

import pytest

from chatterpal.learning.catalog import TaskCatalog
from chatterpal.learning.sessions import TaskSession

NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)


def active_state():
    return TaskSession.start(
        session_id="session-1",
        task=TaskCatalog().get_task("weekend"),
        correction_mode="after_session",
        now=NOW,
    ).append_turn(
        expected_revision=0,
        user_turn_id="user-1",
        assistant_turn_id="coach-1",
        text="I went hiking.",
        reply="Who did you go with?",
        input_source="text",
        now=NOW,
    )


def hinted_state():
    return active_state().use_hint(
        expected_revision=1,
        hint_id="hint-1",
        kind="sentence_starter",
        criterion_id="activity",
        now=NOW,
    )


def completed_state():
    return hinted_state().finish(expected_revision=2, has_pending_turn=False, now=NOW)


def test_a_completed_snapshot_cannot_restore_as_active_with_old_finish_metadata():
    snapshot = completed_state().to_snapshot()
    snapshot["status"] = "active"
    with pytest.raises(ValueError):
        TaskSession.from_snapshot(snapshot)


def test_completed_state_must_have_a_fixed_finish_snapshot():
    snapshot = active_state().to_snapshot()
    snapshot["status"] = "completed"
    with pytest.raises(ValueError):
        TaskSession.from_snapshot(snapshot)


def test_revision_must_match_persisted_events():
    snapshot = active_state().to_snapshot()
    snapshot["revision"] = 0
    with pytest.raises(ValueError):
        TaskSession.from_snapshot(snapshot)


@pytest.mark.parametrize("source_revision", [2, 99])
def test_hint_source_revision_cannot_be_current_or_future(source_revision):
    snapshot = hinted_state().to_snapshot()
    snapshot["hints"][0]["source_revision"] = source_revision
    with pytest.raises(ValueError):
        TaskSession.from_snapshot(snapshot)


def test_hint_source_revisions_must_respect_hint_order():
    state = hinted_state().use_hint(
        expected_revision=2,
        hint_id="hint-2",
        kind="simpler_question",
        criterion_id="feeling",
        now=NOW,
    )
    snapshot = state.to_snapshot()
    snapshot["hints"][1]["source_revision"] = 0
    with pytest.raises(ValueError):
        TaskSession.from_snapshot(snapshot)


def test_message_ids_must_be_unique_including_the_two_sides_of_a_round():
    snapshot = active_state().to_snapshot()
    snapshot["turns"][0]["assistant_turn_id"] = "user-1"
    with pytest.raises(ValueError):
        TaskSession.from_snapshot(snapshot)


def test_duplicate_rounds_are_not_accepted_with_a_matching_revision():
    snapshot = active_state().to_snapshot()
    snapshot["turns"].append(dict(snapshot["turns"][0]))
    snapshot["revision"] = 2
    with pytest.raises(ValueError):
        TaskSession.from_snapshot(snapshot)


def test_hint_ids_must_be_unique_even_when_revision_matches():
    snapshot = hinted_state().to_snapshot()
    second = dict(snapshot["hints"][0])
    second["source_revision"] = 2
    snapshot["hints"].append(second)
    snapshot["revision"] = 3
    with pytest.raises(ValueError):
        TaskSession.from_snapshot(snapshot)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_revision", 99),
        ("turn_count", 99),
        ("condition_results", [["unknown", "unverified"]]),
        ("condition_results", [["activity", "unverified"]] * 3),
    ],
)
def test_finished_summary_must_match_history_and_pinned_task(field, value):
    snapshot = completed_state().to_snapshot()
    snapshot["finished"][field] = value
    with pytest.raises(ValueError):
        TaskSession.from_snapshot(snapshot)


@pytest.mark.parametrize(
    "field,value",
    [
        ("criterion_id", "unknown"),
        ("text", "Never authored in this task"),
        ("kind", "simpler_question"),
    ],
)
def test_restored_hints_must_match_the_pinned_authored_hint(field, value):
    snapshot = hinted_state().to_snapshot()
    snapshot["hints"][0][field] = value
    with pytest.raises(ValueError):
        TaskSession.from_snapshot(snapshot)


def test_valid_restored_state_accepts_the_next_hint_after_rounds_and_hints():
    state = TaskSession.from_snapshot(hinted_state().to_snapshot())
    state = state.append_turn(
        expected_revision=2,
        user_turn_id="user-2",
        assistant_turn_id="coach-2",
        text="With friends.",
        reply="How was it?",
        input_source="text",
        now=NOW,
    ).use_hint(
        expected_revision=3,
        hint_id="hint-2",
        kind="simpler_question",
        criterion_id="feeling",
        now=NOW,
    )
    assert TaskSession.from_snapshot(state.to_snapshot()) == state
    assert [hint.source_revision for hint in state.hints] == [1, 3]
