# -*- coding: utf-8 -*-
"""
对话服务
实现多轮对话上下文管理，集成ASR、TTS和LLM模块
"""

from typing import Optional, Dict, Any, List, Union, Tuple
import logging
import uuid
import time
from dataclasses import dataclass
from datetime import datetime
from threading import RLock

from ..core.asr.base import ASRBase, ASRError
from ..core.tts.base import TTSBase, TTSError
from ..core.llm.base import LLMBase, LLMError, Conversation, Message
from ..core.errors import (
    error_handler, ChatModuleError, AudioInputError, SpeechRecognitionError,
    SpeechSynthesisError, TopicGenerationError as TopicGenError
)
from ..utils.encoding_fix import safe_str
from .topic_generator import TopicGenerator, TopicGenerationError


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioChatResult:
    """一次语音对话的识别文本、回复和会话信息。"""

    recognized_text: str
    response_text: str
    response_audio: Optional[bytes]
    session_id: str


class ChatSession:
    """对话会话类，管理单个用户的对话状态"""

    def __init__(self, session_id: str = None, system_prompt: str = None):
        """
        初始化对话会话

        Args:
            session_id: 会话ID，如果为None则自动生成
            system_prompt: 系统提示词
        """
        self.session_id = session_id or str(uuid.uuid4())
        self.conversation = Conversation(system_prompt)
        self.created_at = datetime.now()
        self.last_activity = datetime.now()
        self.metadata = {}
        self.lock = RLock()

    def add_user_message(self, content: str, **kwargs) -> None:
        """添加用户消息"""
        with self.lock:
            self.conversation.add_user_message(content, **kwargs)
            self.last_activity = datetime.now()

    def add_assistant_message(self, content: str, **kwargs) -> None:
        """添加助手消息"""
        with self.lock:
            self.conversation.add_assistant_message(content, **kwargs)
            self.last_activity = datetime.now()

    def get_messages(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """获取消息列表"""
        with self.lock:
            return self.conversation.get_messages(limit)

    def clear_history(self, keep_system: bool = True) -> None:
        """清空对话历史"""
        with self.lock:
            self.conversation.clear(keep_system)
            self.last_activity = datetime.now()

    def set_metadata(self, key: str, value: Any) -> None:
        """设置元数据"""
        self.metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """获取元数据"""
        return self.metadata.get(key, default)


class ChatService:
    """
    对话服务类
    提供完整的对话功能，包括语音识别、文本对话、语音合成
    """

    def __init__(
        self,
        asr: Optional[ASRBase] = None,
        tts: Optional[TTSBase] = None,
        llm: Optional[LLMBase] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        初始化对话服务

        Args:
            asr: 语音识别实例
            tts: 语音合成实例
            llm: 大语言模型实例
            config: 配置参数
        """
        self.asr = asr
        self.tts = tts
        self.llm = llm
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)

        # 会话管理
        self.sessions: Dict[str, ChatSession] = {}
        
        # 初始化主题生成器
        topic_config = self.config.get("topic_generation", {})
        self.topic_generator = TopicGenerator(llm=self.llm, config=topic_config)

        # 默认配置
        default_prompt = """You are Alex, a professional and friendly English conversation coach with 10+ years of experience helping non-native speakers improve their English skills. Your personality is warm, encouraging, and patient.

## Your Role & Responsibilities:
1. **Conversation Partner**: Engage in natural, interesting conversations on various topics
2. **Language Coach**: Provide gentle corrections and suggestions for improvement
3. **Motivator**: Encourage users to practice more and build confidence
4. **Cultural Guide**: Share insights about English-speaking cultures when relevant

## Communication Style:
- Use clear, natural English appropriate for intermediate learners
- Be encouraging and positive in your feedback
- Ask follow-up questions to keep conversations flowing
- Vary your vocabulary to help users learn new words
- Use examples and explanations when correcting mistakes

## Correction Guidelines:
- Don't correct every small mistake - focus on major errors that affect understanding
- When correcting, use this format: "Great point! Just a small note: instead of '[incorrect]', you could say '[correct]'. For example: [example sentence]"
- Praise good usage and improvements
- Suggest alternative expressions to expand vocabulary

## Conversation Topics:
- Daily life and routines
- Hobbies and interests  
- Travel and culture
- Food and cooking
- Work and career
- Current events (keep it light and positive)
- Learning experiences

## Response Format:
- Keep responses conversational and natural
- Ask one follow-up question per response to maintain engagement
- Include 1-2 new vocabulary words naturally when appropriate
- End with encouragement when the user makes good progress

Remember: Your goal is to make English practice enjoyable and confidence-building while providing valuable learning opportunities."""

        self.default_system_prompt = self.config.get("default_system_prompt", default_prompt)
        self.max_history_length = self.config.get("max_history_length", 20)
        self.session_timeout = self.config.get("session_timeout", 3600)  # 1小时

    def create_session(
        self, session_id: Optional[str] = None, system_prompt: Optional[str] = None
    ) -> str:
        """
        创建新的对话会话

        Args:
            session_id: 指定会话ID，如果为None则自动生成
            system_prompt: 系统提示词，如果为None则使用默认值

        Returns:
            会话ID
        """
        if system_prompt is None:
            system_prompt = self.default_system_prompt

        session = ChatSession(session_id, system_prompt)
        self.sessions[session.session_id] = session

        self.logger.info(f"创建新对话会话: {session.session_id}")
        return session.session_id

    def get_session(self, session_id: str) -> Optional[ChatSession]:
        """
        获取对话会话

        Args:
            session_id: 会话ID

        Returns:
            对话会话对象，如果不存在则返回None
        """
        return self.sessions.get(session_id)

    def delete_session(self, session_id: str) -> bool:
        """
        删除对话会话

        Args:
            session_id: 会话ID

        Returns:
            是否成功删除
        """
        if session_id in self.sessions:
            del self.sessions[session_id]
            self.logger.info(f"删除对话会话: {session_id}")
            return True
        return False

    def cleanup_expired_sessions(self) -> int:
        """
        清理过期的会话

        Returns:
            清理的会话数量
        """
        current_time = datetime.now()
        expired_sessions = []

        for session_id, session in self.sessions.items():
            time_diff = (current_time - session.last_activity).total_seconds()
            if time_diff > self.session_timeout:
                expired_sessions.append(session_id)

        for session_id in expired_sessions:
            del self.sessions[session_id]

        if expired_sessions:
            self.logger.info(f"清理过期会话: {len(expired_sessions)}个")

        return len(expired_sessions)

    def chat_with_text(
        self, text: str, session_id: Optional[str] = None, **kwargs
    ) -> Tuple[str, str]:
        """
        文本对话

        Args:
            text: 用户输入文本
            session_id: 会话ID，如果为None则创建新会话
            **kwargs: 其他参数传递给LLM

        Returns:
            (回复文本, 会话ID)

        Raises:
            LLMError: LLM调用失败
        """
        if not self.llm:
            raise LLMError("LLM模块未初始化")

        # 获取或创建会话
        if session_id is None:
            session_id = self.create_session()

        session = self.get_session(session_id)
        if session is None:
            session_id = self.create_session()
            session = self.get_session(session_id)

        # 同一会话按完整轮次更新历史；不同会话可以并行调用LLM。
        with session.lock:
            message_count = len(session.conversation.messages)
            try:
                session.add_user_message(text)
                messages = session.get_messages(self.max_history_length)
                response = self.llm.chat(messages, **kwargs)
                session.add_assistant_message(response)

                self.logger.info(f"文本对话完成 - 会话: {session_id}")
                return response, session_id

            except Exception as e:
                # 失败的轮次不应留下未配对的用户消息，影响后续上下文。
                del session.conversation.messages[message_count:]
                error_msg = safe_str(e)
                self.logger.error(f"文本对话失败: {error_msg}")
                raise LLMError(f"文本对话失败: {error_msg}") from e

    def chat_with_audio(
        self,
        audio_data: Union[str, bytes, Tuple[int, Any]],
        session_id: Optional[str] = None,
        return_audio: bool = True,
        max_retries: int = 3,
        **kwargs,
    ) -> Tuple[str, Optional[bytes], str]:
        """兼容原接口，返回(回复文本, 回复音频或None, 会话ID)。"""
        result = self.chat_with_audio_result(
            audio_data, session_id, return_audio, max_retries, **kwargs
        )
        return result.response_text, result.response_audio, result.session_id

    def chat_with_audio_result(
        self,
        audio_data: Union[str, bytes, Tuple[int, Any]],
        session_id: Optional[str] = None,
        return_audio: bool = True,
        max_retries: int = 3,
        **kwargs,
    ) -> AudioChatResult:
        """
        语音对话，复用同一次识别结果生成回复和界面显示文本。

        Args:
            audio_data: 音频数据（文件路径、字节数据或Gradio格式）
            session_id: 会话ID，如果为None则创建新会话
            return_audio: 是否返回语音回复
            max_retries: ASR最大重试次数
            **kwargs: 其他参数

        Returns:
            包含识别文本、回复文本、回复音频和会话ID的结果

        Raises:
            AudioInputError: 音频输入错误
            SpeechRecognitionError: 语音识别错误
            LLMError: LLM调用失败
            SpeechSynthesisError: 语音合成错误
        """
        if not self.asr:
            raise error_handler.create_error("ASR_SERVICE_ERROR", message="ASR模块未初始化")

        try:
            # 使用增强的语音识别（带错误处理和重试）
            asr_result = self.asr.recognize_with_error_handling(
                audio_data, max_retries=max_retries, **kwargs
            )
            
            recognized_text = asr_result.text
            if not recognized_text:
                raise error_handler.create_error("ASR_NO_SPEECH")

            self.logger.info(f"语音识别结果: {recognized_text} (置信度: {asr_result.confidence:.2f})")

            # 文本对话
            response_text, session_id = self.chat_with_text(
                recognized_text, session_id, **kwargs
            )

            # 语音合成（如果需要）
            response_audio = None
            if return_audio and self.tts:
                response_audio = self._synthesize_with_error_handling(response_text)

            return AudioChatResult(
                recognized_text=recognized_text,
                response_text=response_text,
                response_audio=response_audio,
                session_id=session_id,
            )

        except (AudioInputError, SpeechRecognitionError, LLMError, SpeechSynthesisError):
            # 重新抛出已知错误
            raise
        except Exception as e:
            self.logger.error(f"语音对话失败: {e}")
            raise error_handler.create_error("ASR_SERVICE_ERROR", error_message=safe_str(e))

    def _synthesize_with_error_handling(self, text: str, max_retries: int = 2) -> Optional[bytes]:
        """
        带错误处理的语音合成
        
        Args:
            text: 要合成的文本
            max_retries: 最大重试次数
            
        Returns:
            合成的音频数据，失败时返回None
        """
        if not self.tts:
            self.logger.warning("TTS实例为None，无法进行语音合成")
            return None
        
        self.logger.info(f"开始语音合成 - TTS类型: {type(self.tts).__name__}")
        
        try:
            # 使用TTS基类的增强错误处理方法
            if hasattr(self.tts, 'synthesize_with_error_handling'):
                self.logger.info("使用TTS增强错误处理方法")
                result = self.tts.synthesize_with_error_handling(text, max_retries=max_retries)
                self.logger.info(f"语音合成完成 (耗时: {result.synthesis_time:.2f}s, 缓存: {result.cached})")
                return result.audio_data
            else:
                # 回退到基本方法
                self.logger.info("回退到基本语音合成方法")
                return self._basic_synthesize_with_retry(text, max_retries)
                
        except Exception as e:
            # 语音合成失败不应阻止对话，记录错误并返回None
            error = error_handler.create_error("TTS_SERVICE_ERROR", error_message=safe_str(e))
            error_handler.log_error(error, {"text_length": len(text)})
            self.logger.warning(f"语音合成失败，但对话将继续: {safe_str(e)}")
            return None

    def _basic_synthesize_with_retry(self, text: str, max_retries: int) -> Optional[bytes]:
        """
        基本的语音合成重试方法（当TTS不支持增强错误处理时使用）
        
        Args:
            text: 要合成的文本
            max_retries: 最大重试次数
            
        Returns:
            合成的音频数据，失败时返回None
        """
        last_error = None
        for attempt in range(max_retries):
            try:
                start_time = time.time()
                response_audio = self.tts.synthesize(text)
                processing_time = time.time() - start_time
                
                # 检查处理时间
                if processing_time > 30:
                    raise error_handler.create_error("TTS_TIMEOUT", processing_time=processing_time)
                
                self.logger.info(f"语音合成完成 (耗时: {processing_time:.2f}s)")
                return response_audio
                
            except Exception as e:
                last_error = error_handler.create_error("TTS_SERVICE_ERROR", 
                                                      attempt=attempt + 1, 
                                                      error_message=safe_str(e))
                
                if attempt < max_retries - 1:
                    self.logger.warning(f"语音合成失败，重试 {attempt + 1}/{max_retries}: {e}")
                    time.sleep(0.5 * (attempt + 1))
                    continue
                else:
                    # 最后一次尝试失败，记录错误
                    error_handler.log_error(last_error, {"text_length": len(text)})
                    self.logger.warning(f"语音合成最终失败: {last_error.error_info.message}")
                    return None
        
        return None

    def get_conversation_history(
        self, session_id: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        获取对话历史

        Args:
            session_id: 会话ID
            limit: 限制消息数量

        Returns:
            消息列表
        """
        session = self.get_session(session_id)
        if session is None:
            return []

        return session.get_messages(limit)

    def clear_conversation_history(
        self, session_id: str, keep_system: bool = True
    ) -> bool:
        """
        清空对话历史

        Args:
            session_id: 会话ID
            keep_system: 是否保留系统消息

        Returns:
            是否成功清空
        """
        session = self.get_session(session_id)
        if session is None:
            return False

        session.clear_history(keep_system)
        self.logger.info(f"清空对话历史 - 会话: {session_id}")
        return True

    def set_system_prompt(self, session_id: str, system_prompt: str) -> bool:
        """
        设置系统提示词

        Args:
            session_id: 会话ID
            system_prompt: 系统提示词

        Returns:
            是否成功设置
        """
        session = self.get_session(session_id)
        if session is None:
            return False

        # 清空现有对话并设置新的系统提示词
        with session.lock:
            session.conversation = Conversation(system_prompt)
            session.last_activity = datetime.now()

        self.logger.info(f"设置系统提示词 - 会话: {session_id}")
        return True

    def generate_conversation_summary(self, session_id: str) -> Optional[str]:
        """
        生成对话摘要

        Args:
            session_id: 会话ID

        Returns:
            对话摘要，如果失败则返回None
        """
        if not self.llm:
            return None

        session = self.get_session(session_id)
        if session is None:
            return None

        messages = session.get_messages()
        if len(messages) < 2:  # 至少需要一轮对话
            return None

        try:
            # 构建摘要请求
            conversation_text = "\n".join(
                [
                    f"{msg['role']}: {msg['content']}"
                    for msg in messages
                    if msg["role"] in ["user", "assistant"]
                ]
            )

            summary_prompt = f"""
Please provide a brief summary of the following conversation:

{conversation_text}

Summary:"""

            summary = self.llm.chat(summary_prompt)
            self.logger.info(f"生成对话摘要 - 会话: {session_id}")
            return summary

        except Exception as e:
            self.logger.error(f"生成对话摘要失败: {e}")
            return None

    def get_conversation_topics(self, session_id: str) -> List[str]:
        """
        提取对话主题

        Args:
            session_id: 会话ID

        Returns:
            主题列表
        """
        if not self.llm:
            return []

        session = self.get_session(session_id)
        if session is None:
            return []

        messages = session.get_messages()
        if len(messages) < 2:
            return []

        try:
            # 构建主题提取请求
            conversation_text = "\n".join(
                [
                    f"{msg['role']}: {msg['content']}"
                    for msg in messages
                    if msg["role"] in ["user", "assistant"]
                ]
            )

            topics_prompt = f"""
Please extract the main topics discussed in the following conversation. 
Return only a comma-separated list of topics (maximum 5 topics):

{conversation_text}

Topics:"""

            topics_response = self.llm.chat(topics_prompt)

            # 解析主题列表
            topics = [
                topic.strip() for topic in topics_response.split(",") if topic.strip()
            ]

            self.logger.info(f"提取对话主题 - 会话: {session_id}, 主题: {topics}")
            return topics[:5]  # 最多返回5个主题

        except Exception as e:
            self.logger.error(f"提取对话主题失败: {e}")
            return []

    def get_service_status(self) -> Dict[str, Any]:
        """
        获取服务状态

        Returns:
            服务状态信息
        """
        status = {
            "asr_available": self.asr is not None,
            "tts_available": self.tts is not None,
            "llm_available": self.llm is not None,
            "topic_generator_available": self.topic_generator is not None,
            "active_sessions": len(self.sessions),
            "service_config": {
                "max_history_length": self.max_history_length,
                "session_timeout": self.session_timeout,
            },
        }

        # 测试各模块连接状态
        if self.asr:
            try:
                status["asr_status"] = self.asr.test_connection()
            except:
                status["asr_status"] = False

        if self.tts:
            try:
                status["tts_status"] = self.tts.test_connection()
            except:
                status["tts_status"] = False

        if self.llm:
            try:
                status["llm_status"] = self.llm.test_connection()
            except:
                status["llm_status"] = False

        # 添加主题生成器统计信息
        if self.topic_generator:
            try:
                status["topic_statistics"] = self.topic_generator.get_statistics()
            except:
                status["topic_statistics"] = {}

        return status

    def process_chat(
        self, 
        audio: Optional[Any] = None,
        text_input: str = "",
        chat_history: List = None,
        use_text_input: bool = True,
        session_id: Optional[str] = None,
        topic_context: Optional[str] = None
    ) -> Tuple[Tuple[int, List], List]:
        """
        统一的聊天处理接口（增强错误处理版本）
        
        Args:
            audio: 音频输入数据
            text_input: 文本输入
            chat_history: 对话历史
            use_text_input: 是否使用文本输入模式
            session_id: 会话ID
            topic_context: 主题上下文信息
            
        Returns:
            (音频输出, 更新的对话历史)
        """
        user_input = ""
        error_context = {
            "use_text_input": use_text_input,
            "has_audio": audio is not None,
            "text_input_length": len(text_input) if text_input else 0
        }
        
        try:
            # 获取或创建会话
            if session_id is None:
                session_id = self.create_session()
            
            session = self.get_session(session_id)
            if session is None:
                session_id = self.create_session()
                session = self.get_session(session_id)
            
            # 处理主题上下文
            if topic_context:
                current_topic = session.get_metadata("current_topic")
                if current_topic != topic_context:
                    # 设置新主题
                    self.set_topic_for_session(session_id, topic_context)
            
            # 处理输入
            if use_text_input:
                # 文本输入模式
                if not text_input.strip():
                    raise error_handler.create_error("AUDIO_FORMAT_ERROR", message="文本输入不能为空")
                
                user_input = text_input
                response_text, _ = self.chat_with_text(text_input, session_id)
                audio_output = None
                
                # 生成语音输出（如果TTS可用）
                if self.tts:
                    self.logger.info(f"TTS实例可用，开始语音合成 - 文本长度: {len(response_text)}")
                    self.logger.debug(f"待合成文本: {response_text[:100]}...")
                    audio_output = self._synthesize_with_error_handling(response_text)
                    if audio_output:
                        self.logger.info(f"语音合成成功 - 音频数据长度: {len(audio_output)} bytes")
                    else:
                        self.logger.warning("语音合成失败 - 返回None")
                else:
                    self.logger.warning("TTS实例不可用，跳过语音合成")
            else:
                # 语音输入模式
                if audio is None:
                    raise error_handler.create_error("AUDIO_FORMAT_ERROR", message="语音输入模式下音频数据不能为空")
                
                # 识别结果同时用于对话上下文和界面显示，避免重复识别。
                result = self.chat_with_audio_result(
                    audio, session_id, return_audio=True, max_retries=3
                )
                user_input = result.recognized_text
                response_text = result.response_text
                audio_output = result.response_audio
            
            # 检查是否需要主题相关的上下文增强
            current_topic = session.get_metadata("current_topic")
            if current_topic and len(session.get_messages()) <= 4:  # 对话开始阶段
                # 在回复中自然地引用主题
                topic_enhanced_response = self._enhance_response_with_topic_context(
                    response_text, current_topic, user_input
                )
                if topic_enhanced_response != response_text:
                    response_text = topic_enhanced_response
                    # 更新会话中的助手消息
                    messages = session.get_messages()
                    if messages and messages[-1]["role"] == "assistant":
                        session.conversation.messages[-1].content = response_text
            
            # 获取更新后的对话历史
            updated_history = self.get_conversation_history(session_id)
            
            # 格式化返回值以匹配Gradio接口
            # 音频输出格式：(sample_rate, audio_data)
            if audio_output:
                try:
                    import numpy as np
                    import io
                    import wave
                    
                    # 将bytes转换为numpy数组
                    if isinstance(audio_output, bytes):
                        # 阿里云TTS返回MP3格式，需要特殊处理
                        try:
                            # 尝试检测音频格式
                            if audio_output.startswith(b'RIFF'):
                                # WAV格式
                                with io.BytesIO(audio_output) as audio_io:
                                    with wave.open(audio_io, 'rb') as wav_file:
                                        frames = wav_file.readframes(-1)
                                        sample_rate = wav_file.getframerate()
                                        audio_array = np.frombuffer(frames, dtype=np.int16)
                                        audio_result = (sample_rate, audio_array)
                            elif audio_output.startswith(b'ID3') or audio_output.startswith(b'\xff\xfb'):
                                 # MP3格式 - 阿里云TTS默认返回格式
                                 self.logger.info("检测到MP3格式音频，直接返回bytes数据")
                                 # 对于MP3格式，Gradio可以直接处理bytes数据
                                 # 直接返回bytes，让Gradio自动处理
                                 audio_result = audio_output
                            else:
                                # 未知格式，尝试作为原始PCM处理
                                self.logger.warning("未知音频格式，尝试作为PCM处理")
                                audio_array = np.frombuffer(audio_output, dtype=np.int16)
                                audio_result = (22050, audio_array)  # 使用阿里云默认采样率
                        except Exception as e:
                            self.logger.warning(f"音频格式处理失败: {e}，返回原始数据")
                            # 最后的回退方案：直接返回bytes
                            audio_result = audio_output
                    else:
                        audio_result = audio_output
                except ImportError:
                    self.logger.warning("numpy未安装，返回原始音频数据")
                    audio_result = audio_output
                except Exception as e:
                    self.logger.error(f"音频格式转换失败: {e}")
                    audio_result = None
            else:
                audio_result = None
            
            # 格式化对话历史为Gradio聊天格式
            formatted_history = []
            for msg in updated_history:
                if msg["role"] == "user":
                    formatted_history.append([msg["content"], None])
                elif msg["role"] == "assistant":
                    if formatted_history and formatted_history[-1][1] is None:
                        formatted_history[-1][1] = msg["content"]
                    else:
                        formatted_history.append([None, msg["content"]])
            
            self.logger.info(f"统一聊天处理完成 - 会话: {session_id}")
            return (audio_result, formatted_history)
            
        except ChatModuleError as e:
            # 处理已知的聊天模块错误
            error_handler.log_error(e, error_context)
            error_info = error_handler.format_user_error_message(e)
            
            # 返回用户友好的错误信息
            error_msg = error_info["message"]
            if error_info["suggestions"]:
                error_msg += f"\n\n建议：\n" + "\n".join(f"• {s}" for s in error_info["suggestions"][:3])
            
            input_display = user_input if user_input else (text_input or "语音输入")
            return (None, [[input_display, error_msg]])
            
        except Exception as e:
            # 处理未预期的错误
            self.logger.error(f"统一聊天处理发生未预期错误: {safe_str(e)}")
            error_context["unexpected_error"] = safe_str(e)
            
            # 创建通用错误
            generic_error = error_handler.create_error("NETWORK_ERROR", **error_context)
            error_handler.log_error(generic_error, error_context)
            
            # 返回通用错误信息
            error_msg = "抱歉，处理您的请求时出现了问题。请稍后重试。"
            input_display = user_input if user_input else (text_input or "语音输入")
            return (None, [[input_display, error_msg]])

    def _enhance_response_with_topic_context(self, response: str, topic: str, user_input: str) -> str:
        """
        使用主题上下文增强回复
        
        Args:
            response: 原始回复
            topic: 当前主题
            user_input: 用户输入
            
        Returns:
            增强后的回复
        """
        try:
            # 检查回复是否已经与主题相关
            topic_keywords = topic.lower().split()
            response_lower = response.lower()
            
            # 如果回复已经包含主题关键词，不需要增强
            if any(keyword in response_lower for keyword in topic_keywords if len(keyword) > 3):
                return response
            
            # 如果用户输入与主题相关，也不需要强制增强
            user_input_lower = user_input.lower()
            if any(keyword in user_input_lower for keyword in topic_keywords if len(keyword) > 3):
                return response
            
            # 简单的主题引导（不使用LLM，避免额外开销）
            topic_starters = [
                f"That's interesting! Speaking of {topic.lower()}, {response}",
                f"Great point! This reminds me of our topic about {topic.lower()}. {response}",
                f"I see! Regarding {topic.lower()}, {response}",
            ]
            
            # 随机选择一个主题引导方式
            import random
            enhanced_response = random.choice(topic_starters)
            
            # 确保增强后的回复不会太长
            if len(enhanced_response) > 500:
                return response
            
            self.logger.info(f"使用主题上下文增强回复: {topic}")
            return enhanced_response
            
        except Exception as e:
            self.logger.warning(f"主题上下文增强失败: {e}")
            return response

    def generate_topic(self, session_id: Optional[str] = None, difficulty: str = "intermediate", category: Optional[str] = None) -> str:
        """
        生成对话主题（增强错误处理版本）
        
        Args:
            session_id: 会话ID，如果提供则基于对话历史生成相关主题
            difficulty: 难度级别 ("beginner", "intermediate", "advanced")
            category: 主题分类，如果为None则随机选择
            
        Returns:
            对话主题字符串（保证不为空）
        """
        try:
            # 如果提供了会话ID，尝试生成上下文相关的主题
            if session_id:
                session = self.get_session(session_id)
                if session:
                    chat_history = session.get_messages()
                    if len(chat_history) >= 2:  # 至少有一轮对话
                        try:
                            contextual_topic = self.topic_generator.generate_contextual_topic(
                                chat_history, difficulty
                            )
                            # 将生成的主题保存到会话元数据中
                            session.set_metadata("current_topic", contextual_topic)
                            session.set_metadata("topic_generated_at", datetime.now().isoformat())
                            
                            self.logger.info(f"为会话 {session_id} 生成上下文相关主题: {contextual_topic}")
                            return contextual_topic
                        except Exception as e:
                            self.logger.warning(f"上下文主题生成失败，使用随机主题: {e}")
            
            # 生成随机主题（带备用方案）
            random_topic = self.topic_generator.generate_random_topic_with_fallback(difficulty, category)
            
            # 如果有会话，保存主题到元数据
            if session_id:
                session = self.get_session(session_id)
                if session:
                    session.set_metadata("current_topic", random_topic)
                    session.set_metadata("topic_generated_at", datetime.now().isoformat())
            
            self.logger.info(f"生成随机主题: {random_topic}")
            return random_topic
            
        except Exception as e:
            # 最终备用方案
            self.logger.error(f"主题生成完全失败，使用硬编码备用主题: {e}")
            fallback_topic = "What's something interesting that happened to you recently?"
            
            # 记录错误
            error = error_handler.create_error("TOPIC_GENERATION_FAILED", 
                                             error_message=safe_str(e),
                                             difficulty=difficulty,
                                             category=category)
            error_handler.log_error(error, {"session_id": session_id})
            
            if session_id:
                session = self.get_session(session_id)
                if session:
                    session.set_metadata("current_topic", fallback_topic)
                    session.set_metadata("topic_generated_at", datetime.now().isoformat())
            
            return fallback_topic



    def clear_context(self, session_id: Optional[str] = None) -> bool:
        """
        清除对话上下文
        
        Args:
            session_id: 会话ID，如果为None则清除所有会话
            
        Returns:
            是否成功清除
        """
        try:
            if session_id is None:
                # 清除所有会话
                cleared_count = len(self.sessions)
                self.sessions.clear()
                self.logger.info(f"清除所有对话上下文，共 {cleared_count} 个会话")
                return True
            else:
                # 清除指定会话的上下文
                session = self.get_session(session_id)
                if session is None:
                    self.logger.warning(f"会话不存在: {session_id}")
                    return False
                
                # 清空对话历史但保留会话
                session.clear_history(keep_system=True)
                # 清除主题相关的元数据
                session.set_metadata("current_topic", None)
                session.set_metadata("topic_generated_at", None)
                self.logger.info(f"清除对话上下文 - 会话: {session_id}")
                return True
                
        except Exception as e:
            self.logger.error(f"清除对话上下文失败: {e}")
            return False

    def get_current_topic(self, session_id: str) -> Optional[str]:
        """
        获取当前会话的主题
        
        Args:
            session_id: 会话ID
            
        Returns:
            当前主题，如果没有则返回None
        """
        session = self.get_session(session_id)
        if session is None:
            return None
        
        return session.get_metadata("current_topic")

    def set_topic_for_session(self, session_id: str, topic: str) -> bool:
        """
        为会话设置主题
        
        Args:
            session_id: 会话ID
            topic: 主题内容
            
        Returns:
            是否成功设置
        """
        session = self.get_session(session_id)
        if session is None:
            return False
        
        try:
            session.set_metadata("current_topic", topic)
            session.set_metadata("topic_generated_at", datetime.now().isoformat())
            
            # 可以选择性地添加一个系统消息来引导对话
            system_message = f"Let's talk about: {topic}"
            session.add_assistant_message(system_message)
            
            self.logger.info(f"为会话 {session_id} 设置主题: {topic}")
            return True
            
        except Exception as e:
            self.logger.error(f"设置会话主题失败: {e}")
            return False

    def get_topic_suggestions(self, difficulty: str = "intermediate", category: Optional[str] = None, count: int = 5) -> List[str]:
        """
        获取主题建议列表
        
        Args:
            difficulty: 难度级别
            category: 主题分类
            count: 返回的主题数量
            
        Returns:
            主题建议列表
        """
        try:
            suggestions = []
            
            if category:
                # 获取指定分类的主题
                topics = self.topic_generator.get_topics_by_category(category, difficulty)
                if topics:
                    import random
                    suggestions = random.sample(topics, min(count, len(topics)))
            else:
                # 从所有分类中随机选择主题
                categories = self.topic_generator.get_topic_categories(difficulty)
                for _ in range(count):
                    try:
                        topic = self.topic_generator.generate_random_topic(difficulty)
                        if topic not in suggestions:
                            suggestions.append(topic)
                    except Exception:
                        continue
            
            self.logger.info(f"生成 {len(suggestions)} 个主题建议")
            return suggestions
            
        except Exception as e:
            self.logger.error(f"获取主题建议失败: {e}")
            return []

    def add_custom_topic(self, topic: str, category: str = "custom", difficulty: str = "intermediate") -> bool:
        """
        添加自定义主题
        
        Args:
            topic: 主题内容
            category: 主题分类
            difficulty: 难度级别
            
        Returns:
            是否成功添加
        """
        try:
            success = self.topic_generator.add_custom_topic(topic, category, difficulty)
            if success:
                self.logger.info(f"成功添加自定义主题: {topic}")
            return success
            
        except Exception as e:
            self.logger.error(f"添加自定义主题失败: {e}")
            return False

    def get_topic_statistics(self) -> Dict[str, Any]:
        """
        获取主题统计信息
        
        Returns:
            主题统计信息
        """
        try:
            return self.topic_generator.get_statistics()
        except Exception as e:
            self.logger.error(f"获取主题统计信息失败: {e}")
            return {}

    def __del__(self):
        """清理资源"""
        try:
            self.sessions.clear()
        except:
            pass
