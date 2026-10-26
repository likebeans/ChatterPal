"""Review acceptance at the public service with real durable operations and UTC time."""

import json
import time
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from chatterpal.learning.catalog import TaskCatalog
from chatterpal.learning.contracts import (
    ReviewAttemptRequest,
    ReviewDeferRequest,
    ReviewHintRequest,
    ReviewWrite,
    SaveReviewRequest,
)
from chatterpal.learning.review import LearningReviewService, public_record
from chatterpal.learning.runtime import LearningRuntime
from chatterpal.learning.sessions import TaskSession
from chatterpal.learning.storage import LearningStore, StoreError
from chatterpal.web.routes.learning_records import COOKIE_NAME, mount_learning_api
from chatterpal.web.routes.learning_review import mount_learning_review


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 25, 8, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, days):
        self.now += timedelta(days=days)


class Provider:
    def __init__(self):
        self.messages = []
        self.result = "met"
        self.fail = False
        self.recognition = "I cooked at home alone last weekend and felt relaxed."
        self.recognition_calls = 0

    def reply(self, messages):
        self.messages.append(messages)
        if self.fail:
            raise RuntimeError("offline provider failure")
        transcript = json.loads(messages[-1]["content"])["transcript"]
        return json.dumps(
            {
                "result": self.result,
                "reason": "The new text supplies the requested details.",
                "evidence": transcript,
            }
        )

    def recognize(self, audio):
        self.recognition_calls += 1
        if self.fail:
            raise RuntimeError("offline ASR failure")
        return self.recognition

    def synthesize(self, text):
        raise AssertionError("Review does not require synthesized audio")


def settle(store, owner, operation):
    deadline = time.monotonic() + 3
    while operation["status"] == "pending" and time.monotonic() < deadline:
        Event().wait(0.003)
        operation = store.get_operation(owner, operation_id=operation["operation_id"])
    assert operation["status"] != "pending", operation
    return operation


def success(env, operation):
    result = settle(env.store, env.owner, operation)
    assert result["status"] == "succeeded", result
    return result["result"]


@pytest.fixture
def review_env(tmp_path):
    clock = Clock()
    store = LearningStore(
        tmp_path / "review.sqlite3", clock=lambda: clock().timestamp()
    )
    identity, token = store.bootstrap()
    owner = identity["learner_id"]
    task = TaskCatalog().get_task("weekend", "1")
    domain = TaskSession.start(
        session_id="source-session",
        task=task,
        correction_mode="after_session",
        now=clock(),
    )
    domain = domain.append_turn(
        expected_revision=0,
        user_turn_id="source-turn",
        assistant_turn_id="coach-turn",
        text="I go hiking last Saturday.",
        reply="Who were you with?",
        input_source="text",
        now=clock(),
    )
    focus = {
        "focus_id": "past-tense",
        "source_turn_id": "source-turn",
        "original": "I go hiking",
        "suggested": "I went hiking",
        "explanation": "Use the past tense.",
        "category": "grammar",
        "source": "text",
    }
    data = {
        "session_id": "source-session",
        "task_id": "weekend",
        "revision": 1,
        "status": "completed",
        "domain": domain.to_snapshot(),
        "turns": [
            {
                "turn_id": "source-turn",
                "role": "user",
                "text": "I go hiking last Saturday.",
            }
        ],
        "feedback": {"feedback_id": "saved-feedback", "focuses": [focus]},
        "attempts": [],
        "created_at": clock().isoformat(),
    }
    record, _ = store.create_record(
        owner,
        "seed-source-session",
        kind="session",
        session_id="source-session",
        data=data,
    )
    provider = Provider()
    runtime = LearningRuntime(store, shutdown_timeout=0.2)
    service = LearningReviewService(store, provider, runtime, clock=clock)
    env = SimpleNamespace(
        clock=clock,
        store=store,
        owner=owner,
        token=token,
        source=record,
        provider=provider,
        runtime=runtime,
        service=service,
    )
    yield env
    runtime.shutdown()


def save(env, request_id="save-review-first"):
    body = SaveReviewRequest(
        request_id=request_id,
        session_id="source-session",
        source_revision=1,
        source_turn_id="source-turn",
        focus_id="past-tense",
    )
    return success(env, env.service.save(env.owner, body))


