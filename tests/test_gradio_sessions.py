"""Exercise browser session state through the real Gradio entry and HTTP events."""

import io
import socket
import threading
import time
import wave
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from gradio_client import Client

from chatterpal.config.settings import Settings
from chatterpal.core.llm.base import LLMError
from chatterpal.web import app as app_module


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 1600)
    return buffer.getvalue()


@pytest.fixture
def gradio_app(monkeypatch, tmp_path):
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(tmp_path / "gradio"))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost,::1")
    contexts = []

    def chat(messages, **kwargs):
        contexts.append([dict(message) for message in messages])
        return f"Reply to {messages[-1]['content']}"

    llm = SimpleNamespace(chat=Mock(side_effect=chat))
    tts = SimpleNamespace(
        synthesize=Mock(return_value=wav_bytes()),
        get_supported_voices=lambda: ["longxiaochun"],
        get_voice_info=lambda voice: {"display_name": voice},
    )
    asr = SimpleNamespace()
    monkeypatch.setattr(app_module, "create_asr", lambda settings: asr)
    monkeypatch.setattr(app_module, "create_llm", lambda settings: llm)
    monkeypatch.setattr(app_module, "create_tts", lambda settings: tts)
    settings = Settings(
        _env_file=None,
        audio_temp_dir=str(tmp_path / "audio"),
        cache_dir=str(tmp_path / "cache"),
        model_cache_dir=str(tmp_path / "models"),
    )
    application = app_module.ChatterPalApp(settings)
    interface = application.create_interface()
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
            llm=llm,
            tts=tts,
            contexts=contexts,
        )
    finally:
        interface.close()


def browser(gradio_app):
    return Client(gradio_app.url, verbose=False, download_files=False)


def send(client, message, history=None):
    return client.predict(
        message, history or [], "longxiaochun", api_name="/chat_response"
    )


def user_turns(session):
    return [
        message for message in session.get_messages() if message["role"] != "system"
    ]


def test_actual_entry_wires_click_submit_clear_and_browser_state(gradio_app):
    config = httpx.get(f"{gradio_app.url}config").json()
    components = {component["id"]: component for component in config["components"]}
    chat_event = next(
        event
        for event in config["dependencies"]
        if event["api_name"] == "chat_response"
    )
    assert {target[1] for target in chat_event["targets"]} == {"click", "submit"}
    assert [components[item]["type"] for item in chat_event["inputs"]] == [
        "textbox",
        "chatbot",
        "dropdown",
        "state",
    ]
    assert [components[item]["type"] for item in chat_event["outputs"]] == [
        "chatbot",
        "textbox",
        "audio",
        "state",
    ]
    clear_event = next(
        event for event in config["dependencies"] if event["api_name"] == "clear_chat"
    )
    assert clear_event["inputs"] == [chat_event["inputs"][-1]]
    assert clear_event["outputs"] == chat_event["outputs"]
    assert {target[1] for target in clear_event["targets"]} == {"click", "clear"}
    functions = gradio_app.interface.fns
    assert (
        functions[clear_event["id"]].concurrency_id
        == functions[chat_event["id"]].concurrency_id
    )
    assert functions[chat_event["id"]].concurrency_limit == 1


def test_three_http_turns_reuse_session_and_previous_replies(gradio_app):
    client = browser(gradio_app)
    history = []
    session_ids = []
    for index in range(3):
        history, text, audio = send(client, f"turn {index}", history)
        assert text == ""
        assert history == [
            [f"turn {turn}", f"Reply to turn {turn}"] for turn in range(index + 1)
        ]
        assert audio is not None
        session_ids.append(tuple(gradio_app.app.chat_service.sessions))
    assert session_ids == [session_ids[0]] * 3
    assert len(session_ids[0]) == 1
    assert gradio_app.contexts[-1][1:] == [
        {"role": "user", "content": "turn 0"},
        {"role": "assistant", "content": "Reply to turn 0"},
        {"role": "user", "content": "turn 1"},
        {"role": "assistant", "content": "Reply to turn 1"},
        {"role": "user", "content": "turn 2"},
    ]


