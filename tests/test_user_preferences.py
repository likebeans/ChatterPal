"""
用户偏好设置管理模块的测试
"""

import pytest
import tempfile
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from chatterpal.utils.preferences import (
    UserPreferences,
    get_preferences_manager,
    get_user_preference,
    set_user_preference
)


@pytest.fixture(autouse=True)
def isolated_preferences_state(tmp_path, monkeypatch):
    """Keep the global convenience API away from checkout/user preferences."""
    from chatterpal.utils import preferences

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(preferences, "_preferences_manager", None)


class TestUserPreferences:
    """用户偏好设置测试"""
    
    def setup_method(self):
        """测试前设置"""
        # 使用临时目录进行测试
        self.temp_dir = tempfile.mkdtemp()
        self.preferences = UserPreferences(config_dir=self.temp_dir)
    
    def teardown_method(self):
        """测试后清理"""
        # 清理临时文件
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_init_creates_config_directory(self):
        """测试初始化时创建配置目录"""
        assert self.preferences.config_dir.exists()
        assert self.preferences.config_dir.is_dir()
    
    def test_default_preferences_loaded(self):
        """测试默认偏好设置加载"""
        # 检查默认设置
        assert self.preferences.get("chat.input_mode") == "text"
        assert self.preferences.get("chat.auto_play_response") is True
        assert self.preferences.get("chat.show_history") is False
        assert self.preferences.get("ui.language") == "zh-CN"
    
    def test_get_set_preference(self):
        """测试获取和设置偏"""
        # 设置简单
        assert self.preferences.set("test_key", "test_value")
        assert self.preferences.get("test_key") == "test_value"
        
        # 设置嵌套
        assert self.preferences.set("nested.key", "nested_value")
        assert self.preferences.get("nested.key") == "nested_value"
        
        # 获取不存在的键
        assert self.preferences.get("nonexistent", "default") == "default"
    
    def test_input_mode_management(self):
        """测试输入模式管理"""
        # 测试设置有效模式
        assert self.preferences.set_input_mode("voice")
        assert self.preferences.get_input_mode() == "voice"
        
        assert self.preferences.set_input_mode("text")
        assert self.preferences.get_input_mode() == "text"
        
        # 测试设置无效模式
        assert not self.preferences.set_input_mode("invalid")
        # 应该保持原来的值
        assert self.preferences.get_input_mode() == "text"
    
    def test_auto_play_response_management(self):
        """测试自动播放回复管理"""
        # 测试设置自动播放
        assert self.preferences.set_auto_play_response(False)
        assert self.preferences.get_auto_play_response() is False
        
        assert self.preferences.set_auto_play_response(True)
        assert self.preferences.get_auto_play_response() is True
    
    def test_show_history_management(self):
        """测试历史记录显示管理"""
        # 测试设置显示历史
        assert self.preferences.set_show_history(True)
        assert self.preferences.get_show_history() is True
        
        assert self.preferences.set_show_history(False)
        assert self.preferences.get_show_history() is False
    
    def test_persistence(self):
        """测试持久化保存"""
        # 设置一些值
        self.preferences.set("test.persistence", "persistent_value")
        self.preferences.set_input_mode("voice")
        
        # 创建新的实例(模拟重启)
        new_preferences = UserPreferences(config_dir=self.temp_dir)
        
        # 检查值是否被保存
        assert new_preferences.get("test.persistence") == "persistent_value"
        assert new_preferences.get_input_mode() == "voice"
    
    def test_get_category_preferences(self):
        """测试获取分类偏好设置"""
        # 测试获取聊天偏好
        chat_prefs = self.preferences.get_chat_preferences()
        assert isinstance(chat_prefs, dict)
        assert "input_mode" in chat_prefs
        assert "auto_play_response" in chat_prefs
        
        # 测试获取音频偏好
        audio_prefs = self.preferences.get_audio_preferences()
        assert isinstance(audio_prefs, dict)
        
        # 测试获取主题偏好
        topic_prefs = self.preferences.get_topic_preferences()
        assert isinstance(topic_prefs, dict)
    
    def test_reset_to_defaults(self):
        """测试重置为默认设置"""
        # 修改一些设置
        self.preferences.set("test.custom", "custom_value")
        self.preferences.set_input_mode("voice")
        
        # 重置为默认
        assert self.preferences.reset_to_defaults()
        
        # 检查是否重置成功
        assert self.preferences.get("test.custom") is None
        assert self.preferences.get_input_mode() == "text"
    
    def test_export_import_preferences(self):
        """测试导出和导入偏好设置"""
        # 设置一些自定义值
        self.preferences.set("custom.setting", "custom_value")
        self.preferences.set_input_mode("voice")
        
        # 导出到临时文件
        export_file = os.path.join(self.temp_dir, "export.json")
        assert self.preferences.export_preferences(export_file)
        assert os.path.exists(export_file)
        
        # 重置设置
        self.preferences.reset_to_defaults()
        assert self.preferences.get("custom.setting") is None
        assert self.preferences.get_input_mode() == "text"
        
        # 导入设置
        assert self.preferences.import_preferences(export_file)
        assert self.preferences.get("custom.setting") == "custom_value"
        assert self.preferences.get_input_mode() == "voice"
    
    def test_update_preferences_batch(self):
        """测试批量更新偏好设置"""
        updates = {
            "chat": {
                "input_mode": "voice",
                "auto_play_response": False
            },
            "ui": {
                "theme": "dark"
            }
        }
        
        assert self.preferences.update_preferences(updates)
        
        # 检查更新结果
        assert self.preferences.get_input_mode() == "voice"
        assert self.preferences.get_auto_play_response() is False
        assert self.preferences.get("ui.theme") == "dark"
    
    def test_merge_preferences(self):
        """测试偏好设置合并"""
        default = {
            "a": 1,
            "b": {"c": 2, "d": 3},
            "e": 4
        }
        
        loaded = {
            "b": {"c": 20},  # 覆盖嵌套
            "f": 5  # 新增
        }
        
        result = self.preferences._merge_preferences(default, loaded)
        
        assert result["a"] == 1  # 保持默认
        assert result["b"]["c"] == 20  # 覆盖
        assert result["b"]["d"] == 3  # 保持默认嵌套
        assert result["e"] == 4  # 保持默认
        assert result["f"] == 5  # 新增
    
    def test_error_handling(self):
        """测试错误处理"""
        # 测试保存失败的情
        with patch('builtins.open', side_effect=PermissionError("Permission denied")):
            # 设置操作应该失败但不抛出异常
            result = self.preferences.set("test.key", "test_value")
            assert result is False
        
        # 测试加载损坏的配置文
        import json
        with patch('json.load', side_effect=json.JSONDecodeError("Invalid JSON", "", 0)):
            # 应该使用默认设置
            prefs = UserPreferences(config_dir=self.temp_dir)
            assert prefs.get("chat.input_mode") == "text"


