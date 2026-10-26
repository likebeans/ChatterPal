"""Offline integration tests for chat, evaluation, and correction services."""

import io
import json
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from chatterpal.core.asr.base import ASRError
from chatterpal.core.asr.whisper import WhisperASR
from chatterpal.core.assessment.base import AssessmentError, AssessmentResult
from chatterpal.core.errors import SpeechRecognitionError, error_handler
from chatterpal.core.llm.alibaba import AlibabaBailianLLM
from chatterpal.core.tts.edge import EdgeTTS
from chatterpal.services.chat import ChatService
from chatterpal.services.correction import CorrectionReport, CorrectionService
from chatterpal.services.evaluation import EvaluationService


@pytest.fixture
def service_components(monkeypatch, tmp_path, setup_test_environment):
    """Keep real adapters and replace only model/cloud SDK entry points."""
    import whisper
    from chatterpal.core.assessment import prosody as prosody_module
    from chatterpal.core.llm import alibaba as llm_module
    from chatterpal.core.tts import edge as tts_module

    sample_rate = 16000
    times = np.arange(sample_rate * 2) / sample_rate
    audio_array = (0.4 * np.sin(2 * np.pi * 440 * times)).astype(np.float32)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes((audio_array * 32767).astype(np.int16).tobytes())
    audio_bytes = buffer.getvalue()
    audio_path = tmp_path / "speech.wav"
    audio_path.write_bytes(audio_bytes)

    asr_sdk = Mock(return_value={"text": "Hello, how are you today"})
    monkeypatch.setattr(
        whisper, "load_model", Mock(return_value=SimpleNamespace(transcribe=asr_sdk))
    )

    async def save_audio(path):
        with open(path, "wb") as output:
            output.write(audio_bytes)

    tts_save = AsyncMock(side_effect=save_audio)
    tts_sdk = Mock(return_value=SimpleNamespace(save=tts_save))
    monkeypatch.setattr(tts_module, "edge_tts", SimpleNamespace(Communicate=tts_sdk))

    def complete(**kwargs):
        content = kwargs["messages"][-1]["content"]
        if "machine transcript" in content:
            response = json.dumps({
                "overall_score": 85,
                "pronunciation_score": 90,
                "fluency_score": 80,
                "accuracy_score": 85,
                "feedback": "Good pronunciation overall",
                "suggestions": ["Practice vowel sounds", "Work on intonation"],
            })
        else:
            response = f"Response to: {content}"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=response))],
            usage=None,
        )

    llm_sdk = Mock(side_effect=complete)
    client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=llm_sdk)
    ))
    monkeypatch.setattr(llm_module, "OpenAI", Mock(return_value=client))
    # myprosody delegates to bundled Praat resources; keep that boundary offline.
    monkeypatch.setattr(prosody_module, "mysp", SimpleNamespace(
        myspsyl=Mock(return_value=5),
        mysppaus=Mock(return_value=1),
        myspsr=Mock(return_value=120),
        myspatc=Mock(return_value=150),
    ))

    return SimpleNamespace(
        asr=WhisperASR({"language": "en"}),
        tts=EdgeTTS({"enable_cache": False}),
        llm=AlibabaBailianLLM({"api_key": "offline-test-key"}),
        asr_sdk=asr_sdk,
        tts_sdk=tts_sdk,
        tts_save=tts_save,
        llm_sdk=llm_sdk,
        audio_bytes=audio_bytes,
        audio_path=str(audio_path),
        gradio_audio=(sample_rate, audio_array),
    )


