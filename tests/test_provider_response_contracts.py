"""Offline response contracts through the real HTTP and SDK adapters."""

import io
import json
import wave
from types import SimpleNamespace
from unittest.mock import Mock, call

import httpx
import pytest
import requests
from openai import APIStatusError

from chatterpal.core.llm import alibaba as bailian_module
from chatterpal.core.llm import openai as openai_module
from chatterpal.core.llm.base import Conversation, LLMError, Message
from chatterpal.core.tts import alibaba as tts_module
from chatterpal.core.tts.base import TTSError, TTSResult


def http_response(payload=None, *, body=None, status=200):
    """Keep requests' real JSON, status and line parsing at the HTTP boundary."""
    response = requests.Response()
    response.status_code = status
    response.url = "https://provider.invalid/v1/chat/completions"
    response.encoding = "utf-8"
    response._content = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if body is None
        else body
    )
    response._content_consumed = True
    return response


def sse_response(*events):
    lines = [
        event if isinstance(event, bytes) else event.encode("utf-8")
        for event in events
    ]
    return http_response(body=b"\n\n".join(lines))


def sdk_completion(content="Reply", *, choices=None, usage=None):
    if choices is None:
        choices = [SimpleNamespace(message=SimpleNamespace(content=content))]
    return SimpleNamespace(choices=choices, usage=usage)


def sdk_chunk(content=None, *, choices=None):
    if choices is None:
        choices = [SimpleNamespace(delta=SimpleNamespace(content=content))]
    return SimpleNamespace(choices=choices)


def sdk_error(status, message="provider rejected request"):
    response = httpx.Response(
        status,
        request=httpx.Request("POST", "https://provider.invalid/v1/chat/completions"),
    )
    return APIStatusError(message, response=response, body={"message": message})


@pytest.fixture
def openai_http(monkeypatch):
    post = Mock(name="requests.post")
    get = Mock(name="requests.get")
    monkeypatch.setattr(openai_module.requests, "post", post)
    monkeypatch.setattr(openai_module.requests, "get", get)
    return SimpleNamespace(post=post, get=get)


@pytest.fixture
def bailian_sdk(monkeypatch):
    create = Mock(name="chat.completions.create")
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    constructor = Mock(name="OpenAI", return_value=client)
    monkeypatch.setattr(bailian_module, "OpenAI", constructor)
    return SimpleNamespace(constructor=constructor, create=create)


@pytest.fixture
def cosyvoice_sdk(monkeypatch, audio_bytes):
    synthesize = Mock(name="SpeechSynthesizer.call", return_value=audio_bytes)
    constructor = Mock(
        name="SpeechSynthesizer", return_value=SimpleNamespace(call=synthesize)
    )
    sdk = SimpleNamespace(api_key=None)
    monkeypatch.setattr(tts_module, "SpeechSynthesizer", constructor)
    monkeypatch.setattr(tts_module, "dashscope", sdk)
    return SimpleNamespace(constructor=constructor, synthesize=synthesize, sdk=sdk)


@pytest.fixture(params=["openai", "bailian"])
def llm_boundary(request, openai_http, bailian_sdk):
    if request.param == "openai":
        return (
            openai_module.OpenAILLM({"api_key": "offline-key"}),
            openai_http.post,
        )
    return (
        bailian_module.AlibabaBailianLLM({"api_key": "offline-key"}),
        bailian_sdk.create,
    )


@pytest.mark.parametrize("streaming", [False, True], ids=["chat", "stream"])
@pytest.mark.parametrize(
    "messages",
    [[], [{"role": "user", "content": 42}], None],
    ids=["empty", "nontext-content", "unsupported-input"],
)
def test_invalid_messages_fail_before_contacting_provider(
    llm_boundary, streaming, messages
):
    llm, boundary = llm_boundary
    with pytest.raises(LLMError, match="消息格式验证失败|不支持的消息格式"):
        if streaming:
            list(llm.chat_stream(messages))
        else:
            llm.chat(messages)
    boundary.assert_not_called()


def test_openai_chat_overrides_are_per_request_and_keep_exact_reply(openai_http):
    reply = "  你好。\nReady?  "
    openai_http.post.return_value = http_response(
        {
            "choices": [
                {"message": {"content": reply}},
                {"message": {"content": "unselected alternative"}},
            ],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }
    )
    config = {
        "api_key": "offline-key",
        "base_url": "https://gateway.invalid/custom/chat/completions",
        "model": "configured-model",
        "max_tokens": 321,
        "temperature": 0.2,
        "top_p": 0.9,
        "frequency_penalty": 0.3,
        "presence_penalty": -0.2,
        "timeout": 13,
    }
    llm = openai_module.OpenAILLM(config)
    conversation = Conversation("Answer in two languages.")
    conversation.add_user_message("Hello")
    overrides = {
        "model": "temporary-model",
        "max_tokens": 17,
        "temperature": 0,
        "top_p": 0.5,
        "frequency_penalty": -0.4,
        "presence_penalty": 0,
    }

    assert llm.chat(conversation, **overrides) == reply
    openai_http.post.assert_called_once_with(
        config["base_url"],
        headers={
            "Authorization": "Bearer offline-key",
            "Content-Type": "application/json",
        },
        json={"messages": conversation.get_messages(), **overrides},
        timeout=13,
    )
    assert conversation.get_messages() == [
        {"role": "system", "content": "Answer in two languages."},
        {"role": "user", "content": "Hello"},
    ]

    assert llm.chat([Message("user", "Again")]) == reply
    assert openai_http.post.call_args.kwargs["json"] == {
        "messages": [{"role": "user", "content": "Again"}],
        "model": "configured-model",
        "max_tokens": 321,
        "temperature": 0.2,
        "top_p": 0.9,
        "frequency_penalty": 0.3,
        "presence_penalty": -0.2,
    }


