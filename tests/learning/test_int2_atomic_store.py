"""INT2 session claims, HTTP snapshots and private audio commit together."""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from chatterpal.learning.storage import LearningStore, StoreError


@pytest.fixture
def clock():
    return [1000.0]


@pytest.fixture
def store(tmp_path, clock):
    return LearningStore(tmp_path / "int2.sqlite3", clock=lambda: clock[0])


@pytest.fixture
def learner(store):
    return store.bootstrap()[0]["learner_id"]


@pytest.fixture
def session(store, learner):
    return store.create_record(
        learner,
        "session-start-1",
        kind="session",
        session_id="weekend-1",
        data={"status": "active", "turns": []},
    )[0]


def begin(
    store, learner, session, request_id="weekend-turn-1", action="turn", **options
):
    return store.begin_operation(
        learner,
        request_id,
        action,
        {"text": "I went to the park"},
        session_id=session["session_id"],
        record_id=session["record_id"],
        expected_revision=session["revision"],
        exclusive_session=True,
        **options,
    )


def update(session, data):
    return {
        "record_id": session["record_id"],
        "expected_revision": session["revision"],
        "data": data,
    }


def complete(store, learner, claim, **changes):
    return store.complete_operation(
        learner, claim.operation["operation_id"], claim.lease_token, **changes
    )


def counts(store):
    with sqlite3.connect(store.path) as connection:
        return {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("records", "operations", "audio_assets", "operation_records")
        }


def test_pending_turn_blocks_finish_but_same_request_replays_first(
    store, learner, session
):
    claim = begin(store, learner, session)
    duplicate = begin(store, learner, session)
    assert not duplicate.acquired and duplicate.operation == claim.operation
    assert store.current_pending(learner, session["session_id"]) == [claim.operation]
    with pytest.raises(StoreError) as failure:
        begin(store, learner, session, request_id="weekend-finish-1", action="finish")
    assert failure.value.code == "session_busy" and failure.value.status_code == 409
    with pytest.raises(StoreError) as failure:
        store.begin_operation(
            learner,
            "weekend-turn-1",
            "turn",
            {"text": "different"},
            session_id=session["session_id"],
            record_id=session["record_id"],
            expected_revision=1,
            exclusive_session=True,
        )
    assert failure.value.code == "request_conflict"
    store.fail_operation(learner, claim.operation["operation_id"], claim.lease_token)
    assert begin(store, learner, session, "weekend-finish-1", "finish").acquired


def test_completed_request_replays_before_source_cas_and_other_pending_work(
    store, learner, session
):
    claim = begin(store, learner, session)
    response = {"session_id": session["session_id"], "revision": 2, "turns": ["first"]}
    done = complete(
        store,
        learner,
        claim,
        result=response,
        result_type="session",
        updates=[update(session, response)],
    )
    current = store.get_record(learner, session["record_id"])
    begin(store, learner, current, "weekend-turn-2")
    replay = begin(store, learner, session)
    assert not replay.acquired and replay.operation == done
    assert replay.operation["result"] == response
    assert current["revision"] == 2


def test_exclusive_claim_is_scoped_to_owner_and_session(store, learner, session):
    first = begin(store, learner, session)
    other = store.bootstrap()[0]["learner_id"]
    foreign = store.create_record(
        other,
        "session-start-1",
        kind="session",
        session_id=session["session_id"],
        data={},
    )[0]
    assert begin(store, other, foreign).acquired
    assert store.begin_operation(
        learner,
        "other-session-1",
        "turn",
        {},
        session_id="weekend-2",
        exclusive_session=True,
    ).acquired
    assert store.current_pending(learner, session["session_id"]) == [first.operation]
    with pytest.raises(StoreError) as failure:
        store.get_operation(other, operation_id=first.operation["operation_id"])
    assert failure.value.status_code == 404