class TestChatService:
    """Exercise ChatService with real provider adapters."""

    @pytest.fixture(autouse=True)
    def setup_service(self, service_components):
        self.components = service_components
        self.asr = service_components.asr
        self.tts = service_components.tts
        self.llm = service_components.llm
        self.chat_service = ChatService(asr=self.asr, tts=self.tts, llm=self.llm)

    def test_initialization(self):
        assert self.chat_service.asr is self.asr
        assert self.chat_service.tts is self.tts
        assert self.chat_service.llm is self.llm
        assert len(self.chat_service.sessions) == 0

    def test_process_audio_input_bytes(self):
        """Process valid WAV bytes through recognition, chat, and synthesis."""
        result = self.chat_service.chat_with_audio(self.components.audio_bytes)
        assert len(result) == 3
        response_text, response_audio, session_id = result
        assert response_text.startswith("Response to:")
        assert response_audio == self.components.audio_bytes
        assert session_id is not None
        self.components.asr_sdk.assert_called_once()
        self.components.tts_sdk.assert_called_once()

    def test_process_audio_input_file(self):
        result = self.chat_service.chat_with_audio(self.components.audio_path)
        assert len(result) == 3
        response_text, response_audio, session_id = result
        assert response_text.startswith("Response to:")
        assert response_audio == self.components.audio_bytes
        assert session_id in self.chat_service.sessions

    def test_process_audio_input_gradio_tuple(self):
        result = self.chat_service.chat_with_audio(self.components.gradio_audio)
        assert len(result) == 3
        response_text, response_audio, session_id = result
        assert response_text.startswith("Response to:")
        assert response_audio == self.components.audio_bytes
        assert session_id in self.chat_service.sessions

    def test_process_text_input(self):
        text_input = "Hello, how are you"
        result = self.chat_service.chat_with_text(text_input)
        assert len(result) == 2
        response_text, session_id = result
        assert response_text == f"Response to: {text_input}"
        assert session_id is not None
        self.components.asr_sdk.assert_not_called()

    def test_get_conversation_history(self):
        session_id = self.chat_service.create_session()
        self.chat_service.chat_with_text("Hello", session_id)
        self.chat_service.chat_with_text("How are you", session_id)
        history = self.chat_service.get_conversation_history(session_id)
        assert len(history) >= 2
        assert [msg["content"] for msg in history if msg["role"] == "user"] == [
            "Hello", "How are you"
        ]

    def test_clear_conversation(self):
        session_id = self.chat_service.create_session()
        self.chat_service.chat_with_text("Hello", session_id)
        self.chat_service.clear_conversation_history(session_id)
        history = self.chat_service.get_conversation_history(session_id)
        assert len(history) <= 1
        assert all(message["role"] == "system" for message in history)

    def test_set_system_prompt(self):
        system_prompt = "You are a helpful English teacher."
        session_id = self.chat_service.create_session()
        self.chat_service.set_system_prompt(session_id, system_prompt)
        history = self.chat_service.get_conversation_history(session_id)
        assert len(history) >= 1
        assert history[0]["role"] == "system"
        assert history[0]["content"] == system_prompt

    def test_generate_topic_suggestions(self):
        session_id = self.chat_service.create_session()
        topics = self.chat_service.get_conversation_topics(session_id)
        assert isinstance(topics, list)
        assert topics == []

    def test_error_handling_asr_failure(self):
        self.components.asr_sdk.side_effect = RuntimeError("ASR failed")
        with pytest.raises(SpeechRecognitionError) as error:
            self.chat_service.chat_with_audio(
                self.components.audio_path, max_retries=1
            )
        assert error.value.error_info.code == "ASR_SERVICE_ERROR"
        self.components.asr_sdk.assert_called_once()
        self.components.llm_sdk.assert_not_called()

    def test_error_handling_tts_failure(self):
        self.components.tts_save.side_effect = RuntimeError("TTS failed")
        result = self.chat_service.chat_with_audio(self.components.audio_path)
        assert len(result) == 3
        response_text, response_audio, session_id = result
        assert response_text.startswith("Response to:")
        assert response_audio is None
        assert session_id in self.chat_service.sessions
        self.components.tts_save.assert_awaited()

    def test_process_chat_text_input(self):
        text_input = "Hello, how are you"
        result = self.chat_service.process_chat(
            text_input=text_input, use_text_input=True
        )
        assert len(result) == 2
        audio_output, formatted_history = result
        assert len(audio_output) == 2
        sample_rate, audio_data = audio_output
        assert sample_rate == 16000
        assert audio_data is not None
        assert isinstance(audio_data, np.ndarray)
        assert audio_data.size == 32000
        assert isinstance(formatted_history, list)
        assert len(formatted_history) >= 1
        last_entry = formatted_history[-1]
        assert len(last_entry) == 2
        assert last_entry[0] == text_input
        assert last_entry[1] == f"Response to: {text_input}"

    def test_process_chat_audio_input(self):
        result = self.chat_service.process_chat(
            audio=self.components.audio_path, use_text_input=False
        )
        assert len(result) == 2
        audio_output, formatted_history = result
        assert len(audio_output) == 2
        sample_rate, audio_data = audio_output
        assert sample_rate == 16000
        assert audio_data is not None
        assert isinstance(formatted_history, list)
        assert len(formatted_history) >= 1
        assert formatted_history[-1][0] == "Hello, how are you today"

    def test_process_chat_with_session_id(self):
        session_id = self.chat_service.create_session()
        result = self.chat_service.process_chat(
            text_input="Hello", use_text_input=True, session_id=session_id
        )
        assert len(result) == 2
        history = self.chat_service.get_conversation_history(session_id)
        assert len(history) >= 2
        assert history[-2]["content"] == "Hello"
        assert history[-1]["content"] == "Response to: Hello"

    def test_process_chat_error_handling(self):
        result = self.chat_service.process_chat(text_input="", use_text_input=True)
        assert len(result) == 2
        audio_output, formatted_history = result
        assert audio_output is None
        assert len(formatted_history) >= 1
        error_entry = formatted_history[-1]
        message = error_entry[1]
        assert "文本" in message and ("空" in message or "输入" in message)
        self.components.llm_sdk.assert_not_called()

    def test_generate_topic_success(self):
        topic = self.chat_service.generate_topic()
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert len(topic) >= 10

    def test_generate_topic_llm_failure(self):
        session_id = self.chat_service.create_session()
        self.chat_service.chat_with_text("Let's discuss travel", session_id)
        self.components.llm_sdk.reset_mock()
        self.components.llm_sdk.side_effect = RuntimeError("LLM failed")
        topic = self.chat_service.generate_topic(session_id)
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert len(topic) >= 10
        assert topic.endswith("?") or any(
            word in topic.lower() for word in ["tell me", "describe", "what", "how"]
        )
        self.components.llm_sdk.assert_called()

    def test_generate_topic_no_llm(self):
        service = ChatService(asr=self.asr, tts=self.tts, llm=None)
        topic = service.generate_topic()
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert len(topic) >= 10
        assert topic.endswith("?") or any(
            word in topic.lower() for word in ["tell me", "describe", "what", "how"]
        )
        predefined = service.topic_generator.default_topics["intermediate"]
        assert topic in [item for topics in predefined.values() for item in topics]

    def test_clear_context_specific_session(self):
        session_id = self.chat_service.create_session()
        self.chat_service.chat_with_text("Hello", session_id)
        history_before = self.chat_service.get_conversation_history(session_id)
        assert len(history_before) >= 2
        assert self.chat_service.clear_context(session_id) is True
        history_after = self.chat_service.get_conversation_history(session_id)
        assert len(history_after) <= 1

    def test_clear_context_all_sessions(self):
        session1 = self.chat_service.create_session()
        session2 = self.chat_service.create_session()
        self.chat_service.chat_with_text("Hello", session1)
        self.chat_service.chat_with_text("Hi", session2)
        assert len(self.chat_service.sessions) == 2
        assert self.chat_service.clear_context() is True
        assert len(self.chat_service.sessions) == 0

    def test_clear_context_nonexistent_session(self):
        assert self.chat_service.clear_context("nonexistent_session_id") is False