@pytest.mark.parametrize("choices", [None, []], ids=["absent", "empty"])
def test_openai_error_envelope_preserves_provider_type_and_message(
    openai_http, choices
):
    payload = {
        "error": {"type": "insufficient_quota", "message": "Quota exhausted"}
    }
    if choices is not None:
        payload["choices"] = choices
    openai_http.post.return_value = http_response(payload)
    llm = openai_module.OpenAILLM({"api_key": "offline-key"})

    with pytest.raises(
        LLMError, match=r"API调用失败 \[insufficient_quota\]: Quota exhausted"
    ):
        llm.chat("Hello")
    openai_http.post.assert_called_once()


@pytest.mark.parametrize("payload", [{}, {"choices": []}])
def test_openai_empty_response_is_an_explicit_api_failure(openai_http, payload):
    openai_http.post.return_value = http_response(payload)
    llm = openai_module.OpenAILLM({"api_key": "offline-key"})

    with pytest.raises(LLMError, match=r"API调用失败 \[UNKNOWN\]: 未知错误"):
        llm.chat("Hello")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"choices": None},
        {"choices": [{}]},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": 42}}]},
    ],
    ids=["array", "null-choices", "no-message", "no-content", "null", "integer"],
)
def test_openai_malformed_reply_becomes_llm_error(openai_http, payload):
    openai_http.post.return_value = http_response(payload)
    llm = openai_module.OpenAILLM({"api_key": "offline-key"})

    with pytest.raises(LLMError, match="对话异常"):
        llm.chat("Hello")
    openai_http.post.assert_called_once()


def test_openai_invalid_json_does_not_escape_as_a_decoder_error(openai_http):
    openai_http.post.return_value = http_response(body=b"<html>upstream failure</html>")
    llm = openai_module.OpenAILLM({"api_key": "offline-key"})

    with pytest.raises(LLMError, match="Expecting value"):
        llm.chat("Hello")
    openai_http.post.assert_called_once()


@pytest.mark.parametrize("streaming", [False, True], ids=["chat", "stream"])
@pytest.mark.parametrize("status", [401, 429, 503])
def test_openai_http_failure_precedes_response_parsing(openai_http, streaming, status):
    # Even a valid-looking body must never turn a failed HTTP request into text.
    openai_http.post.return_value = http_response(
        {"choices": [{"message": {"content": "do not emit"}}]}, status=status
    )
    llm = openai_module.OpenAILLM({"api_key": "offline-key"})

    with pytest.raises(LLMError, match=rf"请求失败.*{status}"):
        if streaming:
            list(llm.chat_stream("Hello"))
        else:
            llm.chat("Hello")
    openai_http.post.assert_called_once()


@pytest.mark.parametrize("streaming", [False, True], ids=["chat", "stream"])
def test_openai_transport_timeout_becomes_llm_error(openai_http, streaming):
    openai_http.post.side_effect = requests.exceptions.Timeout("upstream timed out")
    llm = openai_module.OpenAILLM({"api_key": "offline-key"})

    with pytest.raises(LLMError, match="请求失败: upstream timed out"):
        if streaming:
            list(llm.chat_stream("Hello"))
        else:
            llm.chat("Hello")
    openai_http.post.assert_called_once()


def test_openai_sse_skips_control_and_bad_json_and_stops_at_done(openai_http):
    openai_http.post.return_value = sse_response(
        b"",
        ": keep-alive",
        "event: message",
        "data: not-json",
        "data: {}",
        'data: {"choices": []}',
        'data: {"choices": [{"delta": {"role": "assistant"}}]}',
        'data: {"choices": [{"delta": {"content": ""}}]}',
        'data: {"choices": [{"delta": {"content": null}}]}',
        'data: {"choices": [{"delta": {"content": "你好"}}]}',
        'data: {"choices": [{"delta": {"content": " world"}}]}',
        "data: [DONE] ",
        'data: {"choices": [{"delta": {"content": "after completion"}}]}',
    )
    llm = openai_module.OpenAILLM({"api_key": "offline-key", "timeout": 9})
    stream = llm.chat_stream(
        [Message("system", "Be brief"), Message("user", "Hello")],
        model="stream-model",
        max_tokens=45,
        temperature=0,
        top_p=0.6,
        frequency_penalty=0.4,
        presence_penalty=-0.3,
    )
    openai_http.post.assert_not_called()

    assert list(stream) == ["你好", " world"]
    openai_http.post.assert_called_once_with(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": "Bearer offline-key",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        json={
            "model": "stream-model",
            "messages": [
                {"role": "system", "content": "Be brief"},
                {"role": "user", "content": "Hello"},
            ],
            "max_tokens": 45,
            "temperature": 0,
            "top_p": 0.6,
            "frequency_penalty": 0.4,
            "presence_penalty": -0.3,
            "stream": True,
        },
        timeout=9,
        stream=True,
    )


