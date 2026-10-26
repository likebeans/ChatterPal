"""Whole-recording assessment remains measured, explicit and uncalibrated."""

import numpy as np
import pytest

from chatterpal.core.assessment.corrector import PronunciationCorrector
from chatterpal.core.assessment.phoneme import PhonemeAnalyzer


def test_corrector_serializes_measured_recording_summary_without_phoneme_claims(
    temp_audio_file,
):
    result = PronunciationCorrector().assess(temp_audio_file, "hello world")

    assert result.overall_score is None
    assert (
        result.fluency_score,
        result.pronunciation_score,
        result.prosody_score,
        result.accuracy_score,
    ) == (None, None, None, None)
    assert result.dimensions["overall"]["status"] == "unavailable"
    assert result.feature_metadata["f0_mean"]["source"] == "parselmouth"
    assert result.audio_duration == pytest.approx(1.0)
    assert result.prosody_features.f0_mean == pytest.approx(440.0, abs=2.0)
    assert result.recognized_text == ""
    assert result.word_analysis == []
    assert len(result.phoneme_analysis) == 1
    summary = result.phoneme_analysis[0]
    assert summary.phoneme == "whole_recording"
    assert summary.error_type == "acoustic_summary"
    assert summary.target_formants == {}
    assert summary.correction_method == []
    assert "整段录音" in summary.description
    assert "未进行音素对齐" in summary.description
    assert "440" in summary.description
    assert "未校准" in result.feedback
    assert "暂不提供综合或发音分数" in result.feedback
    assert result.to_dict()["phoneme_analysis"][0]["description"] == summary.description


def test_real_spectrum_uses_nonnegative_complex_magnitudes(tmp_path):
    sample_rate = 16000
    # A power-of-two recording length keeps both tones exactly on FFT bins.
    times = np.arange(16384) / sample_rate
    # Negative real coefficients and a quadrature component exercise both rows
    # of Praat's complex spectrum, with a worked centroid of 700 Hz.
    samples = (
        -0.3 * np.cos(2 * np.pi * 500 * times)
        + 0.2 * np.sin(2 * np.pi * 1000 * times)
    ).astype(np.float32)
    import soundfile as sf

    # Float WAV avoids PCM16 quantization noise shifting spectral moments.
    audio_path = tmp_path / "complex-tones.wav"
    sf.write(audio_path, samples, sample_rate, subtype="FLOAT")
    with np.errstate(invalid="raise", divide="raise"):
        result = PhonemeAnalyzer().assess(str(audio_path))

    spectral = result["spectral"]
    assert spectral["centroid"] == pytest.approx(700.0, abs=2.0)
    assert spectral["spread"] == pytest.approx(245.0, abs=2.0)
    assert all(np.isfinite(spectral[name]) for name in ("centroid", "spread", "skewness"))
    assert spectral["available"] is True
    assert spectral["source"] == "parselmouth"


def test_unvoiced_recording_does_not_label_default_pitch_as_measured():
    audio = (16000, np.zeros(16000, dtype=np.float32))
    analysis = PhonemeAnalyzer().assess(audio)

    assert analysis["analysis_success"] is True
    assert analysis["analysis_status"] == "partial"
    assert analysis["pitch"]["available"] is False
    assert analysis["pitch"]["source"] == "unavailable"
    assert analysis["pitch"]["mean"] is None
    assert analysis["quality_score"] is None
    assert analysis["spectral"]["available"] is False
    result = PronunciationCorrector().assess(audio, phoneme_analysis=analysis)
    assert "基频不可用" in result.phoneme_analysis[0].description
    assert "基频均值" not in result.phoneme_analysis[0].description
    assert result.phoneme_analysis[0].actual_formants == {}
