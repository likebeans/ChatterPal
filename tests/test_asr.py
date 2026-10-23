"""Tests for ASR adapters with model and cloud SDK boundaries replaced."""

import io
import json
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest
import soundfile as sf

from chatterpal.core.asr.base import ASRBase, ASRError
from chatterpal.core.asr.whisper import WhisperASR
from chatterpal.core.asr.aliyun import AliyunASR


@pytest.fixture
def wav_bytes():
    """A decodable mono PCM16 WAV, usable by SpeechRecognition and NLS."""
    sample_rate = 16000
    samples = np.arange(sample_rate) / sample_rate
    audio = (0.5 * np.sin(2 * np.pi * 440 * samples) * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(audio.tobytes())
    return buffer.getvalue()


@pytest.fixture
def wav_path(tmp_path, wav_bytes):
    path = tmp_path / "input.wav"
    path.write_bytes(wav_bytes)
    return str(path)


class MockASR(ASRBase):
    """A recognition boundary for exercising generic ASRBase behavior."""

    def __init__(self, config=None, should_fail=False):
        super().__init__(config)
        self.should_fail = should_fail
        self.recognize_calls = []
        self.recognize_file_calls = []

    def recognize(self, audio_data: bytes, **kwargs):
        self.recognize_calls.append((audio_data, kwargs))
        if self.should_fail:
            raise ASRError("Mock ASR failure")
        return "mock recognition result"

    def recognize_file(self, audio_path: str, **kwargs):
        self.recognize_file_calls.append((audio_path, kwargs))
        if self.should_fail:
            raise ASRError("Mock ASR file failure")
        return "mock file recognition result"


class TestASRBase:
    """Test ASRBase independently of any downloaded model or SDK."""

    def test_initialization(self):
        asr = MockASR()
        assert asr.config == {}
        assert asr.logger is not None
        config = {"test": "value"}
        assert MockASR(config).config == config

    def test_recognize_gradio_audio_with_string(self):
        asr = MockASR()
        result = asr.recognize_gradio_audio("test_file.wav")
        assert result == "mock file recognition result"
        assert len(asr.recognize_file_calls) == 1
        assert asr.recognize_file_calls[0][0] == "test_file.wav"

    def test_recognize_gradio_audio_with_none(self):
        assert MockASR().recognize_gradio_audio(None) is None

    def test_recognize_gradio_audio_with_tuple(self):
        asr = MockASR()
        sample_rate = 16000
        audio_array = np.array([0.1, 0.2, 0.3])
        with patch("soundfile.write", wraps=sf.write) as write:
            result = asr.recognize_gradio_audio((sample_rate, audio_array))
        assert result == "mock file recognition result"
        write.assert_called_once()
        temp_path, actual_array, actual_rate = write.call_args.args
        np.testing.assert_array_equal(actual_array, audio_array)
        assert actual_rate == sample_rate
        assert asr.recognize_file_calls == [(temp_path, {})]
        assert not Path(temp_path).exists()

    def test_recognize_gradio_audio_with_invalid_format(self):
        assert MockASR().recognize_gradio_audio(123) is None

    def test_recognize_gradio_audio_error_handling(self):
        with pytest.raises(ASRError, match="Mock ASR file failure"):
            MockASR(should_fail=True).recognize_gradio_audio("test_file.wav")

    def test_test_connection_success(self):
        assert MockASR().test_connection() is True

    def test_test_connection_failure(self):
        # Base connectivity is a no-op; adapters supply their own probe.
        assert MockASR(should_fail=True).test_connection() is True

    def test_get_supported_formats(self):
        formats = MockASR().get_supported_formats()
        assert isinstance(formats, list)
        assert "wav" in formats
        assert "mp3" in formats

    def test_validate_audio_file_exists(self, wav_path):
        assert MockASR().validate_audio_file(wav_path) is True

    def test_validate_audio_file_not_exists(self, tmp_path):
        assert MockASR().validate_audio_file(str(tmp_path / "missing.wav")) is False

    def test_validate_audio_file_empty(self, tmp_path):
        path = tmp_path / "empty.wav"
        path.touch()
        assert MockASR().validate_audio_file(str(path)) is False

    def test_validate_audio_file_unsupported_format(self, tmp_path, wav_bytes):
        path = tmp_path / "audio.xyz"
        path.write_bytes(wav_bytes)
        # This validator warns about extensions but still accepts nonempty data.
        assert MockASR().validate_audio_file(str(path)) is True


class TestWhisperASR:
    """Exercise the real SpeechRecognition adapter without loading Whisper."""

    @pytest.fixture(autouse=True)
    def whisper_boundary(self):
        with patch(
            "chatterpal.core.asr.whisper.sr.Recognizer.recognize_whisper",
            autospec=True,
        ) as recognize:
            self.recognize_whisper = recognize
            yield

    def test_initialization(self):
        config = {"model_size": "base", "language": "en"}
        asr = WhisperASR(config)
        assert asr.config == config
        assert asr.model_size == "base"
        assert asr.language == "en"
        self.recognize_whisper.assert_not_called()

    def test_recognize_file_success(self, wav_path):
        from speech_recognition import AudioData

        self.recognize_whisper.return_value = "Hello world"
        asr = WhisperASR({"model_size": "base", "language": "en"})
        assert asr.recognize_file(wav_path) == "Hello world"
        self.recognize_whisper.assert_called_once()
        recognizer, audio_data = self.recognize_whisper.call_args.args
        assert recognizer is asr.recognizer
        assert isinstance(audio_data, AudioData)
        assert audio_data.sample_rate == 16000
        assert self.recognize_whisper.call_args.kwargs == {"model": "base", "language": "english"}
        assert Path(wav_path).exists()

    def test_recognize_file_failure(self, wav_path):
        from speech_recognition import RequestError

        self.recognize_whisper.side_effect = RequestError("Transcription failed")
        asr = WhisperASR()
        with pytest.raises(ASRError, match="Transcription failed"):
            asr.recognize_file(wav_path)
        self.recognize_whisper.assert_called_once()
        assert Path(wav_path).exists()

    def test_recognize_bytes_data(self, wav_bytes, tmp_path, monkeypatch):
        self.recognize_whisper.return_value = "Hello from bytes"
        monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
        asr = WhisperASR({"language": "en"})
        assert asr.recognize(wav_bytes) == "Hello from bytes"
        self.recognize_whisper.assert_called_once()
        assert list(tmp_path.iterdir()) == []

    def test_test_connection(self, tmp_path, monkeypatch):
        from speech_recognition import UnknownValueError

        # The silence probe should succeed when the model cannot transcribe it.
        self.recognize_whisper.side_effect = UnknownValueError()
        monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
        assert WhisperASR().test_connection() is True
        self.recognize_whisper.assert_called_once()
        assert list(tmp_path.iterdir()) == []


class TestAliyunASR:
    """Exercise NLS callback handling and streaming with an offline SDK stub."""

    @pytest.fixture(autouse=True)
    def nls_boundary(self, monkeypatch):
        sdk = Mock()
        transcriber = sdk.NlsSpeechTranscriber.return_value
        transcriber.start.return_value = True

        def complete():
            callbacks = sdk.NlsSpeechTranscriber.call_args.kwargs
            callbacks["on_completed"](json.dumps({"payload": {"result": "Hello world"}}))

        transcriber.stop.side_effect = complete
        monkeypatch.setattr("chatterpal.core.asr.aliyun.nls", sdk)
        monkeypatch.setattr("chatterpal.core.asr.aliyun.time.sleep", lambda seconds: None)
        self.sdk = sdk
        self.transcriber = transcriber
        self.config = {"appkey": "test_app", "token": "test_token"}

    def test_initialization(self):
        asr = AliyunASR(self.config)
        assert asr.config == self.config
        assert asr.appkey == "test_app"
        assert asr.token == "test_token"
        self.sdk.NlsSpeechTranscriber.assert_not_called()

    def test_initialization_missing_credentials(self):
        with pytest.raises(ASRError, match="需要appkey和token"):
            AliyunASR({})

    def test_recognize_file_success(self, wav_path, wav_bytes):
        asr = AliyunASR(self.config)
        assert asr.recognize_file(wav_path) == "Hello world"
        self.sdk.NlsSpeechTranscriber.assert_called_once()
        self.transcriber.start.assert_called_once_with(
            aformat="pcm",
            sample_rate=16000,
            ch=1,
            enable_intermediate_result=False,
            enable_punctuation_prediction=True,
            enable_inverse_text_normalization=True,
        )
        sent = b"".join(call.args[0] for call in self.transcriber.send_audio.call_args_list)
        with wave.open(io.BytesIO(wav_bytes), "rb") as audio:
            assert sent == audio.readframes(audio.getnframes())
        self.transcriber.stop.assert_called_once_with()
        assert Path(wav_path).exists()

    def test_recognize_file_failure(self, wav_path):
        def fail():
            callbacks = self.sdk.NlsSpeechTranscriber.call_args.kwargs
            callbacks["on_error"]("API Error")

        self.transcriber.stop.side_effect = fail
        asr = AliyunASR(self.config)
        with pytest.raises(ASRError, match="API Error"):
            asr.recognize_file(wav_path)
        self.transcriber.stop.assert_called_once_with()
        assert Path(wav_path).exists()

    def test_test_connection(self):
        # This API checks configuration and refreshes the token when keys exist.
        self.sdk.token.getToken.return_value = "refreshed_token"
        asr = AliyunASR({
            **self.config,
            "access_key_id": "test_id",
            "access_key_secret": "test_secret",
        })
        assert asr.test_connection() is True
        assert asr.token == "refreshed_token"
        self.sdk.token.getToken.assert_called_once_with("test_id", "test_secret")
        self.sdk.NlsSpeechTranscriber.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__])
