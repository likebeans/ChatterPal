"""Synchronous provider and repository boundaries used outside HTTP handlers."""

from typing import Any, Protocol

from .storage.sqlite import OperationClaim


class LearningProvider(Protocol):
    def recognize(self, audio: bytes) -> str: ...

    def reply(self, messages: list[dict[str, str]]) -> str: ...

    def synthesize(self, text: str) -> bytes: ...


class LearningRepository(Protocol):
    def get_profile(self, learner_id: str) -> dict[str, Any]: ...

    def list_records(self, learner_id: str, **filters: Any) -> dict[str, Any]: ...

    def get_record(self, learner_id: str, record_id: str) -> dict[str, Any]: ...

    def begin_operation(
        self,
        learner_id: str,
        request_id: str,
        action: str,
        payload: Any,
        **options: Any,
    ) -> OperationClaim: ...

    def complete_operation(
        self, learner_id: str, operation_id: str, lease_token: str, **changes: Any
    ) -> dict[str, Any]: ...

    def fail_operation(
        self, learner_id: str, operation_id: str, lease_token: str, **error: Any
    ) -> dict[str, Any]: ...

    def interrupt_operations(self, operation_ids: list[str]) -> int: ...
