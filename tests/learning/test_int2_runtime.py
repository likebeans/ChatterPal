"""Runtime admission, recovery and revoked workers against real SQLite.

The agreed seams are LearningRuntime.submit/shutdown, LearningStore's public
recovery reads, and the learning HTTP application's lifespan. Provider work is
controlled by events; storage and operation transitions are never mocked.
"""

import asyncio
import json
import threading
import time
import httpx
import pytest
from fastapi import FastAPI

from chatterpal.config.settings import Settings
from chatterpal.learning.runtime import LearningRuntime
from chatterpal.learning.storage import LearningStore, StoreError
from chatterpal.web.routes.learning_records import mount_learning_api
from chatterpal.web.routes.learning_tasks import mount_learning_tasks


class BlockedWork:
    """A synchronous provider boundary that always has an explicit release."""

    def __init__(self, changes=None, error=None):
        self.changes = changes or {}
        self.error = error
        self.started = threading.Event()
        self.release = threading.Event()
        self.thread = None

    def __call__(self, claim):
        self.thread = threading.current_thread()
        self.started.set()
        if not self.release.wait(5):
            raise TimeoutError("Test did not release its controlled provider")
        if self.error is not None:
            raise self.error
        return self.changes


class RuntimeResources:
    def __init__(self, store):
        self.store = store
        self.runtimes = []
        self.workers = []

    def runtime(self, store=None, **options):
        runtime = LearningRuntime(
            store or self.store,
            **{
                "max_workers": 1,
                "max_pending": 2,
                "operation_timeout": 3,
                "shutdown_timeout": 2,
                **options,
            },
        )
        self.runtimes.append(runtime)
        return runtime

    def work(self, changes=None, error=None):
        worker = BlockedWork(changes, error)
        self.workers.append(worker)
        return worker

    @staticmethod
    def stop(runtime, *workers):
        for worker in workers:
            worker.release.set()
        runtime.shutdown()
        # shutdown(wait=False) cannot kill running threads. Joining the actual
        # controlled worker after closing its executor observes its late commit.
        for worker in workers:
            if worker.thread is not None:
                worker.thread.join(timeout=3)
                assert not worker.thread.is_alive(), "Provider thread did not exit"

    def close(self):
        for worker in self.workers:
            worker.release.set()
        for runtime in self.runtimes:
            runtime.shutdown()
        for worker in self.workers:
            if worker.thread is not None:
                worker.thread.join(timeout=3)
                assert not worker.thread.is_alive(), "Leaked controlled provider thread"


@pytest.fixture
def store(tmp_path):
    return LearningStore(tmp_path / "runtime.sqlite3")


@pytest.fixture
def identity(store):
    return store.bootstrap()


@pytest.fixture
def owner(identity):
    return identity[0]["learner_id"]


@pytest.fixture
def session(store, owner):
    return store.create_record(
        owner,
        "runtime-session-start",
        kind="session",
        session_id="runtime-weekend",
        data={"status": "active", "turns": []},
    )[0]


@pytest.fixture
def resources(store):
    resources = RuntimeResources(store)
    try:
        yield resources
    finally:
        resources.close()


def await_operation(store, owner, operation, *, timeout=2):
    deadline = time.monotonic() + timeout
    while True:
        current = store.get_operation(owner, operation_id=operation["operation_id"])
        if current["status"] != "pending":
            return current
        assert time.monotonic() < deadline, current
        threading.Event().wait(0.005)


def submit_turn(runtime, owner, session, request_id, work, *, text="I went hiking."):
    return runtime.submit(
        owner,
        request_id,
        "turn",
        {"text": text},
        work,
        session_id=session["session_id"],
        record_id=session["record_id"],
        expected_revision=session["revision"],
    )


def turn_changes(session, audio_bytes):
    snapshot = {
        "status": "active",
        "turns": [{"role": "user", "text": "I went hiking."}],
        "audio": {"audio_key": "user"},
    }
    return {
        "result": snapshot,
        "result_type": "session",
        "updates": [
            {
                "record_id": session["record_id"],
                "expected_revision": session["revision"],
                "data": snapshot,
            }
        ],
        "creates": [
            {
                "key": "attempt",
                "kind": "attempt",
                "session_id": session["session_id"],
                "parent_record_id": session["record_id"],
                "data": {"transcript": "I went hiking."},
            }
        ],
        "audio": [{"key": "user", "content": audio_bytes, "retain": False}],
    }


