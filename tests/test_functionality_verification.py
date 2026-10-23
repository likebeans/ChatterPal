"""Verify core modules and service workflows with offline SDK boundaries.

The other legacy verification modules import ``offline_runtime`` so this group
uses the same valid audio and real adapters without changing shared fixtures.
"""

import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import wave

import numpy as np
import pytest

from chatterpal.core.asr.base import ASRBase
from chatterpal.core.tts.base import TTSBase
from chatterpal.core.llm.base import LLMBase, Conversation
from chatterpal.core.assessment.base import AssessmentBase, AssessmentResult
from chatterpal.services.chat import ChatService
from chatterpal.services.evaluation import EvaluationService
from chatterpal.services.correction import CorrectionReport, CorrectionService


@pytest.fixture
def offline_runtime(monkeypatch, tmp_path):
    """Keep adapters/services real; replace Whisper, cloud SDKs and Praat scripts."""
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")

    from chatterpal.config.settings import Settings
    from chatterpal.core.asr.whisper import WhisperASR
    from chatterpal.core.llm.alibaba import AlibabaBailianLLM
    from chatterpal.core.tts.edge import EdgeTTS
    from chatterpal.utils.preferences import UserPreferences

    sample_rate = 16000
    time = np.arange(sample_rate * 2) / sample_rate
    audio_array = (0.3 * np.sin(2 * np.pi * 440 * time)).astype(np.float32)
    audio_array[: sample_rate // 4] = 0
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes((audio_array * 32767).astype("<i2").tobytes())
    audio_bytes = buffer.getvalue()
    audio_path = tmp_path / "practice.wav"
    audio_path.write_bytes(audio_bytes)

    runtime = SimpleNamespace(
        recognized_text="Hello, how are you",
        reply="I'm fine, thank you!",
        audio_bytes=audio_bytes,
        audio_path=str(audio_path),
        sample_rate=sample_rate,
        audio_array=audio_array,
    )
    runtime.whisper = Mock(side_effect=lambda *args, **kwargs: runtime.recognized_text)
    monkeypatch.setattr("speech_recognition.Recognizer.recognize_whisper", runtime.whisper)

    def complete(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=runtime.reply))],
            usage=None,
        )

    runtime.completion = Mock(side_effect=complete)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=runtime.completion))
    )
    monkeypatch.setattr("chatterpal.core.llm.alibaba.OpenAI", Mock(return_value=client))

    async def save(output_path):
        Path(output_path).write_bytes(audio_bytes)

    runtime.save = AsyncMock(side_effect=save)
    runtime.communicate = Mock(return_value=SimpleNamespace(save=runtime.save))
    monkeypatch.setattr("edge_tts.Communicate", runtime.communicate)
    monkeypatch.setattr(
        "chatterpal.core.assessment.prosody.mysp",
        SimpleNamespace(
            myspsyl=Mock(return_value=5),
            mysppaus=Mock(return_value=1),
            myspsr=Mock(return_value=120.0),
            myspatc=Mock(return_value=150.0),
        ),
    )
    monkeypatch.setattr(
        "chatterpal.utils.preferences._preferences_manager",
        UserPreferences(str(tmp_path / "preferences")),
    )

    runtime.settings = Settings(
        _env_file=None,
        asr_provider="whisper",
        tts_provider="edge",
        llm_provider="alibaba",
        whisper_model="base",
        audio_sample_rate=sample_rate,
        dashscope_api_key="offline-test-key",
        audio_temp_dir=str(tmp_path / "audio"),
        cache_dir=str(tmp_path / "cache"),
        model_cache_dir=str(tmp_path / "models"),
    )
    runtime.asr = WhisperASR({"model_size": "base", "language": "en"})
    runtime.tts = EdgeTTS()
    runtime.llm = AlibabaBailianLLM({"api_key": "offline-test-key"})
    return runtime


