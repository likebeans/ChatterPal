"""主题生成的错误处理、重试和备用方案测试。"""

from unittest.mock import patch

import pytest

from chatterpal.core.errors import TopicGenerationError
from chatterpal.services.chat import ChatService
from chatterpal.services.topic_generator import TopicGenerator


class MockLLMForTopic:
    """在 LLM 边界控制临时失败和无效响应，不访问外部服务。"""

    response = "What's your favorite hobby and why do you enjoy it?"

    def __init__(self):
        self.should_fail = False
        self.fail_count = 0
        self.call_count = 0
        self.return_invalid_topic = False

    def chat(self, messages, **kwargs):
        self.call_count += 1
        if self.should_fail and self.fail_count > 0:
            self.fail_count -= 1
            raise RuntimeError(f"LLM服务错误 (调用 {self.call_count})")
        if self.return_invalid_topic:
            return "Invalid topic without question mark"
        return self.response

    def test_connection(self):
        return not (self.should_fail and self.fail_count > 0)


def _topic_bank(generator, difficulty="intermediate"):
    return {
        topic
        for topics in generator.default_topics[difficulty].values()
        for topic in topics
    }


class TestTopicGeneratorErrorHandling:
    """测试真实主题生成器，替换 LLM 调用边界。"""

    def setup_method(self):
        self.llm = MockLLMForTopic()
        self.topic_generator = TopicGenerator(llm=self.llm)

    def test_generate_random_topic_success(self):
        topic = self.topic_generator.generate_random_topic("intermediate")
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert topic.endswith("?") or any(
            word in topic.lower() for word in ["tell me", "describe", "explain"]
        )
        assert topic in _topic_bank(self.topic_generator)

    def test_generate_random_topic_invalid_difficulty(self):
        topic = self.topic_generator.generate_random_topic("invalid_difficulty")
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert topic in _topic_bank(self.topic_generator)

    def test_generate_random_topic_with_fallback_success(self):
        topic = self.topic_generator.generate_random_topic_with_fallback("intermediate")
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert topic in _topic_bank(self.topic_generator)

    def test_generate_random_topic_with_fallback_error(self):
        # 空主题库使真实随机生成路径报错，随后使用固定的备用主题库。
        self.topic_generator.default_topics = {}
        topic = self.topic_generator.generate_random_topic_with_fallback("intermediate")
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert topic in [
            "What's something interesting that happened to you recently?",
            "Tell me about your favorite hobby.",
            "What's your favorite season and why?",
            "Describe a place you'd love to visit.",
            "What's something you're proud of?",
        ]

    def test_generate_contextual_topic_no_llm(self):
        generator = TopicGenerator(llm=None)
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        topic = generator.generate_contextual_topic(history)
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert topic in _topic_bank(generator)

    def test_generate_contextual_topic_empty_history(self):
        topic = self.topic_generator.generate_contextual_topic([])
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert topic in _topic_bank(self.topic_generator)
        assert self.llm.call_count == 0

    def test_generate_contextual_topic_insufficient_history(self):
        topic = self.topic_generator.generate_contextual_topic(
            [{"role": "user", "content": "Hi"}]
        )
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert topic in _topic_bank(self.topic_generator)
        assert self.llm.call_count == 0

    def test_generate_contextual_topic_llm_failure(self):
        self.llm.should_fail = True
        self.llm.fail_count = 10
        history = [
            {"role": "user", "content": "I love reading books"},
            {"role": "assistant", "content": "What genres do you enjoy?"},
        ]
        topic = self.topic_generator.generate_contextual_topic(history)
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert topic in _topic_bank(self.topic_generator)
        assert self.llm.call_count == self.topic_generator.max_retries

    def test_generate_contextual_topic_retry_mechanism(self):
        self.llm.should_fail = True
        self.llm.fail_count = 2
        history = [
            {"role": "user", "content": "I enjoy cooking"},
            {"role": "assistant", "content": "What's your favorite dish to make?"},
        ]
        topic = self.topic_generator.generate_contextual_topic(history)
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert topic == self.llm.response
        assert self.llm.call_count == 3

    def test_generate_contextual_topic_invalid_response(self):
        self.llm.return_invalid_topic = True
        history = [
            {"role": "user", "content": "I like sports"},
            {"role": "assistant", "content": "Sports help us stay healthy!"},
        ]
        topic = self.topic_generator.generate_contextual_topic(history)
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert topic in _topic_bank(self.topic_generator)
        assert self.llm.call_count == self.topic_generator.max_retries

    def test_validate_topic(self):
        assert self.topic_generator._validate_topic("What's your favorite color?")
        assert self.topic_generator._validate_topic("Tell me about your hobbies.")
        assert self.topic_generator._validate_topic("Describe your ideal vacation.")
        assert not self.topic_generator._validate_topic("")
        assert not self.topic_generator._validate_topic(None)
        assert not self.topic_generator._validate_topic("Hi")
        assert not self.topic_generator._validate_topic("a" * 300)
        assert not self.topic_generator._validate_topic("This is not a question")
        assert not self.topic_generator._validate_topic(
            "What do you think about politics?"
        )

    def test_add_custom_topic(self):
        topic = "What's your favorite programming language?"
        success = self.topic_generator.add_custom_topic(
            topic, "technology", "intermediate"
        )
        assert success is True
        topics = self.topic_generator.get_topics_by_category(
            "technology", "intermediate"
        )
        assert topic in topics
        assert self.topic_generator.add_custom_topic("", "category", "difficulty") is False

    def test_get_statistics(self):
        stats = self.topic_generator.get_statistics()
        assert isinstance(stats, dict)
        assert "total_topics" in stats
        assert "topics_by_difficulty" in stats
        assert "topics_by_category" in stats
        assert "available_difficulties" in stats
        assert "available_categories" in stats
        assert stats["total_topics"] > 0
        assert len(stats["available_difficulties"]) > 0
        assert len(stats["available_categories"]) > 0
        assert stats["total_topics"] == sum(
            entry["total"] for entry in stats["topics_by_difficulty"].values()
        )


