# -*- coding: utf-8 -*-
"""
阿里云语音识别实现
支持实时语音识别和一句话识别
"""

import json
import time
import threading
from dataclasses import dataclass, field
from functools import partial
from typing import Optional, Dict, Any

try:
    import nls
except ImportError:
    nls = None

from .base import ASRBase, ASRError
from ...utils.encoding_fix import safe_str


@dataclass
class _RecognitionState:
    """Callback state owned by one transcriber, including late callbacks."""

    result: Optional[str] = None
    error: Optional[str] = None
    completed: threading.Event = field(default_factory=threading.Event)


class AliyunASR(ASRBase):
    """
    阿里云语音识别实现
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化阿里云语音识别

        Args:
            config: 配置参数，支持以下参数：
                - appkey: 阿里云应用密钥
                - token: 访问令牌
                - access_key_id: 访问密钥ID（用于获取token）
                - access_key_secret: 访问密钥（用于获取token）
                - url: WebSocket网关地址
                - audio_format: 音频格式
                - sample_rate: 采样率
                - enable_punctuation: 是否启用标点符号
                - enable_itn: 是否启用数字转换
        """
        super().__init__(config)

        if not nls:
            raise ImportError(
                "阿里云语音识别SDK未安装，请运行: pip install alibabacloud-nls-python-sdk"
            )

        self.appkey = self.config.get("appkey")
        self.token = self.config.get("token")
        self.access_key_id = self.config.get("access_key_id")
        self.access_key_secret = self.config.get("access_key_secret")
        self.url = self.config.get(
            "url", "wss://nls-gateway-cn-shanghai.aliyuncs.com/ws/v1"
        )

        # 音频参数
        self.audio_format = self.config.get("audio_format", "wav")
        self.sample_rate = self.config.get("sample_rate", 16000)
        self.enable_punctuation = self.config.get("enable_punctuation", True)
        self.enable_itn = self.config.get("enable_itn", True)

        # 如果没有token但有密钥，尝试获取token
        if not self.token and self.access_key_id and self.access_key_secret:
            self.token = self._get_token()

        if not self.appkey or not self.token:
            raise ASRError("阿里云语音识别配置不完整，需要appkey和token")

        self.logger.info("阿里云ASR初始化完成")

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
        import tempfile
        import os

        try:
            # 将字节数据保存为临时文件
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_file.write(audio_data)
                temp_path = temp_file.name

            # 识别临时文件
            result = self.recognize_file(temp_path, **kwargs)

            # 清理临时文件
            try:
                os.unlink(temp_path)
            except:
                pass

            return result

        except Exception as e:
            self.logger.error(f"识别音频字节数据失败: {e}")
            raise ASRError(f"识别音频字节数据失败: {e}")

    def recognize_file(self, audio_path: str, **kwargs) -> Optional[str]:
        """
        识别音频文件并返回文本

        Args:
            audio_path: 音频文件路径
            **kwargs: 其他参数，支持：
                - audio_format: 音频格式
                - sample_rate: 采样率
                - enable_punctuation: 是否启用标点符号
                - enable_itn: 是否启用数字转换

        Returns:
            识别结果文本，失败返回None

        Raises:
            ASRError: 识别过程中的错误
        """
        try:
            if not self.validate_audio_file(audio_path):
                return None

            # 获取参数
            audio_format = kwargs.get("audio_format", self.audio_format)
            sample_rate = kwargs.get("sample_rate", self.sample_rate)
            enable_punctuation = kwargs.get(
                "enable_punctuation", self.enable_punctuation
            )
            enable_itn = kwargs.get("enable_itn", self.enable_itn)

            # 每个识别器持有独立状态，避免并发或延迟回调串扰
            state = _RecognitionState()

            # 创建识别器
            transcriber = nls.NlsSpeechTranscriber(
                url=self.url,
                appkey=self.appkey,
                token=self.token,
                on_start=self._on_start,
                on_sentence_begin=self._on_sentence_begin,
                on_sentence_end=self._on_sentence_end,
                on_result_changed=self._on_result_changed,
                on_completed=partial(self._on_completed, state=state),
                on_error=partial(self._on_error, state=state),
                on_close=partial(self._on_close, state=state),
            )

            # 开始识别
            ret = transcriber.start(
                aformat=(
                    "pcm" if audio_format.lower() == "wav" else audio_format.lower()
                ),
                sample_rate=sample_rate,
                ch=1,
                enable_intermediate_result=False,
                enable_punctuation_prediction=enable_punctuation,
                enable_inverse_text_normalization=enable_itn,
            )

            if not ret:
                raise ASRError("启动阿里云语音识别失败")

            # 读取并发送音频数据
            with open(audio_path, "rb") as f:
                audio_data = f.read()

            # 跳过WAV文件头（44字节）
            if audio_format.lower() == "wav":
                audio_data = audio_data[44:]

            # 分块发送音频数据
            chunk_size = 3200  # 每次发送3200字节
            for i in range(0, len(audio_data), chunk_size):
                chunk = audio_data[i : i + chunk_size]
                transcriber.send_audio(chunk)
                time.sleep(0.01)  # 模拟实时音频流

            # 停止识别
            transcriber.stop()

            # 等待结果
            state.completed.wait(timeout=30)

            if state.error:
                raise ASRError(f"阿里云语音识别失败: {state.error}")

            return state.result

        except ASRError:
            raise
        except Exception as e:
            self.logger.error(f"阿里云语音识别异常: {e}")
            raise ASRError(f"阿里云语音识别异常: {e}")

    def _get_token(self) -> Optional[str]:
        """
        获取访问令牌

        Returns:
            访问令牌，失败返回None
        """
        try:
            if not self.access_key_id or not self.access_key_secret:
                self.logger.error("缺少访问密钥信息")
                return None

            token = nls.token.getToken(self.access_key_id, self.access_key_secret)
            self.logger.info("成功获取阿里云访问令牌")
            return token
        except Exception as e:
            self.logger.error(f"获取令牌失败: {e}")
            return None

    def test_connection(self) -> bool:
        """
        测试阿里云ASR服务连接

        Returns:
            连接是否正常
        """
        try:
            # 检查配置
            if not self.appkey or not self.token:
                return False

            # 尝试刷新token
            if self.access_key_id and self.access_key_secret:
                new_token = self._get_token()
                if new_token:
                    self.token = new_token
                    return True

            return True

        except Exception as e:
            self.logger.error(f"连接测试失败: {e}")
            return False

    def get_supported_formats(self) -> list:
        """
        获取支持的音频格式列表

        Returns:
            支持的音频格式列表
        """
        return ["wav", "pcm", "opus", "speex"]

    # 回调函数
    def _on_start(self, message, *args):
        """识别开始回调"""
        self.logger.debug(f"语音识别开始: {message}")

    def _on_sentence_begin(self, message, *args):
        """句子开始回调"""
        self.logger.debug(f"句子开始: {message}")

    def _on_sentence_end(self, message, *args):
        """句子结束回调"""
        self.logger.debug(f"句子结束: {message}")

    def _on_result_changed(self, message, *args):
        """中间结果回调"""
        try:
            msg_dict = json.loads(message)
            if "payload" in msg_dict and "result" in msg_dict["payload"]:
                result = msg_dict["payload"]["result"]
                self.logger.debug(f"中间结果: {result}")
        except Exception as e:
            self.logger.warning(f"解析中间结果失败: {e}")

    def _on_completed(self, message, *args, state: _RecognitionState):
        """识别完成回调"""
        try:
            msg_dict = json.loads(message)
            if "payload" in msg_dict and "result" in msg_dict["payload"]:
                state.result = msg_dict["payload"]["result"]
                self.logger.info(f"识别完成: {state.result}")
        except Exception as e:
            self.logger.error(f"解析最终结果失败: {e}")
            state.error = safe_str(e)
        finally:
            state.completed.set()

    def _on_error(self, message, *args, state: _RecognitionState):
        """错误回调"""
        self.logger.error(f"识别错误: {message}")
        state.error = message
        state.completed.set()

    def _on_close(self, *args, state: _RecognitionState):
        """连接关闭回调"""
        self.logger.debug("连接已关闭")
        state.completed.set()


# 便捷函数
def create_aliyun_asr(
    appkey: str, access_key_id: str, access_key_secret: str, **kwargs
) -> AliyunASR:
    """
    创建阿里云ASR实例的便捷函数

    Args:
        appkey: 阿里云应用密钥
        access_key_id: 访问密钥ID
        access_key_secret: 访问密钥
        **kwargs: 其他配置参数

    Returns:
        AliyunASR实例
    """
    config = {
        "appkey": appkey,
        "access_key_id": access_key_id,
        "access_key_secret": access_key_secret,
        **kwargs,
    }
    return AliyunASR(config)


def recognize_audio_file(
    audio_file_path: str, appkey: str, access_key_id: str, access_key_secret: str
) -> Optional[str]:
    """
    识别音频文件的便捷函数

    Args:
        audio_file_path: 音频文件路径
        appkey: 阿里云应用密钥
        access_key_id: 访问密钥ID
        access_key_secret: 访问密钥

    Returns:
        识别结果文本，失败返回None
    """
    asr = create_aliyun_asr(appkey, access_key_id, access_key_secret)
    return asr.recognize_file(audio_file_path)
