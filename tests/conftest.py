"""Shared fixtures and explicit opt-ins for ChatterPal's offline test suite."""

import io
import ipaddress
import os
import socket
import sys
import tempfile
import wave
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

# Gradio otherwise starts an analytics thread while test modules are imported.
os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root / "src"))


_EXTERNAL_OPTIONS = {
    "external_api": "--run-external",
    "model": "--run-models",
    "device": "--run-devices",
}
_MODEL_ENV_FIELDS = {"WHISPER_MODEL", "WHISPER_DEVICE", "MODEL_CACHE_DIR", "CACHE_DIR"}


def _enabled_resources(item):
    return {
        marker: bool(item.get_closest_marker(marker) and item.config.getoption(option))
        for marker, option in _EXTERNAL_OPTIONS.items()
    }


def pytest_addoption(parser):
    group = parser.getgroup("chatterpal")
    group.addoption(
        "--run-external", action="store_true", default=False,
        help="Run tests marked external_api using real external services.",
    )
    group.addoption(
        "--run-models", action="store_true", default=False,
        help="Run tests marked model using real model weights.",
    )
    group.addoption(
        "--run-devices", action="store_true", default=False,
        help="Run tests marked device using local audio hardware.",
    )


def _is_local_host(host):
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def pytest_configure(config):
    """Block accidental external access, including during module collection."""
    for marker in (
        "unit: isolated unit test",
        "integration: integration of local application components",
        "slow: long-running local test",
        "web: web component test",
        "service: service layer test",
        "external_api: live external service; requires --run-external",
        "model: real model weights; requires --run-models",
        "device: local audio hardware; requires --run-devices",
    ):
        config.addinivalue_line("markers", marker)

    config._chatterpal_permissions = dict.fromkeys(_EXTERNAL_OPTIONS, False)
    patcher = pytest.MonkeyPatch()
    config.add_cleanup(patcher.undo)
    original_getaddrinfo = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def check_host(host):
        if not config._chatterpal_permissions["external_api"] and not _is_local_host(host):
            pytest.fail(
                f"External network access to {host!r} is disabled. Mock the external "
                "boundary, or mark a live test external_api and use --run-external.",
                pytrace=False,
            )

    def guarded_getaddrinfo(host, *args, **kwargs):
        check_host(host)
        return original_getaddrinfo(host, *args, **kwargs)

    def guarded_connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            check_host(address[0])
        return original_connect(sock, address)

    def guarded_connect_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            check_host(address[0])
        return original_connect_ex(sock, address)

    patcher.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    patcher.setattr(socket.socket, "connect", guarded_connect)
    patcher.setattr(socket.socket, "connect_ex", guarded_connect_ex)

    import pyaudio
    import speech_recognition

    def guard_resource(resource, original, message):
        def guarded(*args, **kwargs):
            if not config._chatterpal_permissions[resource]:
                pytest.fail(message, pytrace=False)
            return original(*args, **kwargs)

        return guarded

    patcher.setattr(
        speech_recognition.Recognizer, "recognize_whisper",
        guard_resource(
            "model", speech_recognition.Recognizer.recognize_whisper,
            "Real Whisper inference is disabled. Mock the recognizer boundary, "
            "or mark the test model and use --run-models.",
        ),
    )
    for method in ("__init__", "open"):
        patcher.setattr(
            pyaudio.PyAudio, method,
            guard_resource(
                "device", getattr(pyaudio.PyAudio, method),
                "Audio hardware is disabled. Mock the device boundary, "
                "or mark the test device and use --run-devices.",
            ),
        )

    # Yield fixtures and request.addfinalizer callbacks retain the permissions
    # of their setup, even if a session fixture outlives the last live test.
    original_addfinalizer = pytest.FixtureDef.addfinalizer

    def addfinalizer(fixturedef, finalizer):
        permissions = config._chatterpal_permissions.copy()

        def authorized_finalizer():
            previous = config._chatterpal_permissions
            config._chatterpal_permissions = permissions
            try:
                return finalizer()
            finally:
                config._chatterpal_permissions = previous

        original_addfinalizer(fixturedef, authorized_finalizer)

    patcher.setattr(pytest.FixtureDef, "addfinalizer", addfinalizer)

    from chatterpal.config import settings as settings_module
    from pydantic_settings import DotEnvSettingsSource

    # Exclude only host defaults. Tests can still create and load synthetic
    # dotenv files, including the configuration contract's fake checkout.
    host_dotenv_files = {
        (Path.cwd() / ".env").resolve(),
        (Path(settings_module.__file__).resolve().parents[3] / ".env").resolve(),
    }
    original_read_env_file = DotEnvSettingsSource._read_env_file

    def read_env_file(source, file_path):
        if (
            issubclass(source.settings_cls, settings_module.Settings)
            and not config._chatterpal_permissions["external_api"]
            and Path(file_path).expanduser().resolve() in host_dotenv_files
        ):
            return {}
        return original_read_env_file(source, file_path)

    patcher.setattr(DotEnvSettingsSource, "_read_env_file", read_env_file)


