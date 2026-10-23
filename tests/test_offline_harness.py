"""Regression coverage for decodable fixtures and the offline test boundary."""

import io
import socket
import textwrap
import wave
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

pytest_plugins = ("pytester",)


@pytest.fixture
def harness_pytester(pytester):
    # Replace the real boundaries before pytest_configure captures them, so a
    # broken gate is observable without model downloads or PortAudio calls.
    pytester.makepyfile(
        safe_boundaries="""
        import pyaudio
        import pytest
        import speech_recognition
        import whisper as whisper_runtime
        import urllib.request

        calls = []

        def whisper(self, audio, **kwargs):
            calls.append(("model", audio, kwargs))
            return "safe inference"

        def load_model(name, **kwargs):
            calls.append(("model_load", name, kwargs))
            return "safe model"

        def open_url(request, *args, **kwargs):
            calls.append(("url", getattr(request, "full_url", request)))
            return "safe response"

        def initialize(self):
            calls.append(("device_init",))

        def open_stream(self, *args, **kwargs):
            calls.append(("device_open",))
            return "safe stream"

        def device_count(self):
            calls.append(("device_enumeration",))
            return 1

        patcher = pytest.MonkeyPatch()
        patcher.setattr(speech_recognition.Recognizer, "recognize_whisper", whisper)
        patcher.setattr(whisper_runtime, "load_model", load_model)
        patcher.setattr(urllib.request, "urlopen", open_url)
        patcher.setattr(pyaudio.PyAudio, "__init__", initialize)
        patcher.setattr(pyaudio.PyAudio, "open", open_stream)
        patcher.setattr(pyaudio.PyAudio, "get_device_count", device_count)
        patcher.setattr(
            pyaudio.PyAudio, "get_default_input_device_info",
            lambda self: {"defaultSampleRate": 16000},
        )
        patcher.setattr(pyaudio.PyAudio, "terminate", lambda self: None)
        """
    )
    pytester.makeconftest(
        "import safe_boundaries\n"
        + Path(__file__).with_name("conftest.py").read_text(encoding="utf-8-sig")
    )
    return pytester


@pytest.mark.parametrize("fixture_name", ["audio_bytes", "temp_audio_file"])
def test_shared_audio_is_decodable_pcm16(request, fixture_name):
    source = request.getfixturevalue(fixture_name)
    if isinstance(source, bytes):
        source = io.BytesIO(source)
    with wave.open(source, "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16000
        assert wav_file.getnframes() == 16000
        samples = np.frombuffer(wav_file.readframes(16000), dtype="<i2")
    assert np.max(np.abs(samples)) > 0


def test_gradio_and_wav_fixtures_contain_the_same_signal(
    audio_bytes, gradio_audio_data
):
    samples, sample_rate = sf.read(io.BytesIO(audio_bytes))
    expected_rate, expected_samples = gradio_audio_data
    assert sample_rate == expected_rate
    np.testing.assert_allclose(samples, expected_samples, atol=1 / 32767)


def test_default_boundary_blocks_external_network():
    with pytest.raises(pytest.fail.Exception, match="External network access"):
        socket.getaddrinfo("example.test", 443)
    with socket.socket() as client:
        with pytest.raises(pytest.fail.Exception, match="External network access"):
            client.connect(("192.0.2.1", 443))
        with pytest.raises(pytest.fail.Exception, match="External network access"):
            client.connect_ex(("192.0.2.1", 443))


def test_local_http_transports_remain_available():
    from http.client import HTTPConnection
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"offline")

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 1
        worker = Thread(target=server.handle_request, daemon=True)
        worker.start()
        client = HTTPConnection("localhost", server.server_port, timeout=1)
        try:
            client.request("GET", "/")
            response = client.getresponse()
            assert response.status == 200
            assert response.read() == b"offline"
        finally:
            client.close()
            worker.join(timeout=2)
        assert not worker.is_alive()