def test_two_connections_cannot_claim_different_writes_for_one_session(
    store, learner, session
):
    stores = [LearningStore(store.path) for _ in range(2)]
    barrier = threading.Barrier(2)

    def worker(index):
        barrier.wait(timeout=5)
        try:
            return begin(
                stores[index],
                learner,
                session,
                f"concurrent-{index}",
                ("turn", "finish")[index],
            )
        except StoreError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, range(2)))
    assert results.count("session_busy") == 1
    assert sum(getattr(result, "acquired", False) for result in results) == 1
    assert len(store.current_pending(learner, session["session_id"])) == 1


def test_default_claims_and_claims_without_session_keep_a2_parallel_behavior(
    store, learner
):
    for index in range(2):
        assert store.begin_operation(
            learner, f"legacy-request-{index}", "turn", {}, session_id="legacy"
        ).acquired
        assert store.begin_operation(
            learner, f"without-session-{index}", "turn", {}, exclusive_session=True
        ).acquired
    assert len(store.current_pending(learner, "legacy")) == 2
    with pytest.raises(StoreError) as failure:
        store.begin_operation(
            learner,
            "finish-legacy-1",
            "finish",
            {},
            session_id="legacy",
            exclusive_session=True,
        )
    assert failure.value.code == "session_busy"


@pytest.mark.parametrize("inspect_first", [False, True])
def test_expiry_is_checked_in_pending_inspection_and_exclusive_claim(
    store, learner, session, clock, inspect_first
):
    expired = begin(store, learner, session, lease_seconds=10)
    clock[0] += 10
    if inspect_first:
        assert store.current_pending(learner, session["session_id"]) == []
        with sqlite3.connect(store.path) as connection:
            assert (
                connection.execute(
                    "SELECT status FROM operations WHERE operation_id=?",
                    (expired.operation["operation_id"],),
                ).fetchone()[0]
                == "interrupted"
            )
    replacement = begin(store, learner, session, "weekend-finish-1", "finish")
    assert replacement.acquired
    assert store.current_pending(learner, session["session_id"]) == [
        replacement.operation
    ]
    assert not begin(store, learner, session).acquired
    with pytest.raises(StoreError) as failure:
        complete(store, learner, expired, updates=[update(session, {"late": True})])
    assert failure.value.code == "operation_lease_lost"
    assert store.get_record(learner, session["record_id"])["revision"] == 1


def test_claim_still_checks_source_owner_revision_and_request_fingerprint(
    store, learner, session
):
    other = store.bootstrap()[0]["learner_id"]
    with pytest.raises(StoreError) as failure:
        begin(store, other, session)
    assert failure.value.status_code == 404
    stale = dict(session, revision=2)
    with pytest.raises(StoreError) as failure:
        begin(store, learner, stale)
    assert failure.value.code == "revision_conflict"
    begin(store, learner, session)
    with pytest.raises(StoreError) as failure:
        begin(store, learner, stale)
    assert failure.value.code == "request_conflict"


@pytest.mark.parametrize(
    "response", [None, False, 0, ["hint"], {"record_id": "business-id"}]
)
def test_business_http_result_is_independent_of_linked_compound_writes(
    store, learner, session, response
):
    claim = begin(store, learner, session)
    done = complete(
        store,
        learner,
        claim,
        result=response,
        result_type="hint",
        creates=[{"key": "event", "kind": "event", "data": {"hint": "Last weekend"}}],
        updates=[update(session, {"hint_count": 1})],
    )
    assert done["result"] == response
    assert done["result_type"] == "hint"
    event = store.list_records(learner, kind="event")["items"][0]
    assert store.get_record(learner, session["record_id"])["revision"] == 2
    store.delete_record(learner, event["record_id"])
    with pytest.raises(StoreError) as failure:
        store.get_operation(learner, operation_id=done["operation_id"])
    assert failure.value.status_code == 404
    assert store.get_record(learner, session["record_id"])["revision"] == 2


