"""
TTS错误处理功能测试
测试语音合成的错误处理和重试机制
"""

import pytest
import time
from unittest.mock import patch

from chatterpal.core.tts.base import TTSBase, TTSResult
from chatterpal.core.errors import AudioOutputError, SpeechSynthesisError
from chatterpal.services.chat import ChatService
from chatterpal.core.llm.openai import OpenAILLM


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    """Keep retries deterministic without spending time on backoff."""
    monkeypatch.setattr("chatterpal.core.tts.base.time.sleep", lambda seconds: None)


class MockTTSWithErrorHandling(TTSBase):
    """支持错误处理的模拟TTS"""
    
    def __init__(self, config=None):
        super().__init__(config)
        self.should_fail = False
        self.fail_count = 0
        self.fail_with_timeout = False
        self.return_empty = False
        self.call_count = 0
    
    def synthesize(self, text: str, **kwargs) -> bytes:
        self.call_count += 1
        
        if self.should_fail:
            if self.fail_count > 0:
                self.fail_count -= 1
                raise Exception(f"TTS服务错误 (调用 {self.call_count})")
            
        if self.fail_with_timeout:
            time.sleep(0.1)  # 模拟处理时间
            raise Exception("TTS处理超时")
            
        if self.return_empty:
            return b""
        
        return b"fake audio data for: " + text.encode()
    
    def synthesize_to_file(self, text: str, output_path: str, **kwargs) -> bool:
        try:
            audio_data = self.synthesize(text, **kwargs)
            with open(output_path, 'wb') as f:
                f.write(audio_data)
            return True
        except:
            return False


class MockTTSBasic:
    """Legacy synthesis boundary without the enhanced base-class interface."""

    def __init__(self, config=None):
        self.config = config or {}
        self.should_fail = False
        self.call_count = 0
    
    def synthesize(self, text: str, **kwargs) -> bytes:
        self.call_count += 1
        if self.should_fail:
            raise Exception("TTS服务错误")
        return b"basic tts audio data"
    
    def synthesize_to_file(self, text: str, output_path: str, **kwargs) -> bool:
        return True


