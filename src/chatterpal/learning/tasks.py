"""Immutable authored content, independent of HTTP and repository contracts."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[
    str,
    StringConstraints(
        strict=True, strip_whitespace=True, min_length=1, max_length=4000
    ),
]
ContentId = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9-]{0,63}$")
]
ContentVersion = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[1-9][0-9.]{0,31}$")
]


class _Content(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TaskCriterion(_Content):
    criterion_id: ContentId
    description: Text


class CoachingStep(_Content):
    criterion_id: ContentId
    question: Text
    simpler_question: Text
    sentence_starter: Text


class ReadAloudPrompt(_Content):
    instruction: Text
    target_text: Text


class FreeResponsePrompt(_Content):
    prompt: Text
    required_information: tuple[Text, ...] = Field(min_length=1)


class TransferQuestion(_Content):
    question_id: ContentId
    prompt: Text
    criterion_ids: tuple[ContentId, ...] = Field(min_length=1)


class TaskDefinition(_Content):
    task_id: ContentId
    content_version: ContentVersion
    title: Text
    description: Text
    estimated_minutes: int = Field(strict=True, ge=1, le=60)
    learner_role: Text
    coach_role: Text
    objectives: tuple[Text, ...] = Field(min_length=1)
    required_information: tuple[Text, ...] = Field(min_length=1)
    examples: tuple[Text, Text]
    target_expressions: tuple[Text, ...] = Field(min_length=1)
    criteria: tuple[TaskCriterion, ...] = Field(min_length=1)
    opening: Text
    steps: tuple[CoachingStep, ...] = Field(min_length=1)
    read_aloud: ReadAloudPrompt
    free_response: FreeResponsePrompt
    transfer_questions: tuple[TransferQuestion, ...] = Field(min_length=2)
    source_reference: Text

    @model_validator(mode="after")
    def check_references(self) -> "TaskDefinition":
        criterion_ids = [criterion.criterion_id for criterion in self.criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("Task criterion IDs must be unique")
        step_ids = [step.criterion_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)) or set(step_ids) != set(criterion_ids):
            raise ValueError("Each task criterion must have exactly one coaching step")
        question_ids = [question.question_id for question in self.transfer_questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("Transfer question IDs must be unique")
        for question in self.transfer_questions:
            if not set(question.criterion_ids) <= set(criterion_ids):
                raise ValueError(
                    "Transfer questions must refer to this task's criteria"
                )
        return self

    def to_snapshot(self) -> dict:
        """Return a detached JSON value suitable for a later repository adapter."""
        return self.model_dump(mode="json")
