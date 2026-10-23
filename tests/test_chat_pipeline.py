"""Chat work is performed once, off the API loop, with ordered session turns."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from chatterpal.core.asr.base import ASRResult, ConfidenceLevel
from chatterpal.core.tts.edge import EdgeTTS
from chatterpal.core.llm.base import LLMError
from chatterpal.services.chat import ChatService
from chatterpal.web import api_server


def transcription(text):
    return ASRResult(
        text=text,
        confidence=1.0,
        confidence_level=ConfidenceLevel.HIGH,
        audio_quality_score=1.0,
        processing_time=0.0,
        metadata={},
    )


@pytest.fixture
def chat_api(monkeypatch, tmp_path):
    monkeypatch.setattr(
        api_server.APIServer, "_init_core_components", lambda self: None
    )
    monkeypatch.setattr(api_server.APIServer, "_init_services", lambda self: None)
    api = api_server.APIServer(settings=SimpleNamespace())
    api.temp_dir = tmp_path
    api.asr = SimpleNamespace(
        recognize_with_error_handling=Mock(return_value=transcription("FIRST AUDIO"))
    )
    api.tts = EdgeTTS()
    api.llm = SimpleNamespace(
        chat=Mock(
            side_effect=lambda messages, **kwargs: f"Answer to: {messages[-1]['content']}"
        )
    )
    api.chat_service = ChatService(asr=api.asr, tts=api.tts, llm=api.llm)
    for name in ("evaluation_service", "correction_service", "ai_correction_service"):
        setattr(api, name, None)

    def communicate(text, *args, **kwargs):
        async def save(output_path):
            Path(output_path).write_bytes(b"ID3" + text.encode())

        return SimpleNamespace(save=save)

    monkeypatch.setattr("edge_tts.Communicate", communicate)
    return api


def test_audio_route_reuses_one_transcript_for_response_history_and_audio(chat_api):
    api = chat_api
    api.asr.recognize_with_error_handling.side_effect = [
        transcription("FIRST AUDIO"),
        transcription("SECOND RECOGNITION"),
    ]

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/chat/audio", files={"audio": ("input.wav", b"input")}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["recognized_text"] == "FIRST AUDIO"
            assert data["response"] == "Answer to: FIRST AUDIO"
            audio = await client.get(data["audio_url"])
            assert audio.status_code == 200
            assert audio.content == b"ID3Answer to: FIRST AUDIO"
            return data["session_id"]

    session_id = asyncio.run(request())
    api.asr.recognize_with_error_handling.assert_called_once()
    assert [
        message
        for message in api.chat_service.get_conversation_history(session_id)
        if message["role"] != "system"
    ] == [
        {"role": "user", "content": "FIRST AUDIO"},
        {"role": "assistant", "content": "Answer to: FIRST AUDIO"},
    ]
    assert not list(api.temp_dir.glob("input_*.wav"))


@pytest.mark.parametrize("provider", ["asr", "llm"])
def test_slow_chat_provider_does_not_block_health_requests(chat_api, provider):
    api = chat_api
    started, release, timed_out = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    provider_threads = []
    loop_thread = threading.get_ident()

    def block():
        provider_threads.append(threading.get_ident())
        started.set()
        if not release.wait(1):
            timed_out.set()
            raise TimeoutError("The API loop did not release the slow provider")

    if provider == "asr":

        def recognize(*args, **kwargs):
            block()
            return transcription("FIRST AUDIO")

        api.asr.recognize_with_error_handling.side_effect = recognize
    else:

        def chat(messages, **kwargs):
            block()
            return "Hello"

        api.llm.chat.side_effect = chat

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://test"
        ) as client:
            if provider == "asr":
                pending = asyncio.create_task(
                    client.post(
                        "/api/chat/audio", files={"audio": ("input.wav", b"input")}
                    )
                )
            else:
                pending = asyncio.create_task(
                    client.post("/api/chat", json={"message": "Hello"})
                )
            try:
                assert await asyncio.to_thread(started.wait, 2)
                assert (
                    not timed_out.is_set()
                ), "Slow provider blocked the API event loop"
                health = await asyncio.wait_for(client.get("/health"), timeout=0.5)
                assert health.status_code == 200
                assert not pending.done()
                release.set()
                response = await asyncio.wait_for(pending, timeout=2)
                assert response.status_code == 200
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(request())
    assert provider_threads and all(
        thread != loop_thread for thread in provider_threads
    )
    assert not list(api.temp_dir.glob("input_*.wav"))


def test_gradio_chat_recognizes_audio_once(chat_api):
    service = chat_api.chat_service
    session_id = service.create_session()
    audio, history = service.process_chat(
        audio=b"input", use_text_input=False, session_id=session_id
    )
    chat_api.asr.recognize_with_error_handling.assert_called_once()
    assert history == [["FIRST AUDIO", "Answer to: FIRST AUDIO"]]
    assert audio == b"ID3Answer to: FIRST AUDIO"


def test_legacy_audio_chat_still_returns_three_values(chat_api):
    result = chat_api.chat_service.chat_with_audio(b"input", return_audio=False)
    assert isinstance(result, tuple) and len(result) == 3
    response, audio, session_id = result
    assert response == "Answer to: FIRST AUDIO" and audio is None
    assert session_id in chat_api.chat_service.sessions
    chat_api.asr.recognize_with_error_handling.assert_called_once()


def test_same_session_turns_stay_ordered_when_llm_calls_overlap():
    started, release, second_worker = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    messages_seen = []

    def chat(messages, **kwargs):
        messages_seen.append(messages)
        if messages[-1]["content"] == "FIRST":
            started.set()
            if not release.wait(3):
                raise TimeoutError("first response was not released")
        return f"Answer to: {messages[-1]['content']}"

    service = ChatService(llm=SimpleNamespace(chat=chat))
    session = service.create_session("shared")

    def second_turn():
        second_worker.set()
        return service.chat_with_text("SECOND", session)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.chat_with_text, "FIRST", session)
        try:
            assert started.wait(1)
            second = executor.submit(second_turn)
            assert second_worker.wait(1)
            with pytest.raises(FutureTimeout):
                second.result(timeout=0.1)
            release.set()
            assert first.result(timeout=2)[0] == "Answer to: FIRST"
            assert second.result(timeout=2)[0] == "Answer to: SECOND"
        finally:
            release.set()

    expected = [
        {"role": "user", "content": "FIRST"},
        {"role": "assistant", "content": "Answer to: FIRST"},
        {"role": "user", "content": "SECOND"},
        {"role": "assistant", "content": "Answer to: SECOND"},
    ]
    assert [
        m for m in service.get_conversation_history(session) if m["role"] != "system"
    ] == expected
    assert [m for m in messages_seen[1] if m["role"] != "system"] == expected[:-1]


def test_failed_llm_turn_leaves_history_ready_for_the_next_request():
    llm = SimpleNamespace(
        chat=Mock(
            side_effect=[
                "Answer to: FIRST",
                OSError("offline LLM failure"),
                "Answer to: NEXT",
            ]
        )
    )
    service = ChatService(llm=llm)
    session_id = service.create_session()
    service.chat_with_text("FIRST", session_id)
    completed_history = service.get_conversation_history(session_id)

    with pytest.raises(LLMError, match="offline LLM failure"):
        service.chat_with_text("FAILED", session_id)

    assert service.get_conversation_history(session_id) == completed_history
    assert service.chat_with_text("NEXT", session_id) == ("Answer to: NEXT", session_id)
    assert llm.chat.call_args.args[0] == completed_history + [
        {"role": "user", "content": "NEXT"}
    ]
    assert service.get_conversation_history(session_id) == completed_history + [
        {"role": "user", "content": "NEXT"},
        {"role": "assistant", "content": "Answer to: NEXT"},
    ]


def test_different_sessions_can_generate_replies_concurrently():
    started, release = threading.Event(), threading.Event()

    def chat(messages, **kwargs):
        if messages[-1]["content"] == "FIRST":
            started.set()
            if not release.wait(3):
                raise TimeoutError("first response was not released")
        return f"Answer to: {messages[-1]['content']}"

    service = ChatService(llm=SimpleNamespace(chat=chat))
    first_session, second_session = service.create_session(), service.create_session()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.chat_with_text, "FIRST", first_session)
        try:
            assert started.wait(1)
            second = executor.submit(service.chat_with_text, "SECOND", second_session)
            assert second.result(timeout=1)[0] == "Answer to: SECOND"
        finally:
            release.set()
        assert first.result(timeout=2)[0] == "Answer to: FIRST"
