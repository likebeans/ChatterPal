"""Tests for audio validation, format conversion, and signal processing."""

import io
import wave

import numpy as np
import pytest

from chatterpal.utils.audio import (
    AudioProcessor,
    AudioQualityLevel,
)


class TestAudioProcessor:
    """AudioProcessor 测试"""

    def setup_method(self):
        """测试前准备"""
        self.processor = AudioProcessor(sample_rate=16000)
        self.rng = np.random.default_rng(2026)
        
    def test_init(self):
        """测试初始化"""
        processor = AudioProcessor(sample_rate=22050)
        assert processor.sample_rate == 22050
        assert processor.min_duration == 1.0
        assert processor.max_duration == 60.0
        
    def test_validate_audio_input_valid_numpy_array(self):
        """测试验证有效的numpy数组音频数据"""
        # Create a two-second sine wave.
        duration = 2.0
        sample_count = int(duration * self.processor.sample_rate)
        t = np.linspace(0, duration, sample_count)
        audio_data = 0.5 * np.sin(2 * np.pi * 440 * t)  # 440Hz正弦
        
        result = self.processor.validate_audio_input(audio_data)
        
        assert result.is_valid == True
        assert abs(result.duration - duration) < 0.1
        assert result.quality_level in [AudioQualityLevel.FAIR, AudioQualityLevel.GOOD, AudioQualityLevel.EXCELLENT]
        assert len(result.issues) == 0
        
    def test_validate_audio_input_too_short(self):
        """测试验证过短的音频。"""
        # Half a second is below the one-second minimum.
        duration = 0.5
        sample_count = int(duration * self.processor.sample_rate)
        audio_data = self.rng.normal(0, 0.1, sample_count)
        
        result = self.processor.validate_audio_input(audio_data)
        
        assert result.is_valid == False
        assert "音频时长过短" in str(result.issues)
        
    def test_validate_audio_input_too_long(self):
        """测试验证过长的音频。"""
        # Sixty-five seconds exceeds the sixty-second maximum.
        duration = 65.0
        sample_count = int(duration * self.processor.sample_rate)
        audio_data = self.rng.normal(0, 0.1, sample_count)
        
        result = self.processor.validate_audio_input(audio_data)
        
        assert result.is_valid == False
        assert "音频时长过长" in str(result.issues)
        
    def test_validate_audio_input_empty_data(self):
        """测试验证空音频数据。"""
        audio_data = np.array([])
        
        result = self.processor.validate_audio_input(audio_data)
        
        assert result.is_valid == False
        assert "音频数据为空" in str(result.issues)
        
    def test_validate_audio_input_silent_audio(self):
        """测试验证静音音频"""
        # 创建几乎全是静音的音
        duration = 2.0
        sample_count = int(duration * self.processor.sample_rate)
        audio_data = np.zeros(sample_count)
        # 添加少量噪音避免完全
        audio_data += self.rng.normal(0, 0.001, sample_count)
        
        result = self.processor.validate_audio_input(audio_data)
        
        assert result.is_valid == False
        assert any("静音比例过高" in issue for issue in result.issues)
        
    def test_validate_audio_input_low_volume(self):
        """测试验证音量过低的音频。"""
        duration = 2.0
        sample_count = int(duration * self.processor.sample_rate)
        # 创建音量很低的音
        audio_data = self.rng.normal(0, 0.0005, sample_count)  # 更低的音
        
        result = self.processor.validate_audio_input(audio_data)
        
        assert result.is_valid == False
        assert any("音量过低" in issue for issue in result.issues)
        
    def test_validate_audio_input_clipped_audio(self):
        """测试验证削波失真的音频。"""
        duration = 2.0
        sample_count = int(duration * self.processor.sample_rate)
        # 创建削波的音
        audio_data = np.ones(sample_count) * 0.98
        
        result = self.processor.validate_audio_input(audio_data)
        
        assert result.is_valid == False
        assert any("削波失真" in issue for issue in result.issues)
        
    def test_validate_audio_input_bytes_data(self):
        """测试验证字节格式的音频数据。"""
        # 创建16位PCM数据
        duration = 2.0
        sample_count = int(duration * self.processor.sample_rate)
        audio_float = 0.5 * np.sin(2 * np.pi * 440 * np.linspace(0, duration, sample_count))
        audio_int16 = (audio_float * 32767).astype(np.int16)
        audio_bytes = audio_int16.tobytes()
        
        result = self.processor.validate_audio_input(audio_bytes)
        
        assert result.is_valid == True
        assert abs(result.duration - duration) < 0.1
        
    def test_validate_audio_input_invalid_format(self):
        """测试验证无效格式的音频数据"""
        invalid_data = "这不是音频数据"
        
        result = self.processor.validate_audio_input(invalid_data)
        
        assert result.is_valid == False
        assert any("无法识别" in issue for issue in result.issues)
        
    def test_convert_audio_format_enhanced_pcm_to_wav(self):
        """测试PCM到WAV格式转换"""
        # 创建测试音频
        duration = 1.0
        sample_count = int(duration * self.processor.sample_rate)
        audio_float = 0.5 * np.sin(2 * np.pi * 440 * np.linspace(0, duration, sample_count))
        audio_int16 = (audio_float * 32767).astype(np.int16)
        pcm_bytes = audio_int16.tobytes()
        
        wav_bytes = self.processor.convert_audio_format_enhanced(
            pcm_bytes, 
            source_format="pcm", 
            target_format="wav"
        )
        
        assert isinstance(wav_bytes, bytes)
        assert len(wav_bytes) > len(pcm_bytes)
        with wave.open(io.BytesIO(wav_bytes), "rb") as audio:
            assert audio.getnchannels() == 1
            assert audio.getsampwidth() == 2
            assert audio.getframerate() == self.processor.sample_rate
            assert audio.getnframes() == sample_count
            decoded = np.frombuffer(audio.readframes(sample_count), dtype="<i2")
        np.testing.assert_allclose(decoded, audio_int16, atol=1)
        
    def test_convert_audio_format_enhanced_numpy_to_pcm(self):
        """测试numpy数组到PCM格式转换"""
        duration = 1.0
        sample_count = int(duration * self.processor.sample_rate)
        audio_data = 0.5 * np.sin(2 * np.pi * 440 * np.linspace(0, duration, sample_count))
        
        pcm_bytes = self.processor.convert_audio_format_enhanced(
            audio_data,
            target_format="pcm"
        )
        
        assert isinstance(pcm_bytes, bytes)
        assert len(pcm_bytes) == len(audio_data) * 2
        expected = (audio_data.astype(np.float32) * 32767).astype("<i2")
        np.testing.assert_array_equal(np.frombuffer(pcm_bytes, dtype="<i2"), expected)
        
    def test_convert_audio_format_enhanced_with_resampling(self):
        """测试带重采样的格式转换。"""
        duration = 1.0
        source_sr = 22050
        target_sr = 16000
        sample_count = int(duration * source_sr)
        audio_data = 0.5 * np.sin(2 * np.pi * 440 * np.linspace(0, duration, sample_count))
        
        pcm_bytes = self.processor.convert_audio_format_enhanced(
            audio_data,
            source_sample_rate=source_sr,
            target_sample_rate=target_sr,
            target_format="pcm"
        )
        
        expected_length = int(duration * target_sr) * 2  # 16位PCM
        assert abs(len(pcm_bytes) - expected_length) <= 4
        decoded = np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0
        assert np.max(np.abs(decoded)) > 0.4
        spectrum = np.abs(np.fft.rfft(decoded))
        frequencies = np.fft.rfftfreq(len(decoded), 1 / target_sr)
        assert frequencies[np.argmax(spectrum)] == pytest.approx(440, abs=1)
        
    def test_get_audio_duration_from_data_numpy(self):
        """测试从numpy数组获取音频时长"""
        duration = 3.5
        sample_count = int(duration * self.processor.sample_rate)
        audio_data = self.rng.normal(0, 0.1, sample_count)
        
        calculated_duration = self.processor.get_audio_duration_from_data(audio_data)
        
        assert abs(calculated_duration - duration) < 0.01
        
    def test_get_audio_duration_from_data_bytes(self):
        """测试从字节数据获取音频时长。"""
        duration = 2.5
        sample_count = int(duration * self.processor.sample_rate)
        audio_int16 = self.rng.integers(-32768, 32767, sample_count, dtype=np.int16)
        audio_bytes = audio_int16.tobytes()
        
        calculated_duration = self.processor.get_audio_duration_from_data(audio_bytes)
        
        assert abs(calculated_duration - duration) < 0.01
        
    def test_detect_voice_activity(self):
        """测试语音活动检测。"""
        # 创建包含语音和静音段的音
        sample_rate = self.processor.sample_rate
        
        # 1秒静
        silence = np.zeros(sample_rate)
        
        # 1秒语音(正弦波)
        t = np.linspace(0, 1, sample_rate)
        voice = 0.5 * np.sin(2 * np.pi * 440 * t)
        
        # 秒静
        audio_data = np.concatenate([silence, voice, silence])
        
        vad_result = self.processor.detect_voice_activity(audio_data, frame_length=1024)
        
        # 检查中间部分检测到语音活动
        middle_frames = len(vad_result) // 3
        voice_detected = np.sum(vad_result[middle_frames:2*middle_frames])
        
        assert voice_detected > 0  # 应该检测到一些语音活
        
    def test_assess_audio_quality(self):
        """测试音频质量评估"""
        # 创建高质量音
        duration = 2.0
        sample_count = int(duration * self.processor.sample_rate)
        t = np.linspace(0, duration, sample_count)
        high_quality_audio = 0.7 * np.sin(2 * np.pi * 440 * t)
        
        metadata = {}
        quality = self.processor._assess_audio_quality(high_quality_audio, metadata)
        
        assert quality in [AudioQualityLevel.FAIR, AudioQualityLevel.GOOD, AudioQualityLevel.EXCELLENT]
        assert 'snr' in metadata
        assert 'spectral_quality' in metadata
        assert 'dynamic_range' in metadata
        
    def test_calculate_snr(self):
        """测试信噪比计算。"""
        # 创建纯信
        duration = 1.0
        sample_count = int(duration * self.processor.sample_rate)
        t = np.linspace(0, duration, sample_count)
        clean_signal = 0.5 * np.sin(2 * np.pi * 440 * t)
        
        snr = self.processor._calculate_snr(clean_signal)
        
        assert snr > 10  # 纯信号应该有较高的SNR
        
    def test_calculate_silence_ratio(self):
        """测试静音比例计算"""
        # 创建50%静音的音
        sample_count = 1000
        audio_data = np.zeros(sample_count)
        audio_data[:500] = 0.5  # 前一半有信号
        
        silence_ratio = self.processor._calculate_silence_ratio(audio_data)
        
        assert abs(silence_ratio - 0.5) < 0.1  # 应该接近50%


