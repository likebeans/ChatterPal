"""Tests for enhanced synthesis, caching, and playback through TTSBase."""

import io
import time
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from chatterpal.core.errors import SpeechSynthesisError
from chatterpal.core.tts.base import (
    CacheEntry,
    PlaybackState,
    TTSBase,
    TTSCacheError,
    TTSError,
    TTSPlaybackError,
    TTSResult,
)


class MockTTS(TTSBase):
    """A deterministic synthesis boundary for exercising the real base class."""

    def __init__(self, config=None):
        super().__init__(config)
        self.mock_audio_data = self._create_test_audio_data()
        self.should_fail = False
        self.synthesis_calls = []

    def synthesize(self, text: str, **kwargs) -> bytes:
        self.synthesis_calls.append((text, kwargs))
        if self.should_fail:
            raise TTSError("模拟合成失败")
        return self.mock_audio_data

    def synthesize_to_file(self, text: str, output_path: str, **kwargs) -> bool:
        with open(output_path, "wb") as output:
            output.write(self.synthesize(text, **kwargs))
        return True

    @staticmethod
    def _create_test_audio_data():
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


def cache_result(audio_data=b"test_audio_data", metadata=None):
    """Build the result accepted by the current _save_to_cache interface."""
    return TTSResult(
        text="测试文本",
        audio_data=audio_data,
        duration=len(audio_data) / 32000,
        format="wav",
        sample_rate=16000,
        cached=False,
        synthesis_time=0.01,
        metadata=metadata or {},
    )


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    """Do not spend wall time on the production retry backoff."""
    monkeypatch.setattr("chatterpal.core.tts.base.time.sleep", lambda seconds: None)


