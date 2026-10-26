# -*- coding: utf-8 -*-
"""
Configuration management system for ChatterPal.

This module provides a unified configuration management system that supports
environment variables, configuration files, and type-safe configuration access.
"""

import re
from pathlib import Path
from typing import Optional, Union

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings with support for environment variables and .env files.

    Configuration is loaded in the following order (later sources override earlier ones):
    1. Default values
    2. .env file
    3. Environment variables
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Allow legacy/shared .env entries that have no application consumer.
    )

    # Legacy keys remain accepted; DashScope and ASR can use separate credentials.
    alibaba_api_key: str = Field(
        default="", description="Legacy DashScope API key or ASR access key ID"
    )
    alibaba_api_secret: str = Field(
        default="", description="Legacy ASR access key secret (not needed for LLM/TTS)"
    )
    aliyun_asr_app_key: str = Field(
        default="", description="Aliyun NLS application key"
    )
    aliyun_asr_token: str = Field(
        default="", description="Aliyun NLS token (optional with access keys)"
    )
    aliyun_asr_access_key_id: str = Field(
        default="", description="Aliyun NLS access key ID"
    )
    aliyun_asr_access_key_secret: str = Field(
        default="", description="Aliyun NLS access key secret"
    )
    openai_api_key: Optional[str] = Field(
        default=None, description="OpenAI API key (optional)"
    )
    openai_base_url: Optional[str] = Field(
        default=None, description="OpenAI full chat-completions endpoint URL (optional)"
    )
    openai_model: str = Field(default="gpt-3.5-turbo", description="OpenAI model name")
    openai_timeout: float = Field(
        default=30,
        gt=0,
        le=300,
        description="OpenAI-compatible HTTP timeout in seconds",
    )
    alibaba_model: str = Field(default="qwen-plus", description="Alibaba model name")

    # Additional Alibaba Configuration
    dashscope_api_key: Optional[str] = Field(
        default=None,
        description="DashScope API key for LLM/TTS; takes precedence over the legacy key",
    )
    alibaba_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="Alibaba OpenAI-compatible API base URL",
    )
    alibaba_max_tokens: int = Field(
        default=2000, gt=0, description="Alibaba max tokens"
    )
    alibaba_temperature: float = Field(default=0.7, description="Alibaba temperature")
    alibaba_top_p: float = Field(default=0.8, description="Alibaba top_p")
    alibaba_enable_search: bool = Field(
        default=False, description="Alibaba enable search"
    )

    # Audio Configuration
    audio_sample_rate: int = Field(default=16000, description="Audio sample rate in Hz")
    audio_temp_dir: str = Field(
        default="temp_audio", description="Directory for temporary audio files"
    )
    audio_max_duration: int = Field(
        default=300, description="Maximum audio duration in seconds"
    )

    # Model Configuration
    whisper_model: str = Field(
        default="base",
        description="Whisper model size (tiny, base, small, medium, large)",
    )
    whisper_device: str = Field(
        default="auto", description="Whisper device (auto, cpu, cuda, cuda:N)"
    )
    whisper_language: str = Field(
        default="en", description="Whisper language (en, zh, auto for detection)"
    )
    whisper_max_cached_models: int = Field(
        default=1, ge=1, le=4, description="Maximum cached Whisper models per adapter"
    )
    asr_provider: str = Field(
        default="whisper", description="ASR provider (whisper, alibaba)"
    )
    tts_provider: str = Field(
        default="edge", description="TTS provider (edge, alibaba)"
    )
    llm_provider: str = Field(
        default="alibaba", description="LLM provider (alibaba, openai)"
    )

    # Chat Configuration
    system_prompt: Optional[str] = Field(
        default=None,
        description="Custom system prompt for the AI conversation coach. If not set, uses default professional coach prompt.",
    )
    max_history_length: int = Field(
        default=20,
        ge=3,
        description="Maximum retained conversation messages, including the system prompt and complete turns",
    )
    session_timeout: int = Field(
        default=3600,
        gt=0,
        description="Chat session timeout in seconds (default: 1 hour)",
    )
    max_sessions: int = Field(default=1000, ge=1, le=100000)
    api_max_pending: int = Field(default=8, ge=1, le=64)
    api_shutdown_timeout: float = Field(default=10, ge=0, le=60)
    generated_audio_ttl: float = Field(default=3600, gt=0, le=604800)
    generated_audio_max_files: int = Field(default=256, ge=1, le=100000)
    generated_audio_max_bytes: int = Field(default=64 * 1024 * 1024, ge=1024)

    # TTS Configuration
    edge_tts_voice: str = Field(
        default="en-US-JennyNeural", description="Edge TTS voice"
    )
    edge_tts_rate: str = Field(default="+0%", description="Edge TTS speech rate")
    edge_tts_volume: str = Field(default="+0%", description="Edge TTS volume")

    # Alibaba TTS Configuration
    alibaba_tts_voice: str = Field(
        default="longxiaochun",
        description="Alibaba TTS voice (longxiaochun for cosyvoice-v1)",
    )
    alibaba_tts_volume: int = Field(
        default=50, ge=0, le=100, description="Alibaba TTS volume (0-100)"
    )
    alibaba_tts_speech_rate: float = Field(
        default=1.0,
        ge=0.5,
        le=2.0,
        description="CosyVoice speech rate multiplier (0.5-2.0)",
    )
    alibaba_tts_pitch_rate: float = Field(
        default=1.0, ge=0.5, le=2.0, description="CosyVoice pitch multiplier (0.5-2.0)"
    )

    # Web Interface Configuration
    gradio_share: bool = Field(
        default=False, description="Enable Gradio public sharing"
    )
    gradio_port: int = Field(default=7860, description="Gradio server port")
    gradio_server_name: str = Field(
        default="127.0.0.1", description="Gradio server host"
    )

    # Learning API and its single-instance SQLite runtime.
    api_host: str = Field(default="127.0.0.1", min_length=1)
    api_port: int = Field(default=8010, ge=1024, le=65535)
    learning_enabled: bool = Field(default=True)
    learning_db_path: str = Field(default="data/learning/learning.sqlite3")
    learning_cookie_secure: bool = Field(default=False)
    learning_max_audio_bytes: int = Field(
        default=16 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024
    )
    learning_audio_retention_days: int = Field(default=7, ge=1, le=30)
    learning_max_workers: int = Field(default=2, ge=1, le=8)
    learning_max_pending: int = Field(default=8, ge=1, le=64)
    learning_operation_timeout: float = Field(default=120, gt=0, le=1800)
    learning_shutdown_timeout: float = Field(default=10, ge=0, le=60)
    learning_allowed_origins: str = Field(
        default="http://localhost:3010,http://127.0.0.1:3010"
    )

    # Logging Configuration
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    log_file: Optional[str] = Field(
        default=None, description="Log file path (optional)"
    )

    # Development Configuration
    debug: bool = Field(default=False, description="Enable debug mode")
    environment: str = Field(
        default="production", description="Environment (development, production)"
    )

    # Cache Configuration
    cache_dir: str = Field(default=".cache", description="Cache directory")
    model_cache_dir: str = Field(
        default="data/models", description="Model cache directory"
    )

    # Security Configuration
    secret_key: str = Field(
        default="change-me-in-production",
        description="Secret key for security features",
    )
    allowed_hosts: str = Field(
        default="localhost,127.0.0.1",
        description="Comma-separated list of allowed hosts",
    )

    @model_validator(mode="after")
    def validate_learning_limits(self):
        if self.learning_max_workers > self.learning_max_pending:
            raise ValueError("Learning max_pending must be at least max_workers")
        return self

    @field_validator("whisper_model")
    @classmethod
    def validate_whisper_model(cls, v):
        """Validate Whisper model size."""
        valid_models = [
            "tiny",
            "base",
            "small",
            "medium",
            "large",
            "large-v2",
            "large-v3",
        ]
        if v not in valid_models:
            raise ValueError(
                f"Invalid Whisper model: {v}. Must be one of {valid_models}"
            )
        return v

    @field_validator("whisper_device")
    @classmethod
    def validate_whisper_device(cls, v: str) -> str:
        """Validate supported devices without requiring accelerator hardware."""
        if v == "mps":
            raise ValueError(
                "Whisper device 'mps' is unsupported by the current Whisper/PyTorch "
                "runtime; use cpu or auto"
            )
        if v not in {"auto", "cpu", "cuda"} and not re.fullmatch(
            r"cuda:(0|[1-9][0-9]*)", v
        ):
            raise ValueError(
                "Whisper device must be one of: auto, cpu, cuda, cuda:N "
                "(N must be a non-negative integer without leading zeros)"
            )
        return v

    @field_validator("whisper_language")
    @classmethod
    def validate_whisper_language(cls, v: str) -> str:
        """Keep the application language policy explicit."""
        if v not in {"en", "zh", "auto"}:
            raise ValueError("Whisper language must be one of: en, zh, auto")
        return v

    @field_validator("whisper_max_cached_models", mode="before")
    @classmethod
    def validate_whisper_max_cached_models(cls, v) -> int:
        """Accept integer environment values without coercing floats or booleans."""
        if isinstance(v, str) and re.fullmatch(r"[0-9]+", v):
            return int(v)
        if type(v) is not int:
            raise ValueError("Whisper max_cached_models must be an integer from 1 to 4")
        return v

    @field_validator("asr_provider")
    @classmethod
    def validate_asr_provider(cls, v):
        """Validate ASR provider."""
        valid_providers = ["whisper", "alibaba"]
        if v not in valid_providers:
            raise ValueError(
                f"Invalid ASR provider: {v}. Must be one of {valid_providers}"
            )
        return v

    @field_validator("tts_provider")
    @classmethod
    def validate_tts_provider(cls, v):
        """Validate TTS provider."""
        valid_providers = ["edge", "alibaba"]
        if v not in valid_providers:
            raise ValueError(
                f"Invalid TTS provider: {v}. Must be one of {valid_providers}"
            )
        return v

    @field_validator("llm_provider")
    @classmethod
    def validate_llm_provider(cls, v):
        """Validate LLM provider."""
        valid_providers = ["alibaba", "openai"]
        if v not in valid_providers:
            raise ValueError(
                f"Invalid LLM provider: {v}. Must be one of {valid_providers}"
            )
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v):
        """Validate log level."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v = v.upper()
        if v not in valid_levels:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return v

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v):
        """Validate environment."""
        valid_environments = ["development", "production"]
        if v not in valid_environments:
            raise ValueError(
                f"Invalid environment: {v}. Must be one of {valid_environments}"
            )
        return v

    @field_validator("gradio_port")
    @classmethod
    def validate_gradio_port(cls, v):
        """Validate Gradio port."""
        if not (1024 <= v <= 65535):
            raise ValueError(f"Invalid port: {v}. Must be between 1024 and 65535")
        return v

    @field_validator("audio_sample_rate")
    @classmethod
    def validate_audio_sample_rate(cls, v):
        """Validate audio sample rate."""
        valid_rates = [8000, 16000, 22050, 44100, 48000]
        if v not in valid_rates:
            raise ValueError(f"Invalid sample rate: {v}. Must be one of {valid_rates}")
        return v

    @field_validator("audio_max_duration")
    @classmethod
    def validate_audio_max_duration(cls, v):
        """Validate audio max duration."""
        if v <= 0 or v > 3600:  # Max 1 hour
            raise ValueError(
                f"Invalid max duration: {v}. Must be between 1 and 3600 seconds"
            )
        return v

    def get_allowed_hosts_list(self) -> list[str]:
        """Get allowed hosts as a list."""
        return [host.strip() for host in self.allowed_hosts.split(",")]

    def get_audio_temp_path(self) -> Path:
        """Get audio temp directory as Path object."""
        return Path(self.audio_temp_dir)

    def get_cache_path(self) -> Path:
        """Get cache directory as Path object."""
        return Path(self.cache_dir)

    def get_model_cache_path(self) -> Path:
        """Get model cache directory as Path object."""
        return Path(self.model_cache_dir)

    def get_log_file_path(self) -> Optional[Path]:
        """Get log file path as Path object."""
        return Path(self.log_file) if self.log_file else None

    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.environment == "development"

    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.environment == "production"

    def ensure_directories(self) -> None:
        """Ensure all required directories exist."""
        directories = [
            self.get_audio_temp_path(),
            self.get_cache_path(),
            self.get_model_cache_path(),
        ]

        # Add log directory if log file is specified
        if self.log_file:
            log_path = self.get_log_file_path()
            if log_path:
                directories.append(log_path.parent)

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def get_dashscope_api_key(self) -> str:
        """Resolve the current DashScope key, with support for the legacy name."""
        return self.dashscope_api_key or self.alibaba_api_key

    def get_aliyun_asr_config(self) -> dict[str, str]:
        """Use the names accepted by AliyunASR and keep legacy access keys working."""
        return {
            "appkey": self.aliyun_asr_app_key,
            "token": self.aliyun_asr_token,
            "access_key_id": self.aliyun_asr_access_key_id or self.alibaba_api_key,
            "access_key_secret": self.aliyun_asr_access_key_secret
            or self.alibaba_api_secret,
        }

    def has_aliyun_asr_credentials(self) -> bool:
        config = self.get_aliyun_asr_config()
        return bool(
            config["appkey"]
            and (
                config["token"]
                or (config["access_key_id"] and config["access_key_secret"])
            )
        )

    def get_configuration_errors(self) -> list[str]:
        """Report only credentials needed by the selected providers."""
        errors = []
        if self.asr_provider == "alibaba" and not self.has_aliyun_asr_credentials():
            errors.append(
                "Alibaba ASR requires ALIYUN_ASR_APP_KEY and either ALIYUN_ASR_TOKEN "
                "or ALIYUN_ASR_ACCESS_KEY_ID + ALIYUN_ASR_ACCESS_KEY_SECRET "
                "(legacy ALIBABA_API_KEY + ALIBABA_API_SECRET are also accepted)."
            )
        if (
            self.llm_provider == "alibaba" or self.tts_provider == "alibaba"
        ) and not self.get_dashscope_api_key():
            errors.append(
                "Alibaba LLM/TTS requires DASHSCOPE_API_KEY "
                "(legacy ALIBABA_API_KEY is also accepted); no ASR secret is required."
            )
        if self.llm_provider == "openai" and not self.openai_api_key:
            errors.append("OpenAI LLM requires OPENAI_API_KEY.")
        return errors

    def validate_config(self) -> bool:
        """
        Validate configuration settings.

        Returns:
            True if configuration is valid, False otherwise
        """
        try:
            errors = self.get_configuration_errors()
            if errors:
                for error in errors:
                    print(error)
                return False

            # Ensure directories exist
            self.ensure_directories()

            return True

        except Exception as e:
            print(f"❌ Configuration validation failed: {e}")
            return False


# Global settings instance
_settings: Optional[Settings] = None


def get_settings(reload: bool = False) -> Settings:
    """
    Get the global settings instance.

    Args:
        reload: If True, reload settings from environment/files

    Returns:
        Settings instance
    """
    global _settings

    if _settings is None or reload:
        # Let pydantic-settings merge dotenv below the process environment.
        # Never copy dotenv values into os.environ: reloads must see edits/removals.
        env_file = Path.cwd() / ".env"
        if not env_file.is_file():
            project_root = Path(__file__).resolve().parents[3]
            env_file = project_root / ".env"

        _settings = Settings(_env_file=env_file)
        _settings.ensure_directories()

    return _settings


def reload_settings() -> Settings:
    """
    Reload settings from environment variables and files.

    Returns:
        Reloaded Settings instance
    """
    return get_settings(reload=True)


# Convenience function for common use cases
def get_api_config() -> dict[str, str]:
    """Get API configuration as a dictionary."""
    settings = get_settings()
    return {
        "alibaba_api_key": settings.alibaba_api_key,
        "alibaba_api_secret": settings.alibaba_api_secret,
        "dashscope_api_key": settings.get_dashscope_api_key(),
        "openai_api_key": settings.openai_api_key or "",
    }


def get_audio_config() -> dict[str, Union[int, str, Path]]:
    """Get audio configuration as a dictionary."""
    settings = get_settings()
    return {
        "sample_rate": settings.audio_sample_rate,
        "temp_dir": settings.get_audio_temp_path(),
        "max_duration": settings.audio_max_duration,
    }


def get_model_config() -> dict[str, Union[int, str]]:
    """Get model configuration, including the configured Whisper runtime policy."""
    settings = get_settings()
    return {
        "whisper_model": settings.whisper_model,
        "whisper_device": settings.whisper_device,
        "whisper_language": settings.whisper_language,
        "whisper_max_cached_models": settings.whisper_max_cached_models,
        "asr_provider": settings.asr_provider,
        "tts_provider": settings.tts_provider,
        "llm_provider": settings.llm_provider,
        "cache_dir": str(settings.get_model_cache_path()),
    }


def get_web_config() -> dict[str, Union[bool, int, str]]:
    """Get web interface configuration as a dictionary."""
    settings = get_settings()
    return {
        "share": settings.gradio_share,
        "port": settings.gradio_port,
        "server_name": settings.gradio_server_name,
        "debug": settings.debug,
    }
