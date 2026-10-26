"""Assessment reports distinguish observed evidence from unsupported grades."""

import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from chatterpal.core.assessment.corrector import PronunciationCorrector
from chatterpal.services.correction import CorrectionService
from chatterpal.core.assessment.phoneme import PhonemeAnalyzer
from chatterpal.core.assessment.prosody import ProsodyAnalyzer
from chatterpal.services.ai_correction import AICorrectionService
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock


def test_unavailable_acoustics_never_create_measured_scores_or_sample_features():
    result = PronunciationCorrector().assess(
        (16000, np.zeros(16000, dtype=np.float32)),
        "hello world",
        phoneme_analysis={"analysis_success": False, "error": "backend unavailable"},
    )
    report = result.to_dict()

    assert report["overall_score"] is None
    assert all(value is None for value in report["detailed_scores"].values())
    assert report["quality_score"] is None
    assert all(value is None for value in report["prosody_features"].values())
    assert report["dimensions"]["pronunciation"]["status"] == "unavailable"
    assert report["dimensions"]["pronunciation"]["reason"]
    assert report["dimensions"]["pronunciation"]["evidence"] == {}
    assert report["calibrated"] is False
    assert "未校准" in report["scope"]
    assert "75.0" not in result.feedback
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize(
    ("recognized", "target", "operation", "expected_score"),
    [
        ("I home went", "I went home", "delete", 100 / 3),
        ("I home", "I went home", "delete", 200 / 3),
        ("I really went home", "I went home", "insert", 200 / 3),
        ("I went went home", "I went home", "insert", 200 / 3),
        ("I have a cat", "I has a cat", "substitute", 75.0),
    ],
)
def test_text_alignment_preserves_order_and_localizes_edits_without_acoustic_claims(
    recognized, target, operation, expected_score
):
    result = PronunciationCorrector().assess(
        (16000, np.zeros(16000, dtype=np.float32)), target,
        recognized_text=recognized,
        phoneme_analysis={"analysis_success": False, "error": "offline"},
    ).to_dict()

    accuracy = result["dimensions"]["accuracy"]
    assert accuracy["status"] == "measured"
    assert accuracy["source"] == "text_sequence_alignment"
    assert accuracy["score"] == pytest.approx(expected_score)
    assert operation in {item["operation"] for item in accuracy["evidence"]["alignment"]}
    assert result["detailed_scores"]["pronunciation"] is None
    assert result["overall_score"] is None
    assert "声学" in accuracy["reason"]
    errors = PronunciationCorrector().detect_pronunciation_errors({}, recognized, target)
    assert errors and all(item["source"] == "text_sequence_alignment" for item in errors)
    assert all("发音错误" not in item["description"] for item in errors)
    if recognized == "I home":
        differences = [item for item in accuracy["evidence"]["alignment"] if item["operation"] != "equal"]
        assert [(item["target_word"], item["recognized_word"]) for item in differences] == [("went", "")]


def test_correction_uses_measured_prosody_instead_of_base_default_features(monkeypatch):
    from chatterpal.core.assessment import prosody

    monkeypatch.setattr(prosody, "mysp", SimpleNamespace(
        myspsyl=Mock(return_value=4), mysppaus=Mock(return_value=3),
        myspsr=Mock(return_value=2.0), myspatc=Mock(return_value=3.0),
    ))
    service = CorrectionService(asr=SimpleNamespace(
        recognize_gradio_audio=lambda audio: "we practice words"
    ))
    report = service.comprehensive_correction(
        (16000, (0.3 * np.sin(2 * np.pi * 180 * np.arange(16000) / 16000)).astype(np.float32)),
        "we practice words",
    ).to_dict()

    pauses = next(issue for issue in report["prosody_issues"] if issue["type"] == "excessive_pauses")
    assert pauses["source"] == "myprosody"
    assert pauses["evidence"] == {"pause_count": 3, "syllable_count": 4}
    assert "未校准" in pauses["reason"]
    assert report["dimensions"]["accuracy"]["score"] == 100
    assert report["dimensions"]["prosody"]["score"] is None
    assert report["overall_score"] is None
    assert all(issue["type"] != "speaking_rate_slow" for issue in report["prosody_issues"])


def test_raw_analyzers_do_not_return_samples_or_ability_scores_when_unmeasured(monkeypatch):
    from chatterpal.core.assessment import phoneme, prosody

    monkeypatch.setattr(phoneme, "parselmouth", None)
    monkeypatch.setattr(prosody, "parselmouth", None)
    monkeypatch.setattr(prosody, "mysp", None)
    audio = (16000, np.zeros(16000, dtype=np.float32))
    acoustic = PhonemeAnalyzer().assess(audio)
    assert acoustic["pitch"]["mean"] is None
    assert acoustic["formants"]["F1"]["mean"] is None
    assert acoustic["quality_score"] is None
    assert acoustic["duration"] is None
    result = ProsodyAnalyzer().assess(audio)
    assert result["analysis_success"] is False
    assert all(value is None for value in vars(result["prosody_features"]).values())
    assert result["feature_sources"]["f0_mean"] == "unavailable"
    json.dumps(acoustic, allow_nan=False)


