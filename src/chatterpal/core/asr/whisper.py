# -*- coding: utf-8 -*-
"""
Whisper语音识别实现
基于OpenAI Whisper进行本地语音识别
"""

import os
import re
import tempfile
import threading
import traceback
from collections import OrderedDict
from typing import Optional, Dict, Any
import logging

try:
    import speech_recognition as sr
except ImportError:
    sr = None

from .base import ASRBase, ASRError

logger = logging.getLogger(__name__)


class WhisperASR(ASRBase):
    """
    基于Whisper的语音识别实现
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化Whisper语音识别

        Args:
            config: 配置参数，支持以下参数：
                - model_size: Whisper模型大小 (tiny, base, small, medium, large)
                - language: 识别语言 (默认en；支持zh、auto、english、chinese)
                - device: 模型设备 (默认auto；支持cpu、cuda、cuda:N；MPS暂不支持)
                - max_cached_models: 实例模型缓存容量 (1至4，默认1)
                - energy_threshold: 能量阈值
                - pause_threshold: 暂停阈值
        """
        super().__init__(config)

        if not sr:
            raise ImportError(
                "speech_recognition库未安装，请运行: pip install SpeechRecognition"
            )

        self.model_size = self.config.get("model_size", "base")
        self.language = self.config.get("language", "en")
        self.device = self.config.get("device", "auto")
        self.max_cached_models = self.config.get("max_cached_models", 1)
        if (
            isinstance(self.max_cached_models, bool)
            or not isinstance(self.max_cached_models, int)
            or not 1 <= self.max_cached_models <= 4
        ):
            raise ValueError("max_cached_models must be an integer from 1 to 4")
        self._convert_language_code(self.language)
        self._validate_device(self.device)
        self._models = OrderedDict()
        self._runtime_lock = threading.Lock()

        # 初始化识别器
        self.recognizer = sr.Recognizer()

        # 调整识别器参数以提高准确性
        self.recognizer.energy_threshold = self.config.get("energy_threshold", 300)
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = self.config.get("pause_threshold", 0.8)
        self.recognizer.operation_timeout = None

        self.logger.info(
            f"WhisperASR初始化完成，模型: {self.model_size}, 语言: {self.language}"
        )

    def recognize(self, audio_data: bytes, **kwargs) -> Optional[str]:
        """
        识别音频字节数据并返回文本

        Args:
            audio_data: 音频字节数据
            **kwargs: 其他参数

        Returns:
            识别结果文本，失败返回None

        Raises:
            ASRError: 识别过程中的错误
        """
        temp_path = None
        try:
            # 字节输入创建的文件归本次调用所有，即使写入或识别失败也会清理。
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_path = temp_file.name
                temp_file.write(audio_data)

            return self.recognize_file(temp_path, **kwargs)

        except ASRError:
            raise
        except Exception as e:
            self.logger.error(f"识别音频字节数据失败: {e}")
            raise ASRError(f"识别音频字节数据失败: {e}") from e
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except OSError as e:
                    self.logger.warning(f"清理临时音频文件失败: {e}")

    def recognize_file(self, audio_path: str, **kwargs) -> Optional[str]:
        """
        识别音频文件并返回文本

        Args:
            audio_path: 音频文件路径
            **kwargs: 其他参数，支持：
                - model_size: 临时覆盖模型大小
                - language: 临时覆盖语言设置
                - device: 临时覆盖模型设备

        Returns:
            识别结果文本，失败返回None

        Raises:
            ASRError: 识别过程中的错误
        """
        try:
            if not self.validate_audio_file(audio_path):
                return None

            # 获取参数
            model_size = kwargs.get("model_size", self.model_size)
            language = kwargs.get("language", self.language)

            # 使用speech_recognition加载音频文件
            with sr.AudioFile(audio_path) as source:
                audio_data = self.recognizer.record(source)

            # 使用Whisper进行识别
            try:
                # 转换语言代码
                whisper_language = self._convert_language_code(language)

                result = self._transcribe(
                    audio_data,
                    model_size,
                    whisper_language,
                    kwargs.get("device", self.device),
                )

                self.logger.info(f"语音识别成功: {result}")
                return result

            except sr.RequestError as e:
                self.logger.error(f"Whisper识别请求错误: {e}")
                raise ASRError(f"Whisper识别请求错误: {e}")
            except sr.UnknownValueError:
                self.logger.warning("Whisper无法识别音频内容")
                return None

        except ASRError:
            raise
        except Exception as e:
            self.logger.error(f"语音识别异常: {e}")
            raise ASRError(f"语音识别异常: {e}")

    def recognize_from_microphone(self, duration: int = 5, **kwargs) -> Optional[str]:
        """
        从麦克风录音并识别

        Args:
            duration: 录音时长（秒）
            **kwargs: 其他参数

        Returns:
            识别结果文本，失败返回None

        Raises:
            ASRError: 识别过程中的错误
        """
        try:
            # 检查是否有麦克风支持
            try:
                import pyaudio
            except ImportError:
                raise ASRError("PyAudio未安装，无法使用麦克风功能")

            with sr.Microphone() as source:
                self.logger.info("正在调整环境噪音...")
                self.recognizer.adjust_for_ambient_noise(source, duration=1)

                self.logger.info(f"开始录音，时长: {duration}秒")
                audio_data = self.recognizer.listen(
                    source, timeout=duration, phrase_time_limit=duration
                )

            # 获取参数
            model_size = kwargs.get("model_size", self.model_size)
            language = kwargs.get("language", self.language)

            # 使用Whisper进行识别
            try:
                whisper_language = self._convert_language_code(language)

                result = self._transcribe(
                    audio_data,
                    model_size,
                    whisper_language,
                    kwargs.get("device", self.device),
                )

                self.logger.info(f"麦克风语音识别成功: {result}")
                return result

            except sr.RequestError as e:
                self.logger.error(f"Whisper识别请求错误: {e}")
                raise ASRError(f"Whisper识别请求错误: {e}")
            except sr.UnknownValueError:
                self.logger.warning("Whisper无法识别麦克风音频内容")
                return None

        except ASRError:
            raise
        except Exception as e:
            self.logger.error(f"麦克风语音识别异常: {e}")
            raise ASRError(f"麦克风语音识别异常: {e}")

    def convert_audio_format(
        self, input_path: str, output_path: str = None
    ) -> Optional[str]:
        """
        转换音频格式为WAV（speech_recognition要求）

        Args:
            input_path: 输入音频文件路径
            output_path: 输出WAV文件路径，如果为None则使用临时文件

        Returns:
            转换后的WAV文件路径，失败返回None

        Raises:
            ASRError: 转换过程中的错误
        """
        try:
            try:
                from pydub import AudioSegment
            except ImportError:
                self.logger.warning("pydub未安装，无法进行音频格式转换")
                return input_path  # 返回原文件，希望它已经是支持的格式

            # 加载音频文件
            audio = AudioSegment.from_file(input_path)

            # 转换为单声道，16kHz采样率
            audio = audio.set_channels(1).set_frame_rate(16000)

            # 确定输出路径
            if output_path is None:
                temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                output_path = temp_file.name
                temp_file.close()

            # 导出为WAV格式
            audio.export(output_path, format="wav")
            self.logger.info(f"音频格式转换成功: {input_path} -> {output_path}")
            return output_path

        except Exception as e:
            self.logger.error(f"音频格式转换失败: {e}")
            raise ASRError(f"音频格式转换失败: {e}")

    def get_supported_formats(self) -> list:
        """
        获取支持的音频格式列表

        Returns:
            支持的音频格式列表
        """
        return ["wav", "aiff", "aiff-c", "flac"]

    def test_connection(self) -> bool:
        """
        测试Whisper功能是否正常

        Returns:
            测试是否成功
        """
        temp_path = None
        try:
            # 创建一个简单的测试音频（静音）
            import numpy as np
            import wave

            # 生成1秒的静音音频
            sample_rate = 16000
            duration = 1
            samples = np.zeros(sample_rate * duration, dtype=np.int16)

            # 保存为临时WAV文件
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_path = temp_file.name
            with wave.open(temp_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(samples.tobytes())

            # 尝试识别（预期会失败，但不应该抛出异常）
            self.recognize_file(temp_path)

            self.logger.info("Whisper功能测试完成")
            return True

        except Exception as e:
            self.logger.error(f"Whisper功能测试失败: {e}")
            return False
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except OSError as e:
                    self.logger.warning(f"清理连接测试音频失败: {e}")

    def _transcribe(
        self, audio_data, model_size: str, language: Optional[str], device: str
    ) -> str:
        """在同一实例锁内加载并推理，避免跨模型键并行使用运行时。"""
        import numpy as np
        import torch
        import whisper

        self._validate_device(device)
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda":
            device = f"cuda:{torch.cuda.current_device()}"
        with self._runtime_lock:
            model = None
            try:
                key = (model_size, device)
                if key not in self._models:
                    if len(self._models) >= self.max_cached_models:
                        # 先释放最久未用模型，加载期间也不超过实例容量。
                        self._models.popitem(last=False)
                    self._models[key] = whisper.load_model(model_size, device=device)
                self._models.move_to_end(key)
                model = self._models[key]
                raw_data = audio_data.get_raw_data(convert_rate=16000, convert_width=2)
                waveform = (
                    np.frombuffer(raw_data, dtype="<i2").astype(np.float32) / 32768.0
                )
                result = model.transcribe(
                    waveform, language=language, fp16=device.startswith("cuda")
                )
                return result["text"]
            except Exception as e:
                # 保留异常堆栈信息，但不让外部保留的异常延长模型/张量寿命。
                traceback.clear_frames(e.__traceback__)
                raise
            finally:
                del model

    @staticmethod
    def _validate_device(device: str) -> None:
        if device == "mps":
            raise ValueError(
                "device 'mps' is unsupported by the current Whisper/PyTorch runtime; "
                "use cpu or auto"
            )
        if not isinstance(device, str) or (
            device not in {"auto", "cpu", "cuda"}
            and not re.fullmatch(r"cuda:(0|[1-9][0-9]*)", device)
        ):
            raise ValueError("device must be auto, cpu, cuda or cuda:N")

    def _convert_language_code(self, language: str) -> Optional[str]:
        """
        转换语言代码为Whisper支持的格式

        Args:
            language: 输入语言代码

        Returns:
            Whisper支持的语言代码
        """
        language_map = {
            "zh": "chinese",
            "en": "english",
            "chinese": "chinese",
            "english": "english",
            "auto": None,
        }

        if not isinstance(language, str) or language.lower() not in language_map:
            raise ValueError("language must be en, zh, auto, english or chinese")
        return language_map[language.lower()]


# 便捷函数
def create_whisper_asr(model_size: str = "base", language: str = "en") -> WhisperASR:
    """
    创建WhisperASR实例的便捷函数

    Args:
        model_size: Whisper模型大小
        language: 识别语言

    Returns:
        WhisperASR实例
    """
    config = {"model_size": model_size, "language": language}
    return WhisperASR(config)


def recognize_audio_file(
    audio_file_path: str, model_size: str = "base", language: str = "en"
) -> Optional[str]:
    """
    识别音频文件的便捷函数

    Args:
        audio_file_path: 音频文件路径
        model_size: Whisper模型大小
        language: 识别语言

    Returns:
        识别结果文本，失败返回None
    """
    asr = create_whisper_asr(model_size=model_size, language=language)
    return asr.recognize_file(audio_file_path)


def recognize_microphone(
    duration: int = 5, model_size: str = "base", language: str = "en"
) -> Optional[str]:
    """
    从麦克风录音并识别的便捷函数

    Args:
        duration: 录音时长（秒）
        model_size: Whisper模型大小
        language: 识别语言

    Returns:
        识别结果文本，失败返回None
    """
    asr = create_whisper_asr(model_size=model_size, language=language)
    return asr.recognize_from_microphone(duration=duration)
