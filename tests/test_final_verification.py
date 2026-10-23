"""Final verification of application services with offline SDK responses."""

from pathlib import Path

import pytest

from tests.test_functionality_verification import offline_runtime


def test_all_modules_import_successfully():
    """Core, service, web and configuration classes must all import."""
    from chatterpal.core.asr.base import ASRBase
    from chatterpal.core.tts.base import TTSBase
    from chatterpal.core.llm.base import LLMBase
    from chatterpal.core.assessment.base import AssessmentBase
    from chatterpal.services.chat import ChatService
    from chatterpal.services.evaluation import EvaluationService
    from chatterpal.services.correction import CorrectionService
    from chatterpal.web.components.chat_tab import ChatTab
    from chatterpal.web.components.score_tab import ScoreTab
    from chatterpal.web.components.correct_tab import CorrectTab
    from chatterpal.config.settings import Settings

    for component in (
        ASRBase, TTSBase, LLMBase, AssessmentBase, ChatService,
        EvaluationService, CorrectionService, ChatTab, ScoreTab, CorrectTab, Settings,
    ):
        assert isinstance(component, type)


def test_chat_functionality(offline_runtime):
    """Text and audio turns use the same session and retain synthesized audio."""
    from chatterpal.services.chat import ChatService

    runtime = offline_runtime
    runtime.recognized_text = "Hello world"
    runtime.reply = "Hi there! How can I help you"
    chat_service = ChatService(asr=runtime.asr, tts=runtime.tts, llm=runtime.llm)
    response_text, session_id = chat_service.chat_with_text("Hello")
    assert response_text == "Hi there! How can I help you"
    assert session_id is not None
    response_text, response_audio, session_id2 = chat_service.chat_with_audio(
        runtime.audio_path, session_id
    )
    assert response_text == "Hi there! How can I help you"
    assert response_audio == runtime.audio_bytes
    assert session_id2 == session_id
    assert runtime.whisper.call_count == 1
    runtime.communicate.assert_called_once()
    assert chat_service.get_conversation_history(session_id)[-2]["content"] == "Hello world"


def test_evaluation_functionality(offline_runtime):
    """Evaluation retains the recognized text and structured result."""
    from chatterpal.services.evaluation import EvaluationService
    from chatterpal.core.assessment.base import AssessmentResult

    runtime = offline_runtime
    runtime.recognized_text = "Hello world"
    runtime.reply = "Good pronunciation!"
    service = EvaluationService(asr=runtime.asr, llm=runtime.llm)
    result = service.evaluate_pronunciation(runtime.audio_bytes, "Hello world")
    assert isinstance(result, AssessmentResult)
    assert result.overall_score >= 0
    assert result.recognized_text == "Hello world"
    assert result.feedback == "Good pronunciation!"


def test_correction_functionality(offline_runtime):
    """Quick correction returns the documented result keys on success."""
    from chatterpal.services.correction import CorrectionService

    runtime = offline_runtime
    runtime.recognized_text = "Hello world"
    result = CorrectionService(asr=runtime.asr).quick_correction(
        runtime.audio_bytes, "Hello world"
    )
    assert isinstance(result, dict)
    assert "overall_score" in result
    assert "main_issues" in result
    assert "key_suggestions" in result
    assert result["overall_score"] > 0
    assert result["key_suggestions"] != ["请检查音频质量后重试"]


def test_web_components_functionality(offline_runtime):
    """Tabs preserve their service wiring."""
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
    assert ChatTab(chat_service).chat_service is chat_service
    assert ScoreTab(eval_service).evaluation_service is eval_service
    assert CorrectTab(correction_service).correction_service is correction_service


def test_configuration_system(offline_runtime):
    """Validate settings against the current production provider names."""
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
    assert settings.asr_provider in ["whisper", "alibaba"]
    assert settings.tts_provider in ["edge", "alibaba"]
    assert settings.llm_provider in ["alibaba", "openai"]


def test_end_to_end_workflow(offline_runtime):
    """Complete a practice cycle and continue the existing conversation."""
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
    assert eval_result.recognized_text == "Hello, how are you"
    correction_result = correction_service.quick_correction(
        runtime.audio_bytes, "Hello, how are you"
    )
    assert isinstance(correction_result, dict)
    assert "overall_score" in correction_result
    assert correction_result["overall_score"] > 0
    feedback_text = f"My score is {eval_result.overall_score:.2f}"
    final_response, final_session = chat_service.chat_with_text(feedback_text, session_id)
    assert final_response == "I'm fine, thank you!"
    assert final_session == session_id
    assert chat_service.get_conversation_history(session_id)[-2]["content"] == feedback_text


def test_project_structure():
    """Resolve required paths relative to the repository, independent of cwd."""
    project_root = Path(__file__).resolve().parents[1]
    required_paths = [
        "src/chatterpal",
        "src/chatterpal/core",
        "src/chatterpal/services",
        "src/chatterpal/web",
        "src/chatterpal/config",
        "src/chatterpal/utils",
        "tests",
        "docs",
        "scripts",
        "pyproject.toml",
        "README.md",
    ]
    for path in required_paths:
        assert (project_root / path).exists(), f"缺少必要的路径: {path}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-s"]))
