"""Offline end-to-end chat flows through real services and provider adapters."""

import io
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pytest

from chatterpal.core.asr.whisper import WhisperASR
from chatterpal.core.llm.alibaba import AlibabaBailianLLM
from chatterpal.core.tts.edge import EdgeTTS
from chatterpal.services.chat import ChatService
from chatterpal.utils.preferences import UserPreferences
from chatterpal.web.components.chat_tab import ChatTab


RECOGNIZED_TEXT = "Hello, how are you today"
RESPONSE_TEXT = "I'm doing well, thank you! How can I help you practice English today"


class TestEndToEndChatFlows:
    @pytest.fixture(autouse=True)
    def setup_components(self, monkeypatch, tmp_path, setup_test_environment):
        """Replace external SDK calls while retaining provider error handling."""
        import whisper
        from chatterpal.core.llm import alibaba as llm_module
        from chatterpal.core.tts import edge as tts_module

        sample_rate, samples = self._create_sample_audio_data()
        audio_buffer = io.BytesIO()
        with wave.open(audio_buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes((samples * 32767).astype(np.int16).tobytes())
        self.audio_bytes = audio_buffer.getvalue()
        audio_path = tmp_path / "speech.wav"
        audio_path.write_bytes(self.audio_bytes)
        self.audio_path = str(audio_path)

        self.asr_sdk = Mock(return_value={"text": RECOGNIZED_TEXT})
        monkeypatch.setattr(
            whisper, "load_model", Mock(return_value=SimpleNamespace(transcribe=self.asr_sdk))
        )

        async def save_audio(path):
            with open(path, "wb") as output:
                output.write(self.audio_bytes)

        self.tts_save = AsyncMock(side_effect=save_audio)
        self.tts_sdk = Mock(return_value=SimpleNamespace(save=self.tts_save))
        monkeypatch.setattr(tts_module, "edge_tts", SimpleNamespace(Communicate=self.tts_sdk))

        self.llm_sdk = Mock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=RESPONSE_TEXT))],
            usage=None,
        ))
        client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=self.llm_sdk)
        ))
        monkeypatch.setattr(llm_module, "OpenAI", Mock(return_value=client))

        self.chat_service = ChatService(
            asr=WhisperASR({"language": "en"}),
            tts=EdgeTTS({"enable_cache": False}),
            llm=AlibabaBailianLLM({"api_key": "offline-test-key"}),
            config={"max_history_length": 20, "session_timeout": 3600},
        )
        self.preferences = UserPreferences(config_dir=str(tmp_path / "preferences"))
        # ChatTab imports the function directly, so patch its lookup location.
        with patch(
            "chatterpal.web.components.chat_tab.get_preferences_manager",
            return_value=self.preferences,
        ):
            self.chat_tab = ChatTab(self.chat_service)

    def _create_sample_audio_data(self, duration=2.0):
        sample_rate = 16000
        times = np.arange(int(sample_rate * duration)) / sample_rate
        samples = (0.5 * np.sin(2 * np.pi * 440 * times)).astype(np.float32)
        return sample_rate, samples

    def test_voice_input_to_voice_output_complete_flow(self):
        session_id = self.chat_service.create_session()
        assert session_id is not None
        gradio_audio = self._create_sample_audio_data()
        response_text, response_audio, returned_session_id = self.chat_service.chat_with_audio(
            audio_data=gradio_audio, session_id=session_id, return_audio=True
        )
        assert response_text == RESPONSE_TEXT
        assert returned_session_id == session_id
        assert response_audio is not None
        assert isinstance(response_audio, bytes)
        assert len(response_audio) > 0
        self.asr_sdk.assert_called_once()
        self.tts_sdk.assert_called_once()
        with wave.open(io.BytesIO(response_audio), "rb") as audio:
            assert audio.getframerate() == 16000
            assert audio.getnframes() == 32000
        history = self.chat_service.get_conversation_history(session_id)
        assert len(history) >= 2
        user_messages = [msg for msg in history if msg["role"] == "user"]
        assistant_messages = [msg for msg in history if msg["role"] == "assistant"]
        assert len(user_messages) >= 1
        assert len(assistant_messages) >= 1
        assert user_messages[0]["content"] == RECOGNIZED_TEXT
        assert assistant_messages[0]["content"] == response_text

    def test_text_input_to_voice_output_complete_flow(self):
        session_id = self.chat_service.create_session()
        user_text = "I want to practice English conversation"
        response_text, returned_session_id = self.chat_service.chat_with_text(
            text=user_text, session_id=session_id
        )
        assert response_text == RESPONSE_TEXT
        assert returned_session_id == session_id
        response_audio = self.chat_service._synthesize_with_error_handling(response_text)
        assert response_audio is not None
        assert isinstance(response_audio, bytes)
        assert len(response_audio) > 0
        self.llm_sdk.assert_called_once()
        self.tts_sdk.assert_called_once()
        self.asr_sdk.assert_not_called()
        assert response_audio == self.audio_bytes
        history = self.chat_service.get_conversation_history(session_id)
        user_messages = [msg for msg in history if msg["role"] == "user"]
        assistant_messages = [msg for msg in history if msg["role"] == "assistant"]
        assert len(user_messages) >= 1
        assert len(assistant_messages) >= 1
        assert user_messages[0]["content"] == user_text
        assert assistant_messages[0]["content"] == response_text

    def test_input_mode_switching_complete_flow(self):
        assert self.chat_tab.preferences.get_input_mode() == "text"
        audio_output1, history1 = self.chat_tab._handle_chat(
            audio=None, text_input="Hello, I'm using text input", chat_history=[], use_text=True
        )
        assert audio_output1 is not None
        assert len(history1) > 0
        session_id = self.chat_tab.current_session_id
        new_use_text, audio_update, text_update, status_update, button_update = (
            self.chat_tab._toggle_input_mode(True)
        )
        assert new_use_text is False
        assert audio_update["visible"] is True
        assert text_update["visible"] is False
        assert "语音输入" in status_update["value"]
        assert "切换到文本输入" in button_update["value"]
        assert self.chat_tab.preferences.get_input_mode() == "voice"
        audio_output2, history2 = self.chat_tab._handle_chat(
            audio=self._create_sample_audio_data(), text_input="", chat_history=history1, use_text=False
        )
        assert audio_output2 is not None
        assert len(history2) > len(history1)
        assert self.chat_tab.current_session_id == session_id
        new_use_text2, audio_update2, text_update2, status_update2, button_update2 = (
            self.chat_tab._toggle_input_mode(False)
        )
        assert new_use_text2 is True
        assert audio_update2["visible"] is False
        assert text_update2["visible"] is True
        assert "文本输入" in status_update2["value"]
        assert "切换到语音输入" in button_update2["value"]
        assert self.chat_tab.preferences.get_input_mode() == "text"
        assert len(history2) >= 2
        assert history2[0] == history1[0]
        assert history2[-1] == [RECOGNIZED_TEXT, RESPONSE_TEXT]

    def test_conversation_context_maintenance(self):
        session_id = self.chat_service.create_session()
        response1, _ = self.chat_service.chat_with_text(
            "Hello, my name is Alice", session_id=session_id
        )
        response2, _, _ = self.chat_service.chat_with_audio(
            audio_data=self.audio_path, session_id=session_id
        )
        response3, _ = self.chat_service.chat_with_text(
            "What did I tell you my name was", session_id=session_id
        )
        history = self.chat_service.get_conversation_history(session_id)
        user_messages = [msg for msg in history if msg["role"] == "user"]
        assistant_messages = [msg for msg in history if msg["role"] == "assistant"]
        assert len(user_messages) == 3
        assert len(assistant_messages) == 3
        assert [msg["content"] for msg in user_messages] == [
            "Hello, my name is Alice", RECOGNIZED_TEXT, "What did I tell you my name was"
        ]
        assert [msg["content"] for msg in assistant_messages] == [response1, response2, response3]
        llm_calls = self.llm_sdk.call_args_list
        assert len(llm_calls) >= 3
        last_messages = llm_calls[-1].kwargs["messages"]
        assert len(last_messages) >= 6
        assert last_messages[1:] == history[1:-1]
        self.chat_service.clear_conversation_history(session_id)
        cleared_history = self.chat_service.get_conversation_history(session_id)
        assert [msg for msg in cleared_history if msg["role"] == "user"] == []

    def test_session_isolation_and_management(self):
        session1 = self.chat_service.create_session()
        session2 = self.chat_service.create_session()
        assert session1 != session2
        self.chat_service.chat_with_text("I'm in session 1", session_id=session1)
        self.chat_service.chat_with_text("I'm in session 2", session_id=session2)
        history1 = self.chat_service.get_conversation_history(session1)
        history2 = self.chat_service.get_conversation_history(session2)
        user_msgs1 = [msg for msg in history1 if msg["role"] == "user"]
        user_msgs2 = [msg for msg in history2 if msg["role"] == "user"]
        assert len(user_msgs1) == 1
        assert len(user_msgs2) == 1
        assert user_msgs1[0]["content"] == "I'm in session 1"
        assert user_msgs2[0]["content"] == "I'm in session 2"
        assert self.chat_service.delete_session(session1) is True
        assert self.chat_service.get_conversation_history(session1) == []
        assert self.chat_service.get_conversation_history(session2) == history2

    def test_ui_integration_with_chat_service(self):
        audio_output, chat_history = self.chat_tab._handle_chat(
            audio=self.audio_path, text_input="", chat_history=[], use_text=False
        )
        assert isinstance(audio_output, tuple)
        assert len(audio_output) == 2
        assert isinstance(audio_output[0], int)
        # process_chat decodes WAV frames to a numpy array for gr.Audio.
        assert isinstance(audio_output[1], np.ndarray)
        assert audio_output[0] == 16000
        assert audio_output[1].size == 32000
        assert isinstance(chat_history, list)
        assert chat_history
        for entry in chat_history:
            assert isinstance(entry, list)
            assert len(entry) == 2
        assert chat_history[-1] == [RECOGNIZED_TEXT, RESPONSE_TEXT]
        assert self.chat_tab.current_session_id is not None
        status_msg, cleared_history = self.chat_tab._clear_context()
        assert "清除" in status_msg
        assert cleared_history == []
        assert self.chat_tab.current_session_id is None

    def test_error_handling_in_complete_flows(self):
        # A file path reaches the failing SDK independently of input conversion.
        self.asr_sdk.side_effect = RuntimeError("ASR service unavailable")
        audio_output, chat_history = self.chat_tab._handle_chat(
            audio=self.audio_path, text_input="", chat_history=[], use_text=False
        )
        assert audio_output is None
        assert len(chat_history) > 0
        error_message = str(chat_history).lower()
        error_keywords = ["错误", "失败", "不可", "重试", "exception", "error", "未能识别"]
        assert any(keyword in error_message for keyword in error_keywords), error_message
        self.asr_sdk.assert_called()
        self.llm_sdk.assert_not_called()

        self.asr_sdk.side_effect = None
        self.tts_save.side_effect = RuntimeError("TTS service unavailable")
        audio_output, chat_history = self.chat_tab._handle_chat(
            text_input="Hello world", audio=None, chat_history=[], use_text=True
        )
        assert len(chat_history) > 0
        assert audio_output is None
        assert chat_history[-1] == ["Hello world", RESPONSE_TEXT]
        self.tts_save.assert_awaited()
        audio_output, chat_history = self.chat_tab._handle_chat(
            audio=None, text_input="", chat_history=[], use_text=True
        )
        assert audio_output is None
        assert len(chat_history) > 0
        assert chat_history[-1][1]

    def test_performance_and_timing(self):
        start_time = time.perf_counter()
        session_id = self.chat_service.create_session()
        response_text, _ = self.chat_service.chat_with_text(
            "Quick response test", session_id=session_id
        )
        assert time.perf_counter() - start_time < 5.0
        assert response_text == RESPONSE_TEXT
        start_time = time.perf_counter()
        response_text, response_audio, _ = self.chat_service.chat_with_audio(
            audio_data=self.audio_path, session_id=session_id
        )
        assert time.perf_counter() - start_time < 10.0
        assert response_text == RESPONSE_TEXT
        assert response_audio
        start_time = time.perf_counter()
        for i in range(5):
            self.chat_service.chat_with_text(f"Message {i}", session_id=session_id)
        assert time.perf_counter() - start_time < 15.0
        history = self.chat_service.get_conversation_history(session_id)
        user_messages = [msg for msg in history if msg["role"] == "user"]
        assert len(user_messages) >= 6

    def test_concurrent_sessions_handling(self):
        ready = Barrier(3)

        def worker(worker_id):
            session_id = self.chat_service.create_session()
            ready.wait(timeout=5)
            for i in range(3):
                response, returned_id = self.chat_service.chat_with_text(
                    f"Worker {worker_id}, message {i}", session_id=session_id
                )
                assert response == RESPONSE_TEXT
                assert returned_id == session_id
            history = self.chat_service.get_conversation_history(session_id)
            return worker_id, session_id, history

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(worker, worker_id) for worker_id in range(3)]
            results = []
            errors = []
            for future in futures:
                try:
                    results.append(future.result(timeout=10))
                except Exception as error:
                    errors.append(str(error))
        assert len(results) == 3, f"Expected three successful sessions: {errors}"
        assert len(errors) == 0, errors
        assert len({session_id for _, session_id, _ in results}) == 3
        for worker_id, session_id, history in results:
            assert len(history) >= 6
            assert [msg["content"] for msg in history if msg["role"] == "user"] == [
                f"Worker {worker_id}, message {i}" for i in range(3)
            ]
