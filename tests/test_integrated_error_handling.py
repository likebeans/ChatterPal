"""真实聊天服务的组件失败、恢复、日志和会话隔离测试。"""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from chatterpal.core.errors import ChatModuleError
from chatterpal.core.tts.base import TTSBase
from chatterpal.services.chat import ChatService
from tests.test_error_handling import MockASR, sample_audio, wav_audio
from tests.test_topic_error_handling import MockLLMForTopic


class MockTTSWithErrorHandling(TTSBase):
    """只替换合成边界，执行真实 TTS 重试和结果包装。"""

    def __init__(self):
        super().__init__({"enable_cache": False})
        self.should_fail = False
        self.call_count = 0
        self.audio_data = wav_audio()

    def synthesize(self, text, **kwargs):
        self.call_count += 1
        if self.should_fail:
            raise RuntimeError("TTS服务错误")
        return self.audio_data

    def synthesize_to_file(self, text, output_path, **kwargs):
        with open(output_path, "wb") as audio_file:
            audio_file.write(self.synthesize(text, **kwargs))
        return True

    def test_connection(self):
        return not self.should_fail


@pytest.fixture(autouse=True)
def no_retry_waits(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda seconds: None)


class TestIntegratedErrorHandling:
    def setup_method(self):
        self.asr = MockASR()
        self.tts = MockTTSWithErrorHandling()
        self.llm = MockLLMForTopic()
        self.chat_service = ChatService(asr=self.asr, tts=self.tts, llm=self.llm)

    def test_end_to_end_error_recovery_chain(self):
        session_id = self.chat_service.create_session()
        self.asr.should_fail = True
        audio, history = self.chat_service.process_chat(
            audio=sample_audio(), use_text_input=False, session_id=session_id
        )
        assert len(history) > 0
        assert audio is None
        assert any(
            keyword in history[0][1]
            for keyword in ["服务", "不可用", "网络", "重试", "文本输入"]
        )
        self.asr.should_fail = False
        audio, history = self.chat_service.process_chat(
            text_input="Hello, how are you?", use_text_input=True, session_id=session_id
        )
        assert len(history) >= 1
        assert self.llm.response in str(history)
        assert audio[0] == 16000
        assert len(audio[1]) == 16000

    def test_multiple_component_failures(self):
        session_id = self.chat_service.create_session()
        # 先建立上下文，确保生成主题时确实触发失败的 LLM 边界。
        self.chat_service.chat_with_text("I enjoy traveling", session_id)
        self.asr.should_fail = True
        self.tts.should_fail = True
        self.llm.should_fail = True
        self.llm.fail_count = 10
        audio, history = self.chat_service.process_chat(
            audio=sample_audio(), use_text_input=False, session_id=session_id
        )
        assert len(history) > 0
        assert audio is None
        calls_before = self.llm.call_count
        topic = self.chat_service.generate_topic(session_id)
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert self.llm.call_count - calls_before == 3
        assert self.chat_service.get_current_topic(session_id) == topic

    def test_partial_failure_graceful_degradation(self):
        session_id = self.chat_service.create_session()
        self.tts.should_fail = True
        audio, history = self.chat_service.process_chat(
            text_input="Tell me about your day", use_text_input=True, session_id=session_id
        )
        assert len(history) > 0
        assert self.llm.response in str(history)
        assert audio is None
        assert self.tts.call_count == 2

    def test_error_recovery_with_retry(self):
        original = self.asr.recognize_enhanced
        calls = 0

        def temporary_failure(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise RuntimeError("临时ASR错误")
            return original(*args, **kwargs)

        with patch.object(self.asr, "recognize_enhanced", side_effect=temporary_failure):
            result = self.asr.recognize_with_error_handling(sample_audio(), max_retries=3)
        assert result.text == "test recognition result"
        assert calls == 3

    def test_user_friendly_error_messages(self):
        scenarios = [
            (
                True,
                {"audio": sample_audio(), "use_text_input": False},
                ["语音", "识别", "重试"],
            ),
            (False, {"text_input": "", "use_text_input": True}, ["输入", "空", "重试"]),
        ]
        for asr_fails, inputs, keywords in scenarios:
            self.asr.should_fail = asr_fails
            self.tts.should_fail = False
            audio, history = self.chat_service.process_chat(**inputs)
            assert len(history) > 0
            assert audio is None
            message = history[0][1]
            expected = keywords + ["服务", "不可用", "问题", "重试"]
            assert any(word in message for word in expected), (
                f"错误消息 {message!r} 不包含预期关键词 {expected}"
            )

    def test_error_logging_integration(self):
        with patch("chatterpal.core.errors.error_handler.log_error") as log_error:
            session_id = self.chat_service.create_session()
            self.asr.should_fail = True
            self.chat_service.process_chat(
                audio=sample_audio(), use_text_input=False, session_id=session_id
            )
        log_error.assert_called_once()
        error = log_error.call_args.args[0]
        assert isinstance(error, ChatModuleError)
        assert error.error_info.code == "ASR_SERVICE_ERROR"

    def test_service_status_with_errors(self):
        self.asr.should_fail = True
        self.tts.should_fail = True
        status = self.chat_service.get_service_status()
        assert "asr_available" in status
        assert "tts_available" in status
        assert "llm_available" in status
        assert "active_sessions" in status
        assert status["asr_status"] is False
        assert status["tts_status"] is False
        assert status["llm_status"] is True

    def test_session_isolation_during_errors(self):
        session1 = self.chat_service.create_session()
        session2 = self.chat_service.create_session()
        self.chat_service.process_chat(
            text_input="Hello from session 1", use_text_input=True, session_id=session1
        )
        history_before = self.chat_service.get_conversation_history(session1)
        self.asr.should_fail = True
        audio, displayed_history2 = self.chat_service.process_chat(
            audio=sample_audio(), use_text_input=False, session_id=session2
        )
        history1 = self.chat_service.get_conversation_history(session1)
        history2 = self.chat_service.get_conversation_history(session2)
        assert len(history1) >= 2
        assert len(history2) >= 1  # 默认系统提示仍然保留；失败轮次不会写入会话。
        assert history1 == history_before
        assert "Hello from session 1" in str(history1)
        assert "错误" not in str(history1) and "失败" not in str(history1)
        assert all(message["role"] == "system" for message in history2)
        assert audio is None
        assert "语音识别" in displayed_history2[0][1]

    def test_cleanup_after_errors(self):
        session_id = self.chat_service.create_session()
        self.asr.should_fail = True
        self.tts.should_fail = True
        audio, failed_history = self.chat_service.process_chat(
            audio=sample_audio(), use_text_input=False, session_id=session_id
        )
        assert audio is None
        assert "语音识别" in failed_history[0][1]
        self.asr.should_fail = False
        self.tts.should_fail = False
        audio, history = self.chat_service.process_chat(
            text_input="Are you working now?", use_text_input=True, session_id=session_id
        )
        assert len(history) >= 1
        assert self.llm.response in str(history)
        assert audio[0] == 16000
        assert len(audio[1]) == 16000

    def test_concurrent_error_handling(self):
        # 固定提供商状态，避免各线程修改同一开关造成测试自身的竞态。
        self.asr.should_fail = True

        def worker(worker_id):
            session_id = self.chat_service.create_session()
            if worker_id % 2 == 0:
                result = self.chat_service.process_chat(
                    audio=sample_audio(), use_text_input=False, session_id=session_id
                )
            else:
                result = self.chat_service.process_chat(
                    text_input=f"Hello from worker {worker_id}",
                    use_text_input=True,
                    session_id=session_id,
                )
            return worker_id, session_id, result

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(worker, worker_id) for worker_id in range(5)]
            results = [future.result(timeout=10) for future in futures]
        assert len(results) == 5
        assert len({session_id for _, session_id, _ in results}) == 5
        for worker_id, session_id, (audio, history) in results:
            assert len(history) > 0
            if worker_id % 2 == 0:
                assert audio is None
                assert "语音识别" in history[-1][1]
            else:
                assert history[-1] == [f"Hello from worker {worker_id}", self.llm.response]
                assert audio[0] == 16000
                stored = self.chat_service.get_conversation_history(session_id)
                assert [message["content"] for message in stored if message["role"] == "user"] == [
                    f"Hello from worker {worker_id}"
                ]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
