"""Durable delayed reviews built on generic, revision-checked learning operations."""

import copy
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from .contracts import ReviewAttemptRequest, ReviewHintRequest, SaveReviewRequest
from .feedback import _words, evaluate_attempt
from .review_rules import next_due, scheduling_evidence, timestamp, utc
from .sessions import TaskSession
from .storage import StoreError
from .tasks import TaskDefinition


def utc_now():
    return datetime.now(timezone.utc)


def review_view(record, now):
    """Project a saved review without answers, internal snapshots or future prompts."""
    item = record["data"]
    questions = item["task_snapshot"]["transfer_questions"]
    fresh = item["cycle"] < len(questions)
    prompt_available = utc(now) >= timestamp(item["due_at"]) or not fresh
    question = questions[min(item["cycle"], len(questions) - 1)]
    assistance = [
        event for event in item["assistance"] if event["cycle"] == item["cycle"]
    ]
    reason = ""
    if not fresh:
        reason = (
            "已用完当前内容中的新问题；可以继续重复练习，本次不会增加独立迁移记录。"
        )
    elif not prompt_available:
        reason = "新问题将在到期后显示；提前练习不会计入延迟迁移记录。"
    return {
        "review_item_id": item["review_item_id"],
        "session_id": item["session_id"],
        "source_turn_id": item["source_turn_id"],
        "focus_id": item["focus_id"],
        "source_attempt_id": item["source_attempt_id"],
        "task_id": item["task_snapshot"]["task_id"],
        "content_version": item["task_snapshot"]["content_version"],
        "prompt": question["prompt"] if prompt_available else None,
        "question_id": question["question_id"] if prompt_available else None,
        "prompt_available": prompt_available,
        "cycle": item["cycle"],
        "independent_variant_available": fresh,
        "availability_reason": reason,
        "due_at": item["due_at"],
        "revision": record["revision"],
        "used_hint": bool(assistance),
        "revealed_answer": any(event["kind"] == "reveal" for event in assistance),
    }


def public_record(record, *, clock=utc_now):
    """Safe generic-record projection; requires no provider or repository setup.

    Keep the record envelope and replace only business data. Exports intentionally
    use the repository's complete records; ordinary reads use this projection.
    """
    if record["kind"] == "review":
        return {**record, "data": review_view(record, clock())}
    if record["kind"] == "review_attempt":
        fields = {
            "attempt_id",
            "session_id",
            "review_item_id",
            "source_turn_id",
            "focus_id",
            "parent_attempt_id",
            "mode",
            "transcript",
            "feedback",
            "evaluation",
            "created_at",
            "audio",
            "question_id",
            "prompt",
            "content_version",
            "cycle",
            "scheduled_due_at",
            "used_hint",
            "revealed_answer",
            "independent",
            "delayed",
            "schedule_advanced",
            "variant_consumed",
            "evidence_kind",
        }
        return {
            **record,
            "data": {
                key: value for key, value in record["data"].items() if key in fields
            },
        }
    return record


