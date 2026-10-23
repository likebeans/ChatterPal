"""
Tests for LLM (Large Language Model) modules.
"""

import pytest
from unittest.mock import patch

from chatterpal.core.llm.base import (
    LLMBase, LLMError, Message, Conversation
)
from chatterpal.core.llm.alibaba import AlibabaDashScopeLLM
from chatterpal.core.llm.openai import OpenAILLM


class MockLLM(LLMBase):
    """Mock LLM implementation for testing base class functionality."""
    
    def __init__(self, config=None, should_fail=False):
        super().__init__(config)
        self.should_fail = should_fail
        self.chat_calls = []
        
    def chat(self, messages, **kwargs):
        self.chat_calls.append((messages, kwargs))
        if self.should_fail:
            raise LLMError("Mock LLM failure")
        return "Mock response"


class TestMessage:
    """Test the Message class."""
    
    def test_initialization(self):
        """Test Message initialization."""
        msg = Message("user", "Hello world")
        assert msg.role == "user"
        assert msg.content == "Hello world"
        assert msg.metadata == {}
        
        msg_with_metadata = Message("assistant", "Hi there", timestamp=123456)
        assert msg_with_metadata.metadata == {"timestamp": 123456}
    
    def test_to_dict(self):
        """Test Message to_dict conversion."""
        msg = Message("user", "Hello", timestamp=123456)
        result = msg.to_dict()
        
        expected = {
            "role": "user",
            "content": "Hello",
            "timestamp": 123456
        }
        assert result == expected
    
    def test_from_dict(self):
        """Test Message from_dict creation."""
        data = {
            "role": "assistant",
            "content": "Hi there",
            "timestamp": 123456
        }
        msg = Message.from_dict(data)
        
        assert msg.role == "assistant"
        assert msg.content == "Hi there"
        assert msg.metadata == {"timestamp": 123456}
    
    def test_from_dict_minimal(self):
        """Test Message from_dict with minimal data."""
        data = {"role": "user", "content": "Hello"}
        msg = Message.from_dict(data)
        
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.metadata == {}
    
    def test_from_dict_missing_fields(self):
        """Test Message from_dict with missing fields."""
        data = {"content": "Hello"}
        msg = Message.from_dict(data)
        
        assert msg.role == "user"  # Default role
        assert msg.content == "Hello"
    
    def test_str_representation(self):
        """Test Message string representation."""
        msg = Message("user", "Hello world")
        assert str(msg) == "user: Hello world"


class TestConversation:
    """Test the Conversation class."""
    
    def test_initialization_empty(self):
        """Test Conversation initialization without system prompt."""
        conv = Conversation()
        assert len(conv) == 0
        assert conv.messages == []
    
    def test_initialization_with_system_prompt(self):
        """Test Conversation initialization with system prompt."""
        conv = Conversation("You are a helpful assistant.")
        assert len(conv) == 1
        assert conv.messages[0].role == "system"
        assert conv.messages[0].content == "You are a helpful assistant."
    
    def test_add_message(self):
        """Test adding messages to conversation."""
        conv = Conversation()
        
        conv.add_message("user", "Hello")
        assert len(conv) == 1
        assert conv.messages[0].role == "user"
        assert conv.messages[0].content == "Hello"
    
    def test_add_user_message(self):
        """Test adding user message."""
        conv = Conversation()
        conv.add_user_message("Hello world")
        
        assert len(conv) == 1
        assert conv.messages[0].role == "user"
        assert conv.messages[0].content == "Hello world"
    
    def test_add_assistant_message(self):
        """Test adding assistant message."""
        conv = Conversation()
        conv.add_assistant_message("Hi there!")
        
        assert len(conv) == 1
        assert conv.messages[0].role == "assistant"
        assert conv.messages[0].content == "Hi there!"
    
    def test_add_system_message(self):
        """Test adding system message."""
        conv = Conversation()
        conv.add_system_message("You are helpful.")
        
        assert len(conv) == 1
        assert conv.messages[0].role == "system"
        assert conv.messages[0].content == "You are helpful."
    
    def test_get_messages_no_limit(self):
        """Test getting all messages without limit."""
        conv = Conversation()
        conv.add_user_message("Hello")
        conv.add_assistant_message("Hi")
        conv.add_user_message("How are you")
        
        messages = conv.get_messages()
        assert len(messages) == 3
        assert all(isinstance(msg, dict) for msg in messages)
    
    def test_get_messages_with_limit(self):
        """Test getting messages with limit."""
        conv = Conversation("System prompt")
        conv.add_user_message("Hello")
        conv.add_assistant_message("Hi")
        conv.add_user_message("How are you")
        conv.add_assistant_message("I'm good")
        
        # Should keep system message + 2 most recent messages
        messages = conv.get_messages(limit=3)
        assert len(messages) == 3
        
        # Check that system message is preserved
        system_messages = [msg for msg in messages if msg["role"] == "system"]
        assert len(system_messages) == 1
    
    def test_clear_keep_system(self):
        """Test clearing conversation while keeping system messages."""
        conv = Conversation("System prompt")
        conv.add_user_message("Hello")
        conv.add_assistant_message("Hi")
        
        conv.clear(keep_system=True)
        assert len(conv) == 1
        assert conv.messages[0].role == "system"
    
    def test_clear_all(self):
        """Test clearing all messages."""
        conv = Conversation("System prompt")
        conv.add_user_message("Hello")
        conv.add_assistant_message("Hi")
        
        conv.clear(keep_system=False)
        assert len(conv) == 0


