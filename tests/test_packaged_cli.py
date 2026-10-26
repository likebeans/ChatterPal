"""Exercise installed wheel entrypoints outside the checkout, without runtime extras."""

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import venv

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def installed_wheel(tmp_path_factory):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("Building the published CLI regression requires uv")
    root = tmp_path_factory.mktemp("published-cli")
    output = root / "dist"
    subprocess.run(
        [
            uv,
            "build",
            "--wheel",
            "--offline",
            "--no-python-downloads",
            "--python",
            sys.executable,
            "--out-dir",
            str(output),
            str(PROJECT_ROOT),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(output.glob("chatterpal-*.whl"))
    environment_root = root / "venv"
    # Standalone CPython on macOS needs its shared library beside the original
    # executable; symlinks preserve that relationship when creating the venv.
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(environment_root)
    binary = environment_root / ("Scripts" if os.name == "nt" else "bin")
    python = binary / ("python.exe" if os.name == "nt" else "python")
    subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--offline",
            "--no-deps",
            "--python",
            str(python),
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    outside = root / "outside-checkout"
    outside.mkdir()
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "CHATTERPAL_ENV_FILE"}
    }
    env.update(
        {
            "HOME": str(root / "user"),
            "XDG_CONFIG_HOME": str(root / "user-config"),
            "XDG_DATA_HOME": str(root / "user-data"),
            "XDG_CACHE_HOME": str(root / "user-cache"),
        }
    )
    return binary, python, outside, env


def _invoke(installed_wheel, command, *args):
    binary, _, outside, env = installed_wheel
    suffix = ".exe" if os.name == "nt" else ""
    return subprocess.run(
        [str(binary / f"{command}{suffix}"), *args],
        cwd=outside,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    "entrypoint", ["chatterpal", "chatterpal-run", "chatterpal-setup"]
)
def test_published_help_needs_no_runtime_extras(installed_wheel, entrypoint):
    result = _invoke(installed_wheel, entrypoint, "--help")
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    assert "Traceback" not in result.stderr


def test_every_launcher_mode_has_provider_free_help(installed_wheel):
    for mode in ("web", "api", "chat", "test", "demo"):
        result = _invoke(installed_wheel, "chatterpal-run", "--mode", mode, "--help")
        assert result.returncode == 0, result.stderr
        assert "--mode {web,api,chat,test,demo}" in result.stdout


def test_wheel_entrypoints_and_public_exports_are_lazy(installed_wheel):
    _, python, outside, env = installed_wheel
    program = """
import importlib.metadata
import importlib.util
import sys
from chatterpal.core.asr import ASRBase, ASRError
from chatterpal.core.llm import LLMBase, LLMError
from chatterpal.services import TopicGenerator
from chatterpal.utils import get_logger, TextProcessor
import chatterpal.services as services
for entry in importlib.metadata.distribution('chatterpal').entry_points:
    assert entry.value.startswith('chatterpal.cli.'), entry
    assert callable(entry.load())
assert 'ChatService' in dir(services)
try:
    services.unknown_export
except AttributeError:
    pass
else:
    raise AssertionError('Unknown exports must raise AttributeError')
assert not any(name == 'scripts' or name.startswith('scripts.') for name in sys.modules)
for name in ('numpy', 'whisper', 'torch', 'pyaudio', 'gradio', 'parselmouth',
             'openai', 'dashscope', 'dotenv', 'pydantic'):
    assert name not in sys.modules, name
    assert importlib.util.find_spec(name) is None, name
assert ASRBase.__module__ == 'chatterpal.core.asr.base'
assert LLMBase.__module__ == 'chatterpal.core.llm.base'
assert get_logger.__module__ == 'chatterpal.utils.logger'
assert TextProcessor.__module__ == 'chatterpal.utils.text'
"""
    result = subprocess.run(
        [str(python), "-I", "-c", program],
        cwd=outside,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_setup_default_is_read_only(installed_wheel):
    _, python, outside, env = installed_wheel
    before = sorted(str(path) for path in outside.parent.rglob("*"))
    result = _invoke(installed_wheel, "chatterpal-setup")
    assert result.returncode == 0, result.stderr
    assert "not installed (optional)" in result.stdout
    after = sorted(str(path) for path in outside.parent.rglob("*"))
    # Python may write bytecode when console scripts import a module for the first time.
    assert [p for p in after if "__pycache__" not in p] == [
        p for p in before if "__pycache__" not in p
    ]
    assert not Path(env["XDG_CONFIG_HOME"]).exists()
    result = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "import importlib.metadata; print(len(list(importlib.metadata.distributions())))",
        ],
        cwd=outside,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "1"