class TestGlobalPreferencesManager:
    """全局偏好管理器测试类"""
    
    def test_singleton_behavior(self):
        """测试单例行为"""
        manager1 = get_preferences_manager()
        manager2 = get_preferences_manager()
        
        # 应该返回同一个实
        assert manager1 is manager2
    
    def test_convenience_functions(self):
        """测试便捷函数"""
        # 测试设置和获
        assert set_user_preference("test.convenience", "test_value")
        assert get_user_preference("test.convenience") == "test_value"
        
        # 测试默认
        assert get_user_preference("nonexistent.key", "default") == "default"


class TestPreferencesIntegration:
    """用户偏好设置管理模块的测试"""
    
    def setup_method(self):
        """测试前设置"""
        self.temp_dir = tempfile.mkdtemp()
    
    def teardown_method(self):
        """测试后清理"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_file_format_compatibility(self):
        """测试文件格式兼容性"""
        preferences = UserPreferences(config_dir=self.temp_dir)
        
        # 设置一些
        preferences.set("test.value", "test")
        preferences.set_input_mode("voice")
        
        # 检查生成的JSON文件格式
        config_file = preferences.config_file
        assert config_file.exists()
        
        with open(config_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        assert data["chat"]["input_mode"] == "voice"
        assert data["test"]["value"] == "test"
        assert "last_updated" in data
    
    def test_concurrent_access(self):
        """测试并发访问"""
        # 创建多个实例访问同一配置
        prefs1 = UserPreferences(config_dir=self.temp_dir)
        prefs2 = UserPreferences(config_dir=self.temp_dir)
        
        # 第一个实例设置
        prefs1.set("concurrent.test", "value1")
        
        # 第二个实例应该能读取到更
        prefs2._preferences = prefs2._load_preferences()
        assert prefs2.get("concurrent.test") == "value1"
    
    def test_migration_compatibility(self):
        """测试迁移兼容性"""
        # 模拟旧版本配置文件
        old_config = {
            "input_mode": "voice",  # 旧格式:直接在根级别
            "auto_play": True
        }
        
        config_file = Path(self.temp_dir) / "user_preferences.json"
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(old_config, f)
        
        # 加载应该能处理旧格式
        preferences = UserPreferences(config_dir=self.temp_dir)
        
        # 检查是否正确合并了默认设置
        assert preferences.get("chat.input_mode") == "text"  # 使用默认
        assert preferences.get("input_mode") == "voice"  # 保留旧


if __name__ == "__main__":
    pytest.main([__file__])








