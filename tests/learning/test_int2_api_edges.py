"""Business API edge cases with real operation workers and isolated SQLite."""

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from chatterpal.config.settings import Settings
from chatterpal.learning.presentation import public_learning_result
from chatterpal.web import api_server
from tests.int2_support import FixtureASR, FixtureLLM, FixtureTTS

PREFIX = "/api/learning"


class ControlledLLM(FixtureLLM):
    def __init__(self):
        super().__init__()
        self.block_evaluation = False
        self.evaluation_started = threading.Event()
        self.evaluation_release = threading.Event()

    def chat(self, messages):
        if self.block_evaluation and messages[0]["content"].startswith(
            "INT2_EVALUATE_FREE_RESPONSE"
        ):
            self.evaluation_started.set()
            if not self.evaluation_release.wait(5):
                raise TimeoutError("Controlled evaluation was not released")
        return super().chat(messages)


class ControlledTTS(FixtureTTS):
    def __init__(self, content):
        super().__init__(content)
        self.block = False
        self.started = threading.Event()
        self.release = threading.Event()

    def synthesize(self, text):
        if self.block:
            self.started.set()
            if not self.release.wait(5):
                raise TimeoutError("Controlled example speech was not released")
        return super().synthesize(text)


@pytest.fixture
def edge_application(monkeypatch, tmp_path, audio_bytes):
    asr, llm, tts = FixtureASR(), ControlledLLM(), ControlledTTS(audio_bytes)
    monkeypatch.setattr(api_server, "create_asr", lambda settings: asr)
    monkeypatch.setattr(api_server, "create_llm", lambda settings: llm)
    monkeypatch.setattr(api_server, "create_tts", lambda settings: tts)

    def create(**overrides):
        settings = Settings(
            _env_file=None,
            **{
                "learning_enabled": True,
                "learning_db_path": str(tmp_path / "api-edges.sqlite3"),
                "learning_shutdown_timeout": 2,
                **overrides,
            },
        )
        server = api_server.APIServer(settings)
        server.temp_dir = tmp_path
        return server

    return SimpleNamespace(create=create, asr=asr, llm=llm, tts=tts)


@asynccontextmanager
async def browser(server):
    async with server.app.router.lifespan_context(server.app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://test"
        ) as client:
            response = await client.post(f"{PREFIX}/bootstrap")
            assert response.status_code == 200, response.text
            yield client


async def terminal(client, response):
    assert response.status_code == 202, response.text
    operation = response.json()
    deadline = time.monotonic() + 4
    while operation["status"] == "pending" and time.monotonic() < deadline:
        await asyncio.sleep(0.005)
        response = await client.get(f"{PREFIX}/operations/{operation['operation_id']}")
        assert response.status_code == 200, response.text
        operation = response.json()
    assert operation["status"] != "pending", operation
    return operation


async def succeeded(client, response):
    operation = await terminal(client, response)
    assert operation["status"] == "succeeded", operation
    result = operation["result"]
    return result["data"] if operation["result_type"] == "record" else result


async def rejected(client, response, code, status):
    if response.status_code == 202:
        operation = await terminal(client, response)
        assert operation["status"] == "failed", operation
        assert operation["error"]["code"] == code, operation
    else:
        assert response.status_code == status, response.text
        assert response.json()["detail"]["code"] == code, response.text


async def start(client, key="edge-session"):
    return await succeeded(
        client,
        await client.post(
            f"{PREFIX}/sessions",
            json={
                "request_id": f"{key}-start",
                "task_id": "weekend",
                "content_version": "1",
            },
        ),
    )


async def add_turn(client, session, text, key):
    return await succeeded(
        client,
        await client.post(
            f"{PREFIX}/sessions/{session['session_id']}/turns",
            json={
                "request_id": key,
                "expected_revision": session["revision"],
                "text": text,
            },
        ),
    )


