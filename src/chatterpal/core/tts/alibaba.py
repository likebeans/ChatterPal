# -*- coding: utf-8 -*-
"""
阿里百炼 TTS 实现
基于阿里云 CosyVoice Python SDK
"""

import logging
import time
import io
from typing import Optional, Dict, Any, Iterator, List
from dataclasses import dataclass

try:
    from dashscope.audio.tts_v2 import SpeechSynthesizer
    import dashscope
except ImportError:
    SpeechSynthesizer = None
    dashscope = None

from .base import TTSBase, TTSError, TTSResult


logger = logging.getLogger(__name__)


@dataclass
class AlibabaTTSConfig:
    """阿里百炼 TTS 配置"""
    api_key: str
    voice: str = "cosyvoice-v1"  # 默认模型
    voice_name: str = "longxiaochun"  # 默认语音，cosyvoice-v1支持的音色
    format: str = "mp3"  # 音频格式，阿里云TTS默认返回MP3格式
    sample_rate: int = 22050  # 采样率
    volume: int = 50  # 音量 (0-100)
    speech_rate: float = 1.0  # 语速 (0.5-2.0)
    pitch_rate: float = 1.0  # 音调 (0.5-2.0)


class AlibabaTTS(TTSBase):
    """
    阿里百炼 TTS 实现
    基于阿里云 CosyVoice Python SDK
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化阿里百炼 TTS

        Args:
            config: 配置参数，支持以下参数：
                - api_key: API密钥
                - voice: 模型名称 (默认: cosyvoice-v1)
                - voice_name: 语音名称 (默认: longxiaochun)
                - format: 音频格式
                - sample_rate: 采样率
                - volume: 音量
                - speech_rate: 语速
                - pitch_rate: 音调
        """
        super().__init__(config)

        if not SpeechSynthesizer:
            raise ImportError("dashscope库未安装，请运行: pip install dashscope")

        # 从配置或环境变量获取API密钥
        import os
        self.api_key = self.config.get("api_key") or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise TTSError("阿里百炼API密钥未配置，请设置 api_key 参数或 DASHSCOPE_API_KEY 环境变量")

        # 设置 API 密钥
        dashscope.api_key = self.api_key

        # 配置参数
        self.tts_config = AlibabaTTSConfig(
            api_key=self.api_key,
            voice=self.config.get("voice", "cosyvoice-v1"),
            voice_name=self.config.get("voice_name", "longxiaochun"),
            format=self.config.get("format", "wav"),
            sample_rate=self.config.get("sample_rate", 22050),
            volume=self.config.get("volume", 50),
            speech_rate=self.config.get("speech_rate", 1.0),
            pitch_rate=self.config.get("pitch_rate", 1.0)
        )

        self.logger.info(f"阿里百炼 TTS 初始化完成，模型: {self.tts_config.voice}, 语音: {self.tts_config.voice_name}")

    def synthesize(self, text: str, **kwargs) -> bytes:
        """
        同步语音合成

        Args:
            text: 要合成的文本
            **kwargs: 其他参数

        Returns:
            合成的音频数据

        Raises:
            TTSError: 合成过程中的错误
        """
        start_time = time.time()
        
        try:
            # 合并配置参数
            voice = kwargs.get("voice", self.tts_config.voice)
            voice_name = kwargs.get("voice_name", self.tts_config.voice_name)
            format_type = kwargs.get("format", self.tts_config.format)
            sample_rate = kwargs.get("sample_rate", self.tts_config.sample_rate)
            speech_rate = kwargs.get("speech_rate", self.tts_config.speech_rate)
            pitch_rate = kwargs.get("pitch_rate", self.tts_config.pitch_rate)

            # 实例化SpeechSynthesizer，传入模型和音色参数
            synthesizer = SpeechSynthesizer(model=voice, voice=voice_name)
            
            # 调用阿里云 TTS API
            audio_data = synthesizer.call(text)

            # 检查音频数据；SDK 的可变缓冲区统一转换为 bytes。
            if audio_data:
                if not isinstance(audio_data, (bytes, bytearray)):
                    raise TTSError("合成失败，音频数据不是二进制数据")
                audio_data = bytes(audio_data)
                synthesis_time = time.time() - start_time
                self.logger.info(f"语音合成完成，耗时: {synthesis_time:.2f}s，音频大小: {len(audio_data)} bytes")
                return audio_data
            else:
                raise TTSError("合成失败，无音频数据")

        except Exception as e:
            self.logger.error(f"语音合成失败: {e}")
            raise TTSError(f"语音合成失败: {e}")

    def synthesize_stream(self, text: str, **kwargs) -> Iterator[bytes]:
        """
        流式语音合成
        注意：当前 SDK 版本可能不支持流式合成，返回完整音频数据

        Args:
            text: 要合成的文本
            **kwargs: 其他参数

        Yields:
            音频数据片段

        Raises:
            TTSError: 合成过程中的错误
        """
        try:
            # 对于不支持流式的情况，返回完整音频数据
            audio_data = self.synthesize(text, **kwargs)
            
            # 将音频数据分块返回，模拟流式效果
            chunk_size = 4096
            for i in range(0, len(audio_data), chunk_size):
                yield audio_data[i:i + chunk_size]

        except Exception as e:
            self.logger.error(f"流式语音合成失败: {e}")
            raise TTSError(f"流式语音合成失败: {e}")

    def synthesize_with_error_handling(self, text: str, max_retries: int = 3, **kwargs) -> TTSResult:
        """
        带错误处理的语音合成

        Args:
            text: 要合成的文本
            max_retries: 最大重试次数
            **kwargs: 其他参数

        Returns:
            TTS结果对象

        Raises:
            TTSError: 合成失败
        """
        last_error = None
        
        for attempt in range(max_retries):
            try:
                start_time = time.time()
                audio_data = self.synthesize(text, **kwargs)
                synthesis_time = time.time() - start_time
                
                return TTSResult(
                    text=text,
                    audio_data=audio_data,
                    duration=self._estimate_audio_duration(audio_data),
                    format=kwargs.get("format", self.tts_config.format),
                    sample_rate=kwargs.get("sample_rate", self.tts_config.sample_rate),
                    cached=False,
                    synthesis_time=synthesis_time,
                    metadata={
                        "voice_name": kwargs.get("voice_name", self.tts_config.voice_name),
                        "model": kwargs.get("voice", self.tts_config.voice),
                        "volume": kwargs.get("volume", self.tts_config.volume),
                        "speech_rate": kwargs.get("speech_rate", self.tts_config.speech_rate),
                        "pitch_rate": kwargs.get("pitch_rate", self.tts_config.pitch_rate)
                    }
                )
                
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    self.logger.warning(f"语音合成失败，重试 {attempt + 1}/{max_retries}: {e}")
                    time.sleep(0.5 * (attempt + 1))  # 指数退避
                    continue
                else:
                    self.logger.error(f"语音合成最终失败: {e}")
                    break
        
        raise TTSError(f"语音合成失败，已重试 {max_retries} 次: {last_error}")

    def get_supported_voices(self) -> List[str]:
        """
        获取支持的语音列表
        cosyvoice-v1模型支持的音色

        Returns:
            支持的语音列表
        """
        return [
            # cosyvoice-v1 支持的音色
            "longxiaochun",  # 龙小春（推荐）
            "longmiao",      # 龙喵
            "longtong",      # 龙童
            "longwan",       # 龙婉
            "longfei",       # 龙飞
            "longxin",       # 龙心
            "longyu",        # 龙语
            "longming"        # 龙鸣
        ]

    def get_voice_info(self, voice: str) -> Dict[str, Any]:
        """
        获取语音信息

        Args:
            voice: 语音名称

        Returns:
            语音信息
        """
        voice_info_map = {
            "longxiaochun": {
                "name": "longxiaochun",
                "display_name": "龙小春",
                "language": "zh-CN",
                "gender": "female",
                "description": "温柔甜美的女声，适合日常对话"
            },
            "longmiao": {
                "name": "longmiao",
                "display_name": "龙喵",
                "language": "zh-CN",
                "gender": "female",
                "description": "活泼可爱的女声"
            },
            "longtong": {
                "name": "longtong",
                "display_name": "龙童",
                "language": "zh-CN",
                "gender": "child",
                "description": "童声，清脆可爱"
            },
            "longwan": {
                "name": "longwan",
                "display_name": "龙婉",
                "language": "zh-CN",
                "gender": "female",
                "description": "优雅温婉的女声"
            },
            "longfei": {
                "name": "longfei",
                "display_name": "龙飞",
                "language": "zh-CN",
                "gender": "male",
                "description": "成熟稳重的男声"
            },
            "longxin": {
                "name": "longxin",
                "display_name": "龙心",
                "language": "zh-CN",
                "gender": "female",
                "description": "知性温和的女声"
            },
            "longyu": {
                "name": "longyu",
                "display_name": "龙语",
                "language": "zh-CN",
                "gender": "female",
                "description": "清新自然的女声"
            },
            "longming": {
                "name": "longming",
                "display_name": "龙鸣",
                "language": "zh-CN",
                "gender": "male",
                "description": "浑厚有力的男声"
            }
        }
        
        return voice_info_map.get(voice, {
            "name": voice,
            "language": "unknown",
            "gender": "unknown",
            "description": "未知语音"
        })

    def test_connection(self) -> bool:
        """
        测试阿里百炼 TTS 服务连接

        Returns:
            连接是否正常
        """
        try:
            # 测试简单的语音合成
            test_text = "Hello, this is a test."
            audio_data = self.synthesize(test_text)
            
            if audio_data and len(audio_data) > 0:
                self.logger.info("阿里百炼 TTS 连接测试成功")
                return True
            else:
                self.logger.error("阿里百炼 TTS 连接测试失败：无音频数据")
                return False
                
        except Exception as e:
            self.logger.error(f"阿里百炼 TTS 连接测试失败: {e}")
            return False

    def synthesize_to_file(self, text: str, output_path: str, **kwargs) -> bool:
        """
        将文本合成为语音并保存到文件

        Args:
            text: 要合成的文本
            output_path: 输出文件路径
            **kwargs: 其他参数

        Returns:
            是否成功保存

        Raises:
            TTSError: 合成或保存过程中的错误
        """
        try:
            # 合成音频数据
            audio_data = self.synthesize(text, **kwargs)
            
            if not audio_data:
                raise TTSError("合成失败，无音频数据")
            
            # 保存到文件
            with open(output_path, "wb") as f:
                f.write(audio_data)
            
            self.logger.info(f"音频已保存到: {output_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"保存音频文件失败: {e}")
            raise TTSError(f"保存音频文件失败: {e}")

    def _estimate_audio_duration(self, audio_data: bytes) -> float:
        """估算音频时长"""
        try:
            # 简单估算：假设是16位PCM，22050Hz采样率
            sample_rate = self.tts_config.sample_rate
            bytes_per_sample = 2  # 16位 = 2字节
            
            # 尝试从WAV头部获取信息
            if len(audio_data) > 44 and audio_data[:4] == b'RIFF':
                # WAV文件格式
                import struct
                try:
                    # 读取采样率（字节24-27）
                    sample_rate = struct.unpack('<I', audio_data[24:28])[0]
                    # 读取位深度（字节34-35）
                    bits_per_sample = struct.unpack('<H', audio_data[34:36])[0]
                    bytes_per_sample = bits_per_sample // 8
                    
                    # 音频数据从字节44开始
                    audio_data_size = len(audio_data) - 44
                except:
                    # 如果解析失败，使用默认值
                    audio_data_size = len(audio_data)
            else:
                audio_data_size = len(audio_data)
            
            duration = audio_data_size / (sample_rate * bytes_per_sample)
            return max(0.0, duration)
            
        except Exception as e:
            self.logger.warning(f"音频时长估算失败: {e}")
            return 0.0

    def get_service_info(self) -> Dict[str, Any]:
        """
        获取服务信息

        Returns:
            服务信息字典
        """
        return {
            "provider": "Alibaba CosyVoice TTS",
            "model": self.tts_config.voice,
            "voice": self.tts_config.voice_name,
            "format": self.tts_config.format,
            "sample_rate": self.tts_config.sample_rate,
            "volume": self.tts_config.volume,
            "speech_rate": self.tts_config.speech_rate,
            "pitch_rate": self.tts_config.pitch_rate,
            "supported_voices": self.get_supported_voices(),
            "features": [
                "多语言支持",
                "高质量语音合成",
                "可调节语速和音调",
                "多种音色选择"
            ]
        }


# 便捷函数
def create_alibaba_tts(
    api_key: str, 
    voice_name: str = "longxiaochun", 
    **kwargs
) -> AlibabaTTS:
    """
    创建阿里百炼 TTS 实例的便捷函数

    Args:
        api_key: API密钥
        voice_name: 语音名称
        **kwargs: 其他配置参数

    Returns:
        AlibabaTTS实例
    """
    config = {"api_key": api_key, "voice_name": voice_name, **kwargs}
    return AlibabaTTS(config)