def test_two_browser_sessions_clear_independently(gradio_app):
    first, second = browser(gradio_app), browser(gradio_app)
    first_history, _, _ = send(first, "first browser")
    second_history, _, _ = send(second, "second browser")
    sessions = gradio_app.app.chat_service.sessions
    assert len(sessions) == 2
    first_id = next(
        session_id
        for session_id, session in sessions.items()
        if user_turns(session)[0]["content"] == "first browser"
    )
    second_id = next(session_id for session_id in sessions if session_id != first_id)
    second_before = user_turns(sessions[second_id])
    assert first.predict(api_name="/clear_chat") == ([], "", None)
    assert first_id not in sessions
    assert user_turns(sessions[second_id]) == second_before
    history, _, _ = send(first, "fresh conversation")
    assert history == [["fresh conversation", "Reply to fresh conversation"]]
    assert gradio_app.contexts[-1][1:] == [
        {"role": "user", "content": "fresh conversation"}
    ]
    history, _, _ = send(second, "keep conversation", second_history)
    assert history == second_history + [
        ["keep conversation", "Reply to keep conversation"]
    ]
    assert gradio_app.contexts[-1][1:3] == second_before


@pytest.mark.parametrize("has_successful_turn", [False, True])
def test_provider_failure_recovers_without_changing_other_browser(
    gradio_app, has_successful_turn
):
    first, second = browser(gradio_app), browser(gradio_app)
    second_history, _, _ = send(second, "unaffected")
    sessions = gradio_app.app.chat_service.sessions
    second_id = next(iter(sessions))
    second_before = user_turns(sessions[second_id])
    first_history, first_before = [], []
    if has_successful_turn:
        first_history, _, _ = send(first, "successful turn")
        first_id = next(
            session_id for session_id in sessions if session_id != second_id
        )
        first_before = user_turns(sessions[first_id])
    chat = gradio_app.llm.chat.side_effect
    gradio_app.llm.chat.side_effect = LLMError("offline provider unavailable")
    failed_history, _, failed_audio = send(first, "fail this request", first_history)
    assert failed_history[:-1] == first_history
    assert "fail this request" == failed_history[-1][0]
    assert "抱歉" in failed_history[-1][1]
    assert failed_audio is None
    first_id = next(session_id for session_id in sessions if session_id != second_id)
    assert len(sessions) == 2
    assert user_turns(sessions[first_id]) == first_before
    assert user_turns(sessions[second_id]) == second_before
    gradio_app.llm.chat.side_effect = chat
    recovered, _, _ = send(first, "try again", failed_history)
    assert recovered[-1] == ["try again", "Reply to try again"]
    assert gradio_app.contexts[-1][1:] == first_before + [
        {"role": "user", "content": "try again"}
    ]
    assert set(sessions) == {first_id, second_id}
    assert user_turns(sessions[second_id]) == second_before
    assert first.predict(api_name="/clear_chat") == ([], "", None)
    assert second_id in sessions
    assert second_history == [["unaffected", "Reply to unaffected"]]


def test_callback_exception_preserves_session_and_browser_isolation(
    gradio_app, monkeypatch
):
    first, second = browser(gradio_app), browser(gradio_app)
    first_history, _, _ = send(first, "first browser")
    second_history, _, _ = send(second, "second browser")
    service = gradio_app.app.chat_service
    ids_before = set(service.sessions)
    histories_before = {
        session_id: user_turns(session)
        for session_id, session in service.sessions.items()
    }
    process_chat = service.process_chat
    monkeypatch.setattr(
        service, "process_chat", Mock(side_effect=RuntimeError("callback failure"))
    )
    history, _, audio = send(first, "failed callback", first_history)
    assert history[:-1] == first_history
    assert history[-1][0] == "failed callback"
    assert "callback failure" in history[-1][1]
    assert audio is None
    assert set(service.sessions) == ids_before
    assert {
        session_id: user_turns(session)
        for session_id, session in service.sessions.items()
    } == histories_before
    monkeypatch.setattr(service, "process_chat", process_chat)
    recovered, _, _ = send(first, "recover callback", history)
    assert recovered[-1] == ["recover callback", "Reply to recover callback"]
    assert set(service.sessions) == ids_before
    first_id = next(
        session_id
        for session_id, messages in histories_before.items()
        if messages[0]["content"] == "first browser"
    )
    second_id = next(session_id for session_id in ids_before if session_id != first_id)
    assert user_turns(service.sessions[second_id]) == histories_before[second_id]
    assert gradio_app.contexts[-1][1:] == histories_before[first_id] + [
        {"role": "user", "content": "recover callback"}
    ]
    assert second_history == [["second browser", "Reply to second browser"]]