class TestCoreModulesIntegration:
    """Verify inherited helpers using minimal concrete implementations."""

    def test_asr_base_functionality(self, offline_runtime):
        class TestASR(ASRBase):
            def recognize(self, audio_data, **kwargs):
                return "测试识别结果"

            def recognize_file(self, audio_path, **kwargs):
                return "文件识别结果"

        asr = TestASR()
        assert asr.recognize(offline_runtime.audio_bytes) == "测试识别结果"
        assert asr.test_connection() is True
        assert "wav" in asr.get_supported_formats()
        assert asr.recognize_gradio_audio(offline_runtime.audio_path) == "文件识别结果"

    def test_tts_base_functionality(self):
        class TestTTS(TTSBase):
            def synthesize(self, text, **kwargs):
                return f"合成音频: {text}".encode()

            def synthesize_to_file(self, text, output_path, **kwargs):
                Path(output_path).write_bytes(self.synthesize(text))
                return True

        tts = TestTTS()
        assert b"Hello world" in tts.synthesize("Hello world")
        assert tts.validate_text("Hello world") is True
        assert tts.validate_text("") is False
        cleaned = tts.clean_text_for_tts("Hello @#$% world!!!")
        assert "Hello" in cleaned
        assert "world" in cleaned

    def test_llm_base_functionality(self):
        class TestLLM(LLMBase):
            def chat(self, messages, **kwargs):
                normalized = self.normalize_messages(messages)
                if normalized:
                    return f"回复: {normalized[-1]['content']}"
                return "默认回复"

        llm = TestLLM()
        assert llm.chat("你好") == "回复: 你好"
        conv = llm.create_conversation("你是助手")
        conv.add_user_message("你好")
        assert "回复:" in llm.chat(conv)
        normalized = llm.normalize_messages("测试消息")
        assert len(normalized) == 1
        assert normalized[0]["role"] == "user"
        assert normalized[0]["content"] == "测试消息"

    def test_assessment_base_functionality(self, offline_runtime):
        from chatterpal.core.assessment.base import ProsodyFeatures

        class TestAssessment(AssessmentBase):
            def assess(self, audio_data, target_text="", **kwargs):
                return AssessmentResult(
                    overall_score=0.8,
                    fluency_score=0.7,
                    pronunciation_score=0.9,
                    prosody_score=0.8,
                    accuracy_score=0.85,
                    prosody_features=ProsodyFeatures(),
                    recognized_text="测试识别文本",
                    target_text=target_text,
                    feedback="测试反馈",
                )

        assessment = TestAssessment()
        assert assessment.validate_audio_data(offline_runtime.audio_bytes) is True
        assert assessment.validate_audio_data("") is False
        assert assessment.validate_audio_data(None) is False
        assert assessment.detect_language("Hello world") == "en"
        assert assessment.detect_language("你好世界") == "zh"
        assert assessment.calculate_text_similarity("hello", "hello") == 1.0


class TestServiceLayerIntegration:
    """Exercise real services and adapters together."""

    @pytest.fixture(autouse=True)
    def setup_services(self, offline_runtime):
        self.runtime = offline_runtime
        self.chat_service = ChatService(
            asr=offline_runtime.asr, tts=offline_runtime.tts, llm=offline_runtime.llm
        )
        self.evaluation_service = EvaluationService(
            asr=offline_runtime.asr, llm=offline_runtime.llm
        )
        self.correction_service = CorrectionService(asr=offline_runtime.asr)

    def test_chat_service_functionality(self):
        response_text, session_id = self.chat_service.chat_with_text("Hello")
        assert response_text == "I'm fine, thank you!"
        assert session_id is not None
        history = self.chat_service.get_conversation_history(session_id)
        assert len(history) >= 2
        assert history[-2:] == [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": response_text},
        ]
        assert self.chat_service.clear_conversation_history(session_id) is True
        history = self.chat_service.get_conversation_history(session_id)
        assert len(history) <= 1
        assert all(message["role"] == "system" for message in history)

    def test_evaluation_service_functionality(self):
        target_text = "Hello, how are you"
        result = self.evaluation_service.evaluate_pronunciation(
            self.runtime.audio_bytes, target_text
        )
        assert isinstance(result, AssessmentResult)
        assert result.overall_score >= 0
        assert result.recognized_text == target_text
        assert result.target_text == target_text
        assert self.runtime.completion.call_count == 1

    def test_correction_service_functionality(self):
        target_text = "Hello, how are you"
        result = self.correction_service.comprehensive_correction(
            self.runtime.audio_bytes, target_text
        )
        assert isinstance(result, CorrectionReport)
        assert result.overall_score >= 0
        assert result.detailed_feedback
        quick_result = self.correction_service.quick_correction(
            self.runtime.audio_bytes, target_text
        )
        assert isinstance(quick_result, dict)
        assert "overall_score" in quick_result
        assert "main_issues" in quick_result
        assert "key_suggestions" in quick_result
        assert quick_result["overall_score"] == result.overall_score
        assert quick_result["key_suggestions"] != ["请检查音频质量后重试"]

    def test_cross_service_integration(self):
        response_text, session_id = self.chat_service.chat_with_text(
            "I want to practice pronunciation"
        )
        assert session_id is not None
        assert response_text == self.runtime.reply
        user_audio = self.runtime.audio_bytes
        eval_result = self.evaluation_service.evaluate_pronunciation(
            user_audio, "I want to practice pronunciation"
        )
        assert isinstance(eval_result, AssessmentResult)
        correction_result = self.correction_service.comprehensive_correction(
            user_audio, "I want to practice pronunciation"
        )
        assert isinstance(correction_result, CorrectionReport)
        feedback_text = f"你的发音得分{eval_result.overall_score:.1f}"
        feedback_response, returned_session = self.chat_service.chat_with_text(
            feedback_text, session_id
        )
        assert feedback_response == "I'm fine, thank you!"
        assert returned_session == session_id