class TestLLMBase:
    """Test the LLM base class functionality."""
    
    def test_initialization(self):
        """Test LLM base class initialization."""
        llm = MockLLM()
        assert llm.config == {}
        assert llm.logger is not None
        
        config = {"model": "test", "temperature": 0.7}
        llm_with_config = MockLLM(config)
        assert llm_with_config.config == config
    
    def test_normalize_messages_string(self):
        """Test normalizing string input to messages."""
        llm = MockLLM()
        
        result = llm.normalize_messages("Hello world")
        expected = [{"role": "user", "content": "Hello world"}]
        assert result == expected
    
    def test_normalize_messages_conversation(self):
        """Test normalizing Conversation object to messages."""
        llm = MockLLM()
        
        conv = Conversation()
        conv.add_user_message("Hello")
        conv.add_assistant_message("Hi")
        
        result = llm.normalize_messages(conv)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
    
    def test_normalize_messages_list(self):
        """Test normalizing message list."""
        llm = MockLLM()
        
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"}
        ]
        
        result = llm.normalize_messages(messages)
        assert result == messages
    
    def test_normalize_messages_invalid(self):
        """Test normalizing invalid input."""
        llm = MockLLM()
        
        with pytest.raises(LLMError):
            llm.normalize_messages(123)
    
    def test_validate_messages_valid(self):
        """Test validating valid messages."""
        llm = MockLLM()
        
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"}
        ]
        
        assert llm.validate_messages(messages) is True
    
    def test_validate_messages_empty(self):
        """Test validating empty messages."""
        llm = MockLLM()
        assert llm.validate_messages([]) is False
    
    def test_validate_messages_invalid_format(self):
        """Test validating messages with invalid format."""
        llm = MockLLM()
        
        # Non-dict message
        assert llm.validate_messages(["invalid"]) is False
        
        # Missing required fields
        assert llm.validate_messages([{"role": "user"}]) is False
        assert llm.validate_messages([{"content": "Hello"}]) is False
        
        # Non-string content
        assert llm.validate_messages([{"role": "user", "content": 123}]) is False
    
    def test_create_conversation(self):
        """Test creating conversation."""
        llm = MockLLM()
        
        conv = llm.create_conversation()
        assert isinstance(conv, Conversation)
        assert len(conv) == 0
        
        conv_with_system = llm.create_conversation("System prompt")
        assert len(conv_with_system) == 1
        assert conv_with_system.messages[0].role == "system"
    
    def test_test_connection_success(self):
        """Test successful connection test."""
        llm = MockLLM()
        assert llm.test_connection() is True
    
    def test_test_connection_failure(self):
        """Test failed connection test."""
        llm = MockLLM(should_fail=True)
        assert llm.test_connection() is False
    
    def test_get_model_info(self):
        """Test getting model information."""
        config = {"model": "test-model", "temperature": 0.7}
        llm = MockLLM(config)
        
        info = llm.get_model_info()
        assert info["provider"] == "MockLLM"
        assert info["model"] == "test-model"
        assert info["temperature"] == 0.7
        assert info["config"] == config
    
    def test_estimate_tokens(self):
        """Test token estimation."""
        llm = MockLLM()
        
        # Test empty text
        assert llm.estimate_tokens("") == 0
        
        # Test English text
        tokens = llm.estimate_tokens("Hello world")
        assert tokens > 0
        
        # Test Chinese text
        tokens = llm.estimate_tokens("你好世界")
        assert tokens > 0
        
        # Test mixed text
        tokens = llm.estimate_tokens("Hello 世界")
        assert tokens > 0
    
    def test_truncate_messages(self):
        """Test message truncation."""
        llm = MockLLM()
        
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "How are you"},
            {"role": "assistant", "content": "Good"}
        ]
        
        # Test with reasonable limit
        truncated = llm.truncate_messages(messages, max_tokens=100)
        assert len(truncated) >= 1  # At least system message
        
        # Test with very low limit
        truncated = llm.truncate_messages(messages, max_tokens=1)
        system_msgs = [msg for msg in truncated if msg["role"] == "system"]
        assert len(system_msgs) == 1
    
    def test_chat_stream_default(self):
        """Test default chat_stream implementation."""
        llm = MockLLM()
        
        result = list(llm.chat_stream("Hello"))
        assert result == ["Mock response"]
    
    def test_chat_stream_failure(self):
        """Test chat_stream failure."""
        llm = MockLLM(should_fail=True)
        
        with pytest.raises(LLMError):
            list(llm.chat_stream("Hello"))