async def complete(client, key="edge-session"):
    session = await start(client, key)
    for index, text in enumerate(
        ("I go hiking last Saturday.", "I was with my friends and felt relaxed.")
    ):
        session = await add_turn(client, session, text, f"{key}-turn-{index}")
    return await succeeded(
        client,
        await client.post(
            f"{PREFIX}/sessions/{session['session_id']}/finish",
            json={
                "request_id": f"{key}-finish",
                "expected_revision": session["revision"],
            },
        ),
    )


def attempt_payload(session, key="edge-attempt"):
    focus = session["feedback"]["focuses"][0]
    return {
        "request_id": key,
        "session_id": session["session_id"],
        "source_revision": session["revision"],
        "source_turn_id": focus["source_turn_id"],
        "focus_id": focus["focus_id"],
        "mode": "free_response",
    }


def provider_calls(application):
    return {
        "asr": len(application.asr.calls),
        "llm": len(application.llm.calls),
        "tts": len(application.tts.calls),
    }


async def get_session(client, session):
    response = await client.get(f"{PREFIX}/sessions/{session['session_id']}")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["turn", "attempt"])
async def test_invalid_audio_bytes_are_rejected_before_asr_or_llm(
    edge_application, endpoint
):
    async with browser(edge_application.create()) as client:
        if endpoint == "turn":
            session = await start(client)
            path = f"{PREFIX}/sessions/{session['session_id']}/turns"
            fields = {
                "request_id": "invalid-bytes-turn",
                "expected_revision": session["revision"],
            }
        else:
            session = await complete(client)
            path = f"{PREFIX}/attempts"
            fields = attempt_payload(session, "invalid-bytes-attempt")
        before = provider_calls(edge_application)
        response = await client.post(
            path,
            data=fields,
            files={"audio": ("answer.wav", b"not an audio container", "audio/wav")},
        )
        await rejected(client, response, "unsupported_audio", 415)
        assert await get_session(client, session) == session
        assert provider_calls(edge_application) == before, {
            "before": before,
            "after": provider_calls(edge_application),
        }


@pytest.mark.asyncio
async def test_opening_alone_cannot_finish_or_generate_feedback(edge_application):
    async with browser(edge_application.create()) as client:
        session = await start(client)
        before = provider_calls(edge_application)
        for endpoint, code in (
            ("finish", "session_conflict"),
            ("feedback", "session_active"),
        ):
            await rejected(
                client,
                await client.post(
                    f"{PREFIX}/sessions/{session['session_id']}/{endpoint}",
                    json={
                        "request_id": f"opening-only-{endpoint}",
                        "expected_revision": session["revision"],
                    },
                ),
                code,
                409,
            )
            assert await get_session(client, session) == session
        assert provider_calls(edge_application) == before


@pytest.mark.asyncio
async def test_completed_session_rejects_turn_and_preserves_valid_feedback(
    edge_application, audio_bytes
):
    async with browser(edge_application.create()) as client:
        session = await complete(client)
        before = provider_calls(edge_application)
        await rejected(
            client,
            await client.post(
                f"{PREFIX}/sessions/{session['session_id']}/turns",
                data={
                    "request_id": "completed-session-turn",
                    "expected_revision": session["revision"],
                },
                files={"audio": ("late.wav", audio_bytes, "audio/wav")},
            ),
            "session_completed",
            409,
        )
        feedback = await succeeded(
            client,
            await client.post(
                f"{PREFIX}/sessions/{session['session_id']}/feedback",
                json={
                    "request_id": "completed-feedback-again",
                    "expected_revision": session["revision"],
                },
            ),
        )
        assert feedback == session
        assert await get_session(client, session) == session
        assert provider_calls(edge_application) == before