def test_external_work_requires_each_explicit_opt_in(harness_pytester):
    pytester = harness_pytester
    pytester.makepyfile(
        """
        import socket
        import pytest

        def test_offline():
            with pytest.raises(pytest.fail.Exception, match="External network access"):
                socket.getaddrinfo("192.0.2.1", 443, flags=socket.AI_NUMERICHOST)

        @pytest.mark.external_api
        def test_cloud():
            # Numeric-only address resolution verifies the gate without DNS or I/O.
            addresses = socket.getaddrinfo(
                "192.0.2.1", 443, flags=socket.AI_NUMERICHOST
            )
            assert addresses[0][4][0] == "192.0.2.1"

        @pytest.mark.model
        def test_model():
            import pyaudio
            import speech_recognition
            assert speech_recognition.Recognizer().recognize_whisper(None) == "safe inference"
            with pytest.raises(pytest.fail.Exception, match="Audio hardware"):
                pyaudio.PyAudio()
            with pytest.raises(pytest.fail.Exception, match="External network access"):
                socket.getaddrinfo("192.0.2.1", 443, flags=socket.AI_NUMERICHOST)

        @pytest.mark.device
        def test_device():
            import pyaudio
            import speech_recognition
            assert pyaudio.PyAudio().open() == "safe stream"
            with pytest.raises(pytest.fail.Exception, match="Real Whisper inference"):
                speech_recognition.Recognizer().recognize_whisper(None)
            with pytest.raises(pytest.fail.Exception, match="External network access"):
                socket.getaddrinfo("192.0.2.1", 443, flags=socket.AI_NUMERICHOST)
        """
    )
    default = pytester.runpytest_subprocess("-o", "addopts=", "-q")
    default.assert_outcomes(passed=1, skipped=3)
    cloud = pytester.runpytest_subprocess("-o", "addopts=", "-q", "--run-external")
    cloud.assert_outcomes(passed=2, skipped=2)
    all_dependencies = pytester.runpytest_subprocess(
        "-o", "addopts=", "-q", "--run-external", "--run-models", "--run-devices"
    )
    all_dependencies.assert_outcomes(passed=4)


def test_unmarked_model_and_device_work_fails_before_execution():
    import pyaudio
    import speech_recognition

    with pytest.raises(pytest.fail.Exception, match="Real Whisper inference"):
        speech_recognition.Recognizer().recognize_whisper(None)
    with pytest.raises(pytest.fail.Exception, match="Audio hardware"):
        pyaudio.PyAudio.open(None)


@pytest.mark.parametrize("as_request", [False, True])
def test_external_url_is_blocked_before_a_loopback_proxy(harness_pytester, as_request):
    pytester = harness_pytester
    pytester.makepyfile(
        f'''
        import pytest
        import urllib.request
        import safe_boundaries

        def test_target_is_blocked(monkeypatch):
            monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
            url = "https://external.example.test/model.pt"
            target = urllib.request.Request(url) if {as_request!r} else url
            with pytest.raises(pytest.fail.Exception, match="External network access"):
                urllib.request.urlopen(target)
            assert safe_boundaries.calls == []
        '''
    )
    pytester.runpytest_subprocess("-o", "addopts=", "-q").assert_outcomes(passed=1)


def test_external_url_explicit_opt_in_remains_available(harness_pytester):
    pytester = harness_pytester
    pytester.makepyfile(
        '''
        import pytest
        import urllib.request
        import safe_boundaries

        @pytest.mark.external_api
        def test_allowed_target():
            url = "https://external.example.test/model.pt"
            assert urllib.request.urlopen(url) == "safe response"
            assert safe_boundaries.calls == [("url", url)]
        '''
    )
    pytester.runpytest_subprocess("-o", "addopts=", "-q").assert_outcomes(skipped=1)
    pytester.runpytest_subprocess(
        "-o", "addopts=", "-q", "--run-external"
    ).assert_outcomes(passed=1)


