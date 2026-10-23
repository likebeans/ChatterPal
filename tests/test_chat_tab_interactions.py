"""
ChatTab 界面交互功能的测试
测试输入模式切换、状态指示器和偏好设置持久化
"""

import pytest
import tempfile
import gradio as gr
from unittest.mock import Mock, MagicMock, patch
from pathlib import Path

from chatterpal.web.components.chat_tab import ChatTab
from chatterpal.services.chat import ChatService
from chatterpal.utils.preferences import UserPreferences


class TestChatTabInputModeInteractions:
    """ChatTab 输入模式交互测试类"""
    
    def setup_method(self):
        """测试前设置"""
        # 创建临时目录用于偏好设置
        self.temp_dir = tempfile.mkdtemp()
        
        # 创建模拟ChatService
        self.mock_chat_service = Mock(spec=ChatService)
        self.mock_chat_service.create_session.return_value = "test_session_id"
        self.mock_chat_service.generate_topic.return_value = "测试对话主题"
        self.mock_chat_service.process_chat.return_value = ((16000, []), [])
        self.mock_chat_service.clear_context.return_value = None
        
        # 使用临时目录创建偏好管理
        with patch('chatterpal.web.components.chat_tab.get_preferences_manager') as mock_get_prefs:
            self.preferences = UserPreferences(config_dir=self.temp_dir)
            mock_get_prefs.return_value = self.preferences
            
            # 创建 ChatTab 实例
            self.chat_tab = ChatTab(self.mock_chat_service)
    
    def teardown_method(self):
        """测试后清理"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_initial_input_mode_from_preferences(self):
        """测试从偏好设置加载初始输入模式"""
        # 设置偏好为语音输入
        self.preferences.set_input_mode("voice")
        
        # 重新创建 ChatTab 以测试初始化
        with patch('chatterpal.web.components.chat_tab.get_preferences_manager') as mock_get_prefs:
            mock_get_prefs.return_value = self.preferences
            chat_tab = ChatTab(self.mock_chat_service)
            
            # Verify the initial widgets and hidden state use the saved mode.
            assert chat_tab.preferences is self.preferences
            with gr.Blocks(analytics_enabled=False) as blocks:
                chat_tab.create_interface()
            audio_input = next(
                component for component in blocks.blocks.values()
                if isinstance(component, gr.Audio) and component.label == "🎤 语音输入"
            )
            text_input = next(
                component for component in blocks.blocks.values()
                if isinstance(component, gr.Textbox) and component.label == "💬 文本输入"
            )
            assert audio_input.visible is True
            assert text_input.visible is False
            assert any(
                isinstance(component, gr.State) and component.value is False
                for component in blocks.blocks.values()
            )
            assert self.preferences.get_input_mode() == "voice"
    
    def test_toggle_input_mode_text_to_voice(self):
        """测试从文本输入切换到语音输入"""
        # 初始状态:文本输入
        current_use_text = True
        
        # 执行切换
        result = self.chat_tab._toggle_input_mode(current_use_text)
        
        # 验证返回
        new_use_text, audio_update, text_update, status_update, button_update = result
        
        assert new_use_text is False  # 切换到语音输
        assert audio_update["visible"] is True  # 音频输入可见
        assert text_update["visible"] is False  # 文本输入隐藏
        assert "🎤 语音输入" in status_update["value"]  # 状态显示语音输
        assert "📝 切换到文本输" in button_update["value"]  # 按钮显示切换到文
        
        # 验证偏好设置已保
        assert self.chat_tab.preferences.get_input_mode() == "voice"
    
    def test_toggle_input_mode_voice_to_text(self):
        """测试从语音输入切换到文本输入"""
        # 设置初始状态:语音输入
        self.preferences.set_input_mode("voice")
        current_use_text = False
        
        # 执行切换
        result = self.chat_tab._toggle_input_mode(current_use_text)
        
        # 验证返回
        new_use_text, audio_update, text_update, status_update, button_update = result
        
        assert new_use_text is True  # 切换到文本输
        assert audio_update["visible"] is False  # 音频输入隐藏
        assert text_update["visible"] is True  # 文本输入可见
        assert "📝 文本输入" in status_update["value"]  # 状态显示文本输
        assert "🎤 切换到语音输" in button_update["value"]  # 按钮显示切换到语
        
        # 验证偏好设置已保
        assert self.chat_tab.preferences.get_input_mode() == "text"
    
    def test_input_mode_persistence(self):
        """测试输入模式持久化保存"""
        # 切换到语音模式
        self.chat_tab._toggle_input_mode(True)
        
        # 验证当前实例的设置已更新
        assert self.chat_tab.preferences.get_input_mode() == "voice"
        
        # 创建新的偏好管理器实例(模拟重启)
        new_preferences = UserPreferences(config_dir=self.temp_dir)
        
        # 验证设置被持久化保存
        assert new_preferences.get_input_mode() == "voice"
    
    def test_history_display_toggle(self):
        """测试历史记录显示切换"""
        # 测试显示历史记录
        chatbot_update, panel_update = self.chat_tab._toggle_history_display(True)
        assert chatbot_update["visible"] is True
        assert panel_update["visible"] is True
        assert self.chat_tab.preferences.get_show_history() is True
        
        # 测试隐藏历史记录
        chatbot_update, panel_update = self.chat_tab._toggle_history_display(False)
        assert chatbot_update["visible"] is False
        assert panel_update["visible"] is False
        assert self.chat_tab.preferences.get_show_history() is False
    
    def test_history_display_persistence(self):
        """测试历史记录显示设置持久化"""
        # 设置显示历史记录
        self.chat_tab._toggle_history_display(True)
        
        # 验证当前实例的设置已更新
        assert self.chat_tab.preferences.get_show_history() is True
        
        # 创建新的偏好管理器实例
        new_preferences = UserPreferences(config_dir=self.temp_dir)
        
        # 验证设置被持久化保存
        assert new_preferences.get_show_history() is True
    
    def test_input_mode_status_indicator(self):
        """测试输入模式状态指示器"""
        # 测试文本模式状
        result = self.chat_tab._toggle_input_mode(False)  # 切换到文
        _, _, _, status_update, button_update = result
        
        assert "📝 文本输入" in status_update["value"]
        assert "🎤 切换到语音输" in button_update["value"]
        
        # 测试语音模式状
        result = self.chat_tab._toggle_input_mode(True)  # 切换到语
        _, _, _, status_update, button_update = result
        
        assert "🎤 语音输入" in status_update["value"]
        assert "📝 切换到文本输" in button_update["value"]
    
    def test_session_management_with_input_modes(self):
        """测试会话管理与输入模式的集成"""
        # 模拟聊天交互
        audio_data = None
        text_input = "Hello, how are you"
        chat_history = []
        use_text = True
        
        # 执行聊天
        result = self.chat_tab._handle_chat(audio_data, text_input, chat_history, use_text)
        
        # 验证会话ID被创
        assert self.chat_tab.current_session_id is not None
        
        # 验证 ChatService 被正确调
        self.mock_chat_service.create_session.assert_called_once()
        self.mock_chat_service.process_chat.assert_called_once_with(
            audio=audio_data,
            text_input=text_input,
            chat_history=chat_history,
            use_text_input=use_text,
            session_id=self.chat_tab.current_session_id
        )
    
    def test_clear_context_with_session(self):
        """测试清除上下文时的会话管理"""
        # 设置会话ID
        self.chat_tab.current_session_id = "test_session"
        
        # 清除上下文
        result = self.chat_tab._clear_context()
        
        # 验证结果
        status_msg, history = result
        assert "对话上下文已清除" in status_msg
        assert history == []
        
        # 验证会话ID被重置
        assert self.chat_tab.current_session_id is None
        
        # 验证 ChatService 被正确调用
        self.mock_chat_service.clear_context.assert_called_once_with("test_session")
    
    def test_error_handling_in_chat(self):
        """测试聊天过程中的错误处理"""
        # 模拟 ChatService 抛出异常
        self.mock_chat_service.process_chat.side_effect = Exception("测试错误")
        
        # 执行聊天
        result = self.chat_tab._handle_chat(None, "test input", [], True)
        
        # 验证错误处理
        audio_output, chat_history = result
        assert audio_output == (22050, [])
        assert len(chat_history) == 2  # 用户消息 + 错误消息
        assert chat_history[0]["role"] == "user"
        assert chat_history[1]["role"] == "assistant"
        assert "对话处理失败" in chat_history[1]["content"]
    
    def test_empty_text_input_handling(self):
        """测试空文本输入的处理"""
        # 模拟空文本输入错误
        self.mock_chat_service.process_chat.side_effect = Exception("文本输入不能为空")
        
        # 执行聊天(空文本)
        result = self.chat_tab._handle_chat(None, "", [], True)
        
        # 验证错误处理
        audio_output, chat_history = result
        assert len(chat_history) == 2
        assert chat_history[0]["content"] == "空文本输入"


class TestChatTabPreferencesIntegration:
    """ChatTab 与偏好设置集成测试"""
    
    def setup_method(self):
        """测试前设置"""
        self.temp_dir = tempfile.mkdtemp()
        self.mock_chat_service = Mock(spec=ChatService)
        
    def teardown_method(self):
        """测试后清理"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_preferences_loaded_on_initialization(self):
        """测试初始化时加载偏好设置"""
        # 预设偏好设置
        preferences = UserPreferences(config_dir=self.temp_dir)
        preferences.set_input_mode("voice")
        preferences.set_show_history(True)
        preferences.set_auto_play_response(False)
        
        # 创建 ChatTab 并验证偏好设置被加载
        with patch('chatterpal.web.components.chat_tab.get_preferences_manager') as mock_get_prefs:
            mock_get_prefs.return_value = preferences
            chat_tab = ChatTab(self.mock_chat_service)
            
            # 验证偏好设置被正确加
            assert chat_tab.preferences.get_input_mode() == "voice"
            assert chat_tab.preferences.get_show_history() is True
            assert chat_tab.preferences.get_auto_play_response() is False
    
    def test_multiple_preference_changes(self):
        """测试多个偏好设置的连续更新"""
        preferences = UserPreferences(config_dir=self.temp_dir)
        
        with patch('chatterpal.web.components.chat_tab.get_preferences_manager') as mock_get_prefs:
            mock_get_prefs.return_value = preferences
            chat_tab = ChatTab(self.mock_chat_service)
            
            # 连续更改多个设置
            chat_tab._toggle_input_mode(True)  # 切换到语音
            chat_tab._toggle_history_display(True)  # 显示历史记录
            chat_tab._toggle_input_mode(False)  # 切换回文本
            
            # 验证最终状态
            assert preferences.get_input_mode() == "text"
            assert preferences.get_show_history() is True
    
    def test_preferences_isolation_between_instances(self):
        """测试不同实例间的偏好设置隔离"""
        # 创建两个不同的临时目录
        temp_dir1 = tempfile.mkdtemp()
        temp_dir2 = tempfile.mkdtemp()
        
        try:
            # 创建两个独立的偏好管理器
            prefs1 = UserPreferences(config_dir=temp_dir1)
            prefs2 = UserPreferences(config_dir=temp_dir2)
            
            # 设置不同的偏
            prefs1.set_input_mode("voice")
            prefs2.set_input_mode("text")
            
            # 验证设置是独立的
            assert prefs1.get_input_mode() == "voice"
            assert prefs2.get_input_mode() == "text"
            
        finally:
            import shutil
            shutil.rmtree(temp_dir1, ignore_errors=True)
            shutil.rmtree(temp_dir2, ignore_errors=True)


