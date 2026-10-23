# -*- coding: utf-8 -*-
"""
Edge TTS语音合成实现
基于Microsoft Edge TTS进行语音合成
"""

import asyncio
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Coroutine, Dict, List, Optional, TypeVar

try:
    import edge_tts
except ImportError:
    edge_tts = None

from .base import TTSBase, TTSError


_Result = TypeVar("_Result")
_FALLBACK_VOICES = {
    "en-US-AriaNeural": "Female",
    "en-US-JennyNeural": "Female",
    "en-US-GuyNeural": "Male",
    "zh-CN-XiaoxiaoNeural": "Female",
    "zh-CN-YunxiNeural": "Male",
    "zh-CN-YunjianNeural": "Male",
}


class EdgeTTS(TTSBase):
    """
    基于Microsoft Edge TTS的语音合成实现
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化Edge TTS

        Args:
            config: 配置参数，支持以下参数：
                - voice: 语音名称 (默认: en-US-AriaNeural)
                - rate: 语速 (默认: +0%)
                - pitch: 音调 (默认: +0Hz)
                - volume: 音量 (默认: +0%)
        """
        super().__init__(config)

        if not edge_tts:
            raise ImportError("edge-tts库未安装，请运行: pip install edge-tts")

        self.voice = self.config.get("voice", "en-US-AriaNeural")
        self.rate = self.config.get("rate", "+0%")
        self.pitch = self.config.get("pitch", "+0Hz")
        self.volume = self.config.get("volume", "+0%")
        self._voices: Optional[List[Dict[str, Any]]] = None
        self._voice_aliases: Dict[str, str] = {}
        self._voices_lock = threading.Lock()
        self._voices_retry_at = 0.0

        self.logger.info(f"EdgeTTS初始化完成，语音: {self.voice}")

    @staticmethod
    def _run_async(factory: Callable[[], Coroutine[Any, Any, _Result]]) -> _Result:
        """Keep the synchronous interface usable when the caller has a loop.

        Create the coroutine in the thread that runs it so a rejected nested
        asyncio.run cannot leave an unawaited coroutine behind. Async callers
        should offload the whole synchronous method to avoid blocking their loop.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(factory())

        def run() -> _Result:
            return asyncio.run(factory())

        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(run).result()

    def synthesize(self, text: str, **kwargs) -> bytes:
        """
        合成语音并返回音频数据

        Args:
            text: 要合成的文本
            **kwargs: 其他参数，支持：
                - voice: 临时覆盖语音
                - rate: 临时覆盖语速
                - pitch: 临时覆盖音调
                - volume: 临时覆盖音量

        Returns:
            音频字节数据

        Raises:
            TTSError: 合成过程中的错误
        """
        try:
            if not self.validate_text(text):
                raise TTSError("输入文本无效")

            # 清理文本
            cleaned_text = self.clean_text_for_tts(text)
            if not cleaned_text:
                raise TTSError("清理后的文本为空")

            # 获取参数
            voice = kwargs.get("voice", self.voice)
            rate = kwargs.get("rate", self.rate)
            pitch = kwargs.get("pitch", self.pitch)
            volume = kwargs.get("volume", self.volume)

            # 使用临时文件进行合成
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_path = temp_file.name

            try:
                # 异步合成语音
                success = self._run_async(
                    lambda: self._async_synthesize_to_file(
                        cleaned_text, temp_path, voice, rate, pitch, volume
                    )
                )

                if not success:
                    raise TTSError("语音合成失败")

                # 读取音频数据
                with open(temp_path, "rb") as f:
                    audio_data = f.read()

                self.logger.info(f"语音合成成功，生成 {len(audio_data)} 字节音频数据")
                return audio_data

            finally:
                # 清理临时文件
                try:
                    os.unlink(temp_path)
                except:
                    pass

        except TTSError:
            raise
        except Exception as e:
            self.logger.error(f"语音合成异常: {e}")
            raise TTSError(f"语音合成异常: {e}")

    def synthesize_to_file(self, text: str, output_path: str, **kwargs) -> bool:
        """
        合成语音并保存到文件

        Args:
            text: 要合成的文本
            output_path: 输出文件路径
            **kwargs: 其他参数

        Returns:
            是否成功保存

        Raises:
            TTSError: 合成过程中的错误
        """
        try:
            if not self.validate_text(text):
                raise TTSError("输入文本无效")

            if not self.validate_output_path(output_path):
                raise TTSError("输出路径无效")

            # 清理文本
            cleaned_text = self.clean_text_for_tts(text)
            if not cleaned_text:
                raise TTSError("清理后的文本为空")

            # 获取参数
            voice = kwargs.get("voice", self.voice)
            rate = kwargs.get("rate", self.rate)
            pitch = kwargs.get("pitch", self.pitch)
            volume = kwargs.get("volume", self.volume)

            # 异步合成语音
            success = self._run_async(
                lambda: self._async_synthesize_to_file(
                    cleaned_text, output_path, voice, rate, pitch, volume
                )
            )

            if success:
                self.logger.info(f"语音合成成功，保存到: {output_path}")
            else:
                raise TTSError("语音合成失败")

            return success

        except TTSError:
            raise
        except Exception as e:
            self.logger.error(f"语音合成到文件异常: {e}")
            raise TTSError(f"语音合成到文件异常: {e}")

    async def _async_synthesize_to_file(
        self,
        text: str,
        output_path: str,
        voice: str,
        rate: str,
        pitch: str,
        volume: str,
    ) -> bool:
        """
        异步合成语音到文件

        Args:
            text: 要合成的文本
            output_path: 输出文件路径
            voice: 语音名称
            rate: 语速
            pitch: 音调
            volume: 音量

        Returns:
            是否成功
        """
        try:
            # 创建TTS通信对象
            communicate = edge_tts.Communicate(
                text, voice, rate=rate, pitch=pitch, volume=volume
            )

            # 保存到文件
            await communicate.save(output_path)

            # 检查文件是否成功创建
            return os.path.exists(output_path) and os.path.getsize(output_path) > 0

        except Exception as e:
            self.logger.error(f"异步语音合成失败: {e}")
            return False

    def synthesize_with_command(self, text: str, output_path: str, **kwargs) -> bool:
        """
        使用命令行方式合成语音（兼容旧代码）

        Args:
            text: 要合成的文本
            output_path: 输出文件路径
            **kwargs: 其他参数

        Returns:
            是否成功
        """
        try:
            if not self.validate_text(text):
                return False

            if not self.validate_output_path(output_path):
                return False

            # 清理文本
            cleaned_text = self.clean_text_for_tts(text)
            if not cleaned_text:
                return False

            # 获取参数
            voice = kwargs.get("voice", self.voice)
            rate = kwargs.get("rate", self.rate)

            # 构建命令
            cmd_parts = [
                "edge-tts",
                "--text",
                f'"{cleaned_text}"',
                "--voice",
                voice,
                "--rate",
                rate,
                "--write-media",
                output_path,
            ]

            cmd = " ".join(cmd_parts)

            # 执行命令
            result = os.system(cmd)

            # 检查结果
            success = (
                result == 0
                and os.path.exists(output_path)
                and os.path.getsize(output_path) > 0
            )

            if success:
                self.logger.info(f"命令行语音合成成功: {output_path}")
            else:
                self.logger.error(f"命令行语音合成失败，返回码: {result}")

            return success

        except Exception as e:
            self.logger.error(f"命令行语音合成异常: {e}")
            return False

    def get_supported_voices(self) -> List[str]:
        """
        获取支持的语音列表

        Returns:
            支持的语音列表
        """
        return [voice["name"] for voice in self._get_voice_catalog()]

    @staticmethod
    def _default_voice_info(voice_name: str) -> Dict[str, Any]:
        locale = "-".join(voice_name.split("-")[:2])
        return {
            "name": voice_name,
            "display_name": voice_name,
            "local_name": voice_name,
            "gender": _FALLBACK_VOICES.get(voice_name, "Unknown"),
            "locale": locale,
            "language": locale,
            "sample_rate": 24000,
            "voice_type": "Neural",
        }

    def _get_voice_catalog(self) -> List[Dict[str, Any]]:
        """Share a catalog across list/metadata calls, including concurrent routes."""
        with self._voices_lock:
            if self._voices is not None and time.monotonic() < self._voices_retry_at:
                return self._voices

            try:
                voices = self._run_async(self._async_get_voices)
                catalog = []
                aliases = {}
                for voice in voices:
                    name = voice.get("ShortName") or voice.get("Name")
                    if not name:
                        continue
                    if voice.get("Name"):
                        aliases[voice["Name"]] = name
                    info = self._default_voice_info(name)
                    info.update(
                        {
                            "display_name": voice.get("DisplayName")
                            or voice.get("FriendlyName")
                            or name,
                            "local_name": voice.get("LocalName") or name,
                            "gender": voice.get("Gender") or "Unknown",
                            "locale": voice.get("Locale") or info["locale"],
                            "language": voice.get("Language")
                            or voice.get("Locale")
                            or info["language"],
                            "sample_rate": voice.get("SampleRateHertz", 24000),
                            "voice_type": voice.get("VoiceType") or "Neural",
                        }
                    )
                    catalog.append(info)
                self._voices = catalog
                self._voice_aliases = aliases
                self._voices_retry_at = time.monotonic() + 3600.0
            except Exception as e:
                self.logger.error(f"获取语音列表失败: {e}")
                # Keep a previously loaded catalog during an outage. Briefly
                # cache failures so metadata lookups do not retry for every voice.
                if self._voices is None:
                    self._voices = [
                        self._default_voice_info(name) for name in _FALLBACK_VOICES
                    ]
                self._voices_retry_at = time.monotonic() + 30.0

            return self._voices

    async def _async_get_voices(self) -> List[Dict]:
        """
        异步获取语音列表

        Returns:
            语音信息列表
        """
        voices = await edge_tts.list_voices()
        return voices

    def get_supported_formats(self) -> List[str]:
        """
        获取支持的音频格式列表

        Returns:
            支持的音频格式列表
        """
        return ["wav", "mp3"]

    def test_connection(self) -> bool:
        """
        测试Edge TTS服务连接

        Returns:
            连接是否正常
        """
        try:
            # 尝试合成一个简单的测试文本
            test_text = "Hello, this is a test."

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_path = temp_file.name

            try:
                success = self.synthesize_to_file(test_text, temp_path)

                if (
                    success
                    and os.path.exists(temp_path)
                    and os.path.getsize(temp_path) > 0
                ):
                    self.logger.info("Edge TTS连接测试成功")
                    return True
                else:
                    self.logger.error("Edge TTS连接测试失败")
                    return False

            finally:
                try:
                    os.unlink(temp_path)
                except:
                    pass

        except Exception as e:
            self.logger.error(f"Edge TTS连接测试异常: {e}")
            return False

    def get_voice_info(self, voice_name: str = None) -> Dict[str, Any]:
        """
        获取语音信息

        Args:
            voice_name: 语音名称，如果为None则使用当前配置的语音

        Returns:
            语音信息字典
        """
        target_voice = voice_name or self.voice

        catalog = self._get_voice_catalog()
        target_voice = self._voice_aliases.get(target_voice, target_voice)
        for voice in catalog:
            if voice["name"] == target_voice:
                return voice.copy()

        return self._default_voice_info(target_voice)


# 便捷函数
def create_edge_tts(
    voice: str = "en-US-AriaNeural",
    rate: str = "+0%",
    pitch: str = "+0Hz",
    volume: str = "+0%",
) -> EdgeTTS:
    """
    创建EdgeTTS实例的便捷函数

    Args:
        voice: 语音名称
        rate: 语速
        pitch: 音调
        volume: 音量

    Returns:
        EdgeTTS实例
    """
    config = {"voice": voice, "rate": rate, "pitch": pitch, "volume": volume}
    return EdgeTTS(config)


def synthesize_text(
    text: str, output_path: str, voice: str = "en-US-AriaNeural", rate: str = "+0%"
) -> bool:
    """
    合成文本到文件的便捷函数

    Args:
        text: 要合成的文本
        output_path: 输出文件路径
        voice: 语音名称
        rate: 语速

    Returns:
        是否成功
    """
    tts = create_edge_tts(voice=voice, rate=rate)
    return tts.synthesize_to_file(text, output_path)


def get_available_voices() -> List[str]:
    """
    获取可用语音列表的便捷函数

    Returns:
        语音名称列表
    """
    tts = create_edge_tts()
    return tts.get_supported_voices()