def test_session_attempt_audio_and_http_result_share_one_committed_asset(
    store, learner, session, audio_bytes
):
    claim = begin(store, learner, session)
    placeholder = {"audio_key": "user"}
    snapshot = {
        "session_id": session["session_id"],
        "revision": 2,
        "turns": [{"role": "user", "text": "I went to the park", "audio": placeholder}],
        "domain": {"events": [{"nested": {"audio": placeholder}}]},
    }
    done = complete(
        store,
        learner,
        claim,
        result=snapshot,
        result_type="session",
        result_id=session["session_id"],
        creates=[
            {
                "key": "attempt",
                "kind": "attempt",
                "session_id": session["session_id"],
                "parent_record_id": session["record_id"],
                "data": {"transcript": "I went to the park", "audio": placeholder},
            }
        ],
        updates=[update(session, snapshot)],
        audio=[{"key": "user", "content": audio_bytes, "retain": False}],
    )
    asset = done["result"]["turns"][0]["audio"]
    assert asset["state"] == "available" and asset["media_type"] == "audio/wav"
    assert asset["record_id"] == session["record_id"]
    assert done["result"]["domain"]["events"][0]["nested"]["audio"] == asset
    saved = store.get_record(learner, session["record_id"])
    assert saved["data"] == done["result"] and saved["revision"] == 2
    assert saved["audio_assets"] == [asset]
    attempt = store.list_records(learner, kind="attempt")["items"][0]
    assert attempt["data"]["audio"] == asset
    assert store.get_audio(learner, asset["audio_asset_id"])[1] == audio_bytes
    assert not store.get_profile(learner)["retain_audio"]
    reopened = LearningStore(store.path, clock=store.clock)
    assert begin(reopened, learner, session).operation == done
    assert counts(store)["audio_assets"] == 1
    assert snapshot["turns"][0]["audio"] == placeholder
    store.delete_record(learner, session["record_id"])
    assert store.list_records(learner)["items"] == []
    assert counts(store)["audio_assets"] == 0
    with pytest.raises(StoreError):
        store.get_operation(learner, operation_id=done["operation_id"])


@pytest.mark.parametrize("custom_result", [False, True])
def test_audio_can_commit_with_a_new_single_record(
    store, learner, audio_bytes, custom_result
):
    claim = store.begin_operation(learner, "start-weekend-1", "start", {})
    arguments = (
        {"result": {"greeting": {"audio_key": "coach"}}} if custom_result else {}
    )
    done = complete(
        store,
        learner,
        claim,
        record={
            "kind": "session",
            "session_id": "weekend-new",
            "data": {"audio": {"audio_key": "coach"}},
        },
        audio=[{"key": "coach", "content": audio_bytes, "retain": False}],
        **arguments,
    )
    saved = store.list_records(learner)["items"][0]
    asset = saved["data"]["audio"]
    assert saved["revision"] == 1 and saved["audio_assets"] == [asset]
    assert done["result_id"] == saved["record_id"]
    assert done["result"] == ({"greeting": asset} if custom_result else saved)


def test_audio_can_use_a_unique_compound_creation(store, learner, audio_bytes):
    claim = store.begin_operation(learner, "start-weekend-1", "start", {})
    done = complete(
        store,
        learner,
        claim,
        creates=[
            {
                "key": "session",
                "kind": "session",
                "data": {"audio": {"audio_key": "coach"}},
            }
        ],
        audio=[{"key": "coach", "content": audio_bytes, "retain": False}],
    )
    saved = done["result"]["created"]["session"]
    assert saved["audio_assets"] == [saved["data"]["audio"]]
    assert store.get_record(learner, saved["record_id"]) == saved