class TestChatTabUIComponents:
    """ChatTab UI 组件测试"""
    
    def setup_method(self):
        """测试前设置"""
        self.temp_dir = tempfile.mkdtemp()
        self.mock_chat_service = Mock(spec=ChatService)
        self.mock_chat_service.generate_topic.return_value = "测试主题"
        
    def teardown_method(self):
        """测试后清理"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_topic_generation(self):
        """测试主题生成功能"""
        preferences = UserPreferences(config_dir=self.temp_dir)
        
        with patch('chatterpal.web.components.chat_tab.get_preferences_manager') as mock_get_prefs:
            mock_get_prefs.return_value = preferences
            chat_tab = ChatTab(self.mock_chat_service)
            
            # 测试主题生成
            result = chat_tab._generate_topic()
            assert result == "测试主题"
            self.mock_chat_service.generate_topic.assert_called_once()
    
    def test_topic_generation_error_handling(self):
        """测试主题生成错误处理"""
        # 模拟主题生成失败
        self.mock_chat_service.generate_topic.side_effect = Exception("生成失败")
        
        preferences = UserPreferences(config_dir=self.temp_dir)
        
        with patch('chatterpal.web.components.chat_tab.get_preferences_manager') as mock_get_prefs:
            mock_get_prefs.return_value = preferences
            chat_tab = ChatTab(self.mock_chat_service)
            
            # 测试错误处理
            result = chat_tab._generate_topic()
            assert "话题生成失败" in result
            assert "生成失败" in result


class TestChatTabStateManagement:
    """ChatTab 状态管理测试"""
    
    def setup_method(self):
        """测试前设置"""
        self.temp_dir = tempfile.mkdtemp()
        self.mock_chat_service = Mock(spec=ChatService)
        
    def teardown_method(self):
        """测试后清理"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_input_mode_state_consistency(self):
        """测试输入模式状态一致性"""
        preferences = UserPreferences(config_dir=self.temp_dir)
        
        with patch('chatterpal.web.components.chat_tab.get_preferences_manager') as mock_get_prefs:
            mock_get_prefs.return_value = preferences
            chat_tab = ChatTab(self.mock_chat_service)
            
            # 初始状态应该与偏好设置一致
            initial_mode = preferences.get_input_mode()
            assert initial_mode == "text"  # 默认值
            
            # 切换模式后状态应该更新
            chat_tab._toggle_input_mode(True)
            assert preferences.get_input_mode() == "voice"
            
            # 再次切换应该回到原状态
            chat_tab._toggle_input_mode(False)
            assert preferences.get_input_mode() == "text"
    
    def test_session_state_management(self):
        """测试会话状态管理"""
        preferences = UserPreferences(config_dir=self.temp_dir)
        
        with patch('chatterpal.web.components.chat_tab.get_preferences_manager') as mock_get_prefs:
            mock_get_prefs.return_value = preferences
            chat_tab = ChatTab(self.mock_chat_service)
            
            # 初始状态
            assert chat_tab.current_session_id is None
            
            # 模拟聊天创建会话
            self.mock_chat_service.create_session.return_value = "new_session_id"
            self.mock_chat_service.process_chat.return_value = (
                (16000, []), [["test", "Test response"]]
            )
            audio, history = chat_tab._handle_chat(None, "test", [], True)
            assert audio == (16000, [])
            assert history == [["test", "Test response"]]
            
            # 验证会话ID被设置
            assert chat_tab.current_session_id == "new_session_id"
            
            # 清除上下文应该重置会话ID
            chat_tab._clear_context()
            assert chat_tab.current_session_id is None


if __name__ == "__main__":
    pytest.main([__file__])









