"""
聊天模块配置管理测试
"""

import pytest
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch

from chatterpal.services.chat_config import (
    AudioConfig,
    TopicGenerationConfig,
    SessionConfig,
    UIConfig,
    ChatModuleConfig,
    ChatConfigManager,
    get_chat_config_manager,
    get_chat_config,
    update_chat_config
)


@pytest.fixture(autouse=True)
def isolated_config_state(tmp_path, monkeypatch):
    """Exercise real managers with per-test storage and fresh singletons."""
    from chatterpal.services import chat_config
    from chatterpal.utils import preferences

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(preferences, "_preferences_manager", None)
    monkeypatch.setattr(chat_config, "_chat_config_manager", None)
    settings = Mock()
    settings.get_cache_path.return_value = tmp_path / "cache"
    monkeypatch.setattr(chat_config, "get_settings", lambda: settings)


class TestAudioConfig:
    """音频配置测试"""
    
    def test_default_audio_config(self):
        """测试默认音频配置"""
        config = AudioConfig()
        
        assert config.max_recording_duration == 60
        assert config.min_recording_duration == 1
        assert config.sample_rate == 16000
        assert config.format == "wav"
        assert config.auto_play is True
        assert config.playback_speed == 1.0
        assert config.volume == 0.8
        assert config.validate() is True
    
    def test_audio_config_validation(self):
        """测试音频配置验证"""
        # 有效配置
        config = AudioConfig(
            max_recording_duration=30,
            min_recording_duration=2,
            sample_rate=16000,
            format="wav",
            playback_speed=1.5,
            volume=0.5
        )
        assert config.validate() is True
        
        # 无效的最大录音时长
        config.max_recording_duration = 400
        assert config.validate() is False
        
        # 无效的采样率
        config.max_recording_duration = 30
        config.sample_rate = 12000
        assert config.validate() is False
        
        # 无效的播放速度
        config.sample_rate = 16000
        config.playback_speed = 5.0
        assert config.validate() is False


class TestTopicGenerationConfig:
    """主题生成配置测试"""
    
    def test_default_topic_config(self):
        """测试默认主题配置"""
        config = TopicGenerationConfig()
        
        assert "beginner" in config.difficulty_levels
        assert "intermediate" in config.difficulty_levels
        assert "advanced" in config.difficulty_levels
        assert config.default_difficulty == "intermediate"
        assert config.max_retries == 3
        assert config.validate() is True
    
    def test_topic_config_validation(self):
        """测试主题配置验证"""
        # 有效配置
        config = TopicGenerationConfig(
            default_difficulty="beginner",
            preferred_categories=["daily", "hobby"]
        )
        assert config.validate() is True
        
        # 无效的默认难度
        config.default_difficulty = "expert"
        assert config.validate() is False
        
        # 无效的偏好分类
        config.default_difficulty = "beginner"
        config.preferred_categories = ["invalid_category"]
        assert config.validate() is False


class TestSessionConfig:
    """会话配置测试"""
    
    def test_default_session_config(self):
        """测试默认会话配置"""
        config = SessionConfig()
        
        assert config.max_history_length == 50
        assert config.session_timeout == 3600
        assert config.auto_save is True
        assert config.validate() is True
    
    def test_session_config_validation(self):
        """测试会话配置验证"""
        # 有效配置
        config = SessionConfig(
            max_history_length=100,
            session_timeout=1800
        )
        assert config.validate() is True
        
        # 无效的历史长度
        config.max_history_length = 2000
        assert config.validate() is False
        
        # 无效的超时时间
        config.max_history_length = 100
        config.session_timeout = 100000
        assert config.validate() is False


class TestUIConfig:
    """UI配置测试"""
    
    def test_default_ui_config(self):
        """测试默认UI配置"""
        config = UIConfig()
        
        assert config.default_input_mode == "text"
        assert config.show_history is False
        assert config.language == "zh-CN"
        assert config.validate() is True
    
    def test_ui_config_validation(self):
        """测试UI配置验证"""
        # 有效配置
        config = UIConfig(
            default_input_mode="voice",
            language="en-US"
        )
        assert config.validate() is True
        
        # 无效的输入模式
        config.default_input_mode = "invalid"
        assert config.validate() is False
        
        # 无效的语言
        config.default_input_mode = "text"
        config.language = "fr-FR"
        assert config.validate() is False


