"""Learning task routes with injected private storage, runtime and providers."""

import asyncio
import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from threading import Lock

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from ...learning.contracts import (
    AttemptRequest,
    HintRequest,
    OperationRequest,
    SessionWrite,
    StartSession,
    TurnRequest,
)
from ...learning.runtime import LearningRuntime
from ...learning.presentation import public_learning_result
from ...learning.service import LearningService
from ...learning.storage import StoreError

logger = logging.getLogger(__name__)


async def _submission(request: Request, model, max_audio_bytes: int):
    audio = None
    try:
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            async with request.form(max_files=1, max_fields=12) as form:
                values = {}
                for key, value in form.multi_items():
                    if key in values:
                        raise StoreError(
                            "duplicate_field", "提交字段重复，请重新提交。", 422
                        )
                    values[key] = value
                upload = values.pop("audio", None)
                if upload is not None:
                    if not isinstance(upload, UploadFile):
                        raise StoreError("invalid_audio", "录音必须是音频文件。", 422)
                    audio = await upload.read(max_audio_bytes + 1)
                for key in ("expected_revision", "source_revision"):
                    if key in values:
                        raw = values[key]
                        if not isinstance(raw, str) or not raw.isdecimal():
                            raise StoreError(
                                "invalid_revision", "请提供有效的练习版本。", 422
                            )
                        values[key] = int(raw)
        else:
            values = await request.json()
        body = model.model_validate(values)
    except (ValidationError, ValueError) as error:
        raise StoreError(
            "invalid_input", "提交内容不完整，请检查文字、目标句和练习版本。", 422
        ) from error
    if (
        sum(
            (
                body.text is not None,
                audio is not None,
                body.transcription_id is not None,
            )
        )
        != 1
    ):
        raise StoreError(
            "input_required", "请选择文字、录音或已核对的转写其中一种方式回答。", 422
        )
    if audio is not None:
        if not audio:
            raise StoreError("empty_audio", "录音为空，请重新录音。", 422)
        if len(audio) > max_audio_bytes:
            raise StoreError("audio_too_large", "录音过大，请缩短后重试。", 413)
    return body, audio


def mount_learning_tasks(app, settings, provider):
    repository = app.state.learning_repository
    identity = app.state.learning_identity
    router = APIRouter(prefix="/api/learning", tags=["learning-tasks"])

    service_lock = Lock()

    @lru_cache(maxsize=1)
    def cached_service():
        store = repository()
        runtime = LearningRuntime(
            store,
            max_workers=settings.learning_max_workers,
            max_pending=settings.learning_max_pending,
            operation_timeout=settings.learning_operation_timeout,
            shutdown_timeout=settings.learning_shutdown_timeout,
        )
        store.purge_expired_audio()
        return LearningService(store, provider, runtime)

    def service():
        with service_lock:
            return cached_service()

    old_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with old_lifespan(application):
            try:
                yield
            finally:
                if cached_service.cache_info().currsize:
                    await asyncio.to_thread(service().runtime.shutdown)
                    await asyncio.to_thread(repository().purge_expired_audio)

    app.router.lifespan_context = lifespan
    app.state.learning_service = service
    app.state.learning_runtime_snapshot = lambda: (
        service().runtime.snapshot() if cached_service.cache_info().currsize else None
    )

    @router.get("/tasks")
    def tasks(owner=Depends(identity)):
        return service().tasks()

    @router.get("/tasks/{task_id}")
    def task(task_id: str, owner=Depends(identity)):
        return service().task(task_id)

    @router.post("/sessions", status_code=202)
    def start(body: StartSession, owner=Depends(identity)):
        return public_learning_result(service().start(owner, body))

    @router.post("/sessions/{session_id}/turns", status_code=202)
    async def turn(session_id: str, request: Request, owner=Depends(identity)):
        body, audio = await _submission(
            request, TurnRequest, settings.learning_max_audio_bytes
        )

        def submit():
            return public_learning_result(
                service().turn(owner, session_id, body, audio)
            )

        return await asyncio.to_thread(submit)

    @router.post("/sessions/{session_id}/hints", status_code=202)
    def hint(session_id: str, body: HintRequest, owner=Depends(identity)):
        return public_learning_result(service().hint(owner, session_id, body))

    @router.post("/sessions/{session_id}/finish", status_code=202)
    def finish(session_id: str, body: SessionWrite, owner=Depends(identity)):
        return public_learning_result(service().finish(owner, session_id, body))

    @router.post("/sessions/{session_id}/feedback", status_code=202)
    def feedback(session_id: str, body: SessionWrite, owner=Depends(identity)):
        return public_learning_result(service().feedback(owner, session_id, body))

    @router.post("/attempts", status_code=202)
    async def attempt(request: Request, owner=Depends(identity)):
        body, audio = await _submission(
            request, AttemptRequest, settings.learning_max_audio_bytes
        )

        def submit():
            return public_learning_result(service().attempt(owner, body, audio))

        return await asyncio.to_thread(submit)

    @router.get("/attempts/{attempt_id}")
    def get_attempt(attempt_id: str, owner=Depends(identity)):
        return service().get_attempt(owner, attempt_id)["data"]

    @router.post("/attempts/{attempt_id}/example-audio", status_code=202)
    def example(attempt_id: str, body: OperationRequest, owner=Depends(identity)):
        return service().example(owner, attempt_id, body.request_id)

    app.include_router(router)
