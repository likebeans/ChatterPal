"""Offline API regressions for synchronous TTS providers."""

import asyncio
import threading
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from chatterpal.core.asr.base import ASRResult, ConfidenceLevel
from chatterpal.core.tts.edge import EdgeTTS
from chatterpal.core.tts.base import TTSResult
from chatterpal.services.chat import AudioChatResult, ChatService
from chatterpal.web import api_server


@pytest.fixture
def api(monkeypatch, tmp_path):
    """Register real routes without starting ASR models or external services."""
    monkeypatch.setattr(
        api_server.APIServer, "_init_core_components", lambda self: None
    )
    monkeypatch.setattr(api_server.APIServer, "_init_services", lambda self: None)
    server = api_server.APIServer(settings=SimpleNamespace())
    server.temp_dir = tmp_path
    server.tts = EdgeTTS()
    return server


@pytest.fixture
def offline_chat_api(api, monkeypatch):
    """Keep routes, ChatService, and EdgeTTS real; replace provider boundaries."""

    def recognize(audio_data, **kwargs):
        return ASRResult(
            text=Path(audio_data).read_text(),
            confidence=1.0,
            confidence_level=ConfidenceLevel.HIGH,
            audio_quality_score=1.0,
            processing_time=0.0,
            metadata={},
        )

    def communicate(text, *args, **kwargs):
        async def save(output_path):
            Path(output_path).write_bytes(text.encode())

        return SimpleNamespace(save=save)

    api.asr = SimpleNamespace(recognize_with_error_handling=recognize)
    api.llm = SimpleNamespace(
        chat=lambda messages, **kwargs: f"Answer to: {messages[-1]['content']}"
    )
    api.chat_service = ChatService(asr=api.asr, tts=api.tts, llm=api.llm)
    monkeypatch.setattr("edge_tts.Communicate", communicate)
    return api


def test_concurrent_audio_chats_keep_uploads_and_histories_separate(
    offline_chat_api, monkeypatch
):
    api = offline_chat_api
    api_loop_thread = threading.get_ident()
    first_started, second_started = threading.Event(), threading.Event()
    release_first, release_second = threading.Event(), threading.Event()
    worker_uploads = []
    recognize = api.asr.recognize_with_error_handling

    def controlled_recognize(audio_data, **kwargs):
        if threading.get_ident() == api_loop_thread:
            return recognize(audio_data, **kwargs)
        worker_uploads.append(Path(audio_data))
        if not first_started.is_set():
            first_started.set()
            if not release_first.wait(5):
                raise TimeoutError("first upload was not released")
            return recognize(audio_data, **kwargs)
        result = recognize(audio_data, **kwargs)
        second_started.set()
        if not release_second.wait(5):
            raise TimeoutError("second upload was not released")
        return result

    monkeypatch.setattr(api.asr, "recognize_with_error_handling", controlled_recognize)
    for session_id in ("first", "second"):
        api.chat_service.create_session(session_id)

    async def request():
        pending = []
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            try:
                first = asyncio.create_task(
                    client.post(
                        "/api/chat/audio",
                        files={"audio": ("input.wav", b"FIRST AUDIO")},
                        data={"session_id": "first"},
                    )
                )
                pending.append(first)
                assert await asyncio.to_thread(first_started.wait, 2)
                second = asyncio.create_task(
                    client.post(
                        "/api/chat/audio",
                        files={"audio": ("input.wav", b"SECOND AUDIO")},
                        data={"session_id": "second"},
                    )
                )
                pending.append(second)
                assert await asyncio.to_thread(second_started.wait, 2)
                release_first.set()
                first_response = await asyncio.wait_for(first, 5)
                assert first_response.status_code == 200
                assert first_response.json()["recognized_text"] == "FIRST AUDIO"
                assert first_response.json()["response"] == "Answer to: FIRST AUDIO"
                assert not worker_uploads[0].exists()
                assert worker_uploads[1].read_bytes() == b"SECOND AUDIO"

                release_second.set()
                second_response = await asyncio.wait_for(second, 5)
                assert second_response.status_code == 200
                assert second_response.json()["recognized_text"] == "SECOND AUDIO"
                assert second_response.json()["response"] == "Answer to: SECOND AUDIO"
                for response, expected in (
                    (first_response, b"Answer to: FIRST AUDIO"),
                    (second_response, b"Answer to: SECOND AUDIO"),
                ):
                    audio = await client.get(response.json()["audio_url"])
                    assert audio.status_code == 200
                    assert audio.content == expected
            finally:
                release_first.set()
                release_second.set()
                await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(request())
    assert not list(api.temp_dir.glob("input_*.wav"))
    for session_id, text in (("first", "FIRST AUDIO"), ("second", "SECOND AUDIO")):
        history = api.chat_service.get_conversation_history(session_id)
        assert [message for message in history if message["role"] != "system"] == [
            {"role": "user", "content": text},
            {"role": "assistant", "content": f"Answer to: {text}"},
        ]


