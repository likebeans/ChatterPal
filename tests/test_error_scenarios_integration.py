"""网络、设备和服务故障经过真实聊天服务与 ChatTab 的集成测试。"""

import socket
import tempfile
from unittest.mock import Mock, patch

import pytest

from chatterpal.core.asr.base import ASRBase, ASRResult, ConfidenceLevel
from chatterpal.core.errors import error_handler
from chatterpal.core.llm.base import LLMBase, LLMError
from chatterpal.core.tts.base import TTSBase, TTSResult
from chatterpal.services.chat import ChatService
from chatterpal.utils.preferences import UserPreferences
from chatterpal.web.components.chat_tab import ChatTab
from tests.test_error_handling import sample_audio, wav_audio


@pytest.fixture(autouse=True)
def no_retry_waits(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda seconds: None)


class _ErrorScenario:
    """替换提供商接口；聊天编排、会话、错误映射和界面回调使用生产代码。"""

    @pytest.fixture(autouse=True)
    def setup_scenario(self):
        self.mock_asr = Mock(spec=ASRBase)
        self.mock_tts = Mock(spec=TTSBase)
        self.mock_llm = Mock(spec=LLMBase)
        self.mock_asr.recognize_with_error_handling.return_value = self.asr_result()
        self.mock_tts.synthesize.return_value = wav_audio()
        self.mock_tts.synthesize_with_error_handling.side_effect = self.synthesize_result
        self.mock_llm.chat.return_value = "Device test response"
        self.mock_asr.test_connection.return_value = True
        self.mock_tts.test_connection.return_value = True
        self.mock_llm.test_connection.return_value = True
        self.chat_service = ChatService(
            asr=self.mock_asr, tts=self.mock_tts, llm=self.mock_llm
        )
        with tempfile.TemporaryDirectory() as config_dir:
            self.preferences = UserPreferences(config_dir=config_dir)
            # ChatTab 在使用处导入该函数，必须 patch 使用处以免写入真实用户偏好。
            with patch(
                "chatterpal.web.components.chat_tab.get_preferences_manager",
                return_value=self.preferences,
            ):
                self.chat_tab = ChatTab(self.chat_service)
            yield

    def asr_result(self, text="Hello, how are you?"):
        return ASRResult(text, 0.9, ConfidenceLevel.HIGH, 0.8, 0.1, {})

    def synthesize_result(self, text, **kwargs):
        # 通过 synthesize 边界注入故障，不将 side_effect 写到普通函数属性上。
        audio = self.mock_tts.synthesize(text)
        return TTSResult(text, audio, 1.0, "wav", 16000, False, 0.1, {})

    def voice_chat(self, chat_history=None):
        return self.chat_tab._handle_chat(
            audio=(16000, sample_audio()),
            text_input="",
            chat_history=[] if chat_history is None else chat_history,
            use_text=False,
        )

    def text_chat(self, text, chat_history=None):
        return self.chat_tab._handle_chat(
            audio=None,
            text_input=text,
            chat_history=[] if chat_history is None else chat_history,
            use_text=True,
        )