@pytest.mark.parametrize("explicit_target", [False, True])
def test_example_audio_can_commit_without_advancing_session_revision(
    store, learner, session, audio_bytes, explicit_target
):
    options = {} if explicit_target else {"record_id": session["record_id"]}
    claim = store.begin_operation(
        learner, "example-audio-1", "example", {"attempt_id": "attempt-1"}, **options
    )
    changes = {"resource_record_id": session["record_id"]} if explicit_target else {}
    done = complete(
        store,
        learner,
        claim,
        result={"audio_key": "example"},
        result_type="audio",
        audio=[{"key": "example", "content": audio_bytes, "retain": False}],
        **changes,
    )
    assert done["result"]["record_id"] == session["record_id"]
    assert store.get_record(learner, session["record_id"])["revision"] == 1
    assert store.get_audio(learner, done["result"]["audio_asset_id"])[1] == audio_bytes
    store.update_record(
        learner,
        "next-session-revision",
        session["record_id"],
        expected_revision=1,
        data={},
    )
    replay = store.begin_operation(
        learner, "example-audio-1", "example", {"attempt_id": "attempt-1"}, **options
    )
    assert not replay.acquired and replay.operation == done


@pytest.mark.parametrize(
    "fault", ["http_placeholder", "data_placeholder", "http_json", "stale_update"]
)
def test_failed_completion_rolls_back_snapshots_children_audio_and_success_result(
    store, learner, session, audio_bytes, fault
):
    claim = begin(store, learner, session)
    data = {"audio": {"audio_key": "user"}}
    response = {"audio": {"audio_key": "user"}}
    if fault == "http_placeholder":
        response["audio"] = {"audio_key": "missing"}
    elif fault == "data_placeholder":
        data["audio"] = {"audio_key": "missing"}
    elif fault == "http_json":
        response["invalid"] = float("nan")
    changes = update(session, data)
    if fault == "stale_update":
        changes["expected_revision"] = 99
    before = counts(store)
    with pytest.raises(StoreError):
        complete(
            store,
            learner,
            claim,
            result=response,
            creates=[
                {"key": "attempt", "kind": "attempt", "data": {"text": "first try"}}
            ],
            updates=[changes],
            audio=[{"key": "user", "content": audio_bytes, "retain": False}],
        )
    assert counts(store) == before
    assert store.get_record(learner, session["record_id"]) == session
    assert (
        store.get_operation(learner, operation_id=claim.operation["operation_id"])[
            "status"
        ]
        == "pending"
    )
    assert store.current_pending(learner, session["session_id"]) == [claim.operation]


@pytest.mark.parametrize(
    "content,status", [(b"", 422), (b"pretend.mp3", 415), (b"x" * 129, 413)]
)
def test_atomic_audio_uses_existing_media_and_size_validation(
    store, learner, session, content, status
):
    store.max_audio_bytes = 128
    claim = begin(store, learner, session)
    with pytest.raises(StoreError) as failure:
        complete(
            store,
            learner,
            claim,
            updates=[update(session, {"changed": True})],
            audio=[{"key": "user", "content": content, "retain": False}],
        )
    assert failure.value.status_code == status
    assert store.get_record(learner, session["record_id"]) == session
    assert counts(store)["audio_assets"] == 0


@pytest.mark.parametrize(
    "audio",
    [
        {},
        [None],
        [{"key": "", "content": b"OggS", "retain": False}],
        [{"key": "user", "content": b"OggS", "retain": "false"}],
        [{"key": "user", "content": b"OggS"}],
        [{"key": "user", "content": b"OggS", "retain": False}] * 2,
    ],
)
def test_invalid_audio_specifications_roll_back_record_write(
    store, learner, session, audio
):
    claim = begin(store, learner, session)
    with pytest.raises(StoreError) as failure:
        complete(store, learner, claim, updates=[update(session, {})], audio=audio)
    assert failure.value.status_code == 422
    assert store.get_record(learner, session["record_id"]) == session
    assert counts(store)["audio_assets"] == 0