@pytest.mark.parametrize("chat_kind", ["text", "audio"])
def test_concurrent_same_session_replies_remain_separately_downloadable(
    offline_chat_api, monkeypatch, chat_kind
):
    api = offline_chat_api
    api.chat_service.create_session("shared-session")
    both_synthesizing = threading.Barrier(2)

    def communicate(text, *args, **kwargs):
        async def save(output_path):
            both_synthesizing.wait(timeout=5)
            Path(output_path).write_bytes(text.encode())

        return SimpleNamespace(save=save)

    monkeypatch.setattr("edge_tts.Communicate", communicate)

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:

            async def send(message):
                if chat_kind == "text":
                    return await client.post(
                        "/api/chat",
                        json={"message": message, "session_id": "shared-session"},
                    )
                return await client.post(
                    "/api/chat/audio",
                    files={"audio": ("input.wav", message.encode())},
                    data={"session_id": "shared-session"},
                )

            first, second = await asyncio.wait_for(
                asyncio.gather(send("FIRST MESSAGE"), send("SECOND MESSAGE")),
                timeout=10,
            )
            assert first.status_code == second.status_code == 200
            first_data, second_data = first.json(), second.json()
            assert (
                first_data["session_id"]
                == second_data["session_id"]
                == "shared-session"
            )
            assert first_data["audio_url"] and second_data["audio_url"]
            assert first_data["audio_url"] != second_data["audio_url"]
            for data, expected in (
                (first_data, "Answer to: FIRST MESSAGE"),
                (second_data, "Answer to: SECOND MESSAGE"),
            ):
                assert data["response"] == expected
                audio = await client.get(data["audio_url"])
                assert audio.status_code == 200
                assert audio.content == expected.encode()

    asyncio.run(request())
    assert not list(api.temp_dir.glob("input_*.wav"))


@pytest.mark.parametrize("failed_provider", ["asr", "llm"])
def test_failed_audio_chat_cleans_its_upload_without_disrupting_another_request(
    offline_chat_api, monkeypatch, failed_provider
):
    api = offline_chat_api
    api_loop_thread = threading.get_ident()
    held_started, release_held = threading.Event(), threading.Event()
    held_uploads = []
    recognize = api.asr.recognize_with_error_handling
    chat = api.llm.chat

    def controlled_recognize(audio_data, **kwargs):
        if threading.get_ident() != api_loop_thread:
            if Path(audio_data).read_bytes() == b"HELD AUDIO":
                held_uploads.append(Path(audio_data))
                held_started.set()
                if not release_held.wait(5):
                    raise TimeoutError("held upload was not released")
            elif failed_provider == "asr":
                raise OSError("offline ASR failure")
        return recognize(audio_data, **kwargs)

    def controlled_chat(messages, **kwargs):
        if failed_provider == "llm" and messages[-1]["content"] == "FAILED AUDIO":
            raise OSError("offline LLM failure")
        return chat(messages, **kwargs)

    monkeypatch.setattr(api.asr, "recognize_with_error_handling", controlled_recognize)
    monkeypatch.setattr(api.llm, "chat", controlled_chat)
    for session_id in ("held", "failed"):
        api.chat_service.create_session(session_id)

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            pending = asyncio.create_task(
                client.post(
                    "/api/chat/audio",
                    files={"audio": ("input.wav", b"HELD AUDIO")},
                    data={"session_id": "held"},
                )
            )
            try:
                assert await asyncio.to_thread(held_started.wait, 2)
                failed = await asyncio.wait_for(
                    client.post(
                        "/api/chat/audio",
                        files={"audio": ("input.wav", b"FAILED AUDIO")},
                        data={"session_id": "failed"},
                    ),
                    timeout=5,
                )
                assert failed.status_code == 500
                assert list(api.temp_dir.glob("input_*.wav")) == held_uploads
                assert held_uploads[0].read_bytes() == b"HELD AUDIO"
                release_held.set()
                response = await asyncio.wait_for(pending, 5)
                assert response.status_code == 200
                assert response.json()["recognized_text"] == "HELD AUDIO"
                assert response.json()["response"] == "Answer to: HELD AUDIO"
                audio = await client.get(response.json()["audio_url"])
                assert audio.status_code == 200
                assert audio.content == b"Answer to: HELD AUDIO"
            finally:
                release_held.set()
                await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(request())
    assert not list(api.temp_dir.glob("input_*.wav"))