def test_full_admission_preserves_original_request_recovery_and_conflicts(
    resources, store, owner
):
    previous = store.begin_operation(
        owner, "runtime-recovered", "hint", {"kind": "starter"}
    )
    completed = store.complete_operation(
        owner,
        previous.operation["operation_id"],
        previous.lease_token,
        result={"hint": "Last weekend, I ..."},
        result_type="hint",
    )
    runtime = resources.runtime(max_workers=1, max_pending=2)
    running_work, queued_work, unexpected_work = (
        resources.work({"result": {"text": text}})
        for text in ("running", "queued", "unexpected")
    )
    running = runtime.submit(
        owner, "runtime-running", "turn", {"text": "first"}, running_work
    )
    assert running_work.started.wait(2)
    queued = runtime.submit(
        owner, "runtime-queued", "turn", {"text": "second"}, queued_work
    )
    assert not queued_work.started.is_set()

    assert (
        runtime.submit(
            owner, "runtime-running", "turn", {"text": "first"}, unexpected_work
        )
        == running
    )
    assert (
        runtime.submit(
            owner, "runtime-queued", "turn", {"text": "second"}, unexpected_work
        )
        == queued
    )
    assert (
        runtime.submit(
            owner, "runtime-recovered", "hint", {"kind": "starter"}, unexpected_work
        )
        == completed
    )
    with pytest.raises(StoreError) as conflict:
        runtime.submit(
            owner, "runtime-running", "turn", {"text": "changed"}, unexpected_work
        )
    assert (conflict.value.code, conflict.value.status_code) == (
        "request_conflict",
        409,
    )
    with pytest.raises(StoreError) as busy:
        runtime.submit(
            owner, "runtime-rejected", "turn", {"text": "third"}, unexpected_work
        )
    assert (busy.value.code, busy.value.status_code) == ("runtime_busy", 503)
    with pytest.raises(StoreError) as absent:
        store.get_operation(owner, request_id="runtime-rejected", action="turn")
    assert absent.value.status_code == 404
    assert not unexpected_work.started.is_set()

    running_work.release.set()
    assert queued_work.started.wait(2)
    assert await_operation(store, owner, running)["status"] == "succeeded"
    third_work = resources.work({"result": {"text": "third"}})
    admitted = runtime.submit(
        owner, "runtime-rejected", "turn", {"text": "third"}, third_work
    )
    assert admitted["status"] == "pending"
    assert not third_work.started.is_set()
    resources.stop(runtime, running_work, queued_work, third_work)
    assert await_operation(store, owner, queued)["status"] == "succeeded"
    assert await_operation(store, owner, admitted)["status"] == "succeeded"
    assert not unexpected_work.started.is_set()


@pytest.mark.parametrize("revocation", ["timeout", "shutdown"])
@pytest.mark.parametrize("late_error", [False, True], ids=["late-result", "late-error"])
def test_revoked_worker_cannot_commit_or_replace_interrupted_status(
    resources, store, owner, session, audio_bytes, revocation, late_error
):
    runtime = resources.runtime(
        max_pending=1,
        operation_timeout=0.1 if revocation == "timeout" else 3,
        shutdown_timeout=0,
    )
    work = resources.work(
        turn_changes(session, audio_bytes),
        error=RuntimeError("Late provider failure") if late_error else None,
    )
    pending = submit_turn(runtime, owner, session, "runtime-revoked-turn", work)
    assert work.started.wait(2)
    if revocation == "shutdown":
        runtime.shutdown()
    interrupted = await_operation(store, owner, pending)
    assert interrupted["status"] == "interrupted"
    assert interrupted["error"]["code"] == "operation_interrupted"
    assert store.current_pending(owner, session["session_id"]) == []
    assert work.thread.is_alive(), "The synchronous provider is still blocked"
    if revocation == "timeout":
        replay_work = resources.work()
        assert (
            submit_turn(runtime, owner, session, "runtime-revoked-turn", replay_work)
            == interrupted
        )
        with pytest.raises(StoreError) as busy:
            submit_turn(runtime, owner, session, "runtime-next-turn", replay_work)
        assert busy.value.code == "runtime_busy"
        assert not replay_work.started.is_set()

    resources.stop(runtime, work)
    assert (
        store.get_operation(owner, operation_id=pending["operation_id"]) == interrupted
    )
    assert store.get_record(owner, session["record_id"]) == session
    assert store.list_records(owner)["items"] == [session]

    reopened = LearningStore(store.path)
    recovered_runtime = resources.runtime(reopened)
    replay_work = resources.work()
    assert (
        submit_turn(
            recovered_runtime, owner, session, "runtime-revoked-turn", replay_work
        )
        == interrupted
    )
    assert not replay_work.started.is_set()


