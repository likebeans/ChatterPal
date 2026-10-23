"""Input and ownership regressions through the real ASR base interfaces."""

import io
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from chatterpal.core.asr.base import ASRBase, ASRError
from chatterpal.core.errors import AudioInputError, SpeechRecognitionError


TRANSCRIPT = "We can practice clear speech today."


def speech_samples(sample_rate=16000, duration=1.25, channels=1):
    """Valid PCM with a distinctive opening and speech-band frequencies."""
    t = np.arange(round(sample_rate * duration)) / sample_rate
    envelope = 0.55 + 0.4 * np.sin(2 * np.pi * 3 * t)
    samples = envelope * (
        0.3 * np.sin(2 * np.pi * 440 * t) + 0.18 * np.sin(2 * np.pi * 1800 * t)
    )
    samples[:8] = [0.31, -0.24, 0.16, -0.08, 0.04, -0.02, 0.12, -0.19]
    pcm = np.round(samples * 32768).astype(np.int16)
    if channels == 2:
        pcm = np.column_stack((pcm, (pcm // 2).astype(np.int16)))
    return pcm


def wav_bytes(samples, sample_rate):
    stream = io.BytesIO()
    sf.write(stream, samples, sample_rate, format="WAV", subtype="PCM_16")
    return stream.getvalue()


class RecordingASR(ASRBase):
    """Replace only the recognition provider; decode and record what it receives."""

    def __init__(self, outcomes=None, config=None):
        super().__init__(config)
        self.outcomes = list(outcomes) if outcomes is not None else [TRANSCRIPT]
        self.calls = []

    def recognize(self, audio_data, **kwargs):
        if audio_data[:4] == b"RIFF" and audio_data[8:12] == b"WAVE":
            samples, rate = sf.read(io.BytesIO(audio_data), dtype="int16")
        else:
            samples = np.frombuffer(audio_data, dtype="<i2")
            rate = kwargs.get("sample_rate", 16000)
        return self._respond(samples, rate, None, kwargs, audio_data)

    def recognize_file(self, audio_path, **kwargs):
        samples, rate = sf.read(audio_path, dtype="int16")
        return self._respond(samples, rate, Path(audio_path), kwargs, None)

    def _respond(self, samples, rate, path, kwargs, data):
        self.calls.append(
            {
                "samples": samples,
                "rate": rate,
                "path": path,
                "kwargs": kwargs,
                "bytes": data,
            }
        )
        outcome = self.outcomes.pop(0) if self.outcomes else TRANSCRIPT
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_retrying_tuple_input_is_audio_until_the_provider_receives_it():
    samples = speech_samples()
    asr = RecordingASR()

    result = asr.recognize_with_error_handling(
        (16000, samples), max_retries=1, language="en"
    )

    assert result.text == TRANSCRIPT
    assert len(asr.calls) == 1
    np.testing.assert_array_equal(asr.calls[0]["samples"], samples)
    assert asr.calls[0]["rate"] == 16000
    assert asr.calls[0]["kwargs"] == {"language": "en"}
    assert not asr.calls[0]["path"].exists()


@pytest.mark.parametrize("input_kind", ["file", "wav_bytes", "tuple"])
@pytest.mark.parametrize("sample_rate,channels", [(16000, 1), (8000, 1), (22050, 2)])
def test_enhanced_inputs_keep_full_audio_and_actual_quality_duration(
    tmp_path, input_kind, sample_rate, channels
):
    samples = speech_samples(sample_rate, channels=channels)
    data = wav_bytes(samples, sample_rate)
    caller_path = tmp_path / "recording.wav"
    caller_path.write_bytes(data)
    audio = {
        "file": str(caller_path),
        "wav_bytes": data,
        "tuple": (sample_rate, samples),
    }[input_kind]
    asr = RecordingASR()

    result = asr.recognize_enhanced(audio)

    assert result.text == TRANSCRIPT
    assert result.metadata["duration"] == pytest.approx(len(samples) / sample_rate)
    assert result.metadata["sample_count"] == len(samples)
    assert result.metadata["sample_rate"] == sample_rate
    assert "quality_assessment_error" not in result.metadata
    assert result.audio_quality_score >= 0.4
    assert len(asr.calls) == 1
    call = asr.calls[0]
    np.testing.assert_array_equal(call["samples"], samples)
    assert call["rate"] == sample_rate
    assert caller_path.read_bytes() == data
    if input_kind == "tuple":
        assert not call["path"].exists()
    elif input_kind == "wav_bytes":
        assert call["bytes"] == data


@pytest.mark.parametrize("input_kind", ["file", "wav_bytes", "tuple"])
@pytest.mark.parametrize("sample_rate,channels", [(8000, 1), (22050, 2)])
def test_retry_entrypoint_uses_real_container_rate(
    tmp_path, input_kind, sample_rate, channels
):
    samples = speech_samples(sample_rate, channels=channels)
    data = wav_bytes(samples, sample_rate)
    caller_path = tmp_path / "caller.wav"
    caller_path.write_bytes(data)
    audio = {
        "file": str(caller_path),
        "wav_bytes": data,
        "tuple": (sample_rate, samples),
    }[input_kind]
    asr = RecordingASR()

    result = asr.recognize_with_error_handling(audio, max_retries=1)

    assert result.text == TRANSCRIPT
    assert result.metadata["duration"] == pytest.approx(len(samples) / sample_rate)
    assert result.metadata["sample_count"] == len(samples)
    assert result.metadata["sample_rate"] == sample_rate
    np.testing.assert_array_equal(asr.calls[0]["samples"], samples)
    assert len(asr.calls) == 1
    assert caller_path.read_bytes() == data


@pytest.mark.parametrize(
    "entrypoint",
    ["recognize_gradio_audio", "recognize_enhanced", "recognize_with_error_handling"],
)
def test_tuple_temp_file_is_deleted_after_success(entrypoint):
    asr = RecordingASR()
    result = getattr(asr, entrypoint)((16000, speech_samples()))

    assert (result if isinstance(result, str) else result.text) == TRANSCRIPT
    assert len(asr.calls) == 1
    assert not asr.calls[0]["path"].exists()


@pytest.mark.parametrize(
    "outcomes",
    [[None, TRANSCRIPT], [ASRError("temporary provider failure"), TRANSCRIPT]],
)
def test_tuple_is_converted_once_and_kept_until_retry_completes(monkeypatch, outcomes):
    asr = RecordingASR(outcomes)
    monkeypatch.setattr("chatterpal.core.asr.base.time.sleep", lambda delay: None)
    result = asr.recognize_with_error_handling((16000, speech_samples()), max_retries=2)

    assert result.text == TRANSCRIPT
    assert len(asr.calls) == 2
    assert asr.calls[0]["path"] == asr.calls[1]["path"]
    assert not asr.calls[0]["path"].exists()
    np.testing.assert_array_equal(asr.calls[0]["samples"], asr.calls[1]["samples"])


@pytest.mark.parametrize(
    "entrypoint",
    ["recognize_gradio_audio", "recognize_enhanced", "recognize_with_error_handling"],
)
def test_tuple_temp_file_is_deleted_after_provider_failure(entrypoint):
    asr = RecordingASR([ASRError("provider failed")])
    audio = (16000, speech_samples())

    if entrypoint == "recognize_enhanced":
        result = asr.recognize_enhanced(audio)
        assert result.text is None
        assert "provider failed" in result.metadata["error"]
    else:
        error = (
            ASRError
            if entrypoint == "recognize_gradio_audio"
            else SpeechRecognitionError
        )
        options = (
            {"max_retries": 1} if entrypoint == "recognize_with_error_handling" else {}
        )
        with pytest.raises(error):
            getattr(asr, entrypoint)(audio, **options)

    assert len(asr.calls) == 1
    assert not asr.calls[0]["path"].exists()


@pytest.mark.parametrize(
    "entrypoint",
    ["recognize_gradio_audio", "recognize_enhanced", "recognize_with_error_handling"],
)
def test_tuple_conversion_failure_cleans_owned_file(tmp_path, monkeypatch, entrypoint):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    def failed_write(path, *args, **kwargs):
        Path(path).write_bytes(b"partial WAV")
        raise OSError("disk write failed")

    monkeypatch.setattr(sf, "write", failed_write)
    asr = RecordingASR()
    audio = (16000, speech_samples())
    if entrypoint == "recognize_enhanced":
        assert asr.recognize_enhanced(audio).text is None
    else:
        error = (
            ASRError
            if entrypoint == "recognize_gradio_audio"
            else SpeechRecognitionError
        )
        with pytest.raises(error):
            getattr(asr, entrypoint)(audio, max_retries=1)
    assert asr.calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "audio",
    [
        (0, np.ones(20)),
        (-16000, np.ones(20)),
        (True, np.ones(20)),
        (16000.0, np.ones(20)),
        (16000,),
        (16000, np.array([])),
        (16000, np.zeros((10, 2, 2))),
        (16000, np.zeros((10, 0))),
        (16000, np.array([float("nan")])),
        (16000, np.array([float("inf")])),
        (16000, np.array(["invalid"])),
    ],
)
def test_malformed_tuple_does_not_reach_provider(audio):
    asr = RecordingASR()
    with pytest.raises(ASRError):
        asr.recognize_gradio_audio(audio)
    with pytest.raises(SpeechRecognitionError):
        asr.recognize_with_error_handling(audio, max_retries=1)
    assert asr.calls == []


def test_gradio_float_and_int16_audio_represent_the_same_signal():
    samples = speech_samples()
    asr = RecordingASR()
    asr.recognize_gradio_audio((16000, samples))
    asr.recognize_gradio_audio((16000, samples.astype(np.float64) / 32768))
    np.testing.assert_array_equal(asr.calls[0]["samples"], asr.calls[1]["samples"])


@pytest.mark.parametrize(
    "entrypoint", ["recognize_enhanced", "recognize_with_error_handling"]
)
def test_raw_pcm_bytes_remain_supported(entrypoint):
    samples = speech_samples()
    asr = RecordingASR()
    result = getattr(asr, entrypoint)(samples.tobytes())
    assert result.text == TRANSCRIPT
    assert result.metadata["duration"] == pytest.approx(1.25)
    np.testing.assert_array_equal(asr.calls[0]["samples"], samples)


def test_short_wav_validation_ignores_container_header_size():
    asr = RecordingASR()
    with pytest.raises(AudioInputError) as error:
        asr.recognize_with_error_handling(
            wav_bytes(speech_samples(duration=0.2), 16000), max_retries=1
        )
    assert error.value.error_info.code == "AUDIO_TOO_SHORT"
    assert asr.calls == []


@pytest.mark.parametrize(
    "outcome", [None, ASRError("provider failed"), ValueError("provider failed")]
)
@pytest.mark.parametrize("silent", [False, True])
def test_caller_file_survives_retry_failure(tmp_path, outcome, silent):
    samples = np.zeros(20000, dtype=np.int16) if silent else speech_samples()
    data = wav_bytes(samples, 16000)
    path = tmp_path / "caller.wav"
    path.write_bytes(data)
    asr = RecordingASR(
        [outcome], config={"min_audio_quality": 0.0, "enable_confidence_check": False}
    )
    with pytest.raises(SpeechRecognitionError) as error:
        asr.recognize_with_error_handling(str(path), max_retries=1)
    assert error.value.error_info.code == "ASR_NO_SPEECH"
    assert len(asr.calls) == 1
    assert path.read_bytes() == data


@pytest.mark.parametrize(
    "word", ["pronunciation", "internationalization", "communication", "extraordinary"]
)
def test_normal_long_english_words_have_no_recognition_error_penalty(word):
    asr = RecordingASR()
    metadata = {"quality_score": 5, "snr": 12, "silence_ratio": 0.3}
    assert not asr._has_recognition_errors(word)
    assert asr._calculate_confidence(word, metadata) == asr._calculate_confidence(
        "clear speech", metadata
    )


@pytest.mark.parametrize(
    "text", ["aaaaaa", "文本！！！", "speech...???", "12345678901234567890"]
)
def test_clear_error_patterns_remain_detectable(text):
    assert RecordingASR()._has_recognition_errors(text)


def test_tuple_quality_failure_cleans_owned_file_before_returning(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    asr = RecordingASR(config={"min_audio_quality": 1.0})
    with pytest.raises(SpeechRecognitionError):
        asr.recognize_with_error_handling(
            (16000, np.zeros(16000, dtype=np.int16)), max_retries=1
        )
    assert asr.calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("sample_rate,channels", [(16000, 1), (44100, 2)])
def test_real_whisper_receives_equivalent_waveforms_for_all_input_forms(
    tmp_path, monkeypatch, sample_rate, channels
):
    from types import SimpleNamespace
    import whisper
    from chatterpal.core.asr.whisper import WhisperASR

    waveforms = []
    loads = []

    def transcribe(audio, **kwargs):
        waveforms.append(audio.copy())
        return {"text": TRANSCRIPT}

    def load_model(name, device):
        loads.append((name, device))
        return SimpleNamespace(transcribe=transcribe)

    monkeypatch.setattr(whisper, "load_model", load_model)
    samples = speech_samples(sample_rate, channels=channels)
    data = wav_bytes(samples, sample_rate)
    caller_path = tmp_path / "caller.wav"
    caller_path.write_bytes(data)
    asr = WhisperASR({"device": "cpu"})

    results = [
        asr.recognize_with_error_handling(audio, max_retries=1)
        for audio in (str(caller_path), data, (sample_rate, samples))
    ]

    assert [result.text for result in results] == [TRANSCRIPT] * 3
    assert loads == [("base", "cpu")]
    assert len(waveforms) == 3
    np.testing.assert_array_equal(waveforms[0], waveforms[1])
    np.testing.assert_array_equal(waveforms[0], waveforms[2])
    assert abs(len(waveforms[0]) - len(samples) * 16000 / sample_rate) <= 1
    if sample_rate == 16000:
        np.testing.assert_array_equal(
            waveforms[0][:8], samples[:8].astype(np.float32) / 32768
        )
    assert caller_path.read_bytes() == data


@pytest.mark.parametrize(
    "entrypoint", ["recognize_enhanced", "recognize_with_error_handling"]
)
def test_raw_pcm_quality_uses_the_request_sample_rate(entrypoint):
    asr = RecordingASR(config={"sample_rate": 8000})
    samples = speech_samples(16000, duration=31)
    result = getattr(asr, entrypoint)(samples.tobytes(), sample_rate=16000)
    assert result.text == TRANSCRIPT
    assert result.metadata["duration"] == pytest.approx(31)
    assert result.metadata["sample_rate"] == 16000
    assert asr.calls[0]["kwargs"]["sample_rate"] == 16000


@pytest.mark.parametrize("prefix", [b"RIFF", b"FORM", b"fLaC", b"OggS", b"ID3x"])
def test_valid_raw_pcm_may_start_with_container_letters(prefix):
    samples = speech_samples()
    samples[:2] = np.frombuffer(prefix, dtype="<i2")
    asr = RecordingASR()
    result = asr.recognize_with_error_handling(samples.tobytes(), max_retries=1)
    assert result.text == TRANSCRIPT
    assert result.metadata["duration"] == pytest.approx(1.25)
    np.testing.assert_array_equal(asr.calls[0]["samples"], samples)


@pytest.mark.parametrize(
    "data", [b"RIFF\x24\x10\x00\x00WAVEfmt " + b"\x00" * 14, b"\x01" * 32001]
)
def test_invalid_audio_is_an_input_error_without_calling_provider(data):
    asr = RecordingASR()
    with pytest.raises(AudioInputError) as error:
        asr.recognize_with_error_handling(data, max_retries=1)
    assert error.value.error_info.code == "AUDIO_FORMAT_ERROR"
    assert asr.calls == []


@pytest.mark.parametrize("input_kind", ["wav_bytes", "file"])
@pytest.mark.parametrize("max_retries", [1, 3])
@pytest.mark.parametrize("enable_quality_check", [True, False])
def test_truncated_wav_is_an_input_error_before_whisper_loads_a_model(
    tmp_path, monkeypatch, input_kind, max_retries, enable_quality_check
):
    import whisper
    from chatterpal.core.asr.whisper import WhisperASR

    loads = []
    delays = []

    def load_model(*args, **kwargs):
        loads.append((args, kwargs))
        pytest.fail("Invalid audio must not load a model")

    monkeypatch.setattr(whisper, "load_model", load_model)
    monkeypatch.setattr("chatterpal.core.asr.base.time.sleep", delays.append)
    data = wav_bytes(speech_samples(), 16000)[:30]
    caller_path = tmp_path / "truncated.wav"
    caller_path.write_bytes(data)
    audio = str(caller_path) if input_kind == "file" else data
    asr = WhisperASR(
        {"device": "cpu", "enable_quality_check": enable_quality_check}
    )

    with pytest.raises(AudioInputError) as error:
        asr.recognize_with_error_handling(audio, max_retries=max_retries)

    assert error.value.error_info.code == "AUDIO_FORMAT_ERROR"
    assert caller_path.read_bytes() == data
    assert loads == []
    assert delays == []


@pytest.mark.parametrize(
    "entrypoint",
    ["recognize_gradio_audio", "recognize_enhanced", "recognize_with_error_handling"],
)
@pytest.mark.parametrize("sample_rate,channels", [(8000, 1), (16000, 2), (44100, 2)])
@pytest.mark.parametrize("target_rate", [16000, 8000])
def test_aliyun_tuple_is_sent_as_the_configured_mono_pcm(
    monkeypatch, entrypoint, sample_rate, channels, target_rate
):
    import json
    from types import SimpleNamespace
    from chatterpal.core.asr import aliyun

    calls = []

    class Transcriber:
        def __init__(self, **callbacks):
            self.callbacks = callbacks
            self.data = bytearray()
            calls.append(self)

        def start(self, **options):
            self.options = options
            return True

        def send_audio(self, chunk):
            self.data.extend(chunk)

        def stop(self):
            self.callbacks["on_completed"](
                json.dumps({"payload": {"result": TRANSCRIPT}})
            )

    monkeypatch.setattr(
        aliyun, "nls", SimpleNamespace(NlsSpeechTranscriber=Transcriber)
    )
    monkeypatch.setattr("chatterpal.core.asr.aliyun.time.sleep", lambda delay: None)
    asr = aliyun.AliyunASR({"appkey": "test-app", "token": "test-token"})
    samples = speech_samples(sample_rate, channels=channels)
    result = getattr(asr, entrypoint)((sample_rate, samples), sample_rate=target_rate)

    assert (result if isinstance(result, str) else result.text) == TRANSCRIPT
    assert len(calls) == 1
    call = calls[0]
    assert call.options["sample_rate"] == target_rate
    assert call.options["ch"] == 1
    sent_samples = np.frombuffer(call.data, dtype="<i2")
    assert len(sent_samples) == pytest.approx(
        len(samples) / sample_rate * target_rate, abs=1
    )
    assert np.max(np.abs(sent_samples)) < 20000


def test_m4a_quality_keeps_the_existing_decoder_fallback(tmp_path):
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("M4A integration check needs a local ffmpeg encoder")
    source = tmp_path / "source.wav"
    source.write_bytes(wav_bytes(speech_samples(), 16000))
    compressed = tmp_path / "speech.m4a"
    subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(source),
            "-c:a",
            "aac",
            str(compressed),
        ],
        check=True,
        capture_output=True,
    )

    class CompressedAudioASR(RecordingASR):
        def recognize_file(self, audio_path, **kwargs):
            assert Path(audio_path).read_bytes() == compressed.read_bytes()
            self.calls.append({"path": Path(audio_path)})
            return TRANSCRIPT

    asr = CompressedAudioASR()
    result = asr.recognize_with_error_handling(str(compressed), max_retries=1)
    assert result.text == TRANSCRIPT
    assert result.metadata["duration"] == pytest.approx(1.25, abs=0.1)
    assert "quality_assessment_error" not in result.metadata
    assert result.audio_quality_score >= 0.4
    assert len(asr.calls) == 1
    assert compressed.exists()