def test_setup_config_is_private_and_never_overwritten(installed_wheel):
    _, _, outside, env = installed_wheel
    path = outside / "private" / "config.env"
    first = _invoke(installed_wheel, "chatterpal-setup", "env", "--env-file", str(path))
    assert first.returncode == 0, first.stderr
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    path.write_text("OPENAI_API_KEY=keep-existing\n", encoding="utf-8")
    second = _invoke(
        installed_wheel, "chatterpal-setup", "env", "--env-file", str(path)
    )
    assert second.returncode == 1
    assert path.read_text(encoding="utf-8") == "OPENAI_API_KEY=keep-existing\n"
    assert "left unchanged" in second.stderr
    assert not Path(env["XDG_CONFIG_HOME"]).exists()


def test_setup_preserves_symlink_target(installed_wheel):
    if os.name == "nt":
        pytest.skip("Symlink creation may require elevated privileges on Windows")
    _, _, outside, _ = installed_wheel
    target = outside / "original.env"
    target.write_text("do not replace\n", encoding="utf-8")
    link = outside / "linked.env"
    link.symlink_to(target)
    result = _invoke(
        installed_wheel, "chatterpal-setup", "env", "--env-file", str(link)
    )
    assert result.returncode == 1
    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == "do not replace\n"


def test_source_wrappers_have_no_separate_runtime(installed_wheel):
    _, python, outside, env = installed_wheel
    for script in ("run.py", "setup.py"):
        result = subprocess.run(
            [str(python), str(PROJECT_ROOT / "scripts" / script), "--help"],
            cwd=outside,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "usage:" in result.stdout


@pytest.mark.parametrize("command", ["run", "setup"])
def test_platform_wrappers_preserve_selected_environment(installed_wheel, command):
    _, python, outside, env = installed_wheel
    env = dict(env, UV_PROJECT_ENVIRONMENT=str(python.parent.parent))
    if os.name == "nt":
        invocation = [os.environ.get("COMSPEC", "cmd.exe"), "/c"]
        script = PROJECT_ROOT / "scripts" / f"{command}.bat"
    else:
        invocation = ["bash"]
        script = PROJECT_ROOT / "scripts" / f"{command}.sh"
    result = subprocess.run(
        [*invocation, str(script), "--help"],
        cwd=outside,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    assert "Downloading" not in result.stderr
    assert "Installed" not in result.stderr


def test_environment_only_and_dotenv_precedence(monkeypatch, tmp_path):
    from chatterpal.cli.config import load_environment, runtime_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CHATTERPAL_ENV_FILE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "missing-user-config"))
    monkeypatch.setenv("API_PORT", "9137")
    assert runtime_settings().api_port == 9137
    env_file = tmp_path / "settings.env"
    env_file.write_text("API_PORT=9138\nOPENAI_MODEL=dotenv-model\n", encoding="utf-8")
    settings = runtime_settings(env_file)
    assert settings.api_port == 9137
    assert settings.openai_model == "dotenv-model"
    with pytest.raises(FileNotFoundError):
        load_environment(tmp_path / "missing.env")


def test_user_config_is_discovered(monkeypatch, tmp_path):
    from chatterpal.cli.config import runtime_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CHATTERPAL_ENV_FILE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))
    env_file = tmp_path / "user-config" / "chatterpal" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("API_PORT=9139\n", encoding="utf-8")
    assert runtime_settings().api_port == 9139