def test_queued_operation_times_out_without_calling_provider(
    resources, store, owner, session, audio_bytes
):
    runtime = resources.runtime(operation_timeout=0.15)
    blocker, queued_work = resources.work(), resources.work(
        turn_changes(session, audio_bytes)
    )
    running = runtime.submit(owner, "runtime-queue-blocker", "hint", {}, blocker)
    assert blocker.started.wait(2)
    queued = submit_turn(runtime, owner, session, "runtime-queued-timeout", queued_work)
    interrupted = await_operation(store, owner, queued)
    assert interrupted["status"] == "interrupted"
    assert await_operation(store, owner, running)["status"] == "interrupted"
    assert not queued_work.started.is_set()
    assert blocker.thread.is_alive()

    resources.stop(runtime, blocker, queued_work)
    assert not queued_work.started.is_set()
    assert (
        store.get_operation(owner, operation_id=queued["operation_id"]) == interrupted
    )
    assert store.get_record(owner, session["record_id"]) == session


def test_shutdown_interrupts_own_running_and_queued_work_only(
    resources, store, owner, session, audio_bytes
):
    first = resources.runtime(shutdown_timeout=0)
    second_store = LearningStore(store.path)
    second = resources.runtime(second_store, shutdown_timeout=0)
    running_work = resources.work(turn_changes(session, audio_bytes))
    queued_work = resources.work(
        {"record": {"kind": "attempt", "data": {"queued": True}}}
    )
    foreign_work = resources.work(
        {"record": {"kind": "attempt", "data": {"other": True}}}
    )
    running = submit_turn(
        first, owner, session, "runtime-shutdown-running", running_work
    )
    assert running_work.started.wait(2)
    queued = first.submit(owner, "runtime-shutdown-queued", "attempt", {}, queued_work)
    foreign = second.submit(owner, "runtime-other-worker", "attempt", {}, foreign_work)
    assert foreign_work.started.wait(2)
    unclaimed = second_store.begin_operation(
        owner, "runtime-durable-pending", "hint", {}
    )

    first.shutdown()
    assert (
        store.get_operation(owner, operation_id=running["operation_id"])["status"]
        == "interrupted"
    )
    assert (
        store.get_operation(owner, operation_id=queued["operation_id"])["status"]
        == "interrupted"
    )
    assert (
        second_store.get_operation(owner, operation_id=foreign["operation_id"])[
            "status"
        ]
        == "pending"
    )
    assert (
        second_store.get_operation(
            owner, operation_id=unclaimed.operation["operation_id"]
        )["status"]
        == "pending"
    )
    with pytest.raises(StoreError) as stopping:
        first.submit(owner, "runtime-after-shutdown", "hint", {}, resources.work())
    assert (stopping.value.code, stopping.value.status_code) == (
        "runtime_stopping",
        503,
    )
    resources.stop(first, running_work, queued_work)
    assert not queued_work.started.is_set()
    foreign_work.release.set()
    assert await_operation(second_store, owner, foreign)["status"] == "succeeded"
    assert store.list_records(owner, kind="attempt")["items"][0]["data"] == {
        "other": True
    }
    assert len(store.list_records(owner, kind="attempt")["items"]) == 1
    assert store.get_record(owner, session["record_id"]) == session


