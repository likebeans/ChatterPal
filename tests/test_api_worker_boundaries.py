"""Real API boundaries retain worker inputs and honor provider output contracts."""

import asyncio
import io
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from chatterpal.core.asr.base import ASRResult, ConfidenceLevel
from chatterpal.core.tts import alibaba
from chatterpal.services.chat import AudioChatResult, ChatService
from chatterpal.web.api_server import APIServer


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setattr(APIServer, "_init_core_components", lambda self: None)
    monkeypatch.setattr(APIServer, "_init_services", lambda self: None)
    server = APIServer(
        settings=SimpleNamespace(api_max_pending=4, api_shutdown_timeout=0)
    )
    server.temp_dir = tmp_path
    server.asr = server.tts = server.llm = None
    server.chat_service = server.evaluation_service = None
    server.correction_service = server.ai_correction_service = None
    return server


@pytest.mark.parametrize("route", ["text", "audio"])
@pytest.mark.parametrize("encoding", ["wav", "opus"])
def test_alibaba_advertised_speaker_and_actual_encoding_reach_browser(
    api, monkeypatch, route, encoding
):
    if encoding == "wav":
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(b"\x01\x00" * 2400)
    else:
        import numpy as np
        import soundfile as sf

        output = io.BytesIO()
        sf.write(output, np.zeros(2400), 24000, format="OGG", subtype="OPUS")
    content = output.getvalue()
    calls = []

    class Synthesizer:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def call(self, text):
            return content

    monkeypatch.setattr(alibaba, "SpeechSynthesizer", Synthesizer)
    api.tts = alibaba.AlibabaTTS(
        {
            "api_key": "offline-test-key",
            "voice": "cosyvoice-v1",
            "voice_name": "longxiaochun",
            "format": encoding,
            "sample_rate": 24000,
        }
    )
    api.asr = SimpleNamespace(
        recognize_with_error_handling=lambda path, **kwargs: ASRResult(
            Path(path).read_text(),
            1.0,
            ConfidenceLevel.HIGH,
            1.0,
            0.0,
            {},
        )
    )
    api.llm = SimpleNamespace(chat=lambda messages: "Same reply")
    api.chat_service = ChatService(asr=api.asr, llm=api.llm, tts=api.tts)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            voices = (await client.get("/api/voices")).json()
            assert "longfei" in {voice["id"] for voice in voices}
            for voice in ("longfei", "longmiao"):
                if route == "text":
                    response = await client.post(
                        "/api/chat", json={"message": "learner", "voice": voice}
                    )
                else:
                    response = await client.post(
                        "/api/chat/audio",
                        files={"audio": ("input.wav", b"learner")},
                        data={"voice": voice},
                    )
                assert response.status_code == 200, response.text
                url = response.json()["audio_url"]
                assert url and url.endswith(f".{encoding}")
                audio = await client.get(url)
                assert audio.status_code == 200
                assert audio.headers["content-type"] == (
                    "audio/wav" if encoding == "wav" else "audio/ogg"
                )
                assert audio.content == content

    asyncio.run(run())
    assert [(call["model"], call["voice"]) for call in calls] == [
        ("cosyvoice-v1", "longfei"),
        ("cosyvoice-v1", "longmiao"),
    ]
    assert api.tts.tts_config.voice == "cosyvoice-v1"
    assert api.tts.tts_config.voice_name == "longxiaochun"


def test_health_responds_when_default_provider_executor_is_saturated(api):
    started, release = threading.Event(), threading.Event()

    def reply(**kwargs):
        started.set()
        if not release.wait(3):
            raise TimeoutError("Provider was not released")
        return "reply", "session"

    api.chat_service = SimpleNamespace(chat_with_text=reply)

    async def run():
        asyncio.get_running_loop().set_default_executor(
            ThreadPoolExecutor(max_workers=1)
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            pending = asyncio.create_task(
                client.post("/api/chat", json={"message": "learner"})
            )
            try:
                while not started.is_set():
                    await asyncio.sleep(0.001)
                assert (await client.get("/")).status_code == 200
                response = await asyncio.wait_for(client.get("/health"), 0.3)
                assert response.status_code == 200
                assert response.json()["runtime"]["requests"]["active"] == 1
                assert not pending.done()
            finally:
                release.set()
                assert (await pending).status_code == 200

    asyncio.run(run())


@pytest.mark.parametrize(
    "route", ["chat/audio", "pronunciation/analyze", "pronunciation/ai-analyze"]
)
def test_event_loop_teardown_keeps_upload_until_sync_worker_releases(
    api, route, monkeypatch
):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    cancelled, teardown, allow_teardown = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    cleaned = threading.Event()
    cleanup_upload = api._cleanup_analysis_upload

    def cleanup(path):
        cleanup_upload(path)
        cleaned.set()

    monkeypatch.setattr(api, "_cleanup_analysis_upload", cleanup)
    paths, survived, failures = [], [], []

    def read(path):
        paths.append(Path(path))
        started.set()
        try:
            if not release.wait(5):
                raise TimeoutError("Worker was not released")
            survived.append(Path(path).read_bytes())
            if route == "chat/audio":
                return AudioChatResult("learner", "reply", None, "session")
            raise RuntimeError("Controlled analysis failure after reading")
        finally:
            finished.set()

    api.chat_service = SimpleNamespace(
        chat_with_audio_result=lambda audio_data, **kwargs: read(audio_data)
    )
    api.correction_service = SimpleNamespace(
        comprehensive_correction=lambda audio_data, **kwargs: read(audio_data)
    )

    async def analyze(audio_data, **kwargs):
        return await asyncio.to_thread(read, audio_data)

    api.ai_correction_service = SimpleNamespace(comprehensive_ai_analysis=analyze)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            async with api.app.router.lifespan_context(api.app):
                pending = asyncio.create_task(
                    client.post(
                        f"/api/{route}",
                        files={"audio": ("input.wav", b"worker-owned audio")},
                        data={"reference_text": "reference"},
                    )
                )
                while not started.is_set():
                    await asyncio.sleep(0.001)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            # Trigger what asyncio.run/Uvicorn does after the lifespan returns.
            tasks = [
                task
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
            ]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            cancelled.set()
            while not allow_teardown.is_set():
                await asyncio.sleep(0.001)

    def runner():
        try:
            asyncio.run(run())
        except BaseException as error:
            failures.append(error)
        finally:
            teardown.set()

    thread = threading.Thread(target=runner)
    thread.start()
    try:
        assert started.wait(3)
        assert cancelled.wait(3)
        assert paths[
            0
        ].is_file(), "Event-loop cancellation deleted active provider input"
        allow_teardown.set()
        release.set()
        assert teardown.wait(3)
        assert cleaned.wait(3), "Worker did not clean its upload after completing"
    finally:
        release.set()
        allow_teardown.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert not failures
    assert finished.is_set()
    assert survived == [b"worker-owned audio"]
    assert not paths[0].exists()
