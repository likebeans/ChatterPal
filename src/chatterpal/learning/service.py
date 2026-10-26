"""Compose authored coaching, private storage and providers into learning actions."""

import copy
import hashlib
import logging
import uuid
from datetime import datetime, timezone

from .catalog import TaskCatalog
from .coaching import build_coach_messages, parse_coach_response
from .contracts import (
    Attempt,
    AttemptRequest,
    Evaluation,
    HintRequest,
    SessionWrite,
    StartSession,
    TurnRequest,
)
from .feedback import evaluate_attempt, generate_feedback
from .ports import LearningProvider
from .runtime import LearningRuntime
from .sessions import SessionConflict, TaskSession
from .storage import LearningStore, StoreError
from .transcription import (
    attempt_context,
    confirmed_submission,
    consume_spec,
    turn_context,
    validate_attempt,
)

logger = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc)


def public_task(task):
    return {
        key: value
        for key, value in task.to_snapshot().items()
        if key
        in {
            "task_id",
            "content_version",
            "title",
            "description",
            "estimated_minutes",
            "learner_role",
            "coach_role",
            "objectives",
            "examples",
            "target_expressions",
            "criteria",
        }
    }


def _dump(value):
    return value.model_dump(mode="json", exclude_none=True)


class LearningService:
    def __init__(
        self,
        repository: LearningStore,
        provider: LearningProvider,
        runtime: LearningRuntime,
        *,
        catalog: TaskCatalog | None = None,
        clock=utc_now,
    ):
        self.repository = repository
        self.provider = provider
        self.runtime = runtime
        self.catalog = catalog or TaskCatalog()
        self.clock = clock

    def tasks(self):
        return [public_task(task) for task in self.catalog.list_tasks()]

    def task(self, task_id):
        try:
            return public_task(self.catalog.get_task(task_id))
        except KeyError as error:
            raise StoreError("task_not_found", "找不到这项任务。", 404) from error

    def _session_record(self, owner, session_id):
        page = self.repository.list_records(
            owner, kind="session", session_id=session_id, limit=1
        )
        if not page["items"]:
            raise StoreError("session_not_found", "找不到这次练习。", 404)
        record = page["items"][0]
        if not isinstance(record["data"].get("domain"), dict):
            raise StoreError(
                "session_not_supported", "这条记录不是可继续的任务练习。", 409
            )
        return record

    def _domain(self, record):
        try:
            return TaskSession.from_snapshot(record["data"]["domain"])
        except ValueError as error:
            raise StoreError(
                "invalid_session", "练习记录不完整，请从学习记录重新打开。", 409
            ) from error

    def _updated(self, record, domain=None):
        snapshot = copy.deepcopy(record["data"])
        snapshot["revision"] = record["revision"] + 1
        snapshot["updated_at"] = self.clock().isoformat()
        if domain is not None:
            snapshot["domain"] = domain.to_snapshot()
            snapshot["status"] = domain.status
        return snapshot

    @staticmethod
    def _update(record, snapshot):
        return {
            "record_id": record["record_id"],
            "expected_revision": record["revision"],
            "data": snapshot,
        }

    def _submit(
        self, owner, body, action, record, work, *, payload=None, transcription=None
    ):
        return self.runtime.submit(
            owner,
            body.request_id,
            action,
            payload if payload is not None else body.model_dump(mode="json"),
            work,
            session_id=record["session_id"],
            record_id=record["record_id"],
            expected_revision=(
                body.source_revision
                if isinstance(body, AttemptRequest)
                else body.expected_revision
            ),
            transcription_consume=transcription,
        )

    @staticmethod
    def _audio_payload(body, audio):
        return {
            **body.submission_payload(),
            "audio_sha256": (
                hashlib.sha256(audio).hexdigest() if audio is not None else None
            ),
        }

    def _transcript(self, text, audio):
        if (audio is None) == (text is None):
            raise StoreError(
                "input_required", "请选择文字或录音其中一种方式回答。", 422
            )
        if audio is not None:
            self.repository._validate_audio(audio)
        transcript = self.provider.recognize(audio) if audio is not None else text
        if not isinstance(transcript, str) or not transcript.strip():
            raise StoreError(
                "no_speech", "未识别到有效表达，请重新录音或输入文字。", 422
            )
        if len(transcript) > 12000:
            raise StoreError("text_too_large", "这段表达过长，请分成较短的回答。", 413)
        return transcript

    def _audio(self, owner, text, *, key):
        try:
            content = self.provider.synthesize(text)
            if not isinstance(content, bytes) or not content:
                raise ValueError("No synthesized audio")
            # The store also checks size and format before atomic publication.
            self.repository._media(content)
            if len(content) > self.repository.max_audio_bytes:
                raise ValueError("Synthesized audio is too large")
            return {"audio_key": key}, [
                {"key": key, "content": content, "retain": False}
            ]
        except Exception as error:
            logger.info("Learning speech unavailable (%s)", type(error).__name__)
            return {"state": "unavailable"}, []

    def start(self, owner, body: StartSession):
        try:
            task = self.catalog.get_task(body.task_id, body.content_version)
        except KeyError as error:
            raise StoreError(
                "task_not_found", "这项任务或内容版本暂不可用。", 404
            ) from error
        session_id = str(uuid.uuid4())

        def work(claim):
            domain = TaskSession.start(
                session_id=session_id,
                task=task,
                correction_mode=body.correction_mode,
                now=self.clock(),
            )
            snapshot = {
                "session_id": session_id,
                "task_id": task.task_id,
                "task": public_task(task),
                "status": "active",
                "revision": 1,
                "turns": [
                    {
                        "turn_id": f"{session_id}-opening",
                        "role": "assistant",
                        "text": domain.opening,
                    }
                ],
                "feedback": None,
                "attempts": [],
                "correction_mode": domain.correction_mode,
                "created_at": domain.created_at.isoformat(),
                "updated_at": domain.updated_at.isoformat(),
                "domain": domain.to_snapshot(),
            }
            return {
                "record": {
                    "kind": "session",
                    "session_id": session_id,
                    "data": snapshot,
                },
                "result_type": "record",
            }

        return self.runtime.submit(
            owner,
            body.request_id,
            "start",
            body.model_dump(mode="json"),
            work,
            session_id=session_id,
        )

    def turn(self, owner, session_id, body: TurnRequest, audio: bytes | None = None):
        record = self._session_record(owner, session_id)
        payload = self._audio_payload(body, audio)
        context = turn_context(session_id, body)

        def work(claim):
            domain = self._domain(record)
            if domain.status != "active":
                raise StoreError(
                    "session_completed", "这次对话已结束，请从原句进入重练。", 409
                )
            provenance = confirmed_submission(
                self.repository, owner, body, context, self.clock()
            )
            text = (
                body.confirmed_text
                if provenance
                else self._transcript(body.text, audio)
            )
            reply = parse_coach_response(
                domain, text, self.provider.reply(build_coach_messages(domain, text))
            )
            user_id, coach_id = str(uuid.uuid4()), str(uuid.uuid4())
            coach_audio, assets = self._audio(owner, reply.reply, key="coach")
            if provenance:
                user_audio = {"audio_key": "transcription"}
            elif audio is not None:
                user_audio = {"audio_key": "user"}
                assets.append(
                    {
                        "key": "user",
                        "content": audio,
                        "retain": self.repository.get_profile(owner)["retain_audio"],
                    }
                )
            else:
                user_audio = {"state": "not_saved"}
            updated_domain = domain.append_turn(
                expected_revision=domain.revision,
                user_turn_id=user_id,
                assistant_turn_id=coach_id,
                text=text,
                reply=reply.reply,
                input_source="audio" if audio is not None or provenance else "text",
                audio_status=(
                    "available" if audio is not None or provenance else "unavailable"
                ),
                now=self.clock(),
            )
            snapshot = self._updated(record, updated_domain)
            snapshot["turns"].extend(
                [
                    {
                        "turn_id": user_id,
                        "role": "user",
                        "text": text,
                        "audio": user_audio,
                        **(provenance or {}),
                    },
                    {
                        "turn_id": coach_id,
                        "role": "assistant",
                        "text": reply.reply,
                        "audio": coach_audio,
                    },
                ]
            )
            snapshot.setdefault("coaching", []).append(
                {
                    "source_turn_id": user_id,
                    "status": reply.correction_status,
                    "reason": reply.reason,
                    "suggestions": [_dump(item) for item in reply.suggestions],
                }
            )
            return {
                "updates": [self._update(record, snapshot)],
                "result": snapshot,
                "result_type": "session",
                "result_id": session_id,
                "audio": assets,
            }

        return self._submit(
            owner,
            body,
            "turn",
            record,
            work,
            payload=payload,
            transcription=consume_spec(body, context),
        )

    def hint(self, owner, session_id, body: HintRequest):
        record = self._session_record(owner, session_id)

        def work(claim):
            domain = self._domain(record)
            if domain.status != "active":
                raise StoreError(
                    "session_completed", "这次对话已结束，请从原句进入重练。", 409
                )
            step = domain.task.steps[min(len(domain.turns), len(domain.task.steps) - 1)]
            updated = domain.use_hint(
                expected_revision=domain.revision,
                hint_id=str(uuid.uuid4()),
                kind=body.kind,
                criterion_id=step.criterion_id,
                now=self.clock(),
            )
            snapshot = self._updated(record, updated)
            return {
                "updates": [self._update(record, snapshot)],
                "result": {"hint": updated.hints[-1].text},
                "result_type": "hint",
                "result_id": updated.hints[-1].hint_id,
            }

        return self._submit(owner, body, "hint", record, work)

    def _feedback_changes(self, record, domain):
        snapshot = self._updated(record, domain)
        feedback = generate_feedback(self.provider, domain, record["revision"])
        snapshot["feedback"] = _dump(feedback)
        creates = []
        existing_sources = {
            (attempt["source_turn_id"], attempt["focus_id"])
            for attempt in snapshot["attempts"]
        }
        for focus in feedback.focuses:
            if (focus.source_turn_id, focus.focus_id) in existing_sources:
                continue
            turn = next(
                turn
                for turn in snapshot["turns"]
                if turn["turn_id"] == focus.source_turn_id and turn["role"] == "user"
            )
            attempt = Attempt(
                attempt_id=str(uuid.uuid4()),
                session_id=domain.session_id,
                source_turn_id=turn["turn_id"],
                focus_id=focus.focus_id,
                parent_attempt_id=None,
                mode="free_response",
                transcript=turn["text"],
                **{
                    key: turn[key]
                    for key in (
                        "machine_transcript",
                        "confirmed_text",
                        "transcription_id",
                        "confirmed_at",
                    )
                    if key in turn
                },
                feedback=feedback,
                evaluation=Evaluation(
                    status="partial",
                    result="unverified",
                    source="text",
                    reason="首次表达已保存，重点建议引用原话；未测量声学发音或掌握度。",
                ),
                audio=turn.get("audio"),
                created_at=self.clock().isoformat(),
            )
            data = _dump(attempt)
            # Required nullable IDs remain explicit for the client.
            data.update({"parent_attempt_id": None, "focus_id": focus.focus_id})
            snapshot["attempts"].append(data)
            creates.append(
                {
                    "key": attempt.attempt_id,
                    "kind": "attempt",
                    "session_id": domain.session_id,
                    "data": data,
                    "parent_record_id": record["record_id"],
                }
            )
        return {
            "updates": [self._update(record, snapshot)],
            "creates": creates,
            "result": snapshot,
            "result_type": "session",
            "result_id": domain.session_id,
        }

    def finish(self, owner, session_id, body: SessionWrite):
        record = self._session_record(owner, session_id)

        def work(claim):
            domain = self._domain(record)
            try:
                domain = domain.finish(
                    expected_revision=domain.revision,
                    has_pending_turn=False,
                    now=self.clock(),
                )
            except SessionConflict as error:
                raise StoreError("session_conflict", str(error), 409) from error
            return self._feedback_changes(record, domain)

        return self._submit(owner, body, "finish", record, work)

    def feedback(self, owner, session_id, body: SessionWrite):
        record = self._session_record(owner, session_id)

        def work(claim):
            domain = self._domain(record)
            if domain.status != "completed":
                raise StoreError(
                    "session_active", "请先结束对话，再生成练后反馈。", 409
                )
            current = record["data"].get("feedback")
            if current and current["status"] != "unavailable":
                return {
                    "result": record["data"],
                    "result_type": "session",
                    "result_id": session_id,
                }
            return self._feedback_changes(record, domain)

        return self._submit(owner, body, "feedback", record, work)

    def attempt(self, owner, body: AttemptRequest, audio: bytes | None = None):
        record = self._session_record(owner, body.session_id)

        def work(claim):
            snapshot = self._updated(record)
            domain = self._domain(record)
            validate_attempt(record, body)
            provenance = confirmed_submission(
                self.repository, owner, body, attempt_context(body), self.clock()
            )
            text = (
                body.confirmed_text
                if provenance
                else self._transcript(body.text, audio)
            )
            evaluation = evaluate_attempt(
                self.provider,
                domain,
                text,
                body.target_text,
                body.mode,
                body.source_turn_id,
            )
            assets = []
            if provenance:
                attempt_audio = {"audio_key": "transcription"}
            elif audio is not None:
                attempt_audio = {"audio_key": "attempt"}
                assets.append(
                    {
                        "key": "attempt",
                        "content": audio,
                        "retain": self.repository.get_profile(owner)["retain_audio"],
                    }
                )
            else:
                attempt_audio = {"state": "not_saved"}
            data = {
                "attempt_id": str(uuid.uuid4()),
                "session_id": body.session_id,
                "source_turn_id": body.source_turn_id,
                "focus_id": body.focus_id,
                "parent_attempt_id": body.parent_attempt_id,
                "mode": body.mode,
                "transcript": text,
                **(provenance or {}),
                "feedback": snapshot.get("feedback"),
                "evaluation": _dump(evaluation),
                "audio": attempt_audio,
                "created_at": self.clock().isoformat(),
                "target_text": body.target_text,
            }
            snapshot["attempts"].append(data)
            return {
                "updates": [self._update(record, snapshot)],
                "creates": [
                    {
                        "key": data["attempt_id"],
                        "kind": "attempt",
                        "session_id": body.session_id,
                        "data": data,
                        "parent_record_id": record["record_id"],
                    }
                ],
                "result": data,
                "result_type": "attempt",
                "result_id": data["attempt_id"],
                "audio": assets,
            }

        return self._submit(
            owner,
            body,
            "attempt",
            record,
            work,
            payload=self._audio_payload(body, audio),
            transcription=consume_spec(body, attempt_context(body)),
        )

    def get_attempt(self, owner, attempt_id):
        cursor = None
        while True:
            page = self.repository.list_records(
                owner, kind="attempt", limit=100, cursor=cursor
            )
            for record in page["items"]:
                if record["data"].get("attempt_id") == attempt_id:
                    return record
            cursor = page["next_cursor"]
            if cursor is None:
                raise StoreError("attempt_not_found", "找不到这次练习尝试。", 404)

    def example(self, owner, attempt_id, request_id):
        attempt = self.get_attempt(owner, attempt_id)["data"]
        record = self._session_record(owner, attempt["session_id"])

        def work(claim):
            snapshot = record["data"]
            source = next(
                turn
                for turn in snapshot["turns"]
                if turn["turn_id"] == attempt["source_turn_id"]
            )
            focus = next(
                (
                    item
                    for item in (snapshot.get("feedback") or {}).get("focuses", [])
                    if item["focus_id"] == attempt["focus_id"]
                ),
                None,
            )
            target = attempt.get("target_text") or (
                focus["suggested"] if focus else source["text"]
            )
            content = self.provider.synthesize(target)
            return {
                "resource_record_id": record["record_id"],
                "result": {"audio_key": "example"},
                "result_type": "audio",
                "result_id": attempt_id,
                "audio": [{"key": "example", "content": content, "retain": False}],
            }

        return self.runtime.submit(
            owner,
            request_id,
            "example",
            {"attempt_id": attempt_id},
            work,
            session_id=record["session_id"],
            record_id=record["record_id"],
        )

    def progress(self, owner):
        sessions, attempts = [], []
        for kind, values in (("session", sessions), ("attempt", attempts)):
            cursor = None
            while True:
                page = self.repository.list_records(
                    owner, kind=kind, limit=100, cursor=cursor
                )
                values.extend(item["data"] for item in page["items"])
                cursor = page["next_cursor"]
                if cursor is None:
                    break
        return {
            "sessions_completed": sum(
                item.get("status") == "completed" for item in sessions
            ),
            "attempts_count": len(attempts),
            "retries_count": sum(
                item.get("parent_attempt_id") is not None for item in attempts
            ),
            "reviews_completed": 0,
            "independent_transfer_count": 0,
            "evidence": [
                {
                    "attempt_id": item["attempt_id"],
                    "session_id": item["session_id"],
                    "description": (
                        "已保存同题重练，可回看原句和本次表达。"
                        if item.get("parent_attempt_id")
                        else "已保存首次表达。"
                    ),
                    "kind": (
                        "same_task" if item.get("parent_attempt_id") else "practice"
                    ),
                    "status": item["evaluation"]["status"],
                }
                for item in attempts
            ],
        }
