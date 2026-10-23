"""Application construction stays offline and does not start a server."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import gradio as gr

from chatterpal.config.settings import Settings
from chatterpal.core.asr.whisper import WhisperASR
from chatterpal.core.llm.alibaba import AlibabaBailianLLM
from chatterpal.core.tts.edge import EdgeTTS
from chatterpal.services.chat import ChatService
from chatterpal.services.correction import CorrectionService
from chatterpal.services.evaluation import EvaluationService
from chatterpal.utils.preferences import UserPreferences


def test_app_startup(tmp_path, monkeypatch):
    """Construct real providers/services while replacing SDK network boundaries."""
    from chatterpal.web.app import ChatterPalApp, create_app

    client = Mock()
    monkeypatch.setattr("chatterpal.core.llm.alibaba.OpenAI", lambda **kwargs: client)
    voices = [{"ShortName": "en-US-AriaNeural", "Gender": "Female", "Locale": "en-US"}]

    async def list_voices():
        return voices

    monkeypatch.setattr("chatterpal.core.tts.edge.edge_tts.list_voices", list_voices)
    preferences = UserPreferences(config_dir=str(tmp_path / "preferences"))
    monkeypatch.setattr("chatterpal.web.components.chat_tab.get_preferences_manager", lambda: preferences)
    settings = Settings(
        _env_file=None,
        asr_provider="whisper", tts_provider="edge", llm_provider="alibaba",
        dashscope_api_key="offline-test-key",
        audio_temp_dir=str(tmp_path / "audio"),
        cache_dir=str(tmp_path / "cache"),
        model_cache_dir=str(tmp_path / "models"),
    )
    app = create_app(settings=settings)
    assert isinstance(app, ChatterPalApp)
    assert app.settings is settings
    assert isinstance(app.asr, WhisperASR)
    assert isinstance(app.tts, EdgeTTS)
    assert isinstance(app.llm, AlibabaBailianLLM)
    assert isinstance(app.chat_service, ChatService)
    assert isinstance(app.evaluation_service, EvaluationService)
    assert isinstance(app.correction_service, CorrectionService)
    assert app.chat_tab.chat_service is app.chat_service
    assert app.score_tab.evaluation_service is app.evaluation_service
    assert app.correct_tab.correction_service is app.correction_service
    interface = app.create_interface()
    assert isinstance(interface, gr.Blocks)
    assert len(interface.fns) >= 3
    client.chat.completions.create.assert_not_called()


def test_run_script_exists(monkeypatch):
    """Load the entrypoint by absolute path without modifying global sys.path."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "run.py"
    assert script.is_file(), f"Run script does not exist: {script}"
    # The imported script sets encoding environment variables; restore them.
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    spec = importlib.util.spec_from_file_location("chatterpal_test_runner", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)
    assert callable(module.create_parser)
    args = module.create_parser().parse_args([])
    assert args.mode == "web"
