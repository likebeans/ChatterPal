"""Transcript confirmation through the real API, runtime and SQLite transactions."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.learning.test_int2_weekend_flow import (
    PREFIX,
    application as application_fixture,
    browser,
    completed,
    operation,
    start,
    succeeded,
    turn,
)

application = application_fixture


async def preview(client, context, audio, request_id="transcription-preview-001"):
    return await succeeded(
        client,
        await client.post(
            f"{PREFIX}/transcriptions",
            data={"request_id": request_id, "context": json.dumps(context)},
            files={"audio": ("answer.wav", audio, "audio/wav")},
        ),
    )


def turn_context(session):
    return {
        "kind": "turn",
        "session_id": session["session_id"],
        "expected_revision": session["revision"],
    }


def confirmed(draft, request_id="transcription-confirm-001"):
    return {
        "request_id": request_id,
        "transcription_id": draft["transcription_id"],
        "confirmed_text": "I went hiking with friends last Saturday.",
    }


@pytest.mark.asyncio
async def test_preview_only_recognizes_and_confirmation_reuses_audio_once(
    application, audio_bytes
):
    async with browser(application.create()) as client:
        await client.post(f"{PREFIX}/bootstrap")
        session = await start(client)
        calls = (len(application.llm.calls), len(application.tts.calls))
        draft = await preview(client, turn_context(session), audio_bytes)
        assert len(application.asr.calls) == 1
        assert (len(application.llm.calls), len(application.tts.calls)) == calls
        assert (
            await client.get(f"{PREFIX}/sessions/{session['session_id']}")
        ).json() == session
        assert draft["machine_transcript"] == application.asr.text
        assert (await client.get(draft["audio"]["url"])).content == audio_bytes
        payload = {**confirmed(draft), "expected_revision": session["revision"]}
        result = await succeeded(
            client,
            await client.post(
                f"{PREFIX}/sessions/{session['session_id']}/turns", json=payload
            ),
        )
        user = next(item for item in result["turns"] if item["role"] == "user")
        assert user["text"] == payload["confirmed_text"]
        assert user["confirmed_text"] == payload["confirmed_text"]
        assert user["machine_transcript"] == application.asr.text
        assert user["audio"]["audio_asset_id"] == draft["audio"]["audio_asset_id"]
        assert (await client.get(user["audio"]["url"])).content == audio_bytes
        assert len(application.asr.calls) == 1
        replay = await succeeded(
            client,
            await client.post(
                f"{PREFIX}/sessions/{session['session_id']}/turns", json=payload
            ),
        )
        assert replay == result
        assert len(application.asr.calls) == 1
        payload.update(
            request_id="transcription-reuse-002", expected_revision=result["revision"]
        )
        assert (
            await client.post(
                f"{PREFIX}/sessions/{session['session_id']}/turns", json=payload
            )
        ).status_code == 409
        assert (
            await client.get(f"{PREFIX}/transcriptions/{draft['transcription_id']}")
        ).json()["status"] == "consumed"
        assert (
            await client.delete(f"{PREFIX}/transcriptions/{draft['transcription_id']}")
        ).status_code == 409
        generic_delete = await client.delete(
            f"{PREFIX}/records/{draft['transcription_id']}"
        )
        assert generic_delete.status_code == 403
        assert generic_delete.json()["detail"]["code"] == "business_route_required"
        assert (
            await succeeded(
                client,
                await client.post(
                    f"{PREFIX}/sessions/{session['session_id']}/turns",
                    json={**confirmed(draft), "expected_revision": session["revision"]},
                ),
            )
            == result
        )


@pytest.mark.asyncio
async def test_preview_request_id_recovers_without_asr_and_rejects_changed_payload(
    application, audio_bytes
):
    async with browser(application.create()) as client:
        await client.post(f"{PREFIX}/bootstrap")
        session = await start(client)
        first = await preview(client, turn_context(session), audio_bytes)
        assert await preview(client, turn_context(session), audio_bytes) == first
        assert len(application.asr.calls) == 1
        changed = {
            **turn_context(session),
            "expected_revision": session["revision"] + 1,
        }
        response = await client.post(
            f"{PREFIX}/transcriptions",
            data={
                "request_id": "transcription-preview-001",
                "context": json.dumps(changed),
            },
            files={"audio": ("answer.wav", audio_bytes, "audio/wav")},
        )
        assert response.status_code == 409
        assert len(application.asr.calls) == 1


@pytest.mark.asyncio
async def test_owner_stale_context_expiry_and_discard_are_checked(
    application, audio_bytes
):
    server = application.create()
    now = datetime(2026, 10, 26, 8, tzinfo=timezone.utc)
    server.app.state.learning_repository().clock = lambda: now.timestamp()
    server.app.state.learning_service().clock = lambda: now
    async with browser(server) as client:
        await client.post(f"{PREFIX}/bootstrap")
        session = await start(client)
        draft = await preview(client, turn_context(session), audio_bytes)
        async with browser(application.create()) as stranger:
            await stranger.post(f"{PREFIX}/bootstrap")
            assert (
                await stranger.get(
                    f"{PREFIX}/transcriptions/{draft['transcription_id']}"
                )
            ).status_code == 404
            assert (await stranger.get(draft["audio"]["url"])).status_code == 404
        session = await turn(
            client, session, "A separate text answer.", "advance-session-001"
        )
        payload = {**confirmed(draft), "expected_revision": session["revision"]}
        assert (
            await client.post(
                f"{PREFIX}/sessions/{session['session_id']}/turns", json=payload
            )
        ).status_code == 409
        assert len(application.asr.calls) == 1
        assert (
            await client.delete(f"{PREFIX}/transcriptions/{draft['transcription_id']}")
        ).status_code == 204
        assert (await client.get(draft["audio"]["url"])).status_code == 404
        fresh = await preview(
            client, turn_context(session), audio_bytes, "transcription-preview-002"
        )
        now += timedelta(hours=1, seconds=1)
        expired = await client.get(
            f"{PREFIX}/transcriptions/{fresh['transcription_id']}"
        )
        assert expired.json()["status"] == "expired"
        payload = {
            **confirmed(fresh, "expired-confirm-001"),
            "expected_revision": session["revision"],
        }
        assert (
            await client.post(
                f"{PREFIX}/sessions/{session['session_id']}/turns", json=payload
            )
        ).status_code == 410


@pytest.mark.asyncio
async def test_ready_draft_survives_restart_and_attempt_inherits_provenance(
    application, audio_bytes
):
    server = application.create()
    async with browser(server) as client:
        await client.post(f"{PREFIX}/bootstrap")
        token = client.cookies.get("chatterpal_learner")
        session = await completed(client)
        focus = session["feedback"]["focuses"][0]
        parent = session["attempts"][0]
        context = {
            "kind": "attempt",
            "session_id": session["session_id"],
            "source_revision": session["revision"],
            "source_turn_id": focus["source_turn_id"],
            "focus_id": focus["focus_id"],
            "parent_attempt_id": parent["attempt_id"],
            "mode": "free_response",
            "target_text": None,
        }
        draft = await preview(client, context, audio_bytes)
    async with browser(application.create(), token) as client:
        assert (
            await client.get(f"{PREFIX}/transcriptions/{draft['transcription_id']}")
        ).json()["status"] == "ready"
        result = await succeeded(
            client,
            await client.post(
                f"{PREFIX}/attempts",
                json={
                    **{key: value for key, value in context.items() if key != "kind"},
                    **confirmed(draft),
                },
            ),
        )
        assert result["transcript"] == result["confirmed_text"]
        assert result["machine_transcript"] == application.asr.text
        assert result["parent_attempt_id"] == parent["attempt_id"]
        assert result["audio"]["audio_asset_id"] == draft["audio"]["audio_asset_id"]
        assert len(application.asr.calls) == 1


@pytest.mark.asyncio
async def test_failed_coach_keeps_draft_for_explicit_retry_without_asr(
    application, audio_bytes
):
    async with browser(application.create()) as client:
        await client.post(f"{PREFIX}/bootstrap")
        session = await start(client)
        draft = await preview(client, turn_context(session), audio_bytes)
        original = application.llm.chat
        application.llm.chat = lambda messages: "invalid coach JSON"
        payload = {**confirmed(draft), "expected_revision": session["revision"]}
        failed = await operation(
            client,
            await client.post(
                f"{PREFIX}/sessions/{session['session_id']}/turns", json=payload
            ),
        )
        assert failed["status"] == "failed"
        assert (
            await client.get(f"{PREFIX}/transcriptions/{draft['transcription_id']}")
        ).json()["status"] == "ready"
        assert (
            await client.get(f"{PREFIX}/sessions/{session['session_id']}")
        ).json() == session
        application.llm.chat = original
        payload["request_id"] = "explicit-confirm-retry-002"
        await succeeded(
            client,
            await client.post(
                f"{PREFIX}/sessions/{session['session_id']}/turns", json=payload
            ),
        )
        assert len(application.asr.calls) == 1


@pytest.mark.asyncio
async def test_review_uses_recording_receipt_time_across_due(application, audio_bytes):
    server = application.create()
    now = datetime(2026, 10, 26, 8, tzinfo=timezone.utc)
    server.app.state.learning_repository().clock = lambda: now.timestamp()
    server.app.state.learning_service().clock = lambda: now
    server.app.state.learning_review_service().clock = lambda: now
    async with browser(server) as client:
        await client.post(f"{PREFIX}/bootstrap")
        session = await completed(client)
        focus = session["feedback"]["focuses"][0]
        item = await succeeded(
            client,
            await client.post(
                f"{PREFIX}/reviews",
                json={
                    "request_id": "save-preview-review-001",
                    "session_id": session["session_id"],
                    "source_revision": session["revision"],
                    "source_turn_id": focus["source_turn_id"],
                    "focus_id": focus["focus_id"],
                },
            ),
        )
        now += timedelta(days=1, seconds=-5)
        context = {
            "kind": "review",
            "review_item_id": item["review_item_id"],
            "expected_revision": item["revision"],
        }
        before = (await client.get(f"{PREFIX}/reviews/{item['review_item_id']}")).json()
        draft = await preview(client, context, audio_bytes)
        assert (
            await client.get(f"{PREFIX}/reviews/{item['review_item_id']}")
        ).json() == before
        now += timedelta(seconds=10)
        result = await succeeded(
            client,
            await client.post(
                f"{PREFIX}/reviews/{item['review_item_id']}/attempts",
                json={**confirmed(draft), "expected_revision": item["revision"]},
            ),
        )
        assert result["attempt"]["evidence_kind"] == "early_practice"
        assert not result["attempt"]["schedule_advanced"]
        assert result["attempt"]["machine_transcript"] == application.asr.text
        assert (
            result["attempt"]["audio"]["audio_asset_id"]
            == draft["audio"]["audio_asset_id"]
        )
        assert len(application.asr.calls) == 1


@pytest.mark.asyncio
async def test_discard_during_confirmation_blocks_late_publication(
    application, audio_bytes
):
    async with browser(application.create()) as client:
        await client.post(f"{PREFIX}/bootstrap")
        session = await start(client)
        draft = await preview(client, turn_context(session), audio_bytes)
        application.llm.block_coach = True
        payload = {**confirmed(draft), "expected_revision": session["revision"]}
        response = await client.post(
            f"{PREFIX}/sessions/{session['session_id']}/turns", json=payload
        )
        assert response.status_code == 202
        assert await asyncio.to_thread(application.llm.started.wait, 2)
        try:
            assert (
                await client.delete(
                    f"{PREFIX}/transcriptions/{draft['transcription_id']}"
                )
            ).status_code == 204
        finally:
            application.llm.release.set()
        await asyncio.sleep(0.03)
        assert (
            await client.get(f"{PREFIX}/sessions/{session['session_id']}")
        ).json() == session
        assert (
            await client.get(f"{PREFIX}/operations/{response.json()['operation_id']}")
        ).status_code == 404


@pytest.mark.asyncio
async def test_concurrent_confirmation_claims_only_once(application, audio_bytes):
    async with browser(application.create()) as client:
        await client.post(f"{PREFIX}/bootstrap")
        session = await start(client)
        draft = await preview(client, turn_context(session), audio_bytes)
        application.llm.block_coach = True
        path = f"{PREFIX}/sessions/{session['session_id']}/turns"
        payload = {**confirmed(draft), "expected_revision": session["revision"]}
        first = await client.post(path, json=payload)
        assert first.status_code == 202
        assert await asyncio.to_thread(application.llm.started.wait, 2)
        try:
            other = await client.post(
                path, json={**payload, "request_id": "concurrent-confirm-002"}
            )
            assert other.status_code == 409
        finally:
            application.llm.release.set()
        await succeeded(client, first)
        assert len(application.asr.calls) == 1
        assert len(application.llm.calls) == 1


@pytest.mark.asyncio
async def test_expiry_during_provider_rolls_back_audio_and_submission(
    application, audio_bytes
):
    server = application.create()
    now = datetime(2026, 10, 26, 8, tzinfo=timezone.utc)
    server.app.state.learning_repository().clock = lambda: now.timestamp()
    server.app.state.learning_service().clock = lambda: now
    async with browser(server) as client:
        await client.post(f"{PREFIX}/bootstrap")
        session = await start(client)
        draft = await preview(client, turn_context(session), audio_bytes)
        now += timedelta(minutes=59, seconds=59)
        application.llm.block_coach = True
        response = await client.post(
            f"{PREFIX}/sessions/{session['session_id']}/turns",
            json={**confirmed(draft), "expected_revision": session["revision"]},
        )
        assert await asyncio.to_thread(application.llm.started.wait, 2)
        now += timedelta(seconds=2)
        application.llm.release.set()
        failed = await operation(client, response)
        assert failed["status"] == "failed"
        assert failed["error"]["code"] == "transcription_expired"
        assert (
            await client.get(f"{PREFIX}/sessions/{session['session_id']}")
        ).json() == session
        assert (
            await client.get(f"{PREFIX}/transcriptions/{draft['transcription_id']}")
        ).json()["status"] == "expired"


@pytest.mark.asyncio
async def test_clearing_audio_during_asr_prevents_late_republication(
    application, audio_bytes
):
    from threading import Event

    entered, release = Event(), Event()
    server = application.create()
    async with browser(server) as client:
        identity = (await client.post(f"{PREFIX}/bootstrap")).json()
        session = await start(client)
        original = application.asr.recognize

        def recognize(content):
            entered.set()
            assert release.wait(3)
            return original(content)

        application.asr.recognize = recognize
        response = await client.post(
            f"{PREFIX}/transcriptions",
            data={
                "request_id": "blocked-asr-001",
                "context": json.dumps(turn_context(session)),
            },
            files={"audio": ("answer.wav", audio_bytes, "audio/wav")},
        )
        assert await asyncio.to_thread(entered.wait, 2)
        try:
            assert (
                await client.patch(f"{PREFIX}/profile", json={"retain_audio": False})
            ).status_code == 200
        finally:
            release.set()
        result = await operation(client, response)
        assert result["status"] == "interrupted"
        await asyncio.sleep(0.03)
        store = server.app.state.learning_repository()
        assert (
            store.list_records(identity["learner_id"], kind="transcription")["items"]
            == []
        )


@pytest.mark.asyncio
async def test_cancelled_queued_preview_never_calls_asr(application, audio_bytes):
    from threading import Event

    entered, release = Event(), Event()
    server = application.create(learning_max_workers=1)
    async with browser(server) as client:
        await client.post(f"{PREFIX}/bootstrap")
        first = await start(client, "start-first-preview-001")
        second = await start(client, "start-second-preview-001")
        original = application.asr.recognize

        def recognize(content):
            entered.set()
            assert release.wait(3)
            return original(content)

        application.asr.recognize = recognize
        active = await client.post(
            f"{PREFIX}/transcriptions",
            data={
                "request_id": "active-asr-preview-001",
                "context": json.dumps(turn_context(first)),
            },
            files={"audio": ("answer.wav", audio_bytes, "audio/wav")},
        )
        assert await asyncio.to_thread(entered.wait, 2)
        try:
            queued = await client.post(
                f"{PREFIX}/transcriptions",
                data={
                    "request_id": "cancel-queued-preview-001",
                    "context": json.dumps(turn_context(second)),
                },
                files={"audio": ("answer.wav", audio_bytes, "audio/wav")},
            )
            assert queued.status_code == 202
            assert (
                await client.delete(
                    f"{PREFIX}/transcriptions/requests/cancel-queued-preview-001"
                )
            ).status_code == 204
        finally:
            release.set()
        barrier = server.app.state.learning_service().runtime._executor.submit(
            lambda: None
        )
        await asyncio.to_thread(barrier.result, 3)
        await succeeded(client, active)
        assert len(application.asr.calls) == 1
        assert (
            await client.get(f"{PREFIX}/operations/{queued.json()['operation_id']}")
        ).status_code == 404
        assert (
            await client.get(f"{PREFIX}/sessions/{second['session_id']}")
        ).json() == second


@pytest.mark.parametrize(
    "entrypoint", ["create", "batch", "complete_record", "complete_batch"]
)
def test_generic_store_writes_cannot_create_transcriptions(tmp_path, entrypoint):
    from chatterpal.learning.storage import LearningStore, StoreError

    store = LearningStore(tmp_path / "protected-transcriptions.sqlite3")
    owner = store.bootstrap()[0]["learner_id"]
    specification = {"kind": "transcription", "data": {}}
    with pytest.raises(StoreError) as failure:
        if entrypoint == "create":
            store.create_record(owner, "generic-draft-create", **specification)
        elif entrypoint == "batch":
            store.commit_records(
                owner,
                "generic-draft-batch",
                "generic",
                {},
                creates=[{"key": "draft", **specification}],
            )
        else:
            claim = store.begin_operation(
                owner, "generic-draft-complete", "generic", {}
            )
            changes = (
                {"record": specification}
                if entrypoint == "complete_record"
                else {"creates": [{"key": "draft", **specification}]}
            )
            store.complete_operation(
                owner, claim.operation["operation_id"], claim.lease_token, **changes
            )
    assert failure.value.code == "business_route_required"
    assert store.list_records(owner, kind="transcription")["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields",
    [
        {"transcription_id": "draft-id"},
        {"confirmed_text": "Some text"},
        {
            "transcription_id": "draft-id",
            "confirmed_text": "Some text",
            "text": "Also text",
        },
    ],
)
async def test_confirmation_requires_a_complete_exclusive_pair(application, fields):
    async with browser(application.create()) as client:
        await client.post(f"{PREFIX}/bootstrap")
        session = await start(client)
        response = await client.post(
            f"{PREFIX}/sessions/{session['session_id']}/turns",
            json={
                "request_id": "invalid-confirm-pair-001",
                "expected_revision": session["revision"],
                **fields,
            },
        )
        assert response.status_code == 422
        assert application.asr.calls == []
