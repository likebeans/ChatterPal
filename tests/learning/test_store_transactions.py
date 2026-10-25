"""Business services commit related learning facts in one owned transaction."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from chatterpal.learning.storage import LearningStore, StoreError


@pytest.fixture
def store(tmp_path):
    return LearningStore(tmp_path / "transactions.sqlite3")


@pytest.fixture
def learner(store):
    return store.bootstrap()[0]["learner_id"]


def session(store, learner):
    return store.create_record(
        learner,
        "session-start-1",
        kind="session",
        session_id="session-1",
        data={"turns": [], "status": "active"},
    )[0]


def test_request_key_is_scoped_to_owner_and_operation_type(store, learner):
    record = session(store, learner)
    # A caller may reuse a key across different actions, as specified by R0's plan.
    updated, _ = store.update_record(
        learner,
        "session-start-1",
        record["record_id"],
        expected_revision=1,
        data={"turns": []},
    )
    assert updated["revision"] == 2
    with pytest.raises(StoreError) as failure:
        store.get_operation(learner, request_id="session-start-1")
    assert failure.value.code == "ambiguous_request"
    found = store.get_operation(
        learner, request_id="session-start-1", action="create_record"
    )
    assert found["result_id"] == record["record_id"]


def test_bound_operation_rejects_foreign_or_deleted_record(store, learner):
    record = session(store, learner)
    other = store.bootstrap()[0]["learner_id"]
    with pytest.raises(StoreError) as failure:
        store.begin_operation(
            other,
            "external-turn-1",
            "turn",
            {},
            record_id=record["record_id"],
            expected_revision=1,
        )
    assert failure.value.status_code == 404
    claim = store.begin_operation(
        learner,
        "external-turn-1",
        "turn",
        {},
        record_id=record["record_id"],
        expected_revision=1,
    )
    store.delete_record(learner, record["record_id"])
    with pytest.raises(StoreError) as failure:
        store.complete_operation(
            learner,
            claim.operation["operation_id"],
            claim.lease_token,
            record={
                "kind": "session",
                "session_id": "session-1",
                "data": {"late": True},
            },
        )
    assert failure.value.status_code == 404
    assert store.list_records(learner)["items"] == []


def test_compound_commit_keeps_attempt_and_review_schedule_together(store, learner):
    parent = session(store, learner)
    review, _ = store.create_record(
        learner,
        "review-start-1",
        kind="review",
        session_id="session-1",
        data={"due_at": "2026-10-24T00:00:00Z"},
    )
    result, operation_id = store.commit_records(
        learner,
        "review-submit-1",
        "review_attempt",
        {"review_id": review["record_id"], "answer": "I'd like a room"},
        creates=[
            {
                "key": "attempt",
                "kind": "attempt",
                "session_id": "session-1",
                "data": {
                    "source_session": parent["record_id"],
                    "transcript": "I'd like a room",
                },
                "parent_record_id": review["record_id"],
            }
        ],
        updates=[
            {
                "record_id": review["record_id"],
                "expected_revision": 1,
                "data": {"due_at": "2026-10-27T00:00:00Z", "reviews_completed": 1},
            }
        ],
    )
    assert result["created"]["attempt"]["data"]["transcript"] == "I'd like a room"
    assert result["updated"][0]["revision"] == 2
    restored = store.get_operation(learner, operation_id=operation_id)
    assert restored["result"] == result
    retry, same_operation = store.commit_records(
        learner,
        "review-submit-1",
        "review_attempt",
        {"review_id": review["record_id"], "answer": "I'd like a room"},
        creates=[
            {
                "key": "attempt",
                "kind": "attempt",
                "session_id": "session-1",
                "data": {
                    "source_session": parent["record_id"],
                    "transcript": "I'd like a room",
                },
                "parent_record_id": review["record_id"],
            }
        ],
        updates=[
            {
                "record_id": review["record_id"],
                "expected_revision": 1,
                "data": {"due_at": "2026-10-27T00:00:00Z", "reviews_completed": 1},
            }
        ],
    )
    assert retry == result and same_operation == operation_id
    assert len(store.list_records(learner, kind="attempt")["items"]) == 1


def test_compound_revision_failure_leaves_no_attempt_or_partial_schedule(
    store, learner
):
    parent = session(store, learner)
    with pytest.raises(StoreError) as failure:
        store.commit_records(
            learner,
            "review-submit-1",
            "review_attempt",
            {},
            creates=[
                {
                    "key": "attempt",
                    "kind": "attempt",
                    "data": {"text": "hello"},
                    "parent_record_id": parent["record_id"],
                }
            ],
            updates=[
                {
                    "record_id": parent["record_id"],
                    "expected_revision": 99,
                    "data": {"changed": True},
                }
            ],
        )
    assert failure.value.code == "revision_conflict"
    assert store.list_records(learner, kind="attempt")["items"] == []
    assert store.get_record(learner, parent["record_id"])["revision"] == 1
    with pytest.raises(StoreError):
        store.get_operation(learner, request_id="review-submit-1")


def test_compound_writes_validate_related_owner(store, learner):
    own = session(store, learner)
    other = store.bootstrap()[0]["learner_id"]
    foreign = session(store, other)
    with pytest.raises(StoreError) as failure:
        store.commit_records(
            learner,
            "review-submit-1",
            "attempt",
            {},
            creates=[
                {
                    "key": "attempt",
                    "kind": "attempt",
                    "data": {},
                    "parent_record_id": foreign["record_id"],
                }
            ],
            updates=[
                {"record_id": own["record_id"], "expected_revision": 1, "data": {}}
            ],
        )
    assert failure.value.status_code == 404
    assert store.get_record(learner, own["record_id"])["revision"] == 1


def test_claim_completion_cannot_publish_against_a_newer_session_revision(
    store, learner
):
    record = session(store, learner)
    claim = store.begin_operation(
        learner,
        "feedback-start-1",
        "feedback",
        {},
        record_id=record["record_id"],
        expected_revision=1,
    )
    store.update_record(
        learner,
        "session-update-1",
        record["record_id"],
        expected_revision=1,
        data={"turns": ["new"]},
    )
    with pytest.raises(StoreError) as failure:
        store.complete_operation(
            learner,
            claim.operation["operation_id"],
            claim.lease_token,
            record={"kind": "analysis", "data": {"source_revision": 1}},
        )
    assert failure.value.code == "revision_conflict"
    assert store.list_records(learner, kind="analysis")["items"] == []


def test_external_call_completes_with_paired_turns_and_related_record_atomically(
    store, learner
):
    parent = session(store, learner)
    claim = store.begin_operation(
        learner,
        "turn-start-1",
        "turn",
        {},
        record_id=parent["record_id"],
        expected_revision=1,
    )
    result = store.complete_operation(
        learner,
        claim.operation["operation_id"],
        claim.lease_token,
        updates=[
            {
                "record_id": parent["record_id"],
                "expected_revision": 1,
                "data": {
                    "turns": [
                        {"role": "user", "text": "Hi"},
                        {"role": "assistant", "text": "Hello"},
                    ]
                },
            }
        ],
        creates=[
            {
                "key": "event",
                "kind": "event",
                "data": {"event": "turn_completed"},
                "parent_record_id": parent["record_id"],
            }
        ],
    )
    assert result["status"] == "succeeded"
    assert len(store.get_record(learner, parent["record_id"])["data"]["turns"]) == 2
    assert len(store.list_records(learner, kind="event")["items"]) == 1
    store.delete_record(learner, parent["record_id"])
    assert store.list_records(learner)["items"] == []
    with pytest.raises(StoreError):
        store.get_operation(learner, operation_id=claim.operation["operation_id"])


def test_two_revisions_race_and_only_one_full_turn_is_written(store, learner):
    parent = session(store, learner)
    barrier = threading.Barrier(2)

    def worker(index):
        barrier.wait(timeout=3)
        try:
            return store.commit_records(
                learner,
                f"turn-submit-{index}",
                "turn",
                {"text": str(index)},
                updates=[
                    {
                        "record_id": parent["record_id"],
                        "expected_revision": 1,
                        "data": {"turns": [f"user-{index}", f"assistant-{index}"]},
                    }
                ],
            )
        except StoreError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(worker, range(2)))
    assert sum(result == "revision_conflict" for result in results) == 1
    stored = store.get_record(learner, parent["record_id"])
    assert stored["revision"] == 2
    assert stored["data"]["turns"] in (
        ["user-0", "assistant-0"],
        ["user-1", "assistant-1"],
    )


@pytest.mark.parametrize(
    "data",
    [
        {"audio_asset_id": []},
        {"audio_asset_id": "business-id", "state": "original", "url": "/business/path"},
        {"record_id": "external-business-id"},
        {"nested": [{"record_id": "external-id", "audio_asset_id": {"custom": True}}]},
    ],
)
def test_opaque_snapshot_fields_survive_save_replay_recovery_and_export(
    store, learner, data
):
    import json

    saved = store.create_record(learner, "opaque-record-1", kind="session", data=data)
    assert saved[0]["data"] == data
    assert (
        store.create_record(learner, "opaque-record-1", kind="session", data=data)
        == saved
    )
    assert store.get_operation(learner, operation_id=saved[1])["result"] == saved[0]
    with store.export_data(learner) as stream:
        assert json.load(stream)["records"][0]["data"] == data


def test_shutdown_recovery_can_interrupt_only_its_known_operations(store, learner):
    first = store.begin_operation(learner, "external-start-1", "feedback", {})
    second = store.begin_operation(learner, "external-start-2", "feedback", {})
    assert store.interrupt_operations([first.operation["operation_id"]]) == 1
    assert (
        store.get_operation(learner, operation_id=first.operation["operation_id"])[
            "status"
        ]
        == "interrupted"
    )
    assert (
        store.get_operation(learner, operation_id=second.operation["operation_id"])[
            "status"
        ]
        == "pending"
    )