@pytest.mark.parametrize("stage", ["collection", "test"])
def test_direct_whisper_loader_requires_model_opt_in(harness_pytester, stage):
    pytester = harness_pytester
    check = '''
with pytest.raises(pytest.fail.Exception, match="Real Whisper model loading"):
    whisper.load_model("tiny", device="cpu")
assert safe_boundaries.calls == []
'''
    imports = "import pytest\nimport whisper\nimport safe_boundaries\n"
    body = check + "\ndef test_collected():\n    pass\n" if stage == "collection" else (
        "def test_loader_blocked():\n" + textwrap.indent(check, "    ")
    )
    pytester.makepyfile(imports + body)
    # Even an enabled CLI flag does not authorize unmarked tests or collection.
    result = pytester.runpytest_subprocess("-o", "addopts=", "-q", "--run-models")
    result.assert_outcomes(passed=1)


def test_direct_whisper_loader_allows_explicit_model_opt_in(harness_pytester):
    pytester = harness_pytester
    pytester.makepyfile(
        '''
        import pytest
        import whisper
        import safe_boundaries

        @pytest.mark.model
        def test_model_loader():
            assert whisper.load_model("tiny", device="cpu") == "safe model"
            assert safe_boundaries.calls == [("model_load", "tiny", {"device": "cpu"})]
        '''
    )
    pytester.runpytest_subprocess("-o", "addopts=", "-q").assert_outcomes(skipped=1)
    pytester.runpytest_subprocess(
        "-o", "addopts=", "-q", "--run-models"
    ).assert_outcomes(passed=1)


def test_collection_cannot_access_external_network(harness_pytester):
    pytester = harness_pytester
    pytester.makepyfile(
        """
        import socket
        socket.getaddrinfo("example.test", 443)

        def test_never_reached():
            assert True
        """
    )
    result = pytester.runpytest_subprocess("-o", "addopts=", "--collect-only", "-q")
    assert result.ret == pytest.ExitCode.INTERRUPTED
    result.stdout.fnmatch_lines(["*External network access*disabled*"])


@pytest.mark.parametrize("stage", ["collection", "session_fixture"])
def test_model_and_device_guards_cover_early_work(harness_pytester, stage):
    pytester = harness_pytester
    checks = """
    with pytest.raises(pytest.fail.Exception, match="Real Whisper inference"):
        speech_recognition.Recognizer().recognize_whisper(None)
    with pytest.raises(pytest.fail.Exception, match="Audio hardware"):
        pyaudio.PyAudio.open(None)
    with pytest.raises(pytest.fail.Exception, match="Audio hardware"):
        pyaudio.PyAudio()
    with pytest.raises(pytest.fail.Exception, match="Audio hardware"):
        speech_recognition.Microphone()
    assert safe_boundaries.calls == []
    """
    checks = textwrap.dedent(checks)
    imports = (
        "import pyaudio\nimport pytest\nimport speech_recognition\n"
        "import safe_boundaries\n"
    )
    if stage == "collection":
        body = checks + "\ndef test_collected():\n    pass\n"
    else:
        body = (
            "@pytest.fixture(scope='session')\ndef early_boundary():\n"
            + textwrap.indent(checks, "    ")
            + "\ndef test_session_boundary(early_boundary):\n    pass\n"
        )
    pytester.makepyfile(imports + body)
    options = ("--run-external", "--run-models", "--run-devices")
    if stage == "collection":
        result = pytester.runpytest_subprocess(
            "-o", "addopts=", "--collect-only", "-q", *options
        )
        assert result.ret == pytest.ExitCode.OK
    else:
        result = pytester.runpytest_subprocess("-o", "addopts=", "-q", *options)
        result.assert_outcomes(passed=1)