def test_demo_basic_needs_no_provider(monkeypatch, tmp_path, capsys):
    from chatterpal.cli.run import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CHATTERPAL_ENV_FILE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "missing-user-config"))
    assert main(["--mode", "demo"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["audio_sample_rate"] == 16000


def test_base_wheel_config_commands_need_no_runtime_extras(installed_wheel, tmp_path):
    _, _, _, env = installed_wheel
    uv = shutil.which("uv")
    environment_root = tmp_path / "base-venv"
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(environment_root)
    binary = environment_root / ("Scripts" if os.name == "nt" else "bin")
    python = binary / ("python.exe" if os.name == "nt" else "python")
    wheel = next(installed_wheel[2].parent.glob("dist/chatterpal-*.whl"))
    subprocess.run(
        [uv, "pip", "install", "--offline", "--python", str(python), str(wheel)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    env = {
        key: value
        for key, value in env.items()
        if key
        not in {
            "ALIYUN_ASR_ACCESS_KEY_ID",
            "ALIYUN_ASR_ACCESS_KEY_SECRET",
            "ALIBABA_API_KEY",
            "ALIBABA_API_SECRET",
            "OPENAI_BASE_URL",
        }
    }
    env.update(
        {
            "XDG_CONFIG_HOME": str(tmp_path / "user-config"),
            "ASR_PROVIDER": "alibaba",
            "ALIYUN_ASR_APP_KEY": "offline-app",
            "ALIYUN_ASR_TOKEN": "offline-token",
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": "offline-key",
            "TTS_PROVIDER": "edge",
            "API_PORT": "9137",
        }
    )
    installed = binary, python, outside, env
    result = _invoke(installed, "chatterpal-setup", "verify")
    assert result.returncode == 0, result.stderr
    assert "No provider calls" in result.stdout
    assert not Path(env["XDG_CONFIG_HOME"]).exists()
    demo = _invoke(installed, "chatterpal-run", "--mode", "demo")
    assert demo.returncode == 0, demo.stderr
    assert json.loads(demo.stdout)["asr_provider"] == "alibaba"

    config = outside / "private" / "settings.env"
    created = _invoke(installed, "chatterpal-setup", "env", "--env-file", str(config))
    assert created.returncode == 0, created.stderr
    config.write_text("API_PORT=9138\nOPENAI_MODEL=from-dotenv\n", encoding="utf-8")
    refused = _invoke(installed, "chatterpal-setup", "env", "--env-file", str(config))
    assert refused.returncode == 1
    assert (
        config.read_text(encoding="utf-8")
        == "API_PORT=9138\nOPENAI_MODEL=from-dotenv\n"
    )
    program = """
import importlib.util
import sys
from pathlib import Path
from chatterpal.cli.config import runtime_settings
settings = runtime_settings(Path(sys.argv[1]))
assert settings.api_port == 9137
assert settings.openai_model == 'from-dotenv'
for module in ('numpy', 'whisper', 'torch', 'pyaudio', 'gradio', 'parselmouth',
               'openai', 'fastapi'):
    assert importlib.util.find_spec(module) is None, module
"""
    result = subprocess.run(
        [str(python), "-I", "-c", program, str(config)],
        cwd=outside,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_tests_mode_uses_active_interpreter_and_absolute_checkout(
    monkeypatch, tmp_path
):
    from chatterpal.cli import run

    tests = tmp_path / "checkout" / "tests"
    tests.mkdir(parents=True)
    seen = []

    def invoke(command, **kwargs):
        seen.append((command, kwargs))
        return subprocess.CompletedProcess(command, 5)

    monkeypatch.setattr(run.subprocess, "run", invoke)
    assert (
        run.main(
            ["--mode", "test", "--test-type", "integration", "--tests-path", str(tests)]
        )
        == 5
    )
    command, options = seen[0]
    assert command == [
        sys.executable,
        "-m",
        "pytest",
        str(tests),
        "-v",
        "-k",
        "integration",
    ]
    assert options["cwd"] == tests.parent
