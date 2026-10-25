"""Bounded in-process workers for durable, recoverable learning operations."""

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any, Callable

from .storage import LearningStore, StoreError
from .storage.sqlite import OperationClaim

logger = logging.getLogger(__name__)


class LearningRuntime:
    def __init__(
        self,
        repository: LearningStore,
        *,
        max_workers: int = 2,
        max_pending: int = 8,
        operation_timeout: float = 120,
        shutdown_timeout: float = 10,
    ):
        if not 1 <= max_workers <= max_pending or operation_timeout <= 0:
            raise ValueError("Learning worker limits must be positive and bounded")
        self.repository = repository
        self.max_pending = max_pending
        self.operation_timeout = operation_timeout
        self.shutdown_timeout = shutdown_timeout
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="learning"
        )
        self._lock = threading.RLock()
        self._futures: dict[str, Future] = {}
        self._timers: dict[str, threading.Timer] = {}
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
                raise StoreError(
                    "runtime_stopping", "学习服务正在关闭，请稍后重连。", 503
                )
            # Existing requests must remain recoverable even when admission is full.
            if len(self._futures) >= self.max_pending:
                try:
                    self.repository.get_operation(
                        owner, request_id=request_id, action=action
                    )
                except StoreError as error:
                    if error.status_code != 404:
                        raise
                    raise StoreError(
                        "runtime_busy", "当前练习正在排队，请稍后用原请求重试。", 503
                    ) from error
                # begin_operation below still verifies the input fingerprint.
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
                return claim.operation
            operation_id = claim.operation["operation_id"]
            try:
                future = self._executor.submit(self._execute, owner, claim, work)
                self._futures[operation_id] = future
                timer = threading.Timer(
                    self.operation_timeout, self._interrupt, args=(operation_id,)
                )
                timer.daemon = True
                self._timers[operation_id] = timer
                timer.start()
                future.add_done_callback(lambda done: self._release(operation_id))
            except BaseException:
                self.repository.interrupt_operations([operation_id])
                raise
            # A small operation may already have committed. Both states are valid.
            return self.repository.get_operation(owner, operation_id=operation_id)

    def _execute(self, owner, claim, work):
        try:
            changes = work(claim)
            return self.repository.complete_operation(
                owner,
                claim.operation["operation_id"],
                claim.lease_token,
                **changes,
            )
        except Exception as error:
            # Log identifiers and error class, never transcript/provider responses.
            logger.warning(
                "Learning operation %s failed (%s)",
                claim.operation["operation_id"],
                type(error).__name__,
            )
            code = error.code if isinstance(error, StoreError) else "provider_failed"
            message = (
                error.message
                if isinstance(error, StoreError)
                else "这次处理未完成，已保存的记录仍保留。请核对后重新提交。"
            )
            try:
                return self.repository.fail_operation(
                    owner,
                    claim.operation["operation_id"],
                    claim.lease_token,
                    code=code,
                    message=message,
                )
            except StoreError:
                # A deletion, expired lease or timeout already revoked this worker.
                return None
        except BaseException:
            # Cancellation must revoke the durable claim before the done callback
            # drops this worker and its timer. Preserve process-control exceptions.
            self.repository.interrupt_operations([claim.operation["operation_id"]])
            raise

    def _interrupt(self, operation_id):
        with self._lock:
            future = self._futures.get(operation_id)
            if future is not None and not future.done():
                self.repository.interrupt_operations([operation_id])
                future.cancel()

    def _release(self, operation_id):
        with self._lock:
            self._futures.pop(operation_id, None)
            timer = self._timers.pop(operation_id, None)
            if timer is not None:
                timer.cancel()

    def shutdown(self):
        with self._lock:
            self._closed = True
            futures = tuple(self._futures.values())
        if futures:
            wait(futures, timeout=self.shutdown_timeout)
        with self._lock:
            remaining = list(self._futures)
            if remaining:
                self.repository.interrupt_operations(remaining)
            for timer in self._timers.values():
                timer.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