class TestTTSErrorHandling:
    """测试TTS错误处理"""
    
    def setup_method(self):
        """设置测试环境"""
        self.tts = MockTTSWithErrorHandling({
            "enable_cache": True,
            "cache_size_limit": 10,
            "max_synthesis_time": 5.0
        })
    
    def test_synthesize_with_error_handling_success(self):
        """测试成功的语音合"""
        result = self.tts.synthesize_with_error_handling("Hello world")
        
        assert isinstance(result, TTSResult)
        assert result.text == "Hello world"
        assert result.audio_data == b"fake audio data for: Hello world"
        assert result.cached is False
        assert result.synthesis_time >= 0  # 允许,因为模拟执行很
    
    def test_synthesize_with_error_handling_retry(self):
        """测试重试机制"""
        # 设置前两次失败,第三次成
        self.tts.should_fail = True
        self.tts.fail_count = 2
        
        result = self.tts.synthesize_with_error_handling("Test retry", max_retries=3)
        
        assert isinstance(result, TTSResult)
        assert result.text == "Test retry"
        assert self.tts.call_count == 3  # 验证重试
    
    def test_synthesize_with_error_handling_invalid_text(self):
        """测试无效文本错误"""
        # 测试空文本
        with pytest.raises(SpeechSynthesisError):
            self.tts.synthesize_with_error_handling("")
        
        # 测试None
        with pytest.raises(SpeechSynthesisError):
            self.tts.synthesize_with_error_handling(None)
        
        # 测试过长文本
        long_text = "a" * 10000
        self.tts.config["max_text_length"] = 100
        with pytest.raises(SpeechSynthesisError):
            self.tts.synthesize_with_error_handling(long_text)
    
    def test_synthesize_with_error_handling_empty_audio(self):
        """测试空音频数据错误"""
        self.tts.return_empty = True
        
        with pytest.raises(SpeechSynthesisError):
            self.tts.synthesize_with_error_handling("Test empty audio")
    
    def test_synthesize_with_error_handling_all_retries_fail(self):
        """测试所有重试都失败"""
        self.tts.should_fail = True
        self.tts.fail_count = 10  # 设置足够多的失败次数
        
        with pytest.raises(SpeechSynthesisError):
            self.tts.synthesize_with_error_handling("Test all fail", max_retries=3)
        
        assert self.tts.call_count == 3  # 验证重试
    
    def test_cache_functionality(self):
        """测试缓存功能"""
        # 第一次合成
        result1 = self.tts.synthesize_with_error_handling("Cache test")
        assert result1.cached is False
        
        # 第二次合成相同文本,应该从缓存获取
        result2 = self.tts.synthesize_with_error_handling("Cache test")
        assert result2.cached is True
        assert result2.audio_data == result1.audio_data
        
        # 验证只调用了一次实际合成
        assert self.tts.call_count == 1
    
    def test_cache_with_different_params(self):
        """测试不同参数的缓存"""
        # 相同文本,不同参数
        result1 = self.tts.synthesize_with_error_handling("Param test", voice="voice1")
        result2 = self.tts.synthesize_with_error_handling("Param test", voice="voice2")
        
        # 应该是两次不同的合成
        assert result1.cached is False
        assert result2.cached is False
        assert self.tts.call_count == 2
    
    def test_text_cleaning(self):
        """测试文本清理功能"""
        dirty_text = "Hello!!! @#$%^&*() World秒秒"
        cleaned = self.tts.clean_text_for_tts(dirty_text)
        
        # 验证特殊字符被清理
        assert "@#$%^&*()" not in cleaned
        assert "Hello" in cleaned
        assert "World" in cleaned
    
    def test_audio_duration_estimation(self):
        """测试音频时长估算"""
        # One second of mono PCM16 at 16 kHz occupies 32,000 bytes.
        audio_data = b'\x00' * 32000
        
        duration = self.tts._estimate_audio_duration(audio_data)
        assert 0.9 < duration < 1.1  # 大约1
    
    def test_cache_eviction(self):
        """测试缓存清理"""
        # 设置小的缓存限制
        self.tts.cache_size_limit = 2
        
        # 添加3个缓存条
        self.tts.synthesize_with_error_handling("Text 1")
        self.tts.synthesize_with_error_handling("Text 2")
        self.tts.synthesize_with_error_handling("Text 3")
        
        # 验证缓存大小不超过限
        cache_stats = self.tts.get_cache_stats()
        assert cache_stats["entries"] <= 2
    
    @pytest.mark.parametrize("platform_name", ["posix", "nt"])
    def test_play_audio_with_error_handling(self, platform_name):
        """Keep real playback orchestration and replace each platform's device API."""
        import io
        import os
        import sys
        import wave
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import Mock

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\x00\x00" * 16000)
        audio_data = buffer.getvalue()
        paths = []
        path_type = type(Path())
        fail_playback = False

        def play(path, *args, **kwargs):
            path = path_type(path)
            paths.append(path)
            assert path.read_bytes() == audio_data
            if fail_playback:
                raise OSError("No audio device")

        winsound = SimpleNamespace(PlaySound=Mock(side_effect=play), SND_FILENAME=131072)
        with patch.dict(sys.modules, {"winsound": winsound}), patch(
            "os.name", platform_name
        ), patch(
            "subprocess.run", side_effect=lambda command, **kwargs: play(command[1])
        ) as run:
            playback = winsound.PlaySound if platform_name == "nt" else run
            unused = run if platform_name == "nt" else winsound.PlaySound
            for invalid in (b"", None):
                with pytest.raises(AudioOutputError) as error:
                    self.tts.play_audio_with_error_handling(invalid)
                assert error.value.error_info.code == "AUDIO_PLAYBACK_ERROR"
            playback.assert_not_called()

            assert self.tts.play_audio_with_error_handling(audio_data) is True
            playback.assert_called_once()
            assert not paths[-1].exists()

            playback.reset_mock()
            fail_playback = True
            with pytest.raises(AudioOutputError) as error:
                self.tts.play_audio_with_error_handling(audio_data)
            assert error.value.error_info.code == "AUDIO_PLAYBACK_ERROR"
            playback.assert_called_once()
            unused.assert_not_called()
            assert not paths[-1].exists()


