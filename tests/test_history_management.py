"""对话历史管理功能的测试
测试历史记录显示、分页、清除等功能
"""

import pytest
import tempfile
import gradio as gr
from unittest.mock import Mock, MagicMock, patch

from chatterpal.web.components.chat_tab import ChatTab
from chatterpal.services.chat import ChatService
from chatterpal.utils.preferences import UserPreferences


class TestHistoryManagement:
    """历史记录管理测试"""
    
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
        self.mock_chat_service.clear_conversation_history.return_value = True
        
        # 模拟对话历史
        self.sample_history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you"},
            {"role": "assistant", "content": "I'm doing well, thank you!"},
            {"role": "user", "content": "What's the weather like"},
            {"role": "assistant", "content": "I don't have access to current weather data."},
        ]
        
        self.mock_chat_service.get_conversation_history.return_value = self.sample_history
        
        # 使用临时目录创建偏好管理
        with patch('chatterpal.web.components.chat_tab.get_preferences_manager') as mock_get_prefs:
            self.preferences = UserPreferences(config_dir=self.temp_dir)
            mock_get_prefs.return_value = self.preferences
            
            # 创建 ChatTab 实例
            self.chat_tab = ChatTab(self.mock_chat_service)
            self.chat_tab.current_session_id = "test_session_id"
    
    def teardown_method(self):
        """测试后清理"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_toggle_history_display(self):
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
    
    def test_clear_history_with_confirmation(self):
        """测试清除历史记录功能"""
        # 模拟有历史记录的情况
        current_history = [["Hello", "Hi there!"], ["How are you", "I'm doing well!"]]
        
        # 执行清除操作
        result = self.chat_tab._clear_history_with_confirmation(current_history)
        history, status_update, page_update, prev_btn_update, next_btn_update = result
        
        # 验证结果
        assert history == []  # 历史记录被清空
        assert "已清除所有对话" in status_update["value"]
        assert page_update["value"] == "**第 1 页，共 1 页**"
        assert prev_btn_update["interactive"] is False
        assert next_btn_update["interactive"] is False
        
        # 验证服务方法被调
        self.mock_chat_service.clear_conversation_history.assert_called_once_with("test_session_id")
        
        # 验证历史状态被重置
        assert self.chat_tab.history_state["current_page"] == 1
        assert self.chat_tab.history_state["total_messages"] == 0
        assert self.chat_tab.history_state["total_pages"] == 1
    
    def test_clear_history_error_handling(self):
        """测试清除历史记录时的错误处理"""
        # 模拟服务抛出异常
        self.mock_chat_service.clear_conversation_history.side_effect = Exception("清除失败")
        
        current_history = [["Hello", "Hi there!"]]
        
        # 执行清除操作
        result = self.chat_tab._clear_history_with_confirmation(current_history)
        history, status_update, _, _, _ = result
        
        # 验证错误处理
        assert history == current_history  # 历史记录保持不变
        assert "清除失败" in status_update["value"]
    
    def test_navigate_history_page_next(self):
        """测试导航到下一页"""
        # 设置每页显示2条消息
        self.chat_tab.history_state["messages_per_page"] = 2
        
        current_history = []
        
        # 导航到下一页
        result = self.chat_tab._navigate_history_page(1, current_history)
        history, page_update, prev_btn_update, next_btn_update = result
        
        # 验证分页结果
        assert len(history) <= 2  # 每页最多条消息
        assert "2 页" in page_update["value"]
        assert prev_btn_update["interactive"] is True  # 上一页按钮可用
        
        # 验证历史状态更新
        assert self.chat_tab.history_state["current_page"] == 2
    
    def test_navigate_history_page_previous(self):
        """测试导航到上一页"""
        # 设置当前在第2页
        self.chat_tab.history_state["current_page"] = 2
        self.chat_tab.history_state["messages_per_page"] = 2
        
        current_history = []
        
        # 导航到上一页
        result = self.chat_tab._navigate_history_page(-1, current_history)
        history, page_update, prev_btn_update, next_btn_update = result
        
        # 验证分页结果
        assert "1 " in page_update["value"]
        assert prev_btn_update["interactive"] is False  # 上一页按钮不可用(已在第一页)
        assert next_btn_update["interactive"] is True   # 下一页按钮可
        
        # 验证历史状态更
        assert self.chat_tab.history_state["current_page"] == 1
    
    def test_navigate_history_page_boundaries(self):
        """测试分页边界条件"""
        # 测试在第一页时向上导航
        self.chat_tab.history_state["current_page"] = 1
        
        result = self.chat_tab._navigate_history_page(-1, [])
        _, page_update, prev_btn_update, _ = result
        
        # 应该保持在第一
        assert self.chat_tab.history_state["current_page"] == 1
        assert prev_btn_update["interactive"] is False
        
        # 测试超出最大页数的导航
        self.chat_tab.history_state["messages_per_page"] = 10  # 设置大的页面大小
        
        result = self.chat_tab._navigate_history_page(10, [])  # 尝试跳转很多
        
        # 应该限制在最大页数内
        total_pages = self.chat_tab.history_state["total_pages"]
        assert self.chat_tab.history_state["current_page"] <= total_pages
    
    def test_navigate_history_no_session(self):
        """测试无会话时的历史导航"""
        # 清除会话ID
        self.chat_tab.current_session_id = None
        
        current_history = [["Hello", "Hi"]]
        
        # 尝试导航
        result = self.chat_tab._navigate_history_page(1, current_history)
        history, _, _, _ = result
        
        # 应该返回原始历史记录
        assert history == current_history
    
    def test_navigate_history_empty_history(self):
        """测试空历史记录的导航"""
        # 模拟空历史记录
        self.mock_chat_service.get_conversation_history.return_value = []
        
        result = self.chat_tab._navigate_history_page(1, [])
        history, _, _, _ = result
        
        # 应该返回空列表
        assert history == []
    
    def test_navigate_history_error_handling(self):
        """测试历史导航时的错误处理"""
        # 模拟服务抛出异常
        self.mock_chat_service.get_conversation_history.side_effect = Exception("获取历史失败")
        
        current_history = [["Hello", "Hi"]]
        
        # 尝试导航
        result = self.chat_tab._navigate_history_page(1, current_history)
        history, page_update, _, _ = result
        
        # 验证错误处理
        assert history == current_history  # 保持原有历史
        assert "页面导航失败" in page_update["value"]
    
    def test_update_history_status_no_session(self):
        """测试无会话时的状态更新"""
        self.chat_tab.current_session_id = None
        
        status = self.chat_tab._update_history_status([])
        assert "无活动会话" in status
    
    def test_update_history_status_empty_history(self):
        """测试空历史记录的状态更新"""
        self.mock_chat_service.get_conversation_history.return_value = []
        
        status = self.chat_tab._update_history_status([])
        assert "暂无对话记录" in status
    
    def test_update_history_status_with_messages(self):
        """测试有消息时的状态更新"""
        # 测试完整显示
        current_display = [["Hello", "Hi"], ["How are you", "Good"]]
        
        status = self.chat_tab._update_history_status(current_display)
        # 由于当前显示2条,总共6条,所以应该显示部分信息
        assert ("显示 2/6 条对话" in status) or ("6 条对话" in status)
        
        # 测试部分显示
        partial_display = [["Hello", "Hi"]]
        
        status = self.chat_tab._update_history_status(partial_display)
        assert "显示 1/6 条对话" in status
    
    def test_update_history_status_error_handling(self):
        """测试状态更新时的错误处理"""
        # 模拟服务抛出异常
        self.mock_chat_service.get_conversation_history.side_effect = Exception("获取状态失败")
        
        status = self.chat_tab._update_history_status([])
        assert "状态获取失败" in status
    
    def test_history_pagination_formatting(self):
        """测试历史记录分页格式"""
        # 设置小的页面大小以测试分页
        self.chat_tab.history_state["messages_per_page"] = 2
        
        # 导航到第一页
        result = self.chat_tab._navigate_history_page(0, [])  # 0表示不改变页面,只格式化当前页
        history, page_update, _, _ = result
        
        # 验证格式化结果
        assert isinstance(history, list)
        
        # ChatTab declares a messages-mode Chatbot; pagination must be
        # accepted by that real component, including both message contents.
        chatbot = gr.Chatbot(type="messages")
        displayed = chatbot.postprocess(history)
        assert displayed is not None
        payload = str(displayed.model_dump())
        assert "Hello" in payload
        assert "Hi there!" in payload
    
    def test_history_state_persistence(self):
        """测试历史状态的持久性"""
        # 修改历史状态
        original_page = self.chat_tab.history_state["current_page"]
        
        # 导航到下一页
        self.chat_tab._navigate_history_page(1, [])
        
        # 验证状态已更新(考虑到边界限制)
        new_page = self.chat_tab.history_state["current_page"]
        assert new_page >= original_page  # 页面应该增加或保持不变(如果已在最后一页)
        
        # 再次导航
        self.chat_tab._navigate_history_page(1, [])
        
        # 验证状态继续更新(考虑到边界限制)
        final_page = self.chat_tab.history_state["current_page"]
        assert final_page >= new_page  # 页面应该增加或保持不变


class TestHistoryManagementIntegration:
    """历史记录管理集成测试"""
    
    def setup_method(self):
        """测试前设置"""
        self.temp_dir = tempfile.mkdtemp()
        self.mock_chat_service = Mock(spec=ChatService)
        
    def teardown_method(self):
        """测试后清理"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_history_management_with_preferences(self):
        """测试历史管理与偏好设置的集成"""
        preferences = UserPreferences(config_dir=self.temp_dir)
        
        # 设置初始偏好
        preferences.set_show_history(True)
        
        with patch('chatterpal.web.components.chat_tab.get_preferences_manager') as mock_get_prefs:
            mock_get_prefs.return_value = preferences
            chat_tab = ChatTab(self.mock_chat_service)
            
            # 验证初始设置
            assert chat_tab.preferences.get_show_history() is True
            
            # 切换显示设置
            chat_tab._toggle_history_display(False)
            
            # 验证设置已更新并持久化
            assert chat_tab.preferences.get_show_history() is False
            
            # 创建新实例验证持久化
            new_preferences = UserPreferences(config_dir=self.temp_dir)
            assert new_preferences.get_show_history() is False
    
    def test_history_management_with_chat_service(self):
        """测试历史管理与聊天服务的集成"""
        # 模拟聊天服务
        sample_messages = [
            {"role": "user", "content": "Test message 1"},
            {"role": "assistant", "content": "Response 1"},
            {"role": "user", "content": "Test message 2"},
            {"role": "assistant", "content": "Response 2"},
        ]
        
        self.mock_chat_service.get_conversation_history.return_value = sample_messages
        self.mock_chat_service.clear_conversation_history.return_value = True
        
        preferences = UserPreferences(config_dir=self.temp_dir)
        
        with patch('chatterpal.web.components.chat_tab.get_preferences_manager') as mock_get_prefs:
            mock_get_prefs.return_value = preferences
            chat_tab = ChatTab(self.mock_chat_service)
            chat_tab.current_session_id = "test_session"
            
            # 测试获取历史状态
            status = chat_tab._update_history_status([])
            assert ("显示 0/4 条对话" in status) or ("4 条对话" in status)
            
            # 测试清除历史
            result = chat_tab._clear_history_with_confirmation([])
            history, status_update, page_update, prev_btn_update, next_btn_update = result

            assert history == []
            assert "已清除所有对话" in status_update["value"]
            assert page_update["value"] == "**第 1 页，共 1 页**"
            assert prev_btn_update["interactive"] is False
            assert next_btn_update["interactive"] is False
            
            # 验证服务方法被调用
            self.mock_chat_service.clear_conversation_history.assert_called_once_with("test_session")

    def test_cleared_history_does_not_count_system_prompt(self):
        """Clearing visible history must leave an empty UI despite retained context."""
        service = ChatService()
        session_id = service.create_session(system_prompt="Internal coaching instructions")
        session = service.get_session(session_id)
        session.add_user_message("Hello")
        session.add_assistant_message("Hi there!")
        preferences = UserPreferences(config_dir=self.temp_dir)
        with patch(
            "chatterpal.web.components.chat_tab.get_preferences_manager",
            return_value=preferences,
        ):
            chat_tab = ChatTab(service)
        chat_tab.current_session_id = session_id

        history, status, page, previous, following = chat_tab._clear_history_with_confirmation(
            [["Hello", "Hi there!"]]
        )
        assert history == []
        assert "已清除所有对话" in status["value"]
        assert page["value"] == "**第 1 页，共 1 页**"
        assert previous["interactive"] is False
        assert following["interactive"] is False
        # Keeping the system message internally is the documented service
        # contract. UI counts and empty-history status must still exclude it.
        assert service.get_conversation_history(session_id) == [
            {"role": "system", "content": "Internal coaching instructions"}
        ]
        assert chat_tab._update_history_status(history) == "**历史记录:** 暂无对话记录"


if __name__ == "__main__":
    pytest.main([__file__])









