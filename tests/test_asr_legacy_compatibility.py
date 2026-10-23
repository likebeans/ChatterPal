"""Keep legacy ndarray callers on the real validation and retry path."""

import numpy as np
import pytest

from chatterpal.core.asr.base import ASRBase, ASRResult, ConfidenceLevel
from chatterpal.core.errors import AudioInputError


class LegacyArrayASR(ASRBase):
    """An existing enhanced adapter that accepts an in-memory waveform."""

    def __init__(self, config=None, failures=0):
        super().__init__(config)
        self.failures = failures
        self.calls = []

    def recognize(self, audio_data, **kwargs):
        raise AssertionError("The enhanced adapter handles recognition")

    def recognize_file(self, audio_path, **kwargs):
        raise AssertionError("The enhanced adapter handles recognition")

    def recognize_enhanced(self, audio_data, **kwargs):
        self.calls.append(audio_data)
        if len(self.calls) <= self.failures:
            raise RuntimeError("Temporary provider failure")
        return ASRResult("Legacy array transcript", 0.9, ConfidenceLevel.HIGH, 0.8, 0.1, {})


def waveform(sample_rate, duration=1.25):
    times = np.arange(round(sample_rate * duration)) / sample_rate
    return (0.4 * np.sin(2 * np.pi * 440 * times)).astype(np.float32)


@pytest.mark.parametrize(
    "config,options,sample_rate",
    [
        ({}, {}, 16000),
        ({"sample_rate": 8000}, {}, 8000),
        ({"sample_rate": 32000}, {"sample_rate": 8000}, 8000),
    ],
    ids=["default-rate", "configured-rate", "request-rate"],
)
def test_legacy_array_uses_quality_validation_and_sample_rate(config, options, sample_rate):
    audio = waveform(sample_rate)
    original = audio.copy()
    asr = LegacyArrayASR(config)

    result = asr.recognize_with_error_handling(audio, max_retries=1, **options)

    assert result.text == "Legacy array transcript"
    assert len(asr.calls) == 1
    assert asr.calls[0] is audio
    np.testing.assert_array_equal(audio, original)


@pytest.mark.parametrize(
    "audio,config,options,expected_code",
    [
        (waveform(16000, duration=0.25), {}, {}, "AUDIO_TOO_SHORT"),
        (np.zeros(16000, dtype=np.float32), {}, {}, "AUDIO_MOSTLY_SILENCE"),
        (
            waveform(8000),
            {"sample_rate": 8000},
            {"sample_rate": 32000},
            "AUDIO_TOO_SHORT",
        ),
    ],
    ids=["too-short", "silence", "request-rate-overrides-config"],
)
def test_legacy_array_keeps_typed_validation_errors(audio, config, options, expected_code):
    asr = LegacyArrayASR(config)

    with pytest.raises(AudioInputError) as error:
        asr.recognize_with_error_handling(audio, max_retries=1, **options)

    assert error.value.error_info.code == expected_code
    assert asr.calls == []


def test_legacy_array_retry_reuses_the_original_waveform(monkeypatch):
    monkeypatch.setattr("chatterpal.core.asr.base.time.sleep", lambda delay: None)
    audio = waveform(16000)
    asr = LegacyArrayASR(failures=1)

    result = asr.recognize_with_error_handling(audio, max_retries=2)

    assert result.text == "Legacy array transcript"
    assert len(asr.calls) == 2
    assert all(received is audio for received in asr.calls)
