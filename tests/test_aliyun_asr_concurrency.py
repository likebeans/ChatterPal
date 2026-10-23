"""Offline concurrency regressions using the real Aliyun ASR provider."""

import asyncio
import io
import json
import queue
import threading
import wave
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from chatterpal.core.asr import aliyun
from chatterpal.core.asr.base import ASRError
from chatterpal.core.tts.edge import EdgeTTS
from chatterpal.services.chat import ChatService
from chatterpal.web import api_server


@pytest.fixture
def wav_uploads(tmp_path):
    """Distinct, valid one-second mono PCM WAVs with normal quality checks."""
    uploads = []
    for name, frequency in (("first", 440), ("second", 880)):
        samples = np.arange(16000) / 16000
        pcm = (16000 * np.sin(2 * np.pi * frequency * samples)).astype("<i2")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(pcm.tobytes())
        path = tmp_path / f"{name}.wav"
        path.write_bytes(buffer.getvalue())
        uploads.append(path)
    return uploads


@pytest.fixture
def nls_boundary(monkeypatch):
    """Replace only the NLS SDK, retaining its per-transcriber callbacks."""
    boundary = SimpleNamespace(stopped=queue.Queue(), instances=[])

    def hold_stop(transcriber):
        boundary.stopped.put(transcriber)
        if not transcriber.release_stop.wait(5):
            raise TimeoutError("test did not release the NLS stop call")

    boundary.on_stop = hold_stop

    class Transcriber:
        def __init__(self, **options):
            self.options = options
            self.audio = bytearray()
            self.release_stop = threading.Event()
            boundary.instances.append(self)

        def start(self, **options):
            self.start_options = options
            self.options["on_start"]("{}")
            return True

        def send_audio(self, data):
            self.audio.extend(data)

        def stop(self):
            boundary.on_stop(self)

        def complete(self, text):
            self.options["on_completed"](json.dumps({"payload": {"result": text}}))

        def fail(self, message):
            self.options["on_error"](message)

        def close(self):
            self.options["on_close"]()

    monkeypatch.setattr(
        aliyun, "nls", SimpleNamespace(NlsSpeechTranscriber=Transcriber)
    )
    boundary.asr = aliyun.AliyunASR({"appkey": "test-app", "token": "test-token"})
    return boundary


def release_transcribers(boundary):
    for transcriber in boundary.instances:
        transcriber.close()
        transcriber.release_stop.set()


@pytest.mark.parametrize("entrypoint", ["file", "bytes"])
@pytest.mark.parametrize(
    "second_event", ["completed", "error", "invalid_completed", "close"]
)
def test_concurrent_terminal_callbacks_only_affect_their_recognition(
    nls_boundary, wav_uploads, entrypoint, second_event
):
    boundary = nls_boundary
    asr = boundary.asr

    def recognize(path):
        if entrypoint == "bytes":
            return asr.recognize(path.read_bytes())
        return asr.recognize_file(str(path))

    with ThreadPoolExecutor(max_workers=2) as executor:
        try:
            first = executor.submit(recognize, wav_uploads[0])
            first_sdk = boundary.stopped.get(timeout=5)
            first_sdk.complete("FIRST AUDIO")

            second = executor.submit(recognize, wav_uploads[1])
            second_sdk = boundary.stopped.get(timeout=5)
            if second_event == "completed":
                second_sdk.complete("SECOND AUDIO")
            elif second_event == "error":
                second_sdk.fail("SECOND AUDIO failed")
            elif second_event == "invalid_completed":
                second_sdk.options["on_completed"]("invalid JSON")
            else:
                second_sdk.close()

            first_sdk.release_stop.set()
            assert first.result(timeout=5) == "FIRST AUDIO"
            second_sdk.release_stop.set()
            if second_event in ("error", "invalid_completed"):
                expected = (
                    "SECOND AUDIO failed"
                    if second_event == "error"
                    else "Expecting value"
                )
                with pytest.raises(ASRError, match=expected):
                    second.result(timeout=5)
            else:
                assert second.result(timeout=5) == (
                    "SECOND AUDIO" if second_event == "completed" else None
                )
        finally:
            release_transcribers(boundary)


