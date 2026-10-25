"""Durability, isolation and concurrent recovery of private learning data."""

import json
import multiprocessing
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from chatterpal.learning import LearningStore, StoreError


@pytest.fixture
def store(tmp_path):
    return LearningStore(tmp_path / "learning.sqlite3")


@pytest.fixture
def learner(store):
    identity, _ = store.bootstrap()
    return identity["learner_id"]


def create(store, learner, request_id="create-record-1", **kwargs):
    return store.create_record(
        learner,
        request_id,
        kind="session",
        data=kwargs.pop("data", {"turns": []}),
        **kwargs,
    )


def test_anonymous_identity_survives_new_store_without_storing_bearer_token(store):
    bootstrap, token = store.bootstrap()
    learner = bootstrap["learner_id"]
    store.update_profile(learner, {"goal": "work", "daily_minutes": 15})
    reopened = LearningStore(store.path)
    restored, same_token = reopened.bootstrap(token)
    assert restored["learner_id"] == learner
    assert same_token == token
    assert restored["profile"]["goal"] == "work"
    assert reopened.authenticate(token) == learner
    with sqlite3.connect(store.path) as database:
        stored = database.execute("SELECT token_hash FROM learners").fetchone()[0]
    assert stored != token and len(stored) == 64
    assert token.encode() not in store.path.read_bytes()
    assert store.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("token", [None, "", "forged", "x" * 43, "é" * 43, "x" * 10000])
def test_invalid_identity_cannot_read_private_data_but_bootstrap_creates_new_one(
    store, token
):
    with pytest.raises(StoreError) as failure:
        store.authenticate(token)
    assert failure.value.status_code == 401
    first, bearer = store.bootstrap(token)
    second, _ = store.bootstrap()
    assert first["learner_id"] != second["learner_id"]
    assert store.authenticate(bearer) == first["learner_id"]


@pytest.mark.parametrize(
    "patch",
    [
        {"goal": "unknown"},
        {"daily_minutes": 0},
        {"daily_minutes": 121},
        {"daily_minutes": True},
        {"daily_minutes": 1.5},
        {"retain_audio": "false"},
        {"retain_audio": None},
        {"onboarding_completed": 1},
        {"correction_mode": "x"},
        {"learner_id": "someone-else"},
    ],
)
def test_invalid_profile_patch_is_atomic(store, learner, patch):
    original = store.get_profile(learner)
    with pytest.raises(StoreError) as failure:
        store.update_profile(learner, patch)
    assert failure.value.status_code == 422
    assert store.get_profile(learner) == original


def test_records_are_isolated_revisioned_and_durable(store, learner):
    second, _ = store.bootstrap()
    other = second["learner_id"]
    record, operation_id = create(
        store,
        learner,
        session_id="business-session",
        data={"turns": [{"text": "你好"}]},
    )
    reopened = LearningStore(store.path)
    assert reopened.get_record(learner, record["record_id"]) == record
    for action in (
        lambda: reopened.get_record(other, record["record_id"]),
        lambda: reopened.update_record(
            other, "other-update", record["record_id"], expected_revision=1, data={}
        ),
        lambda: reopened.delete_record(other, record["record_id"]),
        lambda: reopened.get_operation(other, operation_id=operation_id),
    ):
        with pytest.raises(StoreError) as failure:
            action()
        assert failure.value.status_code == 404
    updated, _ = reopened.update_record(
        learner,
        "update-record-1",
        record["record_id"],
        expected_revision=1,
        data={"completed": True},
    )
    assert updated["revision"] == 2
    assert updated["created_at"] == record["created_at"]
    assert updated["session_id"] == "business-session"
    with pytest.raises(StoreError) as failure:
        reopened.update_record(
            learner, "stale-update", record["record_id"], expected_revision=1, data={}
        )
    assert failure.value.code == "revision_conflict"
    assert reopened.get_record(learner, record["record_id"]) == updated
    assert reopened.list_records(other)["items"] == []


