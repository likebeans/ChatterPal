"""
音频播放控制功能的测试
测试音频播放、暂停、重播和停止功能
"""

import pytest
import tempfile
from unittest.mock import Mock, MagicMock, patch

from chatterpal.web.components.chat_tab import ChatTab
from chatterpal.services.chat import ChatService
from chatterpal.utils.preferences import UserPreferences


class TestAudioPlaybackControls:
    """音频播放控制测试类"""
    
    def setup_method(self):
        """测试前设置"""
        # 创建临时目录用于偏好设置
        self.temp_dir = tempfile.mkdtemp()
        
        # 创建模拟的ChatService
        self.mock_chat_service = Mock(spec=ChatService)
        self.mock_chat_service.create_session.return_value = "test_session_id"
        self.mock_chat_service.generate_topic.return_value = "测试对话主题"
        self.mock_chat_service.process_chat.return_value = ((16000, [1, 2, 3, 4]), [])
        self.mock_chat_service.clear_context.return_value = None
        
        # 使用临时目录创建偏好管理器
        with patch('chatterpal.web.components.chat_tab.get_preferences_manager') as mock_get_prefs:
            self.preferences = UserPreferences(config_dir=self.temp_dir)
            mock_get_prefs.return_value = self.preferences
            
            # 创建 ChatTab 实例
            self.chat_tab = ChatTab(self.mock_chat_service)
    
    def teardown_method(self):
        """测试后清理"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_toggle_auto_play(self):
        """测试自动播放切换"""
        # 测试启用自动播放
        result = self.chat_tab._toggle_auto_play(True)
        assert result["autoplay"] is True
        assert self.chat_tab.preferences.get_auto_play_response() is True
        
        # 测试禁用自动播放
        result = self.chat_tab._toggle_auto_play(False)
        assert result["autoplay"] is False
        assert self.chat_tab.preferences.get_auto_play_response() is False
    
    def test_auto_play_persistence(self):
        """测试自动播放设置持久"""
        # 设置自动播放为False
        self.chat_tab._toggle_auto_play(False)
        
        # 验证当前实例的设置已更新
        assert self.chat_tab.preferences.get_auto_play_response() is False
        
        # 创建新的偏好管理器器实
        new_preferences = UserPreferences(config_dir=self.temp_dir)
        
        # 验证设置被持久化保存
        assert new_preferences.get_auto_play_response() is False
    
    def test_audio_play_with_valid_data(self):
        """测试有效音频数据的播"""
        # 模拟有效的音频数
        audio_data = (16000, [1, 2, 3, 4, 5])
        
        # 执行播放
        result = self.chat_tab._handle_audio_play(audio_data)
        status_update, play_btn_update, pause_btn_update, stop_btn_update = result
        
        # 验证状态更
        assert "正在播放" in status_update["value"]
        assert play_btn_update["interactive"] is False  # 播放按钮不可
        assert pause_btn_update["interactive"] is True  # 暂停按钮可用
        assert stop_btn_update["interactive"] is True   # 停止按钮可用
    
    def test_audio_play_with_empty_data(self):
        """测试空音频数据的播放"""
        # 测试None数据
        result = self.chat_tab._handle_audio_play(None)
        status_update, play_btn_update, pause_btn_update, stop_btn_update = result
        
        assert "无音频内" in status_update["value"]
        assert play_btn_update["interactive"] is True   # 播放按钮保持可用
        assert pause_btn_update["interactive"] is False # 暂停按钮不可
        assert stop_btn_update["interactive"] is False  # 停止按钮不可
        
        # 测试空音频数据
        empty_audio = (16000, [])
        result = self.chat_tab._handle_audio_play(empty_audio)
        status_update, _, _, _ = result
        
        assert "无音频内" in status_update["value"]
    
    def test_audio_pause(self):
        """测试音频暂停"""
        result = self.chat_tab._handle_audio_pause()
        status_update, play_btn_update, pause_btn_update, stop_btn_update = result
        
        # 验证暂停状
        assert "已暂" in status_update["value"]
        assert play_btn_update["interactive"] is True   # 播放按钮可用(继续播放)
        assert pause_btn_update["interactive"] is False # 暂停按钮不可
        assert stop_btn_update["interactive"] is True   # 停止按钮可用
    
    def test_audio_replay_with_valid_data(self):
        """测试有效音频数据的重播"""
        # 模拟有效的音频数据
        audio_data = (16000, [1, 2, 3, 4, 5])
        
        # 执行重播
        result = self.chat_tab._handle_audio_replay(audio_data)
        status_update, play_btn_update, pause_btn_update, stop_btn_update = result
        
        # 验证重播状态
        assert "重新播放" in status_update["value"]
        assert play_btn_update["interactive"] is False  # 播放按钮不可用
        assert pause_btn_update["interactive"] is True  # 暂停按钮可用
        assert stop_btn_update["interactive"] is True   # 停止按钮可用
    
    def test_audio_replay_with_empty_data(self):
        """测试空音频数据的重播"""
        # 测试None数据
        result = self.chat_tab._handle_audio_replay(None)
        status_update, play_btn_update, pause_btn_update, stop_btn_update = result
        
        assert "无音频内容可重播" in status_update["value"]
        assert play_btn_update["interactive"] is True
        assert pause_btn_update["interactive"] is False
        assert stop_btn_update["interactive"] is False
        
        # 测试空音频数
        empty_audio = (16000, [])
        result = self.chat_tab._handle_audio_replay(empty_audio)
        status_update, _, _, _ = result
        
        assert "无音频内容可重播" in status_update["value"]
    
    def test_audio_stop(self):
        """测试音频停止"""
        result = self.chat_tab._handle_audio_stop()
        status_update, play_btn_update, pause_btn_update, stop_btn_update = result
        
        # 验证停止状态
        assert "已停止" in status_update["value"]
        assert play_btn_update["interactive"] is True   # 播放按钮可用
        assert pause_btn_update["interactive"] is False # 暂停按钮不可用
        assert stop_btn_update["interactive"] is False  # 停止按钮不可用
    
    def test_audio_control_state_transitions(self):
        """测试音频控制状态转换"""
        audio_data = (16000, [1, 2, 3, 4, 5])
        
        # 初始状态 -> 播放
        play_result = self.chat_tab._handle_audio_play(audio_data)
        assert "正在播放" in play_result[0]["value"]
        assert play_result[1]["interactive"] is False  # 播放按钮不可用
        assert play_result[2]["interactive"] is True   # 暂停按钮可用
        
        # 播放 -> 暂停
        pause_result = self.chat_tab._handle_audio_pause()
        assert "已暂停" in pause_result[0]["value"]
        assert pause_result[1]["interactive"] is True  # 播放按钮可用
        assert pause_result[2]["interactive"] is False # 暂停按钮不可用
        
        # 暂停 -> 停止
        stop_result = self.chat_tab._handle_audio_stop()
        assert "已停止" in stop_result[0]["value"]
        assert stop_result[1]["interactive"] is True   # 播放按钮可用
        assert stop_result[2]["interactive"] is False  # 暂停按钮不可用
        assert stop_result[3]["interactive"] is False  # 停止按钮不可用
        
        # 停止 -> 重播
        replay_result = self.chat_tab._handle_audio_replay(audio_data)
        assert "重新播放" in replay_result[0]["value"]
        assert replay_result[1]["interactive"] is False # 播放按钮不可用
        assert replay_result[2]["interactive"] is True  # 暂停按钮可用
        assert replay_result[3]["interactive"] is True  # 停止按钮可用


class TestAudioControlsIntegration:
    """音频控制集成测试"""
    
    def setup_method(self):
        """测试前设置"""
        self.temp_dir = tempfile.mkdtemp()
        self.mock_chat_service = Mock(spec=ChatService)
        
    def teardown_method(self):
        """测试后清理"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_audio_controls_with_chat_interaction(self):
        """测试音频控制与聊天交互的集成"""
        # 模拟聊天服务返回音频
        self.mock_chat_service.create_session.return_value = "test_session"
        self.mock_chat_service.process_chat.return_value = (
            (16000, [1, 2, 3, 4, 5]),  # 音频输出
            [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi there!"}]
        )
        
        preferences = UserPreferences(config_dir=self.temp_dir)
        
        with patch('chatterpal.web.components.chat_tab.get_preferences_manager') as mock_get_prefs:
            mock_get_prefs.return_value = preferences
            chat_tab = ChatTab(self.mock_chat_service)
            
            # 执行聊天
            audio_output, chat_history = chat_tab._handle_chat(
                None, "Hello", [], True
            )
            
            # 验证音频输出
            assert audio_output is not None
            assert isinstance(audio_output, tuple)
            assert len(audio_output) == 2
            assert audio_output[0] == 16000  # 采样
            assert len(audio_output[1]) > 0  # 有音频数
            
            # 测试播放控制
            play_result = chat_tab._handle_audio_play(audio_output)
            assert "正在播放" in play_result[0]["value"]
    
    def test_auto_play_preference_integration(self):
        """测试自动播放偏好设置集成"""
        preferences = UserPreferences(config_dir=self.temp_dir)
        
        # 设置初始偏好
        preferences.set_auto_play_response(False)
        
        with patch('chatterpal.web.components.chat_tab.get_preferences_manager') as mock_get_prefs:
            mock_get_prefs.return_value = preferences
            chat_tab = ChatTab(self.mock_chat_service)
            
            # 验证初始设置
            assert chat_tab.preferences.get_auto_play_response() is False
            
            # 切换自动播放
            chat_tab._toggle_auto_play(True)
            
            # 验证设置已更
            assert chat_tab.preferences.get_auto_play_response() is True
    
    def test_audio_controls_error_handling(self):
        """测试音频控制错误处理"""
        preferences = UserPreferences(config_dir=self.temp_dir)
        
        with patch('chatterpal.web.components.chat_tab.get_preferences_manager') as mock_get_prefs:
            mock_get_prefs.return_value = preferences
            chat_tab = ChatTab(self.mock_chat_service)
            
            # A malformed input exercises the real callback's recovery path.
            class UnreadableAudio:
                def __len__(self):
                    raise ValueError("播放错误")

            status, play, pause, stop = chat_tab._handle_audio_play(
                (16000, UnreadableAudio())
            )
            assert "播放失败" in status["value"]
            assert "播放错误" in status["value"]
            assert play["interactive"] is True
            assert pause["interactive"] is False
            assert stop["interactive"] is False


class TestAudioControlsUserExperience:
    """音频控制用户体验测试"""
    
    def setup_method(self):
        """测试前设置"""
        self.temp_dir = tempfile.mkdtemp()
        self.mock_chat_service = Mock(spec=ChatService)
        
    def teardown_method(self):
        """测试后清理"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_button_states_consistency(self):
        """测试按钮状态一致性"""
        preferences = UserPreferences(config_dir=self.temp_dir)
        
        with patch('chatterpal.web.components.chat_tab.get_preferences_manager') as mock_get_prefs:
            mock_get_prefs.return_value = preferences
            chat_tab = ChatTab(self.mock_chat_service)
            
            audio_data = (16000, [1, 2, 3, 4, 5])
            
            # 测试播放状态下的按钮状
            play_result = chat_tab._handle_audio_play(audio_data)
            _, play_btn, pause_btn, stop_btn = play_result
            
            # 播放时:播放按钮不可用,暂停和停止按钮可
            assert play_btn["interactive"] is False
            assert pause_btn["interactive"] is True
            assert stop_btn["interactive"] is True
            
            # 测试暂停状态下的按钮状
            pause_result = chat_tab._handle_audio_pause()
            _, play_btn, pause_btn, stop_btn = pause_result
            
            # 暂停时:播放按钮可用,暂停按钮不可用,停止按钮可
            assert play_btn["interactive"] is True
            assert pause_btn["interactive"] is False
            assert stop_btn["interactive"] is True
            
            # 测试停止状态下的按钮状
            stop_result = chat_tab._handle_audio_stop()
            _, play_btn, pause_btn, stop_btn = stop_result
            
            # 停止时:播放按钮可用,暂停和停止按钮不可
            assert play_btn["interactive"] is True
            assert pause_btn["interactive"] is False
            assert stop_btn["interactive"] is False
    
    def test_status_messages_clarity(self):
        """测试状态消息的清晰性"""
        preferences = UserPreferences(config_dir=self.temp_dir)
        
        with patch('chatterpal.web.components.chat_tab.get_preferences_manager') as mock_get_prefs:
            mock_get_prefs.return_value = preferences
            chat_tab = ChatTab(self.mock_chat_service)
            
            audio_data = (16000, [1, 2, 3, 4, 5])
            
            # 测试各种状态消息
            play_result = chat_tab._handle_audio_play(audio_data)
            assert "播放状态" in play_result[0]["value"]
            assert "正在播放" in play_result[0]["value"]
            
            pause_result = chat_tab._handle_audio_pause()
            assert "播放状态" in pause_result[0]["value"]
            assert "已暂停" in pause_result[0]["value"]
            
            stop_result = chat_tab._handle_audio_stop()
            assert "播放状态" in stop_result[0]["value"]
            assert "已停止" in stop_result[0]["value"]
            
            replay_result = chat_tab._handle_audio_replay(audio_data)
            assert "播放状态" in replay_result[0]["value"]
            assert "重新播放" in replay_result[0]["value"]
            
            # 测试错误状态消息
            empty_play_result = chat_tab._handle_audio_play(None)
            assert "无音频内容" in empty_play_result[0]["value"]
            
            empty_replay_result = chat_tab._handle_audio_replay(None)
            assert "无音频内容可重播" in empty_replay_result[0]["value"]


if __name__ == "__main__":
    pytest.main([__file__])