@pytest.mark.parametrize(
    "event",
    [
        b'data: {"choices": null}',
        b'data: {"choices": [null]}',
        b'data: {"choices": [{"delta": null}]}',
        b"data: \xff",
    ],
    ids=["null-choices", "null-choice", "null-delta", "invalid-utf8"],
)
def test_openai_invalid_stream_structure_raises_after_delivered_prefix(
    openai_http, event
):
    openai_http.post.return_value = sse_response(
        'data: {"choices": [{"delta": {"content": "prefix"}}]}', event
    )
    llm = openai_module.OpenAILLM({"api_key": "offline-key"})
    stream = llm.chat_stream("Hello")

    assert next(stream) == "prefix"
    with pytest.raises(LLMError, match="流式对话异常"):
        next(stream)
    openai_http.post.assert_called_once()


def test_openai_stream_transport_failure_does_not_replay_delivered_text(openai_http):
    def interrupted_lines():
        yield b'data: {"choices": [{"delta": {"content": "prefix"}}]}'
        raise requests.exceptions.ChunkedEncodingError("truncated SSE body")

    response = http_response({})
    response.iter_lines = Mock(return_value=interrupted_lines())
    openai_http.post.return_value = response
    llm = openai_module.OpenAILLM({"api_key": "offline-key"})
    stream = llm.chat_stream("Hello")

    assert next(stream) == "prefix"
    with pytest.raises(LLMError, match="流式请求失败: truncated SSE body"):
        next(stream)
    openai_http.post.assert_called_once()


def test_openai_stream_error_event_is_not_a_successful_empty_reply(openai_http):
    openai_http.post.return_value = sse_response(
        'data: {"error": {"type": "server_error", "message": "Inference failed"}}',
        "data: [DONE]",
    )
    llm = openai_module.OpenAILLM({"api_key": "offline-key"})

    with pytest.raises(LLMError, match="Inference failed"):
        list(llm.chat_stream("Hello"))


@pytest.mark.parametrize(
    ("error", "message"),
    [
        ({"message": "服务暂时不可用", "type": "server_error"}, "服务暂时不可用"),
        ("provider rejected request", "provider rejected request"),
        ({"message": None, "type": None}, "API调用失败 \\[UNKNOWN\\]: 未知错误"),
        (None, "API调用失败 \\[UNKNOWN\\]: 未知错误"),
    ],
    ids=["unicode-message", "text-error", "missing-message", "null-error"],
)
def test_openai_stream_error_after_text_preserves_message_without_retry(
    openai_http, error, message
):
    openai_http.post.return_value = sse_response(
        'data: {"choices": [{"delta": {"content": "prefix"}}]}',
        "data: " + json.dumps({"error": error}, ensure_ascii=False),
        'data: {"choices": [{"delta": {"content": "must not emit"}}]}',
    )
    llm = openai_module.OpenAILLM({"api_key": "offline-key"})
    stream = llm.chat_stream("Hello")

    assert next(stream) == "prefix"
    with pytest.raises(LLMError, match=message):
        next(stream)
    openai_http.post.assert_called_once()


def test_openai_model_listing_preserves_records_and_uses_configured_gateway(
    openai_http,
):
    models = [{"id": "model-a", "owned_by": "offline"}, {"id": "model-b"}]
    openai_http.get.return_value = http_response({"data": models})
    llm = openai_module.OpenAILLM(
        {
            "api_key": "offline-key",
            "base_url": "https://gateway.invalid/custom/chat/completions",
            "timeout": 7,
        }
    )

    assert llm.list_models() == models
    openai_http.get.assert_called_once_with(
        "https://gateway.invalid/custom/models",
        headers={
            "Authorization": "Bearer offline-key",
            "Content-Type": "application/json",
        },
        timeout=7,
    )
    openai_http.post.assert_not_called()