class TestEvaluationService:
    @pytest.fixture(autouse=True)
    def setup_service(self, service_components):
        self.components = service_components
        self.evaluation_service = EvaluationService(
            asr=service_components.asr, llm=service_components.llm
        )

    def test_initialization(self):
        assert self.evaluation_service.asr is self.components.asr
        assert self.evaluation_service.llm is self.components.llm

    def test_evaluate_pronunciation_with_target(self):
        target_text = "Hello, how are you today"
        result = self.evaluation_service.evaluate_pronunciation(
            self.components.audio_bytes, target_text
        )
        assert isinstance(result, AssessmentResult)
        assert result.overall_score is None
        assert result.dimensions['overall']['status'] == 'unavailable'
        assert result.recognized_text == "Hello, how are you today"
        assert result.target_text == target_text
        assert "Good pronunciation overall" in result.feedback
        self.components.llm_sdk.assert_called_once()

    def test_evaluate_pronunciation_without_target(self):
        result = self.evaluation_service.evaluate_pronunciation(
            self.components.audio_bytes
        )
        assert isinstance(result, AssessmentResult)
        assert result.overall_score is None
        assert result.dimensions['overall']['status'] == 'unavailable'
        assert result.target_text == ""

    def test_evaluate_pronunciation_gradio_audio(self):
        target_text = "Test sentence"
        result = self.evaluation_service.evaluate_pronunciation(
            self.components.gradio_audio, target_text
        )
        assert isinstance(result, AssessmentResult)
        assert result.target_text == target_text
        assert result.recognized_text == "Hello, how are you today"

    def test_generate_default_feedback(self):
        # EvaluationService uses the 0-100 score scale in its prompts and thresholds.
        scores = {"overall": 85, "fluency": 80, "pronunciation": 90, "accuracy": 85}
        feedback = self.evaluation_service._generate_default_feedback(scores, 0.9)
        assert isinstance(feedback, str)
        assert len(feedback) > 0
        assert "暂不可评估" in feedback
        assert "未校准" in feedback

    def test_calculate_text_similarity(self):
        score = self.evaluation_service._calculate_similarity("Hello world", "Hello world")
        assert score == 1.0
        score = self.evaluation_service._calculate_similarity("Hello there", "Hello world")
        assert 0 <= score <= 1

    def test_error_handling_asr_failure(self):
        self.components.asr_sdk.side_effect = RuntimeError("ASR failed")
        # _recognize_audio wraps recognition errors in ASRError, which is re-raised.
        with pytest.raises(ASRError, match="ASR failed"):
            self.evaluation_service.evaluate_pronunciation(self.components.audio_bytes)
        self.components.asr_sdk.assert_called_once()

    def test_error_handling_invalid_audio(self):
        with pytest.raises(AssessmentError):
            self.evaluation_service.evaluate_pronunciation(None)