class TestTTSEnhanced:
    """Exercise synthesis and cache behavior using the current base API."""

    def setup_method(self):
        self.tts = MockTTS({
            "enable_cache": True,
            "cache_size_limit": 10,
            "cache_ttl": 3600,
            "auto_play": False,
            "playback_volume": 0.8,
        })

    def test_tts_result_dataclass(self):
        result = TTSResult(
            text="测试文本",
            audio_data=b"test_audio",
            duration=2.5,
            format="wav",
            sample_rate=16000,
            cached=False,
            synthesis_time=1.2,
            metadata={"test": "data"},
        )
        assert result.text == "测试文本"
        assert result.audio_data == b"test_audio"
        assert result.duration == 2.5
        assert result.format == "wav"
        assert result.sample_rate == 16000
        assert result.cached is False
        assert result.synthesis_time == 1.2
        assert result.metadata["test"] == "data"

    def test_cache_entry_dataclass(self):
        entry = CacheEntry(
            key="test_key",
            audio_data=b"test_audio",
            metadata={"test": "data"},
            created_at=time.time(),
            access_count=1,
            last_accessed=time.time(),
        )
        assert entry.key == "test_key"
        assert entry.audio_data == b"test_audio"
        assert entry.metadata["test"] == "data"
        assert entry.access_count == 1

    def test_playback_state_enum(self):
        assert PlaybackState.STOPPED.value == "stopped"
        assert PlaybackState.PLAYING.value == "playing"
        assert PlaybackState.PAUSED.value == "paused"
        assert PlaybackState.ERROR.value == "error"

    def test_synthesize_enhanced_success(self):
        result = self.tts.synthesize_with_error_handling("测试文本")
        assert isinstance(result, TTSResult)
        assert result.text == "测试文本"
        assert result.audio_data == self.tts.mock_audio_data
        assert result.cached is False
        assert result.synthesis_time >= 0
        assert result.duration == pytest.approx(1.0)
        assert self.tts.synthesis_calls == [("测试文本", {})]

    def test_synthesize_enhanced_with_cache(self):
        text = "测试缓存文本"
        result1 = self.tts.synthesize_with_error_handling(text)
        result2 = self.tts.synthesize_with_error_handling(text)
        assert result1.cached is False
        assert result2.cached is True
        assert result2.audio_data == result1.audio_data
        assert self.tts.synthesis_calls == [(text, {})]

    def test_synthesize_enhanced_cache_disabled(self):
        self.tts.enable_cache = False
        result1 = self.tts.synthesize_with_error_handling("测试文本")
        result2 = self.tts.synthesize_with_error_handling("测试文本")
        assert result1.cached is False
        assert result2.cached is False
        assert len(self.tts.synthesis_calls) == 2
        assert self.tts.get_cache_stats()["entries"] == 0

    def test_synthesize_enhanced_failure(self):
        # ChatService catches SpeechSynthesisError from the current base API.
        self.tts.should_fail = True
        with pytest.raises(SpeechSynthesisError) as error:
            self.tts.synthesize_with_error_handling("测试文本")
        assert error.value.error_info.code == "TTS_SERVICE_ERROR"
        assert self.tts.get_cache_stats()["entries"] == 0
        assert len(self.tts.synthesis_calls) == 3

    def test_generate_cache_key(self):
        key1 = self.tts._generate_cache_key("测试文本", {"voice": "test"})
        key2 = self.tts._generate_cache_key("测试文本", {"voice": "test"})
        key3 = self.tts._generate_cache_key("测试文本", {"voice": "other"})
        assert key1 == key2
        assert key1 != key3
        assert len(key1) == 32

    def test_cache_operations(self):
        text = "test_key"
        audio_data = b"test_audio_data"
        metadata = {"test": "metadata"}
        self.tts._save_to_cache(text, cache_result(audio_data, metadata), {})
        result = self.tts._get_from_cache(text, {})
        assert result is not None
        assert result.cached is True
        assert result.audio_data == audio_data
        assert result.metadata == metadata
        entry = self.tts._cache[self.tts._generate_cache_key(text, {})]
        assert entry.access_count == 2

    def test_cache_expiration(self, monkeypatch):
        self.tts.cache_ttl = 0.1
        clock = [1000.0]
        monkeypatch.setattr("chatterpal.core.tts.base.time.time", lambda: clock[0])
        self.tts._save_to_cache("test_key", cache_result(), {})
        assert self.tts._get_from_cache("test_key", {}) is not None
        clock[0] += 0.2
        assert self.tts._get_from_cache("test_key", {}) is None

    def test_cache_size_limit(self):
        self.tts.cache_size_limit = 3
        for index in range(5):
            self.tts._save_to_cache(
                f"test_key_{index}",
                cache_result(f"test_audio_data_{index}".encode(), {"index": index}),
                {},
            )
        assert len(self.tts._cache) <= self.tts.cache_size_limit

    def test_clear_cache(self):
        for index in range(3):
            self.tts._save_to_cache(f"test_key_{index}", cache_result(), {})
        assert len(self.tts._cache) == 3
        self.tts.clear_cache()
        assert len(self.tts._cache) == 0

    def test_get_cache_stats(self):
        for index in range(3):
            self.tts._save_to_cache(f"test_key_{index}", cache_result(), {})
        stats = self.tts.get_cache_stats()
        assert stats["entries"] == 3
        assert stats["total_size_bytes"] > 0
        assert stats["total_access_count"] >= 3
        assert "oldest_entry" in stats
        assert "newest_entry" in stats

    def test_disk_cache_operations(self, tmp_path):
        """Configured persistence should survive clearing only the memory cache."""
        tts = MockTTS({"cache_dir": str(tmp_path / "cache")})
        first = tts.synthesize_with_error_handling("磁盘缓存")
        assert first.cached is False
        assert list((tmp_path / "cache").glob("*.cache"))
        tts._cache.clear()
        second = tts.synthesize_with_error_handling("磁盘缓存")
        assert second.cached is True
        assert second.audio_data == first.audio_data
        assert second.metadata == first.metadata
        assert len(tts.synthesis_calls) == 1

    def test_playback_state_management(self):
        assert self.tts.get_playback_state() == PlaybackState.STOPPED
        self.tts._playback_state = PlaybackState.PLAYING
        assert self.tts.get_playback_state() == PlaybackState.PLAYING
        self.tts._playback_state = PlaybackState.PAUSED
        assert self.tts.get_playback_state() == PlaybackState.PAUSED

    def test_playback_volume_control(self):
        self.tts.set_playback_volume(0.5)
        assert self.tts.get_playback_volume() == 0.5
        self.tts.set_playback_volume(-0.1)
        assert self.tts.get_playback_volume() == 0.0
        self.tts.set_playback_volume(1.5)
        assert self.tts.get_playback_volume() == 1.0

    def test_playback_callbacks(self):
        callback_events = []
        callback = callback_events.append
        self.tts.add_playback_callback(callback)
        self.tts._notify_playback_callbacks("test_event")
        assert "test_event" in callback_events
        self.tts.remove_playback_callback(callback)
        callback_events.clear()
        self.tts._notify_playback_callbacks("another_event")
        assert callback_events == []

    def test_play_with_pygame(self):
        pygame = MagicMock()
        sound = pygame.mixer.Sound.return_value
        with patch.dict("sys.modules", {"pygame": pygame}):
            assert self.tts._play_with_pygame(self.tts.mock_audio_data) is True
        assert self.tts.get_playback_state() == PlaybackState.PLAYING
        sound.play.assert_called_once_with()
        sound.set_volume.assert_called_once_with(0.8)
        audio_buffer = pygame.mixer.Sound.call_args.args[0]
        assert audio_buffer.getvalue() == self.tts.mock_audio_data

    def test_play_audio_fallback(self):
        with patch.dict("sys.modules", {"pygame": None, "playsound": None}):
            with patch("subprocess.run", side_effect=OSError("No audio device")) as run:
                assert self.tts.play_audio(self.tts.mock_audio_data) is False
        run.assert_called_once()
        assert self.tts.get_playback_state() == PlaybackState.ERROR

    def test_stop_playback(self):
        audio = MagicMock()
        self.tts._current_audio = audio
        self.tts._playback_state = PlaybackState.PLAYING
        assert self.tts.stop_playback() is True
        assert self.tts.get_playback_state() == PlaybackState.STOPPED
        assert self.tts._current_audio is None
        audio.stop.assert_called_once_with()

    def test_pause_resume_playback(self):
        audio = MagicMock()
        self.tts._current_audio = audio
        self.tts._playback_state = PlaybackState.PLAYING
        assert self.tts.pause_playback() is True
        assert self.tts.get_playback_state() == PlaybackState.PAUSED
        audio.pause.assert_called_once_with()
        assert self.tts.resume_playback() is True
        assert self.tts.get_playback_state() == PlaybackState.PLAYING
        audio.unpause.assert_called_once_with()