def test_prosody_preserves_provider_measurements_without_inventing_ability_formulas(monkeypatch):
    from chatterpal.core.assessment import prosody

    monkeypatch.setattr(prosody, "mysp", SimpleNamespace(
        myspsyl=lambda *args: 6, mysppaus=lambda *args: 0,
        myspsr=lambda *args: 3.0, myspatc=lambda *args: 4.0,
    ))
    result = ProsodyAnalyzer().assess((16000, np.zeros(16000, dtype=np.float32)))
    features = result["prosody_features"]
    assert features.speaking_rate == 3.0
    assert features.articulation_rate == 4.0
    assert features.pause_count == 0
    assert features.syllable_count == 6
    assert features.words_per_minute is None
    assert features.vowel_accuracy is None
    assert features.intonation_index is None
    assert features.fluency_score is None
    assert features.pause_duration is None
    assert result["feature_metadata"]["speaking_rate"]["unit"] == "syllables/second"


def test_configured_generic_llm_provides_advice_without_ability_confidence_or_progress_claims():
    responses = [
        json.dumps({"grammar_errors": [{
            "error_type": "agreement", "original": "I has", "corrected": "I have", "explanation": "Use have with I",
        }]}),
        json.dumps({"pronunciation_issues": [{
            "phoneme": "/æ/", "issue_description": "Try a short vowel", "correction_technique": "Compare cat and cut", "practice_words": ["cat"],
        }]}),
    ]
    llm = SimpleNamespace(chat=Mock(side_effect=responses))
    base = CorrectionService(asr=SimpleNamespace(recognize_gradio_audio=lambda audio: "I has a cat"))
    service = AICorrectionService(base_correction_service=base, llm=llm)
    result = asyncio.run(service.comprehensive_ai_analysis(
        (16000, np.zeros(16000, dtype=np.float32)), "I have a cat", user_id="learner", analysis_level="basic",
    ))

    assert result.grammar_corrections[0]["corrected"] == "I have"
    assert result.pronunciation_feedback[0]["status"] == "estimated"
    assert result.pronunciation_feedback[0]["scope"] == "practice_guidance_only"
    assert result.overall_score is None
    assert result.difficulty_assessment is None
    assert result.confidence_score is None
    assert result.dimensions["ai_grammar"]["source"] == "configured_llm"
    assert result.dimensions["confidence"]["status"] == "unavailable"
    assert service.get_user_progress("learner")["average_score"] is None
    assert service.get_user_progress("learner")["improvement_trend"] == "未评估"
    assert service.get_user_progress("learner")["sessions"] == 1


@pytest.mark.parametrize("payload", [
    '{"grammar_errors": [], "grammar_errors": []}',
    '{"grammar_errors": [], "confidence": NaN}',
    '{"grammar_errors": [], "score": Infinity}',
    '{"grammar_errors": [], "score": 1e999}',
    '{"grammar_errors": [{"error_type": "agreement", "original": "someone else said this", "corrected": "I have", "explanation": "Wrong source"}]}',
    '{"grammar_errors": [{"error_type": " ", "original": "I has", "corrected": "I have", "explanation": "Use have"}]}',
    '{"grammar_errors": [{"error_type": "agreement", "original": "I has", "corrected": "I have", "explanation": "Use have", "score": 99}]}',
    '{"grammar_errors": [], "improvement_suggestions": [12]}',
    '{"grammar_errors": [], "overall_assessment": {"score": 90}}',
])
def test_invalid_structured_advice_and_unquoted_evidence_cannot_claim_ai_success(payload):
    llm = SimpleNamespace(chat=Mock(return_value=payload))
    base = CorrectionService(asr=SimpleNamespace(recognize_gradio_audio=lambda audio: "I has a cat"))
    result = asyncio.run(AICorrectionService(base_correction_service=base, llm=llm).comprehensive_ai_analysis(
        (16000, np.zeros(16000, dtype=np.float32)), "I have a cat", analysis_level="basic",
    ))

    assert result.grammar_corrections == result.pronunciation_feedback == []
    assert result.recognized_text == "I has a cat"
    assert result.dimensions["accuracy"]["score"] == 75
    assert result.dimensions["ai_grammar"]["status"] == "unavailable"
    assert result.overall_score is result.confidence_score is result.difficulty_assessment is None
    assert "不可用" in result.detailed_analysis
    json.dumps(vars(result), allow_nan=False)