class TestNetworkInterruptionScenarios(_ErrorScenario):
    @pytest.fixture(autouse=True)
    def network_boundaries(self, setup_scenario):
        self.network_available = True

        def recognize(*args, **kwargs):
            if not self.network_available:
                raise error_handler.create_error("NETWORK_ERROR")
            return self.asr_result()

        def synthesize(*args, **kwargs):
            if not self.network_available:
                raise ConnectionError("网络连接超时")
            return wav_audio()

        def chat(*args, **kwargs):
            if not self.network_available:
                raise ConnectionError("网络连接超时")
            return "I'm fine, thank you!"

        self.mock_asr.recognize_with_error_handling.side_effect = recognize
        self.mock_tts.synthesize.side_effect = synthesize
        self.mock_llm.chat.side_effect = chat

    def test_network_interruption_during_voice_chat(self):
        session_id = self.chat_service.create_session()
        response, returned_session = self.chat_service.chat_with_text("Hello", session_id)
        assert response == "I'm fine, thank you!"
        assert returned_session == session_id
        self.chat_tab.current_session_id = session_id
        self.network_available = False
        audio, history = self.voice_chat()
        assert len(history) > 0
        assert audio is None
        assert any(word in str(history) for word in ["网络", "连接", "不可用", "重试", "稍后"])
        self.mock_asr.recognize_with_error_handling.assert_called_once()
        self.network_available = True
        audio, history = self.text_chat("Network is back")
        assert len(history) > 0
        assert "I'm fine, thank you!" in str(history)
        assert audio[0] == 16000
        assert len(audio[1]) == 16000
        assert self.chat_tab.current_session_id == session_id

    def test_network_timeout_handling(self):
        self.mock_llm.chat.side_effect = socket.timeout("连接超时")
        with pytest.raises(LLMError) as exc_info:
            self.chat_service.chat_with_text("Hello")
        assert "超时" in str(exc_info.value) or "timeout" in str(exc_info.value).lower()
        assert isinstance(exc_info.value.__cause__, socket.timeout)

    def test_network_recovery_with_retry_mechanism(self):
        calls = 0

        def temporary_failure(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise ConnectionError("网络暂时不可用")
            return "重试成功后的回复"

        self.mock_llm.chat.side_effect = temporary_failure
        session_id = self.chat_service.create_session()
        response = None
        # 文本入口将提供商错误包装成 LLMError；由调用方重试。
        for _ in range(3):
            try:
                response, returned_session = self.chat_service.chat_with_text(
                    "Test retry", session_id
                )
                break
            except LLMError as error:
                assert isinstance(error.__cause__, ConnectionError)
        assert response == "重试成功后的回复"
        assert returned_session == session_id
        assert calls == 3
        history = self.chat_service.get_conversation_history(session_id)
        assert sum(message["role"] == "user" for message in history) == 1

    def test_partial_network_failure(self):
        self.mock_tts.synthesize.side_effect = ConnectionError("TTS服务网络错误")
        audio, history = self.text_chat("Hello world")
        assert len(history) > 0
        assert "I'm fine, thank you!" in str(history)
        assert audio is None
        self.mock_tts.synthesize.assert_called_once()


class TestAudioDeviceFailureScenarios(_ErrorScenario):
    def test_microphone_device_failure(self):
        self.mock_asr.recognize_with_error_handling.side_effect = OSError("麦克风设备不可用")
        audio, history = self.voice_chat()
        assert len(history) > 0
        assert audio is None
        message = str(history)
        assert any(word in message for word in ["麦克风", "设备", "不可用", "权限", "文本输入"])
        assert any(word in message for word in ["文本", "输入", "切换", "替代"])

    def test_speaker_device_failure(self):
        self.mock_tts.synthesize.side_effect = OSError("音频输出设备不可用")
        audio, history = self.text_chat("Hello, test speaker failure")
        assert len(history) > 0
        assert "Device test response" in str(history)
        assert audio is None
        self.mock_tts.synthesize.assert_called_once()

    def test_audio_permission_denied(self):
        self.mock_asr.recognize_with_error_handling.side_effect = PermissionError("麦克风权限被拒绝")
        audio, history = self.voice_chat()
        assert len(history) > 0
        assert audio is None
        assert any(
            word in str(history) for word in ["权限", "麦克风", "允许", "设置", "浏览器"]
        )

    def test_audio_format_not_supported(self):
        self.mock_asr.recognize_with_error_handling.side_effect = error_handler.create_error(
            "AUDIO_FORMAT_ERROR", message="不支持的音频格式"
        )
        audio, history = self.voice_chat()
        assert len(history) > 0
        assert audio is None
        assert any(word in str(history) for word in ["格式", "支持", "音频", "重新录制"])


class TestServiceTimeoutScenarios(_ErrorScenario):
    def test_asr_service_timeout(self):
        self.mock_asr.recognize_with_error_handling.side_effect = error_handler.create_error(
            "ASR_TIMEOUT", message="语音识别服务响应超时"
        )
        audio, history = self.voice_chat()
        assert len(history) > 0
        assert audio is None
        assert any(word in str(history) for word in ["超时", "响应", "重试", "稍后"])

    def test_tts_service_timeout(self):
        self.mock_tts.synthesize.side_effect = TimeoutError("TTS服务响应超时")
        self.mock_llm.chat.return_value = "TTS timeout test response"
        audio, history = self.text_chat("Test TTS timeout")
        assert len(history) > 0
        assert "TTS timeout test response" in str(history)
        assert audio is None
        self.mock_tts.synthesize.assert_called_once()

    def test_llm_service_timeout(self):
        self.mock_llm.chat.side_effect = TimeoutError("LLM服务响应超时")
        with pytest.raises(LLMError) as exc_info:
            self.chat_service.chat_with_text("Test LLM timeout")
        error = str(exc_info.value).lower()
        assert "超时" in error or "timeout" in error
        assert isinstance(exc_info.value.__cause__, TimeoutError)

    def test_multiple_service_timeouts(self):
        self.mock_asr.recognize_with_error_handling.side_effect = error_handler.create_error(
            "ASR_TIMEOUT", message="语音识别服务超时"
        )
        self.mock_tts.synthesize.side_effect = TimeoutError("语音合成服务超时")
        self.mock_llm.chat.side_effect = TimeoutError("语言模型服务超时")
        audio, history = self.voice_chat()
        assert len(history) > 0
        assert audio is None
        assert any(word in str(history) for word in ["超时", "服务", "不可用", "重试", "稍后"])
        self.mock_llm.chat.assert_not_called()
        self.mock_tts.synthesize.assert_not_called()

    def test_timeout_with_retry_mechanism(self):
        calls = 0

        def timeout_then_success(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise TimeoutError("服务暂时超时")
            return "重试成功的回复"

        self.mock_llm.chat.side_effect = timeout_then_success
        session_id = self.chat_service.create_session()
        response = None
        for _ in range(3):
            try:
                response, returned_session = self.chat_service.chat_with_text(
                    "Test timeout retry", session_id
                )
                break
            except LLMError as error:
                assert isinstance(error.__cause__, TimeoutError)
        assert response == "重试成功的回复"
        assert returned_session == session_id
        assert calls == 3


class TestUserFriendlyErrorMessages(_ErrorScenario):
    @pytest.mark.parametrize(
        "error,expected_keywords",
        [
            (ConnectionError("Network connection failed"), ["网络", "连接", "重试"]),
            (PermissionError("Microphone access denied"), ["权限", "麦克风", "允许"]),
            (TimeoutError("Service timeout"), ["超时", "服务", "稍后"]),
            (ValueError("Invalid audio format"), ["格式", "音频", "支持"]),
        ],
        ids=["network", "permission", "timeout", "format"],
    )
    def test_error_message_localization(self, error, expected_keywords):
        self.mock_asr.recognize_with_error_handling.side_effect = error
        audio, history = self.voice_chat()
        assert len(history) > 0
        assert audio is None
        message = str(history)
        assert any(word in message for word in expected_keywords), (
            f"错误消息 {message!r} 不包含预期关键词 {expected_keywords}"
        )

    def test_error_message_actionable_suggestions(self):
        self.mock_asr.recognize_with_error_handling.side_effect = PermissionError("麦克风权限被拒绝")
        audio, history = self.voice_chat()
        assert len(history) > 0
        assert audio is None
        actionable = ["设置", "允许", "权限", "浏览器", "文本输入", "切换"]
        assert any(word in str(history) for word in actionable), (
            f"错误消息应包含可操作建议: {history}"
        )

    def test_error_message_severity_levels(self):
        self.mock_tts.synthesize.side_effect = RuntimeError("语音合成暂时不可用")
        self.mock_llm.chat.return_value = "Text response works fine"
        audio, history = self.text_chat("Test minor error")
        assert len(history) > 0
        assert "Text response works fine" in str(history)
        assert audio is None
        self.mock_llm.chat.side_effect = RuntimeError("语言模型服务完全不可用")
        with pytest.raises(LLMError):
            self.chat_service.chat_with_text("Test critical error")

    def test_progressive_error_disclosure(self):
        self.mock_asr.recognize_with_error_handling.side_effect = [
            ConnectionError("临时网络问题"),
            TimeoutError("服务响应超时"),
            RuntimeError("服务长时间不可用，请稍后重试"),
        ]
        messages = []
        for _ in range(3):
            audio, history = self.voice_chat()
            assert audio is None
            assert len(history) > 0
            messages.append(str(history))
        assert len(messages) == 3
        assert any(word in messages[0] for word in ["网络", "重试"])
        assert any(word in messages[1] for word in ["超时", "服务"])
        assert any(word in messages[2] for word in ["稍后", "长时间"])
        assert self.mock_asr.recognize_with_error_handling.call_count == 3


class TestErrorRecoveryIntegration(_ErrorScenario):
    def test_graceful_degradation_chain(self):
        self.mock_asr.recognize_with_error_handling.side_effect = RuntimeError("语音识别不可用")
        audio, history = self.voice_chat()
        assert any(word in str(history) for word in ["文本", "输入", "切换"])
        assert audio is None
        self.mock_llm.chat.return_value = "文本输入工作正常"
        audio, history = self.text_chat("Hello via text")
        assert "文本输入工作正常" in str(history)
        assert audio[0] == 16000
        self.mock_tts.synthesize.side_effect = RuntimeError("语音合成不可用")
        audio, history = self.text_chat("Another text message", chat_history=history)
        assert "文本输入工作正常" in str(history)
        assert audio is None
        assert history[-1] == ["Another text message", "文本输入工作正常"]

    def test_error_state_cleanup(self):
        session_id = self.chat_service.create_session()
        history_before = self.chat_service.get_conversation_history(session_id)
        self.mock_llm.chat.side_effect = RuntimeError("临时错误")
        with pytest.raises(LLMError):
            self.chat_service.chat_with_text("Test error", session_id)
        assert self.chat_service.get_conversation_history(session_id) == history_before
        self.mock_llm.chat.side_effect = None
        self.mock_llm.chat.return_value = "错误已恢复"
        response, returned_session = self.chat_service.chat_with_text("Test recovery", session_id)
        assert response == "错误已恢复"
        assert returned_session == session_id
        history = self.chat_service.get_conversation_history(session_id)
        users = [message for message in history if message["role"] == "user"]
        assert len(users) >= 1
        assert users[-1]["content"] == "Test recovery"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