class TestChatServiceTopicIntegration:
    """测试聊天服务中的主题元数据和主题生成接线。"""

    def setup_method(self):
        self.llm = MockLLMForTopic()
        self.chat_service = ChatService(llm=self.llm)

    def test_generate_topic_success(self):
        topic = self.chat_service.generate_topic(difficulty="intermediate")
        assert isinstance(topic, str)
        assert len(topic) > 0

    def test_generate_topic_with_session(self):
        session_id = self.chat_service.create_session()
        self.chat_service.chat_with_text("I love traveling", session_id)
        topic = self.chat_service.generate_topic(session_id, difficulty="intermediate")
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert topic == self.llm.response
        session = self.chat_service.get_session(session_id)
        assert session.get_metadata("current_topic") == topic

    def test_generate_topic_llm_failure(self):
        session_id = self.chat_service.create_session()
        self.chat_service.chat_with_text("I enjoy traveling", session_id)
        self.llm.call_count = 0
        self.llm.should_fail = True
        self.llm.fail_count = 10
        topic = self.chat_service.generate_topic(session_id, difficulty="intermediate")
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert topic in _topic_bank(self.chat_service.topic_generator)
        assert self.llm.call_count == self.chat_service.topic_generator.max_retries

    def test_get_topic_suggestions(self):
        suggestions = self.chat_service.get_topic_suggestions(
            difficulty="intermediate", count=3
        )
        assert isinstance(suggestions, list)
        assert 0 < len(suggestions) <= 3
        for suggestion in suggestions:
            assert isinstance(suggestion, str)
            assert len(suggestion) > 0

    def test_get_topic_suggestions_by_category(self):
        suggestions = self.chat_service.get_topic_suggestions(
            difficulty="intermediate", category="daily", count=2
        )
        assert isinstance(suggestions, list)
        assert len(suggestions) == 2
        assert set(suggestions).issubset(
            self.chat_service.topic_generator.get_topics_by_category("daily")
        )

    def test_add_custom_topic_integration(self):
        topic = "What's your favorite coding framework?"
        success = self.chat_service.add_custom_topic(topic, "technology", "advanced")
        assert success is True
        assert topic in self.chat_service.topic_generator.get_topics_by_category(
            "technology", "advanced"
        )

    def test_get_topic_statistics_integration(self):
        stats = self.chat_service.get_topic_statistics()
        assert isinstance(stats, dict)
        assert "total_topics" in stats
        assert stats["total_topics"] > 0

    def test_set_topic_for_session(self):
        session_id = self.chat_service.create_session()
        assert self.chat_service.set_topic_for_session(
            session_id, "Let's talk about your hobbies"
        ) is True
        assert self.chat_service.get_current_topic(session_id) == "Let's talk about your hobbies"

    def test_get_current_topic(self):
        session_id = self.chat_service.create_session()
        assert self.chat_service.get_current_topic(session_id) is None
        topic = self.chat_service.generate_topic(session_id)
        assert self.chat_service.get_current_topic(session_id) == topic

    def test_clear_context_with_topic(self):
        session_id = self.chat_service.create_session()
        self.chat_service.generate_topic(session_id)
        assert self.chat_service.get_current_topic(session_id) is not None
        assert self.chat_service.clear_context(session_id) is True
        assert self.chat_service.get_current_topic(session_id) is None