class TestWebComponentsBasic:
    """Build the actual Gradio components and verify their event bindings."""

    def test_chat_tab_creation(self, offline_runtime):
        import gradio as gr
        from chatterpal.web.components.chat_tab import ChatTab

        service = ChatService(asr=offline_runtime.asr, llm=offline_runtime.llm)
        chat_tab = ChatTab(service)
        assert chat_tab.chat_service is service
        with gr.Blocks(analytics_enabled=False) as interface:
            tab = chat_tab.create_interface()
        assert isinstance(tab, gr.Tab)
        assert any(event.fn == chat_tab._handle_chat for event in interface.fns.values())

    def test_score_tab_creation(self, offline_runtime):
        import gradio as gr
        from chatterpal.web.components.score_tab import ScoreTab

        service = EvaluationService(asr=offline_runtime.asr, llm=offline_runtime.llm)
        score_tab = ScoreTab(service)
        assert score_tab.evaluation_service is service
        with gr.Blocks(analytics_enabled=False) as interface:
            tab = score_tab.create_interface()
        assert isinstance(tab, gr.Tab)
        assert any(
            event.fn == score_tab._evaluate_pronunciation
            for event in interface.fns.values()
        )

    def test_correct_tab_creation(self, offline_runtime):
        import gradio as gr
        from chatterpal.web.components.correct_tab import CorrectTab

        service = CorrectionService(asr=offline_runtime.asr)
        correct_tab = CorrectTab(service)
        assert correct_tab.correction_service is service
        with gr.Blocks(analytics_enabled=False) as interface:
            tab = correct_tab.create_interface()
        assert isinstance(tab, gr.Tab)
        assert any(
            event.fn == correct_tab._assess_pronunciation
            for event in interface.fns.values()
        )


class TestEndToEndWorkflow:
    """Keep data flowing through conversation, evaluation and correction."""

    def test_complete_pronunciation_practice_workflow(self, offline_runtime):
        runtime = offline_runtime
        runtime.recognized_text = "Hello, how are you today"
        runtime.reply = "Great! Let's practice pronunciation together."
        chat_service = ChatService(asr=runtime.asr, tts=runtime.tts, llm=runtime.llm)
        evaluation_service = EvaluationService(asr=runtime.asr, llm=runtime.llm)
        correction_service = CorrectionService(asr=runtime.asr)

        response_text, session_id = chat_service.chat_with_text(
            "I want to practice English pronunciation"
        )
        assert response_text == "Great! Let's practice pronunciation together."
        assert session_id is not None
        practice_sentence = "Hello, how are you today"
        eval_result = evaluation_service.evaluate_pronunciation(
            runtime.audio_bytes, practice_sentence
        )
        assert isinstance(eval_result, AssessmentResult)
        assert eval_result.recognized_text == "Hello, how are you today"
        assert eval_result.target_text == practice_sentence
        correction_result = correction_service.comprehensive_correction(
            runtime.audio_bytes, practice_sentence
        )
        assert isinstance(correction_result, CorrectionReport)
        feedback_text = f"我的发音得分{eval_result.overall_score:.1f}，请给我一些建议"
        final_response, returned_session = chat_service.chat_with_text(
            feedback_text, session_id
        )
        assert final_response == "Great! Let's practice pronunciation together."
        assert returned_session == session_id
        history = chat_service.get_conversation_history(session_id)
        assert len(history) >= 4
        assert history[-2]["content"] == feedback_text

    def test_audio_processing_pipeline(self, offline_runtime):
        runtime = offline_runtime
        runtime.recognized_text = "Test audio recognition"
        result1 = runtime.asr.recognize(runtime.audio_bytes)
        result2 = runtime.asr.recognize_gradio_audio(
            (runtime.sample_rate, runtime.audio_array)
        )
        result3 = runtime.asr.recognize_file(runtime.audio_path)
        assert result1 == "Test audio recognition"
        assert result2 == "Test audio recognition"
        assert result3 == "Test audio recognition"
        assert runtime.whisper.call_count == 3
        assert Path(runtime.audio_path).exists()

    def test_error_handling_robustness(self, offline_runtime):
        from chatterpal.core.errors import SpeechRecognitionError
        from chatterpal.core.llm.base import LLMError

        runtime = offline_runtime
        chat_service = ChatService(asr=runtime.asr, tts=runtime.tts, llm=runtime.llm)
        runtime.whisper.side_effect = OSError("ASR服务不可用")
        with pytest.raises(SpeechRecognitionError):
            chat_service.chat_with_audio(runtime.audio_path, max_retries=1)
        assert runtime.whisper.call_count == 1

        runtime.completion.side_effect = OSError("LLM服务不可用")
        with pytest.raises(LLMError, match="LLM服务不可用"):
            chat_service.chat_with_text("Hello")

        # ChatService explicitly retains the text response when TTS fails.
        runtime.whisper.side_effect = lambda *args, **kwargs: runtime.recognized_text
        runtime.completion.side_effect = None
        runtime.completion.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=runtime.reply))],
            usage=None,
        )
        runtime.save.side_effect = OSError("TTS服务不可用")
        response, audio, session_id = chat_service.chat_with_audio(
            runtime.audio_path, max_retries=1
        )
        assert response == runtime.reply
        assert audio is None
        assert session_id is not None
        assert runtime.save.await_count == 2

    def test_configuration_integration(self, offline_runtime):
        settings = offline_runtime.settings
        assert settings.audio_sample_rate > 0
        assert settings.whisper_model in [
            "tiny", "base", "small", "medium", "large", "large-v2", "large-v3"
        ]
        assert settings.asr_provider in ["whisper", "alibaba"]
        assert settings.tts_provider in ["edge", "alibaba"]
        assert settings.llm_provider in ["alibaba", "openai"]
        assert settings.get_audio_temp_path() == Path(settings.audio_temp_dir)
        assert settings.get_cache_path() == Path(settings.cache_dir)
        assert settings.get_model_cache_path() == Path(settings.model_cache_dir)