@pytest.mark.parametrize("failure", ["no-data", "http", "json", "transport"])
def test_openai_model_listing_failure_returns_empty_catalog(openai_http, failure):
    openai_http.get.return_value = http_response({})
    if failure == "http":
        openai_http.get.return_value = http_response({"data": ["ignored"]}, status=503)
    elif failure == "json":
        openai_http.get.return_value = http_response(body=b"not json")
    elif failure == "transport":
        openai_http.get.side_effect = requests.exceptions.ConnectionError("offline")
    llm = openai_module.OpenAILLM({"api_key": "offline-key"})

    assert llm.list_models() == []
    openai_http.get.assert_called_once()


@pytest.mark.parametrize(
    ("content", "expected"), [("Ready", True), ("", False), (" \n\t ", False)]
)
def test_openai_connection_probe_requires_visible_text(openai_http, content, expected):
    openai_http.post.return_value = http_response(
        {"choices": [{"message": {"content": content}}]}
    )
    llm = openai_module.OpenAILLM({"api_key": "offline-key"})

    assert llm.test_connection() is expected
    openai_http.post.assert_called_once()


def test_openai_connection_probe_reports_transport_failure(openai_http):
    openai_http.post.side_effect = requests.exceptions.ConnectionError("offline")
    llm = openai_module.OpenAILLM({"api_key": "offline-key"})

    assert llm.test_connection() is False
    openai_http.post.assert_called_once()


@pytest.mark.parametrize("streaming", [False, True], ids=["chat", "stream"])
def test_bailian_request_overrides_preserve_config_and_search_options(
    bailian_sdk, streaming
):
    if streaming:
        bailian_sdk.create.side_effect = [
            iter([sdk_chunk("临时回复")]), iter([sdk_chunk("默认回复")])
        ]
    else:
        bailian_sdk.create.side_effect = [
            sdk_completion("临时回复"), sdk_completion("默认回复")
        ]
    config = {
        "api_key": "offline-key",
        "base_url": "https://bailian-gateway.invalid/v1",
        "timeout": 12,
        "model": "qwen-configured",
        "max_tokens": 512,
        "temperature": 0.4,
        "top_p": 0.75,
        "enable_search": True,
        "stream_options": {"include_usage": False},
    }
    llm = bailian_module.AlibabaBailianLLM(config)
    conversation = Conversation("Be brief")
    conversation.add_user_message("Hello")
    invoke = (
        (lambda messages, **kwargs: "".join(llm.chat_stream(messages, **kwargs)))
        if streaming else llm.chat
    )

    assert invoke(
        conversation,
        model="qwen-temporary",
        max_tokens=19,
        temperature=0,
        top_p=0.3,
        enable_search=False,
    ) == "临时回复"
    expected = {
        "model": "qwen-temporary",
        "messages": conversation.get_messages(),
        "max_tokens": 19,
        "temperature": 0,
        "top_p": 0.3,
    }
    if streaming:
        expected.update(stream=True, stream_options={"include_usage": False})
    bailian_sdk.create.assert_called_once_with(**expected)
    bailian_sdk.constructor.assert_called_once_with(
        api_key="offline-key", base_url=config["base_url"], timeout=12
    )

    assert invoke("Again") == "默认回复"
    expected.update(
        model="qwen-configured",
        messages=[{"role": "user", "content": "Again"}],
        max_tokens=512,
        temperature=0.4,
        top_p=0.75,
        extra_body={"enable_search": True},
    )
    assert bailian_sdk.create.call_args.kwargs == expected
    assert conversation.get_messages() == [
        {"role": "system", "content": "Be brief"},
        {"role": "user", "content": "Hello"},
    ]


def test_bailian_reply_uses_first_choice_without_altering_text_when_usage_exists(
    bailian_sdk
):
    reply = "  你好。\nReady?  "
    completion = sdk_completion(
        reply,
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4, total_tokens=14),
    )
    completion.choices.append(SimpleNamespace(message=SimpleNamespace(content="other")))
    bailian_sdk.create.return_value = completion
    llm = bailian_module.AlibabaBailianLLM({"api_key": "offline-key"})

    assert llm.chat([Message("user", "Hello")], enable_search=True) == reply
    assert bailian_sdk.create.call_args.kwargs["extra_body"] == {"enable_search": True}
    assert bailian_sdk.create.call_args.kwargs["messages"] == [
        {"role": "user", "content": "Hello"}
    ]


def test_bailian_stream_ignores_usage_and_role_chunks_and_keeps_text_order(bailian_sdk):
    usage_only = sdk_chunk(choices=[])
    usage_only.usage = SimpleNamespace(total_tokens=15)
    first = sdk_chunk("你好")
    first.choices.append(SimpleNamespace(delta=SimpleNamespace(content="unselected")))
    bailian_sdk.create.return_value = iter(
        [sdk_chunk(None), sdk_chunk(""), usage_only, first, sdk_chunk(" world")]
    )
    llm = bailian_module.AlibabaBailianLLM({"api_key": "offline-key"})
    stream = llm.chat_stream("Hello")
    bailian_sdk.create.assert_not_called()

    assert list(stream) == ["你好", " world"]
    bailian_sdk.create.assert_called_once_with(
        model="qwen-plus",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=2000,
        temperature=0.7,
        top_p=0.8,
        stream=True,
        stream_options={"include_usage": True},
    )