def test_session_fixture_cleanup_retains_its_own_permissions(harness_pytester):
    pytester = harness_pytester
    pytester.makepyfile(
        """
        import socket
        import pyaudio
        import pytest
        import speech_recognition
        import safe_boundaries

        def check_authorized_boundaries():
            # Numeric resolution exercises the real network wrapper without I/O.
            assert socket.getaddrinfo(
                "192.0.2.1", 443, flags=socket.AI_NUMERICHOST
            )[0][4][0] == "192.0.2.1"
            assert speech_recognition.Recognizer().recognize_whisper(None) == "safe inference"
            assert pyaudio.PyAudio().open() == "safe stream"

        @pytest.fixture(scope="session")
        def live_session(request):
            check_authorized_boundaries()
            request.addfinalizer(check_authorized_boundaries)
            yield
            check_authorized_boundaries()

        @pytest.fixture(scope="session")
        def offline_session(request):
            def check_blocked():
                before = list(safe_boundaries.calls)
                with pytest.raises(pytest.fail.Exception, match="External network access"):
                    socket.getaddrinfo("192.0.2.1", 443, flags=socket.AI_NUMERICHOST)
                with pytest.raises(pytest.fail.Exception, match="Real Whisper inference"):
                    speech_recognition.Recognizer().recognize_whisper(None)
                with pytest.raises(pytest.fail.Exception, match="Audio hardware"):
                    pyaudio.PyAudio()
                assert safe_boundaries.calls == before
            check_blocked()
            request.addfinalizer(check_blocked)
            yield
            check_blocked()

        def test_00_offline_fixture(offline_session):
            pass

        @pytest.mark.external_api
        @pytest.mark.model
        @pytest.mark.device
        def test_10_live_fixture(live_session):
            pass

        def test_20_offline_after_live_fixture():
            with pytest.raises(pytest.fail.Exception, match="External network access"):
                socket.getaddrinfo("192.0.2.1", 443, flags=socket.AI_NUMERICHOST)
            with pytest.raises(pytest.fail.Exception, match="Real Whisper inference"):
                speech_recognition.Recognizer().recognize_whisper(None)
            with pytest.raises(pytest.fail.Exception, match="Audio hardware"):
                pyaudio.PyAudio()
        """
    )
    for last_test in ("test_20_offline_after_live_fixture", "test_10_live_fixture"):
        result = pytester.runpytest_subprocess(
            "-o", "addopts=", "-q", "--run-external", "--run-models", "--run-devices",
            "test_session_fixture_cleanup_retains_its_own_permissions.py::test_00_offline_fixture",
            "test_session_fixture_cleanup_retains_its_own_permissions.py::"
            + ("test_10_live_fixture" if last_test.startswith("test_20") else "test_20_offline_after_live_fixture"),
            "test_session_fixture_cleanup_retains_its_own_permissions.py::" + last_test,
        )
        result.assert_outcomes(passed=3)


def test_authorized_model_keeps_configuration_and_cache(harness_pytester, monkeypatch):
    pytester = harness_pytester
    for key, value in {
        "WHISPER_MODEL": "tiny",
        "WHISPER_DEVICE": "cpu",
        "MODEL_CACHE_DIR": str(pytester.path / "existing-models"),
        "CACHE_DIR": str(pytester.path / "existing-cache"),
        "DASHSCOPE_API_KEY": "host-test-placeholder",
    }.items():
        monkeypatch.setenv(key, value)
    pytester.makepyfile(
        """
        import os
        from pathlib import Path
        import pytest
        from chatterpal.config import Settings, get_settings
        from chatterpal.config.loader import create_asr
        import chatterpal.core.asr.whisper as whisper_module

        @pytest.mark.model
        def test_model_config(monkeypatch):
            settings = get_settings()
            assert settings.whisper_model == Settings().whisper_model == "tiny"
            assert settings.whisper_device == Settings().whisper_device == "cpu"
            assert settings.model_cache_dir == str(Path.cwd() / "existing-models")
            assert settings.cache_dir == str(Path.cwd() / "existing-cache")
            assert not settings.dashscope_api_key
            assert "DASHSCOPE_API_KEY" not in os.environ
            received = []
            monkeypatch.setattr(whisper_module, "WhisperASR", received.append)
            create_asr(settings)
            assert received == [{
                "model_size": "tiny", "device": "cpu", "language": "en",
                "max_cached_models": 1,
            }]

        def test_offline_defaults_after_model():
            settings = get_settings()
            assert settings.whisper_model == "base"
            assert settings.whisper_device == "auto"
            assert settings.model_cache_dir != str(Path.cwd() / "existing-models")
            assert settings.cache_dir != str(Path.cwd() / "existing-cache")
            assert not settings.dashscope_api_key
        """
    )
    result = pytester.runpytest_subprocess("-o", "addopts=", "-q", "--run-models")
    result.assert_outcomes(passed=2)


