"""
增强ASR功能测试
"""

import time
import wave

import numpy as np
import pytest

from chatterpal.core.asr.base import (
    ASRBase, 
    ASRError, 
    ASRQualityError, 
    ASRConfidenceError, 
    ASRTimeoutError,
    ASRResult,
    ConfidenceLevel
)


class MockASR(ASRBase):
    """用于测试的模拟ASR实现"""
    
    def __init__(self, config=None):
        super().__init__(config)
        self.mock_result = "测试识别结果"
        self.should_fail = False
        self.processing_delay = 0.0
        
    def recognize(self, audio_data: bytes, **kwargs):
        time.sleep(self.processing_delay)
        
        if self.should_fail:
            raise ASRError("模拟识别失败")
        
        return self.mock_result
    
    def recognize_file(self, audio_path: str, **kwargs):
        time.sleep(self.processing_delay)
        
        if self.should_fail:
            raise ASRError("模拟识别失败")
        
        return self.mock_result


class TestASREnhanced:
    """增强ASR功能测试类"""
    
    def setup_method(self):
        """测试前准备"""
        self.asr = MockASR({
            "min_confidence": 0.6,
            "min_audio_quality": 0.4,
            "max_processing_time": 5.0,
            "enable_quality_check": True,
            "enable_confidence_check": True
        })
        
    def test_asr_result_dataclass(self):
        """测试ASRResult数据类"""
        result = ASRResult(
            text="测试文本",
            confidence=0.8,
            confidence_level=ConfidenceLevel.HIGH,
            audio_quality_score=0.9,
            processing_time=1.5,
            metadata={"test": "data"}
        )
        
        assert result.text == "测试文本"
        assert result.confidence == 0.8
        assert result.confidence_level == ConfidenceLevel.HIGH
        assert result.audio_quality_score == 0.9
        assert result.processing_time == 1.5
        assert result.metadata["test"] == "data"
        
    def test_confidence_level_enum(self):
        """测试置信度级别枚举"""
        assert ConfidenceLevel.HIGH.value == "high"
        assert ConfidenceLevel.MEDIUM.value == "medium"
        assert ConfidenceLevel.LOW.value == "low"
        assert ConfidenceLevel.VERY_LOW.value == "very_low"
        
    def test_recognize_enhanced_success(self):
        """测试增强识别成功情况"""
        # 创建测试音频数据
        audio_data = self._create_test_audio_bytes()
        
        result = self.asr.recognize_enhanced(audio_data)
        
        assert isinstance(result, ASRResult)
        assert result.text == "测试识别结果"
        assert result.confidence > 0
        assert result.audio_quality_score >= 0
        assert result.processing_time > 0
        
    def test_recognize_enhanced_quality_check_disabled(self):
        """测试禁用质量检查的增强识别"""
        self.asr.enable_quality_check = False
        self.asr.enable_confidence_check = False  # 也禁用置信度检查以避免干扰
        audio_data = self._create_test_audio_bytes()
        
        result = self.asr.recognize_enhanced(audio_data)
        
        assert result.audio_quality_score == 1.0  # 禁用质量检查时默认为1.0
        
    def test_recognize_enhanced_confidence_check_disabled(self):
        """测试禁用置信度检查的增强识别"""
        self.asr.enable_confidence_check = False
        self.asr.mock_result = "a"  # 很短的结果,置信度会很低
        audio_data = self._create_test_audio_bytes()
        
        result = self.asr.recognize_enhanced(audio_data)
        
        # 即使置信度低,也不应该抛出异常
        assert result.text == "a"
        
    def test_recognize_enhanced_quality_error(self):
        """Reject a clipped constant signal through the real quality assessor."""
        audio_data = np.full(16000, 32000, dtype="<i2").tobytes()
        with pytest.raises(ASRQualityError) as exc_info:
            self.asr.recognize_enhanced(audio_data)
        assert "音频质量过低" in str(exc_info.value)
            
    def test_recognize_enhanced_confidence_error(self):
        """Reject a repetitive transcript with mostly silent audio."""
        self.asr.mock_result = "aaaaaaaaaa"
        tone = np.frombuffer(self._create_test_audio_bytes(duration=0.1), dtype="<i2")
        audio_data = np.concatenate([tone, np.zeros(14400, dtype="<i2")]).tobytes()
        with pytest.raises(ASRConfidenceError) as exc_info:
            self.asr.recognize_enhanced(audio_data)
        assert "识别置信度过低" in str(exc_info.value)
        
    def test_recognize_enhanced_timeout_error(self, monkeypatch):
        """Check the real elapsed-time guard without waiting six seconds."""
        clock = [1000.0]

        def advance(seconds):
            clock[0] += seconds

        monkeypatch.setattr(time, "time", lambda: clock[0])
        monkeypatch.setattr(time, "sleep", advance)
        self.asr.processing_delay = 6.0
        audio_data = self._create_test_audio_bytes()
        with pytest.raises(ASRTimeoutError) as exc_info:
            self.asr.recognize_enhanced(audio_data)
        assert "识别超时" in str(exc_info.value)
        
    def test_recognize_enhanced_exception_handling(self):
        """测试增强识别的异常处理"""
        self.asr.should_fail = True
        audio_data = self._create_test_audio_bytes()
        
        result = self.asr.recognize_enhanced(audio_data)
        
        assert result.text is None
        assert result.confidence == 0.0
        assert result.confidence_level == ConfidenceLevel.VERY_LOW
        assert "error" in result.metadata
        
    def test_assess_audio_quality_with_audioprocessor(self):
        """测试使用AudioProcessor的音频质量评估"""
        audio_data = self._create_test_audio_bytes()
        metadata = {}
        
        quality_score = self.asr._assess_audio_quality(audio_data, metadata)
        assert 0 <= quality_score <= 1
        assert isinstance(metadata, dict)
        assert metadata["sample_count"] == 16000
        assert metadata["duration"] == pytest.approx(1.0)
        assert "quality_score" in metadata
        assert "snr" in metadata
        assert "validation_issues" in metadata
            
    def test_simple_audio_quality_check(self):
        """测试简化的音频质量检查"""
        audio_data = self._create_test_audio_bytes()
        metadata = {}
        
        quality_score = self.asr._simple_audio_quality_check(audio_data, metadata)
        
        assert 0 <= quality_score <= 1
        assert "data_size" in metadata
        
    def test_simple_audio_quality_check_file(self, tmp_path):
        """Use a real WAV for the file-size fallback quality check."""
        path = tmp_path / "input.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(self._create_test_audio_bytes())
        metadata = {}
        quality_score = self.asr._simple_audio_quality_check(str(path), metadata)
        assert 0 <= quality_score <= 1
        assert metadata["file_size"] == path.stat().st_size
            
    def test_calculate_confidence_empty_text(self):
        """测试空文本的置信度计算"""
        metadata = {}
        confidence = self.asr._calculate_confidence(None, metadata)
        assert confidence == 0.0
        
        confidence = self.asr._calculate_confidence("", metadata)
        assert confidence == 0.0
        
    def test_calculate_confidence_normal_text(self):
        """测试正常文本的置信度计算"""
        metadata = {"quality_score": 5, "snr": 12, "silence_ratio": 0.3}
        confidence = self.asr._calculate_confidence("这是一段正常的测试文本", metadata)
        
        assert 0 <= confidence <= 1
        assert confidence > 0.5  # 正常文本应该有较高置信度
        
    def test_calculate_confidence_poor_quality(self):
        """测试低质量音频的置信度计算"""
        metadata = {"quality_score": 1, "snr": 3, "silence_ratio": 0.9}
        confidence = self.asr._calculate_confidence("测试文本", metadata)
        
        assert confidence < 0.5  # 低质量应该导致低置信度
        
    def test_has_recognition_errors(self):
        """测试识别错误模式检查"""
        # 正常文本
        assert not self.asr._has_recognition_errors("这是正常的文本")
        
        # 重复字符
        assert self.asr._has_recognition_errors("aaaaaa")
        
        # 过多标点符号
        assert self.asr._has_recognition_errors("文本...???")
        
        # 过长英文字符串
        assert self.asr._has_recognition_errors("abcdefghijklmnop")
        
        # 过多数字
        assert self.asr._has_recognition_errors("12345678901234567890")
        
    def test_get_confidence_level(self):
        """测试置信度级别获取"""
        assert self.asr._get_confidence_level(0.9) == ConfidenceLevel.HIGH
        assert self.asr._get_confidence_level(0.7) == ConfidenceLevel.MEDIUM
        assert self.asr._get_confidence_level(0.4) == ConfidenceLevel.LOW
        assert self.asr._get_confidence_level(0.1) == ConfidenceLevel.VERY_LOW
        
    def test_validate_audio_for_recognition_success(self):
        """测试音频验证成功情况"""
        audio_data = self._create_test_audio_bytes()
        
        is_valid, message = self.asr.validate_audio_for_recognition(audio_data)
        
        # The valid test tone should pass the real quality assessment.
        assert isinstance(is_valid, bool)
        assert isinstance(message, str)
        assert is_valid is True
        assert message == "音频质量良好，适合识别"
        
    def test_validate_audio_for_recognition_failure(self):
        """Reject clipped audio through the real validation path."""
        audio_data = np.full(16000, 32000, dtype="<i2").tobytes()
        is_valid, message = self.asr.validate_audio_for_recognition(audio_data)
        assert not is_valid
        assert "音频质量过低" in message
            
    def test_get_recognition_suggestions(self):
        """测试获取识别建议"""
        audio_data = self._create_test_audio_bytes()
        
        suggestions = self.asr.get_recognition_suggestions(audio_data)
        
        assert isinstance(suggestions, list)
        assert len(suggestions) > 0
        assert all(isinstance(s, str) for s in suggestions)
        
    def test_get_recognition_suggestions_with_issues(self):
        """Produce useful suggestions from quiet, short audio."""
        audio_data = np.zeros(4800, dtype="<i2").tobytes()
        suggestions = self.asr.get_recognition_suggestions(audio_data)
        suggestion_text = " ".join(suggestions)
        assert "静音" in suggestion_text
        assert "音量" in suggestion_text
        assert "时长" in suggestion_text
            
    def _create_test_audio_bytes(self, duration=1.0, sample_rate=16000):
        """创建测试用的音频字节数据"""
        # 生成简单的正弦波
        t = np.linspace(0, duration, int(sample_rate * duration))
        audio_float = 0.5 * np.sin(2 * np.pi * 440 * t)  # 440Hz正弦波
        audio_int16 = (audio_float * 32767).astype(np.int16)
        return audio_int16.tobytes()