class TestChatModuleConfig:
    """聊天模块配置测试"""
    
    def test_default_chat_config(self):
        """测试默认聊天配置"""
        config = ChatModuleConfig()
        
        assert isinstance(config.audio, AudioConfig)
        assert isinstance(config.topic_generation, TopicGenerationConfig)
        assert isinstance(config.session, SessionConfig)
        assert isinstance(config.ui, UIConfig)
        assert config.version == "1.0.0"
        assert config.validate() is True
    
    def test_config_to_dict(self):
        """测试配置值转换为字典"""
        config = ChatModuleConfig()
        config_dict = config.to_dict()
        
        assert "audio" in config_dict
        assert "topic_generation" in config_dict
        assert "session" in config_dict
        assert "ui" in config_dict
        assert "version" in config_dict
        assert "last_updated" in config_dict
    
    def test_config_from_dict(self):
        """测试从字典创建配置"""
        data = {
            "audio": {"max_recording_duration": 45},
            "topic_generation": {"default_difficulty": "advanced"},
            "session": {"max_history_length": 30},
            "ui": {"default_input_mode": "voice"},
            "version": "1.1.0"
        }
        
        config = ChatModuleConfig.from_dict(data)
        
        assert config.audio.max_recording_duration == 45
        assert config.topic_generation.default_difficulty == "advanced"
        assert config.session.max_history_length == 30
        assert config.ui.default_input_mode == "voice"
        assert config.version == "1.1.0"


class TestChatConfigManager:
    """聊天配置管理器测试"""
    
    @pytest.fixture
    def temp_config_dir(self):
        """创建临时配置目录"""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)
    
    @pytest.fixture
    def config_manager(self, temp_config_dir):
        """创建配置管理器实例"""
        return ChatConfigManager(config_dir=temp_config_dir)
    
    def test_config_manager_init(self, config_manager):
        """测试配置管理器初始化"""
        assert config_manager.config_dir.exists()
        assert config_manager.backup_dir.exists()
        assert isinstance(config_manager.get_config(), ChatModuleConfig)
    
    def test_save_and_load_config(self, config_manager):
        """测试保存和加载配置"""
        # 修改配置
        original_config = config_manager.get_config()
        original_config.audio.max_recording_duration = 90
        
        # 保存配置
        success = config_manager._save_config(original_config)
        assert success is True
        
        # 创建新的管理器实例来测试加载
        new_manager = ChatConfigManager(config_dir=str(config_manager.config_dir))
        loaded_config = new_manager.get_config()
        
        assert loaded_config.audio.max_recording_duration == 90
    
    def test_update_config(self, config_manager):
        """测试更新配置"""
        updates = {
            "audio": {
                "max_recording_duration": 120,
                "auto_play": False
            },
            "ui": {
                "default_input_mode": "voice"
            }
        }
        
        success = config_manager.update_config(updates)
        assert success is True
        
        config = config_manager.get_config()
        assert config.audio.max_recording_duration == 120
        assert config.audio.auto_play is False
        assert config.ui.default_input_mode == "voice"
    
    def test_update_audio_config(self, config_manager):
        """测试更新音频配置"""
        success = config_manager.update_audio_config(
            max_recording_duration=75,
            volume=0.9
        )
        assert success is True
        
        audio_config = config_manager.get_audio_config()
        assert audio_config.max_recording_duration == 75
        assert audio_config.volume == 0.9
    
    def test_update_topic_config(self, config_manager):
        """测试更新主题配置"""
        success = config_manager.update_topic_config(
            default_difficulty="advanced",
            auto_generate=True
        )
        assert success is True
        
        topic_config = config_manager.get_topic_config()
        assert topic_config.default_difficulty == "advanced"
        assert topic_config.auto_generate is True
    
    def test_invalid_config_update(self, config_manager):
        """测试无效配置更新"""
        updates = {
            "audio": {
                "max_recording_duration": 500  # 超出有效范围
            }
        }
        
        success = config_manager.update_config(updates)
        assert success is False
    
    def test_reset_to_defaults(self, config_manager):
        """测试重置为默认配置"""
        # 先修改配置
        config_manager.update_audio_config(max_recording_duration=120)
        
        # 重置配置
        success = config_manager.reset_to_defaults()
        assert success is True
        
        config = config_manager.get_config()
        assert config.audio.max_recording_duration == 60  # 默认值
    
    def test_export_import_config(self, config_manager, temp_config_dir):
        """测试导出和导入配置"""
        # 修改配置
        config_manager.update_audio_config(max_recording_duration=80)
        
        # 导出配置
        export_file = Path(temp_config_dir) / "exported_config.json"
        success = config_manager.export_config(str(export_file))
        assert success is True
        assert export_file.exists()
        
        # 重置配置
        config_manager.reset_to_defaults()
        assert config_manager.get_config().audio.max_recording_duration == 60
        
        # 导入配置
        success = config_manager.import_config(str(export_file))
        assert success is True
        assert config_manager.get_config().audio.max_recording_duration == 80
    
    def test_sync_with_user_preferences(self, config_manager):
        """测试与用户偏好同步"""
        # 模拟用户偏好
        mock_prefs = Mock()
        mock_prefs.get_chat_preferences.return_value = {
            "input_mode": "voice",
            "show_history": True,
            "auto_play_response": False
        }
        mock_prefs.get_audio_preferences.return_value = {
            "volume": 0.6,
            "playback_speed": 1.2
        }
        mock_prefs.get_topic_preferences.return_value = {
            "difficulty": "advanced",
            "auto_generate": True
        }
        
        # 直接设置配置管理器的preferences
        config_manager.preferences = mock_prefs
        
        # 同步偏好
        success = config_manager.sync_with_user_preferences()
        assert success is True
        
        config = config_manager.get_config()
        assert config.ui.default_input_mode == "voice"
        assert config.ui.show_history is True
        assert config.audio.auto_play is False
        assert config.audio.volume == 0.6
        assert config.audio.playback_speed == 1.2
        assert config.topic_generation.default_difficulty == "advanced"
        assert config.topic_generation.auto_generate is True
    
    def test_get_config_summary(self, config_manager):
        """测试获取配置摘要"""
        summary = config_manager.get_config_summary()
        
        assert "version" in summary
        assert "last_updated" in summary
        assert "audio" in summary
        assert "topic" in summary
        assert "session" in summary
        assert "ui" in summary
        
        # 检查音频摘要
        assert "max_duration" in summary["audio"]
        assert "sample_rate" in summary["audio"]
        assert "auto_play" in summary["audio"]
        
        # 检查主题摘要
        assert "default_difficulty" in summary["topic"]
        assert "categories_count" in summary["topic"]
        assert "auto_generate" in summary["topic"]