@pytest.mark.parametrize("dotenv_location", ["cwd", "checkout"])
def test_host_dotenv_defaults_are_isolated_but_synthetic_files_work(
    harness_pytester, dotenv_location
):
    pytester = harness_pytester
    checkout = pytester.path / "checkout"
    checkout.mkdir()
    host_root = pytester.path if dotenv_location == "cwd" else checkout
    (host_root / ".env").write_text(
        "WHISPER_MODEL=small\nDASHSCOPE_API_KEY=host-file-placeholder\n"
    )
    pytester.makepyfile(
        dotenv_probe="""
        from pathlib import Path
        import pytest
        import chatterpal.config.settings as settings_module
        from pydantic_settings import DotEnvSettingsSource

        source_calls = []
        original_read = DotEnvSettingsSource._read_env_file
        def read_env_file(source, file_path):
            source_calls.append(Path(file_path).resolve())
            return original_read(source, file_path)

        patcher = pytest.MonkeyPatch()
        patcher.setattr(
            settings_module, "__file__",
            str(Path(__file__).parent / "checkout/src/chatterpal/config/settings.py"),
        )
        patcher.setattr(DotEnvSettingsSource, "_read_env_file", read_env_file)
        """
    )
    pytester.makeconftest(
        "import safe_boundaries\nimport dotenv_probe\n"
        + Path(__file__).with_name("conftest.py").read_text(encoding="utf-8-sig")
    )
    pytester.makepyfile(
        """
        import os
        from pathlib import Path
        import pytest
        import dotenv_probe
        import chatterpal.config.settings as settings_module
        from chatterpal.config import Settings, get_settings, reload_settings

        # Isolation is active at collection, before function fixtures exist.
        assert not Settings().dashscope_api_key
        assert not get_settings().dashscope_api_key
        assert dotenv_probe.source_calls == []

        def test_offline_ignores_host_defaults():
            for settings in (Settings(), get_settings()):
                assert settings.whisper_model == "base"
                assert not settings.dashscope_api_key
            assert "DASHSCOPE_API_KEY" not in os.environ
            assert dotenv_probe.source_calls == []

        def test_synthetic_dotenv_can_be_explicit(tmp_path):
            env_file = tmp_path / "synthetic.env"
            env_file.write_text("WHISPER_MODEL=tiny\\nDASHSCOPE_API_KEY=synthetic-placeholder\\n")
            settings = Settings(_env_file=env_file)
            assert settings.whisper_model == "tiny"
            assert settings.dashscope_api_key == "synthetic-placeholder"

        def test_synthetic_cwd_dotenv_can_be_reloaded(tmp_path, monkeypatch):
            monkeypatch.chdir(tmp_path)
            (tmp_path / ".env").write_text("WHISPER_MODEL=tiny\\n")
            assert Settings().whisper_model == "tiny"
            assert reload_settings().whisper_model == "tiny"

        def test_synthetic_checkout_fallback_still_works(tmp_path, monkeypatch):
            monkeypatch.chdir(tmp_path)
            checkout = tmp_path / "synthetic-checkout"
            checkout.mkdir()
            (checkout / ".env").write_text("WHISPER_MODEL=tiny\\n")
            monkeypatch.setattr(
                settings_module, "__file__",
                str(checkout / "src/chatterpal/config/settings.py"),
            )
            assert reload_settings().whisper_model == "tiny"

        @pytest.mark.external_api
        def test_explicit_external_opt_in_can_read_host_dotenv():
            assert get_settings().whisper_model == "small"
            assert get_settings().dashscope_api_key == "host-file-placeholder"
        """
    )
    result = pytester.runpytest_subprocess("-o", "addopts=", "-q", "--run-external")
    result.assert_outcomes(passed=5)