def test_clear_queued_during_slow_send_removes_completed_session(gradio_app):
    client = browser(gradio_app)
    started, release = threading.Event(), threading.Event()
    chat = gradio_app.llm.chat.side_effect

    def slow_chat(messages, **kwargs):
        started.set()
        if not release.wait(10):
            raise TimeoutError("slow provider was not released")
        return chat(messages, **kwargs)

    gradio_app.llm.chat.side_effect = slow_chat
    pending = client.submit("slow reply", [], "longxiaochun", api_name="/chat_response")
    clear = None
    try:
        assert started.wait(3)
        clear = client.submit(api_name="/clear_chat")
        chat_fn = next(
            fn
            for fn in gradio_app.interface.fns.values()
            if fn.api_name == "chat_response"
        )
        queue = gradio_app.interface._queue.event_queue_per_concurrency_id[
            chat_fn.concurrency_id
        ]
        # SSE estimates can be missed; observe the accepted server event directly.
        deadline = time.monotonic() + 5
        while not any(
            event.fn.api_name == "clear_chat"
            and event.session_hash == client.session_hash
            for event in tuple(queue.queue)
        ):
            assert time.monotonic() < deadline, "clear did not enter the server queue"
            assert not clear.done()
            release.wait(0.01)
        assert not pending.done()
        assert not clear.done()
    finally:
        release.set()
    history, _, _ = pending.result(timeout=8)
    assert history == [["slow reply", "Reply to slow reply"]]
    assert clear.result(timeout=8) == ([], "", None)
    assert not gradio_app.app.chat_service.sessions
    gradio_app.llm.chat.side_effect = chat
    history, _, _ = send(client, "after clearing")
    assert history == [["after clearing", "Reply to after clearing"]]
    assert gradio_app.contexts[-1][1:] == [
        {"role": "user", "content": "after clearing"}
    ]


def test_blank_message_and_clear_before_first_send_create_no_session(gradio_app):
    client = browser(gradio_app)
    assert send(client, "   ") == ([], "", None)
    assert client.predict(api_name="/clear_chat") == ([], "", None)
    assert not gradio_app.app.chat_service.sessions
    gradio_app.llm.chat.assert_not_called()


def test_deleted_session_is_replaced_and_context_starts_fresh(gradio_app):
    client = browser(gradio_app)
    history, _, _ = send(client, "old session")
    service = gradio_app.app.chat_service
    old_id = next(iter(service.sessions))
    service.delete_session(old_id)
    history, _, _ = send(client, "after expiry", history)
    assert len(service.sessions) == 1
    assert old_id not in service.sessions
    assert history == [["after expiry", "Reply to after expiry"]]
    assert gradio_app.contexts[-1][1:] == [{"role": "user", "content": "after expiry"}]


def test_http_audio_output_is_decodable_and_tts_failure_returns_text(gradio_app):
    client = browser(gradio_app)
    history, _, audio = send(client, "with audio")
    audio_url = audio["url"]
    response = httpx.get(audio_url)
    response.raise_for_status()
    with wave.open(io.BytesIO(response.content), "rb") as decoded:
        assert decoded.getframerate() == 16000
        assert decoded.getnframes() == 1600
    gradio_app.tts.synthesize.side_effect = RuntimeError("offline TTS unavailable")
    history, _, audio = send(client, "without audio", history)
    assert history[-1] == ["without audio", "Reply to without audio"]
    assert audio is None
    assert len(gradio_app.app.chat_service.sessions) == 1