@pytest.mark.parametrize("payload", [
    '{"pronunciation_issues": [{"phoneme": "/s/", "issue_description": "Practice", "correction_technique": "Listen", "practice_words": "cat"}]}',
    '{"pronunciation_issues": [], "personalized_tips": [false]}',
    '{"pronunciation_issues": [], "difficulty_level": 99}',
])
def test_optional_structured_fields_have_strict_types(payload):
    llm = SimpleNamespace(chat=Mock(side_effect=['{"grammar_errors": []}', payload]))
    base = CorrectionService(asr=SimpleNamespace(recognize_gradio_audio=lambda audio: "I has a cat"))
    result = asyncio.run(AICorrectionService(base_correction_service=base, llm=llm).comprehensive_ai_analysis(
        (16000, np.zeros(16000, dtype=np.float32)), "I have a cat", analysis_level="basic",
    ))
    assert result.dimensions["ai_feedback"]["status"] == "unavailable"
    assert result.pronunciation_feedback == []
    assert result.overall_score is result.confidence_score is None


@pytest.mark.parametrize("audio", [
    (0, np.ones(32)), (-1, np.ones(32)), (True, np.ones(32)),
    (float("nan"), np.ones(32)), (16000, np.array([float("nan")])),
    (16000, np.array([float("inf")])), (16000, np.array(["text"])),
    (16000, np.array(1.0)),
])
def test_invalid_audio_samples_do_not_create_analysis(audio):
    from chatterpal.core.assessment.base import AssessmentError

    with pytest.raises(AssessmentError, match="音频数据验证失败"):
        CorrectionService().comprehensive_correction(audio)


def test_public_helpers_preserve_unknowns_and_never_infer_phonemes_or_grade_raw_rates():
    from chatterpal.core.assessment.base import ProsodyFeatures

    features = ProsodyFeatures()
    analyzer = ProsodyAnalyzer()
    assert analyzer.analyze_speaking_rate(features)["rate"] is None
    assert analyzer.analyze_pause_patterns(features)["pause_density"] is None
    assert analyzer.analyze_fluency(features)["score"] is None
    features = ProsodyFeatures(speaking_rate=3.0, pause_count=0, syllable_count=6, fluency_score=0.99)
    rate = analyzer.analyze_speaking_rate(features)
    assert rate["rate"] == 3.0
    assert rate["unit"] == "syllables/second"
    assert rate["score"] is None
    assert analyzer.analyze_pause_patterns(features)["pause_density"] == 0
    assert analyzer.analyze_fluency(features)["score"] is None
    acoustic = PhonemeAnalyzer()
    assert acoustic._calculate_quality_score({}, {}, {}) is None
    assert acoustic.detect_vowel_errors({"F1": {"mean": 9000}, "F2": {"mean": 100}}, "/æ/") == []
    feedback = acoustic.generate_pronunciation_feedback(acoustic._get_default_analysis())
    assert "不可用" in feedback and "未校准" in feedback
    assert "反馈生成失败" not in feedback


def test_service_startup_does_not_import_optional_acoustic_or_model_sdks():
    script = '''
import importlib.abc
import sys
class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"numpy", "parselmouth", "myprosody", "soundfile", "librosa", "google", "dashscope", "openai"}:
            raise AssertionError("Optional SDK imported at startup: " + fullname)
sys.meta_path.insert(0, BlockOptional())
from chatterpal.services.correction import CorrectionService
from chatterpal.services.ai_correction import AICorrectionService
base = CorrectionService()
ai = AICorrectionService(api_key="unused-key", base_correction_service=base, llm=object())
assert ai.llm is not None and ai.model is None
assert base._phoneme_analyzer is None and base._prosody_analyzer is None
'''
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")}, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_configured_llm_takes_precedence_over_legacy_key_and_preserves_base_evidence():
    base = CorrectionService(asr=SimpleNamespace(recognize_gradio_audio=lambda audio: "I has a cat"))
    llm = SimpleNamespace(chat=Mock(side_effect=[
        '{"grammar_errors": [{"error_type": "agreement", "original": "I has", "corrected": "I have", "explanation": "Use have"}]}',
        '{"pronunciation_issues": []}',
    ]))
    service = AICorrectionService(api_key="unused-key", base_correction_service=base, llm=llm)
    service._call_legacy_model = Mock(side_effect=AssertionError("Legacy provider must not be used"))
    result = asyncio.run(service.comprehensive_ai_analysis(
        (16000, np.zeros(16000, dtype=np.float32)), "I have a cat", user_id="learner", analysis_level="basic",
    ))
    assert llm.chat.call_count == 2
    service._call_legacy_model.assert_not_called()
    assert result.dimensions["accuracy"]["score"] == 75
    assert result.dimensions["accuracy"]["unit"] == "percent"
    assert result.grammar_corrections[0]["evidence"] == {"original": "I has", "start": 0, "end": 5}
    assert result.grammar_corrections[0]["scope"] == "transcript_grammar_only"
    history = service.user_history["learner"][0]
    assert history["timestamp"].endswith("+00:00")
    assert "overall_performance" not in history
    json.dumps(vars(result), allow_nan=False)
