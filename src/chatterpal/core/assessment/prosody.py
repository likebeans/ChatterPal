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
                # 分析韵律特征
                prosody_features = self._analyze_prosody_features(temp_wav_path)

                return {
                    "prosody_features": prosody_features,
                    "analysis_success": True,
                    "error": None,
                }

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

    def _analyze_prosody_features(self, wav_file_path: str) -> ProsodyFeatures:
        """
        分析音频的韵律特征

        Args:
            wav_file_path: WAV文件路径

        Returns:
            韵律特征对象
        """
        try:
            if mysp is None:
                self.logger.warning("myprosody库未安装，使用默认特征")
                return ProsodyFeatures()

            # 使用myprosody分析音频特征
            audio_dir = os.path.dirname(wav_file_path)
            audio_filename = os.path.basename(wav_file_path)

            # 重要：文件名不包含扩展名，myprosody会自动添加.wav
            audio_name_without_ext = os.path.splitext(audio_filename)[0]

            self.logger.info(
                f"分析音频文件: {audio_name_without_ext} 在目录: {audio_dir}"
            )

            # 检查音频文件是否存在
            if not os.path.exists(wav_file_path):
                self.logger.error(f"音频文件不存在: {wav_file_path}")
                return ProsodyFeatures()

            # 尝试使用myprosody的各个函数
            features = {}

            # 获取音节数
            try:
                syllable_count = mysp.myspsyl(audio_name_without_ext, audio_dir)
                features["syllable_count"] = (
                    syllable_count
                    if isinstance(syllable_count, (int, float)) and syllable_count > 0
                    else 1
                )
                self.logger.info(f"音节数: {features['syllable_count']}")
            except Exception as e:
                self.logger.warning(f"获取音节数失败: {e}")
                features["syllable_count"] = 1

            # 获取停顿数
            try:
                pause_count = mysp.mysppaus(audio_name_without_ext, audio_dir)
                features["pause_count"] = (
                    pause_count
                    if isinstance(pause_count, (int, float)) and pause_count >= 0
                    else 0
                )
                self.logger.info(f"停顿数: {features['pause_count']}")
            except Exception as e:
                self.logger.warning(f"获取停顿数失败: {e}")
                features["pause_count"] = 0

            # 获取语速
            try:
                speaking_rate = mysp.myspsr(audio_name_without_ext, audio_dir)
                features["speaking_rate"] = (
                    speaking_rate
                    if isinstance(speaking_rate, (int, float)) and speaking_rate > 0
                    else 120
                )
                self.logger.info(f"语速: {features['speaking_rate']}")
            except Exception as e:
                self.logger.warning(f"获取语速失败: {e}")
                features["speaking_rate"] = 120

            # 获取发音率
            try:
                articulation_rate = mysp.myspatc(audio_name_without_ext, audio_dir)
                features["articulation_rate"] = (
                    articulation_rate
                    if isinstance(articulation_rate, (int, float))
                    and articulation_rate > 0
                    else 150
                )
                self.logger.info(f"发音率: {features['articulation_rate']}")
            except Exception as e:
                self.logger.warning(f"获取发音率失败: {e}")
                features["articulation_rate"] = 150

            # 计算基于实际特征的其他指标
            duration_estimate = (
                features["syllable_count"] / (features["speaking_rate"] / 60)
                if features["speaking_rate"] > 0
                else 1
            )

            return ProsodyFeatures(
                speaking_rate=features["speaking_rate"],
                articulation_rate=features["articulation_rate"],
                f0_mean=150.0,  # 基于典型值
                f0_std=25.0,  # 基于典型值
                f0_max=200.0,
                f0_min=100.0,
                pause_duration=features["pause_count"] * 0.5,  # 估算停顿时长
                words_per_minute=features["speaking_rate"],
                vowel_accuracy=min(
                    0.9, max(0.5, features["articulation_rate"] / 200)
                ),  # 基于发音率估算
                formant_ratio=0.8,
                intonation_index=min(
                    1.0, max(0.3, features["speaking_rate"] / 150)
                ),  # 基于语速估算
                fluency_score=min(
                    1.0,
                    max(
                        0.3,
                        (features["speaking_rate"] / 150)
                        * (1 - features["pause_count"] / 10),
                    ),
                ),
                syllable_count=features["syllable_count"],
                pause_count=features["pause_count"],
            )

        except Exception as e:
            self.logger.error(f"韵律特征分析失败: {e}")
            return ProsodyFeatures()

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
