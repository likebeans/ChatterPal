# -*- coding: utf-8 -*-
"""
音素分析模块
基于Parselmouth和Praat进行精确的语音分析
"""

import os
import numpy as np
import tempfile
from typing import Dict, List, Tuple, Optional, Any, Union
import logging

try:
    import parselmouth
    from parselmouth.praat import call
except ImportError:
    parselmouth = None

try:
    import scipy.signal
    from scipy import stats
except ImportError:
    scipy = None

from .base import AssessmentBase, AssessmentError, PhonemeAnalysis
from ...utils.encoding_fix import safe_str

logger = logging.getLogger(__name__)


class PhonemeAnalyzer(AssessmentBase):
    """
    音素分析器
    使用Parselmouth和Praat进行精确的语音分析
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化音素分析器

        Args:
            config: 配置参数
        """
        super().__init__(config)

        if not parselmouth:
            self.logger.warning("parselmouth库未安装，将使用默认分析")

        self.temp_dir = tempfile.gettempdir()

        # 标准英语元音共振峰数据 (Hz) - 基于成年男性平均值
        self.standard_vowels = {
            "/i/": {"F1": 270, "F2": 2290, "F3": 3010, "name": "beat"},
            "/ɪ/": {"F1": 390, "F2": 1990, "F3": 2550, "name": "bit"},
            "/e/": {"F1": 530, "F2": 1840, "F3": 2480, "name": "bait"},
            "/ɛ/": {"F1": 660, "F2": 1720, "F3": 2410, "name": "bet"},
            "/æ/": {"F1": 730, "F2": 1090, "F3": 2440, "name": "bat"},
            "/ɑ/": {"F1": 570, "F2": 840, "F3": 2410, "name": "bot"},
            "/ɔ/": {"F1": 440, "F2": 1020, "F3": 2240, "name": "bought"},
            "/o/": {"F1": 300, "F2": 870, "F3": 2240, "name": "boat"},
            "/ʊ/": {"F1": 470, "F2": 1160, "F3": 2680, "name": "book"},
            "/u/": {"F1": 300, "F2": 870, "F3": 2240, "name": "boot"},
            "/ʌ/": {"F1": 760, "F2": 1400, "F3": 2780, "name": "but"},
            "/ə/": {"F1": 500, "F2": 1350, "F3": 1690, "name": "about"},
        }

        # 辅音特征数据
        self.consonant_features = {
            "/p/": {"type": "stop", "voicing": "voiceless", "place": "bilabial"},
            "/b/": {"type": "stop", "voicing": "voiced", "place": "bilabial"},
            "/t/": {"type": "stop", "voicing": "voiceless", "place": "alveolar"},
            "/d/": {"type": "stop", "voicing": "voiced", "place": "alveolar"},
            "/k/": {"type": "stop", "voicing": "voiceless", "place": "velar"},
            "/g/": {"type": "stop", "voicing": "voiced", "place": "velar"},
            "/f/": {
                "type": "fricative",
                "voicing": "voiceless",
                "place": "labiodental",
            },
            "/v/": {"type": "fricative", "voicing": "voiced", "place": "labiodental"},
            "/θ/": {"type": "fricative", "voicing": "voiceless", "place": "dental"},
            "/ð/": {"type": "fricative", "voicing": "voiced", "place": "dental"},
            "/s/": {"type": "fricative", "voicing": "voiceless", "place": "alveolar"},
            "/z/": {"type": "fricative", "voicing": "voiced", "place": "alveolar"},
            "/ʃ/": {
                "type": "fricative",
                "voicing": "voiceless",
                "place": "postalveolar",
            },
            "/ʒ/": {"type": "fricative", "voicing": "voiced", "place": "postalveolar"},
            "/h/": {"type": "fricative", "voicing": "voiceless", "place": "glottal"},
            "/m/": {"type": "nasal", "voicing": "voiced", "place": "bilabial"},
            "/n/": {"type": "nasal", "voicing": "voiced", "place": "alveolar"},
            "/ŋ/": {"type": "nasal", "voicing": "voiced", "place": "velar"},
            "/l/": {"type": "liquid", "voicing": "voiced", "place": "alveolar"},
            "/r/": {"type": "liquid", "voicing": "voiced", "place": "postalveolar"},
            "/w/": {"type": "glide", "voicing": "voiced", "place": "bilabial"},
            "/j/": {"type": "glide", "voicing": "voiced", "place": "palatal"},
        }

        self.logger.info("音素分析器初始化完成")

    def assess(
        self, audio_data: Union[bytes, str, Tuple], target_text: str = "", **kwargs
    ) -> Dict[str, Any]:
        """
        分析音频文件的语音特征

        Args:
            audio_data: 音频数据
            target_text: 目标文本（可选）
            **kwargs: 其他参数

        Returns:
            分析结果字典

        Raises:
            AssessmentError: 评估过程中的错误
        """
        try:
            if not self.validate_audio_data(audio_data):
                raise AssessmentError("音频数据验证失败")

            # 准备音频文件
            audio_path = self._prepare_audio_file(audio_data)

            try:
                if not parselmouth:
                    self.logger.warning("Parselmouth库未安装，使用默认分析")
                    return self._get_default_analysis()

                # 加载音频
                sound = parselmouth.Sound(audio_path)

                # 基础音频信息
                duration = sound.get_total_duration()
                sample_rate = sound.sampling_frequency

                # 提取各种特征
                pitch_analysis = self._analyze_pitch(sound)
                formant_analysis = self._analyze_formants(sound)
                intensity_analysis = self._analyze_intensity(sound)
                spectral_analysis = self._analyze_spectrum(sound)

                # 计算质量评分
                quality_score = self._calculate_quality_score(
                    pitch_analysis, formant_analysis, intensity_analysis
                )

                components = {
                    "pitch": pitch_analysis,
                    "formants": formant_analysis,
                    "intensity": intensity_analysis,
                    "spectral": spectral_analysis,
                }
                feature_errors = {
                    name: component["error"]
                    for name, component in components.items()
                    if not component["available"]
                }
                return {
                    "duration": duration,
                    "sample_rate": sample_rate,
                    **components,
                    "quality_score": quality_score,
                    "quality_source": "legacy_heuristic_uncalibrated",
                    "analysis_success": True,
                    "analysis_status": "partial" if feature_errors else "measured",
                    "feature_errors": feature_errors,
                    "feature_sources": {
                        name: component["source"]
                        for name, component in components.items()
                    },
                    "calibrated": False,
                }

            finally:
                # 只清理当前调用创建的文件，路径输入由调用方拥有
                if not isinstance(audio_data, str):
                    self._cleanup_temp_file(audio_path)

        except AssessmentError:
            raise
        except Exception as e:
            self.logger.error(f"音素分析失败: {e}")
            raise AssessmentError(f"音素分析失败: {e}")

    def _prepare_audio_file(self, audio_data: Union[bytes, str, Tuple]) -> str:
        """
        准备音频文件用于分析

        Args:
            audio_data: 音频数据

        Returns:
            音频文件路径
        """
        if isinstance(audio_data, str):
            return audio_data

        # 独占创建当前调用拥有的临时文件
        temp_fd, temp_path = tempfile.mkstemp(
            dir=self.temp_dir, prefix="temp_phoneme_", suffix=".wav"
        )

        try:
            os.close(temp_fd)
            if not self.convert_audio_to_file(audio_data, temp_path):
                raise AssessmentError("音频文件转换失败")

            return temp_path
        except BaseException:
            self._cleanup_temp_file(temp_path)
            raise

    def _cleanup_temp_file(self, file_path: str) -> None:
        """清理调用方已确认由当前调用拥有的临时文件。"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            self.logger.warning(f"清理临时文件失败: {e}")

    def _analyze_pitch(self, sound) -> Dict[str, Any]:
        """
        分析音调特征

        Args:
            sound: Parselmouth Sound对象

        Returns:
            音调分析结果
        """
        try:
            # 提取音调
            pitch = sound.to_pitch(time_step=0.01, pitch_floor=75, pitch_ceiling=500)

            # 获取音调值
            pitch_values = pitch.selected_array["frequency"]
            pitch_values = pitch_values[np.isfinite(pitch_values) & (pitch_values > 0)]

            if len(pitch_values) == 0:
                return self._get_default_pitch("No voiced frames; F0 is unmeasured")

            return {
                "mean": float(np.mean(pitch_values)),
                "std": float(np.std(pitch_values)),
                "min": float(np.min(pitch_values)),
                "max": float(np.max(pitch_values)),
                "range": float(np.max(pitch_values) - np.min(pitch_values)),
                "median": float(np.median(pitch_values)),
                "voiced_frames": len(pitch_values),
                "total_frames": len(pitch.selected_array["frequency"]),
                "voicing_ratio": len(pitch_values)
                / len(pitch.selected_array["frequency"]),
                "source": "parselmouth",
                "available": True,
            }

        except Exception as e:
            self.logger.warning(f"音调分析失败: {e}")
            return self._get_default_pitch(str(e))

    def _analyze_formants(self, sound) -> Dict[str, Any]:
        """
        分析共振峰特征

        Args:
            sound: Parselmouth Sound对象

        Returns:
            共振峰分析结果
        """
        try:
            # 提取共振峰
            formants = sound.to_formant_burg(
                time_step=0.01,
                max_number_of_formants=5,
                maximum_formant=5500,
                window_length=0.025,
            )

            # 获取前三个共振峰的值
            f1_values = []
            f2_values = []
            f3_values = []

            for i in range(formants.get_number_of_frames()):
                time = formants.get_time_from_frame_number(i + 1)

                f1 = formants.get_value_at_time(1, time)
                f2 = formants.get_value_at_time(2, time)
                f3 = formants.get_value_at_time(3, time)

                if all(np.isfinite(value) and value > 0 for value in (f1, f2, f3)):
                    f1_values.append(f1)
                    f2_values.append(f2)
                    f3_values.append(f3)

            if not f1_values:
                return self._get_default_formants("No valid formant frames")

            return {
                "F1": {
                    "mean": float(np.mean(f1_values)),
                    "std": float(np.std(f1_values)),
                    "values": f1_values[:50],  # 限制数据量
                },
                "F2": {
                    "mean": float(np.mean(f2_values)),
                    "std": float(np.std(f2_values)),
                    "values": f2_values[:50],
                },
                "F3": {
                    "mean": float(np.mean(f3_values)),
                    "std": float(np.std(f3_values)),
                    "values": f3_values[:50],
                },
                "valid_frames": len(f1_values),
                "source": "parselmouth",
                "available": True,
            }

        except Exception as e:
            self.logger.warning(f"共振峰分析失败: {e}")
            return self._get_default_formants(str(e))

    def _analyze_intensity(self, sound) -> Dict[str, Any]:
        """
        分析强度特征

        Args:
            sound: Parselmouth Sound对象

        Returns:
            强度分析结果
        """
        try:
            # 提取强度
            intensity = sound.to_intensity(time_step=0.01)
            intensity_values = intensity.values[0]
            intensity_values = intensity_values[np.isfinite(intensity_values)]

            if len(intensity_values) == 0:
                return self._get_default_intensity("No finite intensity frames")

            return {
                "mean": float(np.mean(intensity_values)),
                "std": float(np.std(intensity_values)),
                "min": float(np.min(intensity_values)),
                "max": float(np.max(intensity_values)),
                "range": float(np.max(intensity_values) - np.min(intensity_values)),
                "source": "parselmouth",
                "available": True,
            }

        except Exception as e:
            self.logger.warning(f"强度分析失败: {e}")
            return self._get_default_intensity(str(e))

    def _analyze_spectrum(self, sound) -> Dict[str, Any]:
        """
        分析频谱特征

        Args:
            sound: Parselmouth Sound对象

        Returns:
            频谱分析结果
        """
        try:
            # 获取频谱
            spectrum = sound.to_spectrum()

            # 计算频谱重心
            frequencies = spectrum.xs()
            # Praat stores real and imaginary Fourier coefficients in rows.
            # Both are signed; spectral moments need nonnegative magnitudes.
            coefficients = np.asarray(spectrum.values)
            magnitudes = (
                np.hypot(coefficients[0], coefficients[1])
                if len(coefficients) > 1
                else np.abs(coefficients[0])
            )
            total_magnitude = float(np.sum(magnitudes))
            if not np.isfinite(total_magnitude) or total_magnitude <= 0:
                raise ValueError("No spectral energy; spectrum is unavailable")

            # 频谱重心 (Spectral Centroid)
            spectral_centroid = np.sum(frequencies * magnitudes) / total_magnitude

            # 频谱扩散 (Spectral Spread)
            spectral_spread = np.sqrt(
                np.sum(((frequencies - spectral_centroid) ** 2) * magnitudes)
                / total_magnitude
            )

            # 频谱倾斜 (Spectral Skewness)
            spectral_skewness = (
                np.sum(((frequencies - spectral_centroid) ** 3) * magnitudes)
                / (total_magnitude * spectral_spread**3)
                if spectral_spread > 0
                else 0.0
            )

            return {
                "centroid": float(spectral_centroid),
                "spread": float(spectral_spread),
                "skewness": float(spectral_skewness),
                "bandwidth": float(np.max(frequencies) - np.min(frequencies)),
                "source": "parselmouth",
                "available": True,
            }

        except Exception as e:
            self.logger.warning(f"频谱分析失败: {e}")
            return {
                "centroid": 1000.0,
                "spread": 500.0,
                "skewness": 0.0,
                "bandwidth": 4000.0,
                "available": False,
                "source": "unavailable_legacy_default",
                "error": str(e),
            }

    def _calculate_quality_score(
        self, pitch_analysis: Dict, formant_analysis: Dict, intensity_analysis: Dict
    ) -> float:
        """
        计算语音质量评分

        Args:
            pitch_analysis: 音调分析结果
            formant_analysis: 共振峰分析结果
            intensity_analysis: 强度分析结果

        Returns:
            质量评分 (0-100)
        """
        try:
            score = 0.0

            # 音调稳定性评分 (30%)
            pitch_stability = 1.0 - min(
                1.0, pitch_analysis["std"] / max(1.0, pitch_analysis["mean"])
            )
            score += pitch_stability * 30

            # 共振峰清晰度评分 (40%)
            f1_clarity = 1.0 - min(
                1.0,
                formant_analysis["F1"]["std"]
                / max(1.0, formant_analysis["F1"]["mean"]),
            )
            f2_clarity = 1.0 - min(
                1.0,
                formant_analysis["F2"]["std"]
                / max(1.0, formant_analysis["F2"]["mean"]),
            )
            formant_clarity = (f1_clarity + f2_clarity) / 2
            score += formant_clarity * 40

            # 强度一致性评分 (30%)
            intensity_consistency = 1.0 - min(
                1.0, intensity_analysis["std"] / max(1.0, intensity_analysis["mean"])
            )
            score += intensity_consistency * 30

            return max(0.0, min(100.0, score))

        except Exception as e:
            self.logger.warning(f"质量评分计算失败: {e}")
            return 70.0

    def detect_vowel_errors(
        self, formant_analysis: Dict, target_vowel: str = None
    ) -> List[PhonemeAnalysis]:
        """
        检测元音发音错误

        Args:
            formant_analysis: 共振峰分析结果
            target_vowel: 目标元音音素

        Returns:
            错误检测结果列表
        """
        errors = []

        try:
            if not target_vowel or target_vowel not in self.standard_vowels:
                # 通用元音质量检测
                f1_mean = formant_analysis["F1"]["mean"]
                f2_mean = formant_analysis["F2"]["mean"]

                # 检测是否在合理范围内
                if f1_mean < 200 or f1_mean > 1000:
                    errors.append(
                        PhonemeAnalysis(
                            phoneme="vowel",
                            error_type="vowel_f1_abnormal",
                            description=f"第一共振峰异常: {f1_mean:.0f} Hz",
                            correction_method=["调整舌位高低，注意口腔开合度"],
                            severity="high",
                            actual_formants={"F1": f1_mean},
                        )
                    )

                if f2_mean < 800 or f2_mean > 3000:
                    errors.append(
                        PhonemeAnalysis(
                            phoneme="vowel",
                            error_type="vowel_f2_abnormal",
                            description=f"第二共振峰异常: {f2_mean:.0f} Hz",
                            correction_method=["调整舌位前后，注意舌尖位置"],
                            severity="high",
                            actual_formants={"F2": f2_mean},
                        )
                    )
            else:
                # 特定元音错误检测
                standard = self.standard_vowels[target_vowel]
                f1_diff = abs(formant_analysis["F1"]["mean"] - standard["F1"])
                f2_diff = abs(formant_analysis["F2"]["mean"] - standard["F2"])

                if f1_diff > 100:  # F1偏差超过100Hz
                    severity = "medium" if f1_diff < 200 else "high"
                    suggestion = (
                        "调整舌位高低"
                        if formant_analysis["F1"]["mean"] > standard["F1"]
                        else "舌位需要更高"
                    )

                    errors.append(
                        PhonemeAnalysis(
                            phoneme=target_vowel,
                            error_type="vowel_height_error",
                            description=f'元音高度错误: 实际F1={formant_analysis["F1"]["mean"]:.0f}Hz, 标准F1={standard["F1"]}Hz',
                            correction_method=[suggestion],
                            severity=severity,
                            target_formants={"F1": standard["F1"]},
                            actual_formants={"F1": formant_analysis["F1"]["mean"]},
                        )
                    )

                if f2_diff > 200:  # F2偏差超过200Hz
                    severity = "medium" if f2_diff < 400 else "high"
                    suggestion = (
                        "舌位需要更前"
                        if formant_analysis["F2"]["mean"] < standard["F2"]
                        else "舌位需要更后"
                    )

                    errors.append(
                        PhonemeAnalysis(
                            phoneme=target_vowel,
                            error_type="vowel_backness_error",
                            description=f'元音前后位置错误: 实际F2={formant_analysis["F2"]["mean"]:.0f}Hz, 标准F2={standard["F2"]}Hz',
                            correction_method=[suggestion],
                            severity=severity,
                            target_formants={"F2": standard["F2"]},
                            actual_formants={"F2": formant_analysis["F2"]["mean"]},
                        )
                    )

        except Exception as e:
            self.logger.error(f"元音错误检测失败: {e}")

        return errors

    def generate_pronunciation_feedback(
        self, analysis_result: Dict[str, Any], target_text: str = ""
    ) -> str:
        """
        生成详细的发音反馈

        Args:
            analysis_result: 分析结果
            target_text: 目标文本

        Returns:
            反馈文本
        """
        try:
            feedback = "## 🔬 专业语音分析报告\n\n"

            # 基础信息
            feedback += f"### 📊 基础信息\n"
            feedback += f"- **音频时长**: {analysis_result['duration']:.2f} 秒\n"
            feedback += f"- **采样率**: {analysis_result['sample_rate']:.0f} Hz\n"
            feedback += (
                f"- **整体质量评分**: {analysis_result['quality_score']:.1f}/100\n\n"
            )

            # 音调分析
            pitch = analysis_result["pitch"]
            feedback += f"### 🎵 音调分析\n"
            feedback += f"- **平均音调**: {pitch['mean']:.1f} Hz\n"
            feedback += f"- **音调范围**: {pitch['min']:.1f} - {pitch['max']:.1f} Hz\n"
            feedback += f"- **音调变化**: {pitch['std']:.1f} Hz\n"
            feedback += f"- **有声比例**: {pitch['voicing_ratio']:.1%}\n\n"

            # 共振峰分析
            formants = analysis_result["formants"]
            feedback += f"### 🔊 共振峰分析\n"
            feedback += f"- **F1 (舌位高低)**: {formants['F1']['mean']:.0f} ± {formants['F1']['std']:.0f} Hz\n"
            feedback += f"- **F2 (舌位前后)**: {formants['F2']['mean']:.0f} ± {formants['F2']['std']:.0f} Hz\n"
            feedback += f"- **F3 (唇形)**: {formants['F3']['mean']:.0f} ± {formants['F3']['std']:.0f} Hz\n\n"

            # 错误检测
            vowel_errors = self.detect_vowel_errors(formants)
            if vowel_errors:
                feedback += f"### ⚠️ 发音问题检测\n"
                for error in vowel_errors:
                    severity_emoji = "🔴" if error.severity == "high" else "🟡"
                    feedback += f"{severity_emoji} **{error.description}**\n"
                    for method in error.correction_method:
                        feedback += f"   💡 建议: {method}\n"
                    feedback += "\n"

            # 改进建议
            feedback += f"### 💡 专业改进建议\n"

            quality_score = analysis_result["quality_score"]
            if quality_score < 60:
                feedback += "- 🎯 **重点练习**: 基础发音准确性\n"
                feedback += "- 📚 **建议**: 从单个音素开始练习，注意口型和舌位\n"
            elif quality_score < 80:
                feedback += "- 🎯 **重点练习**: 音调控制和共振峰稳定性\n"
                feedback += "- 📚 **建议**: 练习长音发音，保持音质一致性\n"
            else:
                feedback += "- 🎉 **表现优秀**: 发音质量很好！\n"
                feedback += "- 📚 **建议**: 可以尝试更复杂的语音练习\n"

            return feedback

        except Exception as e:
            self.logger.error(f"反馈生成失败: {e}")
            return f"反馈生成失败: {safe_str(e)}"

    def _get_default_analysis(self) -> Dict[str, Any]:
        """获取默认分析结果"""
        return {
            "duration": 1.0,
            "sample_rate": 16000,
            "pitch": self._get_default_pitch(),
            "formants": self._get_default_formants(),
            "intensity": self._get_default_intensity(),
            "spectral": {
                "centroid": 1000.0,
                "spread": 500.0,
                "skewness": 0.0,
                "bandwidth": 4000.0,
                "available": False,
                "source": "unavailable_legacy_default",
                "error": "Parselmouth unavailable",
            },
            "quality_score": 70.0,
            "quality_source": "unavailable_legacy_default",
            "analysis_success": False,
            "analysis_status": "unavailable",
            "error": "Parselmouth unavailable; no acoustic features measured",
            "feature_sources": dict.fromkeys(
                ("duration", "sample_rate", "pitch", "formants", "intensity", "spectral"),
                "unavailable_legacy_default",
            ),
            "calibrated": False,
        }

    def _get_default_pitch(self, reason: str = "Pitch unavailable") -> Dict[str, Any]:
        """获取默认音调数据"""
        return {
            "mean": 150.0,
            "std": 25.0,
            "min": 100.0,
            "max": 200.0,
            "range": 100.0,
            "median": 150.0,
            "voiced_frames": 100,
            "total_frames": 120,
            "voicing_ratio": 0.83,
            "available": False,
            "source": "unavailable_legacy_default",
            "error": reason,
        }

    def _get_default_formants(
        self, reason: str = "Formants unavailable"
    ) -> Dict[str, Any]:
        """获取默认共振峰数据"""
        return {
            "F1": {"mean": 500.0, "std": 50.0, "values": []},
            "F2": {"mean": 1500.0, "std": 100.0, "values": []},
            "F3": {"mean": 2500.0, "std": 150.0, "values": []},
            "valid_frames": 100,
            "available": False,
            "source": "unavailable_legacy_default",
            "error": reason,
        }

    def _get_default_intensity(
        self, reason: str = "Intensity unavailable"
    ) -> Dict[str, Any]:
        """获取默认强度数据"""
        return {
            "mean": 60.0,
            "std": 5.0,
            "min": 50.0,
            "max": 70.0,
            "range": 20.0,
            "available": False,
            "source": "unavailable_legacy_default",
            "error": reason,
        }


# 便捷函数
def analyze_phonemes(
    audio_data: Union[bytes, str, Tuple], target_text: str = "", **kwargs
) -> Dict[str, Any]:
    """
    分析音频音素特征的便捷函数

    Args:
        audio_data: 音频数据
        target_text: 目标文本
        **kwargs: 其他参数

    Returns:
        音素分析结果
    """
    analyzer = PhonemeAnalyzer()
    return analyzer.assess(audio_data, target_text, **kwargs)