def test_unbound_audio_with_ambiguous_created_records_is_rejected(
    store, learner, audio_bytes
):
    claim = store.begin_operation(learner, "unbound-audio-1", "start", {})
    with pytest.raises(StoreError) as failure:
        complete(
            store,
            learner,
            claim,
            creates=[{"key": key, "kind": "session", "data": {}} for key in ("a", "b")],
            audio=[{"key": "user", "content": audio_bytes, "retain": False}],
        )
    assert failure.value.code == "invalid_audio_record"
    assert store.list_records(learner)["items"] == []
    assert counts(store)["audio_assets"] == 0


def test_long_retention_checks_current_profile_at_completion(
    store, learner, session, audio_bytes
):
    store.update_profile(learner, {"retain_audio": True})
    claim = begin(store, learner, session)
    store.update_profile(learner, {"retain_audio": False})
    with pytest.raises(StoreError) as failure:
        complete(
            store,
            learner,
            claim,
            updates=[update(session, {"audio": {"audio_key": "user"}})],
            audio=[{"key": "user", "content": audio_bytes, "retain": True}],
        )
    assert failure.value.code == "audio_retention_disabled"
    assert store.get_record(learner, session["record_id"]) == session
    assert counts(store)["audio_assets"] == 0


def test_temporary_and_retained_assets_expire_and_nested_snapshots_refresh(
    store, learner, session, audio_bytes, clock
):
    store.update_profile(learner, {"retain_audio": True})
    claim = begin(store, learner, session)
    snapshot = {
        "nested": [
            {"temporary": {"audio_key": "user"}, "retained": {"audio_key": "coach"}}
        ]
    }
    done = complete(
        store,
        learner,
        claim,
        result=snapshot,
        updates=[update(session, snapshot)],
        audio=[
            {"key": "user", "content": audio_bytes, "retain": False},
            {"key": "coach", "content": b"ID3" + b"\0" * 16, "retain": True},
        ],
    )
    assets = done["result"]["nested"][0]
    created = datetime.fromisoformat(assets["temporary"]["created_at"])
    assert (
        datetime.fromisoformat(assets["temporary"]["expires_at"]) - created
    ).total_seconds() == 3600
    assert (
        datetime.fromisoformat(assets["retained"]["expires_at"]) - created
    ).total_seconds() == 7 * 86400
    with store.export_data(learner) as stream:
        exported = stream.read()
        assert audio_bytes not in exported and b"content" not in exported
    clock[0] += 3600
    expected = {
        "temporary": dict(assets["temporary"], state="expired", url=None),
        "retained": assets["retained"],
    }
    reopened = LearningStore(store.path, clock=store.clock)
    assert (
        reopened.get_record(learner, session["record_id"])["data"]["nested"][0]
        == expected
    )
    assert (
        reopened.get_operation(learner, operation_id=done["operation_id"])["result"][
            "nested"
        ][0]
        == expected
    )
    with pytest.raises(StoreError) as failure:
        reopened.get_audio(learner, assets["temporary"]["audio_asset_id"])
    assert failure.value.code == "audio_expired"
    assert reopened.get_audio(learner, assets["retained"]["audio_asset_id"])[
        1
    ].startswith(b"ID3")
    assert reopened.purge_expired_audio() == 1
    with store.export_data(learner) as stream:
        assert json.load(stream)["records"][0]["data"]["nested"][0] == expected
    clock[0] += 7 * 86400 - 3600
    assert (
        store.get_operation(learner, operation_id=done["operation_id"])["result"][
            "nested"
        ][0]["retained"]["state"]
        == "expired"
    )
    assert store.purge_expired_audio() == 1
    with sqlite3.connect(store.path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM audio_assets WHERE content IS NOT NULL"
            ).fetchone()[0]
            == 0
        )


