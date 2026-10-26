"""Evaluation orchestration through real audio and controlled provider outcomes."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from chatterpal.core.asr.base import ASRError
from chatterpal.core.assessment.base import AssessmentError
from chatterpal.core.assessment.corrector import PronunciationCorrector
from chatterpal.services.evaluation import EvaluationService


@pytest.fixture
def spoken_audio():
    rate = 16000
    times = np.arange(rate) / rate
    return rate, (0.3 * np.sin(2 * np.pi * 180 * times)).astype(np.float32)


def recognition_provider(text="we practice clearly"):
    return SimpleNamespace(recognize_gradio_audio=Mock(return_value=text))


def test_basic_evaluation_keeps_real_assessment_when_llm_is_disabled(spoken_audio):
    provider = recognition_provider()
    llm = SimpleNamespace(chat=Mock(side_effect=AssertionError("must not request analysis")))
    result = EvaluationService(
        asr=provider, llm=llm, assessment=PronunciationCorrector()
    ).evaluate_pronunciation(spoken_audio, "we practice clearly", detailed_analysis=False)
    assert result.recognized_text == "we practice clearly"
    assert result.audio_duration == pytest.approx(1.0)
    assert result.pronunciation_score is None
    assert result.prosody_score is None
    assert result.dimensions['accuracy']['score'] == 100
    assert result.feature_metadata['f0_mean']['source'] == 'parselmouth'
    assert "尚未校准" in result.feedback
    assert result.prosody_features.f0_mean == pytest.approx(180.0, abs=1.0)
    provider.recognize_gradio_audio.assert_called_once_with(spoken_audio)
    llm.chat.assert_not_called()


def test_provider_timeout_keeps_base_assessment_and_transcript(spoken_audio):
    llm = SimpleNamespace(chat=Mock(side_effect=TimeoutError("controlled timeout")))
    result = EvaluationService(
        asr=recognition_provider(), llm=llm, assessment=PronunciationCorrector()
    ).evaluate_pronunciation(spoken_audio, "we practice clearly")
    assert result.recognized_text == "we practice clearly"
    assert result.fluency_score is None
    assert result.dimensions['ai_feedback']['status'] == 'unavailable'
    assert "尚未校准" in result.feedback
    assert result.suggestions
    llm.chat.assert_called_once()
    assert 'Target Text: "we practice clearly"' in llm.chat.call_args.args[0]


def test_assessment_failure_does_not_discard_available_llm_response(spoken_audio):
    llm_result = {
        "pronunciation_score": 83, "fluency_score": 76, "accuracy_score": 90,
        "feedback": "Controlled provider feedback", "suggestions": ["Practice the opening"],
    }
    llm = SimpleNamespace(chat=Mock(return_value=json.dumps(llm_result)))
    unavailable = SimpleNamespace(assess=Mock(side_effect=AssessmentError("unavailable")))
    result = EvaluationService(
        asr=recognition_provider(), llm=llm, assessment=unavailable
    ).evaluate_pronunciation(spoken_audio, "we practice")
    assert result.pronunciation_score is None
    assert result.fluency_score is None
    assert result.accuracy_score == pytest.approx(50.0)
    assert result.dimensions['accuracy']['source'] == 'text_sequence_alignment'
    assert result.dimensions['ai_feedback']['status'] == 'estimated'
    assert llm_result["feedback"] in result.feedback
    assert result.suggestions == llm_result["suggestions"]
    assert [word.is_correct for word in result.word_analysis] == [True, True, False]
    assert result.word_analysis[-1].target_word == ''
    assert result.word_analysis[-1].recognized_word == 'clearly'
    assert all(word.confidence_score is None for word in result.word_analysis)
    unavailable.assess.assert_called_once_with(spoken_audio, "we practice", recognized_text='we practice clearly')


def test_non_json_feedback_keeps_base_feedback_and_unavailable_ai_status(spoken_audio):
    response = "Overall: 82. Try reading slowly. Practice the final word!"
    llm = SimpleNamespace(chat=Mock(return_value=response))
    result = EvaluationService(
        asr=recognition_provider(), llm=llm
    ).evaluate_free_speech(spoken_audio)
    assert result.recognized_text == "we practice clearly"
    assert result.target_text == ""
    assert response not in result.feedback
    assert result.dimensions['ai_feedback']['status'] == 'unavailable'
    assert result.overall_score is result.accuracy_score is None
    assert result.suggestions
    prompt = llm.chat.call_args.args[0]
    assert 'Recognized Text: "we practice clearly"' in prompt
    assert 'You have not heard the recording' in prompt


@pytest.mark.parametrize("outcome", ["", RuntimeError("recognizer unavailable")])
def test_failed_recognition_stops_before_llm_analysis(spoken_audio, outcome):
    provider = recognition_provider(outcome if isinstance(outcome, str) else "")
    if isinstance(outcome, Exception):
        provider.recognize_gradio_audio.side_effect = outcome
    llm = SimpleNamespace(chat=Mock())
    expected = ASRError if isinstance(outcome, Exception) else AssessmentError
    with pytest.raises(expected):
        EvaluationService(asr=provider, llm=llm).evaluate_pronunciation(spoken_audio)
    llm.chat.assert_not_called()
    provider.recognize_gradio_audio.assert_called_once()


def test_missing_recognition_module_is_a_service_error(spoken_audio):
    with pytest.raises(ASRError, match="ASR模块未初始化"):
        EvaluationService().evaluate_pronunciation(spoken_audio)


def test_default_feedback_identifies_substitution_and_omission(spoken_audio):
    result = EvaluationService(asr=recognition_provider("we learn")).evaluate_pronunciation(
        spoken_audio, "we practice clearly", detailed_analysis=False
    )
    assert result.recognized_text == "we learn"
    words = result.word_analysis
    assert [word.target_word for word in words] == ["we", "practice", "clearly"]
    assert [word.recognized_word for word in words] == ["we", "", "learn"]
    assert [word.is_correct for word in words] == [True, False, False]
    assert '识别文本差异' in words[1].correction_tips[0]
    assert 'learn' in words[2].correction_tips[0]
    assert all(word.confidence_score is None for word in words)
    assert "准确性" in result.feedback
    assert "流畅度" in result.feedback
    assert result.suggestions


@pytest.mark.parametrize("second_score,better,change", [(90, 2, "improvements"), (40, 1, "regressions")])
def test_comparison_reports_direction_and_serialized_results(
    spoken_audio, second_score, better, change
):
    responses = [json.dumps({
        "pronunciation_score": score, "fluency_score": score,
        "feedback": f"Analysis {score}",
    }) for score in (70, second_score)]
    llm = SimpleNamespace(chat=Mock(side_effect=responses))
    comparison = EvaluationService(asr=recognition_provider(), llm=llm).compare_pronunciations(
        spoken_audio, spoken_audio, "we practice clearly"
    )
    assert comparison["comparison"]["better_audio"] is None
    assert comparison["comparison"]["score_difference"] is None
    assert comparison["comparison"]["status"] == 'unavailable'
    assert comparison["comparison"][change] == []
    assert 'Analysis 70' in comparison["audio1_result"]["feedback"]
    assert f'Analysis {second_score}' in comparison["audio2_result"]["feedback"]
    assert llm.chat.call_count == 2


def test_comparison_recognition_failure_is_reported_as_assessment_error(spoken_audio):
    with pytest.raises(AssessmentError, match="发音比较失败"):
        EvaluationService().compare_pronunciations(spoken_audio, spoken_audio)


def test_service_status_distinguishes_missing_and_failed_modules():
    asr = SimpleNamespace(test_connection=Mock(side_effect=RuntimeError("offline")))
    llm = SimpleNamespace(test_connection=Mock(return_value=True))
    assessment = SimpleNamespace(test_functionality=Mock(side_effect=RuntimeError("unavailable")))
    status = EvaluationService(asr=asr, llm=llm, assessment=assessment).get_service_status()
    assert status["asr_available"] and status["llm_available"] and status["assessment_available"]
    assert status["asr_status"] is False
    assert status["llm_status"] is True
    assert status["assessment_status"] is False
    asr.test_connection.assert_called_once()
    llm.test_connection.assert_called_once()
    assessment.test_functionality.assert_called_once()
    missing = EvaluationService().get_service_status()
    assert missing["asr_available"] is False
    assert missing["llm_available"] is False
    assert missing["assessment_available"] is False
    assert "asr_status" not in missing


@pytest.mark.parametrize('payload', [
    'Overall 99. Your pronunciation is excellent.',
    '[]',
    '{"feedback": "Advice", "feedback": "Other"}',
    '{"feedback": "Advice", "overall_score": NaN}',
    '{"feedback": "Advice", "overall_score": 1e999}',
    '{"feedback": "Advice", "overall_score": true}',
    '{"feedback": "Advice", "suggestions": "Read slowly"}',
    '{"feedback": {"text": "Advice"}, "suggestions": []}',
    '{"feedback": "Advice", "errors": [{"word": 1}]}',
])
def test_invalid_evaluation_output_keeps_text_metric_and_marks_ai_unavailable(spoken_audio, payload):
    llm = SimpleNamespace(chat=Mock(return_value=payload))
    result = EvaluationService(asr=recognition_provider(), llm=llm).evaluate_pronunciation(
        spoken_audio, 'we practice clearly',
    )
    assert result.recognized_text == 'we practice clearly'
    assert result.overall_score is result.pronunciation_score is result.fluency_score is None
    assert result.accuracy_score == 100
    assert result.dimensions['ai_feedback']['status'] == 'unavailable'
    assert result.dimensions['accuracy']['source'] == 'text_sequence_alignment'
    json.dumps(result.to_dict(), allow_nan=False)


def test_valid_text_only_evaluation_advice_has_source_and_preserves_unmeasured_features(spoken_audio):
    llm = SimpleNamespace(chat=Mock(return_value=json.dumps({
        'feedback': 'Use the past tense to describe yesterday.',
        'suggestions': ['Try I practiced yesterday.'],
    })))
    result = EvaluationService(asr=recognition_provider(), llm=llm).evaluate_free_speech(spoken_audio)
    assert result.dimensions['ai_feedback']['status'] == 'estimated'
    assert result.dimensions['ai_feedback']['source'] == 'configured_llm'
    assert result.overall_score is result.quality_score is result.accuracy_score is None
    assert result.audio_duration == 1
    assert all(value is None for value in vars(result.prosody_features).values())
    assert 'Use the past tense' in result.feedback
    json.dumps(result.to_dict(), allow_nan=False)
