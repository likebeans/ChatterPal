"""语音输入、语音输出和主题生成的错误处理测试。"""

import io
import wave
from unittest.mock import patch

import numpy as np
import pytest

from chatterpal.core.asr.base import ASRBase, ASRResult, ConfidenceLevel
from chatterpal.core.errors import (
    AudioInputError,
    ChatModuleError,
    ErrorCategory,
    ErrorHandler,
    ErrorSeverity,
    SpeechRecognitionError,
)
from chatterpal.services.chat import ChatService
from chatterpal.services.topic_generator import TopicGenerator
from chatterpal.utils.audio import AudioQualityLevel, AudioValidationResult


def sample_audio(sample_count=16000):
    """生成可重复的 16 kHz 测试信号，不访问音频设备。"""
    samples = np.arange(sample_count, dtype=np.float32)
    return (0.4 * np.sin(2 * np.pi * 440 * samples / 16000)).astype(np.float32)


def wav_audio():
    """生成能被 process_chat 解码的 PCM16 WAV。"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(16000)
        audio_file.writeframes((sample_audio() * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def no_retry_waits(monkeypatch):
    """测试仍执行所有重试，只省略退避等待。"""
    monkeypatch.setattr("time.sleep", lambda seconds: None)


class TestErrorHandler:
    def setup_method(self):
        self.error_handler = ErrorHandler()

    def test_get_error_info(self):
        info = self.error_handler.get_error_info("AUDIO_TOO_SHORT")
        assert info is not None
        assert info.code == "AUDIO_TOO_SHORT"
        assert info.category == ErrorCategory.AUDIO_INPUT
        assert info.severity == ErrorSeverity.MEDIUM
        assert self.error_handler.get_error_info("UNKNOWN_ERROR_CODE") is None

    def test_create_error(self):
        error = self.error_handler.create_error("AUDIO_TOO_SHORT", duration=0.5)
        assert isinstance(error, AudioInputError)
        assert error.error_info.code == "AUDIO_TOO_SHORT"
        assert error.error_info.metadata["duration"] == 0.5
        unknown = self.error_handler.create_error("UNKNOWN_CODE")
        assert isinstance(unknown, ChatModuleError)
        assert unknown.error_info.code == "UNKNOWN_ERROR"

    def test_handle_audio_validation_error(self):
        valid = AudioValidationResult(True, 5.0, AudioQualityLevel.GOOD, [], {})
        assert self.error_handler.handle_audio_validation_error(valid) is None
        scenarios = [
            (0.5, AudioQualityLevel.POOR, ["音频时长过短"], {}, "AUDIO_TOO_SHORT"),
            (65.0, AudioQualityLevel.FAIR, ["音频时长过长"], {}, "AUDIO_TOO_LONG"),
            (
                3.0,
                AudioQualityLevel.POOR,
                ["音频音量过低"],
                {"max_amplitude": 0.001, "rms_amplitude": 0.0005},
                "AUDIO_LOW_VOLUME",
            ),
            (
                3.0,
                AudioQualityLevel.POOR,
                ["静音比例过高"],
                {"silence_ratio": 0.9},
                "AUDIO_MOSTLY_SILENCE",
            ),
        ]
        for duration, quality, issues, metadata, code in scenarios:
            result = AudioValidationResult(False, duration, quality, issues, metadata)
            error = self.error_handler.handle_audio_validation_error(result)
            assert isinstance(error, AudioInputError)
            assert error.error_info.code == code

    def test_handle_asr_error(self):
        scenarios = [
            (None, 0.0, ConfidenceLevel.VERY_LOW, 2.0, "ASR_NO_SPEECH"),
            ("hello", 0.2, ConfidenceLevel.VERY_LOW, 2.0, "ASR_LOW_CONFIDENCE"),
            ("hello", 0.8, ConfidenceLevel.HIGH, 35.0, "ASR_TIMEOUT"),
            ("hello world", 0.9, ConfidenceLevel.HIGH, 2.0, None),
        ]
        for text, confidence, level, elapsed, code in scenarios:
            result = ASRResult(text, confidence, level, 0.8, elapsed, {})
            error = self.error_handler.handle_asr_error(result)
            if code is None:
                assert error is None
            else:
                assert isinstance(error, SpeechRecognitionError)
                assert error.error_info.code == code

    def test_format_user_error_message(self):
        error = self.error_handler.create_error("AUDIO_TOO_SHORT", duration=0.5)
        formatted = self.error_handler.format_user_error_message(error)
        assert formatted["error_code"] == "AUDIO_TOO_SHORT"
        assert "录音时间太短" in formatted["message"]
        assert len(formatted["suggestions"]) > 0
        assert formatted["severity"] == "medium"
        assert formatted["category"] == "audio_input"
        assert formatted["can_retry"] is True


class MockASR(ASRBase):
    """可控制识别结果的提供商替身，保留真实校验与重试入口。"""

    def __init__(self, config=None):
        super().__init__(config)
        self.should_fail = False
        self.fail_with_timeout = False
        self.return_low_confidence = False
        self.return_empty = False
        self.call_count = 0

    def recognize(self, audio_data, **kwargs):
        if self.should_fail:
            raise RuntimeError("ASR服务错误")
        return None if self.return_empty else "test recognition result"

    def recognize_file(self, audio_path, **kwargs):
        return self.recognize(audio_path, **kwargs)

    def recognize_enhanced(self, audio_data, **kwargs):
        self.call_count += 1
        if self.should_fail:
            raise RuntimeError("ASR服务错误")
        return ASRResult(
            text=None if self.return_empty else "test recognition result",
            confidence=0.2 if self.return_low_confidence else 0.9,
            confidence_level=(
                ConfidenceLevel.VERY_LOW
                if self.return_low_confidence
                else ConfidenceLevel.HIGH
            ),
            audio_quality_score=0.8,
            processing_time=35.0 if self.fail_with_timeout else 2.0,
            metadata={},
        )

    def test_connection(self):
        return not self.should_fail


class MockTTS:
    """基本合成提供商替身，输出真实 WAV 且不播放音频。"""

    def __init__(self):
        self.should_fail = False
        self.fail_with_timeout = False
        self.call_count = 0
        self.audio_data = wav_audio()

    def synthesize(self, text, **kwargs):
        self.call_count += 1
        if self.should_fail:
            raise RuntimeError("TTS服务错误")
        if self.fail_with_timeout:
            raise TimeoutError("TTS服务响应超时")
        return self.audio_data

    def test_connection(self):
        return not (self.should_fail or self.fail_with_timeout)


class MockLLM:
    def __init__(self):
        self.should_fail = False

    def chat(self, messages, **kwargs):
        if self.should_fail:
            raise RuntimeError("LLM服务错误")
        return "Test response"

    def test_connection(self):
        return not self.should_fail


class TestASRErrorHandling:
    def setup_method(self):
        self.asr = MockASR()

    def test_recognize_with_error_handling_success(self):
        result = self.asr.recognize_with_error_handling(sample_audio())
        assert result.text == "test recognition result"
        assert result.confidence > 0.5

    def test_recognize_with_error_handling_retry(self):
        original = self.asr.recognize_enhanced
        calls = 0

        def temporary_failure(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise RuntimeError("临时错误")
            return original(*args, **kwargs)

        with patch.object(self.asr, "recognize_enhanced", side_effect=temporary_failure):
            result = self.asr.recognize_with_error_handling(sample_audio(), max_retries=3)
        assert result.text == "test recognition result"
        assert calls == 3

    def test_recognize_with_error_handling_audio_too_short(self):
        with pytest.raises(AudioInputError) as exc_info:
            self.asr.recognize_with_error_handling(sample_audio(100))
        assert exc_info.value.error_info.code == "AUDIO_TOO_SHORT"
        assert self.asr.call_count == 0

    def test_recognize_with_error_handling_low_confidence(self):
        self.asr.return_low_confidence = True
        with pytest.raises(SpeechRecognitionError) as exc_info:
            self.asr.recognize_with_error_handling(sample_audio(), max_retries=1)
        assert exc_info.value.error_info.code == "ASR_LOW_CONFIDENCE"

    def test_recognize_with_error_handling_no_speech(self):
        self.asr.return_empty = True
        with pytest.raises(SpeechRecognitionError) as exc_info:
            self.asr.recognize_with_error_handling(sample_audio())
        assert exc_info.value.error_info.code == "ASR_NO_SPEECH"
        assert self.asr.call_count == 3


class TestChatServiceErrorHandling:
    def setup_method(self):
        self.asr = MockASR()
        self.tts = MockTTS()
        self.llm = MockLLM()
        self.chat_service = ChatService(asr=self.asr, tts=self.tts, llm=self.llm)

    def test_chat_with_audio_success(self):
        text, audio, session_id = self.chat_service.chat_with_audio(sample_audio())
        assert text == "Test response"
        assert audio == self.tts.audio_data
        assert session_id is not None

    def test_chat_with_audio_asr_failure(self):
        self.asr.should_fail = True
        with pytest.raises(SpeechRecognitionError):
            self.chat_service.chat_with_audio(sample_audio(), max_retries=1)

    def test_chat_with_audio_tts_failure(self):
        self.tts.should_fail = True
        text, audio, session_id = self.chat_service.chat_with_audio(sample_audio())
        assert text == "Test response"
        assert audio is None
        assert session_id is not None
        assert self.tts.call_count == 2

    def test_process_chat_error_handling(self):
        audio, history = self.chat_service.process_chat(text_input="", use_text_input=True)
        assert len(history) > 0
        assert audio is None
        assert any(
            word in history[0][1] for word in ["错误", "失败", "不支持", "损坏", "重新"]
        )

    def test_process_chat_audio_error_handling(self):
        self.asr.should_fail = True
        audio, history = self.chat_service.process_chat(
            audio=sample_audio(100), use_text_input=False
        )
        assert len(history) > 0
        assert audio is None
        assert any(word in history[0][1] for word in ["录音", "音频", "语音", "重试"])


class TestTopicGeneratorErrorHandling:
    def setup_method(self):
        self.llm = MockLLM()
        self.topic_generator = TopicGenerator(llm=self.llm)

    def test_generate_random_topic_success(self):
        topic = self.topic_generator.generate_random_topic("intermediate")
        assert isinstance(topic, str)
        assert len(topic) > 0

    def test_generate_random_topic_with_fallback(self):
        topic = self.topic_generator.generate_random_topic_with_fallback("invalid_difficulty")
        assert isinstance(topic, str)
        assert len(topic) > 0

    def test_generate_contextual_topic_llm_failure(self):
        self.llm.should_fail = True
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        topic = self.topic_generator.generate_contextual_topic(history)
        assert isinstance(topic, str)
        assert len(topic) > 0

    def test_generate_contextual_topic_empty_history(self):
        topic = self.topic_generator.generate_contextual_topic([])
        assert isinstance(topic, str)
        assert len(topic) > 0


class TestIntegratedErrorHandling:
    def setup_method(self):
        self.asr = MockASR()
        self.tts = MockTTS()
        self.llm = MockLLM()
        self.chat_service = ChatService(asr=self.asr, tts=self.tts, llm=self.llm)

    def test_end_to_end_error_recovery(self):
        session_id = self.chat_service.create_session()
        audio1, history1 = self.chat_service.process_chat(
            audio=sample_audio(), use_text_input=False, session_id=session_id
        )
        assert len(history1) > 0
        assert audio1[0] == 16000
        assert "Test response" in str(history1)
        self.asr.should_fail = True
        audio2, history2 = self.chat_service.process_chat(
            audio=sample_audio(), use_text_input=False, session_id=session_id
        )
        assert len(history2) >= len(history1)
        assert audio2 is None
        assert "语音识别" in history2[-1][1]
        self.asr.should_fail = False
        audio3, history3 = self.chat_service.process_chat(
            text_input="Hello again", use_text_input=True, session_id=session_id
        )
        assert len(history3) > len(history2)
        assert "Test response" in str(history3)
        assert audio3[0] == 16000

    def test_topic_generation_error_recovery(self):
        session_id = self.chat_service.create_session()
        topic1 = self.chat_service.generate_topic(session_id)
        assert isinstance(topic1, str)
        assert len(topic1) > 0
        self.chat_service.chat_with_text("Hello", session_id)
        self.llm.should_fail = True
        topic2 = self.chat_service.generate_topic(session_id)
        assert isinstance(topic2, str)
        assert len(topic2) > 0
        assert self.chat_service.get_current_topic(session_id) == topic2

    @patch("chatterpal.core.errors.error_handler.log_error")
    def test_error_logging(self, mock_log_error):
        self.asr.should_fail = True
        # process_chat 负责记录结构化错误，chat_with_audio 将错误交给调用方。
        audio, history = self.chat_service.process_chat(
            audio=sample_audio(), use_text_input=False
        )
        assert audio is None
        assert "语音识别" in history[0][1]
        mock_log_error.assert_called_once()
        assert isinstance(mock_log_error.call_args.args[0], SpeechRecognitionError)
        assert mock_log_error.call_args.args[0].error_info.code == "ASR_SERVICE_ERROR"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