def test_disabling_retention_clears_long_and_temporary_audio_in_all_replays(
    store, learner, session, audio_bytes
):
    store.update_profile(learner, {"retain_audio": True})
    claim = begin(store, learner, session)
    done = complete(
        store,
        learner,
        claim,
        result={
            "deep": [
                {"original": {"audio_key": "user"}, "coach": {"audio_key": "coach"}}
            ]
        },
        updates=[update(session, {"audio": {"audio_key": "user"}})],
        audio=[
            {"key": "user", "content": audio_bytes, "retain": False},
            {"key": "coach", "content": audio_bytes, "retain": True},
        ],
    )
    other = store.bootstrap()[0]["learner_id"]
    for asset in done["result"]["deep"][0].values():
        with pytest.raises(StoreError) as failure:
            store.get_audio(other, asset["audio_asset_id"])
        assert failure.value.status_code == 404
    store.update_profile(learner, {"retain_audio": False})
    assert counts(store)["audio_assets"] == 0
    for asset in store.get_operation(learner, operation_id=done["operation_id"])[
        "result"
    ]["deep"][0].values():
        assert asset["state"] == "not_saved" and asset["url"] is None
    saved = store.get_record(learner, session["record_id"])
    assert (
        saved["data"]["audio"]["state"] == "not_saved"
        and saved["data"]["audio"]["url"] is None
    )
    assert saved["revision"] == 2 and saved["audio_assets"] == []


def test_no_audio_argument_keeps_opaque_placeholders_and_null_asset_fields(
    store, learner, session
):
    data = {
        "placeholder": {"audio_key": "business-key"},
        "audio": {"audio_asset_id": None, "state": "unavailable", "url": "/opaque"},
        "opaque": {"audio_asset_id": [], "state": []},
    }
    claim = begin(store, learner, session)
    done = complete(store, learner, claim, result=data, updates=[update(session, data)])
    assert done["result"] == data
    assert store.get_record(learner, session["record_id"])["data"] == data
    assert (
        store.get_operation(learner, operation_id=done["operation_id"])["result"]
        == data
    )


def test_foreign_audio_references_never_gain_private_metadata_or_available_urls(
    store, learner, session, audio_bytes
):
    claim = begin(store, learner, session)
    done = complete(
        store,
        learner,
        claim,
        result={"audio_key": "user"},
        audio=[{"key": "user", "content": audio_bytes, "retain": False}],
    )
    asset = done["result"]
    other = store.bootstrap()[0]["learner_id"]
    saved, operation = store.create_record(
        other,
        "foreign-asset-reference",
        kind="session",
        data={
            "nested": [
                {
                    "audio_asset_id": asset["audio_asset_id"],
                    "state": "available",
                    "url": asset["url"],
                }
            ]
        },
    )
    reference = saved["data"]["nested"][0]
    assert reference == {
        "audio_asset_id": asset["audio_asset_id"],
        "state": "not_saved",
        "url": None,
    }
    assert (
        store.get_operation(other, operation_id=operation)["result"]["data"]["nested"][
            0
        ]
        == reference
    )
    with pytest.raises(StoreError) as failure:
        complete(
            store,
            other,
            store.begin_operation(other, "foreign-audio-target", "example", {}),
            resource_record_id=session["record_id"],
            audio=[{"key": "user", "content": audio_bytes, "retain": False}],
        )
    assert failure.value.status_code == 404
    assert counts(store)["audio_assets"] == 1


@pytest.mark.parametrize("delete_owner", [False, True])
def test_delete_during_provider_work_cannot_restore_records_or_audio(
    store, learner, session, audio_bytes, delete_owner
):
    claim = begin(store, learner, session)
    entered, release = threading.Event(), threading.Event()

    def finish_late():
        entered.set()
        assert release.wait(5)
        try:
            complete(
                store,
                learner,
                claim,
                result={"audio_key": "user"},
                creates=[{"key": "attempt", "kind": "attempt", "data": {"late": True}}],
                updates=[update(session, {"audio": {"audio_key": "user"}})],
                audio=[{"key": "user", "content": audio_bytes, "retain": False}],
            )
        except StoreError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(finish_late)
        try:
            assert entered.wait(3)
            if delete_owner:
                store.delete_learner(learner)
            else:
                store.delete_record(learner, session["record_id"])
        finally:
            release.set()
        assert pending.result(timeout=5) == (
            "identity_required" if delete_owner else "operation_not_found"
        )
    assert counts(store) == {
        "records": 0,
        "operations": 0,
        "audio_assets": 0,
        "operation_records": 0,
    }