def test_explicit_save_is_durable_private_and_due_next_day(review_env):
    env = review_env
    assert env.service.due(env.owner)["items"] == []
    item = save(env)
    assert item["due_at"] == "2026-10-26T08:00:00+00:00"
    assert "answer" not in item and "target_text" not in item
    repeated = save(env)
    assert repeated == item
    assert (
        save(env, "save-same-focus-again")["review_item_id"] == item["review_item_id"]
    )
    assert len(env.store.list_records(env.owner, kind="review")["items"]) == 1
    env.clock.advance(1)
    reopened = LearningStore(env.store.path, clock=lambda: env.clock().timestamp())
    restored = LearningReviewService(
        reopened, env.provider, env.runtime, clock=env.clock
    )
    due = restored.due(env.owner)["items"]
    assert due[0]["review_item_id"] == item["review_item_id"]
    assert (
        due[0]["prompt"]
        == TaskCatalog().get_task("weekend", "1").transfer_questions[0].prompt
    )
    assert "answer" not in due[0]
    stranger = env.store.bootstrap()[0]["learner_id"]
    assert restored.due(stranger)["items"] == []
    with pytest.raises(StoreError, match="复习") as error:
        restored.get(stranger, item["review_item_id"])
    assert error.value.status_code == 404


def submit(env, item, request_id="review-answer-first", *, text=None, audio=None):
    current = env.service.get(env.owner, item["review_item_id"])
    body = ReviewAttemptRequest(
        request_id=request_id,
        expected_revision=current["revision"],
        text=text if audio is None else None,
    )
    return success(
        env, env.service.attempt(env.owner, item["review_item_id"], body, audio)
    )


def test_only_verified_new_answers_at_due_time_advance_three_then_seven_days(
    review_env,
):
    env = review_env
    item = save(env)
    assert item["prompt"] is None and item["question_id"] is None
    assert item["prompt_available"] is False
    env.clock.advance(1)
    first = submit(env, item, "first-delayed-answer", text=env.provider.recognition)
    assert first["next_due_at"] == "2026-10-29T08:00:00+00:00"
    assert first["attempt"]["independent"] and first["attempt"]["schedule_advanced"]
    assert first["review"]["prompt"] is None
    assert first["review"]["question_id"] is None
    assert first["review"]["prompt_available"] is False
    env.clock.advance(3)
    assert (
        env.service.get(env.owner, item["review_item_id"])["prompt"]
        == TaskCatalog().get_task("weekend", "1").transfer_questions[1].prompt
    )
    second = submit(
        env,
        item,
        "second-delayed-answer",
        text="I read at home alone and enjoyed the quiet.",
    )
    assert second["next_due_at"] == "2026-11-05T08:00:00+00:00"
    assert "I go hiking last Saturday." not in json.dumps(env.provider.messages)
    assert "I went hiking" not in json.dumps(env.provider.messages)
    assert env.service.progress(env.owner)["independent_transfer_count"] == 2
    repeat = submit(
        env,
        item,
        "immediate-exhausted-repeat",
        text="I watched a movie at home and felt calm.",
    )
    assert repeat["attempt"]["evidence_kind"] == "repeated_prompt"
    assert repeat["attempt"]["delayed"] is False
    assert (
        repeat["attempt"]["independent"] is False
        and not repeat["attempt"]["schedule_advanced"]
    )
    assert repeat["next_due_at"] == second["next_due_at"]
    assert env.service.progress(env.owner)["early_practice_count"] == 0
    assert env.service.progress(env.owner)["repeated_prompt_count"] == 1