@pytest.mark.parametrize("chunks", [[], [sdk_chunk(None), sdk_chunk(choices=[])]])
def test_bailian_stream_without_content_does_not_invent_text(bailian_sdk, chunks):
    bailian_sdk.create.return_value = iter(chunks)
    llm = bailian_module.AlibabaBailianLLM({"api_key": "offline-key"})

    assert list(llm.chat_stream("Hello")) == []
    bailian_sdk.create.assert_called_once()


@pytest.mark.parametrize("streaming", [False, True], ids=["chat", "stream"])
@pytest.mark.parametrize(
    ("status", "message"),
    [
        (400, "provider rejected request"),
        (401, "API密钥无效或已过期"),
        (429, "API调用频率超限，请稍后重试"),
        (500, "服务器内部错误，请稍后重试"),
        (503, "服务器内部错误，请稍后重试"),
    ],
)
def test_bailian_sdk_status_errors_become_actionable_llm_errors(
    bailian_sdk, streaming, status, message
):
    bailian_sdk.create.side_effect = sdk_error(status)
    llm = bailian_module.AlibabaBailianLLM({"api_key": "offline-key"})

    with pytest.raises(LLMError, match=message):
        if streaming:
            list(llm.chat_stream("Hello"))
        else:
            llm.chat("Hello")
    bailian_sdk.create.assert_called_once()


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (sdk_error(429), "API调用频率超限，请稍后重试"),
        (RuntimeError("stream interrupted"), "流式对话异常: stream interrupted"),
    ],
    ids=["rate-limit", "transport"],
)
def test_bailian_error_during_iteration_keeps_prefix_and_does_not_restart(
    bailian_sdk, error, message
):
    def interrupted_stream():
        yield sdk_chunk("prefix")
        raise error

    bailian_sdk.create.return_value = interrupted_stream()
    llm = bailian_module.AlibabaBailianLLM({"api_key": "offline-key"})
    stream = llm.chat_stream("Hello")

    assert next(stream) == "prefix"
    with pytest.raises(LLMError, match=message):
        next(stream)
    bailian_sdk.create.assert_called_once()


@pytest.mark.parametrize("choices", [[], None], ids=["empty", "null"])
def test_bailian_empty_choices_are_an_explicit_failure(bailian_sdk, choices):
    bailian_sdk.create.return_value = SimpleNamespace(choices=choices, usage=None)
    llm = bailian_module.AlibabaBailianLLM({"api_key": "offline-key"})

    with pytest.raises(LLMError, match="API返回空的回复"):
        llm.chat("Hello")
    bailian_sdk.create.assert_called_once()


@pytest.mark.parametrize(
    "completion",
    [SimpleNamespace(), sdk_completion(None), sdk_completion(42)],
    ids=["missing-choices", "null-content", "nontext-content"],
)
def test_bailian_malformed_completion_becomes_llm_error(bailian_sdk, completion):
    bailian_sdk.create.return_value = completion
    llm = bailian_module.AlibabaBailianLLM({"api_key": "offline-key"})

    with pytest.raises(LLMError, match="对话异常"):
        llm.chat("Hello")
    bailian_sdk.create.assert_called_once()


@pytest.mark.parametrize(
    "chunk",
    [SimpleNamespace(), SimpleNamespace(choices=[SimpleNamespace(delta=None)])],
    ids=["missing-choices", "null-delta"],
)
def test_bailian_malformed_stream_chunk_becomes_llm_error(bailian_sdk, chunk):
    bailian_sdk.create.return_value = iter([sdk_chunk("prefix"), chunk])
    llm = bailian_module.AlibabaBailianLLM({"api_key": "offline-key"})
    stream = llm.chat_stream("Hello")

    assert next(stream) == "prefix"
    with pytest.raises(LLMError, match="流式对话异常"):
        next(stream)
    bailian_sdk.create.assert_called_once()


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (sdk_completion("Ready"), True),
        (sdk_completion(" \n\t "), False),
        (sdk_completion(""), False),
        (sdk_completion(None), False),
        (sdk_completion(choices=[]), False),
        (RuntimeError("offline"), False),
    ],
    ids=["visible", "whitespace", "empty", "null", "no-choices", "provider-error"],
)
def test_bailian_connection_probe_requires_visible_text(bailian_sdk, reply, expected):
    if isinstance(reply, Exception):
        bailian_sdk.create.side_effect = reply
    else:
        bailian_sdk.create.return_value = reply
    llm = bailian_module.AlibabaBailianLLM(
        {"api_key": "offline-key", "model": "probe-model"}
    )

    assert llm.test_connection() is expected
    bailian_sdk.create.assert_called_once_with(
        model="probe-model",
        messages=[
            {"role": "user", "content": "Hello, please reply 'Test successful'."}
        ],
        max_tokens=10,
        temperature=0.1,
    )