def test_same_request_replays_exact_result_and_conflicts_on_new_payload(store, learner):
    first = create(store, learner, data={"a": 1, "b": 2})
    assert create(LearningStore(store.path), learner, data={"b": 2, "a": 1}) == first
    assert len(store.list_records(learner)["items"]) == 1
    with pytest.raises(StoreError) as failure:
        create(store, learner, data={"a": 3})
    assert failure.value.code == "request_conflict"
    restored = store.get_operation(learner, request_id="create-record-1")
    assert restored["operation_id"] == first[1]
    assert restored["status"] == "succeeded"
    assert restored["result"] == first[0]
    updated = store.update_record(
        learner,
        "update-record-1",
        first[0]["record_id"],
        expected_revision=1,
        data={"next": 1},
    )
    assert (
        store.update_record(
            learner,
            "update-record-1",
            first[0]["record_id"],
            expected_revision=1,
            data={"next": 1},
        )
        == updated
    )


def test_same_request_key_is_scoped_to_learner(store, learner):
    second, _ = store.bootstrap()
    a, _ = create(store, learner)
    b, _ = create(store, second["learner_id"])
    assert a["record_id"] != b["record_id"]


def test_concurrent_connections_commit_one_record_and_one_operation(store, learner):
    stores = [LearningStore(store.path) for _ in range(8)]
    barrier = threading.Barrier(len(stores))

    def worker(database):
        barrier.wait(timeout=5)
        return create(database, learner)

    with ThreadPoolExecutor(max_workers=len(stores)) as pool:
        results = list(pool.map(worker, stores))
    assert all(result == results[0] for result in results)
    with sqlite3.connect(store.path) as database:
        assert database.execute("SELECT count(*) FROM records").fetchone()[0] == 1
        assert database.execute("SELECT count(*) FROM operations").fetchone()[0] == 1


def _process_create(path, learner, start, result):
    try:
        database = LearningStore(path)
        if not start.wait(10):
            raise TimeoutError("concurrent process did not start")
        record, operation = create(database, learner)
        result.put((record["record_id"], operation))
    except Exception as error:
        result.put(("error", type(error).__name__))


def test_request_deduplication_survives_independent_processes(store, learner):
    context = multiprocessing.get_context("spawn")
    start, results = context.Event(), context.Queue()
    workers = [
        context.Process(
            target=_process_create, args=(str(store.path), learner, start, results)
        )
        for _ in range(2)
    ]
    try:
        for worker in workers:
            worker.start()
        start.set()
        values = [results.get(timeout=20) for _ in workers]
        for worker in workers:
            worker.join(timeout=10)
        assert all(worker.exitcode == 0 for worker in workers)
        assert values[0] == values[1] and values[0][0] != "error"
        assert len(store.list_records(learner)["items"]) == 1
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
        results.close()
        results.join_thread()


def test_history_cursor_has_no_gaps_after_updates_and_new_records(store, learner):
    ids = [
        create(store, learner, f"request-{i:03}", session_id="session-a")[0][
            "record_id"
        ]
        for i in range(5)
    ]
    first = store.list_records(learner, kind="session", session_id="session-a", limit=2)
    assert [item["record_id"] for item in first["items"]] == ids[::-1][:2]
    store.update_record(
        learner, "update-oldest", ids[0], expected_revision=1, data={"changed": True}
    )
    create(store, learner, "new-record-1", session_id="session-a")
    second = store.list_records(
        learner,
        kind="session",
        session_id="session-a",
        limit=2,
        cursor=first["next_cursor"],
    )
    third = store.list_records(
        learner,
        kind="session",
        session_id="session-a",
        limit=2,
        cursor=second["next_cursor"],
    )
    assert [item["record_id"] for item in second["items"] + third["items"]] == ids[
        ::-1
    ][2:]
    assert third["next_cursor"] is None
    with pytest.raises(StoreError, match="cursor"):
        store.list_records(learner, cursor=first["next_cursor"])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"kind": "bad"},
        {"cursor": "bad"},
        {"cursor": "x" * 1000},
    ],
)
def test_invalid_history_parameters(store, learner, kwargs):
    with pytest.raises(StoreError) as failure:
        store.list_records(learner, **kwargs)
    assert failure.value.status_code == 422