def test_voices_returns_service_metadata_without_coroutine_warnings(
    api, monkeypatch, caplog
):
    catalogue = [
        {
            "Name": "Microsoft Server Speech Text to Speech Voice (en-GB, SoniaNeural)",
            "ShortName": "en-GB-SoniaNeural",
            "FriendlyName": "Microsoft Sonia Online (Natural) - English (United Kingdom)",
            "Locale": "en-GB",
            "Gender": "Female",
        },
        {
            "Name": "Microsoft Server Speech Text to Speech Voice (zh-CN, XiaoyiNeural)",
            "ShortName": "zh-CN-XiaoyiNeural",
            "FriendlyName": "Microsoft Xiaoyi Online (Natural) - Chinese (Mainland)",
            "Locale": "zh-CN",
            "Gender": "Female",
        },
    ]
    list_voices = AsyncMock(return_value=catalogue)
    monkeypatch.setattr("edge_tts.list_voices", list_voices)

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            first = await client.get("/api/voices")
            second = await client.get("/api/voices")
        return first, second

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        first, second = asyncio.run(request())

    assert not caught, [str(warning.message) for warning in caught]
    assert "asyncio.run() cannot be called" not in caplog.text
    assert first.status_code == second.status_code == 200
    assert (
        first.json()
        == second.json()
        == [
            {
                "id": "en-GB-SoniaNeural",
                "name": catalogue[0]["FriendlyName"],
                "description": "",
                "language": "en-GB",
                "gender": "Female",
            },
            {
                "id": "zh-CN-XiaoyiNeural",
                "name": catalogue[1]["FriendlyName"],
                "description": "",
                "language": "zh-CN",
                "gender": "Female",
            },
        ]
    )
    assert list_voices.await_count == 1


def test_voices_keeps_api_loop_responsive(api, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow_provider():
        started.set()
        if not release.wait(timeout=2):
            raise TimeoutError("API event loop could not release the provider")
        return ["offline-test-voice"]

    monkeypatch.setattr(api.tts, "get_supported_voices", slow_provider)
    monkeypatch.setattr(
        api.tts, "get_voice_info", lambda voice: {"display_name": "Test voice"}
    )

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            pending = asyncio.create_task(client.get("/api/voices"))
            try:
                assert await asyncio.to_thread(started.wait, 1)
                # This task must advance while the synchronous provider waits.
                release.set()
                return await asyncio.wait_for(pending, timeout=2)
            finally:
                release.set()
                if not pending.done():
                    pending.cancel()

    response = asyncio.run(request())
    assert response.status_code == 200
    assert response.json()[0]["id"] == "offline-test-voice"


def test_chat_synthesis_runs_outside_api_loop(api, monkeypatch):
    api.chat_service = Mock()
    api.chat_service.chat_with_text.return_value = (
        "Hello from the assistant",
        "test-session",
    )
    api_loop_thread = threading.get_ident()
    synthesis_threads = []

    async def save(output_path):
        synthesis_threads.append(threading.get_ident())
        from pathlib import Path

        Path(output_path).write_bytes(b"mock audio")

    communicate = Mock(return_value=SimpleNamespace(save=save))
    monkeypatch.setattr("edge_tts.Communicate", communicate)

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            return await client.post("/api/chat", json={"message": "Hello"})

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        response = asyncio.run(request())

    assert not caught, [str(warning.message) for warning in caught]
    assert response.status_code == 200
    assert response.json()["audio_url"].startswith("/api/audio/")
    assert synthesis_threads and synthesis_threads[0] != api_loop_thread
    assert list(api.temp_dir.glob("response_*.bin"))[0].read_bytes() == b"mock audio"


def test_audio_chat_synthesis_keeps_api_loop_responsive(api, monkeypatch):
    from pathlib import Path

    started = threading.Event()
    release = threading.Event()

    async def save(output_path):
        started.set()
        if not await asyncio.to_thread(release.wait, 2):
            raise TimeoutError("API event loop could not release synthesis")
        Path(output_path).write_bytes(b"mock audio")

    monkeypatch.setattr(
        "edge_tts.Communicate", Mock(return_value=SimpleNamespace(save=save))
    )
    api.asr = None
    api.chat_service = Mock()
    api.chat_service.chat_with_audio_result.side_effect = (
        lambda **kwargs: AudioChatResult(
            recognized_text="Hello from the user",
            response_text="Hello",
            response_audio=api.tts.synthesize("Hello"),
            session_id="test-session",
        )
    )

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            pending = asyncio.create_task(
                client.post(
                    "/api/chat/audio", files={"audio": ("input.wav", b"input audio")}
                )
            )
            try:
                assert await asyncio.to_thread(started.wait, 1)
                assert not pending.done(), "synthesis blocked the API event loop"
                release.set()
                response = await asyncio.wait_for(pending, timeout=2)
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)

            assert response.status_code == 200
            assert response.json()["audio_url"] is not None
            audio = await client.get(response.json()["audio_url"])
            assert audio.status_code == 200
            assert audio.content == b"mock audio"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        asyncio.run(request())
    assert not caught, [str(warning.message) for warning in caught]