class TestTopicErrorRecovery:
    """测试无效或失败的 LLM 响应能回退并记录错误。"""

    def setup_method(self):
        self.llm = MockLLMForTopic()
        self.chat_service = ChatService(llm=self.llm)

    def test_topic_generation_graceful_degradation(self):
        scenarios = [
            {"should_fail": True, "fail_count": 10},
            {"return_invalid_topic": True},
        ]
        for scenario in scenarios:
            self.llm.should_fail = False
            self.llm.return_invalid_topic = False
            session_id = self.chat_service.create_session()
            self.chat_service.chat_with_text("I enjoy reading", session_id)
            self.llm.should_fail = scenario.get("should_fail", False)
            self.llm.fail_count = scenario.get("fail_count", 0)
            self.llm.return_invalid_topic = scenario.get("return_invalid_topic", False)
            self.llm.call_count = 0
            topic = self.chat_service.generate_topic(session_id)
            assert isinstance(topic, str)
            assert len(topic) > 0
            assert topic in _topic_bank(self.chat_service.topic_generator)
            assert self.llm.call_count == self.chat_service.topic_generator.max_retries

    def test_contextual_topic_fallback_chain(self):
        session_id = self.chat_service.create_session()
        self.chat_service.chat_with_text("I enjoy reading science fiction books", session_id)
        self.llm.should_fail = True
        self.llm.fail_count = 10
        topic = self.chat_service.generate_topic(session_id)
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert topic in _topic_bank(self.chat_service.topic_generator)
        assert self.chat_service.get_current_topic(session_id) == topic

    @patch("chatterpal.core.errors.error_handler.log_error")
    def test_error_logging_in_topic_generation(self, mock_log_error):
        self.llm.should_fail = True
        self.llm.fail_count = 5
        history = [
            {"role": "user", "content": "I like music"},
            {"role": "assistant", "content": "Music is wonderful!"},
        ]
        topic = self.chat_service.topic_generator.generate_contextual_topic(history)
        assert isinstance(topic, str)
        assert len(topic) > 0
        mock_log_error.assert_called_once()
        error = mock_log_error.call_args.args[0]
        assert isinstance(error, TopicGenerationError)
        assert error.error_info.code == "TOPIC_GENERATION_FAILED"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
