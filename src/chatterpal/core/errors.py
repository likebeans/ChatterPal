# -*- coding: utf-8 -*-
"""
错误处理模块
定义统一的错误类型和错误处理机制
"""

from typing import Dict, Any, Optional, List
from enum import Enum
from dataclasses import dataclass
from copy import deepcopy
import logging

logger = logging.getLogger(__name__)


class ErrorSeverity(Enum):
    """错误严重程度"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorCategory(Enum):
    """错误分类"""
    AUDIO_INPUT = "audio_input"
    TEXT_INPUT = "text_input"
    AUDIO_OUTPUT = "audio_output"
    SPEECH_RECOGNITION = "speech_recognition"
    SPEECH_SYNTHESIS = "speech_synthesis"
    TOPIC_GENERATION = "topic_generation"
    NETWORK = "network"
    SYSTEM = "system"


@dataclass
class ErrorInfo:
    """错误信息数据类"""
    code: str
    message: str
    category: ErrorCategory
    severity: ErrorSeverity
    user_message: str
    suggestions: List[str]
    metadata: Dict[str, Any]


class ChatModuleError(Exception):
    """聊天模块基础错误"""
    
    def __init__(self, error_info: ErrorInfo):
        self.error_info = error_info
        super().__init__(error_info.message)


class AudioInputError(ChatModuleError):
    """音频输入错误"""
    pass


class AudioOutputError(ChatModuleError):
    """音频输出错误"""
    pass


class SpeechRecognitionError(ChatModuleError):
    """语音识别错误"""
    pass


class SpeechSynthesisError(ChatModuleError):
    """语音合成错误"""
    pass


class TopicGenerationError(ChatModuleError):
    """主题生成错误"""
    pass


class ErrorHandler:
    """错误处理器"""
    
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self._init_error_definitions()
    
    def _init_error_definitions(self):
        """初始化错误定义"""
        self.error_definitions = {
            "TEXT_INPUT_EMPTY": ErrorInfo(
                code="TEXT_INPUT_EMPTY",
                message="文本输入为空",
                category=ErrorCategory.TEXT_INPUT,
                severity=ErrorSeverity.LOW,
                user_message="文本输入不能为空，请输入内容后重试。",
                suggestions=["请重新输入想要练习的文本", "也可以切换为语音输入"],
                metadata={}
            ),
            "AUDIO_PERMISSION_DENIED": ErrorInfo(
                code="AUDIO_PERMISSION_DENIED",
                message="音频访问权限被拒绝",
                category=ErrorCategory.AUDIO_INPUT,
                severity=ErrorSeverity.HIGH,
                user_message="无法访问音频，请检查麦克风或音频文件的访问权限。",
                suggestions=[
                    "在浏览器或系统设置中允许麦克风访问",
                    "检查上传文件的读取权限",
                    "也可以切换为文本输入"
                ],
                metadata={}
            ),
            # 音频输入错误
            "AUDIO_TOO_SHORT": ErrorInfo(
                code="AUDIO_TOO_SHORT",
                message="音频时长过短",
                category=ErrorCategory.AUDIO_INPUT,
                severity=ErrorSeverity.MEDIUM,
                user_message="录音时间太短了，请录制至少1秒的语音。",
                suggestions=[
                    "请录制更长的语音（至少1秒）",
                    "确保在录音时说话",
                    "检查麦克风是否正常工作"
                ],
                metadata={}
            ),
            "AUDIO_TOO_LONG": ErrorInfo(
                code="AUDIO_TOO_LONG",
                message="音频时长过长",
                category=ErrorCategory.AUDIO_INPUT,
                severity=ErrorSeverity.MEDIUM,
                user_message="录音时间太长了，请录制不超过60秒的语音。",
                suggestions=[
                    "请录制较短的语音（不超过60秒）",
                    "可以分段录制长内容",
                    "尝试简化要表达的内容"
                ],
                metadata={}
            ),
            "AUDIO_LOW_VOLUME": ErrorInfo(
                code="AUDIO_LOW_VOLUME",
                message="音频音量过低",
                category=ErrorCategory.AUDIO_INPUT,
                severity=ErrorSeverity.MEDIUM,
                user_message="录音音量太低，可能影响识别效果。",
                suggestions=[
                    "请靠近麦克风说话",
                    "提高说话音量",
                    "检查麦克风设置和权限",
                    "确保麦克风没有被静音"
                ],
                metadata={}
            ),
            "AUDIO_HIGH_NOISE": ErrorInfo(
                code="AUDIO_HIGH_NOISE",
                message="环境噪音过大",
                category=ErrorCategory.AUDIO_INPUT,
                severity=ErrorSeverity.MEDIUM,
                user_message="环境噪音较大，可能影响识别准确性。",
                suggestions=[
                    "请在安静的环境中录音",
                    "关闭背景音乐或电视",
                    "远离噪音源（如空调、风扇）",
                    "使用耳机麦克风以减少噪音"
                ],
                metadata={}
            ),
            "AUDIO_CLIPPING": ErrorInfo(
                code="AUDIO_CLIPPING",
                message="音频削波失真",
                category=ErrorCategory.AUDIO_INPUT,
                severity=ErrorSeverity.MEDIUM,
                user_message="录音音量过大，出现失真。",
                suggestions=[
                    "请降低说话音量",
                    "调整麦克风增益设置",
                    "保持适当的录音距离"
                ],
                metadata={}
            ),
            "AUDIO_MOSTLY_SILENCE": ErrorInfo(
                code="AUDIO_MOSTLY_SILENCE",
                message="音频主要为静音",
                category=ErrorCategory.AUDIO_INPUT,
                severity=ErrorSeverity.HIGH,
                user_message="录音中检测到的语音内容很少。",
                suggestions=[
                    "请确保在录音时说话",
                    "检查麦克风是否正常工作",
                    "确保麦克风权限已开启",
                    "尝试重新录制"
                ],
                metadata={}
            ),
            "AUDIO_FORMAT_ERROR": ErrorInfo(
                code="AUDIO_FORMAT_ERROR",
                message="音频格式错误",
                category=ErrorCategory.AUDIO_INPUT,
                severity=ErrorSeverity.HIGH,
                user_message="音频格式不支持或文件损坏。",
                suggestions=[
                    "请使用支持的音频格式（WAV、MP3等）",
                    "检查音频文件是否完整",
                    "尝试重新录制"
                ],
                metadata={}
            ),
            
            # 语音识别错误
            "ASR_LOW_CONFIDENCE": ErrorInfo(
                code="ASR_LOW_CONFIDENCE",
                message="语音识别置信度过低",
                category=ErrorCategory.SPEECH_RECOGNITION,
                severity=ErrorSeverity.MEDIUM,
                user_message="语音识别不够准确，请确认识别结果。",
                suggestions=[
                    "请说话清晰一些",
                    "尝试放慢语速",
                    "确保发音准确",
                    "如果识别错误，可以重新录制"
                ],
                metadata={}
            ),
            "ASR_TIMEOUT": ErrorInfo(
                code="ASR_TIMEOUT",
                message="语音识别超时",
                category=ErrorCategory.SPEECH_RECOGNITION,
                severity=ErrorSeverity.HIGH,
                user_message="语音识别处理超时，请重试。",
                suggestions=[
                    "请检查网络连接",
                    "尝试录制较短的语音",
                    "稍后再试"
                ],
                metadata={}
            ),
            "ASR_SERVICE_ERROR": ErrorInfo(
                code="ASR_SERVICE_ERROR",
                message="语音识别服务错误",
                category=ErrorCategory.SPEECH_RECOGNITION,
                severity=ErrorSeverity.HIGH,
                user_message="语音识别服务暂时不可用。",
                suggestions=[
                    "请检查网络连接",
                    "稍后再试",
                    "可以尝试使用文本输入"
                ],
                metadata={}
            ),
            "ASR_NO_SPEECH": ErrorInfo(
                code="ASR_NO_SPEECH",
                message="未检测到语音",
                category=ErrorCategory.SPEECH_RECOGNITION,
                severity=ErrorSeverity.MEDIUM,
                user_message="未能识别到语音内容。",
                suggestions=[
                    "请确保在录音时说话",
                    "检查麦克风是否正常",
                    "尝试提高说话音量",
                    "重新录制语音"
                ],
                metadata={}
            ),
            
            # 语音合成错误
            "TTS_SERVICE_ERROR": ErrorInfo(
                code="TTS_SERVICE_ERROR",
                message="语音合成服务错误",
                category=ErrorCategory.SPEECH_SYNTHESIS,
                severity=ErrorSeverity.MEDIUM,
                user_message="语音合成暂时不可用，但您仍可以看到文字回复。",
                suggestions=[
                    "请检查网络连接",
                    "稍后可以重新播放语音",
                    "文字回复不受影响"
                ],
                metadata={}
            ),
            "TTS_TIMEOUT": ErrorInfo(
                code="TTS_TIMEOUT",
                message="语音合成超时",
                category=ErrorCategory.SPEECH_SYNTHESIS,
                severity=ErrorSeverity.MEDIUM,
                user_message="语音合成处理超时。",
                suggestions=[
                    "请检查网络连接",
                    "可以稍后重试语音播放",
                    "文字内容仍然可用"
                ],
                metadata={}
            ),
            "AUDIO_PLAYBACK_ERROR": ErrorInfo(
                code="AUDIO_PLAYBACK_ERROR",
                message="音频播放错误",
                category=ErrorCategory.AUDIO_OUTPUT,
                severity=ErrorSeverity.MEDIUM,
                user_message="音频播放失败。",
                suggestions=[
                    "检查音频设备是否正常",
                    "确保音量设置合适",
                    "尝试刷新页面重试"
                ],
                metadata={}
            ),
            
            # 主题生成错误
            "TOPIC_GENERATION_FAILED": ErrorInfo(
                code="TOPIC_GENERATION_FAILED",
                message="主题生成失败",
                category=ErrorCategory.TOPIC_GENERATION,
                severity=ErrorSeverity.LOW,
                user_message="无法生成新主题，将使用默认主题。",
                suggestions=[
                    "可以手动输入想要讨论的话题",
                    "稍后可以重试生成主题",
                    "继续当前对话也很好"
                ],
                metadata={}
            ),
            
            # 网络错误
            "NETWORK_ERROR": ErrorInfo(
                code="NETWORK_ERROR",
                message="网络连接错误",
                category=ErrorCategory.NETWORK,
                severity=ErrorSeverity.HIGH,
                user_message="网络连接出现问题。",
                suggestions=[
                    "请检查网络连接",
                    "确保网络稳定",
                    "稍后重试"
                ],
                metadata={}
            )
        }
    
    def get_error_info(self, error_code: str) -> Optional[ErrorInfo]:
        """获取错误信息"""
        return self.error_definitions.get(error_code)
    
    def create_error(self, error_code: str, **kwargs) -> ChatModuleError:
        """创建错误对象"""
        error_info = deepcopy(self.get_error_info(error_code))
        if not error_info:
            # 创建通用错误
            error_info = ErrorInfo(
                code="UNKNOWN_ERROR",
                message=f"未知错误: {error_code}",
                category=ErrorCategory.SYSTEM,
                severity=ErrorSeverity.MEDIUM,
                user_message="发生了未知错误，请重试。",
                suggestions=["请重试操作", "如果问题持续，请联系技术支持"],
                metadata=kwargs
            )
        
        # 更新元数据
        error_info.metadata.update(kwargs)
        
        # 根据错误分类创建相应的错误对象
        if error_info.category == ErrorCategory.AUDIO_INPUT:
            return AudioInputError(error_info)
        elif error_info.category == ErrorCategory.AUDIO_OUTPUT:
            return AudioOutputError(error_info)
        elif error_info.category == ErrorCategory.SPEECH_RECOGNITION:
            return SpeechRecognitionError(error_info)
        elif error_info.category == ErrorCategory.SPEECH_SYNTHESIS:
            return SpeechSynthesisError(error_info)
        elif error_info.category == ErrorCategory.TOPIC_GENERATION:
            return TopicGenerationError(error_info)
        else:
            return ChatModuleError(error_info)
    
    def handle_audio_validation_error(self, validation_result) -> Optional[ChatModuleError]:
        """
        处理音频验证错误
        
        Args:
            validation_result: AudioValidationResult对象
            
        Returns:
            错误对象，如果没有错误则返回None
        """
        if validation_result.is_valid:
            return None
        
        # 根据验证问题确定错误类型
        issues = validation_result.issues
        metadata = validation_result.metadata
        
        # 检查时长问题（优先级最高）
        duration = validation_result.duration
        if duration < 1.0:
            return self.create_error("AUDIO_TOO_SHORT", duration=duration)
        elif duration > 60.0:
            return self.create_error("AUDIO_TOO_LONG", duration=duration)
        
        # 检查静音问题（优先级高于音量问题）
        silence_ratio = metadata.get('silence_ratio', 0)
        if silence_ratio > 0.8:
            return self.create_error("AUDIO_MOSTLY_SILENCE", silence_ratio=silence_ratio)
        
        # 检查音量问题
        max_amplitude = metadata.get('max_amplitude', 0)
        rms_amplitude = metadata.get('rms_amplitude', 0)
        
        if max_amplitude < 0.005 or rms_amplitude < 0.001:
            return self.create_error("AUDIO_LOW_VOLUME", 
                                   max_amplitude=max_amplitude, 
                                   rms_amplitude=rms_amplitude)
        elif max_amplitude > 0.95:
            return self.create_error("AUDIO_CLIPPING", max_amplitude=max_amplitude)
        
        # 检查噪音问题
        snr = metadata.get('snr', 0)
        if snr < 5:
            return self.create_error("AUDIO_HIGH_NOISE", snr=snr)
        
        # 如果有其他问题，创建通用音频格式错误
        if issues:
            return self.create_error("AUDIO_FORMAT_ERROR", issues=issues)
        
        return None
    
    def handle_asr_exception(self, error: Exception) -> ChatModuleError:
        """保留已分类错误，并将 ASR 边界的原始异常映射为用户提示。"""
        if isinstance(error, ChatModuleError):
            return error
        if isinstance(error, PermissionError):
            code = "AUDIO_PERMISSION_DENIED"
        elif isinstance(error, ValueError):
            code = "AUDIO_FORMAT_ERROR"
        elif isinstance(error, TimeoutError):
            code = "ASR_TIMEOUT"
        else:
            code = "ASR_SERVICE_ERROR"
        return self.create_error(code, error_message=str(error))

    def handle_asr_error(self, asr_result) -> Optional[ChatModuleError]:
        """
        处理ASR错误
        
        Args:
            asr_result: ASRResult对象
            
        Returns:
            错误对象，如果没有错误则返回None
        """
        metadata = asr_result.metadata or {}
        if "error" in metadata:
            code = metadata.get("error_code", "ASR_SERVICE_ERROR")
            if code not in self.error_definitions:
                code = "ASR_SERVICE_ERROR"
            return self.create_error(code, error_message=metadata["error"])
        if not asr_result.text:
            return self.create_error("ASR_NO_SPEECH")
        
        # 检查置信度
        if asr_result.confidence < 0.3:
            return self.create_error("ASR_LOW_CONFIDENCE", 
                                   confidence=asr_result.confidence,
                                   confidence_level=asr_result.confidence_level.value)
        
        # 检查处理时间
        if asr_result.processing_time > 30:
            return self.create_error("ASR_TIMEOUT", 
                                   processing_time=asr_result.processing_time)
        
        return None
    
    def format_user_error_message(self, error: ChatModuleError) -> Dict[str, Any]:
        """
        格式化用户错误消息
        
        Args:
            error: 错误对象
            
        Returns:
            格式化的错误信息
        """
        return {
            "error_code": error.error_info.code,
            "message": error.error_info.user_message,
            "suggestions": error.error_info.suggestions,
            "severity": error.error_info.severity.value,
            "category": error.error_info.category.value,
            "can_retry": error.error_info.severity in [ErrorSeverity.LOW, ErrorSeverity.MEDIUM]
        }
    
    def log_error(self, error: ChatModuleError, context: Optional[Dict[str, Any]] = None):
        """
        记录错误日志
        
        Args:
            error: 错误对象
            context: 额外的上下文信息
        """
        log_data = {
            "error_code": error.error_info.code,
            "category": error.error_info.category.value,
            "severity": error.error_info.severity.value,
            "message": error.error_info.message,
            "metadata": error.error_info.metadata
        }
        
        if context:
            log_data["context"] = context
        
        if error.error_info.severity == ErrorSeverity.CRITICAL:
            self.logger.critical(f"Critical error: {log_data}")
        elif error.error_info.severity == ErrorSeverity.HIGH:
            self.logger.error(f"High severity error: {log_data}")
        elif error.error_info.severity == ErrorSeverity.MEDIUM:
            self.logger.warning(f"Medium severity error: {log_data}")
        else:
            self.logger.info(f"Low severity error: {log_data}")


# 全局错误处理器实例
error_handler = ErrorHandler()