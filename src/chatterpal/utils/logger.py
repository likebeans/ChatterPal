# -*- coding: utf-8 -*-
"""
日志工具模块

提供统一的日志配置和管理功能。
"""

import logging
import sys
from pathlib import Path
from typing import Optional, Dict, Any
from logging.handlers import RotatingFileHandler
import json
from datetime import datetime

from .encoding_fix import safe_str


class ColoredFormatter(logging.Formatter):
    """彩色日志格式化器"""

    # ANSI颜色代码
    COLORS = {
        "DEBUG": "\033[36m",  # 青色
        "INFO": "\033[32m",  # 绿色
        "WARNING": "\033[33m",  # 黄色
        "ERROR": "\033[31m",  # 红色
        "CRITICAL": "\033[35m",  # 紫色
        "RESET": "\033[0m",  # 重置
    }

    def format(self, record):
        # 格式化结束后恢复记录，避免颜色进入其他处理器的输出。
        levelname = record.levelname
        try:
            if levelname in self.COLORS:
                record.levelname = f"{self.COLORS[levelname]}{levelname}{self.COLORS['RESET']}"
            return super().format(record)
        finally:
            record.levelname = levelname


class JSONFormatter(logging.Formatter):
    """JSON格式化器"""

    def format(self, record):
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # 添加异常信息
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # 添加额外字段
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)

        return json.dumps(log_entry, ensure_ascii=False)


class ChatterPalLogger:
    """ChatterPal专用日志器"""

    def __init__(self, name: str = "chatterpal"):
        """
        初始化日志器

        Args:
            name: 日志器名称
        """
        self.name = name
        self.logger = logging.getLogger(name)
        self._configured = False

    def configure(
        self,
        level: str = "INFO",
        log_file: Optional[str] = None,
        max_file_size: int = 10 * 1024 * 1024,  # 10MB
        backup_count: int = 5,
        console_output: bool = True,
        json_format: bool = False,
        colored_output: bool = True,
    ) -> None:
        """
        配置日志器

        Args:
            level: 日志级别
            log_file: 日志文件路径
            max_file_size: 最大文件大小（字节）
            backup_count: 备份文件数量
            console_output: 是否输出到控制台
            json_format: 是否使用JSON格式
            colored_output: 是否使用彩色输出（仅控制台）
        """
        if self._configured:
            return

        # 设置日志级别
        self.logger.setLevel(getattr(logging, level.upper()))

        # 清除现有处理器
        self.logger.handlers.clear()

        # 创建格式化器
        if json_format:
            formatter = JSONFormatter()
        else:
            format_string = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            if colored_output and console_output:
                formatter = ColoredFormatter(format_string)
            else:
                formatter = logging.Formatter(format_string)

        # 控制台处理器
        if console_output:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)

        # 文件处理器
        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)

            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=max_file_size,
                backupCount=backup_count,
                encoding="utf-8",
            )

            # 文件日志使用普通格式（不使用颜色）
            if json_format:
                file_formatter = JSONFormatter()
            else:
                file_formatter = logging.Formatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
                )

            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)

        self._configured = True

    def debug(self, message: str, **kwargs) -> None:
        """记录调试信息"""
        self._log_with_extra(logging.DEBUG, message, kwargs)

    def info(self, message: str, **kwargs) -> None:
        """记录信息"""
        self._log_with_extra(logging.INFO, message, kwargs)

    def warning(self, message: str, **kwargs) -> None:
        """记录警告"""
        self._log_with_extra(logging.WARNING, message, kwargs)

    def error(self, message: str, **kwargs) -> None:
        """记录错误"""
        self._log_with_extra(logging.ERROR, message, kwargs)

    def critical(self, message: str, **kwargs) -> None:
        """记录严重错误"""
        self._log_with_extra(logging.CRITICAL, message, kwargs)

    def exception(self, message: str, **kwargs) -> None:
        """记录异常（包含堆栈跟踪）"""
        self._log_with_extra(logging.ERROR, message, kwargs, exc_info=True)

    def _log_with_extra(
        self,
        level: int,
        message: str,
        extra_fields: Dict[str, Any],
        exc_info: bool = False,
    ) -> None:
        """带额外字段的日志记录"""
        extra = {"extra_fields": extra_fields} if extra_fields else None
        self.logger.log(level, message, exc_info=exc_info, extra=extra)


# 全局日志器实例
_global_logger: Optional[ChatterPalLogger] = None


def get_logger(name: str = "chatterpal") -> ChatterPalLogger:
    """
    获取日志器实例

    Args:
        name: 日志器名称

    Returns:
        ChatterPalLogger: 日志器实例
    """
    global _global_logger

    if _global_logger is None or _global_logger.name != name:
        _global_logger = ChatterPalLogger(name)

    return _global_logger


def setup_logging(config: Optional[Dict[str, Any]] = None) -> ChatterPalLogger:
    """
    设置全局日志配置

    Args:
        config: 日志配置字典

    Returns:
        ChatterPalLogger: 配置好的日志器
    """
    logger = get_logger()

    if config is None:
        config = {"level": "INFO", "console_output": True, "colored_output": True}

    logger.configure(**config)
    return logger


class LoggerMixin:
    """日志器混入类"""

    @property
    def logger(self) -> ChatterPalLogger:
        """获取日志器"""
        if not hasattr(self, "_logger"):
            class_name = self.__class__.__name__
            self._logger = get_logger(f"chatterpal.{class_name}")
        return self._logger


def log_function_call(func):
    """
    函数调用日志装饰器

    Args:
        func: 被装饰的函数

    Returns:
        装饰后的函数
    """

    def wrapper(*args, **kwargs):
        logger = get_logger()
        func_name = f"{func.__module__}.{func.__name__}"

        logger.debug(
            f"调用函数: {func_name}",
            function=func_name,
            args=str(args)[:100],
            kwargs=str(kwargs)[:100],
        )

        try:
            result = func(*args, **kwargs)
            logger.debug(f"函数完成: {func_name}")
            return result
        except Exception as e:
            logger.exception(f"函数异常: {func_name}", function=func_name, error=safe_str(e))
            raise

    return wrapper


def log_performance(func):
    """
    性能监控日志装饰器

    Args:
        func: 被装饰的函数

    Returns:
        装饰后的函数
    """
    import time

    def wrapper(*args, **kwargs):
        logger = get_logger()
        func_name = f"{func.__module__}.{func.__name__}"

        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            execution_time = time.time() - start_time

            logger.info(
                f"性能监控: {func_name}",
                function=func_name,
                execution_time=f"{execution_time:.3f}s",
            )

            return result
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(
                f"性能监控(异常): {func_name}",
                function=func_name,
                execution_time=f"{execution_time:.3f}s",
                error=safe_str(e),
            )
            raise

    return wrapper


# 便捷函数
def debug(message: str, **kwargs) -> None:
    """记录调试信息"""
    get_logger().debug(message, **kwargs)


def info(message: str, **kwargs) -> None:
    """记录信息"""
    get_logger().info(message, **kwargs)


def warning(message: str, **kwargs) -> None:
    """记录警告"""
    get_logger().warning(message, **kwargs)


def error(message: str, **kwargs) -> None:
    """记录错误"""
    get_logger().error(message, **kwargs)


def critical(message: str, **kwargs) -> None:
    """记录严重错误"""
    get_logger().critical(message, **kwargs)


def exception(message: str, **kwargs) -> None:
    """记录异常"""
    get_logger().exception(message, **kwargs)