class TestAudioProcessorIntegration:
    """AudioProcessor 集成测试"""
    
    def setup_method(self):
        """测试前准备。"""
        self.processor = AudioProcessor()
        
    def test_full_audio_processing_pipeline(self):
        """测试完整的音频处理流程。"""
        # 1. 创建测试音频
        duration = 3.0
        sample_count = int(duration * self.processor.sample_rate)
        t = np.linspace(0, duration, sample_count)
        original_audio = 0.6 * np.sin(2 * np.pi * 440 * t)
        
        # 2. 验证音频
        validation_result = self.processor.validate_audio_input(original_audio)
        assert validation_result.is_valid
        
        # 3. 转换格式
        wav_bytes = self.processor.convert_audio_format_enhanced(
            original_audio, target_format="wav"
        )
        assert isinstance(wav_bytes, bytes)
        with wave.open(io.BytesIO(wav_bytes), "rb") as audio:
            assert audio.getframerate() == self.processor.sample_rate
            assert audio.getnframes() == sample_count
            decoded = np.frombuffer(audio.readframes(sample_count), dtype="<i2") / 32768.0
        np.testing.assert_allclose(decoded, original_audio, atol=2 / 32768)

        # 4. 获取时长
        calculated_duration = self.processor.get_audio_duration_from_data(original_audio)
        assert abs(calculated_duration - duration) < 0.1
        
        # 5. 语音活动检
        vad_result = self.processor.detect_voice_activity(original_audio)
        assert len(vad_result) > 0
        
    def test_error_handling_robustness(self):
        """测试错误处理的健壮性。"""
        # 测试各种异常情况
        test_cases = [
            None,
            [],
            "",
            {"invalid": "data"},
            np.array([np.nan, np.inf, -np.inf])
        ]
        
        for invalid_data in test_cases:
            result = self.processor.validate_audio_input(invalid_data)
            assert result.is_valid == False
            assert len(result.issues) > 0


if __name__ == "__main__":
    pytest.main([__file__])