@pytest.mark.parametrize(
    "late_event", ["completed", "error", "invalid_completed", "close"]
)
def test_late_callbacks_cannot_finish_the_next_recognition(
    nls_boundary, wav_uploads, late_event
):
    boundary = nls_boundary
    with ThreadPoolExecutor(max_workers=2) as executor:
        try:
            first = executor.submit(boundary.asr.recognize_file, str(wav_uploads[0]))
            first_sdk = boundary.stopped.get(timeout=5)
            first_sdk.complete("FIRST AUDIO")
            first_sdk.release_stop.set()
            assert first.result(timeout=5) == "FIRST AUDIO"

            second = executor.submit(boundary.asr.recognize_file, str(wav_uploads[1]))
            second_sdk = boundary.stopped.get(timeout=5)
            if late_event == "completed":
                first_sdk.complete("STALE AUDIO")
            elif late_event == "error":
                first_sdk.fail("stale connection error")
            elif late_event == "invalid_completed":
                first_sdk.options["on_completed"]("invalid JSON")
            else:
                first_sdk.close()
            second_sdk.release_stop.set()

            # The second call still needs its own SDK result, even after an old
            # connection emits a terminal callback on the same provider.
            with pytest.raises(FutureTimeout):
                second.result(timeout=0.3)
            second_sdk.complete("SECOND AUDIO")
            assert second.result(timeout=5) == "SECOND AUDIO"
        finally:
            release_transcribers(boundary)


def test_audio_route_and_chat_service_keep_aliyun_recognitions_separate(
    nls_boundary, wav_uploads, monkeypatch, tmp_path
):
    boundary = nls_boundary
    loop_thread = threading.get_ident()
    texts = {
        wav_uploads[0].read_bytes()[44:]: "FIRST AUDIO",
        wav_uploads[1].read_bytes()[44:]: "SECOND AUDIO",
    }

    def stop(transcriber):
        # Each upload gets its own SDK callback in the chat worker.
        # Hold the recognitions so separate requests overlap.
        transcriber.complete(texts[bytes(transcriber.audio)])
        if threading.get_ident() != loop_thread:
            boundary.stopped.put(transcriber)
            if not transcriber.release_stop.wait(5):
                raise TimeoutError("test did not release the chat recognition")

    def communicate(text, *args, **kwargs):
        async def save(output_path):
            Path(output_path).write_bytes(text.encode())

        return SimpleNamespace(save=save)

    boundary.on_stop = stop
    monkeypatch.setattr(
        api_server.APIServer, "_init_core_components", lambda self: None
    )
    monkeypatch.setattr(api_server.APIServer, "_init_services", lambda self: None)
    monkeypatch.setattr("edge_tts.Communicate", communicate)
    api = api_server.APIServer(settings=SimpleNamespace())
    api.temp_dir = tmp_path
    api.asr = boundary.asr
    api.tts = EdgeTTS()
    api.llm = SimpleNamespace(
        chat=lambda messages, **kwargs: f"Answer to: {messages[-1]['content']}"
    )
    api.chat_service = ChatService(asr=api.asr, tts=api.tts, llm=api.llm)
    assert api.asr.enable_quality_check and api.asr.enable_confidence_check
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
                        files={"audio": ("input.wav", wav_uploads[0].read_bytes())},
                        data={"session_id": "first"},
                    )
                )
                pending.append(first)
                first_sdk = await asyncio.to_thread(boundary.stopped.get, True, 5)
                second = asyncio.create_task(
                    client.post(
                        "/api/chat/audio",
                        files={"audio": ("input.wav", wav_uploads[1].read_bytes())},
                        data={"session_id": "second"},
                    )
                )
                pending.append(second)
                second_sdk = await asyncio.to_thread(boundary.stopped.get, True, 5)
                first_sdk.release_stop.set()
                first_response = await asyncio.wait_for(first, 5)
                assert first_response.status_code == 200
                first_data = first_response.json()
                assert first_data["recognized_text"] == "FIRST AUDIO"
                assert first_data["response"] == "Answer to: FIRST AUDIO"
                assert first_data["session_id"] == "first"
                first_audio = await client.get(first_data["audio_url"])
                assert first_audio.status_code == 200
                assert first_audio.content == b"Answer to: FIRST AUDIO"

                first_sdk.close()
                second_sdk.release_stop.set()
                second_response = await asyncio.wait_for(second, 5)
                assert second_response.status_code == 200
                second_data = second_response.json()
                assert second_data["recognized_text"] == "SECOND AUDIO"
                assert second_data["response"] == "Answer to: SECOND AUDIO"
                assert second_data["session_id"] == "second"
                assert first_data["audio_url"] != second_data["audio_url"]
                second_audio = await client.get(second_data["audio_url"])
                assert second_audio.status_code == 200
                assert second_audio.content == b"Answer to: SECOND AUDIO"
            finally:
                release_transcribers(boundary)
                await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(request())
    assert not list(api.temp_dir.glob("input_*.wav"))
    assert len(boundary.instances) == 2  # One worker recognition per upload.
    for session_id, text in (("first", "FIRST AUDIO"), ("second", "SECOND AUDIO")):
        history = api.chat_service.get_conversation_history(session_id)
        assert [message for message in history if message["role"] != "system"] == [
            {"role": "user", "content": text},
            {"role": "assistant", "content": f"Answer to: {text}"},
        ]
