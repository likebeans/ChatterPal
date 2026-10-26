"""Cookie-authenticated HTTP routes for the learning repository."""

from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from starlette.background import BackgroundTask

from ...learning.storage import LearningStore, StoreError
from ...learning.presentation import public_learning_result
from ...learning.review import public_record


COOKIE_NAME = "chatterpal_learner"
COOKIE_MAX_AGE = 365 * 86400
DEVELOPMENT_ORIGINS = {
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3010",
    "http://127.0.0.1:3010",
}


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: Literal["daily", "travel", "work"] | None = None
    daily_minutes: StrictInt | None = Field(default=None, ge=1, le=120)
    correction_mode: Literal["immediate", "after_session"] | None = None
    retain_audio: StrictBool | None = None
    onboarding_completed: StrictBool | None = None


class CreateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=128)
    kind: Literal["session", "chat", "analysis", "attempt", "review"]
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    data: dict[str, Any]


class UpdateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=128)
    expected_revision: StrictInt = Field(ge=1)
    data: dict[str, Any]


def _same_origin(origin: str, request: Request) -> bool:
    try:
        parsed = urlsplit(origin)
        current = urlsplit(str(request.base_url))
        return (
            parsed.scheme in {"http", "https"}
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and parsed.path in {"", "/"}
            and (
                parsed.scheme,
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
            )
            == (
                current.scheme,
                current.hostname,
                current.port or (443 if current.scheme == "https" else 80),
            )
        )
    except ValueError:
        return False