class LearningReviewService:
    def __init__(self, repository, provider, runtime, *, clock=utc_now):
        self.repository = repository
        self.provider = provider
        self.runtime = runtime
        self.clock = clock

    def _records(self, owner, kind):
        cursor = None
        while True:
            page = self.repository.list_records(
                owner, kind=kind, limit=100, cursor=cursor
            )
            yield from page["items"]
            cursor = page["next_cursor"]
            if cursor is None:
                break

    def _source(self, owner, session_id):
        page = self.repository.list_records(
            owner, kind="session", session_id=session_id, limit=1
        )
        if not page["items"]:
            raise StoreError("session_not_found", "找不到这次练习。", 404)
        return page["items"][0]

    def _review(self, owner, item_id):
        for record in self._records(owner, "review"):
            if record["data"].get("review_item_id") == item_id:
                return record
        raise StoreError("review_not_found", "找不到这项复习。", 404)

    @staticmethod
    def _question(item):
        task = TaskDefinition.model_validate(item["task_snapshot"])
        return task.transfer_questions[
            min(item["cycle"], len(task.transfer_questions) - 1)
        ]

    def _view(self, record):
        return review_view(record, self.clock())

    def public_record(self, record):
        return public_record(record, clock=self.clock)

    def get(self, owner, item_id):
        view = self._view(self._review(owner, item_id))
        view["attempts"] = [
            self.public_record(record)["data"]
            for record in self._records(owner, "review_attempt")
            if record["data"]["review_item_id"] == item_id
        ]
        return view

    @staticmethod
    def _digest(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _source_context(self, owner, item):
        # Corrections and previous expressions stay in the owned source record.
        # Only the fingerprint, source revision and stable IDs live in the review.
        source = self.repository.get_record(owner, item["source_record_id"])["data"]
        focus = next(
            (
                focus
                for focus in (source.get("feedback") or {}).get("focuses", [])
                if focus["focus_id"] == item["focus_id"]
                and focus["source_turn_id"] == item["source_turn_id"]
            ),
            None,
        )
        attempt = next(
            (
                attempt
                for attempt in source.get("attempts", [])
                if attempt["attempt_id"] == item["source_attempt_id"]
                and attempt["source_turn_id"] == item["source_turn_id"]
                and attempt.get("focus_id") == item["focus_id"]
            ),
            None,
        )
        target = (
            focus["suggested"]
            if focus
            else (
                (attempt.get("target_text") or attempt["transcript"])
                if attempt
                else None
            )
        )
        if target is None or self._digest(target) != item["target_sha256"]:
            raise StoreError(
                "review_source_changed",
                "保存时的参考表达已变化，无法核对；请从学习记录重新保存。",
                409,
            )
        turn = next(
            (
                turn
                for turn in source["domain"]["turns"]
                if turn["user_turn_id"] == item["source_turn_id"]
            ),
            None,
        )
        if turn is None:
            raise StoreError(
                "review_source_changed",
                "无法核对保存时的原句，请从学习记录重新保存。",
                409,
            )
        return target, (
            turn["text"],
            attempt["transcript"] if attempt else None,
            target,
        )

    def _target(self, owner, item):
        return self._source_context(owner, item)[0]

    def due(self, owner):
        now = utc(self.clock())
        items = [
            self._view(record)
            for record in self._records(owner, "review")
            if timestamp(record["data"]["due_at"]) <= now
        ]
        items.sort(key=lambda item: (item["due_at"], item["review_item_id"]))
        return {"items": items, "next_cursor": None}

    def save(self, owner, body: SaveReviewRequest):
        source = self._source(owner, body.session_id)

        def work(claim):
            snapshot = source["data"]
            try:
                domain = TaskSession.from_snapshot(snapshot["domain"])
            except (KeyError, ValueError) as error:
                raise StoreError(
                    "invalid_session", "练习来源不完整，无法保存复习。", 409
                ) from error
            turn = next(
                (
                    turn
                    for turn in domain.turns
                    if turn.user_turn_id == body.source_turn_id
                ),
                None,
            )
            if turn is None:
                raise StoreError("turn_not_found", "找不到复习关联的原句。", 404)
            focus = None
            if body.focus_id is not None:
                focus = next(
                    (
                        focus
                        for focus in (snapshot.get("feedback") or {}).get("focuses", [])
                        if focus["focus_id"] == body.focus_id
                        and focus["source_turn_id"] == body.source_turn_id
                    ),
                    None,
                )
                if focus is None:
                    raise StoreError("focus_not_found", "这项反馈不属于当前原句。", 404)
            attempt = None
            if body.attempt_id is not None:
                attempt = next(
                    (
                        attempt
                        for attempt in snapshot.get("attempts", [])
                        if attempt["attempt_id"] == body.attempt_id
                        and attempt["source_turn_id"] == body.source_turn_id
                        and attempt.get("focus_id") == body.focus_id
                    ),
                    None,
                )
                if attempt is None:
                    raise StoreError(
                        "attempt_not_found", "这次尝试不属于当前原句和重点。", 404
                    )
            if focus is None and attempt is None:
                raise StoreError(
                    "review_source_required",
                    "请明确选择一项反馈或已保存的尝试来安排复习。",
                    422,
                )
            key = {
                "session_id": body.session_id,
                "source_turn_id": body.source_turn_id,
                "focus_id": body.focus_id,
                "source_attempt_id": body.attempt_id,
            }
            for existing in self._records(owner, "review"):
                if all(
                    existing["data"].get(name) == value for name, value in key.items()
                ):
                    return {
                        "result": self._view(existing),
                        "result_type": "review_saved",
                        "result_id": existing["data"]["review_item_id"],
                    }
            now = utc(self.clock())
            item_id = str(uuid.uuid4())
            item = {
                **key,
                "review_item_id": item_id,
                "source_record_id": source["record_id"],
                "source_revision": source["revision"],
                "task_snapshot": domain.task.to_snapshot(),
                "target_sha256": self._digest(
                    focus["suggested"]
                    if focus
                    else attempt.get("target_text") or attempt["transcript"]
                ),
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "due_at": next_due(now, 0),
                "cycle": 0,
                "independent_successes": 0,
                "assistance": [],
                "deferrals": [],
            }
            return {
                "creates": [
                    {
                        "key": item_id,
                        "kind": "review",
                        "session_id": body.session_id,
                        "parent_record_id": source["record_id"],
                        "data": item,
                    }
                ],
                "result": self._view({"data": item, "revision": 1}),
                "result_type": "review_saved",
                "result_id": item_id,
            }

        return self.runtime.submit(
            owner,
            body.request_id,
            "save-review",
            body.model_dump(mode="json"),
            work,
            session_id=body.session_id,
            record_id=source["record_id"],
            expected_revision=body.source_revision,
        )

    @staticmethod
    def _update(record, item):
        return {
            "record_id": record["record_id"],
            "expected_revision": record["revision"],
            "data": item,
        }

    def _submit(self, owner, record, body, action, work, *, payload=None):
        return self.runtime.submit(
            owner,
            body.request_id,
            action,
            (
                payload
                if payload is not None
                else {
                    **body.model_dump(mode="json"),
                    "review_item_id": record["data"]["review_item_id"],
                }
            ),
            work,
            session_id=record["session_id"],
            record_id=record["record_id"],
            expected_revision=body.expected_revision,
        )

    def assist(self, owner, item_id, body, kind):
        record = self._review(owner, item_id)

        def work(claim):
            item = copy.deepcopy(record["data"])
            if not self._view(record)["prompt_available"]:
                raise StoreError(
                    "review_not_due",
                    "新问题将在到期后开放；请到时再使用提示或参考表达。",
                    409,
                )
            now = utc(self.clock()).isoformat()
            event = {
                "kind": kind,
                "cycle": item["cycle"],
                "created_at": now,
                "request_id": body.request_id,
            }
            if kind == "hint":
                event["hint_kind"] = body.kind
            item["assistance"].append(event)
            item["updated_at"] = now
            result = self._view({"data": item, "revision": record["revision"] + 1})
            if kind == "reveal":
                result["answer"] = self._target(owner, item)
                result["answer_context"] = (
                    "保存时的参考表达，仅供回看；新情境可以使用其他正确说法。"
                )
            else:
                task = TaskDefinition.model_validate(item["task_snapshot"])
                step = next(
                    step
                    for step in task.steps
                    if step.criterion_id in self._question(item).criterion_ids
                )
                result["hint"] = getattr(step, body.kind)
            return {
                "updates": [self._update(record, item)],
                "result": result,
                "result_type": "review_reveal" if kind == "reveal" else "review_hint",
                "result_id": item_id,
            }

        return self._submit(
            owner, record, body, "reveal" if kind == "reveal" else "review-hint", work
        )

    def reveal(self, owner, item_id, body):
        return self.assist(owner, item_id, body, "reveal")

    def hint(self, owner, item_id, body: ReviewHintRequest):
        return self.assist(owner, item_id, body, "hint")

    def defer(self, owner, item_id, body):
        record = self._review(owner, item_id)

        def work(claim):
            item = copy.deepcopy(record["data"])
            now = utc(self.clock())
            item["due_at"] = (
                max(now, timestamp(item["due_at"])) + timedelta(days=body.days)
            ).isoformat()
            item["updated_at"] = now.isoformat()
            # Deferring cannot remove hints or start a fresh independent cycle.
            item["deferrals"].append(
                {
                    "created_at": now.isoformat(),
                    "days": body.days,
                    "request_id": body.request_id,
                }
            )
            return {
                "updates": [self._update(record, item)],
                "result": self._view(
                    {"data": item, "revision": record["revision"] + 1}
                ),
                "result_type": "review_deferred",
                "result_id": item_id,
            }

        return self._submit(owner, record, body, "defer-review", work)

    def _evaluation_session(self, item, now, *, early=False):
        # The evaluator sees a pinned authored transfer task and the NEW transcript.
        # Neither saved source text, a past attempt nor the answer is supplied.
        task = copy.deepcopy(item["task_snapshot"])
        question = self._question(item)
        prompt = task["free_response"]["prompt"] if early else question.prompt
        task["free_response"]["prompt"] = prompt
        task["free_response"]["required_information"] = [
            criterion["description"]
            for criterion in task["criteria"]
            if criterion["criterion_id"] in question.criterion_ids
        ]
        task["criteria"] = [
            criterion
            for criterion in task["criteria"]
            if criterion["criterion_id"] in question.criterion_ids
        ]
        task["steps"] = [
            step
            for step in task["steps"]
            if step["criterion_id"] in question.criterion_ids
        ]
        task["transfer_questions"] = [
            question.model_dump(mode="json"),
            {
                **question.model_dump(mode="json"),
                "question_id": "review-evidence-context",
            },
        ]
        domain = TaskSession.start(
            session_id=item["review_item_id"],
            task=TaskDefinition.model_validate(task),
            correction_mode="after_session",
            now=now,
        )
        return domain.append_turn(
            expected_revision=0,
            user_turn_id="review-context",
            assistant_turn_id="review-prompt",
            text="No previous learner evidence supplied for this review.",
            reply=prompt,
            input_source="text",
            now=now,
        )

    def attempt(self, owner, item_id, body: ReviewAttemptRequest, audio=None):
        record = self._review(owner, item_id)
        payload = {
            **body.model_dump(mode="json"),
            "review_item_id": item_id,
            "audio_sha256": (
                hashlib.sha256(audio).hexdigest() if audio is not None else None
            ),
        }
        # Waiting for a worker or provider must not turn an early answer into a
        # delayed one. Eligibility is fixed at server receipt, before those calls.
        submitted_at = utc(self.clock())

        def work(claim):
            item = copy.deepcopy(record["data"])
            if (body.text is None) == (audio is None):
                raise StoreError(
                    "input_required", "请选择文字或录音其中一种方式回答。", 422
                )
            if audio is not None:
                self.repository._validate_audio(audio)
            text = self.provider.recognize(audio) if audio is not None else body.text
            if not isinstance(text, str) or not text.strip():
                raise StoreError(
                    "no_speech", "未识别到有效表达，请重新录音或输入文字。", 422
                )
            if len(text) > 12000:
                raise StoreError(
                    "text_too_large", "这段表达过长，请分成较短的回答。", 413
                )
            now = submitted_at
            early = now < timestamp(item["due_at"]) and item["cycle"] < len(
                item["task_snapshot"]["transfer_questions"]
            )
            _, source_texts = self._source_context(owner, item)
            evaluation = evaluate_attempt(
                self.provider,
                self._evaluation_session(item, now, early=early),
                text,
                None,
                "free_response",
                "review-context",
            )
            assessment = evaluation.model_dump(mode="json")
            normalized = _words(text)
            previous = [
                *source_texts,
                *(
                    attempt["data"]["transcript"]
                    for attempt in self._records(owner, "review_attempt")
                    if attempt["data"]["review_item_id"] == item_id
                    and attempt["data"]["evaluation"]["status"] != "unavailable"
                ),
            ]
            source_repeat = any(
                normalized == _words(value) for value in previous if value
            )
            evidence = scheduling_evidence(
                item, assessment, now, source_repeat=source_repeat
            )
            question = self._question(item)
            attempt = {
                "attempt_id": str(uuid.uuid4()),
                "session_id": item["session_id"],
                "review_item_id": item_id,
                "source_turn_id": item["source_turn_id"],
                "focus_id": item["focus_id"],
                "parent_attempt_id": item["source_attempt_id"],
                "mode": "free_response",
                "transcript": text,
                "feedback": None,
                "evaluation": assessment,
                "created_at": now.isoformat(),
                "audio": (
                    {"audio_key": "review"}
                    if audio is not None
                    else {"state": "not_saved"}
                ),
                "question_id": "early-practice" if early else question.question_id,
                "prompt": (
                    item["task_snapshot"]["free_response"]["prompt"]
                    if early
                    else question.prompt
                ),
                "content_version": item["task_snapshot"]["content_version"],
                "cycle": item["cycle"],
                "scheduled_due_at": item["due_at"],
                **evidence,
            }
            if evidence["schedule_advanced"]:
                item["independent_successes"] += 1
                item["due_at"] = next_due(now, item["independent_successes"])
            if evidence["variant_consumed"]:
                item["cycle"] += 1
            item["updated_at"] = now.isoformat()
            assets = (
                [
                    {
                        "key": "review",
                        "content": audio,
                        "retain": self.repository.get_profile(owner)["retain_audio"],
                    }
                ]
                if audio is not None
                else []
            )
            return {
                "updates": [self._update(record, item)],
                "creates": [
                    {
                        "key": attempt["attempt_id"],
                        "kind": "review_attempt",
                        "session_id": item["session_id"],
                        "data": attempt,
                        "parent_record_id": record["record_id"],
                    }
                ],
                "result": {
                    "review_item_id": item_id,
                    "attempt": attempt,
                    "next_due_at": item["due_at"],
                    "used_hint": evidence["used_hint"],
                    "review": self._view(
                        {"data": item, "revision": record["revision"] + 1}
                    ),
                },
                "result_type": "review",
                "result_id": attempt["attempt_id"],
                "audio": assets,
            }

        return self._submit(owner, record, body, "review", work, payload=payload)

    def progress(self, owner):
        sessions = [record["data"] for record in self._records(owner, "session")]
        attempts = [record["data"] for record in self._records(owner, "attempt")]
        reviews = [record["data"] for record in self._records(owner, "review_attempt")]
        evidence = [
            {
                "attempt_id": item["attempt_id"],
                "session_id": item["session_id"],
                "description": (
                    "已保存同题重练，可回看原句和本次表达。"
                    if item.get("parent_attempt_id")
                    else "已保存首次表达。"
                ),
                "kind": "same_task" if item.get("parent_attempt_id") else "practice",
                "status": item["evaluation"]["status"],
            }
            for item in attempts
        ]
        descriptions = {
            "shown_answer": "看过参考表达后的复习，按有提示练习记录。",
            "hinted": "使用提示后的复习，未计为独立迁移。",
            "early_practice": "到期前的练习，未计为延迟迁移。",
            "source_repeat": "复述保存的原句或参考表达，未计为新问题中的独立迁移。",
            "repeated_prompt": "已用完新问题，本次按重复问题练习记录。",
            "delayed_transfer": "到期后回答了新问题；尚无本次文字达成的完整证据。",
        }
        for item in reviews:
            description = (
                "到期后独立回答新问题，已有本次文字达成证据。"
                if item["schedule_advanced"]
                else descriptions[item["evidence_kind"]]
            )
            if item["evaluation"]["status"] == "unavailable":
                description += " 评价暂不可用，回答已保存，排程和新问题均保留。"
            evidence.append(
                {
                    "attempt_id": item["attempt_id"],
                    "session_id": item["session_id"],
                    "review_item_id": item["review_item_id"],
                    "description": description,
                    "kind": "transfer" if item["schedule_advanced"] else "practice",
                    "status": item["evaluation"]["status"],
                    "revealed_answer": item["revealed_answer"],
                    "used_hint": item["used_hint"],
                    "independent": item["independent"],
                    "delayed": item["delayed"],
                    "evidence_kind": item["evidence_kind"],
                    "schedule_advanced": item["schedule_advanced"],
                    "result": item["evaluation"]["result"],
                }
            )
        return {
            "sessions_completed": sum(
                item.get("status") == "completed" for item in sessions
            ),
            "attempts_count": len(attempts) + len(reviews),
            "retries_count": sum(
                item.get("parent_attempt_id") is not None for item in attempts
            ),
            "review_attempts_count": len(reviews),
            "reviews_completed": sum(
                item["delayed"] and item["evaluation"]["status"] == "available"
                for item in reviews
            ),
            "independent_transfer_count": sum(
                item["schedule_advanced"] for item in reviews
            ),
            "early_practice_count": sum(
                item["evidence_kind"] == "early_practice" for item in reviews
            ),
            "source_repeat_count": sum(
                item["evidence_kind"] == "source_repeat" for item in reviews
            ),
            "hinted_review_count": sum(item["used_hint"] for item in reviews),
            "repeated_prompt_count": sum(
                item["evidence_kind"] == "repeated_prompt" for item in reviews
            ),
            "unassessed_reviews_count": sum(
                item["evaluation"]["status"] != "available" for item in reviews
            ),
            "evidence": evidence,
        }
