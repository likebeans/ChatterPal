# -*- coding: utf-8 -*-
"""Chat tab component for conversational practice."""

import gradio as gr
from typing import List, Tuple, Optional
from ...services.chat import ChatService
from ...utils.preferences import get_preferences_manager
from ...utils.encoding_fix import safe_str


class ChatTab:
    """Chat interface component for conversational practice."""

    def __init__(self, chat_service: ChatService):
        """Initialize chat tab with chat service."""
        self.chat_service = chat_service
        self.preferences = get_preferences_manager()
        
        # 当前会话状态
        self.current_session_id = None
        self.input_mode_state = {"current_mode": None}
        
        # 历史记录管理状态
        self.history_state = {
            "current_page": 1,
            "messages_per_page": 10,
            "total_messages": 0,
            "total_pages": 1
        }

    def create_interface(self) -> gr.Tab:
        """Create the chat tab interface."""
        with gr.Tab("Chat") as tab:
            gr.Markdown(
                """
                ## 💬 AI对话练习
                
                与AI进行自然对话，提升英语口语表达能力。支持文本和语音输入，获得实时反馈。
                
                **功能特色：**
                - 🎯 智能话题生成
                - 🔄 多轮对话上下文
                - 🎤 语音识别输入
                - 🔊 语音合成输出
                """
            )

            # Topic generation section
            with gr.Accordion("🎲 主题生成", open=False):
                with gr.Row():
                    with gr.Column(scale=3):
                        topic_textbox = gr.Textbox(
                            label="对话主题",
                            placeholder="点击生成按钮获取随机对话主题...",
                        )
                    with gr.Column(scale=1):
                        generate_topic_btn = gr.Button(
                            value="🎲 生成主题", variant="secondary"
                        )

            # Context management section
            with gr.Accordion("📝 对话上下文管理", open=False):
                with gr.Row():
                    context_status = gr.Textbox(
                        label="上下文状态", value="对话上下文为空", interactive=False
                    )
                    clear_context_btn = gr.Button(
                        value="🗑️ 清除上下文", variant="secondary"
                    )

            # Use the saved mode for both visible controls and the hidden state.
            initial_mode = self.preferences.get_input_mode()
            use_text_initial = initial_mode == "text"

            # Input mode selection section
            with gr.Row():
                with gr.Column(scale=2):
                    input_mode_status = gr.Markdown(
                        value=("**当前输入模式:** 📝 文本输入" if use_text_initial
                               else "**当前输入模式:** 🎤 语音输入"),
                        elem_id="input_mode_status"
                    )
                with gr.Column(scale=1):
                    input_mode_toggle = gr.Button(
                        value=("🎤 切换到语音输入" if use_text_initial
                               else "📝 切换到文本输入"),
                        variant="secondary",
                        size="sm"
                    )

            # Input section
            with gr.Row():
                with gr.Column(scale=4):
                    # Audio and text inputs (visibility controlled by mode)
                    audio_input = gr.Audio(
                        sources=["microphone"], type="numpy",
                        label="🎤 语音输入",
                        visible=not use_text_initial
                    )
                    text_input = gr.Textbox(
                        label="💬 文本输入",
                        placeholder="请在此输入您想要对话的内容...",
                        visible=use_text_initial,
                        lines=2,
                    )
                with gr.Column(scale=1):
                    # Control buttons
                    send_btn = gr.Button(value="📤 发送", variant="primary", size="lg")
                    
                    # 从用户偏好加载初始设置
                    initial_show_history = self.preferences.get_show_history()
                    show_history = gr.Checkbox(
                        label="📜 显示历史", 
                        value=initial_show_history
                    )
                    
                    # 隐藏的状态组件用于跟踪输入模式
                    use_text_input = gr.State(value=use_text_initial)

            # Output section
            with gr.Row():
                with gr.Column():
                    # 从用户偏好加载自动播放设置
                    initial_autoplay = self.preferences.get_auto_play_response()
                    audio_output = gr.Audio(
                        label="🔊 AI语音回复", 
                        autoplay=initial_autoplay,
                        show_download_button=True,
                        show_share_button=False
                    )
                    
                    # 音频播放控制面板
                    with gr.Row():
                        with gr.Column(scale=2):
                            audio_controls_status = gr.Markdown(
                                value="**播放状态:** 待播放",
                                elem_id="audio_controls_status"
                            )
                        with gr.Column(scale=1):
                            auto_play_toggle = gr.Checkbox(
                                label="🔄 自动播放",
                                value=initial_autoplay
                            )
                    
                    # 播放控制按钮
                    with gr.Row():
                        play_btn = gr.Button(
                            value="▶️ 播放", 
                            variant="secondary", 
                            size="sm",
                            scale=1
                        )
                        pause_btn = gr.Button(
                            value="⏸️ 暂停", 
                            variant="secondary", 
                            size="sm",
                            scale=1,
                            interactive=False
                        )
                        replay_btn = gr.Button(
                            value="🔄 重播", 
                            variant="secondary", 
                            size="sm",
                            scale=1
                        )
                        stop_btn = gr.Button(
                            value="⏹️ 停止", 
                            variant="secondary", 
                            size="sm",
                            scale=1,
                            interactive=False
                        )
                    
                    # 对话历史管理面板
                    with gr.Row(visible=initial_show_history) as history_panel:
                        with gr.Column():
                            # 历史记录控制
                            with gr.Row():
                                with gr.Column(scale=2):
                                    history_status = gr.Markdown(
                                        value="**历史记录:** 0 条对话",
                                        elem_id="history_status"
                                    )
                                with gr.Column(scale=1):
                                    clear_history_btn = gr.Button(
                                        value="🗑️ 清除历史",
                                        variant="secondary",
                                        size="sm"
                                    )
                            
                            # 分页控制
                            with gr.Row():
                                prev_page_btn = gr.Button(
                                    value="⬅️ 上一页",
                                    variant="secondary",
                                    size="sm",
                                    scale=1,
                                    interactive=False
                                )
                                with gr.Column(scale=2):
                                    page_info = gr.Markdown(
                                        value="**第 1 页，共 1 页**",
                                        elem_id="page_info",
                                    )
                                next_page_btn = gr.Button(
                                    value="➡️ 下一页",
                                    variant="secondary",
                                    size="sm",
                                    scale=1,
                                    interactive=False
                                )
                    
                    chatbot = gr.Chatbot(
                        label="💬 对话记录", 
                        visible=initial_show_history, 
                        type="messages", 
                        height=400,
                        show_copy_button=True,
                        show_share_button=False,
                        avatar_images=None
                    )

            # Event handlers
            generate_topic_btn.click(
                fn=self._generate_topic, inputs=None, outputs=topic_textbox
            )

            send_btn.click(
                fn=self._handle_chat_messages,
                inputs=[audio_input, text_input, chatbot, use_text_input],
                outputs=[audio_output, chatbot],
                api_name="_handle_chat",
            )

            clear_context_btn.click(
                fn=self._clear_context, inputs=None, outputs=[context_status, chatbot]
            )

            # 输入模式切换
            input_mode_toggle.click(
                fn=self._toggle_input_mode,
                inputs=[use_text_input],
                outputs=[
                    use_text_input, 
                    audio_input, 
                    text_input, 
                    input_mode_status, 
                    input_mode_toggle
                ]
            )

            # 历史记录显示切换（保存偏好设置）
            show_history.change(
                fn=self._toggle_history_display,
                inputs=[show_history],
                outputs=[chatbot, history_panel]
            )

            # 音频播放控制事件
            auto_play_toggle.change(
                fn=self._toggle_auto_play,
                inputs=[auto_play_toggle],
                outputs=[audio_output]
            )

            play_btn.click(
                fn=self._handle_audio_play,
                inputs=[audio_output],
                outputs=[audio_controls_status, play_btn, pause_btn, stop_btn]
            )

            pause_btn.click(
                fn=self._handle_audio_pause,
                inputs=None,
                outputs=[audio_controls_status, play_btn, pause_btn, stop_btn]
            )

            replay_btn.click(
                fn=self._handle_audio_replay,
                inputs=[audio_output],
                outputs=[audio_controls_status, play_btn, pause_btn, stop_btn]
            )

            stop_btn.click(
                fn=self._handle_audio_stop,
                inputs=None,
                outputs=[audio_controls_status, play_btn, pause_btn, stop_btn]
            )

            # 历史记录管理事件
            clear_history_btn.click(
                fn=self._clear_history_with_confirmation,
                inputs=[chatbot],
                outputs=[chatbot, history_status, page_info, prev_page_btn, next_page_btn]
            )

            prev_page_btn.click(
                fn=self._navigate_history_page,
                inputs=[gr.State(-1), chatbot],  # -1 表示上一页
                outputs=[chatbot, page_info, prev_page_btn, next_page_btn]
            )

            next_page_btn.click(
                fn=self._navigate_history_page,
                inputs=[gr.State(1), chatbot],   # 1 表示下一页
                outputs=[chatbot, page_info, prev_page_btn, next_page_btn]
            )

        return tab

    def _generate_topic(self) -> str:
        """Generate a random conversation topic."""
        try:
            return self.chat_service.generate_topic()
        except Exception as e:
            return f"话题生成失败: {safe_str(e)}"

    def _handle_chat(
        self, audio, text_input: str, chat_history: List, use_text: bool
    ) -> Tuple[Tuple[int, List], List]:
        """Handle chat interaction."""
        try:
            # 确保有会话ID
            if self.current_session_id is None:
                self.current_session_id = self.chat_service.create_session()
            
            audio_output, updated_history = self.chat_service.process_chat(
                audio=audio,
                text_input=text_input,
                chat_history=chat_history,
                use_text_input=use_text,
                session_id=self.current_session_id
            )
            
            # 如果有音频输出，更新播放状态
            if audio_output and isinstance(audio_output, tuple) and len(audio_output[1]) > 0:
                # 音频生成成功，可以播放
                pass
            
            return audio_output, updated_history
        except Exception as e:
            error_msg = f"对话处理失败: {safe_str(e)}"
            if chat_history is None:
                chat_history = []
            
            # 根据输入模式显示用户输入
            user_input_display = text_input if use_text else "语音输入"
            if use_text and not text_input.strip():
                user_input_display = "空文本输入"
            
            chat_history.append({"role": "user", "content": user_input_display})
            chat_history.append({"role": "assistant", "content": error_msg})
            return (22050, []), chat_history

    @staticmethod
    def _as_messages(history: List) -> List:
        """Convert legacy pairs or service messages to visible Gradio messages."""
        messages = []
        for item in history or []:
            if isinstance(item, dict):
                if item.get("role") in {"user", "assistant"}:
                    messages.append({"role": item["role"], "content": item["content"]})
            else:
                user, assistant = item
                if user is not None:
                    messages.append({"role": "user", "content": user})
                if assistant is not None:
                    messages.append({"role": "assistant", "content": assistant})
        return messages

    def _handle_chat_messages(self, audio, text_input: str, chat_history: List, use_text: bool):
        """Adapt the legacy callback to the messages-mode widget boundary."""
        previous = self._as_messages(chat_history)
        audio_output, history = self._handle_chat(audio, text_input, list(previous), use_text)
        messages = self._as_messages(history)
        # The service returns the complete session on success and only an error
        # row on failure. Keep earlier visible turns when displaying that row.
        session_messages = self._as_messages(
            self.chat_service.get_conversation_history(self.current_session_id)
        )
        if messages != session_messages and messages[:len(previous)] != previous:
            messages = previous + messages
        if (
            isinstance(audio_output, tuple)
            and len(audio_output) == 2
            and len(audio_output[1]) == 0
        ):
            audio_output = None
        return audio_output, messages

    def _clear_context(self) -> Tuple[str, List]:
        """Clear conversation context."""
        try:
            if self.current_session_id:
                self.chat_service.clear_context(self.current_session_id)
            else:
                self.chat_service.clear_context()
            
            # 重置会话ID以创建新会话
            self.current_session_id = None
            
            return "对话上下文已清除", []
        except Exception as e:
            return f"清除上下文失败: {safe_str(e)}", []

    def _toggle_input_mode(self, current_use_text: bool) -> Tuple[bool, gr.update, gr.update, gr.update, gr.update]:
        """
        切换输入模式并保存偏好设置
        
        Args:
            current_use_text: 当前是否使用文本输入
            
        Returns:
            (新的输入模式状态, 音频输入更新, 文本输入更新, 状态显示更新, 按钮更新)
        """
        # 切换模式
        new_use_text = not current_use_text
        
        # 保存到用户偏好
        mode = "text" if new_use_text else "voice"
        self.preferences.set_input_mode(mode)
        
        # 更新界面
        audio_visible = not new_use_text
        text_visible = new_use_text
        
        # 更新状态显示
        if new_use_text:
            status_text = "**当前输入模式:** 📝 文本输入"
            button_text = "🎤 切换到语音输入"
        else:
            status_text = "**当前输入模式:** 🎤 语音输入"
            button_text = "📝 切换到文本输入"
        
        return (
            new_use_text,
            gr.update(visible=audio_visible),
            gr.update(visible=text_visible),
            gr.update(value=status_text),
            gr.update(value=button_text)
        )
    
    def _toggle_history_display(self, show: bool) -> Tuple[gr.update, gr.update]:
        """
        切换历史记录显示并保存偏好设置
        
        Args:
            show: 是否显示历史记录
            
        Returns:
            (聊天机器人组件更新, 历史面板更新)
        """
        # 保存到用户偏好
        self.preferences.set_show_history(show)
        
        return gr.update(visible=show), gr.update(visible=show)
    
    def _toggle_auto_play(self, auto_play: bool) -> gr.update:
        """
        切换自动播放设置并保存偏好设置
        
        Args:
            auto_play: 是否自动播放
            
        Returns:
            音频组件更新
        """
        # 保存到用户偏好
        self.preferences.set_auto_play_response(auto_play)
        
        return gr.update(autoplay=auto_play)
    
    def _handle_audio_play(self, audio_data) -> Tuple[gr.update, gr.update, gr.update, gr.update]:
        """
        处理音频播放
        
        Args:
            audio_data: 音频数据
            
        Returns:
            (状态更新, 播放按钮更新, 暂停按钮更新, 停止按钮更新)
        """
        try:
            if audio_data is None or (isinstance(audio_data, tuple) and len(audio_data[1]) == 0):
                status_text = "**播放状态:** 无音频内容"
                return (
                    gr.update(value=status_text),
                    gr.update(interactive=True),  # 播放按钮保持可用
                    gr.update(interactive=False),  # 暂停按钮不可用
                    gr.update(interactive=False)   # 停止按钮不可用
                )
            
            # 更新播放状态
            status_text = "**播放状态:** 正在播放"
            
            return (
                gr.update(value=status_text),
                gr.update(interactive=False),  # 播放按钮不可用
                gr.update(interactive=True),   # 暂停按钮可用
                gr.update(interactive=True)    # 停止按钮可用
            )
            
        except Exception as e:
            status_text = f"**播放状态:** 播放失败 - {safe_str(e)}"
            return (
                gr.update(value=status_text),
                gr.update(interactive=True),
                gr.update(interactive=False),
                gr.update(interactive=False)
            )
    
    def _handle_audio_pause(self) -> Tuple[gr.update, gr.update, gr.update, gr.update]:
        """
        处理音频暂停
        
        Returns:
            (状态更新, 播放按钮更新, 暂停按钮更新, 停止按钮更新)
        """
        status_text = "**播放状态:** 已暂停"
        
        return (
            gr.update(value=status_text),
            gr.update(interactive=True),   # 播放按钮可用（继续播放）
            gr.update(interactive=False),  # 暂停按钮不可用
            gr.update(interactive=True)    # 停止按钮可用
        )
    
    def _handle_audio_replay(self, audio_data) -> Tuple[gr.update, gr.update, gr.update, gr.update]:
        """
        处理音频重播
        
        Args:
            audio_data: 音频数据
            
        Returns:
            (状态更新, 播放按钮更新, 暂停按钮更新, 停止按钮更新)
        """
        try:
            if audio_data is None or (isinstance(audio_data, tuple) and len(audio_data[1]) == 0):
                status_text = "**播放状态:** 无音频内容可重播"
                return (
                    gr.update(value=status_text),
                    gr.update(interactive=True),
                    gr.update(interactive=False),
                    gr.update(interactive=False)
                )
            
            # 重播音频
            status_text = "**播放状态:** 重新播放中"
            
            return (
                gr.update(value=status_text),
                gr.update(interactive=False),  # 播放按钮不可用
                gr.update(interactive=True),   # 暂停按钮可用
                gr.update(interactive=True)    # 停止按钮可用
            )
            
        except Exception as e:
            status_text = f"**播放状态:** 重播失败 - {safe_str(e)}"
            return (
                gr.update(value=status_text),
                gr.update(interactive=True),
                gr.update(interactive=False),
                gr.update(interactive=False)
            )
    
    def _handle_audio_stop(self) -> Tuple[gr.update, gr.update, gr.update, gr.update]:
        """
        处理音频停止
        
        Returns:
            (状态更新, 播放按钮更新, 暂停按钮更新, 停止按钮更新)
        """
        status_text = "**播放状态:** 已停止"
        
        return (
            gr.update(value=status_text),
            gr.update(interactive=True),   # 播放按钮可用
            gr.update(interactive=False),  # 暂停按钮不可用
            gr.update(interactive=False)   # 停止按钮不可用
        )
    
    def _clear_history_with_confirmation(self, chat_history: List) -> Tuple[List, gr.update, gr.update, gr.update, gr.update]:
        """
        清除历史记录（带确认）
        
        Args:
            chat_history: 当前对话历史
            
        Returns:
            (清空的历史记录, 状态更新, 页面信息更新, 上一页按钮更新, 下一页按钮更新)
        """
        try:
            # 清除会话历史
            if self.current_session_id:
                self.chat_service.clear_conversation_history(self.current_session_id)
            
            # 重置历史状态
            self.history_state.update({
                "current_page": 1,
                "total_messages": 0,
                "total_pages": 1
            })
            
            # 更新状态显示
            status_text = "**历史记录:** 已清除所有对话"
            page_text = "**第 1 页，共 1 页**"
            
            return (
                [],  # 清空的对话历史
                gr.update(value=status_text),
                gr.update(value=page_text),
                gr.update(interactive=False),  # 上一页按钮不可用
                gr.update(interactive=False)   # 下一页按钮不可用
            )
            
        except Exception as e:
            error_status = f"**历史记录:** 清除失败 - {safe_str(e)}"
            return (
                chat_history,  # 保持原有历史
                gr.update(value=error_status),
                gr.update(),  # 页面信息不变
                gr.update(),  # 按钮状态不变
                gr.update()
            )
    
    def _navigate_history_page(self, direction: int, chat_history: List) -> Tuple[List, gr.update, gr.update, gr.update]:
        """
        导航历史记录页面
        
        Args:
            direction: 导航方向 (-1: 上一页, 1: 下一页)
            chat_history: 当前对话历史
            
        Returns:
            (分页后的历史记录, 页面信息更新, 上一页按钮更新, 下一页按钮更新)
        """
        try:
            if not self.current_session_id:
                return chat_history, gr.update(), gr.update(), gr.update()
            
            # 获取完整的对话历史
            full_history = self._as_messages(
                self.chat_service.get_conversation_history(self.current_session_id)
            )

            if not full_history:
                return [], gr.update(), gr.update(), gr.update()
            
            # 计算分页信息
            total_messages = len(full_history)
            messages_per_page = self.history_state["messages_per_page"]
            total_pages = max(1, (total_messages + messages_per_page - 1) // messages_per_page)
            
            # 更新当前页
            current_page = self.history_state["current_page"] + direction
            current_page = max(1, min(current_page, total_pages))
            
            # 更新状态
            self.history_state.update({
                "current_page": current_page,
                "total_messages": total_messages,
                "total_pages": total_pages
            })
            
            # 计算当前页的消息范围
            start_idx = (current_page - 1) * messages_per_page
            end_idx = min(start_idx + messages_per_page, total_messages)
            
            # 获取当前页的消息
            page_messages = full_history[start_idx:end_idx]
            
            # The interface uses Gradio's messages format.
            formatted_history = page_messages
            
            # 更新页面信息
            page_text = f"**第 {current_page} 页，共 {total_pages} 页** (显示 {len(page_messages)} 条消息)"
            
            # 更新按钮状态
            prev_enabled = current_page > 1
            next_enabled = current_page < total_pages
            
            return (
                formatted_history,
                gr.update(value=page_text),
                gr.update(interactive=prev_enabled),
                gr.update(interactive=next_enabled)
            )
            
        except Exception as e:
            error_page_text = f"**页面导航失败:** {safe_str(e)}"
            return (
                chat_history,
                gr.update(value=error_page_text),
                gr.update(),
                gr.update()
            )
    
    def _update_history_status(self, chat_history: List) -> str:
        """
        更新历史记录状态显示
        
        Args:
            chat_history: 当前对话历史
            
        Returns:
            状态文本
        """
        try:
            if not self.current_session_id:
                return "**历史记录:** 无活动会话"
            
            # 获取完整历史记录数量
            full_history = self._as_messages(
                self.chat_service.get_conversation_history(self.current_session_id)
            )
            total_count = len(full_history)
            
            # 当前显示的消息数量
            current_count = len(chat_history) if chat_history else 0
            
            if total_count == 0:
                return "**历史记录:** 暂无对话记录"
            elif current_count == total_count:
                return f"**历史记录:** 共 {total_count} 条对话"
            else:
                return f"**历史记录:** 显示 {current_count}/{total_count} 条对话"
                
        except Exception as e:
            return f"**历史记录:** 状态获取失败 - {safe_str(e)}"