"""Offline regressions for ASR settings and their real adapter wiring."""

import os
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from dotenv import dotenv_values

import chatterpal.config.settings as settings_module
from chatterpal.config import (
    Settings,
    create_default_config_file,
    get_config_summary,
    get_model_config,
    get_settings,
    load_config_file,
    print_config_status,
    reload_settings,
)
from chatterpal.config.loader import create_asr


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_asr_configuration(tmp_path, monkeypatch):
    """Use real settings sources without loading the developer's environment."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    for key in tuple(os.environ):
        if key.lower() in Settings.model_fields or key.lower().startswith("whisper_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("AUDIO_TEMP_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MODEL_CACHE_DIR", str(tmp_path / "models"))
    monkeypatch.setattr(settings_module, "_settings", None)


def test_default_settings_create_an_english_asr_with_bounded_cache():
    settings = Settings(_env_file=None)
    adapter = create_asr(settings)

    assert settings.whisper_language == "en"
    assert settings.whisper_max_cached_models == 1
    assert adapter.config == {
        "model_size": "base",
        "device": "auto",
        "language": "en",
        "max_cached_models": 1,
    }


@pytest.mark.parametrize("language", ["en", "zh", "auto"])
def test_language_environment_setting_reaches_the_real_adapter(monkeypatch, language):
    monkeypatch.setenv("WHISPER_LANGUAGE", language)
    settings = Settings(_env_file=None)

    assert settings.whisper_language == language
    assert create_asr(settings).config["language"] == language


@pytest.mark.parametrize("language", ["", " ", "fr", "english", "chinese", "automatic"])
def test_settings_reject_unsupported_language_with_choices(monkeypatch, language):
    monkeypatch.setenv("WHISPER_LANGUAGE", language)

    with pytest.raises(ValueError, match=r"whisper_language[\s\S]*en.*zh.*auto"):
        Settings(_env_file=None)


@pytest.mark.parametrize("device", ["auto", "cpu", "cuda", "cuda:0", "cuda:2"])
def test_explicit_device_setting_is_preserved_for_the_real_adapter(monkeypatch, device):
    monkeypatch.setenv("WHISPER_DEVICE", device)
    settings = Settings(_env_file=None)

    assert settings.whisper_device == device
    assert create_asr(settings).config["device"] == device


@pytest.mark.parametrize(
    "device",
    [
        "",
        " ",
        "automatic",
        "gpu",
        "CPU",
        "cpu:0",
        "cuda:",
        "cuda:-1",
        "cuda:x",
        "cuda:01",
        "mps:0",
    ],
)
def test_settings_reject_unusable_devices_with_choices(monkeypatch, device):
    monkeypatch.setenv("WHISPER_DEVICE", device)

    with pytest.raises(ValueError, match=r"whisper_device[\s\S]*auto.*cpu.*cuda"):
        Settings(_env_file=None)


def test_mps_configuration_explains_the_supported_fallback(monkeypatch):
    monkeypatch.setenv("WHISPER_DEVICE", "mps")

    with pytest.raises(ValueError, match=r"whisper_device[\s\S]*mps[\s\S]*cpu.*auto"):
        Settings(_env_file=None)


@pytest.mark.parametrize("capacity", ["1.0", "2.5", "3e0", "true", "", " ", "many"])
def test_settings_reject_non_integer_cache_capacity_from_environment(
    monkeypatch, capacity
):
    monkeypatch.setenv("WHISPER_MAX_CACHED_MODELS", capacity)

    with pytest.raises(ValueError, match="whisper_max_cached_models"):
        Settings(_env_file=None)


@pytest.mark.parametrize("capacity", [0, 5, -1, 1.0, 2.5, True, False, None])
def test_settings_reject_invalid_cache_capacity_without_coercion(capacity):
    with pytest.raises(ValueError, match="whisper_max_cached_models"):
        Settings(_env_file=None, whisper_max_cached_models=capacity)


@pytest.mark.parametrize("capacity", ["0", "5", "-1", "100"])
def test_settings_reject_out_of_range_environment_cache_capacity(monkeypatch, capacity):
    monkeypatch.setenv("WHISPER_MAX_CACHED_MODELS", capacity)

    with pytest.raises(ValueError, match="whisper_max_cached_models"):
        Settings(_env_file=None)


@pytest.mark.parametrize("capacity", [1, 2, 3, 4])
def test_integer_environment_cache_capacity_reaches_the_real_adapter(
    monkeypatch, capacity
):
    monkeypatch.setenv("WHISPER_MAX_CACHED_MODELS", str(capacity))
    settings = Settings(_env_file=None)

    assert settings.whisper_max_cached_models == capacity
    assert type(settings.whisper_max_cached_models) is int
    assert create_asr(settings).config["max_cached_models"] == capacity


def test_model_config_helper_reports_asr_runtime_policy_and_preserves_existing_keys(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("WHISPER_MODEL", "small")
    monkeypatch.setenv("WHISPER_DEVICE", "cuda:2")
    monkeypatch.setenv("WHISPER_LANGUAGE", "auto")
    monkeypatch.setenv("WHISPER_MAX_CACHED_MODELS", "4")

    assert get_model_config() == {
        "whisper_model": "small",
        "whisper_device": "cuda:2",
        "whisper_language": "auto",
        "whisper_max_cached_models": 4,
        "asr_provider": "whisper",
        "tts_provider": "edge",
        "llm_provider": "alibaba",
        "cache_dir": str(tmp_path / "models"),
    }


def test_configuration_summary_includes_runtime_policy_and_preserves_existing_keys(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("WHISPER_MODEL", "small")
    monkeypatch.setenv("WHISPER_DEVICE", "cuda:1")
    monkeypatch.setenv("WHISPER_LANGUAGE", "zh")
    monkeypatch.setenv("WHISPER_MAX_CACHED_MODELS", "3")

    assert get_config_summary() == {
        "providers": {"asr": "whisper", "tts": "edge", "llm": "alibaba"},
        "models": {
            "whisper": "small",
            "whisper_device": "cuda:1",
            "whisper_language": "zh",
            "whisper_max_cached_models": 3,
        },
        "audio": {
            "sample_rate": 16000,
            "max_duration": 300,
            "temp_dir": str(tmp_path / "audio"),
        },
        "web": {"port": 7860, "server_name": "127.0.0.1", "share": False},
        "environment": {
            "debug": False,
            "environment": "production",
            "log_level": "INFO",
        },
        "api_keys_configured": {
            "alibaba": False,
            "alibaba_asr": False,
            "openai": False,
        },
    }


@pytest.mark.parametrize("file_format", ["json", "yaml"])
def test_exported_configuration_preserves_runtime_policy_without_credentials(
    tmp_path, monkeypatch, file_format
):
    monkeypatch.setenv("WHISPER_DEVICE", "cpu")
    monkeypatch.setenv("WHISPER_LANGUAGE", "auto")
    monkeypatch.setenv("WHISPER_MAX_CACHED_MODELS", "2")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "private-placeholder-key")
    output_path = tmp_path / f"configuration.{file_format}"

    create_default_config_file(output_path, format=file_format)
    exported = load_config_file(output_path)

    assert exported["models"] == {
        "whisper": "base",
        "whisper_device": "cpu",
        "whisper_language": "auto",
        "whisper_max_cached_models": 2,
    }
    assert "api_keys_configured" not in exported
    assert "private-placeholder-key" not in output_path.read_text(encoding="utf-8")


def test_printed_configuration_status_reports_runtime_policy(monkeypatch, capsys):
    monkeypatch.setenv("WHISPER_DEVICE", "cuda:2")
    monkeypatch.setenv("WHISPER_LANGUAGE", "zh")
    monkeypatch.setenv("WHISPER_MAX_CACHED_MODELS", "4")

    print_config_status()
    output = capsys.readouterr().out

    assert "Whisper: base" in output
    assert "Whisper Device: cuda:2" in output
    assert "Whisper Language: zh" in output
    assert "Whisper Cached Models per Adapter: 4" in output


def test_copied_environment_template_declares_and_loads_english_runtime_defaults(
    tmp_path,
):
    env_file = tmp_path / ".env"
    env_file.write_bytes((PROJECT_ROOT / ".env.example").read_bytes())
    declared = dotenv_values(env_file)

    assert {
        key: declared[key]
        for key in (
            "ASR_PROVIDER",
            "WHISPER_MODEL",
            "WHISPER_DEVICE",
            "WHISPER_LANGUAGE",
            "WHISPER_MAX_CACHED_MODELS",
        )
    } == {
        "ASR_PROVIDER": "whisper",
        "WHISPER_MODEL": "base",
        "WHISPER_DEVICE": "auto",
        "WHISPER_LANGUAGE": "en",
        "WHISPER_MAX_CACHED_MODELS": "1",
    }
    assert all(key.lower() in Settings.model_fields for key in declared)
    settings = Settings(_env_file=env_file)
    assert create_asr(settings).config == {
        "model_size": "base",
        "device": "auto",
        "language": "en",
        "max_cached_models": 1,
    }


def test_process_environment_overrides_dotenv_asr_runtime_fields(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "WHISPER_MODEL=tiny\nWHISPER_DEVICE=cpu\n"
        "WHISPER_LANGUAGE=zh\nWHISPER_MAX_CACHED_MODELS=2\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("WHISPER_MODEL", "small")
    monkeypatch.setenv("WHISPER_DEVICE", "cuda:2")
    monkeypatch.setenv("WHISPER_LANGUAGE", "auto")
    monkeypatch.setenv("WHISPER_MAX_CACHED_MODELS", "4")

    settings = Settings(_env_file=env_file)

    assert create_asr(settings).config == {
        "model_size": "small",
        "device": "cuda:2",
        "language": "auto",
        "max_cached_models": 4,
    }


def test_reload_reads_asr_runtime_edits_and_restores_defaults_after_removal(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "WHISPER_MODEL=tiny\nWHISPER_DEVICE=cpu\n"
        "WHISPER_LANGUAGE=zh\nWHISPER_MAX_CACHED_MODELS=2\n",
        encoding="utf-8",
    )
    assert create_asr().config == {
        "model_size": "tiny",
        "device": "cpu",
        "language": "zh",
        "max_cached_models": 2,
    }

    env_file.write_text(
        "WHISPER_MODEL=small\nWHISPER_DEVICE=cpu\n"
        "WHISPER_LANGUAGE=auto\nWHISPER_MAX_CACHED_MODELS=4\n",
        encoding="utf-8",
    )
    assert create_asr(reload_settings()).config == {
        "model_size": "small",
        "device": "cpu",
        "language": "auto",
        "max_cached_models": 4,
    }

    env_file.write_text("", encoding="utf-8")
    assert create_asr(reload_settings()).config == {
        "model_size": "base",
        "device": "auto",
        "language": "en",
        "max_cached_models": 1,
    }
    assert all(
        key not in os.environ
        for key in (
            "WHISPER_MODEL",
            "WHISPER_DEVICE",
            "WHISPER_LANGUAGE",
            "WHISPER_MAX_CACHED_MODELS",
        )
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("WHISPER_LANGUAGE", "english"),
        ("WHISPER_DEVICE", "gpu"),
        ("WHISPER_DEVICE", "mps"),
        ("WHISPER_MAX_CACHED_MODELS", "1.0"),
        ("WHISPER_MAX_CACHED_MODELS", "2.5"),
        ("WHISPER_MAX_CACHED_MODELS", "0"),
        ("WHISPER_MAX_CACHED_MODELS", "5"),
    ],
)
def test_invalid_dotenv_asr_values_are_rejected(tmp_path, field, value):
    env_file = tmp_path / ".env"
    env_file.write_text(f"{field}={value}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=field.lower()):
        Settings(_env_file=env_file)


@pytest.fixture
def decoded_whisper_boundary(tmp_path, monkeypatch):
    """Keep settings, factory and WAV decoding real; replace only the model boundary."""
    import whisper

    audio_path = tmp_path / "configuration-audio.wav"
    with wave.open(str(audio_path), "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(16000)
        audio_file.writeframes(np.zeros(16000, dtype="<i2").tobytes())

    boundary = SimpleNamespace(audio_path=str(audio_path), loads=[], transcriptions=[])

    class Model:
        def __init__(self, model_size, device):
            self.model_size = model_size
            self.device = device

        def transcribe(self, waveform, **options):
            assert waveform.dtype == np.float32
            assert waveform.shape == (16000,)
            boundary.transcriptions.append((self.model_size, self.device, options))
            return {"text": f"Recognized with {self.model_size}"}

    def load_model(model_size, device=None, **options):
        boundary.loads.append((model_size, device))
        return Model(model_size, device)

    monkeypatch.setattr(whisper, "load_model", load_model)
    return boundary


def test_factory_defaults_reach_real_whisper_load_and_transcribe(
    decoded_whisper_boundary, monkeypatch
):
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    adapter = create_asr()
    assert decoded_whisper_boundary.loads == []

    assert (
        adapter.recognize_file(decoded_whisper_boundary.audio_path)
        == "Recognized with base"
    )
    assert (
        adapter.recognize_file(decoded_whisper_boundary.audio_path)
        == "Recognized with base"
    )

    assert decoded_whisper_boundary.loads == [("base", "cpu")]
    assert decoded_whisper_boundary.transcriptions == [
        ("base", "cpu", {"language": "english", "fp16": False}),
        ("base", "cpu", {"language": "english", "fp16": False}),
    ]


@pytest.mark.parametrize(
    ("language", "expected_language"),
    [("en", "english"), ("zh", "chinese"), ("auto", None)],
)
def test_language_from_dotenv_reaches_whisper_transcribe(
    tmp_path, decoded_whisper_boundary, language, expected_language
):
    (tmp_path / ".env").write_text(
        f"WHISPER_LANGUAGE={language}\nWHISPER_DEVICE=cpu\n",
        encoding="utf-8",
    )
    adapter = create_asr()

    assert (
        adapter.recognize_file(decoded_whisper_boundary.audio_path)
        == "Recognized with base"
    )
    assert decoded_whisper_boundary.transcriptions == [
        ("base", "cpu", {"language": expected_language, "fp16": False}),
    ]


@pytest.mark.parametrize(
    ("device", "cuda_available", "resolved_device", "fp16"),
    [
        ("auto", False, "cpu", False),
        ("auto", True, "cuda:1", True),
        ("cpu", True, "cpu", False),
        ("cuda", True, "cuda:1", True),
        ("cuda:2", False, "cuda:2", True),
    ],
)
def test_configured_device_reaches_whisper_loader_and_precision_policy(
    monkeypatch, decoded_whisper_boundary, device, cuda_available, resolved_device, fp16
):
    import torch

    monkeypatch.setenv("WHISPER_DEVICE", device)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: cuda_available)
    monkeypatch.setattr(torch.cuda, "current_device", lambda: 1)
    adapter = create_asr()

    assert (
        adapter.recognize_file(decoded_whisper_boundary.audio_path)
        == "Recognized with base"
    )
    assert decoded_whisper_boundary.loads == [("base", resolved_device)]
    assert decoded_whisper_boundary.transcriptions == [
        ("base", resolved_device, {"language": "english", "fp16": fp16}),
    ]
    assert get_settings().whisper_device == device


@pytest.mark.parametrize("capacity", [1, 2, 3, 4])
def test_environment_cache_capacity_controls_factory_created_adapter_reuse(
    monkeypatch, decoded_whisper_boundary, capacity
):
    monkeypatch.setenv("WHISPER_DEVICE", "cpu")
    monkeypatch.setenv("WHISPER_MAX_CACHED_MODELS", str(capacity))
    adapter = create_asr()
    models = ["tiny", "base", "small", "medium", "large"]

    for model_size in models[:capacity]:
        assert (
            adapter.recognize_file(
                decoded_whisper_boundary.audio_path, model_size=model_size
            )
            == f"Recognized with {model_size}"
        )
    assert (
        adapter.recognize_file(decoded_whisper_boundary.audio_path, model_size="tiny")
        == "Recognized with tiny"
    )
    assert decoded_whisper_boundary.loads == [
        (model_size, "cpu") for model_size in models[:capacity]
    ]

    next_model = models[capacity]
    assert (
        adapter.recognize_file(
            decoded_whisper_boundary.audio_path, model_size=next_model
        )
        == f"Recognized with {next_model}"
    )
    evicted_model = "tiny" if capacity == 1 else "base"
    assert (
        adapter.recognize_file(
            decoded_whisper_boundary.audio_path, model_size=evicted_model
        )
        == f"Recognized with {evicted_model}"
    )
    assert decoded_whisper_boundary.loads[-2:] == [
        (next_model, "cpu"),
        (evicted_model, "cpu"),
    ]


def test_whisper_factory_changes_preserve_aliyun_token_credentials(monkeypatch):
    import chatterpal.core.asr.aliyun as aliyun_module

    token_provider = Mock(return_value="unused-generated-token")
    monkeypatch.setattr(
        aliyun_module,
        "nls",
        SimpleNamespace(token=SimpleNamespace(getToken=token_provider)),
    )
    for key, value in {
        "ASR_PROVIDER": "alibaba",
        "ALIYUN_ASR_APP_KEY": "test-app-key",
        "ALIYUN_ASR_TOKEN": "test-token",
        "ALIYUN_ASR_ACCESS_KEY_ID": "test-access-id",
        "ALIYUN_ASR_ACCESS_KEY_SECRET": "test-access-secret",
        "ALIBABA_API_KEY": "legacy-id",
        "ALIBABA_API_SECRET": "legacy-secret",
        "DASHSCOPE_API_KEY": "llm-only-key",
        "WHISPER_LANGUAGE": "auto",
        "WHISPER_DEVICE": "cuda:2",
        "WHISPER_MAX_CACHED_MODELS": "4",
    }.items():
        monkeypatch.setenv(key, value)

    settings = get_settings()
    adapter = create_asr(settings)

    assert settings.has_aliyun_asr_credentials() is True
    assert adapter.config == {
        "appkey": "test-app-key",
        "token": "test-token",
        "access_key_id": "test-access-id",
        "access_key_secret": "test-access-secret",
    }
    assert adapter.appkey == "test-app-key"
    assert adapter.token == "test-token"
    token_provider.assert_not_called()


@pytest.mark.parametrize("legacy_credentials", [False, True])
def test_aliyun_factory_preserves_access_key_token_exchange(
    monkeypatch, legacy_credentials
):
    import chatterpal.core.asr.aliyun as aliyun_module

    token_provider = Mock(return_value="generated-token")
    monkeypatch.setattr(
        aliyun_module,
        "nls",
        SimpleNamespace(token=SimpleNamespace(getToken=token_provider)),
    )
    monkeypatch.setenv("ASR_PROVIDER", "alibaba")
    monkeypatch.setenv("ALIYUN_ASR_APP_KEY", "test-app-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "llm-only-key")
    if legacy_credentials:
        monkeypatch.setenv("ALIBABA_API_KEY", "legacy-id")
        monkeypatch.setenv("ALIBABA_API_SECRET", "legacy-secret")
        expected_credentials = ("legacy-id", "legacy-secret")
    else:
        monkeypatch.setenv("ALIYUN_ASR_ACCESS_KEY_ID", "test-access-id")
        monkeypatch.setenv("ALIYUN_ASR_ACCESS_KEY_SECRET", "test-access-secret")
        monkeypatch.setenv("ALIBABA_API_KEY", "legacy-id")
        monkeypatch.setenv("ALIBABA_API_SECRET", "legacy-secret")
        expected_credentials = ("test-access-id", "test-access-secret")

    settings = get_settings()
    adapter = create_asr(settings)

    assert settings.has_aliyun_asr_credentials() is True
    assert adapter.appkey == "test-app-key"
    assert adapter.token == "generated-token"
    token_provider.assert_called_once_with(*expected_credentials)