class TestTTSErrorTypes:
    """Test TTS error inheritance and messages."""

    def test_tts_error_inheritance(self):
        assert issubclass(TTSCacheError, TTSError)
        assert issubclass(TTSPlaybackError, TTSError)

    def test_tts_cache_error(self):
        error = TTSCacheError("缓存错误")
        assert str(error) == "缓存错误"
        assert isinstance(error, TTSError)

    def test_tts_playback_error(self):
        error = TTSPlaybackError("播放错误")
        assert str(error) == "播放错误"
        assert isinstance(error, TTSError)


class TestTTSIntegration:
    """Exercise the base synthesis pipeline and playback integration offline."""

    def test_full_enhanced_synthesis_pipeline(self):
        tts = MockTTS({"enable_cache": True, "cache_size_limit": 5, "auto_play": False})
        text = "这是一个完整的测试文本"
        result = tts.synthesize_with_error_handling(text)
        assert result.text == text
        assert len(result.audio_data) > 0
        assert result.cached is False
        assert result.synthesis_time >= 0
        assert result.duration == pytest.approx(1.0)
        result2 = tts.synthesize_with_error_handling(text)
        assert result2.cached is True
        assert result2.audio_data == result.audio_data
        assert tts.get_cache_stats()["entries"] >= 1
        assert len(tts.synthesis_calls) == 1
        assert tts.get_playback_state() == PlaybackState.STOPPED
        tts.set_playback_volume(0.7)
        assert tts.get_playback_volume() == 0.7

    def test_auto_play_functionality(self):
        tts = MockTTS({"auto_play": True, "playback_volume": 0.5})
        pygame = MagicMock()
        sound = pygame.mixer.Sound.return_value
        with patch.dict("sys.modules", {"pygame": pygame}):
            result = tts.synthesize_with_error_handling("测试自动播放")
        sound.play.assert_called_once_with()
        sound.set_volume.assert_called_once_with(0.5)
        assert pygame.mixer.Sound.call_args.args[0].getvalue() == result.audio_data
        assert tts.get_playback_state() == PlaybackState.PLAYING

    def test_error_handling_robustness(self):
        tts = MockTTS()
        for text in ["", None, "a" * 10000]:
            with pytest.raises(SpeechSynthesisError) as error:
                tts.synthesize_with_error_handling(text)
            assert error.value.error_info.code == "TTS_SERVICE_ERROR"
        assert tts.synthesis_calls == []
        assert tts.get_cache_stats()["entries"] == 0


if __name__ == "__main__":
    pytest.main([__file__])
