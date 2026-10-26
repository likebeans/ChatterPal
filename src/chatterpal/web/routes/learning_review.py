"""Review routes reuse the task runtime, providers and cookie identity dependency."""

import asyncio
from functools import lru_cache

from fastapi import APIRouter, Depends, Request

from ...learning.contracts import (
    ReviewAttemptRequest,
    ReviewDeferRequest,
    ReviewHintRequest,
    ReviewWrite,
    SaveReviewRequest,
)
from ...learning.review import LearningReviewService
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

    @router.get("/progress")
    def progress(owner=Depends(identity)):
        return review_service().progress(owner)

    app.include_router(router)