def mount_learning_api(
    app: FastAPI,
    settings: Any,
    *,
    store: LearningStore | None = None,
    allowed_origins: set[str] | None = None,
    allow_record_writes: bool = True,
) -> None:
    """Register independently of providers; initialize storage lazily in a worker."""
    router = APIRouter(prefix="/api/learning", tags=["learning"])
    trusted_origins = frozenset(
        DEVELOPMENT_ORIGINS if allowed_origins is None else allowed_origins
    )

    @lru_cache(maxsize=1)
    def repository() -> LearningStore:
        # Injecting a store makes real routes usable with an isolated database.
        return store or LearningStore(
            getattr(settings, "learning_db_path", "data/learning/learning.sqlite3"),
            max_audio_bytes=getattr(
                settings, "learning_max_audio_bytes", 16 * 1024 * 1024
            ),
            audio_retention_days=getattr(settings, "learning_audio_retention_days", 7),
        )

    def identity(
        request: Request, database: LearningStore = Depends(repository)
    ) -> str:
        return database.authenticate(request.cookies.get(COOKIE_NAME))

    @app.exception_handler(StoreError)
    async def store_error(request: Request, error: StoreError):
        return JSONResponse(
            status_code=error.status_code,
            content={"detail": {"code": error.code, "message": error.message}},
            headers={"Cache-Control": "no-store"},
        )

    @app.middleware("http")
    async def learning_privacy(request: Request, call_next):
        if request.url.path == "/api/learning" or request.url.path.startswith(
            "/api/learning/"
        ):
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("origin")
                if (
                    origin is not None
                    and origin not in trusted_origins
                    and not _same_origin(origin, request)
                ) or request.headers.get("sec-fetch-site") == "cross-site":
                    return JSONResponse(
                        status_code=403,
                        content={
                            "detail": {
                                "code": "origin_not_allowed",
                                "message": "Untrusted learning request origin",
                            }
                        },
                        headers={"Cache-Control": "no-store"},
                    )
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        return await call_next(request)

    @router.post("/bootstrap")
    def bootstrap(
        request: Request,
        response: Response,
        database: LearningStore = Depends(repository),
    ):
        result, token = database.bootstrap(request.cookies.get(COOKIE_NAME))
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=bool(getattr(settings, "learning_cookie_secure", False))
            or request.url.scheme == "https",
            path="/api/learning",
        )
        return result

    @router.get("/profile")
    def get_profile(
        learner_id: str = Depends(identity),
        database: LearningStore = Depends(repository),
    ):
        return database.get_profile(learner_id)

    @router.patch("/profile")
    def update_profile(
        patch: ProfilePatch,
        learner_id: str = Depends(identity),
        database: LearningStore = Depends(repository),
    ):
        return database.update_profile(learner_id, patch.model_dump(exclude_unset=True))

    def record_view(record):
        # Formal learning routes expose business views; repository export keeps
        # complete owner data for portability.
        if allow_record_writes:
            return record
        return public_learning_result(public_record(record))

    @router.get("/records")
    def list_records(
        kind: str | None = None,
        session_id: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
        learner_id: str = Depends(identity),
        database: LearningStore = Depends(repository),
    ):
        page = database.list_records(
            learner_id, kind=kind, session_id=session_id, limit=limit, cursor=cursor
        )
        return {**page, "items": [record_view(record) for record in page["items"]]}

    @router.get("/sessions")
    def list_sessions(
        limit: int = 20,
        cursor: str | None = None,
        learner_id: str = Depends(identity),
        database: LearningStore = Depends(repository),
    ):
        records = database.list_records(
            learner_id, kind="session", limit=limit, cursor=cursor
        )
        return {
            "items": [record_view(item)["data"] for item in records["items"]],
            "next_cursor": records["next_cursor"],
        }

    @router.get("/sessions/{session_id}")
    def get_session(
        session_id: str,
        learner_id: str = Depends(identity),
        database: LearningStore = Depends(repository),
    ):
        records = database.list_records(
            learner_id, kind="session", session_id=session_id, limit=1
        )
        if not records["items"]:
            raise StoreError("session_not_found", "Learning session not found", 404)
        return record_view(records["items"][0])["data"]

    @router.get("/records/{record_id}")
    def get_record(
        record_id: str,
        learner_id: str = Depends(identity),
        database: LearningStore = Depends(repository),
    ):
        return record_view(database.get_record(learner_id, record_id))

    @router.post("/records")
    def create_record(
        body: CreateRecord,
        response: Response,
        learner_id: str = Depends(identity),
        database: LearningStore = Depends(repository),
    ):
        if not allow_record_writes:
            raise StoreError(
                "business_route_required", "请通过任务或重练入口保存学习记录。", 403
            )
        result, operation_id = database.create_record(learner_id, **body.model_dump())
        response.headers["X-Operation-ID"] = operation_id
        return result

    @router.put("/records/{record_id}")
    def update_record(
        record_id: str,
        body: UpdateRecord,
        response: Response,
        learner_id: str = Depends(identity),
        database: LearningStore = Depends(repository),
    ):
        if not allow_record_writes:
            raise StoreError(
                "business_route_required", "请通过任务或重练入口更新学习记录。", 403
            )
        result, operation_id = database.update_record(
            learner_id, record_id=record_id, **body.model_dump()
        )
        response.headers["X-Operation-ID"] = operation_id
        return result

    @router.delete("/records/{record_id}")
    def delete_record(
        record_id: str,
        learner_id: str = Depends(identity),
        database: LearningStore = Depends(repository),
    ):
        database.delete_record(learner_id, record_id)
        return {"deleted": True}

    @router.post("/records/{record_id}/audio")
    def add_audio(
        record_id: str,
        response: Response,
        audio: UploadFile = File(...),
        request_id: str = Form(...),
        learner_id: str = Depends(identity),
        database: LearningStore = Depends(repository),
    ):
        # The UploadFile is owned by FastAPI. Read one bounded copy in its worker.
        content = audio.file.read(database.max_audio_bytes + 1)
        result, operation_id = database.add_audio(
            learner_id, request_id, record_id, content
        )
        response.headers["X-Operation-ID"] = operation_id
        return result

    @router.get("/audio/{audio_asset_id}")
    def get_audio(
        audio_asset_id: str,
        learner_id: str = Depends(identity),
        database: LearningStore = Depends(repository),
    ):
        metadata, content = database.get_audio(learner_id, audio_asset_id)
        return Response(content=content, media_type=metadata["media_type"])

    @router.get("/operations/{operation_id}")
    def get_operation(
        operation_id: str,
        learner_id: str = Depends(identity),
        database: LearningStore = Depends(repository),
    ):
        operation = database.get_operation(learner_id, operation_id=operation_id)
        return operation if allow_record_writes else public_learning_result(operation)

    @router.get("/requests/{request_id}")
    def get_request(
        request_id: str,
        action: str | None = None,
        learner_id: str = Depends(identity),
        database: LearningStore = Depends(repository),
    ):
        operation = database.get_operation(
            learner_id, request_id=request_id, action=action
        )
        return operation if allow_record_writes else public_learning_result(operation)

    @router.get("/export")
    def export_data(
        learner_id: str = Depends(identity),
        database: LearningStore = Depends(repository),
    ):
        archive = database.export_data(learner_id)

        def chunks():
            try:
                while chunk := archive.read(64 * 1024):
                    yield chunk
            finally:
                archive.close()

        return StreamingResponse(
            chunks(),
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="chatterpal-learning.json"'
            },
            background=BackgroundTask(archive.close),
        )

    @router.delete("/data")
    def delete_data(
        response: Response,
        request: Request,
        learner_id: str = Depends(identity),
        database: LearningStore = Depends(repository),
    ):
        database.delete_learner(learner_id)
        response.delete_cookie(
            COOKIE_NAME,
            path="/api/learning",
            httponly=True,
            samesite="lax",
            secure=bool(getattr(settings, "learning_cookie_secure", False))
            or request.url.scheme == "https",
        )
        return {"deleted": True}

    app.include_router(router)
    # Business routers can share the same repository/identity dependencies.
    app.state.learning_repository = repository
    app.state.learning_identity = identity
