# -*- coding: utf-8 -*-
"""
用户偏好设置管理模块
提供用户界面偏好的持久化存储和管理功能
"""

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional
import logging
from datetime import datetime


logger = logging.getLogger(__name__)


class UserPreferences:
    """用户偏好设置管理类"""
    
    def __init__(self, config_dir: str = ".config/chatterpal"):
        """
        初始化用户偏好管理器
        
        Args:
            config_dir: 配置文件目录
        """
        self.config_dir = Path(config_dir)
        self.config_file = self.config_dir / "user_preferences.json"
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # 确保配置目录存在
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.logger.warning(f"无法创建配置目录 {self.config_dir}: {e}")
            # 继续使用默认设置，不阻止初始化
        
        # 默认偏好设置
        self.default_preferences = {
            "chat": {
                "input_mode": "text",  # "text" 或 "voice"
                "auto_play_response": True,
                "show_history": False,
                "voice_settings": {
                    "auto_record": False,
                    "recording_timeout": 30
                }
            },
            "ui": {
                "theme": "default",
                "language": "zh-CN",
                "compact_mode": False
            },
            "audio": {
                "playback_speed": 1.0,
                "volume": 0.8,
                "auto_play": True
            },
            "topic": {
                "difficulty": "intermediate",
                "preferred_categories": [],
                "auto_generate": False
            },
            "session": {
                "auto_save": True,
                "max_history": 50
            },
            "last_updated": datetime.now().isoformat()
        }
        
        # 加载现有偏好设置
        self._preferences = self._load_preferences()
    
    def _load_preferences(self) -> Dict[str, Any]:
        """
        从文件加载偏好设置
        
        Returns:
            偏好设置字典
        """
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded_prefs = json.load(f)
                
                # 合并默认设置和加载的设置
                preferences = self._merge_preferences(self.default_preferences, loaded_prefs)
                self.logger.info(f"成功加载用户偏好设置: {self.config_file}")
                return preferences
            else:
                self.logger.info("偏好设置文件不存在，使用默认设置")
                return deepcopy(self.default_preferences)
                
        except Exception as e:
            self.logger.error(f"加载偏好设置失败: {e}")
            return deepcopy(self.default_preferences)
    
    def _merge_preferences(self, default: Dict[str, Any], loaded: Dict[str, Any]) -> Dict[str, Any]:
        """
        合并默认设置和加载的设置
        
        Args:
            default: 默认设置
            loaded: 加载的设置
            
        Returns:
            合并后的设置
        """
        result = deepcopy(default)
        
        for key, value in loaded.items():
            if key in result:
                if isinstance(value, dict) and isinstance(result[key], dict):
                    result[key] = self._merge_preferences(result[key], value)
                else:
                    result[key] = deepcopy(value)
            else:
                result[key] = deepcopy(value)
        
        return result
    
    def _save_preferences(self) -> bool:
        """
        保存偏好设置到文件
        
        Returns:
            是否保存成功
        """
        try:
            # 更新最后修改时间
            self._preferences["last_updated"] = datetime.now().isoformat()
            
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self._preferences, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"成功保存用户偏好设置: {self.config_file}")
            return True
            
        except Exception as e:
            self.logger.error(f"保存偏好设置失败: {e}")
            return False
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        获取偏好设置值
        
        Args:
            key: 设置键，支持点分隔的嵌套键（如 "chat.input_mode"）
            default: 默认值
            
        Returns:
            设置值
        """
        try:
            keys = key.split('.')
            value = self._preferences
            
            for k in keys:
                if isinstance(value, dict) and k in value:
                    value = value[k]
                else:
                    return default
            
            return value
            
        except Exception as e:
            self.logger.error(f"获取偏好设置失败 {key}: {e}")
            return default
    
    def set(self, key: str, value: Any, save: bool = True) -> bool:
        """
        设置偏好设置值
        
        Args:
            key: 设置键，支持点分隔的嵌套键
            value: 设置值
            save: 是否立即保存到文件
            
        Returns:
            是否设置成功
        """
        try:
            keys = key.split('.')
            current = self._preferences
            
            # 导航到目标位置
            for k in keys[:-1]:
                if k not in current:
                    current[k] = {}
                elif not isinstance(current[k], dict):
                    current[k] = {}
                current = current[k]
            
            # 设置值
            current[keys[-1]] = value
            
            if save:
                return self._save_preferences()
            
            return True
            
        except Exception as e:
            self.logger.error(f"设置偏好设置失败 {key}: {e}")
            return False
    
    def get_chat_preferences(self) -> Dict[str, Any]:
        """
        获取聊天相关的偏好设置
        
        Returns:
            聊天偏好设置字典
        """
        return self.get("chat", {})
    
    def set_input_mode(self, mode: str) -> bool:
        """
        设置输入模式
        
        Args:
            mode: 输入模式 ("text" 或 "voice")
            
        Returns:
            是否设置成功
        """
        if mode not in ["text", "voice"]:
            self.logger.error(f"无效的输入模式: {mode}")
            return False
        
        return self.set("chat.input_mode", mode)
    
    def get_input_mode(self) -> str:
        """
        获取当前输入模式
        
        Returns:
            输入模式 ("text" 或 "voice")
        """
        return self.get("chat.input_mode", "text")
    
    def set_auto_play_response(self, auto_play: bool) -> bool:
        """
        设置是否自动播放回复
        
        Args:
            auto_play: 是否自动播放
            
        Returns:
            是否设置成功
        """
        return self.set("chat.auto_play_response", auto_play)
    
    def get_auto_play_response(self) -> bool:
        """
        获取是否自动播放回复
        
        Returns:
            是否自动播放
        """
        return self.get("chat.auto_play_response", True)
    
    def set_show_history(self, show: bool) -> bool:
        """
        设置是否显示历史记录
        
        Args:
            show: 是否显示历史记录
            
        Returns:
            是否设置成功
        """
        return self.set("chat.show_history", show)
    
    def get_show_history(self) -> bool:
        """
        获取是否显示历史记录
        
        Returns:
            是否显示历史记录
        """
        return self.get("chat.show_history", False)
    
    def get_audio_preferences(self) -> Dict[str, Any]:
        """
        获取音频相关的偏好设置
        
        Returns:
            音频偏好设置字典
        """
        return self.get("audio", {})
    
    def get_topic_preferences(self) -> Dict[str, Any]:
        """
        获取主题相关的偏好设置
        
        Returns:
            主题偏好设置字典
        """
        return self.get("topic", {})
    
    def reset_to_defaults(self) -> bool:
        """
        重置为默认设置
        
        Returns:
            是否重置成功
        """
        try:
            # 深拷贝默认设置以避免引用问题
            self._preferences = deepcopy(self.default_preferences)
            return self._save_preferences()
        except Exception as e:
            self.logger.error(f"重置偏好设置失败: {e}")
            return False
    
    def export_preferences(self, file_path: str) -> bool:
        """
        导出偏好设置到文件
        
        Args:
            file_path: 导出文件路径
            
        Returns:
            是否导出成功
        """
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(self._preferences, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"成功导出偏好设置到: {file_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"导出偏好设置失败: {e}")
            return False
    
    def import_preferences(self, file_path: str) -> bool:
        """
        从文件导入偏好设置
        
        Args:
            file_path: 导入文件路径
            
        Returns:
            是否导入成功
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                imported_prefs = json.load(f)
            
            # 合并导入的设置
            self._preferences = self._merge_preferences(self.default_preferences, imported_prefs)
            
            # 保存合并后的设置
            success = self._save_preferences()
            
            if success:
                self.logger.info(f"成功导入偏好设置从: {file_path}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"导入偏好设置失败: {e}")
            return False
    
    def get_all_preferences(self) -> Dict[str, Any]:
        """
        获取所有偏好设置
        
        Returns:
            所有偏好设置的副本
        """
        return deepcopy(self._preferences)
    
    def update_preferences(self, updates: Dict[str, Any]) -> bool:
        """
        批量更新偏好设置
        
        Args:
            updates: 要更新的设置字典
            
        Returns:
            是否更新成功
        """
        try:
            self._preferences = self._merge_preferences(self._preferences, updates)
            return self._save_preferences()
        except Exception as e:
            self.logger.error(f"批量更新偏好设置失败: {e}")
            return False


# 全局偏好管理器实例
_preferences_manager: Optional[UserPreferences] = None


def get_preferences_manager() -> UserPreferences:
    """
    获取全局偏好管理器实例
    
    Returns:
        偏好管理器实例
    """
    global _preferences_manager
    
    if _preferences_manager is None:
        _preferences_manager = UserPreferences()
    
    return _preferences_manager


def get_user_preference(key: str, default: Any = None) -> Any:
    """
    获取用户偏好设置的便捷函数
    
    Args:
        key: 设置键
        default: 默认值
        
    Returns:
        设置值
    """
    return get_preferences_manager().get(key, default)


def set_user_preference(key: str, value: Any) -> bool:
    """
    设置用户偏好设置的便捷函数
    
    Args:
        key: 设置键
        value: 设置值
        
    Returns:
        是否设置成功
    """
    return get_preferences_manager().set(key, value)