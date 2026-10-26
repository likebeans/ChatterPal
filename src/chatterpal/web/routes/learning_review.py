"""Review routes reuse the task runtime, providers and cookie identity dependency."""

import asyncio
import json
from functools import lru_cache

from fastapi import APIRouter, Depends, Request, Response
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from ...learning.contracts import (
    ReviewAttemptRequest,
    ReviewDeferRequest,
    ReviewHintRequest,
    ReviewWrite,
    SaveReviewRequest,
    TranscriptionRequest,
)
from ...learning.review import LearningReviewService
from ...learning.storage import StoreError
from ...learning.transcription import TranscriptionService
from .learning_tasks import _submission


def mount_learning_review(app, settings, *, service=None):
    """Mount after learning records/tasks, optionally injecting a review service.

    The default service shares app.state.learning_service()'s repository, provider
    and runtime, including its admission limits and shutdown lifecycle. Tests may
    supply LearningReviewService with an injected UTC clock. This router owns
    /progress and /reviews/due; callers must remove the old task-route stubs.
    """
    identity = app.state.learning_identity
    router = APIRouter(prefix="/api/learning", tags=["learning-review"])

    @lru_cache(maxsize=1)
    def review_service():
        if service is not None:
            return service
        task_service = app.state.learning_service()
        return LearningReviewService(
            task_service.repository,
            task_service.provider,
            task_service.runtime,
            clock=task_service.clock,
        )

    app.state.learning_review_service = review_service

    @router.get("/reviews/due")
    def due(owner=Depends(identity)):
        return review_service().due(owner)

    @router.post("/reviews", status_code=202)
    def save(body: SaveReviewRequest, owner=Depends(identity)):
        return review_service().save(owner, body)

    @router.get("/reviews/{review_item_id}")
    def get(review_item_id: str, owner=Depends(identity)):
        return review_service().get(owner, review_item_id)

    @router.post("/reviews/{review_item_id}/reveal", status_code=202)
    def reveal(review_item_id: str, body: ReviewWrite, owner=Depends(identity)):
        return review_service().reveal(owner, review_item_id, body)

    @router.post("/reviews/{review_item_id}/hints", status_code=202)
    def hint(review_item_id: str, body: ReviewHintRequest, owner=Depends(identity)):
        return review_service().hint(owner, review_item_id, body)

    @router.post("/reviews/{review_item_id}/defer", status_code=202)
    def defer(review_item_id: str, body: ReviewDeferRequest, owner=Depends(identity)):
        return review_service().defer(owner, review_item_id, body)

    @router.post("/reviews/{review_item_id}/attempts", status_code=202)
    async def attempt(review_item_id: str, request: Request, owner=Depends(identity)):
        body, audio = await _submission(
            request, ReviewAttemptRequest, settings.learning_max_audio_bytes
        )
        return await asyncio.to_thread(
            review_service().attempt, owner, review_item_id, body, audio
        )

    def transcriptions():
        return TranscriptionService(app.state.learning_service(), review_service())

    @router.post("/transcriptions", status_code=202)
    async def transcribe(request: Request, owner=Depends(identity)):
        try:
            async with request.form(max_files=1, max_fields=2) as form:
                values = {}
                for key, value in form.multi_items():
                    if key in values:
                        raise StoreError(
                            "duplicate_field", "提交字段重复，请重新提交。", 422
                        )
                    values[key] = value
                upload = values.pop("audio", None)
                if not isinstance(upload, UploadFile):
                    raise StoreError("invalid_audio", "请上传需要核对的录音。", 422)
                values["context"] = json.loads(values.get("context", "null"))
                body = TranscriptionRequest.model_validate(values)
                audio = await upload.read(settings.learning_max_audio_bytes + 1)
        except (ValidationError, ValueError, TypeError) as error:
            raise StoreError(
                "invalid_input", "请提供录音和有效的练习信息。", 422
            ) from error
        return await asyncio.to_thread(transcriptions().create, owner, body, audio)

    @router.get("/transcriptions/{transcription_id}")
    def get_transcription(transcription_id: str, owner=Depends(identity)):
        return transcriptions().repository.get_transcription(owner, transcription_id)

    @router.delete("/transcriptions/requests/{request_id}", status_code=204)
    def discard_transcription_request(request_id: str, owner=Depends(identity)):
        transcriptions().repository.discard_transcription_request(owner, request_id)
        return Response(status_code=204)

    @router.delete("/transcriptions/{transcription_id}", status_code=204)
    def discard_transcription(transcription_id: str, owner=Depends(identity)):
        transcriptions().discard(owner, transcription_id)
        return Response(status_code=204)

    @router.get("/progress")
    def progress(owner=Depends(identity)):
        return review_service().progress(owner)

    app.include_router(router)
