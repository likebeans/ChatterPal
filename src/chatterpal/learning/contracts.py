"""Validated HTTP views shared by learning routes, providers and the React client."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints
from typing_extensions import Annotated

RequestId = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_.:-]{8,128}$")
]
Identifier = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=128)
]
Text = Annotated[
    str,
    StringConstraints(
        strict=True, strip_whitespace=True, min_length=1, max_length=12000
    ),
]
Availability = Literal["available", "partial", "unavailable"]
Result = Literal["met", "not_met", "unverified"]
Source = Literal["text", "acoustic", "model", "self_report"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AudioView(Contract):
    audio_asset_id: str | None = None
    url: str | None = None
    state: Literal["available", "not_saved", "expired", "unavailable"]
    expires_at: str | None = None


class Focus(Contract):
    focus_id: str
    source_turn_id: str
    original: Text
    suggested: Text
    explanation: Text
    category: Literal["grammar", "vocabulary", "clarity", "practice"]
    source: Literal["text"] = "text"


class CriterionResult(Contract):
    criterion_id: str
    description: str
    result: Result
    evidence: str


class Feedback(Contract):
    feedback_id: str
    source_revision: StrictInt = Field(ge=1)
    status: Availability
    reason: str
    strength: str
    focuses: list[Focus] = Field(max_length=2)
    criteria: list[CriterionResult]


class Evaluation(Contract):
    status: Availability
    result: Result
    reason: str
    source: Literal["text", "model"] = "text"


class Attempt(Contract):
    attempt_id: str
    session_id: str
    source_turn_id: str
    focus_id: str | None
    parent_attempt_id: str | None
    mode: Literal["read_aloud", "free_response"]
    transcript: str
    feedback: Feedback | None = None
    evaluation: Evaluation
    audio: dict[str, Any] | None = None
    created_at: str


class StartSession(Contract):
    request_id: RequestId
    task_id: Identifier
    content_version: Identifier
    correction_mode: Literal["immediate", "after_session"] = "after_session"


class SessionWrite(Contract):
    request_id: RequestId
    expected_revision: StrictInt = Field(ge=1)


class HintRequest(SessionWrite):
    kind: Literal["sentence_starter", "simpler_question"] = "sentence_starter"


class TurnRequest(SessionWrite):
    text: Text | None = None


class AttemptRequest(Contract):
    request_id: RequestId
    session_id: Identifier
    source_revision: StrictInt = Field(ge=1)
    source_turn_id: Identifier
    focus_id: Identifier | None = None
    parent_attempt_id: Identifier | None = None
    mode: Literal["read_aloud", "free_response"]
    target_text: Text | None = None
    text: Text | None = None


class OperationRequest(Contract):
    request_id: RequestId


class SaveReviewRequest(Contract):
    request_id: RequestId
    session_id: Identifier
    source_revision: StrictInt = Field(ge=1)
    source_turn_id: Identifier
    focus_id: Identifier | None = None
    attempt_id: Identifier | None = None


class ReviewWrite(Contract):
    request_id: RequestId
    expected_revision: StrictInt = Field(ge=1)


class ReviewHintRequest(ReviewWrite):
    kind: Literal["sentence_starter", "simpler_question"] = "sentence_starter"


class ReviewAttemptRequest(ReviewWrite):
    text: Text | None = None


class ReviewDeferRequest(ReviewWrite):
    days: StrictInt = Field(default=1, ge=1, le=30)