class TestGlobalFunctions:
    """全局函数测试"""
    
    @patch('chatterpal.services.chat_config._chat_config_manager', None)
    def test_get_chat_config_manager(self):
        """测试获取全局配置管理器"""
        manager1 = get_chat_config_manager()
        manager2 = get_chat_config_manager()
        
        # 应该返回同一个实例
        assert manager1 is manager2
        assert isinstance(manager1, ChatConfigManager)
    
    def test_get_chat_config(self):
        """测试获取聊天配置"""
        config = get_chat_config()
        assert isinstance(config, ChatModuleConfig)
    
    def test_update_chat_config(self):
        """测试更新聊天配置"""
        updates = {
            "audio": {"volume": 0.5}
        }
        
        success = update_chat_config(updates)
        assert success is True
        
        config = get_chat_config()
        assert config.audio.volume == 0.5


class TestConfigIntegration:
    """配置集成测试"""
    
    @pytest.fixture
    def temp_config_dir(self):
        """创建临时配置目录"""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)
    
    def test_config_persistence(self, temp_config_dir):
        """测试配置值持久化"""
        # 创建配置管理器并修改配置
        manager1 = ChatConfigManager(config_dir=temp_config_dir)
        manager1.update_audio_config(max_recording_duration=100)
        
        # 创建新的管理器实例,应该加载之前的配置
        manager2 = ChatConfigManager(config_dir=temp_config_dir)
        config = manager2.get_config()
        
        assert config.audio.max_recording_duration == 100
    
    def test_config_backup_creation(self, temp_config_dir):
        """测试配置值备份创建"""
        manager = ChatConfigManager(config_dir=temp_config_dir)
        
        # 先保存一次配置以创建初始文件
        manager._save_config()
        
        # 添加一些延迟以确保时间戳不同
        import time
        
        # 多次更新配置以创建备份
        for i in range(3):
            time.sleep(1)  # 确保时间戳不同
            manager.update_audio_config(max_recording_duration=60 + i * 10)
        
        # 检查备份文件
        backup_files = list(manager.backup_dir.glob("chat_config_*.json"))
        assert len(backup_files) >= 1  # 至少一个备份文件
        
        # 检查配置文件存在
        assert manager.config_file.exists()
    
    def test_invalid_config_file_handling(self, temp_config_dir):
        """测试无效配置文件处理"""
        # An explicit config_dir is used directly by ChatConfigManager.
        config_file = Path(temp_config_dir) / "chat_config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 写入无效的JSON
        with open(config_file, 'w') as f:
            f.write("invalid json content")
        
        # 创建管理器,应该使用默认配置
        manager = ChatConfigManager(config_dir=temp_config_dir)
        config = manager.get_config()

        assert manager.config_file == config_file
        assert config.audio.max_recording_duration == 60  # 默认


if __name__ == "__main__":
    pytest.main([__file__])

    