@pytest.mark.asyncio
async def test_attempt_rejects_foreign_sources_focus_parent_and_invalid_mode_targets(
    edge_application, audio_bytes
):
    async with browser(edge_application.create()) as client:
        session = await complete(client)
        other = await complete(client, "other-edge-session")
        before = provider_calls(edge_application)
        focus = session["feedback"]["focuses"][0]
        other_focus = other["feedback"]["focuses"][0]
        second_user = [turn for turn in session["turns"] if turn["role"] == "user"][1]
        invalid = (
            (
                {"source_revision": session["revision"] - 1},
                "revision_conflict",
                409,
            ),
            ({"source_turn_id": session["turns"][0]["turn_id"]}, "turn_not_found", 404),
            ({"source_turn_id": other_focus["source_turn_id"]}, "turn_not_found", 404),
            ({"focus_id": other_focus["focus_id"]}, "focus_not_found", 404),
            ({"source_turn_id": second_user["turn_id"]}, "focus_not_found", 404),
            (
                {"parent_attempt_id": other["attempts"][0]["attempt_id"]},
                "attempt_not_found",
                404,
            ),
            (
                {
                    "focus_id": None,
                    "parent_attempt_id": session["attempts"][0]["attempt_id"],
                },
                "attempt_not_found",
                404,
            ),
            ({"mode": "read_aloud"}, "target_required", 422),
            (
                {"mode": "read_aloud", "target_text": "An unrelated sentence."},
                "target_required",
                422,
            ),
            ({"target_text": focus["suggested"]}, "unexpected_target", 422),
        )
        for index, (changes, code, status) in enumerate(invalid):
            fields = {
                **attempt_payload(session, f"invalid-attempt-link-{index}"),
                **changes,
            }
            # Omitted nullable fields are the multipart representation of None.
            fields = {key: value for key, value in fields.items() if value is not None}
            await rejected(
                client,
                await client.post(
                    f"{PREFIX}/attempts",
                    data=fields,
                    files={"audio": ("answer.wav", audio_bytes, "audio/wav")},
                ),
                code,
                status,
            )
        assert await get_session(client, session) == session
        assert await get_session(client, other) == other
        assert provider_calls(edge_application) == before


