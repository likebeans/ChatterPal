"""Private ASR drafts and explicit confirmation without repeating recognition."""

import hashlib
import uuid
from datetime import timedelta

from .contracts import AttemptRequest
from .storage import StoreError


def turn_context(session_id, body):
    return {
        "kind": "turn",
        "session_id": session_id,
        "expected_revision": body.expected_revision,
    }


def attempt_context(body):
    return {
        "kind": "attempt",
        **{
            key: getattr(body, key)
            for key in (
                "session_id",
                "source_revision",
                "source_turn_id",
                "focus_id",
                "parent_attempt_id",
                "mode",
                "target_text",
            )
        },
    }


def review_context(item_id, body):
    return {
        "kind": "review",
        "review_item_id": item_id,
        "expected_revision": body.expected_revision,
    }


def consume_spec(body, context):
    if body.transcription_id is None:
        return None
    return {"transcription_id": body.transcription_id, "context": context}


def validate_attempt(record, body):
    snapshot = record["data"]
    turn = next(
        (
            item
            for item in snapshot["turns"]
            if item["turn_id"] == body.source_turn_id and item["role"] == "user"
        ),
        None,
    )
    if turn is None:
        raise StoreError("turn_not_found", "找不到这次重练关联的原句。", 404)
    focus = None
    if body.focus_id is not None:
        focus = next(
            (
                item
                for item in (snapshot.get("feedback") or {}).get("focuses", [])
                if item["focus_id"] == body.focus_id
                and item["source_turn_id"] == body.source_turn_id
            ),
            None,
        )
        if focus is None:
            raise StoreError("focus_not_found", "这项反馈不属于当前原句。", 404)
    if body.parent_attempt_id is not None and not any(
        item["attempt_id"] == body.parent_attempt_id
        and item["source_turn_id"] == body.source_turn_id
        and item["focus_id"] == body.focus_id
        and item["mode"] == body.mode
        for item in snapshot["attempts"]
    ):
        raise StoreError("attempt_not_found", "前次尝试不属于当前练习方式或原句。", 404)
    targets = {turn["text"]}
    if focus:
        targets.update((focus["original"], focus["suggested"]))
    if body.mode == "read_aloud" and body.target_text not in targets:
        raise StoreError(
            "target_required", "跟读需要使用当前原句或反馈中的目标表达。", 422
        )
    if body.mode == "free_response" and body.target_text is not None:
        raise StoreError("unexpected_target", "自由表达不需要参考句。", 422)


def transcript_text(value):
    if not isinstance(value, str) or not value.strip():
        raise StoreError("no_speech", "未识别到有效表达，请重新录音或输入文字。", 422)
    if len(value) > 12000:
        raise StoreError("text_too_large", "这段表达过长，请分成较短的回答。", 413)
    return value


def confirmed_submission(repository, owner, body, context, now):
    """Read a draft for provider work; publication checks it again atomically."""
    if body.transcription_id is None:
        return None
    draft = repository.get_transcription(owner, body.transcription_id)
    if draft["status"] != "ready":
        raise StoreError(
            "transcription_unavailable", "这份录音已提交或已到期，请重新录音。", 409
        )
    if draft["context"] != context:
        raise StoreError(
            "transcription_context_changed", "这份录音不属于当前练习版本。", 409
        )
    return {
        "machine_transcript": draft["machine_transcript"],
        "confirmed_text": body.confirmed_text,
        "transcription_id": body.transcription_id,
        "confirmed_at": now.isoformat(),
    }


class TranscriptionService:
    def __init__(self, tasks, reviews):
        self.tasks = tasks
        self.reviews = reviews
        self.repository = tasks.repository

    def create(self, owner, body, audio):
        self.repository._validate_audio(audio)
        context = body.context.model_dump(mode="json")
        if context["kind"] == "review":
            record = self.reviews._review(owner, context["review_item_id"])
            self.reviews._source_context(owner, record["data"])
            revision = context["expected_revision"]
        else:
            record = self.tasks._session_record(owner, context["session_id"])
            revision = context.get("expected_revision", context.get("source_revision"))
        received = self.tasks.clock()
        expires = received + timedelta(hours=1)
        payload = {
            **body.model_dump(mode="json"),
            "audio_sha256": hashlib.sha256(audio).hexdigest(),
        }

        def work(claim):
            # Validate before ASR, preserving idempotent recovery before these checks.
            if (
                context["kind"] == "turn"
                and self.tasks._domain(record).status != "active"
            ):
                raise StoreError(
                    "session_completed", "这次对话已结束，请从原句进入重练。", 409
                )
            if context["kind"] == "attempt":
                values = {key: value for key, value in context.items() if key != "kind"}
                validate_attempt(
                    record, AttemptRequest(request_id=body.request_id, **values)
                )
            text = transcript_text(self.tasks.provider.recognize(audio))
            identifier = str(uuid.uuid4())
            view = {
                "transcription_id": identifier,
                "status": "ready",
                "context": context,
                "machine_transcript": text,
                "audio": {"audio_key": "preview"},
                "received_at": received.isoformat(),
                "expires_at": expires.isoformat(),
            }
            data = {
                **view,
                "source_record_id": record["record_id"],
                "source_revision": revision,
                "expires_epoch": expires.timestamp(),
                "audio_sha256": payload["audio_sha256"],
                "request_id": body.request_id,
            }
            return {
                "creates": [
                    {
                        "key": "transcription",
                        "record_id": identifier,
                        "kind": "transcription",
                        "session_id": record["session_id"],
                        "parent_record_id": record["record_id"],
                        "data": data,
                    }
                ],
                "resource_record_id": identifier,
                "result": view,
                "result_type": "transcription",
                "result_id": identifier,
                "audio": [{"key": "preview", "content": audio, "retain": False}],
                "transcription_create": {"expires_epoch": expires.timestamp()},
            }

        return self.tasks.runtime.submit(
            owner,
            body.request_id,
            "transcribe",
            payload,
            work,
            session_id=record["session_id"],
            record_id=record["record_id"],
            expected_revision=revision,
        )

    def discard(self, owner, transcription_id):
        record = self.repository.get_record(owner, transcription_id)
        if record["kind"] != "transcription":
            raise StoreError("transcription_not_found", "找不到这份录音转写。", 404)
        self.repository.discard_transcription_request(
            owner, record["data"]["request_id"]
        )