@pytest.mark.parametrize(
    "kwargs",
    [
        {"data": []},
        {"data": {"bad": float("nan")}},
        {"data": {"bad": object()}},
        {"data": {"huge": "a" * (1024 * 1024)}},
        {"session_id": ""},
        {"session_id": "a" * 129},
    ],
)
def test_invalid_records_do_not_leave_operation_or_record(store, learner, kwargs):
    with pytest.raises(StoreError):
        create(store, learner, **kwargs)
    assert store.list_records(learner)["items"] == []
    with pytest.raises(StoreError) as failure:
        store.get_operation(learner, request_id="create-record-1")
    assert failure.value.status_code == 404


@pytest.mark.parametrize(
    "request_id", ["", "short", "a" * 129, "has/slash", "请求标识123456"]
)
def test_invalid_request_keys_are_rejected(store, learner, request_id):
    with pytest.raises(StoreError) as failure:
        create(store, learner, request_id)
    assert failure.value.code == "invalid_request_id"


def test_audio_requires_opt_in_is_private_and_survives_restart(
    store, learner, audio_bytes
):
    record, _ = create(store, learner)
    with pytest.raises(StoreError) as failure:
        store.add_audio(learner, "audio-request-1", record["record_id"], audio_bytes)
    assert failure.value.code == "audio_retention_disabled"
    store.update_profile(learner, {"retain_audio": True})
    asset, operation_id = store.add_audio(
        learner, "audio-request-1", record["record_id"], audio_bytes
    )
    assert asset["media_type"] == "audio/wav"
    assert asset["size_bytes"] == len(audio_bytes)
    assert store.add_audio(
        learner, "audio-request-1", record["record_id"], audio_bytes
    ) == (asset, operation_id)
    reopened = LearningStore(store.path)
    assert reopened.get_audio(learner, asset["audio_asset_id"])[1] == audio_bytes
    other, _ = reopened.bootstrap()
    with pytest.raises(StoreError) as failure:
        reopened.get_audio(other["learner_id"], asset["audio_asset_id"])
    assert failure.value.status_code == 404
    assert reopened.get_record(learner, record["record_id"])["audio_assets"] == [asset]
    reopened.update_profile(learner, {"retain_audio": False})
    with pytest.raises(StoreError) as failure:
        reopened.get_audio(learner, asset["audio_asset_id"])
    assert failure.value.status_code == 404
    assert (
        reopened.get_operation(learner, operation_id=operation_id)["result"]["url"]
        is None
    )
    assert reopened.get_record(learner, record["record_id"])["data"] == record["data"]


@pytest.mark.parametrize(
    "audio,status", [(b"", 422), (b"text file", 415), (b"x" * 2048, 413)]
)
def test_invalid_or_oversized_audio_is_not_persisted(tmp_path, audio, status):
    store = LearningStore(tmp_path / "limits.sqlite3", max_audio_bytes=1024)
    identity, _ = store.bootstrap()
    learner = identity["learner_id"]
    store.update_profile(learner, {"retain_audio": True})
    record, _ = create(store, learner)
    with pytest.raises(StoreError) as failure:
        store.add_audio(learner, "audio-request-1", record["record_id"], audio)
    assert failure.value.status_code == status
    assert store.get_record(learner, record["record_id"])["audio_assets"] == []


