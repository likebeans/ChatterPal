"""INT2 vertical acceptance through APIServer, operation workers and real SQLite."""

import asyncio
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from chatterpal.config.settings import Settings
from chatterpal.web import api_server
from chatterpal.web.routes.learning_records import COOKIE_NAME
from tests.int2_support import FixtureASR, FixtureLLM, FixtureTTS

PREFIX = "/api/learning"


@pytest.fixture
def application(monkeypatch, tmp_path, audio_bytes):
    asr, llm, tts = FixtureASR(), FixtureLLM(), FixtureTTS(audio_bytes)
    monkeypatch.setattr(api_server, "create_asr", lambda settings: asr)
    monkeypatch.setattr(api_server, "create_llm", lambda settings: llm)
    monkeypatch.setattr(api_server, "create_tts", lambda settings: tts)

    def create(**overrides):
        settings = Settings(
            _env_file=None,
            **{
                "learning_db_path": str(tmp_path / "learning.sqlite3"),
                "learning_shutdown_timeout": 0,
                **overrides,
            },
        )
        server = api_server.APIServer(settings)
        server.temp_dir = tmp_path
        return server

    return SimpleNamespace(create=create, asr=asr, llm=llm, tts=tts)


@asynccontextmanager
async def browser(server, token=None):
    async with server.app.router.lifespan_context(server.app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://test"
        ) as client:
            if token:
                client.cookies.set(COOKIE_NAME, token, path=PREFIX)
            yield client


async def operation(client, response):
    assert response.status_code == 202, response.text
    result = response.json()
    deadline = time.monotonic() + 4
    while result["status"] == "pending" and time.monotonic() < deadline:
        await asyncio.sleep(0.005)
        response = await client.get(f"{PREFIX}/operations/{result['operation_id']}")
        assert response.status_code == 200, response.text
        result = response.json()
    assert result["status"] != "pending", result
    return result


async def succeeded(client, response):
    result = await operation(client, response)
    assert result["status"] == "succeeded", result
    value = result["result"]
    return value["data"] if result["result_type"] == "record" else value


async def start(client, request_id="start-weekend-001"):
    return await succeeded(
        client,
        await client.post(
            f"{PREFIX}/sessions",
            json={
                "request_id": request_id,
                "task_id": "weekend",
                "content_version": "1",
            },
        ),
    )


async def turn(client, session, text, request_id):
    return await succeeded(
        client,
        await client.post(
            f"{PREFIX}/sessions/{session['session_id']}/turns",
            json={
                "request_id": request_id,
                "expected_revision": session["revision"],
                "text": text,
            },
        ),
    )


async def completed(client):
    session = await start(client)
    for index, text in enumerate(
        (
            "I go hiking last Saturday.",
            "I was with my friends.",
            "It was relaxing because we had time to talk.",
        )
    ):
        session = await turn(client, session, text, f"weekend-turn-{index:03}")
    return await succeeded(
        client,
        await client.post(
            f"{PREFIX}/sessions/{session['session_id']}/finish",
            json={
                "request_id": "finish-weekend-001",
                "expected_revision": session["revision"],
            },
        ),
    )


