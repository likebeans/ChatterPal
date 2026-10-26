"""Core functionality checks using real services and offline SDK responses."""

from pathlib import Path

import pytest

from tests.test_functionality_verification import offline_runtime


def test_core_modules_import():
    """Core classes must import without initializing external runtimes."""
    from chatterpal.core.asr.base import ASRBase
    from chatterpal.core.tts.base import TTSBase
    from chatterpal.core.llm.base import LLMBase
    from chatterpal.core.assessment.base import AssessmentBase
    from chatterpal.services.chat import ChatService
    from chatterpal.services.evaluation import EvaluationService
    from chatterpal.services.correction import CorrectionService
    from chatterpal.config.settings import Settings

    for component in (
        ASRBase, TTSBase, LLMBase, AssessmentBase, ChatService,
        EvaluationService, CorrectionService, Settings,
    ):
        assert isinstance(component, type)


def test_app_can_be_imported(offline_runtime):
    """Create the real application with explicit settings and SDK boundaries."""
    from chatterpal.web.app import ChatterPalApp, create_app
    from chatterpal.services.chat import ChatService
    from chatterpal.services.evaluation import EvaluationService
    from chatterpal.services.correction import CorrectionService

    app = create_app(offline_runtime.settings)
    assert app is not None
    assert isinstance(app, ChatterPalApp)
    assert isinstance(app.chat_service, ChatService)
    assert isinstance(app.evaluation_service, EvaluationService)
    assert isinstance(app.correction_service, CorrectionService)
    assert app.chat_service.asr is app.asr
    assert app.chat_service.tts is app.tts
    assert app.chat_service.llm is app.llm


def test_chat_service_basic(offline_runtime):
    """A text turn must return the provider reply and create a session."""
    from chatterpal.services.chat import ChatService

    runtime = offline_runtime
    runtime.reply = "Hi there!"
    service = ChatService(asr=runtime.asr, tts=runtime.tts, llm=runtime.llm)
    response_text, session_id = service.chat_with_text("Hello")
    assert response_text == "Hi there!"
    assert session_id is not None
    assert runtime.completion.call_args.kwargs["messages"][-1] == {
        "role": "user", "content": "Hello"
    }


def test_evaluation_service_basic(offline_runtime):
    """Evaluation must retain the ASR transcript in its result."""
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
    """Quick correction must complete the success path with valid audio."""
    from chatterpal.services.correction import CorrectionService

    runtime = offline_runtime
    runtime.recognized_text = "Hello world"
    service = CorrectionService(asr=runtime.asr)
    result = service.quick_correction(runtime.audio_bytes, "Hello world")
    assert isinstance(result, dict)
    assert "overall_score" in result
    assert result['overall_score'] is None
    assert result['dimensions']['overall']['status'] == 'unavailable'
    assert result["key_suggestions"] != ["请检查音频质量后重试"]


def test_configuration_system(offline_runtime):
    """Configuration values match the Settings validators."""
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
    """Exercise the base recognition contract with a concrete test adapter."""
    from chatterpal.core.asr.base import ASRBase

    class TestASR(ASRBase):
        def recognize(self, audio_data, **kwargs):
            return "测试识别结果"

        def recognize_file(self, audio_path, **kwargs):
            return "文件识别结果"

    asr = TestASR({})
    assert asr.recognize(offline_runtime.audio_bytes) == "测试识别结果"
    assert asr.test_connection() is True


def test_tts_base_functionality():
    """The base text validator rejects empty text."""
    from chatterpal.core.tts.base import TTSBase

    class TestTTS(TTSBase):
        def synthesize(self, text, **kwargs):
            return f"合成音频: {text}".encode()

        def synthesize_to_file(self, text, output_path, **kwargs):
            Path(output_path).write_bytes(self.synthesize(text))
            return True

    tts = TestTTS({})
    assert b"Hello world" in tts.synthesize("Hello world")
    assert tts.validate_text("Hello world") is True
    assert tts.validate_text("") is False


def test_llm_base_functionality():
    """The base normalizer converts plain input to a user message."""
    from chatterpal.core.llm.base import LLMBase

    class TestLLM(LLMBase):
        def chat(self, messages, **kwargs):
            normalized = self.normalize_messages(messages)
            if normalized:
                return f"回复: {normalized[-1]['content']}"
            return "默认回复"

    llm = TestLLM({})
    assert llm.chat("你好") == "回复: 你好"


def test_end_to_end_workflow(offline_runtime):
    """A conversation can lead into evaluation and correction."""
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
