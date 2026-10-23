# -*- coding: utf-8 -*-
"""
Configuration loader utilities.

This module provides utilities for loading and validating configuration
from various sources including environment variables, files, and defaults.
"""

import os
import json
import yaml
from pathlib import Path
from typing import Any, Dict, Optional, Union
from .settings import Settings, get_settings


class ConfigurationError(Exception):
    """Raised when there's an error in configuration."""

    pass


def load_config_file(file_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Load configuration from a file.

    Supports JSON and YAML formats based on file extension.

    Args:
        file_path: Path to the configuration file

    Returns:
        Dictionary containing configuration data

    Raises:
        ConfigurationError: If file cannot be loaded or parsed
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise ConfigurationError(f"Configuration file not found: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            if file_path.suffix.lower() == ".json":
                return json.load(f)
            elif file_path.suffix.lower() in [".yml", ".yaml"]:
                return yaml.safe_load(f) or {}
            else:
                raise ConfigurationError(f"Unsupported file format: {file_path.suffix}")
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise ConfigurationError(f"Error parsing configuration file {file_path}: {e}")
    except Exception as e:
        raise ConfigurationError(f"Error reading configuration file {file_path}: {e}")


def validate_api_keys() -> Dict[str, bool]:
    """
    Validate that required API keys are configured.

    Returns:
        Dictionary with validation results for each provider
    """
    settings = get_settings()

    validation_results = {
        "alibaba": bool(settings.get_dashscope_api_key()),
        "alibaba_asr": settings.has_aliyun_asr_credentials(),
        "openai": bool(settings.openai_api_key),
    }

    return validation_results


def check_required_config() -> None:
    """
    Check that all required configuration is present.

    Raises:
        ConfigurationError: If required configuration is missing
    """
    settings = get_settings()

    errors = settings.get_configuration_errors()
    if errors:
        raise ConfigurationError(" ".join(errors))


def get_config_summary() -> Dict[str, Any]:
    """
    Get a summary of current configuration (without sensitive data).

    Returns:
        Dictionary with configuration summary
    """
    settings = get_settings()

    return {
        "providers": {
            "asr": settings.asr_provider,
            "tts": settings.tts_provider,
            "llm": settings.llm_provider,
        },
        "models": {
            "whisper": settings.whisper_model,
        },
        "audio": {
            "sample_rate": settings.audio_sample_rate,
            "max_duration": settings.audio_max_duration,
            "temp_dir": settings.audio_temp_dir,
        },
        "web": {
            "port": settings.gradio_port,
            "server_name": settings.gradio_server_name,
            "share": settings.gradio_share,
        },
        "environment": {
            "debug": settings.debug,
            "environment": settings.environment,
            "log_level": settings.log_level,
        },
        "api_keys_configured": validate_api_keys(),
    }


def create_default_config_file(
    file_path: Union[str, Path], format: str = "yaml"
) -> None:
    """
    Create a default configuration file with current settings.

    Args:
        file_path: Path where to create the configuration file
        format: File format ("json" or "yaml")
    """
    file_path = Path(file_path)

    # Get current settings (without sensitive data)
    config_data = get_config_summary()

    # Remove API keys status (not needed in config file)
    config_data.pop("api_keys_configured", None)

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            if format.lower() == "json":
                json.dump(config_data, f, indent=2, ensure_ascii=False)
            elif format.lower() in ["yaml", "yml"]:
                yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)
            else:
                raise ValueError(f"Unsupported format: {format}")
    except Exception as e:
        raise ConfigurationError(f"Error creating configuration file {file_path}: {e}")


def print_config_status() -> None:
    """Print current configuration status to console."""
    try:
        summary = get_config_summary()

        print("=== ChatterPal Configuration Status ===")
        print(f"Environment: {summary['environment']['environment']}")
        print(f"Debug Mode: {summary['environment']['debug']}")
        print(f"Log Level: {summary['environment']['log_level']}")
        print()

        print("Providers:")
        print(f"  ASR: {summary['providers']['asr']}")
        print(f"  TTS: {summary['providers']['tts']}")
        print(f"  LLM: {summary['providers']['llm']}")
        print()

        print("Models:")
        print(f"  Whisper: {summary['models']['whisper']}")
        print()

        print("Audio Settings:")
        print(f"  Sample Rate: {summary['audio']['sample_rate']} Hz")
        print(f"  Max Duration: {summary['audio']['max_duration']} seconds")
        print(f"  Temp Directory: {summary['audio']['temp_dir']}")
        print()

        print("Web Interface:")
        print(f"  Port: {summary['web']['port']}")
        print(f"  Server: {summary['web']['server_name']}")
        print(f"  Public Share: {summary['web']['share']}")
        print()

        print("API Keys Status:")
        api_status = summary["api_keys_configured"]
        print(
            f"  Alibaba DashScope (LLM/TTS): {'Configured' if api_status['alibaba'] else 'Missing'}"
        )
        print(f"  Alibaba ASR: {'Configured' if api_status['alibaba_asr'] else 'Missing'}")
        print(f"  OpenAI: {'✓ Configured' if api_status['openai'] else '✗ Missing'}")
        print()

        # Check for potential issues
        issues = []
        if summary["providers"]["asr"] == "alibaba" and not api_status["alibaba_asr"]:
            issues.append("Alibaba ASR selected but speech credentials are not configured")
        if summary["providers"]["tts"] == "alibaba" and not api_status["alibaba"]:
            issues.append("Alibaba TTS selected but DashScope API key is not configured")
        if summary["providers"]["llm"] == "alibaba" and not api_status["alibaba"]:
            issues.append("Alibaba LLM selected but API keys not configured")
        if summary["providers"]["llm"] == "openai" and not api_status["openai"]:
            issues.append("OpenAI LLM selected but API key not configured")

        if issues:
            print("⚠️  Configuration Issues:")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print("✅ Configuration looks good!")

    except Exception as e:
        print(f"Error checking configuration: {e}")


def create_tts(settings: Optional[Settings] = None):
    """Create TTS instance based on settings."""
    if settings is None:
        settings = get_settings()
        
    if settings.tts_provider == "edge":
        from chatterpal.core.tts.edge import EdgeTTS

        return EdgeTTS(
            {
                "voice": settings.edge_tts_voice,
                "rate": settings.edge_tts_rate,
                "volume": settings.edge_tts_volume,
            }
        )
    elif settings.tts_provider == "alibaba":
        from chatterpal.core.tts.alibaba import AlibabaTTS
        
        return AlibabaTTS(
            {
                "api_key": settings.get_dashscope_api_key(),
                "voice": "cosyvoice-v1",  # 使用cosyvoice模型
                "voice_name": settings.alibaba_tts_voice,
                "volume": settings.alibaba_tts_volume,
                "speech_rate": settings.alibaba_tts_speech_rate,
                "pitch_rate": settings.alibaba_tts_pitch_rate,
            }
        )
    else:
        raise ValueError(f"Unsupported TTS provider: {settings.tts_provider}")


def create_asr(settings: Optional[Settings] = None):
    """Create ASR instance based on settings."""
    if settings is None:
        settings = get_settings()
        
    if settings.asr_provider == "whisper":
        from chatterpal.core.asr.whisper import WhisperASR
        
        return WhisperASR(
            {
                "model_size": settings.whisper_model,
                "device": settings.whisper_device,
            }
        )
    elif settings.asr_provider == "alibaba":
        from chatterpal.core.asr.aliyun import AliyunASR
        
        return AliyunASR(settings.get_aliyun_asr_config())
    else:
        raise ValueError(f"Unsupported ASR provider: {settings.asr_provider}")


def create_llm(settings: Optional[Settings] = None):
    """Create LLM instance based on settings."""
    if settings is None:
        settings = get_settings()
        
    if settings.llm_provider == "openai":
        from chatterpal.core.llm.openai import OpenAILLM
        
        return OpenAILLM(
            {
                "api_key": settings.openai_api_key,
                "model": settings.openai_model,
                "base_url": settings.openai_base_url or "https://api.openai.com/v1/chat/completions",
            }
        )
    elif settings.llm_provider == "alibaba":
        from chatterpal.core.llm.alibaba import AlibabaBailianLLM
        
        return AlibabaBailianLLM(
            {
                "api_key": settings.get_dashscope_api_key(),
                "model": settings.alibaba_model,
                "base_url": settings.alibaba_base_url,
                "max_tokens": settings.alibaba_max_tokens,
                "temperature": settings.alibaba_temperature,
                "top_p": settings.alibaba_top_p,
                "enable_search": settings.alibaba_enable_search,
            }
        )
    else:
        raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")


if __name__ == "__main__":
    # Print configuration status when run as script
    print_config_status()