@pytest.mark.asyncio
async def test_same_turn_request_cannot_be_replayed_into_another_session(
    edge_application,
):
    async with browser(edge_application.create()) as client:
        first = await start(client, "first-edge-session")
        second = await start(client, "second-edge-session")
        payload = {
            "request_id": "shared-edge-turn-request",
            "expected_revision": 1,
            "text": "I went hiking with my friends.",
        }
        response = await client.post(
            f"{PREFIX}/sessions/{first['session_id']}/turns", json=payload
        )
        original_operation = response.json()["operation_id"]
        saved = await succeeded(client, response)
        before = provider_calls(edge_application)
        conflict = await client.post(
            f"{PREFIX}/sessions/{second['session_id']}/turns", json=payload
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["detail"]["code"] == "request_conflict"
        replay = await client.post(
            f"{PREFIX}/sessions/{first['session_id']}/turns", json=payload
        )
        assert replay.json()["operation_id"] == original_operation
        assert await succeeded(client, replay) == saved
        recovered = await client.get(f"{PREFIX}/requests/{payload['request_id']}")
        assert recovered.json()["operation_id"] == original_operation
        assert await get_session(client, second) == second
        assert provider_calls(edge_application) == before


@pytest.mark.asyncio
async def test_example_audio_replays_after_revision_change_and_conflicts_for_other_attempt(
    edge_application,
):
    async with browser(edge_application.create()) as client:
        session = await complete(client)
        original = session["attempts"][0]
        path = f"{PREFIX}/attempts/{original['attempt_id']}/example-audio"
        body = {"request_id": "stable-edge-example"}
        first_response = await client.post(path, json=body)
        first_operation = first_response.json()["operation_id"]
        first_audio = await succeeded(client, first_response)
        assert (
            edge_application.tts.calls[-1]
            == session["feedback"]["focuses"][0]["suggested"]
        )
        assert await get_session(client, session) == session
        retry = await succeeded(
            client,
            await client.post(
                f"{PREFIX}/attempts",
                json={
                    **attempt_payload(session, "retry-before-example-replay"),
                    "parent_attempt_id": original["attempt_id"],
                    "text": "I spent Saturday hiking with friends.",
                },
            ),
        )
        current = await get_session(client, session)
        before = provider_calls(edge_application)
        replay = await client.post(path, json=body)
        assert replay.json()["operation_id"] == first_operation
        assert await succeeded(client, replay) == first_audio
        conflict = await client.post(
            f"{PREFIX}/attempts/{retry['attempt_id']}/example-audio", json=body
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["detail"]["code"] == "request_conflict"
        assert await get_session(client, session) == current
        assert provider_calls(edge_application) == before


@pytest.mark.asyncio
async def test_free_response_without_target_records_only_the_new_attempt(
    edge_application, audio_bytes
):
    async with browser(edge_application.create()) as client:
        session = await complete(client)
        before = provider_calls(edge_application)
        edge_application.asr.text = "I spent Sunday outside with my family."
        response = await client.post(
            f"{PREFIX}/attempts",
            data={
                **attempt_payload(session, "valid-free-response-audio"),
                "parent_attempt_id": session["attempts"][0]["attempt_id"],
            },
            files={"audio": ("retry.wav", audio_bytes, "audio/wav")},
        )
        operation_id = response.json()["operation_id"]
        attempt = await succeeded(client, response)
        assert attempt["transcript"] == edge_application.asr.text
        assert attempt["target_text"] is None
        assert attempt["evaluation"]["source"] == "model"
        assert attempt["evaluation"]["result"] == "met"
        assert provider_calls(edge_application) == {
            "asr": before["asr"] + 1,
            "llm": before["llm"] + 1,
            "tts": before["tts"],
        }
        restored = await client.get(f"{PREFIX}/attempts/{attempt['attempt_id']}")
        assert restored.json() == attempt
        current = await get_session(client, session)
        assert current["revision"] == session["revision"] + 1
        assert current["turns"] == session["turns"]
        assert current["feedback"] == session["feedback"]
        assert current["attempts"] == [*session["attempts"], attempt]
        before_replay = provider_calls(edge_application)
        replay = await client.post(
            f"{PREFIX}/attempts",
            data={
                **attempt_payload(session, "valid-free-response-audio"),
                "parent_attempt_id": session["attempts"][0]["attempt_id"],
            },
            files={"audio": ("retry.wav", audio_bytes, "audio/wav")},
        )
        assert replay.json()["operation_id"] == operation_id
        assert await succeeded(client, replay) == attempt
        assert provider_calls(edge_application) == before_replay


@pytest.mark.asyncio
async def test_json_fields_are_strict_before_reserving_an_operation(edge_application):
    async with browser(edge_application.create()) as client:
        session = await complete(client)
        active = await start(client, "json-input-session")
        cases = (
            ("turn", active, f"{PREFIX}/sessions/{active['session_id']}/turns"),
            ("attempt", session, f"{PREFIX}/attempts"),
        )
        before = provider_calls(edge_application)
        for kind, source, path in cases:
            base = (
                {"expected_revision": source["revision"]}
                if kind == "turn"
                else attempt_payload(source)
            )
            revision = "expected_revision" if kind == "turn" else "source_revision"
            invalid = (
                {"text": None},
                {"text": " \t "},
                {"text": 123},
                {"text": "a" * 12001},
                {revision: True},
                {revision: str(source["revision"])},
                {revision: 0},
                {"owner": "client-supplied-owner"},
                {"audio": "client-supplied-bytes"},
                {"request_id": "bad/id!!"},
            )
            for index, changes in enumerate(invalid):
                request_id = f"strict-json-{kind}-{index}"
                payload = {
                    **base,
                    "text": "I went hiking on Saturday.",
                    "request_id": request_id,
                    **changes,
                }
                response = await client.post(path, json=payload)
                assert response.status_code == 422, (kind, changes, response.text)
                assert (
                    await client.get(f"{PREFIX}/requests/{request_id}")
                ).status_code == 404
            malformed = await client.post(
                path,
                content=b'{"request_id":',
                headers={"Content-Type": "application/json"},
            )
            assert malformed.status_code == 422, malformed.text
        assert await get_session(client, session) == session
        assert await get_session(client, active) == active
        assert provider_calls(edge_application) == before


@pytest.mark.asyncio
async def test_multipart_rejects_duplicate_fields_mixed_inputs_and_invalid_revisions(
    edge_application, audio_bytes
):
    async with browser(
        edge_application.create(learning_max_audio_bytes=len(audio_bytes))
    ) as client:
        session = await complete(client)
        active = await start(client, "form-input-session")
        before = provider_calls(edge_application)
        for kind, source, path in (
            ("turn", active, f"{PREFIX}/sessions/{active['session_id']}/turns"),
            ("attempt", session, f"{PREFIX}/attempts"),
        ):
            revision = "expected_revision" if kind == "turn" else "source_revision"
            base = (
                {revision: source["revision"]}
                if kind == "turn"
                else attempt_payload(source)
            )
            valid_audio = ("audio", ("answer.wav", audio_bytes, "audio/wav"))
            cases = (
                (
                    "duplicate",
                    {},
                    [("request_id", (None, "other-request")), valid_audio],
                    422,
                ),
                ("revision", {revision: "1.0"}, [valid_audio], 422),
                ("negative", {revision: "-1"}, [valid_audio], 422),
                ("mixed", {"text": "I went hiking."}, [valid_audio], 422),
                ("missing", {}, [], 422),
                ("plain-audio", {"audio": "not a file"}, [], 422),
                ("extra", {"domain": "client state"}, [valid_audio], 422),
                ("empty", {}, [("audio", ("answer.wav", b"", "audio/wav"))], 422),
                (
                    "large",
                    {},
                    [
                        (
                            "audio",
                            ("answer.wav", b"x" * (len(audio_bytes) + 1), "audio/wav"),
                        )
                    ],
                    413,
                ),
            )
            for label, changes, uploads, status in cases:
                request_id = f"strict-form-{kind}-{label}"
                fields = {**base, "request_id": request_id, **changes}
                multipart = [(key, (None, str(value))) for key, value in fields.items()]
                response = await client.post(path, files=[*multipart, *uploads])
                assert response.status_code == status, (kind, label, response.text)
                assert (
                    await client.get(f"{PREFIX}/requests/{request_id}")
                ).status_code == 404
        assert await get_session(client, session) == session
        assert await get_session(client, active) == active
        assert provider_calls(edge_application) == before


@pytest.mark.asyncio
async def test_operation_recovery_and_example_audio_remain_private_to_the_owner(
    edge_application, audio_bytes
):
    server = edge_application.create()
    async with browser(server) as first:
        session = await complete(first)
        original = session["attempts"][0]
        edge_application.tts.block = True
        pending = await first.post(
            f"{PREFIX}/attempts/{original['attempt_id']}/example-audio",
            json={"request_id": "private-example-request"},
        )
        assert pending.status_code == 202, pending.text
        operation_id = pending.json()["operation_id"]
        try:
            assert await asyncio.to_thread(edge_application.tts.started.wait, 2)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.app), base_url="http://test"
            ) as second:
                assert (await second.post(f"{PREFIX}/bootstrap")).status_code == 200
                for path in (
                    f"{PREFIX}/operations/{operation_id}",
                    f"{PREFIX}/requests/private-example-request",
                    f"{PREFIX}/attempts/{original['attempt_id']}",
                    f"{PREFIX}/sessions/{session['session_id']}",
                ):
                    response = await second.get(path)
                    assert response.status_code == 404, (path, response.text)
                    assert response.headers["Cache-Control"] == "no-store"
                foreign_write = await second.post(
                    f"{PREFIX}/attempts/{original['attempt_id']}/example-audio",
                    json={"request_id": "foreign-example-request"},
                )
                assert foreign_write.status_code == 404, foreign_write.text
                edge_application.tts.release.set()
                audio = await succeeded(first, pending)
                assert (await first.get(audio["url"])).content == audio_bytes
                foreign_audio = await second.get(audio["url"])
                assert foreign_audio.status_code == 404
                assert foreign_audio.headers["Cache-Control"] == "no-store"
                assert (
                    await second.get(f"{PREFIX}/operations/{operation_id}")
                ).status_code == 404
                # The same request key belongs to a separate owner namespace.
                own = await start(second)
                assert own["session_id"] != session["session_id"]
                assert (await second.get(f"{PREFIX}/export")).json()["records"][0][
                    "data"
                ]["session_id"] == own["session_id"]
        finally:
            edge_application.tts.release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["attempt", "example"])
