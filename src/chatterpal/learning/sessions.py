"""Pure task-session transitions, ready for the R0 repository transaction boundary.

These values are B2 domain state, not a replacement HTTP or repository contract.
The eventual repository must atomically check ownership, revision and pending
operations before storing a new snapshot. No provider or database is called here.
"""

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .tasks import TaskDefinition

Identifier = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=128)
]
Message = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=12000)]
CorrectionMode = Literal["immediate", "after_session"]
HintKind = Literal["sentence_starter", "simpler_question"]


class SessionConflict(ValueError):
    """A state transition is not valid for the current snapshot."""


def _utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Session time must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


class _State(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TaskRound(_State):
    user_turn_id: Identifier
    assistant_turn_id: Identifier
    text: Message
    reply: Message
    source: Literal["text", "audio"]
    audio_status: Literal["available", "unavailable"] = "unavailable"
    created_at: datetime

    @field_validator("text", "reply")
    @classmethod
    def require_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A round requires both a user message and a coach reply")
        return value

    @field_validator("created_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class HintUse(_State):
    hint_id: Identifier
    kind: HintKind
    criterion_id: str
    text: Message
    source_revision: int = Field(strict=True, ge=0)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class FinishedTask(_State):
    source_revision: int = Field(strict=True, ge=0)
    turn_count: int = Field(strict=True, ge=1)
    condition_results: tuple[tuple[str, Literal["unverified"]], ...]
    goal_status: Literal["unverified"] = "unverified"
    finished_at: datetime

    @field_validator("finished_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class TaskSession(_State):
    session_id: Identifier
    task: TaskDefinition
    correction_mode: CorrectionMode
    status: Literal["active", "completed", "archived"] = "active"
    revision: int = Field(strict=True, ge=0)
    turns: tuple[TaskRound, ...] = ()
    hints: tuple[HintUse, ...] = ()
    finished: FinishedTask | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def check_history(self) -> "TaskSession":
        if self.status == "active" and self.finished is not None:
            raise ValueError("An active session cannot have finish metadata")
        if self.status == "completed" and self.finished is None:
            raise ValueError("A completed session requires finish metadata")
        event_count = len(self.turns) + len(self.hints)
        if self.revision != event_count + int(self.finished is not None):
            raise ValueError("The session revision must match its recorded events")
        message_ids = [
            identifier
            for turn in self.turns
            for identifier in (turn.user_turn_id, turn.assistant_turn_id)
        ]
        if len(message_ids) != len(set(message_ids)):
            raise ValueError("Turn IDs must be unique within the session")
        hint_ids = [hint.hint_id for hint in self.hints]
        if len(hint_ids) != len(set(hint_ids)):
            raise ValueError("Hint IDs must be unique within the session")
        steps = {step.criterion_id: step for step in self.task.steps}
        previous_revision = -1
        for index, hint in enumerate(self.hints):
            if not previous_revision < hint.source_revision < event_count:
                raise ValueError("Hint source revisions must be ordered prior events")
            if hint.source_revision > len(self.turns) + index:
                raise ValueError("A hint cannot reference a nonexistent prior event")
            step = steps.get(hint.criterion_id)
            if step is None or hint.text != getattr(step, hint.kind):
                raise ValueError("Hints must match the pinned task's authored content")
            previous_revision = hint.source_revision
        if self.finished is not None:
            if (
                self.finished.source_revision != event_count
                or self.finished.turn_count != len(self.turns)
            ):
                raise ValueError("Finish metadata must describe this session's history")
            expected = tuple(
                (criterion.criterion_id, "unverified")
                for criterion in self.task.criteria
            )
            if self.finished.condition_results != expected:
                raise ValueError("Finish conditions must match the pinned task")
        return self

    @property
    def opening(self) -> str:
        return self.task.opening

    def _with_changes(self, **changes: object) -> "TaskSession":
        return self.__class__.model_validate({**dict(self), **changes})

    @classmethod
    def start(
        cls,
        *,
        session_id: str,
        task: TaskDefinition,
        correction_mode: CorrectionMode,
        now: datetime,
    ) -> "TaskSession":
        timestamp = _utc(now)
        return cls(
            session_id=session_id,
            task=task,
            correction_mode=correction_mode,
            revision=0,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def _check_write(self, expected_revision: int) -> None:
        if type(expected_revision) is not int or expected_revision != self.revision:
            raise SessionConflict(
                "The session revision has changed; reload before retrying"
            )
        if self.status != "active":
            raise SessionConflict(
                "This session has ended and cannot accept new changes"
            )

    def append_turn(
        self,
        *,
        expected_revision: int,
        user_turn_id: str,
        assistant_turn_id: str,
        text: str,
        reply: str,
        input_source: Literal["text", "audio"],
        now: datetime,
        audio_status: Literal["available", "unavailable"] = "unavailable",
    ) -> "TaskSession":
        """Return an entire successful round; a provider failure leaves state intact."""
        self._check_write(expected_revision)
        used_ids = {
            identifier
            for turn in self.turns
            for identifier in (turn.user_turn_id, turn.assistant_turn_id)
        }
        if (
            user_turn_id == assistant_turn_id
            or user_turn_id in used_ids
            or assistant_turn_id in used_ids
        ):
            raise ValueError("Turn IDs must be unique within the session")
        turn = TaskRound(
            user_turn_id=user_turn_id,
            assistant_turn_id=assistant_turn_id,
            text=text,
            reply=reply,
            source=input_source,
            audio_status=audio_status,
            created_at=_utc(now),
        )
        return self._with_changes(
            turns=(*self.turns, turn),
            revision=self.revision + 1,
            updated_at=_utc(now),
        )

    def use_hint(
        self,
        *,
        expected_revision: int,
        hint_id: str,
        kind: HintKind,
        criterion_id: str,
        now: datetime,
    ) -> "TaskSession":
        self._check_write(expected_revision)
        if any(hint.hint_id == hint_id for hint in self.hints):
            raise ValueError("Hint IDs must be unique within the session")
        step = next(
            (step for step in self.task.steps if step.criterion_id == criterion_id),
            None,
        )
        if step is None:
            raise ValueError("The hint must refer to a criterion in this task snapshot")
        if kind not in ("sentence_starter", "simpler_question"):
            raise ValueError("Unknown hint kind")
        hint = HintUse(
            hint_id=hint_id,
            kind=kind,
            criterion_id=criterion_id,
            text=getattr(step, kind),
            source_revision=self.revision,
            created_at=_utc(now),
        )
        return self._with_changes(
            hints=(*self.hints, hint),
            revision=self.revision + 1,
            updated_at=_utc(now),
        )

    def finish(
        self,
        *,
        expected_revision: int,
        has_pending_turn: bool,
        now: datetime,
    ) -> "TaskSession":
        self._check_write(expected_revision)
        if has_pending_turn:
            raise SessionConflict("Wait for the pending round before finishing")
        if not self.turns:
            raise SessionConflict("Send a reply before finishing this task")
        timestamp = _utc(now)
        finished = FinishedTask(
            source_revision=self.revision,
            turn_count=len(self.turns),
            condition_results=tuple(
                (criterion.criterion_id, "unverified")
                for criterion in self.task.criteria
            ),
            finished_at=timestamp,
        )
        return self._with_changes(
            status="completed",
            revision=self.revision + 1,
            finished=finished,
            updated_at=timestamp,
        )

    def to_snapshot(self) -> dict:
        return self.model_dump(mode="json")

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> "TaskSession":
        return cls.model_validate(snapshot)
