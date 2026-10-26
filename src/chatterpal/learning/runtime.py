"""Bounded in-process workers for durable, recoverable learning operations."""

from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
import logging
import math
import threading
from time import perf_counter
from typing import Any, Callable

from .observability import RuntimeMetrics, metrics_context, safe_error_type
from .storage import LearningStore, StoreError
from .storage.sqlite import OperationClaim

logger = logging.getLogger(__name__)


@dataclass
class _WorkTiming:
    submitted: float
    started: float | None = None
    terminal_status: str | None = None


class LearningRuntime:
    def __init__(
        self,
        repository: LearningStore,
        *,
        max_workers: int = 2,
        max_pending: int = 8,
        operation_timeout: float = 120,
        shutdown_timeout: float = 10,
        metrics: RuntimeMetrics | None = None,
    ):
        if (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or isinstance(max_pending, bool)
            or not isinstance(max_pending, int)
            or not 1 <= max_workers <= max_pending
            or isinstance(operation_timeout, bool)
            or not isinstance(operation_timeout, (int, float))
            or not math.isfinite(operation_timeout)
            or operation_timeout <= 0
            or isinstance(shutdown_timeout, bool)
            or not isinstance(shutdown_timeout, (int, float))
            or not math.isfinite(shutdown_timeout)
            or shutdown_timeout < 0
        ):
            raise ValueError("Learning worker limits must be positive and bounded")
        self.repository = repository
        self.max_workers = max_workers
        self.max_pending = max_pending
        self.operation_timeout = operation_timeout
        self.shutdown_timeout = shutdown_timeout
        self.metrics = metrics if metrics is not None else RuntimeMetrics()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="learning"
        )
        self._lock = threading.RLock()
        self._futures: dict[str, Future] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._timings: dict[str, _WorkTiming] = {}
        self._closed = False

    def submit(
        self,
        owner: str,
        request_id: str,
        action: str,
        payload: Any,
        work: Callable[[OperationClaim], dict[str, Any]],
        *,
        session_id: str | None = None,
        record_id: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                self.metrics.increment("rejected_stopping")
                raise StoreError(
                    "runtime_stopping", "学习服务正在关闭，请稍后重连。", 503
                )
            # Late synchronous workers retain their slot until their future ends.
            # Existing requests remain recoverable even when admission is full.
            if len(self._futures) >= self.max_pending:
                try:
                    self.repository.get_operation(
                        owner, request_id=request_id, action=action
                    )
                except StoreError as error:
                    if error.status_code != 404:
                        raise
                    self.metrics.increment("rejected_busy")
                    raise StoreError(
                        "runtime_busy", "当前练习正在排队，请稍后用原请求重试。", 503
                    ) from error
                # begin_operation below still verifies the input fingerprint.
            submitted = perf_counter()
            claim = self.repository.begin_operation(
                owner,
                request_id,
                action,
                payload,
                session_id=session_id,
                record_id=record_id,
                expected_revision=expected_revision,
                exclusive_session=session_id is not None,
                lease_seconds=min(3600, max(1, int(self.operation_timeout) + 2)),
            )
            if not claim.acquired:
                self.metrics.increment("recovered")
                return claim.operation
            operation_id = claim.operation["operation_id"]
            timing = _WorkTiming(submitted)
            self._timings[operation_id] = timing
            try:
                future = self._executor.submit(
                    self._execute, owner, claim, work, timing
                )
                self._futures[operation_id] = future
                timer = threading.Timer(
                    self.operation_timeout, self._interrupt, args=(operation_id,)
                )
                timer.daemon = True
                self._timers[operation_id] = timer
                timer.start()
                future.add_done_callback(lambda done: self._release(operation_id))
                self.metrics.increment("admitted")
            except BaseException as error:
                self.repository.interrupt_operations([operation_id])
                self._finish(timing, "interrupted", error)
                # An executor rejection has no live worker or done callback.
                if operation_id not in self._futures:
                    self._timings.pop(operation_id, None)
                else:
                    future.add_done_callback(lambda done: self._release(operation_id))
                    future.cancel()
                raise
            # A small operation may already have committed. Both states are valid.
            return self.repository.get_operation(owner, operation_id=operation_id)

    def _finish(self, timing, status, error=None):
        """Record one terminal operation outcome; revocation can win this race."""
        with self._lock:
            if timing.terminal_status is not None:
                return
            timing.terminal_status = status
            elapsed = perf_counter() - timing.submitted
            if timing.started is None:
                self.metrics.observe(
                    "operation_queue", elapsed, status=status, error=error
                )
            self.metrics.observe("operation_total", elapsed, status=status, error=error)

    def _execute(self, owner, claim, work, timing):
        with self._lock:
            # A timeout can revoke the queued claim just after the executor marks
            # its future running. Do not call a provider for that revoked claim.
            if timing.terminal_status is not None:
                return None
            timing.started = perf_counter()
            self.metrics.observe("operation_queue", timing.started - timing.submitted)
        try:
            with metrics_context(self.metrics):
                changes = work(claim)
            completed = self.repository.complete_operation(
                owner,
                claim.operation["operation_id"],
                claim.lease_token,
                **changes,
            )
            self._finish(timing, "succeeded")
            return completed
        except Exception as error:
            # Log a fixed class label, never transcript or provider responses.
            logger.warning("Learning operation failed (%s)", safe_error_type(error))
            code = error.code if isinstance(error, StoreError) else "provider_failed"
            message = (
                error.message
                if isinstance(error, StoreError)
                else "这次处理未完成，已保存的记录仍保留。请核对后重新提交。"
            )
            try:
                failed = self.repository.fail_operation(
                    owner,
                    claim.operation["operation_id"],
                    claim.lease_token,
                    code=code,
                    message=message,
                )
                self._finish(timing, "failed", error)
                return failed
            except StoreError:
                # Deletion, expired lease or a timeout already revoked the worker.
                self._finish(timing, "interrupted", error)
                return None
        except BaseException as error:
            # Cancellation must revoke the durable claim before the done callback
            # drops this worker and its timer. Preserve process-control exceptions.
            self.repository.interrupt_operations([claim.operation["operation_id"]])
            self._finish(timing, "interrupted", error)
            raise
        finally:
            self.metrics.observe(
                "worker_total",
                perf_counter() - timing.started,
                status=timing.terminal_status or "failed",
            )

    def _interrupt(self, operation_id):
        with self._lock:
            future = self._futures.get(operation_id)
            if future is not None and not future.done():
                if self.repository.interrupt_operations([operation_id]):
                    self._finish(self._timings[operation_id], "interrupted")
                future.cancel()

    def _release(self, operation_id):
        with self._lock:
            self._futures.pop(operation_id, None)
            self._timings.pop(operation_id, None)
            timer = self._timers.pop(operation_id, None)
            if timer is not None:
                timer.cancel()

    def snapshot(self) -> dict[str, Any]:
        """Local status only: no provider calls, identities or operation payloads."""
        with self._lock:
            pending = [
                self._timings[operation_id]
                for operation_id, future in self._futures.items()
                if not future.done()
            ]
            running = sum(timing.started is not None for timing in pending)
            return {
                "closed": self._closed,
                "max_workers": self.max_workers,
                "max_pending": self.max_pending,
                "pending": len(pending),
                "running": running,
                "queued": len(pending) - running,
                "running_after_interrupt": sum(
                    timing.started is not None
                    and timing.terminal_status == "interrupted"
                    for timing in pending
                ),
                "metrics": self.metrics.snapshot(),
            }

    def shutdown(self):
        with self._lock:
            self._closed = True
            futures = tuple(self._futures.values())
        if futures:
            wait(futures, timeout=self.shutdown_timeout)
        with self._lock:
            for operation_id in list(self._futures):
                if self.repository.interrupt_operations([operation_id]):
                    self._finish(self._timings[operation_id], "interrupted")
            for timer in self._timers.values():
                timer.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
