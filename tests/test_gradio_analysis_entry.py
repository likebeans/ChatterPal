"""Exercise analysis through the actual Gradio application's HTTP events."""

import json
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import gradio as gr
import httpx
import numpy as np
import pytest
import soundfile as sf
from gradio_client import Client, handle_file

from chatterpal.config.settings import Settings
from chatterpal.core.asr.base import ASRBase
from chatterpal.utils.preferences import UserPreferences
from chatterpal.web import app as app_module


TRANSCRIPT = "I went hiking with my friends last weekend."
ADVICE = "Check the transcript before practising the sentence again."


@pytest.fixture
def analysis_app(tmp_path, monkeypatch):
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(tmp_path / "gradio"))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost,::1")
    preferences = UserPreferences(config_dir=str(tmp_path / "preferences"))
    monkeypatch.setattr(
        "chatterpal.web.components.chat_tab.get_preferences_manager",
        lambda: preferences,
    )
    asr = Mock(spec=ASRBase)
    asr.recognize_gradio_audio.return_value = TRANSCRIPT
    llm = SimpleNamespace(
        chat=Mock(return_value=json.dumps({"feedback": ADVICE, "suggestions": []}))
    )
    tts = SimpleNamespace(
        get_supported_voices=lambda: ["en-US-JennyNeural"],
        get_voice_info=lambda voice: {"display_name": voice},
    )
    monkeypatch.setattr(app_module, "create_asr", lambda settings: asr)
    monkeypatch.setattr(app_module, "create_llm", lambda settings: llm)
    monkeypatch.setattr(app_module, "create_tts", lambda settings: tts)
    settings = Settings(
        _env_file=None,
        audio_temp_dir=str(tmp_path / "audio"),
        cache_dir=str(tmp_path / "cache"),
        model_cache_dir=str(tmp_path / "models"),
    )
    application = app_module.create_app(settings)
    interface = application.create_interface()
    audio_path = tmp_path / "input.wav"
    rate = 16000
    samples = (0.2 * np.sin(2 * np.pi * 180 * np.arange(rate) / rate)).astype(
        np.float32
    )
    sf.write(audio_path, samples, rate, subtype="PCM_16")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    try:
        interface.launch(
            server_name="127.0.0.1",
            server_port=port,
            prevent_thread_lock=True,
            inbrowser=False,
            share=False,
            quiet=True,
        )
        yield SimpleNamespace(
            app=application,
            interface=interface,
            url=interface.local_url,
            client=Client(interface.local_url, verbose=False, download_files=False),
            asr=asr,
            llm=llm,
            audio=audio_path,
        )
    finally:
        interface.close()


def test_main_page_registers_real_analysis_components(analysis_app):
    config = httpx.get(f"{analysis_app.url}config").json()
    dependencies = {item["api_name"]: item for item in config["dependencies"]}
    assert "_evaluate_pronunciation" in dependencies
    assert "_assess_pronunciation" in dependencies
    functions = analysis_app.interface.fns
    score = dependencies["_evaluate_pronunciation"]
    correction = dependencies["_assess_pronunciation"]
    assert (
        functions[score["id"]].fn == analysis_app.app.score_tab._evaluate_pronunciation
    )
    assert (
        functions[correction["id"]].fn
        == analysis_app.app.correct_tab._assess_pronunciation
    )
    assert len(score["outputs"]) == 1
    assert len(correction["outputs"]) == 5
    labels = [
        item.label
        for item in analysis_app.interface.blocks.values()
        if isinstance(item, gr.Tab)
    ]
    assert labels == ["💬 对话练习", "📊 发音评分", "🔧 发音纠错"]
    analysis_app.asr.recognize_gradio_audio.assert_not_called()
    analysis_app.llm.chat.assert_not_called()


def test_main_score_uses_one_recognition_and_text_advice(analysis_app):
    result = analysis_app.client.predict(
        TRANSCRIPT, handle_file(analysis_app.audio), api_name="/_evaluate_pronunciation"
    )
    assert TRANSCRIPT in result
    assert ADVICE in result
    assert "识别文本一致度: 100.0%" in result
    assert "非声学发音准确率" in result
    assert "综合评分: 暂不可评估" in result
    assert "音素分析不可用" in result
    assert "演示" not in result
    analysis_app.asr.recognize_gradio_audio.assert_called_once()
    analysis_app.llm.chat.assert_called_once()
    rate, samples = analysis_app.asr.recognize_gradio_audio.call_args.args[0]
    assert rate == 16000
    assert len(samples) == 16000


