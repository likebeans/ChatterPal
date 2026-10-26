"""Check real core helpers and services while replacing external SDK calls."""

from pathlib import Path

import pytest

from tests.test_functionality_verification import offline_runtime


def test_app_can_be_imported():
    """The application factory must be importable without a model or API call."""
    from chatterpal.web.app import create_app

    assert create_app is not None
    assert callable(create_app)


def test_services_can_be_imported():
    """Service classes must be importable."""
    from chatterpal.services.chat import ChatService
    from chatterpal.services.evaluation import EvaluationService
    from chatterpal.services.correction import CorrectionService

    assert ChatService is not None
    assert EvaluationService is not None
    assert CorrectionService is not None


def test_core_modules_can_be_imported():
    """Core interfaces must be importable."""
    from chatterpal.core.asr.base import ASRBase
    from chatterpal.core.tts.base import TTSBase
    from chatterpal.core.llm.base import LLMBase
    from chatterpal.core.assessment.base import AssessmentBase

    assert ASRBase is not None
    assert TTSBase is not None
    assert LLMBase is not None
    assert AssessmentBase is not None


def test_web_components_can_be_imported():
    """All three Gradio tab classes must be importable."""
    from chatterpal.web.components.chat_tab import ChatTab
    from chatterpal.web.components.score_tab import ScoreTab
    from chatterpal.web.components.correct_tab import CorrectTab

    assert ChatTab is not None
    assert ScoreTab is not None
    assert CorrectTab is not None


def test_configuration_system(offline_runtime):
    """Verify core configuration fields and accepted Whisper models."""
    settings = offline_runtime.settings
    assert hasattr(settings, "audio_sample_rate")
    assert hasattr(settings, "whisper_model")
    assert hasattr(settings, "asr_provider")
    assert hasattr(settings, "tts_provider")
    assert hasattr(settings, "llm_provider")
    assert settings.audio_sample_rate > 0
    assert settings.whisper_model in [
        "tiny", "base", "small", "medium", "large", "large-v2", "large-v3"
    ]


def test_asr_base_functionality(offline_runtime):
    """Check recognition, connection reporting and supported formats."""
    from chatterpal.core.asr.base import ASRBase

    class TestASR(ASRBase):
        def recognize(self, audio_data, **kwargs):
            return "测试识别结果"

        def recognize_file(self, audio_path, **kwargs):
            return "文件识别结果"

    asr = TestASR()
    assert asr.recognize(offline_runtime.audio_bytes) == "测试识别结果"
    assert asr.test_connection() is True
    formats = asr.get_supported_formats()
    assert isinstance(formats, list)
    assert "wav" in formats


def test_tts_base_functionality():
    """Check the inherited text validator through a concrete adapter."""
    from chatterpal.core.tts.base import TTSBase

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


def test_llm_base_functionality():
    """Plain text and Conversation inputs use the same normalization helper."""
    from chatterpal.core.llm.base import LLMBase

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


def test_assessment_base_functionality(offline_runtime):
    """Check audio validation and language detection."""
    from chatterpal.core.assessment.base import (
        AssessmentBase, AssessmentResult, ProsodyFeatures,
    )

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


def test_chat_service_basic(offline_runtime):
    """A text turn must be recorded in the returned session."""
    from chatterpal.services.chat import ChatService

    runtime = offline_runtime
    runtime.reply = "Hi there!"
    chat_service = ChatService(asr=runtime.asr, tts=runtime.tts, llm=runtime.llm)
    response_text, session_id = chat_service.chat_with_text("Hello")
    assert response_text == "Hi there!"
    assert session_id is not None
    history = chat_service.get_conversation_history(session_id)
    assert isinstance(history, list)
    assert history[-2:] == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]


def test_evaluation_service_basic(offline_runtime):
    """Evaluation retains the transcript and provider feedback."""
    from chatterpal.services.evaluation import EvaluationService
    from chatterpal.core.assessment.base import AssessmentResult

    runtime = offline_runtime
    runtime.recognized_text = "Hello world"
    runtime.reply = "Good pronunciation!"
    service = EvaluationService(asr=runtime.asr, llm=runtime.llm)
    result = service.evaluate_pronunciation(runtime.audio_bytes, "Hello world")
    assert isinstance(result, AssessmentResult)
    assert result.overall_score is None
    assert result.dimensions['overall']['status'] == 'unavailable'
    assert result.recognized_text == "Hello world"
    assert result.dimensions['ai_feedback']['status'] == 'unavailable'
    assert '未校准' in result.feedback


def test_correction_service_basic(offline_runtime):
    """Quick correction must succeed instead of returning its error fallback."""
    from chatterpal.services.correction import CorrectionService

    runtime = offline_runtime
    runtime.recognized_text = "Hello world"
    result = CorrectionService(asr=runtime.asr).quick_correction(
        runtime.audio_bytes, "Hello world"
    )
    assert isinstance(result, dict)
    assert "overall_score" in result
    assert result['overall_score'] is None
    assert result['dimensions']['overall']['status'] == 'unavailable'
    assert result["key_suggestions"] != ["请检查音频质量后重试"]


def test_web_components_basic(offline_runtime):
    """Tabs retain the actual service instances passed to their constructors."""
    from chatterpal.web.components.chat_tab import ChatTab
    from chatterpal.web.components.score_tab import ScoreTab
    from chatterpal.web.components.correct_tab import CorrectTab
    from chatterpal.services.chat import ChatService
    from chatterpal.services.evaluation import EvaluationService
    from chatterpal.services.correction import CorrectionService

    runtime = offline_runtime
    chat_service = ChatService(asr=runtime.asr, tts=runtime.tts, llm=runtime.llm)
    eval_service = EvaluationService(asr=runtime.asr, llm=runtime.llm)
    correction_service = CorrectionService(asr=runtime.asr)
    chat_tab = ChatTab(chat_service)
    score_tab = ScoreTab(eval_service)
    correct_tab = CorrectTab(correction_service)
    assert chat_tab.chat_service is chat_service
    assert score_tab.evaluation_service is eval_service
    assert correct_tab.correction_service is correction_service


def test_end_to_end_workflow(offline_runtime):
    """Follow a conversation into evaluation and quick correction."""
    from chatterpal.services.chat import ChatService
    from chatterpal.services.evaluation import EvaluationService
    from chatterpal.services.correction import CorrectionService
    from chatterpal.core.assessment.base import AssessmentResult

    runtime = offline_runtime
    chat_service = ChatService(asr=runtime.asr, tts=runtime.tts, llm=runtime.llm)
    eval_service = EvaluationService(asr=runtime.asr, llm=runtime.llm)
    correction_service = CorrectionService(asr=runtime.asr)
    response_text, session_id = chat_service.chat_with_text("Hello")
    assert response_text == "I'm fine, thank you!"
    assert session_id is not None
    eval_result = eval_service.evaluate_pronunciation(
        runtime.audio_bytes, "Hello, how are you"
    )
    assert isinstance(eval_result, AssessmentResult)
    correction_result = correction_service.quick_correction(
        runtime.audio_bytes, "Hello, how are you"
    )
    assert isinstance(correction_result, dict)
    assert correction_result['overall_score'] is None
    assert correction_result['dimensions']['accuracy']['score'] == 100


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-s"]))