@pytest.mark.parametrize("expected_error", ["provider_failed", "no_speech"])
def test_provider_failure_is_durable_and_does_not_replay_paid_work(
    resources, store, owner, session, expected_error, caplog
):
    runtime = resources.runtime()
    provider_detail = "private transcript and provider response"
    error = (
        RuntimeError(provider_detail)
        if expected_error == "provider_failed"
        else StoreError("no_speech", "No usable speech", 422)
    )
    work = resources.work(error=error)
    pending = submit_turn(runtime, owner, session, "runtime-provider-failure", work)
    assert work.started.wait(2)
    work.release.set()
    failed = await_operation(store, owner, pending)
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == expected_error
    assert failed["result"] is None
    assert provider_detail not in failed["error"]["message"]
    assert provider_detail not in caplog.text
    assert store.get_record(owner, session["record_id"]) == session
    assert store.current_pending(owner, session["session_id"]) == []

    replay_work = resources.work()
    assert (
        submit_turn(runtime, owner, session, "runtime-provider-failure", replay_work)
        == failed
    )
    reopened = LearningStore(store.path)
    recovered = resources.runtime(reopened)
    assert (
        submit_turn(recovered, owner, session, "runtime-provider-failure", replay_work)
        == failed
    )
    assert not replay_work.started.is_set()
    retry = resources.work(
        {"result": {"hint": "Last weekend, I ..."}, "result_type": "hint"}
    )
    retry.release.set()
    new_operation = submit_turn(
        recovered, owner, session, "runtime-explicit-retry", retry
    )
    assert await_operation(reopened, owner, new_operation)["status"] == "succeeded"


@pytest.mark.parametrize("deleted", ["source", "owner"])
def test_deleted_owner_or_source_cannot_be_restored_by_running_or_recovered_work(
    resources, store, owner, identity, session, audio_bytes, deleted
):
    runtime = resources.runtime()
    work = resources.work(turn_changes(session, audio_bytes))
    pending = submit_turn(runtime, owner, session, "runtime-deleted-turn", work)
    assert work.started.wait(2)
    if deleted == "owner":
        store.delete_learner(owner)
    else:
        store.delete_record(owner, session["record_id"])
    reopened = LearningStore(store.path)
    recovered_runtime = resources.runtime(reopened)
    replay_work = resources.work()
    with pytest.raises(StoreError) as recovery:
        submit_turn(
            recovered_runtime, owner, session, "runtime-deleted-turn", replay_work
        )
    assert recovery.value.status_code == (401 if deleted == "owner" else 404)
    assert not replay_work.started.is_set()

    restored_identity, restored_token = reopened.bootstrap(identity[1])
    restored_owner = restored_identity["learner_id"]
    assert (restored_owner == owner) is (deleted == "source")
    if deleted == "owner":
        assert restored_token != identity[1]
    replacement = reopened.create_record(
        restored_owner,
        "runtime-replacement-session",
        kind="session",
        session_id=session["session_id"],
        data={"status": "active", "turns": []},
    )[0]
    resources.stop(runtime, work)
    assert reopened.list_records(restored_owner)["items"] == [replacement]
    with pytest.raises(StoreError) as missing:
        reopened.get_operation(restored_owner, operation_id=pending["operation_id"])
    assert missing.value.status_code == 404
    with reopened.export_data(restored_owner) as stream:
        exported = json.load(stream)
    assert len(exported["records"]) == 1
    assert exported["records"][0]["audio_assets"] == []
    assert all(
        operation["operation_id"] != pending["operation_id"]
        for operation in exported["operations"]
    )


def test_cancelled_provider_becomes_interrupted_and_releases_session_claim(
    resources, store, owner, session
):
    runtime = resources.runtime(operation_timeout=0.1)
    work = resources.work(error=asyncio.CancelledError())
    pending = submit_turn(runtime, owner, session, "runtime-cancelled-provider", work)
    assert work.started.wait(2)
    work.release.set()
    try:
        terminal = await_operation(store, owner, pending, timeout=0.4)
    except AssertionError:
        # Distinguish the real leaked claim from a delayed timer: even after the
        # provider thread and runtime are gone, the durable row stays pending.
        resources.stop(runtime, work)
        current = store.get_operation(owner, operation_id=pending["operation_id"])
        pytest.fail(
            f"Cancelled provider has no worker after shutdown but operation remains {current}; "
            f"session claims: {store.current_pending(owner, session['session_id'])}"
        )
    assert terminal["status"] == "interrupted"
    assert store.current_pending(owner, session["session_id"]) == []
    assert store.get_record(owner, session["record_id"]) == session


