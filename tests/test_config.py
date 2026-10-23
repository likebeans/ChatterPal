"""
Tests for configuration management system.
"""

import os
import tempfile
from pathlib import Path
import pytest
from unittest.mock import patch

from chatterpal.config import (
    Settings,
    get_settings,
    reload_settings,
    ConfigurationError,
    validate_api_keys,
    get_config_summary,
)


class TestSettings:
    """Test the Settings class."""
    
    def test_default_settings(self):
        """Test default settings values."""
        settings = Settings()
        
        assert settings.audio_sample_rate == 16000
        assert settings.whisper_model == "base"
        assert settings.asr_provider == "whisper"
        assert settings.tts_provider == "edge"
        assert settings.llm_provider == "alibaba"
        assert settings.gradio_port == 7860
        # In test environment, debug is True due to test fixture
        assert settings.debug is True
        assert settings.environment == "development"
    
    def test_environment_variable_override(self):
        """Test that environment variables override defaults."""
        with patch.dict(os.environ, {
            'AUDIO_SAMPLE_RATE': '22050',
            'WHISPER_MODEL': 'small',
            'DEBUG': 'true',
            'GRADIO_PORT': '8080'
        }):
            settings = Settings()
            
            assert settings.audio_sample_rate == 22050
            assert settings.whisper_model == "small"
            assert settings.debug is True
            assert settings.gradio_port == 8080
    
    def test_validation_whisper_model(self):
        """Test Whisper model validation."""
        # Valid model
        settings = Settings(whisper_model="large")
        assert settings.whisper_model == "large"
        
        # Invalid model should raise ValueError
        with pytest.raises(ValueError, match="Invalid Whisper model"):
            Settings(whisper_model="invalid")
    
    def test_validation_asr_provider(self):
        """Test ASR provider validation."""
        # Valid provider
        settings = Settings(asr_provider="alibaba")
        assert settings.asr_provider == "alibaba"
        
        # Invalid provider should raise ValueError
        with pytest.raises(ValueError, match="Invalid ASR provider"):
            Settings(asr_provider="invalid")
    
    def test_validation_port(self):
        """Test port validation."""
        # Valid port
        settings = Settings(gradio_port=8080)
        assert settings.gradio_port == 8080
        
        # Invalid port should raise ValueError
        with pytest.raises(ValueError, match="Invalid port"):
            Settings(gradio_port=80)  # Too low
        
        with pytest.raises(ValueError, match="Invalid port"):
            Settings(gradio_port=70000)  # Too high
    
    def test_path_methods(self):
        """Test path utility methods."""
        settings = Settings(
            audio_temp_dir="test_temp",
            cache_dir="test_cache",
            model_cache_dir="test_models"
        )
        
        assert settings.get_audio_temp_path() == Path("test_temp")
        assert settings.get_cache_path() == Path("test_cache")
        assert settings.get_model_cache_path() == Path("test_models")
    
    def test_allowed_hosts_list(self):
        """Test allowed hosts parsing."""
        settings = Settings(allowed_hosts="localhost,127.0.0.1,example.com")
        hosts = settings.get_allowed_hosts_list()
        
        assert hosts == ["localhost", "127.0.0.1", "example.com"]
    
    def test_environment_checks(self):
        """Test environment check methods."""
        dev_settings = Settings(environment="development")
        prod_settings = Settings(environment="production")
        
        assert dev_settings.is_development() is True
        assert dev_settings.is_production() is False
        
        assert prod_settings.is_development() is False
        assert prod_settings.is_production() is True


class TestConfigurationFunctions:
    """Test configuration utility functions."""
    
    def test_get_settings_singleton(self):
        """Test that get_settings returns the same instance."""
        settings1 = get_settings()
        settings2 = get_settings()
        
        assert settings1 is settings2
    
    def test_reload_settings(self):
        """Test settings reload functionality."""
        # Get initial settings
        settings1 = get_settings()
        
        # Reload settings
        with patch.dict(os.environ, {'DEBUG': 'true'}):
            settings2 = reload_settings()
        
        # Should be different instances
        assert settings1 is not settings2
        assert settings2.debug is True
    
    def test_validate_api_keys(self):
        """Test API key validation."""
        with patch.dict(os.environ, {
            'ALIBABA_API_KEY': 'test_key',
            'ALIBABA_API_SECRET': 'test_secret',
            'OPENAI_API_KEY': 'test_openai_key'
        }):
            # Force reload to pick up new env vars
            reload_settings()
            
            validation = validate_api_keys()
            
            assert validation['alibaba'] is True
            assert validation['openai'] is True
    
    def test_validate_api_keys_missing(self, monkeypatch, tmp_path):
        """Test API key validation with missing keys and isolated default directories."""
        monkeypatch.chdir(tmp_path)
        with patch.dict(os.environ, {}, clear=True):
            # Force reload to clear any existing keys
            reload_settings()
            
            validation = validate_api_keys()
            
            assert validation['alibaba'] is False
            assert validation['openai'] is False
    
    def test_get_config_summary(self):
        """Test configuration summary generation."""
        summary = get_config_summary()
        
        # Check that all expected sections are present
        assert 'providers' in summary
        assert 'models' in summary
        assert 'audio' in summary
        assert 'web' in summary
        assert 'environment' in summary
        assert 'api_keys_configured' in summary
        
        # Check provider information
        assert 'asr' in summary['providers']
        assert 'tts' in summary['providers']
        assert 'llm' in summary['providers']


class TestConfigurationIntegration:
    """Integration tests for configuration system."""
    
    def test_env_file_loading(self):
        """Test loading configuration from .env file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            
            # Create a test .env file
            env_content = """
AUDIO_SAMPLE_RATE=22050
WHISPER_MODEL=small
DEBUG=true
GRADIO_PORT=8080
ALIBABA_API_KEY=test_key
ALIBABA_API_SECRET=test_secret
"""
            env_file.write_text(env_content.strip())
            
            # Change to temp directory and reload settings
            original_cwd = os.getcwd()
            try:
                os.chdir(temp_dir)
                settings = reload_settings()
                
                assert settings.audio_sample_rate == 22050
                assert settings.whisper_model == "small"
                assert settings.debug is True
                assert settings.gradio_port == 8080
                assert settings.alibaba_api_key == "test_key"
                assert settings.alibaba_api_secret == "test_secret"
                
            finally:
                os.chdir(original_cwd)
    
    def test_directory_creation(self):
        """Test that ensure_directories creates required directories."""
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(
                audio_temp_dir=f"{temp_dir}/audio",
                cache_dir=f"{temp_dir}/cache",
                model_cache_dir=f"{temp_dir}/models"
            )
            
            # Directories should not exist yet
            assert not settings.get_audio_temp_path().exists()
            assert not settings.get_cache_path().exists()
            assert not settings.get_model_cache_path().exists()
            
            # Call ensure_directories
            settings.ensure_directories()
            
            # Directories should now exist
            assert settings.get_audio_temp_path().exists()
            assert settings.get_cache_path().exists()
            assert settings.get_model_cache_path().exists()


if __name__ == "__main__":
    pytest.main([__file__])