def test_voices_service_failure_caches_consistent_fallback(api, monkeypatch, caplog):
    list_voices = AsyncMock(side_effect=OSError("voice service offline"))
    monkeypatch.setattr("edge_tts.list_voices", list_voices)

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            first = await client.get("/api/voices")
            second = await client.get("/api/voices")
        return first, second

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        first, second = asyncio.run(request())

    assert not caught, [str(warning.message) for warning in caught]
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(first.json()) == 6
    chinese_voice = next(
        voice for voice in first.json() if voice["id"] == "zh-CN-YunxiNeural"
    )
    assert chinese_voice["language"] == "zh-CN"
    assert chinese_voice["gender"] == "Male"
    list_voices.assert_awaited_once()
    assert caplog.text.count("voice service offline") == 1


@pytest.mark.parametrize("chat_kind", ["text", "audio"])
def test_concurrent_request_voices_reach_sdk_without_mutating_provider(
    offline_chat_api, monkeypatch, chat_kind
):
    api = offline_chat_api
    api.settings.api_max_pending = 2
    barrier = threading.Barrier(2)
    requests = []
    original_voice = api.tts.voice

    def communicate(text, voice, **kwargs):
        requests.append((text, voice))

        async def save(output_path):
            barrier.wait(timeout=3)
            Path(output_path).write_bytes(b"ID3" + voice.encode())

        return SimpleNamespace(save=save)

    monkeypatch.setattr("edge_tts.Communicate", communicate)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:

            async def send(voice):
                if chat_kind == "text":
                    return await client.post(
                        "/api/chat", json={"message": "same text", "voice": voice}
                    )
                return await client.post(
                    "/api/chat/audio",
                    files={"audio": ("input.wav", b"same text")},
                    data={"voice": voice},
                )

            voices = ["en-US-JennyNeural", "en-GB-SoniaNeural"]
            responses = await asyncio.wait_for(
                asyncio.gather(*(send(voice) for voice in voices)), 5
            )
            for response, voice in zip(responses, voices):
                assert response.status_code == 200, response.text
                url = response.json()["audio_url"]
                assert url.endswith(".mp3")
                audio = await client.get(url)
                assert audio.headers["content-type"] == "audio/mpeg"
                assert audio.content == b"ID3" + voice.encode()

    asyncio.run(run())
    assert {voice for _, voice in requests} == {
        "en-US-JennyNeural",
        "en-GB-SoniaNeural",
    }
    assert api.tts.voice == original_voice


def test_explicit_pcm_tts_is_wrapped_for_browser_playback(api, monkeypatch):
    import io
    import wave

    pcm = b"\x01\x00" * 160
    api.chat_service = SimpleNamespace(
        chat_with_text=lambda **kwargs: ("coach", "session")
    )
    monkeypatch.setattr(
        api.tts,
        "synthesize_with_error_handling",
        lambda text, **kwargs: TTSResult(text, pcm, 0.01, "pcm", 16000, False, 0, {}),
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            response = await client.post("/api/chat", json={"message": "learner"})
            assert response.status_code == 200
            assert response.json()["audio_url"].endswith(".wav")
            output = await client.get(response.json()["audio_url"])
            assert output.headers["content-type"] == "audio/wav"
            with wave.open(io.BytesIO(output.content), "rb") as audio:
                assert audio.getframerate() == 16000
                assert audio.getnchannels() == 1
                assert audio.readframes(160) == pcm

    asyncio.run(run())


def test_core_initialization_forwards_settings_to_provider_factories(monkeypatch):
    settings = SimpleNamespace(asr_provider="alibaba", llm_provider="openai")
    server = api_server.APIServer.__new__(api_server.APIServer)
    server.settings = settings
    server.logger = Mock()
    asr, tts, llm = Mock(), Mock(), Mock()
    create_asr, create_tts, create_llm = (
        Mock(return_value=asr),
        Mock(return_value=tts),
        Mock(return_value=llm),
    )
    monkeypatch.setattr(api_server, "create_asr", create_asr)
    monkeypatch.setattr(api_server, "create_tts", create_tts)
    monkeypatch.setattr(api_server, "create_llm", create_llm)

    server._init_core_components()

    create_asr.assert_called_once_with(settings)
    create_tts.assert_called_once_with(settings)
    create_llm.assert_called_once_with(settings)
    assert (server.asr, server.tts, server.llm) == (asr, tts, llm)
