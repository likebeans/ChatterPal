"""AI analysis augments a real correction report or explicitly returns basic results."""

import asyncio
import io
import json
import threading
import wave
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from chatterpal.core.assessment.base import AssessmentError
from chatterpal.services.ai_correction import AICorrectionService
from chatterpal.services.correction import CorrectionService


@pytest.fixture
def service():
    correction = CorrectionService(
        asr=SimpleNamespace(recognize=Mock(return_value="I has a cat"))
    )
    return AICorrectionService(base_correction_service=correction)


def audio_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x04" * 16000)
    return output.getvalue()


def analyze(service):
    return asyncio.run(
        service.comprehensive_ai_analysis(
            audio_bytes(), "I have a cat", analysis_level="basic"
        )
    )


def test_unconfigured_ai_preserves_basic_result_and_labels_the_fallback(service):
    result = analyze(service)
    assert result.overall_score is None
    assert result.dimensions['overall']['status'] == 'unavailable'
    assert result.recognized_text == "I has a cat"
    assert result.difficulty_assessment is None
    assert result.confidence_score is None
    assert result.dimensions['confidence']['status'] == 'unavailable'
    assert result.grammar_corrections == result.pronunciation_feedback == []
    assert "基础分析" in result.detailed_analysis
    assert "未配置" in result.detailed_analysis
    assert "AI驱动" not in result.detailed_analysis


def test_base_failure_keeps_original_error_instead_of_unbound_report(
    service, monkeypatch
):
    monkeypatch.setattr(
        service.base_service.corrector,
        "assess",
        Mock(side_effect=AssessmentError("Base assessment unavailable")),
    )
    with pytest.raises(AssessmentError, match="Base assessment unavailable"):
        analyze(service)


def test_ai_prompts_reach_provider_and_internal_fields_map_to_http_contract(service):
    prompts = []

    def generate(prompt):
        prompts.append(prompt)
        if "grammar_errors" in prompt:
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "grammar_errors": [
                            {
                                "error_type": "subject-verb",
                                "original": "I has",
                                "corrected": "I have",
                                "explanation": "Use have with I",
                                "rule": "agreement",
                            }
                        ]
                    }
                )
            )
        return SimpleNamespace(
            text=json.dumps(
                {
                    "pronunciation_issues": [
                        {
                            "phoneme": "/æ/",
                            "issue_description": "Check the vowel",
                            "correction_technique": "Practice a short vowel",
                            "practice_words": ["cat"],
                        }
                    ]
                }
            )
        )

    service.model = SimpleNamespace(generate_content=generate)
    result = analyze(service)
    assert len(prompts) == 2
    assert all(
        "I has a cat" in prompt and "I have a cat" in prompt for prompt in prompts
    )
    assert result.grammar_corrections[0]["type"] == "subject-verb"
    assert result.grammar_corrections[0]["corrected"] == "I have"
    feedback = result.pronunciation_feedback[0]
    assert feedback["type"] == "pronunciation"
    assert feedback["original"] == "/æ/"
    assert feedback["corrected"] == "Practice a short vowel"
    assert feedback["explanation"] == "Check the vowel"
    assert result.recognized_text == "I has a cat"
    assert result.confidence_score is None
    assert result.dimensions['ai_feedback']['status'] == 'estimated'


@pytest.mark.parametrize(
    "payload",
    [
        "not JSON",
        "[]",
        '{"grammar_errors": {}}',
        '{"grammar_errors": [null]}',
        '{"grammar_errors": [{"original": 12}]}',
    ],
)
def test_invalid_ai_data_returns_explicit_basic_fallback(service, payload):
    service.model = SimpleNamespace(
        generate_content=Mock(return_value=SimpleNamespace(text=payload))
    )
    result = analyze(service)
    assert result.recognized_text == "I has a cat"
    assert result.overall_score is None
    assert result.dimensions['overall']['status'] == 'unavailable'
    assert result.confidence_score is None
    assert result.dimensions['confidence']['status'] == 'unavailable'
    assert result.grammar_corrections == result.pronunciation_feedback == []
    assert "基础分析" in result.detailed_analysis
    assert "不可用" in result.detailed_analysis


@pytest.mark.parametrize(
    "payload",
    [
        "not JSON",
        '{"pronunciation_issues": {}}',
        '{"pronunciation_issues": [null]}',
        '{"pronunciation_issues": [{"phoneme": 12}]}',
    ],
)
def test_invalid_pronunciation_response_falls_back_after_valid_grammar(
    service, payload
):
    service.model = SimpleNamespace(
        generate_content=Mock(
            side_effect=[
                SimpleNamespace(text='{"grammar_errors": []}'),
                SimpleNamespace(text=payload),
            ]
        )
    )
    result = analyze(service)
    assert service.model.generate_content.call_count == 2
    assert result.recognized_text == "I has a cat"
    assert result.confidence_score is None
    assert result.dimensions['confidence']['status'] == 'unavailable'
    assert "基础分析" in result.detailed_analysis
    assert "不可用" in result.detailed_analysis


def test_service_without_asr_never_claims_ai_success():
    service = AICorrectionService()
    service.model = SimpleNamespace(
        generate_content=Mock(
            return_value=SimpleNamespace(text='{"pronunciation_issues": []}')
        )
    )
    result = analyze(service)
    service.model.generate_content.assert_not_called()
    assert result.recognized_text == ""
    assert result.confidence_score is None
    assert result.dimensions['confidence']['status'] == 'unavailable'
    assert "识别文本不可用" in result.detailed_analysis
    assert "AI驱动" not in result.detailed_analysis


def test_ai_provider_failure_does_not_discard_the_basic_report(service):
    service.model = SimpleNamespace(
        generate_content=Mock(side_effect=OSError("Provider unavailable"))
    )
    result = analyze(service)
    assert result.recognized_text == "I has a cat"
    assert result.overall_score is None
    assert result.dimensions['overall']['status'] == 'unavailable'
    assert result.confidence_score is None
    assert result.dimensions['confidence']['status'] == 'unavailable'
    assert "不可用" in result.detailed_analysis


def test_sync_ai_provider_runs_outside_event_loop(service):
    started, release, expired = threading.Event(), threading.Event(), threading.Event()
    threads = []
    loop_thread = threading.get_ident()

    def generate(prompt):
        threads.append(threading.get_ident())
        started.set()
        if not release.wait(1):
            expired.set()
            raise TimeoutError("Loop was blocked")
        key = "grammar_errors" if "grammar_errors" in prompt else "pronunciation_issues"
        return SimpleNamespace(text=json.dumps({key: []}))

    service.model = SimpleNamespace(generate_content=generate)

    async def request():
        pending = asyncio.create_task(
            service.comprehensive_ai_analysis(
                audio_bytes(), "I have a cat", analysis_level="basic"
            )
        )
        try:
            assert await asyncio.to_thread(started.wait, 2)
            assert not expired.is_set(), "AI provider blocked event loop"
            assert not pending.done()
            release.set()
            return await pending
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)

    result = asyncio.run(request())
    assert result.confidence_score is None
    assert result.dimensions['ai_feedback']['status'] == 'estimated'
    assert threads and all(thread != loop_thread for thread in threads)


def test_all_prompt_templates_accept_their_documented_substitutions(service):
    substitutions = {
        "recognized_text": "I said words",
        "reference_text": "Reference words",
        "base_assessment": {},
        "overall_score": 75,
        "pronunciation_score": 70,
        "grammar_score": 80,
        "current_scenario": "daily_conversation",
        "difficulty_level": "intermediate",
    }
    for prompt in service.prompts.values():
        assert "{" in prompt.format(**substitutions)
