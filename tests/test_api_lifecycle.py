"""Real legacy routes keep resource ownership until synchronous work completes."""

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from chatterpal.services.chat import AudioChatResult
from chatterpal.web.api_server import APIServer


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setattr(APIServer, "_init_core_components", lambda self: None)
    monkeypatch.setattr(APIServer, "_init_services", lambda self: None)
    server = APIServer(
        settings=SimpleNamespace(api_max_pending=1, api_shutdown_timeout=0)
    )
    server.temp_dir = tmp_path
    server.asr = server.tts = server.llm = None
    server.evaluation_service = server.correction_service = (
        server.ai_correction_service
    ) = None
    return server


def test_cancelled_audio_request_keeps_capacity_and_upload_until_worker_finishes(api):
    entered, release = threading.Event(), threading.Event()
    uploads, survived = [], []

    def reply(*, audio_data, **kwargs):
        path = Path(audio_data)
        uploads.append(path)
        entered.set()
        if not release.wait(5):
            raise TimeoutError("Worker was not released")
        survived.append(path.read_bytes())
        return AudioChatResult("learner", "coach", None, "session")

    api.chat_service = SimpleNamespace(chat_with_audio_result=reply)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            pending = asyncio.create_task(
                client.post(
                    "/api/chat/audio", files={"audio": ("input.wav", b"learner audio")}
                )
            )
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                assert uploads[0].is_file()
                health = (await client.get("/health")).json()
                assert health["runtime"]["requests"]["active"] == 1
                rejected = await client.post(
                    "/api/chat/audio", files={"audio": ("second.wav", b"second audio")}
                )
                assert rejected.status_code == 503
                assert len(uploads) == 1
                assert rejected.headers["retry-after"] == "1"
            finally:
                release.set()
                if api._request_tasks:
                    await asyncio.wait_for(
                        asyncio.gather(*tuple(api._request_tasks)), 2
                    )
            assert not uploads[0].exists()
            assert (await client.get("/health")).json()["runtime"]["requests"][
                "active"
            ] == 0
            response = await client.post(
                "/api/chat/audio", files={"audio": ("next.wav", b"next audio")}
            )
            assert response.status_code == 200

    asyncio.run(run())
    assert survived == [b"learner audio", b"next audio"]
    assert not list(api.temp_dir.glob("input_*"))


def test_shutdown_is_bounded_and_retains_active_upload(api):
    entered, release = threading.Event(), threading.Event()
    uploads = []

    def reply(*, audio_data, **kwargs):
        uploads.append(Path(audio_data))
        entered.set()
        if not release.wait(5):
            raise TimeoutError("Worker was not released")
        assert uploads[0].read_bytes() == b"active input"
        return AudioChatResult("learner", "coach", None, "session")

    api.chat_service = SimpleNamespace(chat_with_audio_result=reply)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            lifespan = api.app.router.lifespan_context(api.app)
            await lifespan.__aenter__()
            pending = asyncio.create_task(
                client.post(
                    "/api/chat/audio", files={"audio": ("input.wav", b"active input")}
                )
            )
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                await asyncio.wait_for(lifespan.__aexit__(None, None, None), 0.5)
                assert uploads[0].exists()
                assert len(api._request_tasks) == 1
                assert (
                    await client.post("/api/chat", json={"message": "new"})
                ).status_code == 503
            finally:
                release.set()
                assert (await asyncio.wait_for(pending, 2)).status_code == 200
            assert not uploads[0].exists()

    asyncio.run(run())


def test_audio_route_serves_only_generated_assets_with_actual_mime(api):
    api.chat_service = None
    name = api._generated_audio().save(b"ID3-audio", "mp3")
    upload = api.temp_dir / "input_private.wav"
    upload.write_bytes(b"private learner audio")

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/audio/{name}")
            assert response.status_code == 200
            assert response.headers["content-type"] == "audio/mpeg"
            assert response.headers["cache-control"] == "no-store"
            assert response.content == b"ID3-audio"
            assert (await client.get("/api/audio/input_private.wav")).status_code == 404
            assert (await client.get("/api/audio/missing.mp3")).status_code == 404

    asyncio.run(run())
    assert upload.read_bytes() == b"private learner audio"