@pytest.mark.parametrize("assistance", ["reveal", "hint"])
def test_assistance_is_durable_and_never_claimed_independent_or_cleared_by_defer(
    review_env, assistance
):
    env = review_env
    item = save(env)
    env.clock.advance(1)
    body = (
        ReviewWrite(
            request_id="review-reveal-first", expected_revision=item["revision"]
        )
        if assistance == "reveal"
        else ReviewHintRequest(
            request_id="review-hint-first", expected_revision=item["revision"]
        )
    )
    result = success(
        env, getattr(env.service, assistance)(env.owner, item["review_item_id"], body)
    )
    assert result["used_hint"] is True
    if assistance == "reveal":
        assert result["answer"] == "I went hiking"
    else:
        assert result["hint"] == "Last weekend, I ..."
    restored = LearningReviewService(
        LearningStore(env.store.path, clock=lambda: env.clock().timestamp()),
        env.provider,
        env.runtime,
        clock=env.clock,
    )
    assert restored.get(env.owner, item["review_item_id"])["used_hint"] is True
    # Deferral alone cannot launder assistance into an independent answer.
    deferred = success(
        env,
        env.service.defer(
            env.owner,
            item["review_item_id"],
            ReviewDeferRequest(
                request_id="defer-hinted-review", expected_revision=result["revision"]
            ),
        ),
    )
    assert deferred["due_at"] == "2026-10-27T08:00:00+00:00"
    assert deferred["used_hint"] and deferred["cycle"] == 0
    assert deferred["prompt"] is None
    env.clock.advance(1)
    answer = submit(env, item, text=env.provider.recognition)
    assert answer["next_due_at"] == deferred["due_at"]
    assert answer["used_hint"] and not answer["attempt"]["independent"]
    assert answer["attempt"]["variant_consumed"]
    assert answer["review"]["cycle"] == 1 and not answer["review"]["used_hint"]
    # The second authored question starts a new cycle with its own assistance.
    new_variant = submit(
        env, item, "answer-next-variant", text="I read at home alone and felt relaxed."
    )
    assert (
        new_variant["attempt"]["independent"]
        and not new_variant["attempt"]["used_hint"]
    )
    assert new_variant["next_due_at"] == "2026-10-30T08:00:00+00:00"
    progress = env.service.progress(env.owner)
    assert progress["reviews_completed"] == 2
    assert progress["hinted_review_count"] == 1
    assert progress["independent_transfer_count"] == 1


@pytest.mark.parametrize("outcome", ["not_met", "unverified", "unavailable"])
def test_not_met_uncertain_or_unavailable_never_extend_the_schedule(
    review_env, outcome
):
    env = review_env
    item = save(env)
    env.clock.advance(1)
    env.provider.result = outcome if outcome != "unavailable" else "met"
    env.provider.fail = outcome == "unavailable"
    answer = submit(env, item, text=env.provider.recognition)
    assert answer["next_due_at"] == item["due_at"]
    assert answer["attempt"]["schedule_advanced"] is False
    assert env.service.progress(env.owner)["independent_transfer_count"] == 0
    assert len(env.store.list_records(env.owner, kind="review_attempt")["items"]) == 1
    assert answer["attempt"]["variant_consumed"] is (outcome != "unavailable")
    current = env.service.get(env.owner, item["review_item_id"])
    assert current["cycle"] == (0 if outcome == "unavailable" else 1)
    progress = env.service.progress(env.owner)
    assert progress["reviews_completed"] == (1 if outcome == "not_met" else 0)
    assert progress["unassessed_reviews_count"] == (0 if outcome == "not_met" else 1)


def test_audio_submission_and_replay_commit_one_attempt_and_one_due_update(
    review_env, audio_bytes
):
    env = review_env
    item = save(env)
    env.clock.advance(1)
    body = ReviewAttemptRequest(request_id="audio-review-request", expected_revision=1)
    first = success(
        env, env.service.attempt(env.owner, item["review_item_id"], body, audio_bytes)
    )
    repeated = success(
        env, env.service.attempt(env.owner, item["review_item_id"], body, audio_bytes)
    )
    assert first == repeated
    assert env.provider.recognition_calls == 1
    assert first["attempt"]["audio"]["state"] == "available"
    assert (
        env.store.get_audio(env.owner, first["attempt"]["audio"]["audio_asset_id"])[1]
        == audio_bytes
    )
    assert len(env.store.list_records(env.owner, kind="review_attempt")["items"]) == 1
    with pytest.raises(StoreError) as conflict:
        env.service.attempt(
            env.owner, item["review_item_id"], body, audio_bytes + b"different"
        )
    assert conflict.value.code == "request_conflict"


