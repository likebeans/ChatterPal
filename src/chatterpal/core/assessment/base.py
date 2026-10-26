# -*- coding: utf-8 -*-
"""
发音评估基类定义
定义统一的发音评估接口和数据结构
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, Union, Tuple
from dataclasses import dataclass, field
import logging
import math
from numbers import Real
from dataclasses import asdict


def finite_number(value: Any, minimum: Optional[float] = None, maximum: Optional[float] = None) -> bool:
    """Validate measurements without coercing strings, booleans or nonfinite values."""
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and (minimum is None or value >= minimum)
        and (maximum is None or value <= maximum)
    )


def dimension_result(
    score: Optional[float] = None,
    *,
    status: str = "unavailable",
    source: str = "none",
    reason: str,
    evidence: Optional[Dict[str, Any]] = None,
    unit: Optional[str] = None,
) -> Dict[str, Any]:
    """Common additive metadata for measurements, estimates and unavailable scores."""
    return {
        "score": score if finite_number(score, 0, 100) else None,
        "status": status,
        "source": source,
        "reason": reason,
        "evidence": evidence or {},
        "unit": unit,
        "calibrated": False,
    }

logger = logging.getLogger(__name__)


class AssessmentError(Exception):
    """发音评估错误基类"""

    pass


@dataclass
class WordAnalysis:
    """单词级别分析结果"""

    target_word: str
    recognized_word: str
    is_correct: bool
    phonetic_info: Dict[str, Any] = field(default_factory=dict)
    correction_tips: List[str] = field(default_factory=list)
    confidence_score: Optional[float] = None


@dataclass
class PhonemeAnalysis:
    """音素级别分析结果"""

    phoneme: str
    error_type: str
    description: str
    correction_method: List[str] = field(default_factory=list)
    severity: str = "medium"  # low, medium, high
    target_formants: Dict[str, float] = field(default_factory=dict)
    actual_formants: Dict[str, float] = field(default_factory=dict)


@dataclass
class ProsodyFeatures:
    """韵律特征数据"""

    # None means unmeasured; an unavailable feature must not resemble a sample.
    speaking_rate: Optional[float] = None  # myprosody: syllables/second
    articulation_rate: Optional[float] = None  # myprosody: syllables/voiced second
    f0_mean: Optional[float] = None
    f0_std: Optional[float] = None
    f0_min: Optional[float] = None
    f0_max: Optional[float] = None
    pause_duration: Optional[float] = None
    words_per_minute: Optional[float] = None
    vowel_accuracy: Optional[float] = None
    formant_ratio: Optional[float] = None
    intonation_index: Optional[float] = None
    fluency_score: Optional[float] = None
    syllable_count: Optional[int] = None
    pause_count: Optional[int] = None


@dataclass
class AssessmentResult:
    """发音评估结果"""

    overall_score: Optional[float]
    fluency_score: Optional[float]
    pronunciation_score: Optional[float]
    prosody_score: Optional[float]
    accuracy_score: Optional[float]

    # 详细分析
    prosody_features: ProsodyFeatures
    word_analysis: List[WordAnalysis] = field(default_factory=list)
    phoneme_analysis: List[PhonemeAnalysis] = field(default_factory=list)

    # 反馈和建议
    feedback: str = ""
    suggestions: List[str] = field(default_factory=list)

    # 元数据
    recognized_text: str = ""
    target_text: str = ""
    audio_duration: Optional[float] = None
    quality_score: Optional[float] = None
    dimensions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    feature_metadata: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    calibrated: bool = False
    scope: str = "录音声学摘要和识别文本对齐；未校准，不代表音素准确率或语言能力。"

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "overall_score": self.overall_score,
            "detailed_scores": {
                "fluency": self.fluency_score,
                "pronunciation": self.pronunciation_score,
                "prosody": self.prosody_score,
                "accuracy": self.accuracy_score,
            },
            "prosody_features": asdict(self.prosody_features),
            "dimensions": self.dimensions,
            "feature_metadata": self.feature_metadata,
            "calibrated": self.calibrated,
            "scope": self.scope,
            "word_analysis": [
                {
                    "target_word": wa.target_word,
                    "recognized_word": wa.recognized_word,
                    "is_correct": wa.is_correct,
                    "phonetic_info": wa.phonetic_info,
                    "correction_tips": wa.correction_tips,
                    "confidence_score": wa.confidence_score,
                }
                for wa in self.word_analysis
            ],
            "phoneme_analysis": [
                {
                    "phoneme": pa.phoneme,
                    "error_type": pa.error_type,
                    "description": pa.description,
                    "correction_method": pa.correction_method,
                    "severity": pa.severity,
                }
                for pa in self.phoneme_analysis
            ],
            "feedback": self.feedback,
            "suggestions": self.suggestions,
            "recognized_text": self.recognized_text,
            "target_text": self.target_text,
            "audio_duration": self.audio_duration,
            "quality_score": self.quality_score,
        }


class AssessmentBase(ABC):
    """
    发音评估基类
    定义统一的评估接口，所有评估实现都应继承此类
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化评估实例

        Args:
            config: 配置参数字典
        """
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def assess(
        self, audio_data: Union[bytes, str, Tuple], target_text: str = "", **kwargs
    ) -> AssessmentResult:
        """
        评估音频发音质量

        Args:
            audio_data: 音频数据，可以是字节数据、文件路径或Gradio音频元组
            target_text: 目标文本（可选）
            **kwargs: 其他参数

        Returns:
            评估结果

        Raises:
            AssessmentError: 评估过程中的错误
        """
        pass

    def validate_audio_data(self, audio_data: Union[bytes, str, Tuple]) -> bool:
        """
        验证音频数据是否有效

        Args:
            audio_data: 音频数据

        Returns:
            是否有效
        """
        if audio_data is None:
            self.logger.error("音频数据为空")
            return False

        if isinstance(audio_data, str):
            # 文件路径
            import os

            if not os.path.exists(audio_data):
                self.logger.error(f"音频文件不存在: {audio_data}")
                return False
            if os.path.getsize(audio_data) == 0:
                self.logger.error(f"音频文件为空: {audio_data}")
                return False
        elif isinstance(audio_data, bytes):
            # 字节数据
            if len(audio_data) == 0:
                self.logger.error("音频字节数据为空")
                return False
        elif isinstance(audio_data, tuple):
            # Gradio音频数据
            if len(audio_data) != 2:
                self.logger.error("Gradio音频数据格式错误")
                return False
            sample_rate, audio_array = audio_data
            if not finite_number(sample_rate, 1) or sample_rate != int(sample_rate):
                self.logger.error("音频采样率无效")
                return False
            try:
                import numpy as np

                samples = np.asarray(audio_array)
                if (
                    samples.ndim not in (1, 2) or samples.size == 0
                    or samples.dtype.kind not in "iuf" or not np.isfinite(samples).all()
                ):
                    self.logger.error("音频样本为空或不是有限数值")
                    return False
            except (ImportError, TypeError, ValueError):
                self.logger.error("无法验证音频样本")
                return False
        else:
            self.logger.error(f"不支持的音频数据类型: {type(audio_data)}")
            return False

        return True

    def convert_audio_to_file(
        self, audio_data: Union[bytes, Tuple], output_path: str
    ) -> bool:
        """
        将音频数据转换为文件

        Args:
            audio_data: 音频数据
            output_path: 输出文件路径

        Returns:
            是否成功
        """
        try:
            if isinstance(audio_data, bytes):
                # 字节数据直接写入文件
                with open(output_path, "wb") as f:
                    f.write(audio_data)
                return True
            elif isinstance(audio_data, tuple):
                # Gradio音频数据
                sample_rate, audio_array = audio_data

                import soundfile as sf

                sf.write(output_path, audio_array, sample_rate)
                return True
            else:
                self.logger.error(f"不支持的音频数据类型: {type(audio_data)}")
                return False

        except ImportError:
            self.logger.error("soundfile库未安装，无法转换音频")
            return False
        except Exception as e:
            self.logger.error(f"音频转换失败: {e}")
            return False

    def detect_language(self, text: str) -> str:
        """
        检测文本语言

        Args:
            text: 输入文本

        Returns:
            语言代码 ('zh' 或 'en')
        """
        if not text:
            return "en"  # 默认英文

        import re

        # 检测是否包含中文字符
        chinese_pattern = re.compile(r"[\u4e00-\u9fff]")
        if chinese_pattern.search(text):
            return "zh"

        # 检测是否主要是英文字符
        english_pattern = re.compile(r"[a-zA-Z]")
        if english_pattern.search(text):
            return "en"

        return "en"  # 默认英文

    def calculate_text_similarity(self, text1: str, text2: str) -> float:
        """
        计算文本相似度

        Args:
            text1: 文本1
            text2: 文本2

        Returns:
            相似度 (0-1)
        """
        from .alignment import align_text, words

        if not words(text1) and not words(text2):
            return 1.0
        alignment = align_text(text1, text2)
        return alignment["score"] / 100 if alignment["score"] is not None else 0.0

    def estimate_audio_duration(self, audio_data: Union[bytes, str, Tuple]) -> float:
        """
        估算音频时长

        Args:
            audio_data: 音频数据

        Returns:
            时长（秒）
        """
        try:
            if isinstance(audio_data, str):
                # 文件路径
                import librosa

                duration = librosa.get_duration(filename=audio_data)
                return duration
            elif isinstance(audio_data, tuple):
                # Gradio音频数据
                sample_rate, audio_array = audio_data
                return len(audio_array) / sample_rate
            else:
                # 默认估算
                return 1.0

        except ImportError:
            self.logger.warning("librosa库未安装，无法准确计算音频时长")
            return 1.0
        except Exception as e:
            self.logger.warning(f"音频时长计算失败: {e}")
            return 1.0

    def create_default_result(self, error_message: str = "") -> AssessmentResult:
        """
        创建默认的评估结果

        Args:
            error_message: 错误信息

        Returns:
            默认评估结果
        """
        return AssessmentResult(
            overall_score=None,
            fluency_score=None,
            pronunciation_score=None,
            prosody_score=None,
            accuracy_score=None,
            prosody_features=ProsodyFeatures(),
            feedback=error_message or "评估失败，请重试",
            suggestions=["请检查音频质量", "确保录音清晰", "重新录制音频"],
            dimensions={
                name: dimension_result(reason=error_message or "评估不可用")
                for name in ("overall", "fluency", "pronunciation", "prosody", "accuracy", "quality")
            },
        )

    def test_functionality(self) -> bool:
        """
        测试评估功能是否正常

        Returns:
            功能是否正常
        """
        try:
            # 创建一个简单的测试音频
            import numpy as np
            import tempfile
            import os

            # 生成1秒的测试音频
            sample_rate = 16000
            duration = 1.0
            t = np.linspace(0, duration, int(sample_rate * duration))
            audio_array = np.sin(2 * np.pi * 440 * t).astype(np.float32)  # 440Hz正弦波

            test_audio = (sample_rate, audio_array)

            # 尝试评估
            result = self.assess(test_audio, "test")

            # 检查结果是否有效
            return isinstance(result, AssessmentResult) and (
                finite_number(result.overall_score, 0, 100)
                or any(metadata.get("status") == "measured" for metadata in result.feature_metadata.values())
            )

        except Exception as e:
            self.logger.error(f"功能测试失败: {e}")
            return False