class TestDataFlowIntegrity:
    """Verify message, assessment and status payloads."""

    def test_message_flow_integrity(self):
        conv = Conversation("你是英语老师")
        conv.add_user_message("Hello")
        conv.add_assistant_message("Hi there!")
        conv.add_user_message("How are you")
        messages = conv.get_messages()
        assert len(messages) == 4
        for msg in messages:
            assert "role" in msg
            assert "content" in msg
            assert msg["role"] in ["system", "user", "assistant"]
        assert [msg["content"] for msg in messages] == [
            "你是英语老师", "Hello", "Hi there!", "How are you"
        ]

    def test_assessment_result_integrity(self):
        from chatterpal.core.assessment.base import (
            PhonemeAnalysis, ProsodyFeatures, WordAnalysis,
        )

        result = AssessmentResult(
            overall_score=0.85,
            fluency_score=0.8,
            pronunciation_score=0.9,
            prosody_score=0.8,
            accuracy_score=0.85,
            prosody_features=ProsodyFeatures(speaking_rate=120.0, f0_mean=150.0),
            word_analysis=[WordAnalysis("hello", "hello", True, confidence_score=0.9)],
            phoneme_analysis=[PhonemeAnalysis("h", "correct", "发音正确")],
            recognized_text="Hello world",
            target_text="Hello world",
            feedback="发音很好",
        )
        result_dict = result.to_dict()
        assert "overall_score" in result_dict
        assert "detailed_scores" in result_dict
        assert "prosody_features" in result_dict
        assert "word_analysis" in result_dict
        assert "phoneme_analysis" in result_dict
        assert len(result_dict["word_analysis"]) == 1
        assert len(result_dict["phoneme_analysis"]) == 1
        assert result_dict["word_analysis"][0]["confidence_score"] == 0.9
        assert result_dict["phoneme_analysis"][0]["phoneme"] == "h"
        assert result_dict["recognized_text"] == "Hello world"

    def test_service_status_reporting(self, offline_runtime):
        runtime = offline_runtime
        chat_service = ChatService(asr=runtime.asr, tts=runtime.tts, llm=runtime.llm)
        chat_service.create_session()
        status = chat_service.get_service_status()
        assert isinstance(status, dict)
        assert status["asr_available"] is True
        assert status["tts_available"] is True
        assert status["llm_available"] is True
        assert status["active_sessions"] == 1
        assert status["asr_status"] is True
        assert status["tts_status"] is True
        assert status["llm_status"] is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