def test_source_repeat_and_exhausted_authored_variants_are_not_independent_evidence(
    review_env,
):
    env = review_env
    item = save(env)
    raw = env.store.list_records(env.owner, kind="review")["items"][0]["data"]
    assert not {
        "focus_snapshot",
        "attempt_snapshot",
        "target_text",
        "answer",
    }.intersection(raw)
    env.clock.advance(1)
    repeated = submit(env, item, text="I go hiking last Saturday.")
    assert repeated["attempt"]["evidence_kind"] == "source_repeat"
    assert not repeated["attempt"]["schedule_advanced"]
    assert repeated["next_due_at"] == item["due_at"]
    assert repeated["attempt"]["variant_consumed"] and repeated["review"]["cycle"] == 1
    submit(env, item, "first-new-delayed-answer", text=env.provider.recognition)
    env.clock.advance(3)
    current = env.service.get(env.owner, item["review_item_id"])
    assert current["independent_variant_available"] is False
    assert current["prompt_available"] is True
    no_variant = submit(env, item, "third-reused-prompt", text=env.provider.recognition)
    assert no_variant["attempt"]["evidence_kind"] == "repeated_prompt"
    assert no_variant["attempt"]["evaluation"]["status"] == "available"
    assert (
        not no_variant["attempt"]["schedule_advanced"]
        and not no_variant["attempt"]["independent"]
    )
    assert no_variant["next_due_at"] == "2026-10-29T08:00:00+00:00"
    progress = env.service.progress(env.owner)
    assert progress["independent_transfer_count"] == 1
    assert (
        progress["source_repeat_count"] == 1 and progress["repeated_prompt_count"] == 1
    )
    assert [item["kind"] for item in progress["evidence"]] == [
        "practice",
        "transfer",
        "practice",
    ]


def test_technical_audio_failure_preserves_schedule_and_source_deletion_removes_descendants(
    review_env, audio_bytes
):
    env = review_env
    item = save(env)
    env.clock.advance(1)
    env.provider.fail = True
    failed = settle(
        env.store,
        env.owner,
        env.service.attempt(
            env.owner,
            item["review_item_id"],
            ReviewAttemptRequest(request_id="failed-audio-review", expected_revision=1),
            audio_bytes,
        ),
    )
    assert failed["status"] == "failed"
    assert (
        env.service.get(env.owner, item["review_item_id"])["due_at"] == item["due_at"]
    )
    assert env.store.list_records(env.owner, kind="review_attempt")["items"] == []
    env.provider.fail = False
    submit(env, item, text=env.provider.recognition)
    env.store.delete_record(env.owner, env.source["record_id"])
    assert env.service.due(env.owner)["items"] == []
    assert env.store.list_records(env.owner, kind="review_attempt")["items"] == []
    assert env.service.progress(env.owner)["reviews_completed"] == 0


def test_early_practice_consumes_once_without_exposing_future_prompt_or_advancing(
    review_env,
):
    env = review_env
    item = save(env)
    future_prompts = [
        question.prompt
        for question in TaskCatalog().get_task("weekend", "1").transfer_questions
    ]
    early = submit(env, item, text=env.provider.recognition)
    assert early["attempt"]["evidence_kind"] == "early_practice"
    assert early["attempt"]["question_id"] == "early-practice"
    assert early["attempt"]["variant_consumed"]
    assert early["review"]["cycle"] == 1 and early["review"]["prompt"] is None
    assert early["next_due_at"] == item["due_at"]
    assert all(prompt not in json.dumps(early) for prompt in future_prompts)
    progress = env.service.progress(env.owner)
    assert progress["early_practice_count"] == 1
    assert progress["review_attempts_count"] == 1 and progress["reviews_completed"] == 0
    assert progress["independent_transfer_count"] == 0
    env.clock.advance(1)
    # Waiting and repeating an earlier answer cannot manufacture new evidence.
    repeated = submit(env, item, "repeat-early-answer", text=env.provider.recognition)
    assert repeated["attempt"]["evidence_kind"] == "source_repeat"
    assert not repeated["attempt"]["schedule_advanced"]
    assert repeated["review"]["independent_variant_available"] is False


@pytest.mark.parametrize("method", ["reveal", "hint"])
def test_assistance_before_due_neither_exposes_answers_nor_consumes_a_variant(
    review_env, method
):
    env = review_env
    item = save(env)
    model = ReviewHintRequest if method == "hint" else ReviewWrite
    operation = getattr(env.service, method)(
        env.owner,
        item["review_item_id"],
        model(request_id=f"premature-{method}-request", expected_revision=1),
    )
    failed = settle(env.store, env.owner, operation)
    assert failed["status"] == "failed" and failed["error"]["code"] == "review_not_due"
    current = env.service.get(env.owner, item["review_item_id"])
    assert current == {**item, "attempts": []} and current["used_hint"] is False