def pytest_collection_modifyitems(config, items):
    for item in items:
        for marker, option in _EXTERNAL_OPTIONS.items():
            if item.get_closest_marker(marker) and not config.getoption(option):
                item.add_marker(pytest.mark.skip(reason=f"Requires explicit {option}"))


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_protocol(item):
    previous = item.config._chatterpal_permissions
    item.config._chatterpal_permissions = _enabled_resources(item)
    try:
        return (yield)
    finally:
        item.config._chatterpal_permissions = previous


@pytest.fixture(autouse=True)
def setup_test_environment(monkeypatch, tmp_path, request):
    """Keep credentials, runtime files and settings independent between tests."""
    from chatterpal.config import settings as settings_module

    permissions = _enabled_resources(request.node)
    preserved_fields = _MODEL_ENV_FIELDS if permissions["model"] else set()
    if not permissions["external_api"]:
        fields = {name.upper() for name in settings_module.Settings.model_fields}
        for key in tuple(os.environ):
            if key.upper() in fields - preserved_fields:
                monkeypatch.delenv(key)

    for key, value in {
        "ENVIRONMENT": "development",
        "DEBUG": "true",
        "AUDIO_TEMP_DIR": str(tmp_path / "audio"),
        "CACHE_DIR": str(tmp_path / "cache"),
        "MODEL_CACHE_DIR": str(tmp_path / "models"),
        "GRADIO_ANALYTICS_ENABLED": "False",
    }.items():
        if key in preserved_fields and any(name.upper() == key for name in os.environ):
            continue
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(settings_module, "_settings", None)


@pytest.fixture
def sample_audio_array():
    """One second of mono audio with an exact 16 kHz sample grid."""
    sample_rate = 16000
    time = np.arange(sample_rate) / sample_rate
    return sample_rate, (0.5 * np.sin(2 * np.pi * 440 * time)).astype(np.float32)


@pytest.fixture
def audio_bytes(sample_audio_array):
    """Decodable PCM16 WAV bytes for audio-taking services and provider doubles."""
    sample_rate, audio = sample_audio_array
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        pcm = np.clip(np.rint(audio * 32768), -32768, 32767).astype("<i2")
        wav_file.writeframes(pcm.tobytes())
    return buffer.getvalue()


@pytest.fixture
def temp_audio_file(tmp_path, audio_bytes):
    path = tmp_path / "sample.wav"
    path.write_bytes(audio_bytes)
    return str(path)


@pytest.fixture
def gradio_audio_data(sample_audio_array):
    return sample_audio_array


@pytest.fixture
def mock_config():
    return {
        "audio_sample_rate": 16000,
        "whisper_model": "base",
        "asr_provider": "whisper",
        "tts_provider": "edge",
        "llm_provider": "alibaba",
        "api_key": "test_key",
        "api_secret": "test_secret",
        "temperature": 0.7,
        "max_tokens": 1000,
    }


@pytest.fixture
def temp_directory():
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir


@pytest.fixture
def mock_asr():
    mock = Mock()
    mock.recognize.return_value = "Hello, this is a test recognition result."
    mock.recognize_file.return_value = mock.recognize.return_value
    mock.recognize_gradio_audio.return_value = mock.recognize.return_value
    mock.test_connection.return_value = True
    mock.get_supported_formats.return_value = ["wav", "mp3", "flac"]
    mock.validate_audio_file.return_value = True
    return mock


