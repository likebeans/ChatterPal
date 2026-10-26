"""Legacy chat retention, active-session admission and request voice isolation."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from chatterpal.core.llm.base import LLMError
from chatterpal.services.chat import ChatService


def service(**config):
    return ChatService(
        llm=SimpleNamespace(chat=lambda messages, **kwargs: "reply"), config=config
    )


def test_retained_history_is_bounded_and_failed_turn_restores_committed_pairs():
    chat = service(max_history_length=5)
    session_id = chat.create_session(system_prompt="Keep this instruction")
    for turn in range(30):
        chat.chat_with_text(str(turn), session_id)
    session = chat.get_session(session_id)
    history = chat.get_conversation_history(session_id)
    assert len(session.conversation.messages) == 5
    assert history == [
        {"role": "system", "content": "Keep this instruction"},
        {"role": "user", "content": "28"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "29"},
        {"role": "assistant", "content": "reply"},
    ]
    chat.llm.chat = Mock(side_effect=RuntimeError("sensitive provider response"))
    with pytest.raises(LLMError):
        chat.chat_with_text("failed", session_id)
    assert chat.get_conversation_history(session_id) == history
    assert len(session.conversation.messages) <= chat.max_history_length


@pytest.mark.parametrize("limit", [3, 4, 5, 6, 20])
@pytest.mark.parametrize("prompt", ["instruction", ""])
def test_history_and_provider_context_keep_whole_rounds(limit, prompt):
    observed = []

    def reply(messages, **kwargs):
        observed.append(messages)
        return "reply"

    chat = ChatService(
        llm=SimpleNamespace(chat=reply), config={"max_history_length": limit}
    )
    session_id = chat.create_session(system_prompt=prompt)
    for turn in range(25):
        chat.chat_with_text(str(turn), session_id)
        retained = chat.get_conversation_history(session_id)
        assert len(retained) <= limit
        roles = [message["role"] for message in retained if message["role"] != "system"]
        assert roles == ["user", "assistant"] * (len(roles) // 2)
        context = [message for message in observed[-1] if message["role"] != "system"]
        assert len(observed[-1]) <= limit
        assert [message["role"] for message in context[:-1]] == [
            "user",
            "assistant",
        ] * ((len(context) - 1) // 2)
        assert context[-1] == {"role": "user", "content": str(turn)}
        if prompt:
            assert retained[0] == {"role": "system", "content": prompt}


def test_cancelled_llm_restores_history_and_releases_the_session_pin():
    import asyncio

    chat = service(max_history_length=3)
    session_id = chat.create_session()
    chat.chat_with_text("committed", session_id)
    history = chat.get_conversation_history(session_id)
    chat.llm.chat = Mock(side_effect=asyncio.CancelledError("private"))
    with pytest.raises(asyncio.CancelledError):
        chat.chat_with_text("cancelled", session_id)
    assert chat.get_conversation_history(session_id) == history
    assert chat.runtime_snapshot()["active_sessions"] == 0
    assert chat.runtime_snapshot()["metrics"]["stages"]["chat_text"]["statuses"] == {
        "succeeded": 1,
        "interrupted": 1,
    }


def test_audio_result_preserves_explicit_pcm_metadata_and_legacy_return_shape():
    tts = SimpleNamespace(
        synthesize_with_error_handling=Mock(
            return_value=SimpleNamespace(
                audio_data=b"\x00\x01" * 32,
                format="pcm",
                sample_rate=24000,
            )
        )
    )
    chat = ChatService(
        llm=SimpleNamespace(chat=lambda messages, **kwargs: "reply"),
        tts=tts,
        asr=SimpleNamespace(
            recognize_with_error_handling=lambda audio, **kwargs: SimpleNamespace(
                text="hello"
            )
        ),
    )
    result = chat.chat_with_audio_result(b"input", tts_options={"voice": "request"})
    assert result.response_audio_format == "pcm"
    assert result.response_audio_sample_rate == 24000
    assert result.response_audio == b"\x00\x01" * 32
    legacy = chat.chat_with_audio(b"input", result.session_id)
    assert legacy == ("reply", b"\x00\x01" * 32, result.session_id)
    from chatterpal.services.chat import AudioChatResult

    assert (
        AudioChatResult("hello", "reply", b"audio", "session").response_audio_format
        is None
    )


def test_expiry_and_idle_capacity_evict_without_unbounded_sessions():
    chat = service(max_sessions=2, session_timeout=10)
    expired = chat.create_session("expired")
    chat.get_session(expired).last_activity = datetime.now() - timedelta(seconds=11)
    assert chat.get_session(expired) is None
    for index in range(40):
        chat.chat_with_text("hello", f"unknown-{index}")
        assert len(chat.sessions) <= 2
    assert len(chat.sessions) == 2


def test_concurrent_session_admission_never_exceeds_capacity():
    chat = service(max_sessions=3)
    ready = Barrier(8)

    def create(index):
        ready.wait(timeout=3)
        return chat.create_session(f"session-{index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert len(list(pool.map(create, range(8)))) == 8
    assert len(chat.sessions) == 3


def test_active_and_waiting_turns_are_not_pruned_or_replaced():
    chat = service(max_sessions=1, session_timeout=1)
    session_id = chat.create_session("shared")
    original = chat.get_session(session_id)
    started, release, waiting = Event(), Event(), Event()

    def reply(messages, **kwargs):
        if messages[-1]["content"] == "first":
            started.set()
            assert release.wait(3)
        return "reply"

    def next_turn():
        waiting.set()
        return chat.chat_with_text("second", session_id)

    chat.llm.chat = reply
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(chat.chat_with_text, "first", session_id)
        try:
            assert started.wait(2)
            second = pool.submit(next_turn)
            assert waiting.wait(2)
            original.last_activity = datetime.now() - timedelta(seconds=10)
            assert chat.cleanup_expired_sessions() == 0
            assert chat.create_session(session_id) == session_id
            assert chat.get_session(session_id) is original
            with pytest.raises(LLMError):
                chat.create_session("would-evict-active")
            assert chat.delete_session(session_id) is False
            assert chat.clear_context() is False
            assert chat.get_session(session_id) is original
        finally:
            release.set()
        assert first.result(timeout=3) == ("reply", session_id)
        assert second.result(timeout=3) == ("reply", session_id)
    assert [
        message["content"]
        for message in chat.get_conversation_history(session_id)
        if message["role"] == "user"
    ] == ["first", "second"]
    assert chat.delete_session(session_id)


@pytest.mark.parametrize("stage", ["asr", "tts"])
def test_audio_session_stays_reserved_across_all_provider_stages(stage):
    chat = service(max_sessions=1, session_timeout=1)
    session_id = chat.create_session()
    session = chat.get_session(session_id)
    started, release = Event(), Event()

    def block(value):
        if stage == value:
            started.set()
            assert release.wait(3)

    def recognize(audio, **kwargs):
        block("asr")
        return SimpleNamespace(text="hello", confidence=1)

    def synthesize(text):
        block("tts")
        return b"audio"

    chat.asr = SimpleNamespace(recognize_with_error_handling=recognize)
    chat.tts = SimpleNamespace(synthesize=synthesize)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(chat.chat_with_audio_result, b"input", session_id)
        try:
            assert started.wait(2)
            session.last_activity = datetime.now() - timedelta(seconds=10)
            assert chat.cleanup_expired_sessions() == 0
            with pytest.raises(LLMError):
                chat.create_session()
        finally:
            release.set()
        assert future.result(timeout=3).session_id == session_id
    assert chat.runtime_snapshot()["active_sessions"] == 0


@pytest.mark.parametrize("enhanced", [False, True])
def test_tts_options_are_request_scoped_and_only_reach_synthesis(enhanced):
    asr = SimpleNamespace(
        recognize_with_error_handling=Mock(
            return_value=SimpleNamespace(text="hello", confidence=1)
        )
    )
    llm = SimpleNamespace(chat=Mock(return_value="reply"))
    tts = SimpleNamespace(synthesize=Mock(return_value=b"audio"), voice="default")
    if enhanced:
        tts.synthesize_with_error_handling = Mock(
            return_value=SimpleNamespace(
                audio_data=b"audio", synthesis_time=0, cached=False
            )
        )
    chat = ChatService(asr=asr, llm=llm, tts=tts)
    result = chat.chat_with_audio_result(
        b"input", tts_options={"voice": "request-voice"}
    )
    assert result.response_audio == b"audio"
    asr.recognize_with_error_handling.assert_called_once_with(b"input", max_retries=3)
    assert llm.chat.call_args.kwargs == {}
    if enhanced:
        tts.synthesize_with_error_handling.assert_called_once_with(
            "reply", max_retries=2, voice="request-voice"
        )
    else:
        tts.synthesize.assert_called_once_with("reply", voice="request-voice")
    assert tts.voice == "default"


def test_no_option_synthesis_keeps_legacy_mock_signatures():
    tts = SimpleNamespace(synthesize=Mock(return_value=b"audio"))
    chat = ChatService(tts=tts)
    assert chat._synthesize_with_error_handling("reply") == b"audio"
    tts.synthesize.assert_called_once_with("reply")


def test_pipeline_metrics_and_logs_do_not_include_provider_content(caplog):
    secret = "private transcript api-key and raw reply"
    chat = ChatService(
        asr=SimpleNamespace(
            recognize_with_error_handling=lambda audio, **kwargs: SimpleNamespace(
                text=secret, confidence=1
            )
        ),
        llm=SimpleNamespace(chat=lambda messages, **kwargs: secret),
        tts=SimpleNamespace(synthesize=lambda text: b"audio"),
    )
    caplog.set_level("DEBUG")
    chat.chat_with_audio(b"input")
    snapshot = chat.runtime_snapshot()
    assert secret not in str(snapshot)
    assert secret not in caplog.text
    for stage in ("asr", "llm", "tts", "chat_text", "chat_audio", "chat_queue"):
        assert snapshot["metrics"]["stages"][stage]["count"] == 1


@pytest.mark.parametrize(
    "option,value",
    [("max_sessions", 0), ("max_history_length", 2), ("session_timeout", 0)],
)
def test_invalid_retention_configuration_is_rejected(option, value):
    with pytest.raises(ValueError):
        service(**{option: value})