class TestASRErrorTypes:
    """ASR错误类型测试"""
    
    def test_asr_error_inheritance(self):
        """测试ASR错误类继承关系"""
        assert issubclass(ASRQualityError, ASRError)
        assert issubclass(ASRConfidenceError, ASRError)
        assert issubclass(ASRTimeoutError, ASRError)
        
    def test_asr_quality_error(self):
        """测试音频质量错误"""
        error = ASRQualityError("质量过低")
        assert str(error) == "质量过低"
        assert isinstance(error, ASRError)
        
    def test_asr_confidence_error(self):
        """测试置信度错误"""
        error = ASRConfidenceError("置信度过低")
        assert str(error) == "置信度过低"
        assert isinstance(error, ASRError)
        
    def test_asr_timeout_error(self):
        """测试超时错误"""
        error = ASRTimeoutError("处理超时")
        assert str(error) == "处理超时"
        assert isinstance(error, ASRError)


class TestASRIntegration:
    """ASR集成测试"""
    
    def test_full_enhanced_recognition_pipeline(self):
        """测试完整的增强识别流程"""
        asr = MockASR({
            "min_confidence": 0.6,
            "min_audio_quality": 0.2,
            "max_processing_time": 10.0
        })
        
        # 创建测试音频
        audio_data = self._create_good_quality_audio()
        
        # 执行增强识别
        result = asr.recognize_enhanced(audio_data)
        
        # 验证结果
        assert result.text is not None
        assert result.confidence > 0
        assert result.confidence_level != ConfidenceLevel.VERY_LOW
        assert result.audio_quality_score > 0
        assert result.processing_time > 0
        
        # 验证音频
        is_valid, message = asr.validate_audio_for_recognition(audio_data)
        assert isinstance(is_valid, bool)
        
        # 获取建议
        suggestions = asr.get_recognition_suggestions(audio_data)
        assert len(suggestions) > 0
        
    def _create_good_quality_audio(self):
        """创建高质量的测试音频"""
        duration = 2.0
        sample_rate = 16000
        t = np.linspace(0, duration, int(sample_rate * duration))
        
        # 创建包含多个频率的复合信号
        audio_float = (
            0.3 * np.sin(2 * np.pi * 440 * t) +  # 基频
            0.2 * np.sin(2 * np.pi * 880 * t) +  # 倍频
            0.1 * np.sin(2 * np.pi * 1320 * t)   # 三倍频
        )
        
        # 添加少量噪音
        noise = np.random.default_rng(2026).normal(0, 0.02, len(audio_float))
        audio_float += noise
        
        # 转换为16位整数
        audio_int16 = (np.clip(audio_float, -1.0, 1.0) * 32767).astype(np.int16)
        return audio_int16.tobytes()


if __name__ == "__main__":
    pytest.main([__file__])