async def test_deleting_source_during_provider_work_prevents_late_children_and_audio(
    edge_application, audio_bytes, action
):
    async with browser(edge_application.create(learning_max_workers=1)) as client:
        session = await complete(client)
        original = session["attempts"][0]
        records = (
            await client.get(
                f"{PREFIX}/records",
                params={"kind": "session", "session_id": session["session_id"]},
            )
        ).json()["items"]
        record_id = records[0]["record_id"]
        existing_audio = [
            turn["audio"]["url"]
            for turn in session["turns"]
            if turn.get("audio", {}).get("state") == "available"
        ]
        if action == "attempt":
            edge_application.llm.block_evaluation = True
            started = edge_application.llm.evaluation_started
            release = edge_application.llm.evaluation_release
            pending = await client.post(
                f"{PREFIX}/attempts",
                data={
                    **attempt_payload(session, "delete-source-attempt"),
                    "parent_attempt_id": original["attempt_id"],
                },
                files={"audio": ("answer.wav", audio_bytes, "audio/wav")},
            )
        else:
            edge_application.tts.block = True
            started, release = (
                edge_application.tts.started,
                edge_application.tts.release,
            )
            pending = await client.post(
                f"{PREFIX}/attempts/{original['attempt_id']}/example-audio",
                json={"request_id": "delete-source-example"},
            )
        assert pending.status_code == 202, pending.text
        operation_id = pending.json()["operation_id"]
        try:
            assert await asyncio.to_thread(started.wait, 2)
            deleted = await client.delete(f"{PREFIX}/records/{record_id}")
            assert deleted.status_code == 200, deleted.text
        finally:
            release.set()
        # One worker means this successful operation runs after the old worker exits.
        survivor = await start(client, "after-source-deletion")
        for path in (
            f"{PREFIX}/sessions/{session['session_id']}",
            f"{PREFIX}/attempts/{original['attempt_id']}",
            f"{PREFIX}/operations/{operation_id}",
            f"{PREFIX}/requests/delete-source-{action}",
            *existing_audio,
        ):
            response = await client.get(path)
            assert response.status_code == 404, (path, response.text)
        archive = (await client.get(f"{PREFIX}/export")).json()
        assert len(archive["records"]) == 1
        archived_session = archive["records"][0]["data"]
        assert archived_session["domain"]["task"]["transfer_questions"]
        assert "transfer_questions" not in survivor["domain"]["task"]
        assert public_learning_result(archived_session) == survivor
        assert len(archive["operations"]) == 1
        assert archive["operations"][0]["request_id"] == "after-source-deletion-start"