class RuntimeProvider:
    def __init__(self, work, audio_bytes):
        self.work = work
        self.audio_bytes = audio_bytes

    def recognize(self, audio):
        return "I went hiking."

    def reply(self, messages):
        self.work(None)
        return '{"reply": "Who were you with?", "suggestions": []}'

    def synthesize(self, text):
        return self.audio_bytes


@pytest.mark.asyncio
async def test_http_lifespan_waits_off_loop_and_interrupts_late_provider_result(
    resources, store, audio_bytes
):
    app = FastAPI()
    settings = Settings(
        _env_file=None,
        learning_db_path=str(store.path),
        learning_max_workers=1,
        learning_max_pending=2,
        learning_operation_timeout=3,
        learning_shutdown_timeout=0.2,
    )
    work = resources.work()
    mount_learning_api(app, settings, store=store, allow_record_writes=False)
    mount_learning_tasks(app, settings, RuntimeProvider(work, audio_bytes))
    loop = asyncio.get_running_loop()
    observed_lags = []
    stop_ticks = asyncio.Event()

    async def heartbeat():
        while not stop_ticks.is_set():
            before = loop.time()
            await asyncio.sleep(0.005)
            observed_lags.append(loop.time() - before)

    ticker = asyncio.create_task(heartbeat())
    closing = None
    lifespan = app.router.lifespan_context(app)
    entered = False
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await lifespan.__aenter__()
            entered = True
            identity = (await client.post("/api/learning/bootstrap")).json()
            response = await client.post(
                "/api/learning/sessions",
                json={
                    "request_id": "runtime-http-start",
                    "task_id": "weekend",
                    "content_version": "1",
                },
            )
            assert response.status_code == 202, response.text
            operation = response.json()
            deadline = loop.time() + 2
            while operation["status"] == "pending":
                assert loop.time() < deadline, operation
                await asyncio.sleep(0.005)
                polled = await client.get(
                    f"/api/learning/operations/{operation['operation_id']}"
                )
                assert polled.status_code == 200, polled.text
                operation = polled.json()
            assert operation["status"] == "succeeded", operation
            result = operation["result"]
            session = result["data"] if operation["result_type"] == "record" else result
            response = await client.post(
                f"/api/learning/sessions/{session['session_id']}/turns",
                json={
                    "request_id": "runtime-http-slow-turn",
                    "expected_revision": session["revision"],
                    "text": "I went hiking.",
                },
            )
            assert response.status_code == 202, response.text
            pending = response.json()
            assert await asyncio.to_thread(work.started.wait, 2)
            runtime = app.state.learning_service().runtime
            resources.runtimes.append(runtime)
            await asyncio.sleep(0)
            observed_lags.clear()
            closing = asyncio.create_task(lifespan.__aexit__(None, None, None))
            await asyncio.wait_for(asyncio.shield(closing), timeout=2)
            await asyncio.sleep(0.01)
            assert observed_lags and max(observed_lags) < 0.1, observed_lags
            assert work.thread.is_alive()
            recovered = await client.get(
                f"/api/learning/requests/{pending['request_id']}"
            )
            assert recovered.status_code == 200, recovered.text
            interrupted = recovered.json()
            assert interrupted["status"] == "interrupted"
            await asyncio.to_thread(resources.stop, runtime, work)
            assert (
                store.get_operation(
                    identity["learner_id"], operation_id=pending["operation_id"]
                )
                == interrupted
            )
            restored = await client.get(
                f"/api/learning/sessions/{session['session_id']}"
            )
            assert restored.status_code == 200, restored.text
            assert restored.json() == session
    finally:
        work.release.set()
        try:
            if entered and closing is None:
                closing = asyncio.create_task(lifespan.__aexit__(None, None, None))
            if closing is not None:
                await asyncio.wait_for(asyncio.shield(closing), timeout=3)
        finally:
            stop_ticks.set()
            await ticker