def test_provider_failure_keeps_question_available_for_a_delayed_retry(review_env):
    env = review_env
    item = save(env)
    env.clock.advance(1)
    env.provider.fail = True
    unavailable = submit(env, item, text=env.provider.recognition)
    assert unavailable["attempt"]["evaluation"]["status"] == "unavailable"
    assert unavailable["attempt"]["variant_consumed"] is False
    assert unavailable["review"]["cycle"] == 0
    assert unavailable["next_due_at"] == item["due_at"]
    env.provider.fail = False
    verified = submit(
        env, item, "retry-provider-failure", text=env.provider.recognition
    )
    assert verified["attempt"]["schedule_advanced"] and verified["review"]["cycle"] == 1
    progress = env.service.progress(env.owner)
    assert progress["review_attempts_count"] == 2 and progress["reviews_completed"] == 1
    assert progress["unassessed_reviews_count"] == 1
    restored = LearningReviewService(
        LearningStore(env.store.path, clock=lambda: env.clock().timestamp()),
        env.provider,
        env.runtime,
        clock=env.clock,
    )
    history = restored.get(env.owner, item["review_item_id"])["attempts"]
    by_id = {attempt["attempt_id"]: attempt for attempt in history}
    assert set(by_id) == {
        unavailable["attempt"]["attempt_id"],
        verified["attempt"]["attempt_id"],
    }
    assert (
        by_id[unavailable["attempt"]["attempt_id"]]["evaluation"]["status"]
        == "unavailable"
    )
    assert by_id[verified["attempt"]["attempt_id"]]["schedule_advanced"]
    assert (
        by_id[verified["attempt"]["attempt_id"]]["transcript"]
        == env.provider.recognition
    )


def test_early_receipt_stays_early_when_provider_finishes_after_due(review_env):
    env = review_env
    item = save(env)
    env.clock.now = datetime.fromisoformat(item["due_at"]) - timedelta(seconds=10)
    original_reply = env.provider.reply

    def reply_after_due(messages):
        env.clock.now += timedelta(seconds=20)
        return original_reply(messages)

    env.provider.reply = reply_after_due
    answer = submit(env, item, text=env.provider.recognition)
    assert answer["attempt"]["evidence_kind"] == "early_practice"
    assert answer["attempt"]["delayed"] is False
    assert answer["next_due_at"] == item["due_at"]


def test_raw_review_keeps_no_inline_target_and_public_projection_is_detached(
    review_env,
):
    env = review_env
    source = env.store.get_record(env.owner, env.source["record_id"])
    source["data"]["feedback"]["focuses"][0][
        "suggested"
    ] = "I hiked along a coastal trail on Saturday."
    env.source, _ = env.store.update_record(
        env.owner,
        "pin-unique-review-target",
        source["record_id"],
        expected_revision=1,
        data=source["data"],
    )
    item = success(
        env,
        env.service.save(
            env.owner,
            SaveReviewRequest(
                request_id="save-unique-target",
                session_id="source-session",
                source_revision=2,
                source_turn_id="source-turn",
                focus_id="past-tense",
            ),
        ),
    )
    record = env.store.list_records(env.owner, kind="review")["items"][0]
    raw_json = json.dumps(record["data"])
    assert (
        "I hiked along a coastal trail on Saturday." not in raw_json
        and "I go hiking last Saturday." not in raw_json
    )
    projected = public_record(record, clock=env.clock)
    assert projected["record_id"] == record["record_id"] and projected["data"] == item
    assert not {
        "task_snapshot",
        "source_record_id",
        "target_sha256",
        "source_text",
        "source_attempt_text",
    }.intersection(projected["data"])
    assert record["data"]["task_snapshot"]["transfer_questions"]
    env.clock.advance(1)
    answer = submit(env, item, text=env.provider.recognition)
    attempt_record = env.store.list_records(env.owner, kind="review_attempt")["items"][
        0
    ]
    safe_attempt = public_record(attempt_record, clock=env.clock)
    assert safe_attempt["data"]["attempt_id"] == answer["attempt"]["attempt_id"]
    assert safe_attempt["data"]["transcript"] == env.provider.recognition