@pytest.mark.asyncio
async def test_weekend_turns_feedback_two_retries_audio_and_restart(
    application, audio_bytes
):
    server = application.create()
    async with browser(server) as client:
        identity = (await client.post(f"{PREFIX}/bootstrap")).json()
        token = client.cookies.get(COOKIE_NAME)
        tasks = (await client.get(f"{PREFIX}/tasks")).json()
        assert (
            next(task for task in tasks if task["task_id"] == "weekend")[
                "content_version"
            ]
            == "1"
        )
        assert (await client.get(f"{PREFIX}/tasks/weekend")).json()[
            "title"
        ] == "描述周末"
        assert (await client.get(f"{PREFIX}/tasks/missing")).status_code == 404
        session = await start(client)
        start_again = await start(client)
        assert start_again["session_id"] == session["session_id"]
        session = await turn(
            client, session, "I go hiking last Saturday.", "weekend-turn-text"
        )
        turn_payload = {
            "request_id": "weekend-turn-text",
            "expected_revision": 1,
            "text": "I go hiking last Saturday.",
        }
        replay = await succeeded(
            client,
            await client.post(
                f"{PREFIX}/sessions/{session['session_id']}/turns", json=turn_payload
            ),
        )
        assert replay == session
        conflict = await client.post(
            f"{PREFIX}/sessions/{session['session_id']}/turns",
            json={**turn_payload, "text": "Changed"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "request_conflict"
        application.tts.fail = True
        session = await succeeded(
            client,
            await client.post(
                f"{PREFIX}/sessions/{session['session_id']}/turns",
                data={
                    "request_id": "weekend-turn-audio",
                    "expected_revision": str(session["revision"]),
                },
                files={"audio": ("answer.wav", audio_bytes, "audio/wav")},
            ),
        )
        assert application.asr.calls == [audio_bytes]
        assert session["turns"][-2]["text"] == application.asr.text
        assert session["turns"][-1]["audio"]["state"] == "unavailable"
        audio = session["turns"][-2]["audio"]
        assert audio["state"] == "available"
        playback = await client.get(audio["url"])
        assert playback.content == audio_bytes
        assert playback.headers["content-type"] == "audio/wav"
        application.tts.fail = False
        session = await turn(
            client,
            session,
            "It was relaxing because we had time to talk.",
            "weekend-turn-last",
        )
        before_finish = session["revision"]
        session = await succeeded(
            client,
            await client.post(
                f"{PREFIX}/sessions/{session['session_id']}/finish",
                json={
                    "request_id": "finish-weekend-001",
                    "expected_revision": before_finish,
                },
            ),
        )
        assert session["status"] == "completed"
        assert session["revision"] == before_finish + 1
        feedback = session["feedback"]
        assert feedback["status"] == "available"
        assert feedback["source_revision"] == before_finish
        assert "relaxing" in feedback["strength"]
        assert all(item["result"] == "met" for item in feedback["criteria"])
        focus = feedback["focuses"][0]
        original = session["attempts"][0]
        assert original["parent_attempt_id"] is None
        assert original["transcript"] == "I go hiking last Saturday."
        parent = original["attempt_id"]
        attempts = []
        for index, text in enumerate(
            (
                "On Saturday I hiked with friends and enjoyed the quiet trail.",
                "I spent Saturday hiking with my friends. I felt relaxed outdoors.",
            )
        ):
            payload = {
                "request_id": f"weekend-retry-{index:03}",
                "session_id": session["session_id"],
                "source_revision": session["revision"],
                "source_turn_id": focus["source_turn_id"],
                "focus_id": focus["focus_id"],
                "parent_attempt_id": parent,
                "mode": "free_response",
                "text": text,
            }
            attempt = await succeeded(
                client, await client.post(f"{PREFIX}/attempts", json=payload)
            )
            assert attempt["parent_attempt_id"] == parent
            assert attempt["transcript"] == text
            assert attempt["evaluation"]["result"] == "met"
            assert attempt["evaluation"]["source"] == "model"
            attempts.append(attempt)
            parent = attempt["attempt_id"]
            session = (
                await client.get(f"{PREFIX}/sessions/{session['session_id']}")
            ).json()
        assert len({item["attempt_id"] for item in session["attempts"]}) == 3
        example_url = f"{PREFIX}/attempts/{parent}/example-audio"
        example = await succeeded(
            client,
            await client.post(example_url, json={"request_id": "example-weekend-001"}),
        )
        assert example["state"] == "available"
        example_again = await succeeded(
            client,
            await client.post(example_url, json={"request_id": "example-weekend-001"}),
        )
        assert example_again["audio_asset_id"] == example["audio_asset_id"]
        assert (await client.get(f"{PREFIX}/sessions/{session['session_id']}")).json()[
            "revision"
        ] == session["revision"]
        assert (await client.get(f"{PREFIX}/attempts/{parent}")).json()[
            "transcript"
        ] == attempts[-1]["transcript"]
        progress = (await client.get(f"{PREFIX}/progress")).json()
        assert progress["sessions_completed"] == 1 and progress["attempts_count"] == 3
        assert progress["retries_count"] == 2 and progress["reviews_completed"] == 0
        due = await client.get(f"{PREFIX}/reviews/due")
        assert due.status_code == 200
        assert due.json()["items"] == []
        archived = (await client.get(f"{PREFIX}/export")).json()
        assert len(archived["records"]) == 4
        assert "content" not in archived["records"][0]["data"].get("audio", {})
    async with browser(application.create(), token) as reopened:
        restored_identity = (await reopened.post(f"{PREFIX}/bootstrap")).json()
        assert restored_identity["learner_id"] == identity["learner_id"]
        restored = (
            await reopened.get(f"{PREFIX}/sessions/{session['session_id']}")
        ).json()
        assert restored == session
        assert (await reopened.get(f"{PREFIX}/attempts/{parent}")).json() == attempts[
            -1
        ]
        assert (await reopened.get(audio["url"])).content == audio_bytes


@pytest.mark.asyncio
async def test_pending_turn_blocks_finish_and_health_stays_responsive(application):
    server = application.create()
    async with browser(server) as client:
        await client.post(f"{PREFIX}/bootstrap")
        session = await start(client)
        application.llm.block_coach = True
        response = await client.post(
            f"{PREFIX}/sessions/{session['session_id']}/turns",
            json={
                "request_id": "slow-weekend-turn",
                "expected_revision": session["revision"],
                "text": "I went hiking.",
            },
        )
        assert await asyncio.to_thread(application.llm.started.wait, 2)
        try:
            health = await asyncio.wait_for(client.get("/health"), timeout=0.5)
            assert health.status_code == 200
            finish = await client.post(
                f"{PREFIX}/sessions/{session['session_id']}/finish",
                json={
                    "request_id": "finish-while-pending",
                    "expected_revision": session["revision"],
                },
            )
            assert finish.status_code == 409
            assert finish.json()["detail"]["code"] == "session_busy"
            repeated = await client.post(
                f"{PREFIX}/sessions/{session['session_id']}/turns",
                json={
                    "request_id": "slow-weekend-turn",
                    "expected_revision": session["revision"],
                    "text": "I went hiking.",
                },
            )
            assert repeated.json()["operation_id"] == response.json()["operation_id"]
        finally:
            application.llm.release.set()
        assert (await operation(client, response))["status"] == "succeeded"
        stale = await client.post(
            f"{PREFIX}/sessions/{session['session_id']}/hints",
            json={
                "request_id": "stale-weekend-hint",
                "expected_revision": session["revision"],
            },
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "revision_conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["fail_feedback", "invalid_feedback"])
async def test_feedback_outage_keeps_completed_session_and_can_regenerate(
    application, failure
):
    setattr(application.llm, failure, True)
    async with browser(application.create()) as client:
        await client.post(f"{PREFIX}/bootstrap")
        session = await completed(client)
        assert session["status"] == "completed"
        assert session["feedback"]["status"] == "unavailable"
        assert len(session["turns"]) == 7
        setattr(application.llm, failure, False)
        session = await succeeded(
            client,
            await client.post(
                f"{PREFIX}/sessions/{session['session_id']}/feedback",
                json={
                    "request_id": "regenerate-feedback",
                    "expected_revision": session["revision"],
                },
            ),
        )
        assert session["feedback"]["status"] == "available"
        assert len(session["attempts"]) == 1


@pytest.mark.asyncio
async def test_read_aloud_requires_owned_target_and_mode_parent(application):
    async with browser(application.create()) as client:
        await client.post(f"{PREFIX}/bootstrap")
        session = await completed(client)
        focus, original = session["feedback"]["focuses"][0], session["attempts"][0]
        payload = {
            "request_id": "read-aloud-invalid",
            "session_id": session["session_id"],
            "source_revision": session["revision"],
            "source_turn_id": focus["source_turn_id"],
            "focus_id": focus["focus_id"],
            "parent_attempt_id": None,
            "mode": "read_aloud",
            "target_text": "An unrelated target.",
            "text": "An unrelated target.",
        }
        rejected = await operation(
            client, await client.post(f"{PREFIX}/attempts", json=payload)
        )
        assert rejected["error"]["code"] == "target_required"
        wrong_parent = await operation(
            client,
            await client.post(
                f"{PREFIX}/attempts",
                json={
                    **payload,
                    "request_id": "read-aloud-parent",
                    "parent_attempt_id": original["attempt_id"],
                    "target_text": focus["suggested"],
                    "text": focus["suggested"],
                },
            ),
        )
        assert wrong_parent["error"]["code"] == "attempt_not_found"
        calls = len(application.llm.calls)
        result = await succeeded(
            client,
            await client.post(
                f"{PREFIX}/attempts",
                json={
                    **payload,
                    "request_id": "read-aloud-owned",
                    "target_text": focus["suggested"],
                    "text": focus["suggested"],
                },
            ),
        )
        assert len(application.llm.calls) == calls
        assert result["evaluation"]["source"] == "text"
        assert result["evaluation"]["result"] == "met"
        assert "未评估声学发音" in result["evaluation"]["reason"]


@pytest.mark.asyncio
async def test_two_owners_and_delete_during_provider_call_cannot_revive_data(
    application,
):
    server = application.create()
    async with browser(server) as first:
        await first.post(f"{PREFIX}/bootstrap")
        session = await start(first)
        application.llm.block_coach = True
        pending = await first.post(
            f"{PREFIX}/sessions/{session['session_id']}/turns",
            json={
                "request_id": "deleted-owner-turn",
                "expected_revision": session["revision"],
                "text": "I went hiking.",
            },
        )
        assert await asyncio.to_thread(application.llm.started.wait, 2)
        old_token = first.cookies.get(COOKIE_NAME)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.app), base_url="http://test"
            ) as second:
                await second.post(f"{PREFIX}/bootstrap")
                assert (
                    await second.get(f"{PREFIX}/sessions/{session['session_id']}")
                ).status_code == 404
                assert (
                    await second.get(
                        f"{PREFIX}/operations/{pending.json()['operation_id']}"
                    )
                ).status_code == 404
            assert (await first.delete(f"{PREFIX}/data")).status_code == 200
        finally:
            application.llm.release.set()
        runtime = server.app.state.learning_service().runtime
        await asyncio.to_thread(runtime.shutdown)
        first.cookies.clear()
        first.cookies.set(COOKIE_NAME, old_token, domain="test.local", path=PREFIX)
        assert (await first.get(f"{PREFIX}/profile")).status_code == 401
        first.cookies.clear()
        await first.post(f"{PREFIX}/bootstrap")
        assert (await first.get(f"{PREFIX}/records")).json()["items"] == []
        assert (
            await first.get(f"{PREFIX}/operations/{pending.json()['operation_id']}")
        ).status_code == 404


@pytest.mark.asyncio
async def test_strict_inputs_hints_and_form_limit(application, audio_bytes):
    async with browser(application.create(learning_max_audio_bytes=1024)) as client:
        await client.post(f"{PREFIX}/bootstrap")
        assert (
            await client.post(
                f"{PREFIX}/sessions",
                json={
                    "request_id": "bad-version-start",
                    "task_id": "weekend",
                    "content_version": "missing",
                },
            )
        ).status_code == 404
        session = await start(client)
        hint = await succeeded(
            client,
            await client.post(
                f"{PREFIX}/sessions/{session['session_id']}/hints",
                json={
                    "request_id": "weekend-sentence-hint",
                    "expected_revision": session["revision"],
                },
            ),
        )
        assert hint["hint"] == "Last weekend, I ..."
        session = (
            await client.get(f"{PREFIX}/sessions/{session['session_id']}")
        ).json()
        assert session["revision"] == 2 and len(session["domain"]["hints"]) == 1
        endpoint = f"{PREFIX}/sessions/{session['session_id']}/turns"
        base = {
            "request_id": "weekend-input-001",
            "expected_revision": session["revision"],
        }
        for extra in (
            {},
            {"text": ""},
            {"text": "Hello", "domain": {}},
            {"text": "Hello", "expected_revision": True},
        ):
            assert (
                await client.post(endpoint, json={**base, **extra})
            ).status_code == 422
        assert (
            await client.post(
                endpoint, data=base, files={"audio": ("large.wav", audio_bytes)}
            )
        ).status_code == 413
        assert (
            await client.post(
                endpoint,
                data={**base, "text": "Hello"},
                files={"audio": ("small.wav", b"audio")},
            )
        ).status_code == 422
        assert (
            await client.post(
                f"{PREFIX}/records",
                json={
                    "request_id": "generic-create-001",
                    "kind": "session",
                    "data": {},
                },
            )
        ).status_code == 403