@pytest.mark.parametrize("explicit_target", [False, True])
def test_deleted_source_rejects_audio_only_completion(
    store, learner, session, audio_bytes, explicit_target
):
    options = {} if explicit_target else {"record_id": session["record_id"]}
    claim = store.begin_operation(
        learner, "deleted-example-1", "example", {}, **options
    )
    store.delete_record(learner, session["record_id"])
    before = counts(store)
    with pytest.raises(StoreError) as failure:
        complete(
            store,
            learner,
            claim,
            resource_record_id=session["record_id"],
            result={"audio_key": "example"},
            audio=[{"key": "example", "content": audio_bytes, "retain": False}],
        )
    assert failure.value.status_code == 404
    assert counts(store) == before
    assert counts(store)["records"] == counts(store)["audio_assets"] == 0


def test_expired_lease_rejects_audio_only_without_changing_source(
    store, learner, session, clock, audio_bytes
):
    claim = store.begin_operation(
        learner,
        "expired-example-1",
        "example",
        {},
        record_id=session["record_id"],
        lease_seconds=10,
    )
    clock[0] += 10
    before = counts(store)
    with pytest.raises(StoreError) as failure:
        complete(
            store,
            learner,
            claim,
            resource_record_id=session["record_id"],
            result={"audio_key": "example"},
            audio=[{"key": "example", "content": audio_bytes, "retain": False}],
        )
    assert failure.value.code == "operation_lease_lost"
    assert counts(store) == before
    assert store.get_record(learner, session["record_id"]) == session


def test_failed_http_result_can_retry_same_claim_without_orphan_audio(
    store, learner, session, audio_bytes
):
    claim = begin(store, learner, session)
    changes = {
        "updates": [update(session, {"audio": {"audio_key": "user"}})],
        "audio": [{"key": "user", "content": audio_bytes, "retain": False}],
    }
    with pytest.raises(StoreError) as failure:
        complete(store, learner, claim, result={"invalid": float("inf")}, **changes)
    assert failure.value.code == "invalid_json"
    assert counts(store)["audio_assets"] == 0
    done = complete(store, learner, claim, result={"audio_key": "user"}, **changes)
    assert done["status"] == "succeeded"
    assert store.get_record(learner, session["record_id"])["revision"] == 2
    assert counts(store)["audio_assets"] == 1
    assert store.get_audio(learner, done["result"]["audio_asset_id"])[1] == audio_bytes


def test_unrelated_preference_change_preserves_temporary_audio_until_explicit_disable(
    store, learner, session, audio_bytes
):
    claim = begin(store, learner, session)
    done = complete(
        store,
        learner,
        claim,
        result={"audio_key": "user"},
        audio=[{"key": "user", "content": audio_bytes, "retain": False}],
    )
    asset_id = done["result"]["audio_asset_id"]
    store.update_profile(learner, {"goal": "travel"})
    assert store.get_audio(learner, asset_id)[1] == audio_bytes
    store.update_profile(learner, {"retain_audio": False})
    with pytest.raises(StoreError) as failure:
        store.get_audio(learner, asset_id)
    assert failure.value.status_code == 404
    assert counts(store)["audio_assets"] == 0