@pytest.fixture
def mock_tts(audio_bytes):
    mock = Mock()
    mock.synthesize.return_value = audio_bytes

    def synthesize_to_file(text, output_path, **kwargs):
        Path(output_path).write_bytes(audio_bytes)
        return True

    mock.synthesize_to_file.side_effect = synthesize_to_file
    mock.test_connection.return_value = True
    mock.get_supported_voices.return_value = ["en-US-AriaNeural", "en-US-GuyNeural"]
    mock.get_supported_formats.return_value = ["wav", "mp3"]
    mock.validate_text.return_value = True
    mock.clean_text_for_tts.side_effect = lambda text: text
    return mock


@pytest.fixture
def mock_llm():
    mock = Mock()
    mock.chat.return_value = "This is a mock response from the language model."
    mock.chat_stream.return_value = iter(["This ", "is ", "a ", "mock ", "response."])
    mock.test_connection.return_value = True
    mock.normalize_messages.side_effect = lambda messages: (
        messages if isinstance(messages, list)
        else [{"role": "user", "content": str(messages)}]
    )
    mock.validate_messages.return_value = True
    mock.create_conversation.return_value = Mock()
    mock.get_model_info.return_value = {
        "provider": "MockLLM", "model": "test-model", "temperature": 0.7,
    }
    return mock


@pytest.fixture
def mock_assessment():
    from chatterpal.core.assessment.base import (
        AssessmentResult, PhonemeAnalysis, ProsodyFeatures, WordAnalysis,
    )

    mock = Mock()
    mock.assess.return_value = AssessmentResult(
        overall_score=85.0,
        fluency_score=80.0,
        pronunciation_score=90.0,
        prosody_score=80.0,
        accuracy_score=85.0,
        prosody_features=ProsodyFeatures(
            speaking_rate=120.0, f0_mean=150.0, fluency_score=0.8,
        ),
        word_analysis=[WordAnalysis(
            target_word="hello", recognized_word="hello",
            is_correct=True, confidence_score=0.9,
        )],
        phoneme_analysis=[PhonemeAnalysis(
            phoneme="h", error_type="correct", description="Correctly pronounced",
        )],
        recognized_text="Hello, how are you today",
        target_text="Hello, how are you today",
        feedback="Good pronunciation overall",
        suggestions=["Continue practicing", "Work on intonation"],
    )
    mock.test_functionality.return_value = True
    mock.validate_audio_data.return_value = True
    mock.detect_language.return_value = "en"
    mock.calculate_text_similarity.return_value = 0.9
    mock.estimate_audio_duration.return_value = 2.5
    return mock


@pytest.fixture
def sample_conversation_history():
    return [
        {"role": "system", "content": "You are a helpful English teacher."},
        {"role": "user", "content": "Hello, how are you"},
        {"role": "assistant", "content": "I'm doing well, thank you! How can I help you practice English today"},
        {"role": "user", "content": "I want to practice pronunciation."},
        {"role": "assistant", "content": "Great! Let's start with some basic words. Can you say 'hello' for me"},
    ]


@pytest.fixture
def sample_assessment_result():
    from chatterpal.core.assessment.base import (
        AssessmentResult, PhonemeAnalysis, ProsodyFeatures, WordAnalysis,
    )

    return AssessmentResult(
        overall_score=75.0,
        fluency_score=70.0,
        pronunciation_score=80.0,
        prosody_score=75.0,
        accuracy_score=80.0,
        prosody_features=ProsodyFeatures(
            speaking_rate=110.0, f0_mean=160.0,
            fluency_score=0.7, vowel_accuracy=0.8,
        ),
        word_analysis=[
            WordAnalysis(
                target_word="pronunciation", recognized_word="pronunciation",
                is_correct=True, confidence_score=0.85, correction_tips=[],
            ),
            WordAnalysis(
                target_word="practice", recognized_word="practise", is_correct=False,
                confidence_score=0.6, correction_tips=["Focus on the 'c' sound"],
            ),
        ],
        phoneme_analysis=[PhonemeAnalysis(
            phoneme="æ", error_type="substitution",
            description="Vowel substitution detected",
            correction_method=["Practice with minimal pairs"], severity="medium",
        )],
        recognized_text="I want to practice pronunciation",
        target_text="I want to practice pronunciation",
        audio_duration=3.2,
        feedback="Good effort! Focus on vowel clarity.",
        suggestions=[
            "Practice vowel sounds with minimal pairs",
            "Record yourself and compare with native speakers",
            "Work on word stress patterns",
        ],
    )