def test_expired_audio_cannot_reappear_in_operation_replay_or_export(
    tmp_path, audio_bytes
):
    now = [1000.0]
    store = LearningStore(
        tmp_path / "expiry.sqlite3", audio_retention_days=1, clock=lambda: now[0]
    )
    identity, _ = store.bootstrap()
    learner = identity["learner_id"]
    store.update_profile(learner, {"retain_audio": True})
    record, _ = create(store, learner)
    asset, operation_id = store.add_audio(
        learner, "audio-request-1", record["record_id"], audio_bytes
    )
    now[0] += 86401
    with pytest.raises(StoreError) as failure:
        store.get_audio(learner, asset["audio_asset_id"])
    assert failure.value.status_code == 404
    replayed, same_operation = store.add_audio(
        learner, "audio-request-1", record["record_id"], audio_bytes
    )
    assert same_operation == operation_id
    assert replayed["state"] == "expired" and replayed["url"] is None
    assert store.purge_expired_audio() == 1
    assert store.purge_expired_audio() == 0
    with store.export_data(learner) as stream:
        manifest = json.load(stream)
        assert manifest["records"][0]["audio_assets"][0]["state"] == "expired"


def test_export_contains_only_owned_text_and_audio_metadata_without_credentials(
    store, learner, audio_bytes
):
    store.update_profile(learner, {"retain_audio": True})
    record, _ = create(store, learner, data={"text": "private lesson"})
    asset, _ = store.add_audio(
        learner, "audio-request-1", record["record_id"], audio_bytes
    )
    other, token = store.bootstrap()
    create(store, other["learner_id"], data={"text": "someone else's lesson"})
    with store.export_data(learner) as stream:
        manifest = stream.read()
        data = json.loads(manifest)
        assert data["learner"]["learner_id"] == learner
        assert len(data["records"]) == 1
        assert "someone else's lesson" not in manifest.decode()
        assert b"token_hash" not in manifest and token.encode() not in manifest
        assert b"lease_token" not in manifest and audio_bytes not in manifest
        assert data["records"][0]["audio_assets"][0] == asset


def test_record_delete_removes_audio_and_recovery_results(store, learner, audio_bytes):
    store.update_profile(learner, {"retain_audio": True})
    record, operation_id = create(store, learner)
    asset, audio_operation = store.add_audio(
        learner, "audio-request-1", record["record_id"], audio_bytes
    )
    store.delete_record(learner, record["record_id"])
    for action in (
        lambda: store.get_record(learner, record["record_id"]),
        lambda: store.get_audio(learner, asset["audio_asset_id"]),
        lambda: store.get_operation(learner, operation_id=operation_id),
        lambda: store.get_operation(learner, operation_id=audio_operation),
    ):
        with pytest.raises(StoreError) as failure:
            action()
        assert failure.value.status_code == 404


def test_deleting_identity_revokes_cookie_and_outstanding_writes(store, learner):
    identity, token = store.bootstrap()
    target = identity["learner_id"]
    record, _ = create(store, target)
    pending = store.begin_operation(target, "external-call-1", "feedback", {})
    create(store, learner, data={"kept": True})
    store.delete_learner(target)
    for action in (
        lambda: store.authenticate(token),
        lambda: store.get_record(target, record["record_id"]),
        lambda: create(store, target),
        lambda: store.complete_operation(
            target, pending.operation["operation_id"], pending.lease_token, result={}
        ),
    ):
        with pytest.raises(StoreError) as failure:
            action()
        assert failure.value.status_code == 401
    assert len(store.list_records(learner)["items"]) == 1
    bootstrap, _ = store.bootstrap(token)
    assert bootstrap["learner_id"] != target
    with sqlite3.connect(store.path) as database:
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []


def test_external_operation_claim_is_durable_and_record_completion_is_atomic(
    store, learner
):
    claim = store.begin_operation(
        learner,
        "external-call-1",
        "analysis",
        {"audio_hash": "digest"},
        session_id="session-1",
    )
    assert claim.acquired and claim.operation["status"] == "pending"
    reopened = LearningStore(store.path)
    duplicate = reopened.begin_operation(
        learner, "external-call-1", "analysis", {"audio_hash": "digest"}
    )
    assert not duplicate.acquired and duplicate.operation == claim.operation
    with pytest.raises(StoreError) as failure:
        reopened.complete_operation(
            learner, claim.operation["operation_id"], "wrong-token", result={}
        )
    assert failure.value.code == "operation_lease_lost"
    result = reopened.complete_operation(
        learner,
        claim.operation["operation_id"],
        claim.lease_token,
        record={
            "kind": "analysis",
            "session_id": "session-1",
            "data": {"analysis": "partial"},
        },
    )
    assert result["status"] == "succeeded"
    assert result["result"]["data"] == {"analysis": "partial"}
    assert reopened.get_record(learner, result["result_id"]) == result["result"]
    assert (
        reopened.begin_operation(
            learner, "external-call-1", "analysis", {"audio_hash": "digest"}
        ).operation
        == result
    )
    with pytest.raises(StoreError) as failure:
        reopened.begin_operation(
            learner, "external-call-1", "analysis", {"audio_hash": "changed"}
        )
    assert failure.value.code == "request_conflict"


def test_concurrent_external_claims_have_one_worker_owner(store, learner):
    barrier = threading.Barrier(4)

    def worker(_):
        barrier.wait(timeout=5)
        return store.begin_operation(learner, "external-call-1", "analysis", {})

    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(worker, range(4)))
    assert sum(claim.acquired for claim in claims) == 1
    assert len({claim.operation["operation_id"] for claim in claims}) == 1


def test_expired_external_claim_becomes_interrupted_without_automatic_retry(tmp_path):
    now = [1000.0]
    store = LearningStore(tmp_path / "lease.sqlite3", clock=lambda: now[0])
    identity, token = store.bootstrap()
    learner = identity["learner_id"]
    claim = store.begin_operation(
        learner, "external-call-1", "analysis", {}, lease_seconds=10
    )
    now[0] += 11
    restored = store.get_operation(
        learner, operation_id=claim.operation["operation_id"]
    )
    assert restored["status"] == "interrupted"
    assert not store.begin_operation(
        learner, "external-call-1", "analysis", {}
    ).acquired
    with pytest.raises(StoreError) as failure:
        store.complete_operation(
            learner, claim.operation["operation_id"], claim.lease_token, result={}
        )
    assert failure.value.code == "operation_lease_lost"
    bootstrap, _ = store.bootstrap(token)
    assert bootstrap["pending_operations"] == [restored]


def test_failed_external_operation_does_not_claim_success_or_retry(store, learner):
    claim = store.begin_operation(learner, "external-call-1", "analysis", {})
    failed = store.fail_operation(
        learner,
        claim.operation["operation_id"],
        claim.lease_token,
        code="provider_failed",
        message="Unavailable",
    )
    assert failed["status"] == "failed" and failed["result"] is None
    assert failed["error"] == {"code": "provider_failed", "message": "Unavailable"}
    assert not store.begin_operation(
        learner, "external-call-1", "analysis", {}
    ).acquired
    assert store.list_records(learner)["items"] == []


def test_failed_completion_rolls_back_record_and_preserves_claim(store, learner):
    claim = store.begin_operation(learner, "external-call-1", "analysis", {})
    with pytest.raises(StoreError):
        store.complete_operation(
            learner,
            claim.operation["operation_id"],
            claim.lease_token,
            record={"kind": "analysis", "data": {"bad": float("inf")}},
        )
    assert store.list_records(learner)["items"] == []
    assert (
        store.get_operation(learner, operation_id=claim.operation["operation_id"])[
            "status"
        ]
        == "pending"
    )


def test_newer_schema_is_rejected_without_mutation(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError, match="newer"):
        LearningStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
