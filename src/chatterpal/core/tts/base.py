# -*- coding: utf-8 -*-
"""
TTS基类定义
定义统一的语音合成接口
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass
from enum import Enum
import base64
import glob
import hashlib
import json
import logging
import math
import os
import tempfile
import time
import threading

from ...utils.encoding_fix import safe_str

logger = logging.getLogger(__name__)


class TTSError(Exception):
    """语音合成错误基类"""
    pass


class TTSCacheError(TTSError):
    """TTS缓存错误"""
    pass


class TTSPlaybackError(TTSError):
    """TTS播放错误"""
    pass


class PlaybackState(Enum):
    """播放状态"""
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class TTSResult:
    """TTS合成结果"""
    text: str
    audio_data: bytes
    duration: float
    format: str
    sample_rate: int
    cached: bool
    synthesis_time: float
    metadata: Dict[str, Any]


@dataclass
class CacheEntry:
    """缓存条目"""
    key: str
    audio_data: bytes
    metadata: Dict[str, Any]
    created_at: float
    access_count: int
    last_accessed: float


class TTSBase(ABC):
    """
    语音合成基类
    定义统一的TTS接口，所有TTS实现都应继承此类
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化TTS实例

        Args:
            config: 配置参数字典
        """
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # 缓存配置
        self.enable_cache = self.config.get("enable_cache", True)
        self.cache_size_limit = self.config.get("cache_size_limit", 100)  # 最大缓存条目数
        self.cache_ttl = self.config.get("cache_ttl", 3600)  # 缓存生存时间（秒）
        self.cache_dir = self.config.get("cache_dir", None)  # 持久化缓存目录
        
        # 播放控制配置
        self.auto_play = self.config.get("auto_play", False)
        self.playback_volume = self.config.get("playback_volume", 1.0)
        
        # 内部状态
        self._cache = {}  # 内存缓存
        self._cache_lock = threading.Lock()
        self._playback_state = PlaybackState.STOPPED
        self._current_audio = None
        self._playback_callbacks = []
        
        # 初始化可选的磁盘缓存；失败时保留内存缓存。
        if self.enable_cache and self.cache_dir:
            self._init_cache_dir()

    @abstractmethod
    def synthesize(self, text: str, **kwargs) -> bytes:
        """
        合成语音并返回音频数据

        Args:
            text: 要合成的文本
            **kwargs: 其他参数

        Returns:
            音频字节数据

        Raises:
            TTSError: 合成过程中的错误
        """
        pass

    @abstractmethod
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
        pass

    def validate_text(self, text: str) -> bool:
        """
        验证输入文本是否有效

        Args:
            text: 输入文本

        Returns:
            文本是否有效
        """
        if not text or not isinstance(text, str):
            self.logger.error("输入文本为空或不是字符串")
            return False

        if len(text.strip()) == 0:
            self.logger.error("输入文本为空白字符")
            return False

        # 检查文本长度限制
        max_length = self.config.get("max_text_length", 5000)
        if len(text) > max_length:
            self.logger.warning(f"文本长度超过限制: {len(text)} > {max_length}")
            return False

        return True

    def clean_text_for_tts(self, text: str) -> str:
        """
        清理文本以适合TTS合成

        Args:
            text: 原始文本

        Returns:
            清理后的文本
        """
        if not text:
            return ""

        # 移除或替换不适合语音合成的字符
        import re

        # 保留字母、数字、空格和基本标点符号
        cleaned_text = re.sub(r"[^\w\s\.\,\!\?\;\:\-\'\"]", " ", text)

        # 合并多个空格为单个空格
        cleaned_text = re.sub(r"\s+", " ", cleaned_text)

        # 去除首尾空格
        cleaned_text = cleaned_text.strip()

        return cleaned_text

    def synthesize_with_error_handling(
        self, 
        text: str, 
        max_retries: int = 3,
        **kwargs
    ) -> TTSResult:
        """
        带错误处理和重试机制的语音合成
        
        Args:
            text: 要合成的文本
            max_retries: 最大重试次数
            **kwargs: 其他参数
            
        Returns:
            TTSResult: 合成结果
            
        Raises:
            SpeechSynthesisError: 语音合成错误
        """
        from ..errors import error_handler, SpeechSynthesisError
        
        # 验证输入文本
        if not self.validate_text(text):
            raise error_handler.create_error("TTS_SERVICE_ERROR", 
                                           message="输入文本无效",
                                           text_length=len(text) if text else 0)
        
        # 清理文本
        cleaned_text = self.clean_text_for_tts(text)
        if not cleaned_text:
            raise error_handler.create_error("TTS_SERVICE_ERROR", 
                                           message="清理后的文本为空",
                                           original_text=text)
        
        # 检查缓存
        if self.enable_cache:
            cached_result = self._get_from_cache(cleaned_text, kwargs)
            if cached_result:
                self.logger.info(f"TTS缓存命中: {cleaned_text[:50]}...")
                self._auto_play_result(cached_result)
                return cached_result
        
        # 执行合成（带重试）
        last_error = None
        for attempt in range(max_retries):
            try:
                start_time = time.time()
                
                # 调用实际的合成方法
                audio_data = self.synthesize(cleaned_text, **kwargs)
                
                synthesis_time = time.time() - start_time
                
                # 检查合成时间
                max_synthesis_time = self.config.get("max_synthesis_time", 30.0)
                if synthesis_time > max_synthesis_time:
                    raise error_handler.create_error("TTS_TIMEOUT", 
                                                   synthesis_time=synthesis_time,
                                                   max_time=max_synthesis_time)
                
                # 验证音频数据
                if not audio_data or len(audio_data) == 0:
                    raise error_handler.create_error("TTS_SERVICE_ERROR", 
                                                   message="合成的音频数据为空")
                
                # 创建结果对象
                result = TTSResult(
                    text=cleaned_text,
                    audio_data=audio_data,
                    duration=self._estimate_audio_duration(audio_data),
                    format=self.config.get("audio_format", "wav"),
                    sample_rate=self.config.get("sample_rate", 16000),
                    cached=False,
                    synthesis_time=synthesis_time,
                    metadata={
                        "attempt": attempt + 1,
                        "original_text": text,
                        "text_length": len(cleaned_text)
                    }
                )
                
            except SpeechSynthesisError:
                # 重新抛出已知错误
                raise
            except Exception as e:
                last_error = error_handler.create_error("TTS_SERVICE_ERROR",
                                                      attempt=attempt + 1,
                                                      error_message=safe_str(e),
                                                      text_length=len(cleaned_text))
                
                if attempt < max_retries - 1:
                    self.logger.warning(f"TTS合成失败，重试 {attempt + 1}/{max_retries}: {e}")
                    time.sleep(0.5 * (attempt + 1))  # 递增延迟
                    continue
                else:
                    # 最后一次尝试失败
                    error_handler.log_error(last_error, {
                        "text": cleaned_text[:100],
                        "text_length": len(cleaned_text)
                    })
                    raise last_error

            # 合成已成功；缓存和播放失败都不能触发重新合成。
            if self.enable_cache:
                try:
                    self._save_to_cache(cleaned_text, result, kwargs)
                except Exception as e:
                    self.logger.warning(f"缓存保存失败: {e}")
            if attempt > 0:
                self.logger.info(f"TTS合成在第 {attempt + 1} 次尝试后成功")
            self._auto_play_result(result)
            return result

        # 如果所有重试都失败了
        if last_error:
            raise last_error
        else:
            raise error_handler.create_error("TTS_SERVICE_ERROR")

    def _auto_play_result(self, result: TTSResult) -> None:
        """可选播放失败时仍保留并返回已合成的音频。"""
        if not self.auto_play:
            return
        try:
            if self.play_audio(result.audio_data):
                return
            self.logger.warning("自动播放失败，保留已合成的音频")
        except Exception as e:
            self.logger.warning(f"自动播放失败，保留已合成的音频: {e}")
        self._playback_state = PlaybackState.ERROR

    def _get_from_cache(self, text: str, kwargs: Dict[str, Any]) -> Optional[TTSResult]:
        """从内存或磁盘获取结果，损坏或过期的缓存视为未命中。"""
        if not self.enable_cache or self.cache_size_limit <= 0:
            return None
        try:
            cache_key = self._generate_cache_key(text, kwargs)
            with self._cache_lock:
                entry = self._cache.get(cache_key)
                if entry is None:
                    entry = self._load_from_disk_cache(cache_key)
                if entry is None:
                    return None

                now = time.time()
                if now - entry.created_at > self.cache_ttl:
                    self._cache.pop(cache_key, None)
                    self._remove_from_disk_cache(cache_key)
                    return None

                if cache_key not in self._cache:
                    self._evict_cache_entries(cache_key)
                    self._cache[cache_key] = entry
                entry.access_count += 1
                entry.last_accessed = now
                self._save_to_disk_cache(entry)

                return TTSResult(
                    text=text,
                    audio_data=entry.audio_data,
                    duration=self._estimate_audio_duration(entry.audio_data),
                    format=self.config.get("audio_format", "wav"),
                    sample_rate=self.config.get("sample_rate", 16000),
                    cached=True,
                    synthesis_time=0.0,
                    metadata=entry.metadata.copy()
                )
        except Exception as e:
            self.logger.warning(f"缓存读取失败: {e}")
            return None

    def _save_to_cache(self, text: str, result: TTSResult, kwargs: Dict[str, Any]) -> None:
        """保存结果到内存和可选的磁盘缓存。"""
        if not self.enable_cache or self.cache_size_limit <= 0:
            return
        try:
            cache_key = self._generate_cache_key(text, kwargs)
            with self._cache_lock:
                if cache_key not in self._cache:
                    self._evict_cache_entries(cache_key)
                now = time.time()
                entry = CacheEntry(
                    key=cache_key,
                    audio_data=result.audio_data,
                    metadata=result.metadata.copy(),
                    created_at=now,
                    access_count=1,
                    last_accessed=now
                )
                self._cache[cache_key] = entry
                self._save_to_disk_cache(entry)
        except Exception as e:
            self.logger.warning(f"缓存保存失败: {e}")

    def _generate_cache_key(self, text: str, kwargs: Dict[str, Any]) -> str:
        """生成缓存键"""
        # 创建包含文本和参数的字符串
        key_data = f"{text}|{sorted(kwargs.items())}"
        return hashlib.md5(key_data.encode()).hexdigest()

    def _evict_cache_entries(self, incoming_key: Optional[str] = None) -> None:
        """为新条目腾出空间，同时清理内存和磁盘中的过期或冷门记录。"""
        entries = self._cache.copy()
        if self.cache_dir:
            try:
                cache_files = glob.glob(os.path.join(self.cache_dir, "*.cache"))
                for cache_file in cache_files:
                    key = os.path.basename(cache_file)[:-6]
                    if key not in entries and key != incoming_key:
                        entry = self._load_from_disk_cache(key)
                        if entry is not None:
                            entries[key] = entry
            except OSError as e:
                self.logger.warning(f"扫描磁盘缓存失败: {e}")

        current_time = time.time()
        for key, entry in list(entries.items()):
            if current_time - entry.created_at > self.cache_ttl:
                entries.pop(key)
                self._cache.pop(key, None)
                self._remove_from_disk_cache(key)

        candidates = sorted(
            ((key, entry) for key, entry in entries.items() if key != incoming_key),
            key=lambda item: (item[1].access_count, item[1].last_accessed)
        )
        entries_to_remove = max(0, len(candidates) - self.cache_size_limit + 1)
        for key, _ in candidates[:entries_to_remove]:
            self._cache.pop(key, None)
            self._remove_from_disk_cache(key)

    def _estimate_audio_duration(self, audio_data: bytes) -> float:
        """估算音频时长"""
        try:
            # 简单估算：假设是16位PCM，16kHz采样率
            sample_rate = self.config.get("sample_rate", 16000)
            bytes_per_sample = 2  # 16位 = 2字节
            
            # 尝试从WAV头部获取信息
            if len(audio_data) > 44 and audio_data[:4] == b'RIFF':
                # WAV文件格式
                import struct
                try:
                    # 读取采样率（字节44-47）
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

    def play_audio_with_error_handling(self, audio_data: bytes) -> bool:
        """
        带错误处理的音频播放
        
        Args:
            audio_data: 音频数据
            
        Returns:
            是否播放成功
        """
        from ..errors import error_handler, AudioOutputError
        
        try:
            if not audio_data:
                raise error_handler.create_error("AUDIO_PLAYBACK_ERROR", 
                                                message="音频数据为空")
            
            # 尝试播放音频
            success = self._play_audio_internal(audio_data)
            
            if not success:
                raise error_handler.create_error("AUDIO_PLAYBACK_ERROR",
                                                message="音频播放失败")
            
            return True
            
        except AudioOutputError:
            raise
        except Exception as e:
            error = error_handler.create_error("AUDIO_PLAYBACK_ERROR", 
                                             error_message=safe_str(e))
            error_handler.log_error(error, {"audio_size": len(audio_data) if audio_data else 0})
            return False

    def _play_audio_internal(self, audio_data: bytes) -> bool:
        """
        内部音频播放方法（子类可以重写）
        
        Args:
            audio_data: 音频数据
            
        Returns:
            是否播放成功
        """
        # 默认实现：尝试使用系统播放器
        try:
            import tempfile
            import os
            import subprocess
            
            # 创建临时文件
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
                temp_file.write(audio_data)
                temp_path = temp_file.name
            
            try:
                # 尝试播放（Windows）
                if os.name == 'nt':
                    import winsound
                    winsound.PlaySound(temp_path, winsound.SND_FILENAME)
                    return True
                else:
                    # Linux/Mac
                    subprocess.run(['aplay', temp_path], check=True, 
                                 capture_output=True, timeout=30)
                    return True
            finally:
                # 清理临时文件
                try:
                    os.unlink(temp_path)
                except:
                    pass
                    
        except Exception as e:
            self.logger.warning(f"音频播放失败: {e}")
            return False

    def get_supported_voices(self) -> List[str]:
        """
        获取支持的语音列表

        Returns:
            支持的语音列表
        """
        # 默认返回空列表，子类应该重写此方法
        return []

    def get_supported_formats(self) -> List[str]:
        """
        获取支持的音频格式列表

        Returns:
            支持的音频格式列表
        """
        # 默认支持的格式，子类可以重写
        return ["wav", "mp3"]

    def test_connection(self) -> bool:
        """
        测试TTS服务连接

        Returns:
            连接是否正常
        """
        try:
            # 尝试合成一个简单的测试文本
            test_text = "Hello, this is a test."
            result = self.synthesize(test_text)
            return result is not None and len(result) > 0
        except Exception as e:
            self.logger.error(f"连接测试失败: {e}")
            return False

    def estimate_duration(self, text: str, words_per_minute: int = 150) -> float:
        """
        估算语音合成的时长

        Args:
            text: 输入文本
            words_per_minute: 每分钟单词数

        Returns:
            估算的时长（秒）
        """
        if not text:
            return 0.0

        # 简单的单词计数
        words = len(text.split())

        # 计算时长（秒）
        duration = (words / words_per_minute) * 60

        return max(duration, 1.0)  # 最少1秒

    def validate_output_path(self, output_path: str) -> bool:
        """
        验证输出路径是否有效

        Args:
            output_path: 输出文件路径

        Returns:
            路径是否有效
        """
        if not output_path:
            self.logger.error("输出路径为空")
            return False

        # 检查目录是否存在，如果不存在则创建
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir, exist_ok=True)
                self.logger.info(f"创建输出目录: {output_dir}")
            except Exception as e:
                self.logger.error(f"创建输出目录失败: {e}")
                return False

        # 检查文件扩展名
        file_ext = os.path.splitext(output_path)[1].lower().lstrip(".")
        if file_ext not in self.get_supported_formats():
            self.logger.warning(f"输出格式可能不受支持: {file_ext}")

        return True

    def get_audio_info(self, audio_data: bytes) -> Dict[str, Any]:
        """
        获取音频数据的基本信息

        Args:
            audio_data: 音频字节数据

        Returns:
            音频信息字典
        """
        info = {
            "size_bytes": len(audio_data),
            "format": "unknown",
            "duration": 0.0,
            "sample_rate": 0,
            "channels": 0,
        }

        try:
            # 尝试使用librosa分析音频
            import librosa
            import io
            import soundfile as sf

            # 将字节数据转换为音频
            audio_io = io.BytesIO(audio_data)
            data, sr = sf.read(audio_io)

            info.update(
                {
                    "duration": len(data) / sr,
                    "sample_rate": sr,
                    "channels": 1 if data.ndim == 1 else data.shape[1],
                    "format": "detected",
                }
            )

        except ImportError:
            self.logger.warning("librosa或soundfile未安装，无法分析音频信息")
        except Exception as e:
            self.logger.warning(f"分析音频信息失败: {e}")

        return info

    def _init_cache_dir(self) -> None:
        """初始化可选缓存目录，目录不可用时退回内存缓存。"""
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
        except (OSError, TypeError, ValueError) as e:
            self.logger.warning(f"初始化磁盘缓存失败，使用内存缓存: {e}")
            self.cache_dir = None

    def _load_from_disk_cache(self, cache_key: str) -> Optional[CacheEntry]:
        """只读取经过验证的 JSON 数据，不反序列化可执行对象。"""
        if not self.cache_dir:
            return None
        try:
            cache_file = os.path.join(self.cache_dir, f"{cache_key}.cache")
            with open(cache_file, "r", encoding="utf-8") as source:
                data = json.load(source)
            if (
                not isinstance(data, dict)
                or type(data.get("version")) is not int
                or data["version"] != 1
                or data.get("key") != cache_key
                or not isinstance(data.get("metadata"), dict)
                or not isinstance(data.get("audio_data"), str)
                or type(data.get("access_count")) is not int
                or data["access_count"] < 1
                or any(
                    type(data.get(field)) not in (int, float)
                    or not math.isfinite(data[field])
                    for field in ("created_at", "last_accessed")
                )
            ):
                raise ValueError("缓存数据格式无效")
            audio_data = base64.b64decode(data["audio_data"], validate=True)
            if (
                not audio_data
                or hashlib.sha256(audio_data).hexdigest() != data.get("sha256")
            ):
                raise ValueError("缓存音频数据无效")
            return CacheEntry(
                key=cache_key,
                audio_data=audio_data,
                metadata=data["metadata"],
                created_at=data["created_at"],
                access_count=data["access_count"],
                last_accessed=data["last_accessed"]
            )
        except FileNotFoundError:
            return None
        except Exception as e:
            self.logger.warning(f"读取磁盘缓存失败，忽略该条目: {e}")
            self._remove_from_disk_cache(cache_key)
            return None

    def _remove_from_disk_cache(self, cache_key: str) -> None:
        if not self.cache_dir:
            return
        try:
            os.unlink(os.path.join(self.cache_dir, f"{cache_key}.cache"))
        except FileNotFoundError:
            pass
        except OSError as e:
            self.logger.warning(f"删除磁盘缓存失败: {e}")

    def _save_to_disk_cache(self, entry: CacheEntry) -> None:
        """在同一目录写入临时文件，再原子替换完整的缓存记录。"""
        if not self.cache_dir:
            return
        temp_path = None
        try:
            payload = json.dumps(
                {
                    "version": 1,
                    "key": entry.key,
                    "audio_data": base64.b64encode(entry.audio_data).decode("ascii"),
                    "sha256": hashlib.sha256(entry.audio_data).hexdigest(),
                    "metadata": entry.metadata,
                    "created_at": entry.created_at,
                    "access_count": entry.access_count,
                    "last_accessed": entry.last_accessed
                },
                ensure_ascii=False,
                allow_nan=False
            )
            cache_file = os.path.join(self.cache_dir, f"{entry.key}.cache")
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.cache_dir, suffix=".tmp", delete=False
            ) as output:
                temp_path = output.name
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_path, cache_file)
        except Exception as e:
            self.logger.warning(f"保存到磁盘缓存失败: {e}")
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass
                except OSError as e:
                    self.logger.warning(f"清理缓存临时文件失败: {e}")

    def clear_cache(self) -> None:
        """
        清空缓存
        """
        with self._cache_lock:
            self._cache.clear()
            if self.cache_dir:
                for cache_file in glob.glob(os.path.join(self.cache_dir, "*.cache")):
                    key = os.path.basename(cache_file)[:-6]
                    self._remove_from_disk_cache(key)
        self.logger.info("TTS缓存已清空")

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        获取缓存统计信息
        
        Returns:
            Dict[str, Any]: 缓存统计信息
        """
        with self._cache_lock:
            total_size = sum(len(entry.audio_data) for entry in self._cache.values())
            total_access = sum(entry.access_count for entry in self._cache.values())
            
            return {
                "entries": len(self._cache),
                "total_size_bytes": total_size,
                "total_access_count": total_access,
                "cache_hit_rate": 0.0,  # 需要在实际使用中计算
                "oldest_entry": min(
                    (entry.created_at for entry in self._cache.values()),
                    default=0
                ),
                "newest_entry": max(
                    (entry.created_at for entry in self._cache.values()),
                    default=0
                )
            }

    # 播放控制接口
    def play_audio(self, audio_data: bytes, **kwargs) -> bool:
        """
        播放音频数据
        
        Args:
            audio_data: 音频数据
            **kwargs: 播放参数
            
        Returns:
            bool: 是否成功开始播放
        """
        try:
            # 尝试使用pygame播放
            return self._play_with_pygame(audio_data, **kwargs)
        except ImportError:
            try:
                # 尝试使用playsound播放
                return self._play_with_playsound(audio_data, **kwargs)
            except ImportError:
                try:
                    # 尝试使用系统命令播放
                    return self._play_with_system(audio_data, **kwargs)
                except Exception as e:
                    self.logger.error(f"音频播放失败: {e}")
                    self._playback_state = PlaybackState.ERROR
                    return False

    def _play_with_pygame(self, audio_data: bytes, **kwargs) -> bool:
        """
        使用pygame播放音频
        
        Args:
            audio_data: 音频数据
            **kwargs: 播放参数
            
        Returns:
            bool: 是否成功
        """
        try:
            import pygame
            import io
            
            # 初始化pygame mixer
            pygame.mixer.init()
            
            # 加载音频数据
            audio_io = io.BytesIO(audio_data)
            sound = pygame.mixer.Sound(audio_io)
            
            # 设置音量
            volume = kwargs.get("volume", self.playback_volume)
            sound.set_volume(volume)
            
            # 播放音频
            self._playback_state = PlaybackState.PLAYING
            self._current_audio = sound
            sound.play()
            
            # 通知回调
            self._notify_playback_callbacks("started")
            
            return True
            
        except Exception as e:
            self.logger.error(f"pygame播放失败: {e}")
            raise ImportError("pygame播放失败")

    def _play_with_playsound(self, audio_data: bytes, **kwargs) -> bool:
        """
        使用playsound播放音频
        
        Args:
            audio_data: 音频数据
            **kwargs: 播放参数
            
        Returns:
            bool: 是否成功
        """
        try:
            import playsound
            import tempfile
            
            # 保存到临时文件
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_file.write(audio_data)
                temp_path = temp_file.name
            
            try:
                self._playback_state = PlaybackState.PLAYING
                self._notify_playback_callbacks("started")
                
                # 播放音频
                playsound.playsound(temp_path)
                
                self._playback_state = PlaybackState.STOPPED
                self._notify_playback_callbacks("finished")
                
                return True
                
            finally:
                try:
                    os.unlink(temp_path)
                except:
                    pass
                    
        except Exception as e:
            self.logger.error(f"playsound播放失败: {e}")
            raise ImportError("playsound播放失败")

    def _play_with_system(self, audio_data: bytes, **kwargs) -> bool:
        """
        使用系统命令播放音频
        
        Args:
            audio_data: 音频数据
            **kwargs: 播放参数
            
        Returns:
            bool: 是否成功
        """
        try:
            import tempfile
            import subprocess
            import platform
            
            # 保存到临时文件
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_file.write(audio_data)
                temp_path = temp_file.name
            
            try:
                self._playback_state = PlaybackState.PLAYING
                self._notify_playback_callbacks("started")
                
                # 根据操作系统选择播放命令
                system = platform.system().lower()
                if system == "windows":
                    subprocess.run(["start", temp_path], shell=True, check=True)
                elif system == "darwin":  # macOS
                    subprocess.run(["afplay", temp_path], check=True)
                elif system == "linux":
                    subprocess.run(["aplay", temp_path], check=True)
                else:
                    raise Exception(f"不支持的操作系统: {system}")
                
                self._playback_state = PlaybackState.STOPPED
                self._notify_playback_callbacks("finished")
                
                return True
                
            finally:
                try:
                    os.unlink(temp_path)
                except:
                    pass
                    
        except Exception as e:
            self.logger.error(f"系统命令播放失败: {e}")
            raise Exception("系统命令播放失败")

    def stop_playback(self) -> bool:
        """
        停止播放
        
        Returns:
            bool: 是否成功停止
        """
        try:
            if self._current_audio and hasattr(self._current_audio, "stop"):
                self._current_audio.stop()
            
            self._playback_state = PlaybackState.STOPPED
            self._current_audio = None
            self._notify_playback_callbacks("stopped")
            
            return True
            
        except Exception as e:
            self.logger.error(f"停止播放失败: {e}")
            return False

    def pause_playback(self) -> bool:
        """
        暂停播放
        
        Returns:
            bool: 是否成功暂停
        """
        try:
            if self._current_audio and hasattr(self._current_audio, "pause"):
                self._current_audio.pause()
                self._playback_state = PlaybackState.PAUSED
                self._notify_playback_callbacks("paused")
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"暂停播放失败: {e}")
            return False

    def resume_playback(self) -> bool:
        """
        恢复播放
        
        Returns:
            bool: 是否成功恢复
        """
        try:
            if self._current_audio and hasattr(self._current_audio, "unpause"):
                self._current_audio.unpause()
                self._playback_state = PlaybackState.PLAYING
                self._notify_playback_callbacks("resumed")
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"恢复播放失败: {e}")
            return False

    def get_playback_state(self) -> PlaybackState:
        """
        获取播放状态
        
        Returns:
            PlaybackState: 当前播放状态
        """
        return self._playback_state

    def add_playback_callback(self, callback: Callable[[str], None]) -> None:
        """
        添加播放状态回调
        
        Args:
            callback: 回调函数，接收状态字符串参数
        """
        self._playback_callbacks.append(callback)

    def remove_playback_callback(self, callback: Callable[[str], None]) -> None:
        """
        移除播放状态回调
        
        Args:
            callback: 要移除的回调函数
        """
        if callback in self._playback_callbacks:
            self._playback_callbacks.remove(callback)

    def _notify_playback_callbacks(self, event: str) -> None:
        """
        通知播放状态回调
        
        Args:
            event: 事件名称
        """
        for callback in self._playback_callbacks:
            try:
                callback(event)
            except Exception as e:
                self.logger.error(f"播放回调执行失败: {e}")

    def set_playback_volume(self, volume: float) -> None:
        """
        设置播放音量
        
        Args:
            volume: 音量 (0.0-1.0)
        """
        self.playback_volume = max(0.0, min(1.0, volume))
        
        # 如果当前有音频在播放，更新音量
        if self._current_audio and hasattr(self._current_audio, "set_volume"):
            self._current_audio.set_volume(self.playback_volume)

    def get_playback_volume(self) -> float:
        """
        获取播放音量
        
        Returns:
            float: 当前音量
        """
        return self.playback_volume