def test_business_result_without_record_writes_remains_opaque(store, learner, session):
    claim = begin(store, learner, session)
    response = {"record_id": "external-business-value", "nested": {"records": []}}
    done = complete(store, learner, claim, result=response, result_type="hint")
    assert done["result"] == response
    assert store.get_record(learner, session["record_id"]) == session
    store.delete_record(learner, session["record_id"])
    with pytest.raises(StoreError) as failure:
        store.get_operation(learner, operation_id=done["operation_id"])
    assert failure.value.status_code == 404


def test_lease_expiring_during_audio_commit_rolls_back_all_writes(
    store, learner, session, clock, audio_bytes, monkeypatch
):
    claim = begin(store, learner, session, lease_seconds=10)
    insert_audio = store._insert_audio

    def advance_after_audio(*args, **kwargs):
        asset = insert_audio(*args, **kwargs)
        clock[0] += 10
        return asset

    monkeypatch.setattr(store, "_insert_audio", advance_after_audio)
    before = counts(store)
    with pytest.raises(StoreError) as failure:
        complete(
            store,
            learner,
            claim,
            result={"audio_key": "user"},
            creates=[{"key": "attempt", "kind": "attempt", "data": {}}],
            updates=[update(session, {"audio": {"audio_key": "user"}})],
            audio=[{"key": "user", "content": audio_bytes, "retain": False}],
        )
    assert failure.value.code == "operation_lease_lost"
    assert counts(store) == before
    assert store.get_record(learner, session["record_id"]) == session
    assert store.current_pending(learner, session["session_id"]) == []
    assert (
        store.get_operation(learner, operation_id=claim.operation["operation_id"])[
            "status"
        ]
        == "interrupted"
    )


def test_lease_expiring_while_building_http_response_rolls_back_publication(
    store, learner, session, clock, audio_bytes, monkeypatch
):
    claim = begin(store, learner, session, lease_seconds=10)
    operation_view = store._operation

    def advance_after_response(connection, row):
        response = operation_view(connection, row)
        clock[0] += 10
        return response

    before = counts(store)
    with monkeypatch.context() as patch:
        patch.setattr(store, "_operation", advance_after_response)
        with pytest.raises(StoreError) as failure:
            complete(
                store,
                learner,
                claim,
                result={"audio_key": "user"},
                updates=[update(session, {"audio": {"audio_key": "user"}})],
                audio=[{"key": "user", "content": audio_bytes, "retain": False}],
            )
    assert failure.value.code == "operation_lease_lost"
    assert counts(store) == before
    assert store.get_record(learner, session["record_id"]) == session
    assert store.current_pending(learner, session["session_id"]) == []
    assert (
        store.get_operation(learner, operation_id=claim.operation["operation_id"])[
            "status"
        ]
        == "interrupted"
    )


@pytest.mark.parametrize("with_audio", [False, True])
def test_deep_json_snapshots_survive_save_replay_and_audio_resolution(
    store, learner, session, audio_bytes, with_audio
):
    leaf = {"audio_key": "user"} if with_audio else {"text": "opaque business data"}
    snapshot = leaf
    for _ in range(600):
        snapshot = {"nested": snapshot}

    def leaf_of(value):
        for _ in range(600):
            value = value["nested"]
        return value

    claim = begin(store, learner, session)
    options = (
        {"audio": [{"key": "user", "content": audio_bytes, "retain": False}]}
        if with_audio
        else {}
    )
    done = complete(
        store,
        learner,
        claim,
        result=snapshot,
        updates=[update(session, snapshot)],
        **options,
    )
    expected = leaf_of(done["result"])
    if with_audio:
        assert expected["state"] == "available"
        assert store.get_audio(learner, expected["audio_asset_id"])[1] == audio_bytes
    else:
        assert expected == leaf
    saved = store.get_record(learner, session["record_id"])
    assert saved["revision"] == 2 and leaf_of(saved["data"]) == expected
    replay = store.get_operation(learner, operation_id=done["operation_id"])
    assert leaf_of(replay["result"]) == expected