@pytest.mark.parametrize("provider", ["openai", "bailian"])
def test_convenience_chat_keeps_the_real_provider_response_path(
    provider, openai_http, bailian_sdk
):
    if provider == "openai":
        openai_http.post.return_value = http_response(
            {"choices": [{"message": {"content": "wrapper reply"}}]}
        )
        result = openai_module.openai_chat("Hello", "offline-key", "wrapper-model")
        payload = openai_http.post.call_args.kwargs["json"]
    else:
        bailian_sdk.create.return_value = sdk_completion("wrapper reply")
        result = bailian_module.alibaba_chat("Hello", "offline-key", "wrapper-model")
        payload = bailian_sdk.create.call_args.kwargs

    assert result == "wrapper reply"
    assert payload["model"] == "wrapper-model"
    assert payload["messages"] == [{"role": "user", "content": "Hello"}]


def test_cosyvoice_model_and_voice_overrides_do_not_change_next_request(
    cosyvoice_sdk, audio_bytes
):
    tts = tts_module.AlibabaTTS(
        {
            "api_key": "offline-key",
            "voice": "cosyvoice-configured",
            "voice_name": "longwan",
        }
    )

    assert tts.synthesize(
        "你好", voice="cosyvoice-temporary", voice_name="longfei"
    ) == audio_bytes
    assert cosyvoice_sdk.constructor.call_args.kwargs["model"] == "cosyvoice-temporary"
    assert cosyvoice_sdk.constructor.call_args.kwargs["voice"] == "longfei"
    assert tts.synthesize("Again") == audio_bytes
    assert cosyvoice_sdk.constructor.call_args.kwargs["model"] == "cosyvoice-configured"
    assert cosyvoice_sdk.constructor.call_args.kwargs["voice"] == "longwan"
    assert cosyvoice_sdk.constructor.call_count == 2
    assert cosyvoice_sdk.synthesize.call_args_list == [call("你好"), call("Again")]
    assert cosyvoice_sdk.sdk.api_key == "offline-key"


@pytest.mark.parametrize("streaming", [False, True], ids=["synthesize", "stream"])
def test_cosyvoice_bytearray_response_becomes_independent_bytes(
    cosyvoice_sdk, audio_bytes, streaming
):
    provider_buffer = bytearray(audio_bytes)
    cosyvoice_sdk.synthesize.return_value = provider_buffer
    tts = tts_module.AlibabaTTS({"api_key": "offline-key"})

    if streaming:
        chunks = list(tts.synthesize_stream("Hello"))
        assert all(type(chunk) is bytes for chunk in chunks)
        audio = b"".join(chunks)
    else:
        audio = tts.synthesize("Hello")
    assert type(audio) is bytes
    assert audio == audio_bytes
    provider_buffer[:] = b"changed by SDK"
    assert audio == audio_bytes
    cosyvoice_sdk.synthesize.assert_called_once_with("Hello")


@pytest.mark.parametrize("audio", [None, b""], ids=["null", "empty"])
def test_cosyvoice_empty_audio_is_not_success(cosyvoice_sdk, audio):
    cosyvoice_sdk.synthesize.return_value = audio
    tts = tts_module.AlibabaTTS({"api_key": "offline-key"})

    with pytest.raises(TTSError, match="合成失败，无音频数据"):
        tts.synthesize("Hello")
    cosyvoice_sdk.synthesize.assert_called_once_with("Hello")


@pytest.mark.parametrize(
    "audio", ["not binary audio", {"error": "not audio"}], ids=["text", "mapping"]
)
def test_cosyvoice_nonbinary_response_is_not_audio(cosyvoice_sdk, audio):
    cosyvoice_sdk.synthesize.return_value = audio
    tts = tts_module.AlibabaTTS({"api_key": "offline-key"})

    with pytest.raises(TTSError, match="音频|audio"):
        tts.synthesize("Hello")
    cosyvoice_sdk.synthesize.assert_called_once_with("Hello")


@pytest.mark.parametrize("stage", ["constructor", "call"])
def test_cosyvoice_sdk_errors_become_tts_errors(cosyvoice_sdk, stage):
    boundary = (
        cosyvoice_sdk.constructor
        if stage == "constructor"
        else cosyvoice_sdk.synthesize
    )
    boundary.side_effect = RuntimeError("provider unavailable")
    tts = tts_module.AlibabaTTS({"api_key": "offline-key"})

    with pytest.raises(TTSError, match="语音合成失败: provider unavailable"):
        tts.synthesize("Hello")
    cosyvoice_sdk.constructor.assert_called_once()
    assert cosyvoice_sdk.synthesize.call_count == (0 if stage == "constructor" else 1)


