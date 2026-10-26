# -*- coding: utf-8 -*-
"""
韵律分析模块
基于myprosody库进行音频韵律特征分析
"""

import os
import tempfile
import numpy as np
import wave
from typing import Dict, Any, Optional, Union, Tuple
import logging

try:
    mysp = __import__("myprosody")
except ImportError:
    mysp = None

try:
    import parselmouth
except ImportError:
    parselmouth = None

from .base import AssessmentBase, AssessmentError, ProsodyFeatures, dimension_result, finite_number

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
            self.logger.warning("myprosody库未安装，相关韵律测量不可用")

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

                # 按时间帧混合声道，避免将交错声道当作连续采样。
                if audio_array.ndim == 2:
                    audio_array = audio_array.mean(axis=1).astype(np.int16)

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
        """Preserve only provider measurements, including each feature's provenance."""
        if not os.path.isfile(wav_file_path):
            raise AssessmentError(f"音频文件不存在: {wav_file_path}")
        features = ProsodyFeatures()
        sources = dict.fromkeys(vars(features), "unavailable")
        errors = {}
        metadata = {name: dimension_result(reason="没有实测数据或经过验证的计算方法") for name in vars(features)}
        audio_dir = os.path.dirname(wav_file_path)
        audio_name = os.path.splitext(os.path.basename(wav_file_path))[0]
        provider_calls = {
            "syllable_count": ("myspsyl", "count"),
            "pause_count": ("mysppaus", "count"),
            "speaking_rate": ("myspsr", "syllables/second"),
            "articulation_rate": ("myspatc", "syllables/voiced_second"),
        }
        if mysp is None:
            errors["myprosody"] = "myprosody unavailable; provider features unmeasured"
        else:
            for name, (function_name, unit) in provider_calls.items():
                try:
                    value = getattr(mysp, function_name)(audio_name, audio_dir)
                    if (
                        not finite_number(value, 0)
                        or (name != "pause_count" and value == 0)
                        or (name.endswith("count") and value != int(value))
                    ):
                        raise ValueError(f"unusable {function_name} result: {value!r}")
                    value = int(value) if name.endswith("count") else float(value)
                    setattr(features, name, value)
                    sources[name] = "myprosody"
                    metadata[name] = dimension_result(
                        status="measured", source="myprosody", unit=unit,
                        reason="本次录音的韵律特征；不代表语言能力分数",
                        evidence={"value": value},
                    )
                except Exception as error:
                    errors[name] = str(error)
                    metadata[name] = dimension_result(reason=str(error))
        acoustic_measurements = {}
        try:
            acoustic_measurements = self._measure_pitch(wav_file_path)
        except Exception as error:
            errors["acoustics"] = str(error)
        duration = acoustic_measurements.pop("duration", None)
        for name, value in acoustic_measurements.items():
            setattr(features, name, value)
            sources[name] = "parselmouth"
            metadata[name] = dimension_result(
                status="measured", source="parselmouth", unit="Hz",
                reason="有效有声帧的基频统计；不代表音素准确性", evidence={"value": value},
            )
        if not acoustic_measurements:
            errors.setdefault("pitch", "No voiced frames; F0 is unmeasured")
        success = any(source != "unavailable" for source in sources.values()) or duration is not None
        return {
            "prosody_features": features,
            "analysis_success": success,
            "analysis_status": "unavailable" if not success else ("partial" if errors else "measured"),
            "error": "; ".join(f"{name}: {error}" for name, error in errors.items()) or None,
            "feature_errors": errors, "feature_sources": sources,
            "feature_metadata": metadata,
            "unavailable_features": [name for name, source in sources.items() if source == "unavailable"],
            "duration": duration, "calibrated": False,
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

    def analyze_speaking_rate(self, prosody_features: ProsodyFeatures) -> Dict[str, Any]:
        """Expose the syllable rate without applying words/minute grade thresholds."""
        value = prosody_features.speaking_rate
        return {
            **dimension_result(reason="音节语速没有经过能力评分校准", unit="syllables/second"),
            "rate": float(value) if finite_number(value, 0) else None,
            "rating": "unavailable",
            "description": "语速是录音摘要；不能据此判断语言能力",
            "suggestions": ["结合句意回听录音，检查是否清楚表达"],
        }

    def analyze_pause_patterns(self, prosody_features: ProsodyFeatures) -> Dict[str, Any]:
        """Summarize available counts while keeping the uncalibrated grade absent."""
        pauses, syllables = prosody_features.pause_count, prosody_features.syllable_count
        duration = prosody_features.pause_duration
        return {
            **dimension_result(reason="停顿密度没有经过能力评分校准"),
            "pause_count": pauses if finite_number(pauses, 0) else None,
            "pause_duration": duration if finite_number(duration, 0) else None,
            "pause_density": pauses / syllables if finite_number(pauses, 0) and finite_number(syllables, 1) else None,
            "rating": "unavailable",
            "description": "停顿需要结合句意判断；计数不能确认流畅度问题",
            "suggestions": ["回听停顿位置，结合句意判断是否需要调整"],
        }

    def analyze_fluency(self, prosody_features: ProsodyFeatures) -> Dict[str, Any]:
        """Do not turn provider statistics or legacy sample fields into a grade."""
        return {
            **dimension_result(reason="没有经过校准的流畅度评估方法"),
            "rating": "unavailable", "description": "流畅度暂不可评估",
            "factors": {
                name: value if finite_number(value, 0) else None
                for name in ("speaking_rate", "pause_count", "articulation_rate")
                for value in (getattr(prosody_features, name),)
            },
            "suggestions": ["可结合原音或老师的具体反馈练习"],
        }


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