@pytest.mark.asyncio
async def test_timed_out_turn_cannot_publish_when_provider_returns_late(
    edge_application,
):
    server = edge_application.create(
        learning_max_workers=1, learning_operation_timeout=0.15
    )
    async with browser(server) as client:
        session = await start(client)
        edge_application.llm.block_coach = True
        pending = await client.post(
            f"{PREFIX}/sessions/{session['session_id']}/turns",
            json={
                "request_id": "timed-out-edge-turn",
                "expected_revision": session["revision"],
                "text": "I went hiking on Saturday.",
            },
        )
        assert pending.status_code == 202, pending.text
        try:
            assert await asyncio.to_thread(edge_application.llm.started.wait, 2)
            interrupted = await terminal(client, pending)
            assert interrupted["status"] == "interrupted", interrupted
            assert await get_session(client, session) == session
        finally:
            edge_application.llm.release.set()
            edge_application.llm.block_coach = False
        await start(client, "after-timed-out-worker")
        assert await get_session(client, session) == session
        recovered = await client.get(
            f"{PREFIX}/operations/{pending.json()['operation_id']}"
        )
        assert recovered.json()["status"] == "interrupted"
        current = await add_turn(
            client, session, "I spent Sunday with my family.", "fresh-after-timeout"
        )
        assert current["revision"] == session["revision"] + 1
        assert [
            turn["text"] for turn in current["turns"] if turn["role"] == "user"
        ] == ["I spent Sunday with my family."]
