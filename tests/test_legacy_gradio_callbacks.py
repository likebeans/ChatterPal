"""Exercise the legacy tab's messages boundary with real services and Gradio."""

import json
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import gradio as gr
from gradio_client import Client, handle_file
import numpy as np
import pytest
import soundfile as sf

from chatterpal.core.asr.base import ASRBase
from chatterpal.core.llm.alibaba import AlibabaBailianLLM
from chatterpal.services.chat import ChatService
from chatterpal.services.correction import CorrectionService
from chatterpal.services.evaluation import EvaluationService
from chatterpal.utils.preferences import UserPreferences
from chatterpal.web.components.chat_tab import ChatTab
from chatterpal.web.components.correct_tab import CorrectTab
from chatterpal.web.components.score_tab import ScoreTab


@pytest.fixture
def chat_ui(tmp_path, monkeypatch):
    preferences = UserPreferences(config_dir=str(tmp_path / "preferences"))
    monkeypatch.setattr(
        "chatterpal.web.components.chat_tab.get_preferences_manager",
        lambda: preferences,
    )
    client = Mock()
    client.chat.completions.create.side_effect = lambda **kwargs: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=f"Reply to {kwargs['messages'][-1]['content']}",
        ))],
        usage=None,
    )
    monkeypatch.setattr("chatterpal.core.llm.alibaba.OpenAI", lambda **kwargs: client)
    service = ChatService(llm=AlibabaBailianLLM({"api_key": "offline-test-key"}))
    tab = ChatTab(service)
    with gr.Blocks(analytics_enabled=False) as blocks:
        tab.create_interface()
    chatbot = next(item for item in blocks.blocks.values() if isinstance(item, gr.Chatbot))
    return SimpleNamespace(tab=tab, service=service, client=client, blocks=blocks, chatbot=chatbot)


def messages_callback(ui):
    return next(
        event.fn for event in ui.blocks.fns.values()
        if event.fn.__name__ == "_handle_chat_messages"
    )


def test_registered_messages_callback_preserves_turns_and_legacy_pairs(chat_ui):
    callback = messages_callback(chat_ui)
    audio, history = callback(None, "first", [], True)
    assert audio is None
    assert history == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "Reply to first"},
    ]
    displayed = chat_ui.chatbot.postprocess(history).model_dump()
    assert [message["content"] for message in displayed] == ["first", "Reply to first"]

    audio, history = callback(None, "second", history, True)
    assert audio is None
    assert [message["content"] for message in history] == [
        "first", "Reply to first", "second", "Reply to second",
    ]
    assert chat_ui.chatbot.postprocess(history) is not None
    assert len(chat_ui.service.sessions) == 1

    _, pairs = chat_ui.tab._handle_chat(None, "third", [], True)
    assert pairs == [
        ["first", "Reply to first"],
        ["second", "Reply to second"],
        ["third", "Reply to third"],
    ]


def test_registered_messages_callback_preserves_history_on_provider_error(chat_ui):
    callback = messages_callback(chat_ui)
    _, history = callback(None, "first", [], True)
    previous = [dict(message) for message in history]
    session_id = chat_ui.tab.current_session_id
    service_history = chat_ui.service.get_conversation_history(session_id)
    chat_ui.client.chat.completions.create.side_effect = RuntimeError("offline provider unavailable")

    audio, failed = callback(None, "retry me", history, True)

    assert audio is None
    assert history == previous
    assert failed[:2] == previous
    assert failed[-2] == {"role": "user", "content": "retry me"}
    assert failed[-1]["role"] == "assistant"
    assert failed[-1]["content"]
    assert len(failed) == 4
    assert chat_ui.chatbot.postprocess(failed) is not None
    assert chat_ui.service.get_conversation_history(session_id) == service_history