@pytest.mark.parametrize("size", [1, 4095, 4096, 4097, 8192, 8193])
def test_cosyvoice_stream_chunks_preserve_every_audio_byte(cosyvoice_sdk, size):
    audio = (bytes(range(256)) * ((size + 255) // 256))[:size]
    cosyvoice_sdk.synthesize.return_value = audio
    tts = tts_module.AlibabaTTS({"api_key": "offline-key"})
    stream = tts.synthesize_stream("Hello", voice_name="longmiao")
    cosyvoice_sdk.constructor.assert_not_called()
    cosyvoice_sdk.synthesize.assert_not_called()

    chunks = list(stream)
    assert b"".join(chunks) == audio
    assert all(isinstance(chunk, bytes) and 0 < len(chunk) <= 4096 for chunk in chunks)
    assert len(chunks) == (size + 4095) // 4096
    assert len(chunks[-1]) == ((size - 1) % 4096) + 1
    cosyvoice_sdk.synthesize.assert_called_once_with("Hello")
    assert cosyvoice_sdk.constructor.call_args.kwargs["voice"] == "longmiao"


@pytest.mark.parametrize("failure", [b"", RuntimeError("stream unavailable")])
def test_cosyvoice_stream_failure_is_an_error_without_audio(cosyvoice_sdk, failure):
    if isinstance(failure, Exception):
        cosyvoice_sdk.synthesize.side_effect = failure
        message = "stream unavailable"
    else:
        cosyvoice_sdk.synthesize.return_value = failure
        message = "无音频数据"
    tts = tts_module.AlibabaTTS({"api_key": "offline-key"})

    with pytest.raises(TTSError, match=rf"流式语音合成失败.*{message}"):
        list(tts.synthesize_stream("Hello"))
    cosyvoice_sdk.synthesize.assert_called_once_with("Hello")


def test_cosyvoice_retry_recovers_empty_and_failed_calls_with_result_metadata(
    cosyvoice_sdk, audio_bytes
):
    cosyvoice_sdk.synthesize.side_effect = [
        b"", RuntimeError("transient SDK failure"), audio_bytes,
        RuntimeError("must not retry after success"),
    ]
    tts = tts_module.AlibabaTTS(
        {"api_key": "offline-key", "format": "wav", "sample_rate": 16000}
    )

    result = tts.synthesize_with_error_handling(
        "Retry this", max_retries=3, voice="cosyvoice-v1", voice_name="longmiao"
    )

    assert isinstance(result, TTSResult)
    assert result.text == "Retry this"
    assert result.audio_data == audio_bytes
    assert result.duration == pytest.approx(1.0)
    assert result.format == "wav"
    assert result.sample_rate == 16000
    assert result.cached is False
    assert result.synthesis_time >= 0
    expected_metadata = {
        "voice_name": "longmiao",
        "model": "cosyvoice-v1",
        "volume": 50,
        "speech_rate": 1.0,
        "pitch_rate": 1.0,
        "attempt": 3,
        "original_text": "Retry this",
        "text_length": len("Retry this"),
    }
    assert {key: result.metadata[key] for key in expected_metadata} == expected_metadata
    assert cosyvoice_sdk.synthesize.call_args_list == [call("Retry this")] * 3
    assert cosyvoice_sdk.constructor.call_count == 3
    assert all(
        entry.kwargs["model"] == "cosyvoice-v1"
        and entry.kwargs["voice"] == "longmiao"
        for entry in cosyvoice_sdk.constructor.call_args_list
    )


@pytest.mark.parametrize("attempts", [1, 2])
def test_cosyvoice_retry_exhaustion_stops_at_limit_and_keeps_last_error(
    cosyvoice_sdk, attempts
):
    cosyvoice_sdk.synthesize.side_effect = [
        RuntimeError(f"failure {index}") for index in range(1, attempts + 2)
    ]
    tts = tts_module.AlibabaTTS({"api_key": "offline-key"})

    with pytest.raises(
        TTSError, match=rf"已重试 {attempts} 次:.*failure {attempts}$"
    ) as caught:
        tts.synthesize_with_error_handling("Hello", max_retries=attempts)
    assert caught.value.metadata == {
        "error_code": "TTS_SERVICE_ERROR",
        "attempt": attempts,
        "error_message": f"语音合成失败: failure {attempts}",
    }
    assert cosyvoice_sdk.synthesize.call_args_list == [call("Hello")] * attempts
    assert cosyvoice_sdk.constructor.call_count == attempts


@pytest.mark.parametrize("sample_rate", [8000, 22050])
def test_cosyvoice_raw_pcm_duration_uses_configured_rate(cosyvoice_sdk, sample_rate):
    audio = b"\x00\x01" * sample_rate
    cosyvoice_sdk.synthesize.return_value = audio
    tts = tts_module.AlibabaTTS(
        {"api_key": "offline-key", "format": "pcm", "sample_rate": sample_rate}
    )

    result = tts.synthesize_with_error_handling("One second", max_retries=1)

    assert result.audio_data == audio
    assert result.duration == pytest.approx(1.0)
    assert result.format == "pcm"
    assert result.sample_rate == sample_rate
    cosyvoice_sdk.synthesize.assert_called_once_with("One second")


def test_cosyvoice_wav_duration_uses_provider_header_instead_of_config(cosyvoice_sdk):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(1)
        output.setframerate(8000)
        output.writeframes(b"\x80" * 4000)
    audio = buffer.getvalue()
    cosyvoice_sdk.synthesize.return_value = audio
    tts = tts_module.AlibabaTTS({"api_key": "offline-key", "sample_rate": 22050})

    result = tts.synthesize_with_error_handling("Half a second", max_retries=1)

    assert result.audio_data == audio
    assert result.duration == pytest.approx(0.5)
    cosyvoice_sdk.synthesize.assert_called_once_with("Half a second")


def test_cosyvoice_file_synthesis_writes_exact_sdk_bytes(
    cosyvoice_sdk, tmp_path, audio_bytes
):
    tts = tts_module.AlibabaTTS({"api_key": "offline-key"})
    destination = tmp_path / "speech.wav"

    assert tts.synthesize_to_file(
        "Save this", str(destination), voice_name="longfei"
    ) is True
    assert destination.read_bytes() == audio_bytes
    cosyvoice_sdk.synthesize.assert_called_once_with("Save this")
    assert cosyvoice_sdk.constructor.call_args.kwargs["voice"] == "longfei"


@pytest.mark.parametrize("failure", [b"", RuntimeError("synthesis failed")])
def test_cosyvoice_failed_synthesis_preserves_existing_output(
    cosyvoice_sdk, tmp_path, failure
):
    if isinstance(failure, Exception):
        cosyvoice_sdk.synthesize.side_effect = failure
        message = "synthesis failed"
    else:
        cosyvoice_sdk.synthesize.return_value = failure
        message = "无音频数据"
    destination = tmp_path / "speech.wav"
    destination.write_bytes(b"existing audio")
    tts = tts_module.AlibabaTTS({"api_key": "offline-key"})

    with pytest.raises(TTSError, match=rf"保存音频文件失败.*{message}"):
        tts.synthesize_to_file("Hello", str(destination))
    assert destination.read_bytes() == b"existing audio"
    cosyvoice_sdk.synthesize.assert_called_once_with("Hello")


def test_cosyvoice_file_write_failure_is_not_success(cosyvoice_sdk, tmp_path):
    destination = tmp_path / "absent-directory" / "speech.wav"
    tts = tts_module.AlibabaTTS({"api_key": "offline-key"})

    with pytest.raises(TTSError, match="保存音频文件失败"):
        tts.synthesize_to_file("Hello", str(destination))
    assert not destination.exists()
    cosyvoice_sdk.synthesize.assert_called_once_with("Hello")


@pytest.mark.parametrize(
    ("response", "expected"),
    [(b"audio", True), (b"", False), (None, False), (RuntimeError("offline"), False)],
    ids=["audio", "empty", "null", "provider-error"],
)
def test_cosyvoice_connection_probe_requires_audio(cosyvoice_sdk, response, expected):
    if isinstance(response, Exception):
        cosyvoice_sdk.synthesize.side_effect = response
    else:
        cosyvoice_sdk.synthesize.return_value = response
    tts = tts_module.AlibabaTTS({"api_key": "offline-key"})

    assert tts.test_connection() is expected
    cosyvoice_sdk.synthesize.assert_called_once_with("Hello, this is a test.")


def test_cosyvoice_voice_catalog_and_unknown_voice_metadata_are_consistent(
    cosyvoice_sdk,
):
    tts = tts_module.AlibabaTTS({"api_key": "offline-key", "voice_name": "longfei"})
    voices = tts.get_supported_voices()

    assert "longxiaochun" in voices
    assert "longfei" in voices
    assert len(voices) == len(set(voices))
    for voice in voices:
        info = tts.get_voice_info(voice)
        assert info["name"] == voice
        assert info["language"] == "zh-CN"
        assert info["gender"] in {"male", "female", "child"}
        assert info["display_name"]
        assert info["description"]
    assert tts.get_voice_info("custom-unlisted-voice") == {
        "name": "custom-unlisted-voice",
        "language": "unknown",
        "gender": "unknown",
        "description": "未知语音",
    }
    info = tts.get_service_info()
    assert info["voice"] == "longfei"
    assert info["model"] == "cosyvoice-v1"
    assert info["supported_voices"] == voices
    cosyvoice_sdk.constructor.assert_not_called()
    cosyvoice_sdk.synthesize.assert_not_called()


def test_cosyvoice_factory_preserves_requested_voice(cosyvoice_sdk, audio_bytes):
    tts = tts_module.create_alibaba_tts(
        "offline-key", voice_name="longtong", voice="cosyvoice-v1"
    )

    assert tts.synthesize("童声") == audio_bytes
    assert cosyvoice_sdk.constructor.call_args.kwargs["model"] == "cosyvoice-v1"
    assert cosyvoice_sdk.constructor.call_args.kwargs["voice"] == "longtong"
    cosyvoice_sdk.synthesize.assert_called_once_with("童声")