class TestCorrectionService:
    @pytest.fixture(autouse=True)
    def setup_service(self, service_components):
        self.components = service_components
        self.correction_service = CorrectionService(asr=service_components.asr)

    def test_initialization(self):
        assert self.correction_service.asr is self.components.asr
        assert self.correction_service.corrector is not None
        assert self.correction_service.phoneme_analyzer is not None
        assert self.correction_service.prosody_analyzer is not None

    def test_comprehensive_correction(self):
        result = self.correction_service.comprehensive_correction(
            self.components.audio_bytes, "Hello, how are you today"
        )
        assert isinstance(result, CorrectionReport)
        assert result.overall_score is None
        assert result.dimensions["overall"]["status"] == "unavailable"
        assert result.dimensions["accuracy"]["score"] == 100
        assert result.dimensions["accuracy"]["source"] == "text_sequence_alignment"
        assert isinstance(result.pronunciation_errors, list)
        assert isinstance(result.improvement_suggestions, list)

    def test_comprehensive_correction_gradio_audio(self):
        result = self.correction_service.comprehensive_correction(
            self.components.gradio_audio, "Test sentence"
        )
        assert isinstance(result, CorrectionReport)

    def test_quick_correction(self):
        result = self.correction_service.quick_correction(
            self.components.audio_bytes, "Hello world"
        )
        assert isinstance(result, dict)
        assert "overall_score" in result
        assert "main_issues" in result
        assert "key_suggestions" in result

    def test_get_service_status(self):
        status = self.correction_service.get_service_status()
        assert isinstance(status, dict)
        assert "asr_available" in status
        assert "corrector_available" in status
        assert "phoneme_analyzer_available" in status
        assert "prosody_analyzer_available" in status

    def test_error_handling_correction_failure(self):
        self.components.asr_sdk.side_effect = RuntimeError("ASR failed")
        # A failed recognition must not silently produce a successful scored report.
        with pytest.raises(AssessmentError):
            self.correction_service.comprehensive_correction(self.components.audio_bytes)

    def test_error_handling_invalid_audio(self):
        with pytest.raises(AssessmentError):
            self.correction_service.comprehensive_correction(None)


class TestServiceIntegration:
    @pytest.fixture(autouse=True)
    def setup_services(self, service_components):
        self.components = service_components
        self.chat_service = ChatService(
            asr=service_components.asr,
            tts=service_components.tts,
            llm=service_components.llm,
        )
        self.evaluation_service = EvaluationService(
            asr=service_components.asr, llm=service_components.llm
        )
        self.correction_service = CorrectionService(asr=service_components.asr)

    def test_chat_to_evaluation_workflow(self):
        chat_result = self.chat_service.chat_with_text("Hello, how are you")
        assert len(chat_result) == 2
        response_text, session_id = chat_result
        assert response_text == "Response to: Hello, how are you"
        assert session_id in self.chat_service.sessions
        result = self.evaluation_service.evaluate_pronunciation(
            self.components.audio_bytes, "Hello, how are you"
        )
        assert isinstance(result, AssessmentResult)

    def test_evaluation_to_correction_workflow(self):
        audio_data = self.components.audio_bytes
        target_text = "Hello world"
        result = self.evaluation_service.evaluate_pronunciation(audio_data, target_text)
        assert isinstance(result, AssessmentResult)
        correction = self.correction_service.comprehensive_correction(audio_data, target_text)
        assert isinstance(correction, CorrectionReport)

    def test_full_workflow_integration(self):
        chat_result = self.chat_service.chat_with_text("Practice sentence")
        assert len(chat_result) == 2
        user_audio = self.components.audio_bytes
        result = self.evaluation_service.evaluate_pronunciation(user_audio, "Practice sentence")
        assert isinstance(result, AssessmentResult)
        correction = self.correction_service.comprehensive_correction(user_audio, "Practice sentence")
        assert isinstance(correction, CorrectionReport)
        assert result.overall_score is None
        feedback_text = 'My pronunciation score is unavailable; help me practice.'
        feedback_result = self.chat_service.chat_with_text(feedback_text, chat_result[1])
        assert len(feedback_result) == 2
        assert feedback_result == (f"Response to: {feedback_text}", chat_result[1])
