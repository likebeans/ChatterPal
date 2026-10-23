# -*- coding: utf-8 -*-
"""Main Gradio application for ChatterPal."""

import os
import sys

# 设置编码环境变量
os.environ['PYTHONIOENCODING'] = 'utf-8'
if os.name == 'nt':  # Windows
    os.environ['PYTHONLEGACYWINDOWSSTDIO'] = '1'

import gradio as gr
from typing import Optional
import logging

from ..config.settings import Settings, get_settings
from ..config.loader import create_tts, create_asr, create_llm
from ..core.assessment.corrector import PronunciationCorrector
from ..services.chat import ChatService
from ..services.evaluation import EvaluationService
from ..services.correction import CorrectionService
from ..utils.encoding_fix import safe_str
from .components import ChatTab, ScoreTab, CorrectTab


class ChatterPalApp:
    """Main application class for ChatterPal."""

    def __init__(self, settings: Optional[Settings] = None):
        """Initialize the application."""
        self.settings = settings or get_settings()
        self.logger = logging.getLogger(__name__)

        # Initialize components
        self._init_core_components()
        self._init_services()
        self._init_ui_components()

    def _init_core_components(self):
        """Initialize core ASR, TTS, LLM, and assessment components."""
        try:
            self.asr = create_asr(self.settings)

            # Initialize TTS using configuration loader
            self.tts = create_tts(self.settings)
            self.logger.info(f"TTS initialized: {type(self.tts).__name__ if self.tts else 'None'}")
            if self.tts:
                self.logger.info(f"TTS provider: {self.settings.tts_provider}")
                self.logger.info(f"TTS voice: {getattr(self.settings, 'alibaba_tts_voice', 'Not set')}")
            else:
                self.logger.warning("TTS instance is None - no voice synthesis will be available")

            self.llm = create_llm(self.settings)

            # Initialize assessment components
            self.pronunciation_corrector = PronunciationCorrector()

            self.logger.info("Core components initialized successfully")

        except Exception as e:
            self.logger.error(f"Failed to initialize core components: {e}")
            raise

    def _init_services(self):
        """Initialize business service layer."""
        try:
            # 准备ChatService配置
            chat_config = {
                "max_history_length": self.settings.max_history_length,
                "session_timeout": self.settings.session_timeout,
            }
            
            # 如果用户自定义了系统提示词，则使用自定义的
            if self.settings.system_prompt:
                chat_config["default_system_prompt"] = self.settings.system_prompt
            
            self.chat_service = ChatService(
                asr=self.asr, 
                tts=self.tts, 
                llm=self.llm,
                config=chat_config
            )

            self.evaluation_service = EvaluationService(asr=self.asr, llm=self.llm)

            self.correction_service = CorrectionService(asr=self.asr)

            self.logger.info("Services initialized successfully")

        except Exception as e:
            self.logger.error(f"Failed to initialize services: {e}")
            raise

    def _init_ui_components(self):
        """Initialize UI components."""
        try:
            self.chat_tab = ChatTab(self.chat_service)
            self.score_tab = ScoreTab(self.evaluation_service)
            self.correct_tab = CorrectTab(self.correction_service)

            self.logger.info("UI components initialized successfully")

        except Exception as e:
            self.logger.error(f"Failed to initialize UI components: {e}")
            raise

    def create_interface(self):
        """Create and return the Gradio interface."""
        try:
            # Create main interface with tabs
            with gr.Blocks(
                title="🎯 ChatterPal - AI英语口语练习系统",
                theme=gr.themes.Soft(),
            ) as app:
                gr.Markdown(
                    """
                    # 🎯 ChatterPal - AI英语口语练习系统
                    
                    专业的AI驱动英语口语练习平台，提供智能对话、发音评估和纠错指导。
                    """
                )
                
                # Create tabs
                with gr.Tabs():
                    # Chat tab
                    with gr.Tab("💬 对话练习"):
                        self._create_chat_interface()
                    
                    # Score tab  
                    with gr.Tab("📊 发音评分"):
                        self._create_score_interface()
                    
                    # Correct tab
                    with gr.Tab("🔧 发音纠错"):
                        self._create_correct_interface()

            return app

        except Exception as e:
            self.logger.error(f"Failed to create interface: {e}")
            raise

    def _create_chat_interface(self):
        """Create chat interface content."""
        gr.Markdown(
            """
            ## 💬 AI对话练习
            
            与AI进行自然对话，提升英语口语表达能力。支持文本和语音输入，获得实时反馈。
            """
        )
        
        # Voice selection section
        with gr.Row():
            with gr.Column(scale=2):
                # Get available voices from TTS service
                available_voices = []
                voice_display_map = {}
                try:
                    if hasattr(self.tts, 'get_supported_voices'):
                        available_voices = self.tts.get_supported_voices()
                        for voice in available_voices:
                            voice_info = self.tts.get_voice_info(voice)
                            display_name = voice_info.get('display_name', voice)
                            description = voice_info.get('description', '')
                            voice_display_map[voice] = f"{display_name} - {description}"
                except Exception as e:
                    self.logger.warning(f"Failed to get voice list: {e}")
                    available_voices = ["longxiaochun"]
                    voice_display_map = {"longxiaochun": "龙小春 - 温柔甜美的女声"}
                
                voice_selector = gr.Dropdown(
                    choices=[(voice_display_map.get(v, v), v) for v in available_voices],
                    value="longxiaochun",
                    label="🎵 选择音色",
                    info="选择AI回复的语音音色"
                )
            with gr.Column(scale=2):
                gr.Markdown("")
        
        with gr.Row():
            with gr.Column(scale=4):
                text_input = gr.Textbox(
                    label="💬 文本输入",
                    placeholder="请在此输入您想要对话的内容...",
                    lines=2,
                    info="Shift+Enter 发送，Enter 换行",
                )
            with gr.Column(scale=1):
                send_btn = gr.Button(value="📤 发送", variant="primary")
                clear_btn = gr.Button(value="清空对话", variant="secondary")

        session_state = gr.State(value=None)
        chatbot = gr.Chatbot(label="💬 对话记录", height=400, type="tuples")

        # Audio output component for TTS
        audio_output = gr.Audio(
            label="🔊 AI语音回复",
            autoplay=True,
            show_download_button=True,
            show_share_button=False
        )

        def turn_completed(session_id, previous_messages, message):
            messages = self.chat_service.get_conversation_history(session_id)
            return (
                len(messages) == len(previous_messages) + 2
                and messages[:-2] == previous_messages
                and messages[-2].get("role") == "user"
                and messages[-2].get("content") == message
                and messages[-1].get("role") == "assistant"
            )

        def restore_history(session_id, previous_messages):
            session = self.chat_service.get_session(session_id)
            if session is None or session.get_messages() == previous_messages:
                return
            self.chat_service.clear_conversation_history(session_id)
            for previous in previous_messages:
                metadata = {
                    key: value for key, value in previous.items()
                    if key not in {"role", "content"}
                }
                if previous["role"] == "user":
                    session.add_user_message(previous["content"], **metadata)
                elif previous["role"] == "assistant":
                    session.add_assistant_message(previous["content"], **metadata)

        def chat_response(message, history, selected_voice, session_id):
            history = list(history or [])
            if not message or not message.strip():
                return history, "", None, session_id

            previous_messages = None
            try:
                # Keep the service session ID in this browser's Gradio state.
                if not session_id or self.chat_service.get_session(session_id) is None:
                    session_id = self.chat_service.create_session()
                    history = []
                previous_messages = self.chat_service.get_conversation_history(session_id)

                # Update TTS voice if different from current
                if hasattr(self.tts, 'voice_name') and self.tts.voice_name != selected_voice:
                    self.logger.info(f"Switching TTS voice from {self.tts.voice_name} to {selected_voice}")
                    self.tts.voice_name = selected_voice

                audio_output_data, updated_history = self.chat_service.process_chat(
                    audio=None,
                    text_input=message,
                    chat_history=history,
                    use_text_input=True,
                    session_id=session_id
                )

                # process_chat reports provider failures as a single error row.
                if not turn_completed(session_id, previous_messages, message):
                    restore_history(session_id, previous_messages)
                    return history + updated_history, "", None, session_id

                # The service uses an empty tuple payload when no audio is available.
                if (
                    isinstance(audio_output_data, tuple)
                    and len(audio_output_data) == 2
                    and len(audio_output_data[1]) == 0
                ):
                    audio_output_data = None
                return updated_history, "", audio_output_data, session_id

            except Exception as e:
                self.logger.error(f"Chat error: {safe_str(e)}")
                if previous_messages is not None and not turn_completed(
                    session_id, previous_messages, message
                ):
                    restore_history(session_id, previous_messages)
                error_msg = f"抱歉，处理您的消息时出现错误：{safe_str(e)}\n\n请检查API密钥配置是否正确。"
                return history + [[message, error_msg]], "", None, session_id

        def clear_chat(session_id):
            if session_id:
                self.chat_service.delete_session(session_id)
            return [], "", None, None

        outputs = [chatbot, text_input, audio_output, session_state]
        gr.on(
            triggers=[send_btn.click, text_input.submit],
            fn=chat_response,
            inputs=[text_input, chatbot, voice_selector, session_state],
            outputs=outputs,
            api_name="chat_response",
            concurrency_id="gradio_chat",
            concurrency_limit=1,
        )
        # Clear after any pending send, so a late reply cannot restore the old chat.
        gr.on(
            triggers=[clear_btn.click, chatbot.clear],
            fn=clear_chat,
            inputs=[session_state],
            outputs=outputs,
            api_name="clear_chat",
            concurrency_id="gradio_chat",
            concurrency_limit=1,
        )

    def _create_score_interface(self):
        """Create score interface content."""
        gr.Markdown(
            """
            ## 📊 发音评分
            
            上传音频文件或录制语音，获得专业的发音质量评估和详细分析报告。
            """
        )
        
        with gr.Row():
            with gr.Column():
                audio_input = gr.Audio(label="🎤 音频输入", type="filepath")
                text_reference = gr.Textbox(
                    label="📝 参考文本",
                    placeholder="请输入要评估的参考文本...",
                    lines=3
                )
                evaluate_btn = gr.Button(value="📊 开始评估", variant="primary")
            
            with gr.Column():
                score_output = gr.Textbox(
                    label="📈 评分结果",
                    lines=10,
                    interactive=False
                )
        
        def evaluate_pronunciation(audio, text):
            if not audio or not text:
                return "请提供音频文件和参考文本"
            return f"演示评分结果：\n\n参考文本: {text}\n\n这是一个演示输出。请配置API密钥以启用完整功能。"
        
        evaluate_btn.click(
            evaluate_pronunciation,
            inputs=[audio_input, text_reference],
            outputs=[score_output]
        )

    def _create_correct_interface(self):
        """Create correction interface content."""
        gr.Markdown(
            """
            ## 🔧 发音纠错
            
            获得详细的发音纠错建议，包括音素级别的分析和改进指导。
            """
        )
        
        with gr.Row():
            with gr.Column():
                audio_input = gr.Audio(label="🎤 音频输入", type="filepath")
                text_reference = gr.Textbox(
                    label="📝 参考文本",
                    placeholder="请输入要纠错的参考文本...",
                    lines=3
                )
                correct_btn = gr.Button(value="🔧 开始纠错", variant="primary")
            
            with gr.Column():
                correction_output = gr.Textbox(
                    label="🔧 纠错建议",
                    lines=10,
                    interactive=False
                )
        
        def correct_pronunciation(audio, text):
            if not audio or not text:
                return "请提供音频文件和参考文本"
            return f"演示纠错结果：\n\n参考文本: {text}\n\n这是一个演示输出。请配置API密钥以启用完整功能。"
        
        correct_btn.click(
            correct_pronunciation,
            inputs=[audio_input, text_reference],
            outputs=[correction_output]
        )

    def launch(self, **kwargs):
        """Launch the application."""
        try:
            app = self.create_interface()

            # Default launch parameters
            launch_params = {
                "server_name": self.settings.gradio_server_name,
                "server_port": self.settings.gradio_port,
                "share": self.settings.gradio_share,
                "show_error": True,
                "quiet": False,
            }

            # Override with user parameters
            launch_params.update(kwargs)

            self.logger.info(
                f"Launching application on port {launch_params['server_port']}"
            )
            app.launch(**launch_params)

        except Exception as e:
            self.logger.error(f"Failed to launch application: {e}")
            raise


def create_app(settings: Optional[Settings] = None) -> ChatterPalApp:
    """Create and return a ChatterPal application instance."""
    return ChatterPalApp(settings)


def main():
    """Main entry point for the application."""
    import logging

    # Configure logging with UTF-8 support
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # 确保日志处理器支持UTF-8
    for handler in logging.root.handlers:
        if hasattr(handler, 'stream') and hasattr(handler.stream, 'reconfigure'):
            try:
                handler.stream.reconfigure(encoding='utf-8')
            except:
                pass

    try:
        # Create and launch app
        app = create_app()
        app.launch()

    except KeyboardInterrupt:
        print("\n应用已停止")
    except Exception as e:
        print(f"应用启动失败: {e}")
        raise


if __name__ == "__main__":
    main()