def test_main_correction_accepts_optional_target_without_invented_scores(analysis_app):
    result = analysis_app.client.predict(
        handle_file(analysis_app.audio), "", api_name="/_assess_pronunciation"
    )
    assert len(result) == 5
    assert result[0] == TRANSCRIPT
    assert "综合评分: 暂不可评估" in result[1]
    assert "来源：" in result[1]
    assert "单词分析不可用" in result[2]
    assert "音素分析不可用" in result[3]
    assert "演示" not in "\n".join(result)
    analysis_app.asr.recognize_gradio_audio.assert_called_once()
    analysis_app.llm.chat.assert_not_called()


def test_main_correction_preserves_stereo_upload_duration_and_waveform(
    analysis_app, tmp_path, monkeypatch
):
    rate = 16000
    tone = np.sin(2 * np.pi * 180 * np.arange(rate) / rate)
    stereo = np.column_stack((0.8 * tone, 0.4 * tone))
    audio_path = tmp_path / "stereo.wav"
    sf.write(audio_path, stereo, rate, subtype="PCM_16")
    analyzer = analysis_app.app.correction_service.prosody_analyzer
    analyze = analyzer._analyze_prosody_features
    measured = {}

    def inspect_audio(path):
        samples, sample_rate = sf.read(path, dtype="int16")
        measured.update(samples=samples, rate=sample_rate, result=analyze(path))
        return measured["result"]

    monkeypatch.setattr(analyzer, "_analyze_prosody_features", inspect_audio)
    result = analysis_app.client.predict(
        handle_file(audio_path), "", api_name="/_assess_pronunciation"
    )
    assert result[0] == TRANSCRIPT
    analysis_app.asr.recognize_gradio_audio.assert_called_once()
    input_rate, input_samples = analysis_app.asr.recognize_gradio_audio.call_args.args[
        0
    ]
    assert input_rate == rate
    assert input_samples.shape == (rate, 2)
    assert measured["rate"] == rate
    assert measured["samples"].shape == (rate,)
    expected = input_samples.mean(axis=1).astype(np.int16)
    np.testing.assert_array_equal(measured["samples"], expected)
    assert measured["result"]["duration"] == pytest.approx(1.0)
    assert measured["result"]["prosody_features"].f0_mean == pytest.approx(180.0, abs=2)


def test_main_analysis_rejects_missing_inputs_without_provider_work(analysis_app):
    score = analysis_app.client.predict("", None, api_name="/_evaluate_pronunciation")
    assert "请先输入" in score
    score = analysis_app.client.predict(
        TRANSCRIPT, None, api_name="/_evaluate_pronunciation"
    )
    assert "请录制" in score
    correction = analysis_app.client.predict(
        None, "", api_name="/_assess_pronunciation"
    )
    assert "请录制" in correction[0]
    analysis_app.asr.recognize_gradio_audio.assert_not_called()
    analysis_app.llm.chat.assert_not_called()


@pytest.mark.parametrize("kind", ["score", "correction"])
def test_main_analysis_recovers_after_recognition_failure(analysis_app, kind):
    analysis_app.asr.recognize_gradio_audio.side_effect = RuntimeError(
        "offline recognition unavailable"
    )
    args, name = (
        ([TRANSCRIPT, handle_file(analysis_app.audio)], "/_evaluate_pronunciation")
        if kind == "score"
        else ([handle_file(analysis_app.audio), TRANSCRIPT], "/_assess_pronunciation")
    )
    failed = analysis_app.client.predict(*args, api_name=name)
    text = failed if isinstance(failed, str) else "\n".join(failed)
    assert "出错" in text
    assert "offline recognition unavailable" in text
    assert "演示" not in text
    analysis_app.llm.chat.assert_not_called()
    analysis_app.asr.recognize_gradio_audio.side_effect = None
    recovered = analysis_app.client.predict(*args, api_name=name)
    text = recovered if isinstance(recovered, str) else "\n".join(recovered)
    assert TRANSCRIPT in text
    assert "综合评分: 暂不可评估" in text
    assert "出错" not in text
    assert analysis_app.asr.recognize_gradio_audio.call_count == 2


def test_main_score_preserves_transcript_when_llm_advice_fails(analysis_app):
    analysis_app.llm.chat.side_effect = RuntimeError("offline model unavailable")
    result = analysis_app.client.predict(
        TRANSCRIPT, handle_file(analysis_app.audio), api_name="/_evaluate_pronunciation"
    )
    assert TRANSCRIPT in result
    assert "AI练习建议未配置或不可用" in result
    assert "识别文本一致度: 100.0%" in result
    assert "综合评分: 暂不可评估" in result
    assert "演示" not in result
    analysis_app.asr.recognize_gradio_audio.assert_called_once()