def test_legacy_tab_messages_round_trip_through_http_queue(chat_ui):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    try:
        chat_ui.blocks.launch(
            server_name="127.0.0.1", server_port=port,
            prevent_thread_lock=True, inbrowser=False, share=False, quiet=True,
        )
        client = Client(chat_ui.blocks.local_url, verbose=False, download_files=False)
        audio, history = client.predict(
            None, "first", [], api_name="/_handle_chat",
        )
        assert audio is None
        assert [(message["role"], message["content"]) for message in history] == [
            ("user", "first"), ("assistant", "Reply to first"),
        ]
        _, history = client.predict(
            None, "second", history, api_name="/_handle_chat",
        )
        assert [message["content"] for message in history] == [
            "first", "Reply to first", "second", "Reply to second",
        ]
        assert len(chat_ui.service.sessions) == 1
        chat_ui.client.chat.completions.create.side_effect = RuntimeError("offline unavailable")
        audio, failed = client.predict(
            None, "failed turn", history, api_name="/_handle_chat",
        )
        assert audio is None
        assert failed[:4] == history
        assert failed[-2]["content"] == "failed turn"
        assert failed[-1]["role"] == "assistant"
        assert failed[-1]["content"]
        status, cleared = client.predict(api_name="/_clear_context")
        assert "已清除" in status
        assert cleared == []
    finally:
        chat_ui.blocks.close()


def test_score_and_correction_reports_round_trip_through_http_queue(tmp_path, monkeypatch):
    target = "hello"
    sample_rate = 16000
    audio_path = tmp_path / "recording.wav"
    samples = (0.3 * np.sin(2 * np.pi * 180 * np.arange(sample_rate) / sample_rate)).astype(np.float32)
    sf.write(audio_path, samples, sample_rate, subtype="PCM_16")
    samples, actual_rate = sf.read(audio_path, dtype="int16")
    audio = (actual_rate, samples)
    asr = Mock(spec=ASRBase)
    asr.recognize_gradio_audio.return_value = target
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
            "pronunciation_score": 83,
            "fluency_score": 76,
            "feedback": "Practise the final consonant",
            "suggestions": ["Repeat the word slowly"],
        })))],
        usage=None,
    )
    monkeypatch.setattr("chatterpal.core.llm.alibaba.OpenAI", lambda **kwargs: client)
    evaluation = EvaluationService(
        asr=asr, llm=AlibabaBailianLLM({"api_key": "offline-test-key"}),
    )
    correction = CorrectionService(asr=asr)
    expected = correction.comprehensive_correction(audio, target, analysis_level="expert")
    with gr.Blocks(analytics_enabled=False) as blocks:
        ScoreTab(evaluation).create_interface()
        CorrectTab(correction).create_interface()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    try:
        blocks.launch(
            server_name="127.0.0.1", server_port=port,
            prevent_thread_lock=True, inbrowser=False, share=False, quiet=True,
        )
        browser = Client(blocks.local_url, verbose=False, download_files=False)
        score = browser.predict(
            target, handle_file(audio_path), api_name="/_evaluate_pronunciation",
        )
        assert isinstance(score, str)
        assert "83.0/100" in score
        assert "76.0/100" in score
        assert "Practise the final consonant" in score
        assert "Repeat the word slowly" in score
        assert "未校准" in score
        assert "声学分析不可用" in score
        assert "音素分析不可用" in score
        report = browser.predict(
            handle_file(audio_path), target, api_name="/_assess_pronunciation",
        )
        assert isinstance(report, tuple)
        assert len(report) == 5
        assert all(isinstance(value, str) for value in report)
        assert report[0] == target
        assert "未校准" in report[1]
        assert f"{expected.overall_score:.1f}/100" in report[1]
        assert expected.detailed_feedback in report[1]
        for item in expected.pronunciation_errors:
            assert item["description"] in report[2]
        for item in expected.phoneme_problems:
            assert item["description"] in report[3]
        for item in expected.improvement_suggestions + expected.practice_recommendations:
            assert item in report[4]
    finally:
        blocks.close()
