"""Regression tests for the shipped environment configuration contract."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import chatterpal.config.settings as settings_module
from chatterpal.config import (
    ConfigurationError,
    Settings,
    check_required_config,
    get_settings,
    reload_settings,
    validate_api_keys,
)
from chatterpal.config.loader import create_asr, create_llm, create_tts


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Keep user environment, local .env files and singleton state out of tests."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        settings_module, "__file__", str(tmp_path / "src/chatterpal/config/settings.py")
    )
    monkeypatch.setattr(settings_module, "_settings", None)
    with patch.dict(os.environ, {}, clear=True):
        yield tmp_path


def test_copied_environment_template_loads_real_settings(tmp_path, monkeypatch):
    """Copying the documented template must be accepted by the application."""
    env_file = tmp_path / ".env"
    env_file.write_bytes((PROJECT_ROOT / ".env.example").read_bytes())
    monkeypatch.chdir(tmp_path)

    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(_env_file=env_file)

    assert settings.asr_provider == "whisper"
    assert settings.tts_provider == "edge"
    assert settings.llm_provider == "alibaba"
    assert settings.audio_sample_rate == 16000


def test_template_only_advertises_declared_environment_fields():
    from dotenv import dotenv_values

    keys = dotenv_values(PROJECT_ROOT / ".env.example")
    assert all(key.lower() in Settings.model_fields for key in keys)


def test_old_shared_env_fields_do_not_break_loading(isolated_config):
    (isolated_config / ".env").write_text(
        "CHAT_MAX_HISTORY_LENGTH=50\nTOPIC_DEFAULT_DIFFICULTY=intermediate\n"
        "TTS_CACHE_SIZE=1000\nXDG_CACHE_HOME=/tmp/cache\n"
    )
    assert get_settings().whisper_model == "base"


def test_environment_overrides_dotenv_without_mutating_environment(
    isolated_config, monkeypatch
):
    env_file = isolated_config / ".env"
    env_file.write_text("ALIBABA_MODEL=file-model\nDASHSCOPE_API_KEY=file-key\n")
    monkeypatch.setenv("ALIBABA_MODEL", "environment-model")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "environment-key")

    settings = get_settings()
    assert settings.alibaba_model == "environment-model"
    assert settings.dashscope_api_key == "environment-key"
    assert os.environ["ALIBABA_MODEL"] == "environment-model"
    assert os.environ["DASHSCOPE_API_KEY"] == "environment-key"


def test_reload_reads_file_edits_without_stale_dotenv_environment(isolated_config):
    env_file = isolated_config / ".env"
    env_file.write_text("ALIBABA_MODEL=first-model\n")
    assert get_settings().alibaba_model == "first-model"
    assert "ALIBABA_MODEL" not in os.environ

    env_file.write_text("ALIBABA_MODEL=second-model\n")
    assert reload_settings().alibaba_model == "second-model"
    assert "ALIBABA_MODEL" not in os.environ


def test_current_directory_dotenv_takes_precedence_over_checkout(
    isolated_config, monkeypatch
):
    checkout = isolated_config / "checkout"
    checkout.mkdir()
    (checkout / ".env").write_text("ALIBABA_MODEL=checkout-model\n")
    monkeypatch.setattr(
        settings_module, "__file__", str(checkout / "src/chatterpal/config/settings.py")
    )
    (isolated_config / ".env").write_text("ALIBABA_MODEL=current-model\n")
    assert get_settings().alibaba_model == "current-model"


def test_checkout_dotenv_is_used_when_current_directory_has_none(
    isolated_config, monkeypatch
):
    checkout = isolated_config / "checkout"
    checkout.mkdir()
    (checkout / ".env").write_text("ALIBABA_MODEL=checkout-model\n")
    monkeypatch.setattr(
        settings_module, "__file__", str(checkout / "src/chatterpal/config/settings.py")
    )
    assert get_settings().alibaba_model == "checkout-model"
    assert "ALIBABA_MODEL" not in os.environ


def test_dashscope_only_credentials_pass_all_real_validation_paths(
    isolated_config, monkeypatch
):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-dashscope-key")
    settings = get_settings()
    assert settings.alibaba_api_secret == ""
    assert settings.validate_config() is True
    check_required_config()
    assert validate_api_keys()["alibaba"] is True


@pytest.mark.parametrize("provider_field", ["llm_provider", "tts_provider"])
def test_missing_dashscope_credentials_fail_with_actionable_message(
    isolated_config, provider_field
):
    settings = Settings(_env_file=None, **{provider_field: "alibaba"})
    with patch.object(settings_module, "_settings", settings):
        with pytest.raises(ConfigurationError, match="DASHSCOPE_API_KEY"):
            check_required_config()
    assert settings.validate_config() is False


def test_asr_still_requires_speech_credentials(isolated_config, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-dashscope-key")
    monkeypatch.setenv("ASR_PROVIDER", "alibaba")
    with pytest.raises(ConfigurationError, match="ALIYUN_ASR_APP_KEY"):
        check_required_config()
    assert get_settings().validate_config() is False


@pytest.mark.parametrize(
    "speech_credentials",
    [
        {"ALIYUN_ASR_TOKEN": "test-token"},
        {
            "ALIYUN_ASR_ACCESS_KEY_ID": "test-access-key",
            "ALIYUN_ASR_ACCESS_KEY_SECRET": "test-access-secret",
        },
        {"ALIBABA_API_KEY": "legacy-key", "ALIBABA_API_SECRET": "legacy-secret"},
    ],
)
def test_asr_credential_modes_pass_validation(
    isolated_config, monkeypatch, speech_credentials
):
    for key, value in {
        "ASR_PROVIDER": "alibaba",
        "ALIYUN_ASR_APP_KEY": "test-app-key",
        "DASHSCOPE_API_KEY": "test-dashscope-key",
        **speech_credentials,
    }.items():
        monkeypatch.setenv(key, value)
    check_required_config()
    assert get_settings().validate_config() is True


def test_legacy_alibaba_key_still_works_without_secret_for_llm(
    isolated_config, monkeypatch
):
    monkeypatch.setenv("ALIBABA_API_KEY", "legacy-dashscope-key")
    check_required_config()
    assert get_settings().validate_config() is True
    assert validate_api_keys()["alibaba"] is True


@pytest.mark.parametrize("field", ["alibaba_tts_speech_rate", "alibaba_tts_pitch_rate"])
def test_cosyvoice_rates_accept_fractional_multipliers(field):
    settings = Settings(_env_file=None, **{field: 1.25})
    assert getattr(settings, field) == 1.25
    assert getattr(Settings(_env_file=None), field) == 1.0
    for invalid in (0, 0.49, 2.01):
        with pytest.raises(ValueError):
            Settings(_env_file=None, **{field: invalid})


def test_openai_timeout_flows_from_environment_to_http_request(
    isolated_config, monkeypatch
):
    import chatterpal.core.llm.openai as openai_module

    monkeypatch.setenv("OPENAI_TIMEOUT", "60")
    settings = Settings(
        _env_file=None, llm_provider="openai", openai_api_key="offline-test-key"
    )
    assert settings.openai_timeout == 60
    llm = create_llm(settings)
    response = patch.object(openai_module.requests, "post")
    with response as post:
        post.return_value.json.return_value = {
            "choices": [{"message": {"content": "reply"}}]
        }
        assert llm.chat("hello") == "reply"
        assert post.call_args.kwargs["timeout"] == 60
    monkeypatch.delenv("OPENAI_TIMEOUT")
    assert Settings(_env_file=None).openai_timeout == 30
    for invalid in (0, -1, 301):
        with pytest.raises(ValueError):
            Settings(_env_file=None, openai_timeout=invalid)


def test_real_alibaba_llm_receives_all_documented_options(isolated_config, monkeypatch):
    import chatterpal.core.llm.alibaba as alibaba_module

    (isolated_config / ".env").write_text(
        "DASHSCOPE_API_KEY=test-dashscope-key\nALIBABA_API_KEY=legacy-key\n"
        "ALIBABA_MODEL=qwen-custom\nALIBABA_BASE_URL=https://example.test/v1\n"
        "ALIBABA_MAX_TOKENS=345\nALIBABA_TEMPERATURE=0.4\n"
        "ALIBABA_TOP_P=0.6\nALIBABA_ENABLE_SEARCH=true\n"
    )
    with patch.object(alibaba_module, "OpenAI") as client:
        llm = create_llm(get_settings())

    assert llm.api_key == "test-dashscope-key"
    assert llm.model == "qwen-custom"
    assert llm.max_tokens == 345
    assert llm.temperature == 0.4
    assert llm.top_p == 0.6
    assert llm.enable_search is True
    client.assert_called_once_with(
        api_key="test-dashscope-key", base_url="https://example.test/v1", timeout=60
    )


def test_real_alibaba_llm_defaults_match_template(isolated_config):
    import chatterpal.core.llm.alibaba as alibaba_module

    with patch.object(alibaba_module, "OpenAI"):
        llm = create_llm(
            Settings(_env_file=None, dashscope_api_key="test-dashscope-key")
        )
    assert llm.model == "qwen-plus"
    assert llm.max_tokens == 2000
    assert llm.temperature == 0.7
    assert llm.top_p == 0.8
    assert llm.enable_search is False


def test_tts_factory_accepts_dashscope_key_and_fractional_rates(isolated_config):
    import chatterpal.core.tts.alibaba as alibaba_module

    with (
        patch.object(alibaba_module, "SpeechSynthesizer"),
        patch.object(alibaba_module, "dashscope"),
    ):
        tts = create_tts(
            Settings(
                _env_file=None,
                tts_provider="alibaba",
                dashscope_api_key="test-dashscope-key",
                alibaba_tts_speech_rate=1.25,
                alibaba_tts_pitch_rate=0.75,
            )
        )
    assert tts.api_key == "test-dashscope-key"
    assert tts.tts_config.speech_rate == 1.25
    assert tts.tts_config.pitch_rate == 0.75


@pytest.mark.parametrize(
    "configured_url, expected_url",
    [
        (None, "https://api.openai.com/v1/chat/completions"),
        (
            "https://example.test/v1/chat/completions",
            "https://example.test/v1/chat/completions",
        ),
    ],
)
def test_openai_factory_uses_complete_chat_endpoint(
    isolated_config, configured_url, expected_url
):
    llm = create_llm(
        Settings(
            _env_file=None,
            llm_provider="openai",
            openai_api_key="test-openai-key",
            openai_base_url=configured_url,
        )
    )
    assert llm.base_url == expected_url


def test_asr_factory_passes_actual_consumer_credential_names(isolated_config):
    import chatterpal.core.asr.aliyun as aliyun_module

    with patch.object(aliyun_module, "nls") as nls:
        asr = create_asr(
            Settings(
                _env_file=None,
                asr_provider="alibaba",
                aliyun_asr_app_key="test-app-key",
                aliyun_asr_token="test-token",
                alibaba_api_key="legacy-id",
                alibaba_api_secret="legacy-secret",
            )
        )
    assert asr.appkey == "test-app-key"
    assert asr.token == "test-token"
    assert asr.access_key_id == "legacy-id"
    assert asr.access_key_secret == "legacy-secret"
    nls.token.getToken.assert_not_called()


def test_advanced_chat_configuration_uses_json_not_environment(
    isolated_config, monkeypatch
):
    from chatterpal.services import chat_config as chat_config_module

    monkeypatch.setenv("CHAT_MAX_HISTORY_LENGTH", "99")
    monkeypatch.setenv("TOPIC_DEFAULT_DIFFICULTY", "beginner")
    monkeypatch.setattr(chat_config_module, "get_preferences_manager", lambda: object())
    manager = chat_config_module.ChatConfigManager(
        str(isolated_config / "chat-settings")
    )
    assert manager.get_session_config().max_history_length == 50
    assert manager.get_topic_config().default_difficulty == "intermediate"

    assert manager.update_config(
        {
            "session": {"max_history_length": 30},
            "topic_generation": {"default_difficulty": "beginner"},
        }
    )
    stored = json.loads(manager.config_file.read_text())
    assert stored["session"]["max_history_length"] == 30
    reloaded = chat_config_module.ChatConfigManager(str(manager.config_dir))
    assert reloaded.get_session_config().max_history_length == 30
    assert reloaded.get_topic_config().default_difficulty == "beginner"


def test_configuration_checker_accepts_template_without_network(isolated_config):
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts/verify_deployment_config.py")],
        cwd=isolated_config,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "Settings loaded" in result.stdout


def test_configuration_checker_checks_selected_credentials_and_hides_values(
    isolated_config,
):
    env_file = isolated_config / ".env"
    env_file.write_text("LLM_PROVIDER=alibaba\n")
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts/verify_deployment_config.py"),
        "--env-file",
        str(env_file),
        "--require-credentials",
    ]
    result = subprocess.run(
        command, cwd=isolated_config, capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "DASHSCOPE_API_KEY" in result.stdout

    env_file.write_text("DASHSCOPE_API_KEY=test-private-placeholder\n")
    result = subprocess.run(
        command, cwd=isolated_config, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "test-private-placeholder" not in result.stdout + result.stderr

    env_file.write_text(
        "DASHSCOPE_API_KEY=test-private-placeholder\nALIBABA_MAX_TOKENS=test-invalid-private-placeholder\n"
    )
    result = subprocess.run(
        command, cwd=isolated_config, capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "alibaba_max_tokens" in result.stdout
    assert "test-invalid-private-placeholder" not in result.stdout + result.stderr
