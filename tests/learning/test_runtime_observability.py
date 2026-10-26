"""Bounded, private stage timing over real SQLite operation transitions."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from types import SimpleNamespace

import pytest

from chatterpal.learning.adapters import ExistingProviders, ProviderError
from chatterpal.learning.runtime import LearningRuntime
from chatterpal.learning.storage import LearningStore, StoreError


def terminal(store, owner, operation):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        result = store.get_operation(owner, operation_id=operation["operation_id"])
        if result["status"] != "pending":
            return result
        Event().wait(0.005)
    pytest.fail("Operation did not reach a durable terminal state")


def idle(runtime):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        if runtime.snapshot()["pending"] == 0:
            return
        Event().wait(0.005)
    pytest.fail("Actual provider worker did not release capacity")


def test_metrics_bound_samples_and_labels_across_concurrent_writers():
    from chatterpal.learning.observability import RuntimeMetrics

    metrics = RuntimeMetrics(max_samples=7)

    def record(index):
        metrics.observe("llm", 0.01, status="succeeded")
        metrics.observe(
            "untrusted stage with secret", 1, status="private status", error="secret"
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, range(500)))
    snapshot = metrics.snapshot()
    assert len(snapshot["stages"]) == 1
    llm = snapshot["stages"]["llm"]
    assert llm["count"] == 500
    assert llm["sample_count"] == 7
    assert llm["p50_seconds"] == pytest.approx(0.01)
    assert llm["p95_seconds"] == pytest.approx(0.01)
    assert llm["statuses"] == {"succeeded": 500}
    assert "secret" not in str(snapshot)
    assert snapshot["max_samples"] == 7
    snapshot["stages"]["llm"]["statuses"]["succeeded"] = -1
    assert metrics.snapshot()["stages"]["llm"]["statuses"]["succeeded"] == 500


def test_adapter_timings_capture_failures_without_provider_payload_or_error_text():
    from chatterpal.learning.observability import RuntimeMetrics

    secret = "private transcript, response, credential"
    metrics = RuntimeMetrics(max_samples=2)

    def fail(value):
        raise RuntimeError(secret)

    provider = ExistingProviders(
        asr=SimpleNamespace(recognize=lambda audio: secret),
        llm=SimpleNamespace(chat=fail),
        tts=SimpleNamespace(synthesize=lambda text: b"audio"),
        metrics=metrics,
    )
    assert provider.recognize(b"input") == secret
    with pytest.raises(ProviderError):
        provider.reply([{"role": "user", "content": secret}])
    assert provider.synthesize(secret) == b"audio"
    snapshot = metrics.snapshot()
    assert secret not in str(snapshot)
    assert snapshot["stages"]["asr"]["statuses"] == {"succeeded": 1}
    assert snapshot["stages"]["llm"]["statuses"] == {"failed": 1}
    assert snapshot["stages"]["llm"]["errors"] == {"ProviderError": 1}
    assert snapshot["stages"]["tts"]["count"] == 1


@pytest.mark.parametrize("late_failure", [False, True])
def test_timeout_snapshot_keeps_running_worker_until_actual_completion(
    tmp_path, late_failure
):
    store = LearningStore(tmp_path / "observability.sqlite3")
    owner = store.bootstrap()[0]["learner_id"]
    runtime = LearningRuntime(
        store, max_workers=1, max_pending=1, operation_timeout=0.08, shutdown_timeout=0
    )
    started, release = Event(), Event()

    def work(claim):
        started.set()
        assert release.wait(3)
        if late_failure:
            raise RuntimeError("private provider response")
        return {"result": {"answer": "not published"}}

    try:
        operation = runtime.submit(owner, "observe-timeout", "turn", {}, work)
        assert started.wait(2)
        assert terminal(store, owner, operation)["status"] == "interrupted"
        snapshot = runtime.snapshot()
        assert (
            snapshot["pending"],
            snapshot["running"],
            snapshot["queued"],
            snapshot["running_after_interrupt"],
        ) == (1, 1, 0, 1)
        assert snapshot["metrics"]["stages"]["operation_total"]["statuses"] == {
            "interrupted": 1
        }
        with pytest.raises(StoreError) as error:
            runtime.submit(owner, "observe-rejected", "turn", {}, lambda claim: {})
        assert error.value.code == "runtime_busy"
        assert (
            runtime.submit(owner, "observe-timeout", "turn", {}, work)["status"]
            == "interrupted"
        )
    finally:
        release.set()
        runtime.shutdown()
    idle(runtime)
    snapshot = runtime.snapshot()
    assert snapshot["running_after_interrupt"] == 0
    assert snapshot["metrics"]["stages"]["operation_total"]["count"] == 1
    assert snapshot["metrics"]["stages"]["worker_total"]["count"] == 1
    assert "private provider response" not in str(snapshot)
    assert (
        store.get_operation(owner, operation_id=operation["operation_id"])["status"]
        == "interrupted"
    )


def test_queue_time_and_runtime_context_include_adapted_stages(tmp_path):
    store = LearningStore(tmp_path / "observability.sqlite3")
    owner = store.bootstrap()[0]["learner_id"]
    runtime = LearningRuntime(store, max_workers=1, max_pending=2, shutdown_timeout=1)
    started, release = Event(), Event()
    provider = ExistingProviders(llm=SimpleNamespace(chat=lambda messages: "reply"))

    def work(claim):
        provider.reply([{"role": "user", "content": "private"}])
        return {"result": {"done": True}}

    def block(claim):
        started.set()
        assert release.wait(3)
        return work(claim)

    try:
        first = runtime.submit(owner, "observe-first", "turn", {}, block)
        assert started.wait(2)
        second = runtime.submit(owner, "observe-second", "turn", {}, work)
        snapshot = runtime.snapshot()
        assert (snapshot["pending"], snapshot["running"], snapshot["queued"]) == (
            2,
            1,
            1,
        )
        Event().wait(0.02)
        release.set()
        assert terminal(store, owner, first)["status"] == "succeeded"
        assert terminal(store, owner, second)["status"] == "succeeded"
        idle(runtime)
        snapshot = runtime.snapshot()
        queue = snapshot["metrics"]["stages"]["operation_queue"]
        assert queue["count"] == 2
        assert queue["max_seconds"] >= 0.02
        assert snapshot["metrics"]["stages"]["llm"]["count"] == 2
        assert snapshot["metrics"]["stages"]["operation_total"]["statuses"] == {
            "succeeded": 2
        }
        assert "private" not in str(snapshot)
    finally:
        release.set()
        runtime.shutdown()


def test_revoked_claim_waiting_to_enter_worker_never_calls_provider(
    tmp_path, monkeypatch
):
    store = LearningStore(tmp_path / "queued-race.sqlite3")
    owner = store.bootstrap()[0]["learner_id"]
    runtime = LearningRuntime(store, max_workers=1, max_pending=1, operation_timeout=3)
    entered, release, called = Event(), Event(), Event()
    execute = runtime._execute

    def held_execute(*args):
        entered.set()
        assert release.wait(3)
        return execute(*args)

    monkeypatch.setattr(runtime, "_execute", held_execute)

    def work(claim):
        called.set()
        return {"result": {"done": True}}

    try:
        operation = runtime.submit(owner, "queued-race", "turn", {}, work)
        assert entered.wait(2)
        runtime._interrupt(operation["operation_id"])
        assert terminal(store, owner, operation)["status"] == "interrupted"
        assert runtime.snapshot()["pending"] == 1
        release.set()
        idle(runtime)
        assert not called.is_set()
        snapshot = runtime.snapshot()["metrics"]["stages"]
        assert snapshot["operation_queue"]["count"] == 1
        assert snapshot["operation_total"]["statuses"] == {"interrupted": 1}
    finally:
        release.set()
        runtime.shutdown()


@pytest.mark.parametrize(
    "options",
    [
        {"max_workers": True},
        {"max_pending": 1.5},
        {"max_workers": 0},
        {"operation_timeout": float("nan")},
        {"operation_timeout": True},
        {"shutdown_timeout": float("inf")},
        {"shutdown_timeout": -1},
    ],
)
def test_invalid_runtime_limits_are_rejected(tmp_path, options):
    store = LearningStore(tmp_path / "limits.sqlite3")
    with pytest.raises(ValueError):
        LearningRuntime(store, **options)


def test_shared_adapter_context_is_isolated_between_runtimes_and_restored(tmp_path):
    from chatterpal.learning.observability import RuntimeMetrics, metrics_context

    store = LearningStore(tmp_path / "isolation.sqlite3")
    owner = store.bootstrap()[0]["learner_id"]
    first = LearningRuntime(store, max_workers=1, max_pending=1)
    second = LearningRuntime(store, max_workers=1, max_pending=1)
    first_started, second_started, release = Event(), Event(), Event()
    provider = ExistingProviders(llm=SimpleNamespace(chat=lambda messages: "reply"))

    def work(started, calls):
        def execute(claim):
            started.set()
            assert release.wait(3)
            for _ in range(calls):
                provider.reply([{"role": "user", "content": "private"}])
            return {"result": {"done": True}}

        return execute

    try:
        one = first.submit(owner, "isolation-first", "turn", {}, work(first_started, 1))
        two = second.submit(
            owner, "isolation-second", "turn", {}, work(second_started, 2)
        )
        assert first_started.wait(2) and second_started.wait(2)
        release.set()
        assert terminal(store, owner, one)["status"] == "succeeded"
        assert terminal(store, owner, two)["status"] == "succeeded"
        idle(first)
        idle(second)
        assert first.snapshot()["metrics"]["stages"]["llm"]["count"] == 1
        assert second.snapshot()["metrics"]["stages"]["llm"]["count"] == 2
        assert provider.snapshot()["stages"] == {}
        provider.reply([{"role": "user", "content": "outside worker"}])
        assert provider.snapshot()["stages"]["llm"]["count"] == 1

        outer, inner = RuntimeMetrics(), RuntimeMetrics()
        with metrics_context(outer):
            with pytest.raises(asyncio.CancelledError):
                with metrics_context(inner):
                    provider.reply([{"role": "user", "content": "inner"}])
                    raise asyncio.CancelledError()
            provider.reply([{"role": "user", "content": "outer restored"}])
        assert inner.snapshot()["stages"]["llm"]["count"] == 1
        assert outer.snapshot()["stages"]["llm"]["count"] == 1
        assert provider.snapshot()["stages"]["llm"]["count"] == 1
    finally:
        release.set()
        first.shutdown()
        second.shutdown()


def test_cancelled_worker_is_measured_as_interrupted_and_releases_slot(tmp_path):
    store = LearningStore(tmp_path / "observability.sqlite3")
    owner = store.bootstrap()[0]["learner_id"]
    runtime = LearningRuntime(store, max_pending=2)

    def cancel(claim):
        raise asyncio.CancelledError("private cancellation detail")

    try:
        operation = runtime.submit(owner, "observe-cancel", "turn", {}, cancel)
        assert terminal(store, owner, operation)["status"] == "interrupted"
        idle(runtime)
        assert runtime.snapshot()["metrics"]["stages"]["operation_total"][
            "statuses"
        ] == {"interrupted": 1}
    finally:
        runtime.shutdown()
