"""Detailed reports and base summaries share one real acoustic analysis."""
from unittest.mock import Mock

import numpy as np
import pytest
import soundfile as sf

from chatterpal.core.assessment.phoneme import PhonemeAnalyzer
from chatterpal.services.correction import CorrectionService


@pytest.fixture
def voice(tmp_path):
    path = tmp_path / 'voice.wav'
    signal = 0.3 * np.sin(2 * np.pi * 180 * np.arange(16000) / 16000)
    sf.write(path, signal, 16000)
    return str(path)


@pytest.mark.parametrize('level', ['basic', 'detailed', 'expert'])
def test_each_correction_measures_acoustics_once(voice, level, monkeypatch):
    from chatterpal.core.assessment import prosody
    monkeypatch.setattr(prosody, 'mysp', None)
    original = PhonemeAnalyzer.assess
    measured = []

    def record(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        measured.append(result)
        return result

    monkeypatch.setattr(PhonemeAnalyzer, 'assess', record)
    service = CorrectionService()
    service.corrector.assess = Mock(wraps=service.corrector.assess)
    report = service.comprehensive_correction(voice, 'hello world', analysis_level=level)

    assert len(measured) == 1
    assert measured[0]['analysis_success'] is True
    assert measured[0]['pitch']['mean'] == pytest.approx(180, abs=2)
    assert report.overall_score is None
    assert report.dimensions['overall']['status'] == 'unavailable'
    if level != 'basic':
        assert service.corrector.assess.call_args.kwargs['phoneme_analysis'] is measured[0]
    assert '未校准' in report.detailed_feedback


def test_failed_detailed_measurement_is_not_retried_by_base_summary(voice, monkeypatch):
    from chatterpal.core.assessment import prosody
    monkeypatch.setattr(prosody, 'mysp', None)
    failed = Mock(side_effect=RuntimeError('controlled acoustic failure'))
    monkeypatch.setattr(PhonemeAnalyzer, 'assess', failed)
    service = CorrectionService()
    service.corrector.assess = Mock(wraps=service.corrector.assess)

    report = service.comprehensive_correction(voice, 'hello world')

    assert failed.call_count == 1
    supplied = service.corrector.assess.call_args.kwargs['phoneme_analysis']
    assert supplied == {'analysis_success': False, 'error': 'controlled acoustic failure'}
    assert report.phoneme_problems == []
    assert '不可用' in report.detailed_feedback
    assert report.overall_score is None
    assert report.dimensions['overall']['status'] == 'unavailable'
