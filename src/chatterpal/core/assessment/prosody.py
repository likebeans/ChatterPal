# -*- coding: utf-8 -*-
"""
韵律分析模块
基于myprosody库进行音频韵律特征分析
"""

import os
import tempfile
import numpy as np
import wave
from typing import Dict, Any, Optional, Union, Tuple, List
import logging

try:
    mysp = __import__("myprosody")
except ImportError:
    mysp = None

try:
    import parselmouth
except ImportError:
    parselmouth = None

from .base import AssessmentBase, AssessmentError, ProsodyFeatures

logger = logging.getLogger(__name__)


class ProsodyAnalyzer(AssessmentBase):
    """
    韵律分析器
    基于myprosody库进行音频韵律特征分析
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化韵律分析器

        Args:
            config: 配置参数
        """
        super().__init__(config)

        if not mysp:
            self.logger.warning("myprosody库未安装，将使用默认分析")

        self.temp_dir = tempfile.gettempdir()
        self.logger.info("韵律分析器初始化完成")

    def assess(
        self, audio_data: Union[bytes, str, Tuple], target_text: str = "", **kwargs
    ) -> Dict[str, Any]:
        """
        评估音频的韵律特征

        Args:
            audio_data: 音频数据
            target_text: 目标文本（可选）
            **kwargs: 其他参数

        Returns:
            韵律特征分析结果

        Raises:
            AssessmentError: 评估过程中的错误
        """
        try:
            if not self.validate_audio_data(audio_data):
                raise AssessmentError("音频数据验证失败")

            # 转换音频为WAV文件
            temp_wav_path = self._prepare_audio_file(audio_data)

            try:
                return self._analyze_prosody_features(temp_wav_path)

            finally:
                # 只清理当前调用创建的文件，路径输入由调用方拥有
                if not isinstance(audio_data, str):
                    self._cleanup_temp_file(temp_wav_path)

        except AssessmentError:
            raise
        except Exception as e:
            self.logger.error(f"韵律分析失败: {e}")
            raise AssessmentError(f"韵律分析失败: {e}")

    def _prepare_audio_file(self, audio_data: Union[bytes, str, Tuple]) -> str:
        """
        准备音频文件用于分析

        Args:
            audio_data: 音频数据

        Returns:
            WAV文件路径
        """
        if isinstance(audio_data, str):
            # 如果是文件路径，直接返回
            return audio_data

        # 独占创建当前调用拥有的临时WAV文件
        temp_fd, temp_wav_path = tempfile.mkstemp(
            dir=self.temp_dir, prefix="temp_prosody_", suffix=".wav"
        )

        try:
            os.close(temp_fd)
            if isinstance(audio_data, tuple):
                # Gradio音频数据
                sample_rate, audio_array = audio_data

                # 确保音频数据是16位整数格式
                if audio_array.dtype != np.int16:
                    if audio_array.dtype in [np.float32, np.float64]:
                        audio_array = np.clip(audio_array, -1.0, 1.0)
                        audio_array = (audio_array * 32767).astype(np.int16)
                    else:
                        audio_array = audio_array.astype(np.int16)

                # 保存为WAV文件
                with wave.open(temp_wav_path, "w") as wav_file:
                    wav_file.setnchannels(1)  # 单声道
                    wav_file.setsampwidth(2)  # 16位
                    wav_file.setframerate(sample_rate)
                    wav_file.writeframes(audio_array.tobytes())

            elif isinstance(audio_data, bytes):
                # 字节数据直接写入文件
                with open(temp_wav_path, "wb") as f:
                    f.write(audio_data)
            else:
                raise AssessmentError(f"不支持的音频数据类型: {type(audio_data)}")

            return temp_wav_path
        except BaseException:
            self._cleanup_temp_file(temp_wav_path)
            raise

    def _analyze_prosody_features(self, wav_file_path: str) -> Dict[str, Any]:
        """Keep measured features separate from legacy, uncalibrated estimates."""
        if not os.path.isfile(wav_file_path):
            raise AssessmentError(f"音频文件不存在: {wav_file_path}")

        features = ProsodyFeatures()
        sources = dict.fromkeys(vars(features), "unavailable_legacy_default")
        errors = {}
        provider_features = {}
        audio_dir = os.path.dirname(wav_file_path)
        audio_name = os.path.splitext(os.path.basename(wav_file_path))[0]
        provider_calls = {
            "syllable_count": ("myspsyl", 1),
            "pause_count": ("mysppaus", 0),
            "speaking_rate": ("myspsr", 120.0),
            "articulation_rate": ("myspatc", 150.0),
        }

        if mysp is None:
            errors["myprosody"] = "myprosody unavailable; provider features unmeasured"
        else:
            for name, (function_name, fallback) in provider_calls.items():
                try:
                    value = getattr(mysp, function_name)(audio_name, audio_dir)
                    if (
                        isinstance(value, (bool, np.bool_))
                        or not isinstance(value, (int, float, np.integer, np.floating))
                        or not np.isfinite(value)
                        or value < 0
                        or (name != "pause_count" and value == 0)
                        or (name.endswith("count") and value != int(value))
                    ):
                        raise ValueError(f"unusable {function_name} result: {value!r}")
                    provider_features[name] = (
                        int(value) if name.endswith("count") else float(value)
                    )
                    sources[name] = "myprosody"
                except Exception as error:
                    errors[name] = str(error)
                    setattr(features, name, fallback)
                    self.logger.warning(f"{function_name}分析失败: {error}")

            if not provider_features:
                raise AssessmentError(f"myprosody未返回可用特征: {errors}")

        for name, value in provider_features.items():
            setattr(features, name, value)

        # These formulas are retained for numeric compatibility, not validation
        # of vowel accuracy, intonation, fluency, or words per minute.
        if "speaking_rate" in provider_features:
            features.words_per_minute = features.speaking_rate
            sources["words_per_minute"] = "legacy_rate_alias_uncalibrated"
            features.intonation_index = min(
                1.0, max(0.3, features.speaking_rate / 150)
            )
            sources["intonation_index"] = "legacy_heuristic_uncalibrated"
        if "articulation_rate" in provider_features:
            features.vowel_accuracy = min(
                0.9, max(0.5, features.articulation_rate / 200)
            )
            sources["vowel_accuracy"] = "legacy_heuristic_uncalibrated"
        if "pause_count" in provider_features:
            features.pause_duration = features.pause_count * 0.5
            sources["pause_duration"] = "legacy_heuristic_uncalibrated"
        if {"speaking_rate", "pause_count"} <= provider_features.keys():
            features.fluency_score = min(
                1.0,
                max(
                    0.3,
                    (features.speaking_rate / 150) * (1 - features.pause_count / 10),
                ),
            )
            sources["fluency_score"] = "legacy_heuristic_uncalibrated"

        acoustic_measurements = {}
        try:
            acoustic_measurements = self._measure_pitch(wav_file_path)
        except Exception as error:
            errors["acoustics"] = str(error)
        duration = acoustic_measurements.pop("duration", None)
        for name, value in acoustic_measurements.items():
            setattr(features, name, value)
            sources[name] = "parselmouth"
        if not acoustic_measurements:
            errors.setdefault("pitch", "No voiced frames; F0 is unmeasured")

        if not provider_features and duration is None:
            raise AssessmentError(f"没有可用的韵律分析后端: {errors}")

        return {
            "prosody_features": features,
            "analysis_success": True,
            "analysis_status": "partial" if errors else "measured",
            "error": "; ".join(f"{name}: {error}" for name, error in errors.items())
            or None,
            "feature_errors": errors,
            "feature_sources": sources,
            "unavailable_features": [
                name
                for name, source in sources.items()
                if source == "unavailable_legacy_default"
            ],
            "duration": duration,
            "calibrated": False,
        }

    def _measure_pitch(self, wav_file_path: str) -> Dict[str, float]:
        """Measure recording-level F0; unvoiced recordings have no F0 estimate."""
        if parselmouth is None:
            raise AssessmentError("Parselmouth unavailable; pitch is unmeasured")
        sound = parselmouth.Sound(wav_file_path)
        measurements = {"duration": float(sound.get_total_duration())}
        pitch = sound.to_pitch(time_step=0.01, pitch_floor=75, pitch_ceiling=500)
        values = pitch.selected_array["frequency"]
        values = values[np.isfinite(values) & (values > 0)]
        if values.size:
            measurements.update(
                f0_mean=float(np.mean(values)),
                f0_std=float(np.std(values)),
                f0_min=float(np.min(values)),
                f0_max=float(np.max(values)),
            )
        return measurements

    def _cleanup_temp_file(self, file_path: str) -> None:
        """
        清理调用方已确认由当前调用拥有的临时文件

        Args:
            file_path: 当前调用拥有的文件路径
        """
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            self.logger.warning(f"清理临时文件失败: {e}")

    def analyze_speaking_rate(
        self, prosody_features: ProsodyFeatures
    ) -> Dict[str, Any]:
        """
        分析语速特征

        Args:
            prosody_features: 韵律特征

        Returns:
            语速分析结果
        """
        speaking_rate = prosody_features.speaking_rate

        # 语速评级标准（词/分钟）
        if 140 <= speaking_rate <= 160:
            rating = "optimal"
            description = "语速适中，非常好"
            score = 95
        elif 120 <= speaking_rate <= 180:
            rating = "good"
            description = "语速良好"
            score = 85
        elif 100 <= speaking_rate <= 200:
            rating = "acceptable"
            description = "语速可接受"
            score = 70
        elif speaking_rate < 100:
            rating = "too_slow"
            description = "语速过慢"
            score = 50
        else:  # speaking_rate > 200
            rating = "too_fast"
            description = "语速过快"
            score = 50

        return {
            "rate": speaking_rate,
            "rating": rating,
            "description": description,
            "score": score,
            "suggestions": self._get_speaking_rate_suggestions(rating),
        }

    def analyze_pause_patterns(
        self, prosody_features: ProsodyFeatures
    ) -> Dict[str, Any]:
        """
        分析停顿模式

        Args:
            prosody_features: 韵律特征

        Returns:
            停顿分析结果
        """
        pause_count = prosody_features.pause_count
        pause_duration = prosody_features.pause_duration
        syllable_count = prosody_features.syllable_count

        # 计算停顿密度（每个音节的停顿数）
        pause_density = pause_count / max(1, syllable_count)

        if pause_density < 0.1:
            rating = "excellent"
            description = "停顿控制优秀"
            score = 95
        elif pause_density < 0.2:
            rating = "good"
            description = "停顿控制良好"
            score = 85
        elif pause_density < 0.3:
            rating = "acceptable"
            description = "停顿控制可接受"
            score = 70
        else:
            rating = "needs_improvement"
            description = "停顿过多，需要改进"
            score = 50

        return {
            "pause_count": pause_count,
            "pause_duration": pause_duration,
            "pause_density": pause_density,
            "rating": rating,
            "description": description,
            "score": score,
            "suggestions": self._get_pause_suggestions(rating),
        }

    def analyze_fluency(self, prosody_features: ProsodyFeatures) -> Dict[str, Any]:
        """
        分析流畅度

        Args:
            prosody_features: 韵律特征

        Returns:
            流畅度分析结果
        """
        fluency_score = prosody_features.fluency_score * 100

        if fluency_score >= 85:
            rating = "excellent"
            description = "流畅度优秀"
        elif fluency_score >= 70:
            rating = "good"
            description = "流畅度良好"
        elif fluency_score >= 55:
            rating = "acceptable"
            description = "流畅度可接受"
        else:
            rating = "needs_improvement"
            description = "流畅度需要改进"

        return {
            "score": fluency_score,
            "rating": rating,
            "description": description,
            "factors": {
                "speaking_rate": prosody_features.speaking_rate,
                "pause_count": prosody_features.pause_count,
                "articulation_rate": prosody_features.articulation_rate,
            },
            "suggestions": self._get_fluency_suggestions(rating),
        }

    def _get_speaking_rate_suggestions(self, rating: str) -> List[str]:
        """获取语速改进建议"""
        suggestions = {
            "too_slow": ["尝试稍微加快语速", "练习流畅的连读", "减少不必要的停顿"],
            "too_fast": [
                "放慢语速，注意清晰度",
                "在重要词汇处适当停顿",
                "练习控制呼吸节奏",
            ],
            "acceptable": ["继续保持当前语速", "可以尝试轻微调整以达到最佳状态"],
            "good": ["语速控制很好，继续保持"],
            "optimal": ["语速完美，继续保持这种状态"],
        }
        return suggestions.get(rating, ["继续练习语速控制"])

    def _get_pause_suggestions(self, rating: str) -> List[str]:
        """获取停顿改进建议"""
        suggestions = {
            "needs_improvement": [
                "减少不必要的停顿",
                "练习连续发音",
                "提前准备要说的内容",
            ],
            "acceptable": ["适当减少停顿频率", "注意停顿的自然性"],
            "good": ["停顿控制良好，继续保持"],
            "excellent": ["停顿控制优秀，非常自然"],
        }
        return suggestions.get(rating, ["注意停顿的合理性"])

    def _get_fluency_suggestions(self, rating: str) -> List[str]:
        """获取流畅度改进建议"""
        suggestions = {
            "needs_improvement": [
                "多练习连续发音",
                "提高语音连接的自然度",
                "减少犹豫和重复",
            ],
            "acceptable": ["继续练习提高流畅度", "注意语音的连贯性"],
            "good": ["流畅度很好，继续保持"],
            "excellent": ["流畅度优秀，表现出色"],
        }
        return suggestions.get(rating, ["继续练习提高流畅度"])


# 便捷函数
def analyze_prosody(audio_data: Union[bytes, str, Tuple], **kwargs) -> Dict[str, Any]:
    """
    分析音频韵律特征的便捷函数

    Args:
        audio_data: 音频数据
        **kwargs: 其他参数

    Returns:
        韵律分析结果
    """
    analyzer = ProsodyAnalyzer()
    return analyzer.assess(audio_data, **kwargs)