class TestAlibabaDashScopeLLM:
    """Test the legacy alias through the current OpenAI-compatible SDK boundary."""

    @pytest.fixture(autouse=True)
    def sdk_client(self, monkeypatch):
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        with patch("chatterpal.core.llm.alibaba.OpenAI") as constructor:
            self.client = constructor.return_value
            self.create = self.client.chat.completions.create
            yield

    def test_initialization(self):
        """Test Alibaba LLM initialization."""
        config = {"api_key": "test_key", "model": "qwen-turbo"}
        llm = AlibabaDashScopeLLM(config)
        assert llm.config == config
        assert llm.api_key == "test_key"
        assert llm.model == "qwen-turbo"
        assert llm.client is self.client

    def test_initialization_missing_api_key(self):
        """Test missing credentials independently of the developer environment."""
        with pytest.raises(LLMError, match="阿里百炼API密钥未配置"):
            AlibabaDashScopeLLM({})

    def test_chat_success(self):
        """Test a successful reply using the SDK's choices/message shape."""
        from types import SimpleNamespace

        self.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="你好!我可以帮助你什么?"))],
            usage=None,
        )
        llm = AlibabaDashScopeLLM({"api_key": "test_key"})

        assert llm.chat("你好") == "你好!我可以帮助你什么?"
        self.create.assert_called_once_with(
            model="qwen-plus",
            messages=[{"role": "user", "content": "你好"}],
            max_tokens=2000,
            temperature=0.7,
            top_p=0.8,
        )

    def test_chat_failure(self):
        """Test SDK failure conversion to LLMError."""
        self.create.side_effect = RuntimeError("API错误")
        llm = AlibabaDashScopeLLM({"api_key": "test_key"})

        with pytest.raises(LLMError, match="API错误"):
            llm.chat("你好")
        self.create.assert_called_once()

    def test_chat_with_conversation(self):
        """Test conversation normalization before the SDK request."""
        from types import SimpleNamespace

        self.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="回复内容"))],
            usage=None,
        )
        llm = AlibabaDashScopeLLM({"api_key": "test_key"})
        conv = Conversation("你是一个有用的助手")
        conv.add_user_message("你好")

        assert llm.chat(conv) == "回复内容"
        assert self.create.call_args.kwargs["messages"] == conv.get_messages()


class TestOpenAILLM:
    """Test the OpenAI provider's actual HTTP boundary."""

    def test_initialization(self):
        """Test OpenAI LLM initialization."""
        config = {"api_key": "test_key", "model": "gpt-3.5-turbo"}
        llm = OpenAILLM(config)
        assert llm.config == config

    def test_initialization_missing_api_key(self):
        """Test OpenAI LLM initialization with missing API key."""
        with pytest.raises(LLMError, match="OpenAI API密钥未配置"):
            OpenAILLM({})

    @patch("chatterpal.core.llm.openai.requests.post")
    def test_chat_success(self, mock_post):
        """Test HTTP response parsing and request construction."""
        mock_post.return_value.json.return_value = {
            "choices": [{"message": {"content": "Hello! How can I help"}}]
        }
        llm = OpenAILLM({"api_key": "test_key"})

        assert llm.chat("Hello") == "Hello! How can I help"
        mock_post.return_value.raise_for_status.assert_called_once_with()
        mock_post.assert_called_once_with(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": "Bearer test_key", "Content-Type": "application/json"},
            json={
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 2000,
                "temperature": 0.7,
                "top_p": 1.0,
                "frequency_penalty": 0.0,
                "presence_penalty": 0.0,
            },
            timeout=30,
        )

    @patch("chatterpal.core.llm.openai.requests.post")
    def test_chat_failure(self, mock_post):
        """Test HTTP failures are reported as LLMError."""
        from requests.exceptions import HTTPError

        mock_post.return_value.raise_for_status.side_effect = HTTPError("API Error")
        llm = OpenAILLM({"api_key": "test_key"})

        with pytest.raises(LLMError, match="API Error"):
            llm.chat("Hello")
        mock_post.return_value.json.assert_not_called()

    @patch("chatterpal.core.llm.openai.requests.post")
    def test_chat_stream_success(self, mock_post):
        """Test SSE chunks, empty deltas, and the stream terminator."""
        mock_post.return_value.iter_lines.return_value = [
            b'data: {"choices": [{"delta": {"content": "Hello"}}]}',
            b'data: {"choices": [{"delta": {"content": " world"}}]}',
            b'data: {"choices": [{"delta": {"content": null}}]}',
            b'data: [DONE]',
            b'data: {"choices": [{"delta": {"content": "ignored"}}]}',
        ]
        llm = OpenAILLM({"api_key": "test_key"})

        assert list(llm.chat_stream("Hello")) == ["Hello", " world"]
        mock_post.return_value.raise_for_status.assert_called_once_with()
        assert mock_post.call_args.kwargs["stream"] is True
        assert mock_post.call_args.kwargs["json"]["stream"] is True
        assert mock_post.call_args.kwargs["json"]["messages"] == [
            {"role": "user", "content": "Hello"}
        ]


if __name__ == "__main__":
    pytest.main([__file__])