class TestChatServiceTTSIntegration:
    """测试ChatService与TTS的集成。"""
    
    @pytest.fixture(autouse=True)
    def llm_http(self):
        with patch("chatterpal.core.llm.openai.requests.post") as post:
            post.return_value.json.return_value = {
                "choices": [{"message": {"content": "Test response"}}]
            }
            yield

    def setup_method(self):
        """设置测试环境"""
        self.tts_enhanced = MockTTSWithErrorHandling()
        self.tts_basic = MockTTSBasic()
        self.llm = OpenAILLM({"api_key": "test_key"})
    
    def test_chat_service_with_enhanced_tts(self):
        """测试ChatService使用增强TTS"""
        chat_service = ChatService(tts=self.tts_enhanced, llm=self.llm)
        
        # 测试成功的语音合
        audio_data = chat_service._synthesize_with_error_handling("Hello")
        assert audio_data is not None
        assert b"Hello" in audio_data
    
    def test_chat_service_with_basic_tts(self):
        """测试ChatService使用基本TTS"""
        chat_service = ChatService(tts=self.tts_basic, llm=self.llm)
        
        # 测试成功的语音合
        audio_data = chat_service._synthesize_with_error_handling("Hello")
        assert audio_data is not None
        assert audio_data == b"basic tts audio data"
        assert self.tts_basic.call_count == 1
    
    def test_chat_service_tts_failure_graceful(self):
        """测试TTS失败时的优雅处理"""
        self.tts_enhanced.should_fail = True
        self.tts_enhanced.fail_count = 10  # 确保所有重试都失败
        
        chat_service = ChatService(tts=self.tts_enhanced, llm=self.llm)
        
        # TTS失败不应该抛出异常,而是返回None
        audio_data = chat_service._synthesize_with_error_handling("Hello")
        assert audio_data is None
    
    def test_process_chat_with_tts_error(self):
        """测试process_chat中的TTS错误处理"""
        self.tts_enhanced.should_fail = True
        self.tts_enhanced.fail_count = 10
        
        chat_service = ChatService(tts=self.tts_enhanced, llm=self.llm)
        
        # 即使TTS失败,对话也应该继续
        result = chat_service.process_chat(text_input="Hello", use_text_input=True)
        audio_output, chat_history = result
        
        # 应该有文本回复,但没有音
        assert len(chat_history) > 0
        assert "Test response" in str(chat_history)
        assert audio_output is None
        assert chat_history == [["Hello", "Test response"]]


class TestTTSCacheManagement:
    """测试TTS缓存管理"""
    
    def setup_method(self):
        """设置测试环境"""
        self.tts = MockTTSWithErrorHandling({
            "enable_cache": True,
            "cache_size_limit": 5,
            "cache_ttl": 1  # 1秒过期时
        })
    
    def test_cache_expiration(self, monkeypatch):
        """测试缓存过期"""
        clock = [1000.0]
        monkeypatch.setattr("chatterpal.core.tts.base.time.time", lambda: clock[0])
        # 添加缓存条目
        result1 = self.tts.synthesize_with_error_handling("Expire test")
        assert result1.cached is False
        
        # 立即再次请求,应该从缓存获取
        result2 = self.tts.synthesize_with_error_handling("Expire test")
        assert result2.cached is True
        
        # Advance only the clock used by cache expiry.
        clock[0] += 1.1
        
        # 再次请求,应该重新合成
        result3 = self.tts.synthesize_with_error_handling("Expire test")
        assert result3.cached is False
    
    def test_cache_stats(self):
        """测试缓存统计"""
        # 添加一些缓存条目
        self.tts.synthesize_with_error_handling("Stats test 1")
        self.tts.synthesize_with_error_handling("Stats test 2")
        
        stats = self.tts.get_cache_stats()
        assert stats["entries"] == 2
        assert stats["total_size_bytes"] > 0
    
    def test_clear_cache(self):
        """测试清空缓存"""
        # 添加缓存条目
        self.tts.synthesize_with_error_handling("Clear test")
        
        stats_before = self.tts.get_cache_stats()
        assert stats_before["entries"] > 0
        
        # 清空缓存
        self.tts.clear_cache()
        
        stats_after = self.tts.get_cache_stats()
        assert stats_after["entries"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])








