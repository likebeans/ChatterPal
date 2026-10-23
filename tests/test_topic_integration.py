"""主题生成器与真实聊天服务的离线集成测试。"""

from unittest.mock import Mock

import pytest

from chatterpal.core.llm.base import LLMBase
from chatterpal.services.chat import ChatService
from chatterpal.services.topic_generator import TopicGenerator


class TestTopicIntegration:
    @pytest.fixture
    def mock_llm(self):
        llm = Mock(spec=LLMBase)
        llm.chat.return_value = "What's your favorite hobby and why do you enjoy it?"
        llm.test_connection.return_value = True
        return llm

    @pytest.fixture
    def chat_service(self, mock_llm):
        config = {
            "topic_generation": {
                "difficulty_levels": ["beginner", "intermediate", "advanced"],
                "categories": ["daily", "hobby", "travel", "work"],
                "max_retries": 3,
            }
        }
        return ChatService(llm=mock_llm, config=config)

    def test_topic_generator_initialization(self, chat_service):
        assert chat_service.topic_generator is not None
        assert isinstance(chat_service.topic_generator, TopicGenerator)

    def test_generate_random_topic(self, chat_service):
        topic = chat_service.generate_topic()
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert topic.endswith("?") or any(
            word in topic.lower() for word in ["tell me", "describe"]
        )

    def test_generate_topic_with_difficulty(self, chat_service):
        beginner_topic = chat_service.generate_topic(difficulty="beginner")
        assert isinstance(beginner_topic, str)
        assert len(beginner_topic) > 0
        advanced_topic = chat_service.generate_topic(difficulty="advanced")
        assert isinstance(advanced_topic, str)
        assert len(advanced_topic) > 0
        for topic, difficulty in [(beginner_topic, "beginner"), (advanced_topic, "advanced")]:
            assert any(
                topic in topics
                for topics in chat_service.topic_generator.default_topics[difficulty].values()
            )

    def test_generate_topic_with_category(self, chat_service):
        topic = chat_service.generate_topic(category="hobby")
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert topic in chat_service.topic_generator.get_topics_by_category("hobby")

    def test_generate_contextual_topic(self, chat_service, mock_llm):
        session_id = chat_service.create_session()
        chat_service.chat_with_text("I love playing guitar", session_id)
        chat_service.chat_with_text("That's great! Music is wonderful.", session_id)
        mock_llm.chat.reset_mock()
        topic = chat_service.generate_topic(session_id=session_id)
        assert isinstance(topic, str)
        assert len(topic) > 0
        mock_llm.chat.assert_called_once()
        prompt = mock_llm.chat.call_args.args[0]
        assert isinstance(prompt, str)
        assert "I love playing guitar" in prompt
        assert "Music is wonderful" in prompt
        assert topic == mock_llm.chat.return_value
        assert chat_service.get_current_topic(session_id) == topic

    def test_set_topic_for_session(self, chat_service):
        session_id = chat_service.create_session()
        topic = "Let's talk about your favorite movies."
        assert chat_service.set_topic_for_session(session_id, topic) is True
        assert chat_service.get_current_topic(session_id) == topic

    def test_get_current_topic(self, chat_service):
        session_id = chat_service.create_session()
        assert chat_service.get_current_topic(session_id) is None
        topic = chat_service.generate_topic(session_id=session_id)
        assert chat_service.get_current_topic(session_id) == topic

    def test_clear_context_removes_topic(self, chat_service):
        session_id = chat_service.create_session()
        chat_service.generate_topic(session_id=session_id)
        assert chat_service.get_current_topic(session_id) is not None
        assert chat_service.clear_context(session_id) is True
        assert chat_service.get_current_topic(session_id) is None

    def test_get_topic_suggestions(self, chat_service):
        suggestions = chat_service.get_topic_suggestions(count=3)
        assert isinstance(suggestions, list)
        assert 0 < len(suggestions) <= 3
        for suggestion in suggestions:
            assert isinstance(suggestion, str)
            assert len(suggestion) > 0

    def test_add_custom_topic(self, chat_service):
        topic = "What's your opinion on artificial intelligence?"
        assert chat_service.add_custom_topic(topic, "tech") is True
        assert topic in chat_service.topic_generator.get_topics_by_category("tech")

    def test_get_topic_statistics(self, chat_service):
        stats = chat_service.get_topic_statistics()
        assert isinstance(stats, dict)
        assert "total_topics" in stats
        assert "topics_by_difficulty" in stats
        assert "topics_by_category" in stats
        assert stats["total_topics"] > 0

    def test_process_chat_with_topic_context(self, chat_service, mock_llm):
        session_id = chat_service.create_session()
        topic = "Tell me about your favorite book."
        audio, history = chat_service.process_chat(
            text_input="I love reading science fiction novels.",
            session_id=session_id,
            topic_context=topic,
        )
        # 没有 TTS 时 process_chat 明确返回 None。
        assert audio is None
        assert isinstance(history, list)
        assert chat_service.get_current_topic(session_id) == topic
        mock_llm.chat.assert_called_once()
        messages = mock_llm.chat.call_args.args[0]
        assert any(topic in message["content"] for message in messages)
        assert history[-1][0] == "I love reading science fiction novels."
        assert mock_llm.chat.return_value in history[-1][1]

    def test_topic_generation_error_handling(self, chat_service, mock_llm):
        session_id = chat_service.create_session()
        chat_service.chat_with_text("I love playing guitar", session_id)
        mock_llm.chat.reset_mock()
        mock_llm.chat.side_effect = RuntimeError("LLM调用失败")
        topic = chat_service.generate_topic(session_id)
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert mock_llm.chat.call_count == 3
        assert any(
            topic in topics
            for topics in chat_service.topic_generator.default_topics["intermediate"].values()
        )

    def test_service_status_includes_topic_generator(self, chat_service):
        status = chat_service.get_service_status()
        assert "topic_generator_available" in status
        assert status["topic_generator_available"] is True
        assert "topic_statistics" in status
        assert isinstance(status["topic_statistics"], dict)
        assert status["topic_statistics"]["total_topics"] > 0

    def test_topic_context_enhancement(self, chat_service, mock_llm):
        session_id = chat_service.create_session()
        topic = "What's your favorite way to exercise?"
        mock_llm.chat.return_value = "A daily walk can help us stay active."
        assert chat_service.set_topic_for_session(session_id, topic) is True
        audio, history = chat_service.process_chat(
            text_input="I like walking in the park.", session_id=session_id
        )
        assert len(history) > 0
        response = history[-1][1]
        assert isinstance(response, str)
        assert len(response) > 0
        assert mock_llm.chat.return_value in response
        assert topic.lower() in response.lower()
        assert audio is None
        stored_history = chat_service.get_conversation_history(session_id)
        assert stored_history[-1]["content"] == response

    def test_invalid_session_handling(self, chat_service):
        assert chat_service.get_current_topic("nonexistent_session") is None
        assert chat_service.set_topic_for_session("nonexistent_session", "test topic") is False

    def test_topic_generator_fallback_without_llm(self):
        chat_service = ChatService()
        topic = chat_service.generate_topic()
        assert isinstance(topic, str)
        assert len(topic) > 0

    @pytest.mark.parametrize("difficulty", ["beginner", "intermediate", "advanced"])
    def test_topic_generation_all_difficulties(self, chat_service, difficulty):
        topic = chat_service.generate_topic(difficulty=difficulty)
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert any(
            topic in topics
            for topics in chat_service.topic_generator.default_topics[difficulty].values()
        )

    @pytest.mark.parametrize("category", ["daily", "hobby", "travel", "work"])
    def test_topic_generation_all_categories(self, chat_service, category):
        topic = chat_service.generate_topic(category=category)
        assert isinstance(topic, str)
        assert len(topic) > 0
        assert topic in chat_service.topic_generator.get_topics_by_category(category)