@pytest.mark.asyncio
async def test_review_http_routes_enforce_identity_revision_and_durable_assistance(
    review_env, audio_bytes
):
    env = review_env
    item = save(env)
    app = FastAPI()
    settings = SimpleNamespace(learning_max_audio_bytes=2 * 1024 * 1024)
    mount_learning_api(app, settings, store=env.store, allow_record_writes=False)
    mount_learning_review(app, settings, service=env.service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/learning/reviews/due")).status_code == 401
        client.cookies.set(COOKIE_NAME, env.token, path="/api/learning")
        assert (await client.get("/api/learning/reviews/due")).json()["items"] == []
        raw = env.store.list_records(env.owner, kind="review")["items"][0]
        public = (await client.get(f"/api/learning/records/{raw['record_id']}")).json()[
            "data"
        ]
        assert not {
            "task_snapshot",
            "target_sha256",
            "answer",
            "target_text",
        }.intersection(public)
        raw_list = (await client.get("/api/learning/records?kind=review")).json()[
            "items"
        ]
        assert raw_list[0]["data"]["prompt"] is None
        env.clock.advance(1)
        current = (
            await client.get(f"/api/learning/reviews/{item['review_item_id']}")
        ).json()
        assert current["prompt_available"] is True
        payload = {"request_id": "http-review-reveal", "expected_revision": 1}
        response = await client.post(
            f"/api/learning/reviews/{item['review_item_id']}/reveal", json=payload
        )
        assert response.status_code == 202
        revealed = success(env, response.json())
        assert revealed["answer"] == "I went hiking" and revealed["revision"] == 2
        conflict = await client.post(
            f"/api/learning/reviews/{item['review_item_id']}/attempts",
            json={
                "request_id": "http-review-stale",
                "expected_revision": 1,
                "text": env.provider.recognition,
            },
        )
        assert conflict.status_code == 409
        forged = await client.post(
            f"/api/learning/reviews/{item['review_item_id']}/attempts",
            json={
                "request_id": "http-review-forged",
                "expected_revision": 2,
                "text": env.provider.recognition,
                "used_hint": False,
            },
        )
        assert forged.status_code == 422
        response = await client.post(
            f"/api/learning/reviews/{item['review_item_id']}/attempts",
            data={"request_id": "http-review-audio", "expected_revision": "2"},
            files={"audio": ("review.wav", audio_bytes, "audio/wav")},
        )
        assert response.status_code == 202
        result = success(env, response.json())
        assert result["attempt"]["used_hint"] and result["attempt"]["revealed_answer"]
        assert not result["attempt"]["schedule_advanced"]
        assert env.provider.recognition_calls == 1
        progress = (await client.get("/api/learning/progress")).json()
        assert (
            progress["independent_transfer_count"] == 0
            and progress["hinted_review_count"] == 1
        )
        stranger, token = env.store.bootstrap()
        client.cookies.set(COOKIE_NAME, token, path="/api/learning")
        assert (
            await client.get(f"/api/learning/reviews/{item['review_item_id']}")
        ).status_code == 404
        assert (await client.get("/api/learning/reviews/due")).json()["items"] == []
        assert stranger["learner_id"] != env.owner


def test_source_fingerprint_change_fails_without_advancing_or_consuming(review_env):
    env = review_env
    item = save(env)
    env.clock.advance(1)
    source = env.store.get_record(env.owner, env.source["record_id"])
    source["data"]["feedback"]["focuses"][0]["suggested"] = "A new correction"
    env.store.update_record(
        env.owner,
        "update-review-source",
        source["record_id"],
        expected_revision=1,
        data=source["data"],
    )
    operation = env.service.attempt(
        env.owner,
        item["review_item_id"],
        ReviewAttemptRequest(
            request_id="changed-source-answer",
            expected_revision=1,
            text=env.provider.recognition,
        ),
    )
    failed = settle(env.store, env.owner, operation)
    assert (
        failed["status"] == "failed"
        and failed["error"]["code"] == "review_source_changed"
    )
    current = env.service.get(env.owner, item["review_item_id"])
    assert current["cycle"] == 0 and current["due_at"] == item["due_at"]
    assert env.store.list_records(env.owner, kind="review_attempt")["items"] == []
