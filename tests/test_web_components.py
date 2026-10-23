"""Exercise actual Gradio callbacks with real services and offline providers."""

import io
import json
import wave
from types import SimpleNamespace
from unittest.mock import Mock

import gradio as gr
import numpy as np
import pytest

from chatterpal.core.asr.base import ASRBase, ASRResult, ConfidenceLevel
from chatterpal.core.assessment.corrector import PronunciationCorrector
from chatterpal.core.llm.alibaba import AlibabaBailianLLM
from chatterpal.core.tts.base import TTSBase, TTSResult
from chatterpal.services.chat import ChatService
from chatterpal.services.correction import CorrectionService
from chatterpal.services.evaluation import EvaluationService
from chatterpal.utils.preferences import UserPreferences
from chatterpal.web.components.chat_tab import ChatTab
from chatterpal.web.components.correct_tab import CorrectTab
from chatterpal.web.components.score_tab import ScoreTab


TRANSCRIPT = "Hello, how are you today"
REPLY = "I'm doing well, thank you!"


@pytest.fixture
def components(tmp_path, monkeypatch):
    preferences = UserPreferences(config_dir=str(tmp_path / "preferences"))
    monkeypatch.setattr("chatterpal.web.components.chat_tab.get_preferences_manager", lambda: preferences)
    sample_rate = 16000
    t = np.arange(sample_rate) / sample_rate
    audio = (sample_rate, (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32))
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes((audio[1] * 32767).astype("<i2").tobytes())
    audio_bytes = output.getvalue()
    asr = Mock(spec=ASRBase)
    asr.recognize_gradio_audio.return_value = TRANSCRIPT
    asr.recognize_with_error_handling.return_value = ASRResult(
        TRANSCRIPT, 0.95, ConfidenceLevel.HIGH, 0.8, 0.01, {},
    )
    tts = Mock(spec=TTSBase)
    tts.synthesize_with_error_handling.return_value = TTSResult(
        REPLY, audio_bytes, 1.0, "wav", sample_rate, False, 0.01, {},
    )
    client = Mock()
    sdk = client.chat.completions.create
    sdk.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=REPLY))], usage=None,
    )
    monkeypatch.setattr("chatterpal.core.llm.alibaba.OpenAI", lambda **kwargs: client)
    llm = AlibabaBailianLLM({"api_key": "offline-test-key"})
    chat_service = ChatService(asr=asr, tts=tts, llm=llm)
    evaluation_service = EvaluationService(asr=asr, llm=llm)
    correction_service = CorrectionService(asr=asr)
    return SimpleNamespace(
        chat=ChatTab(chat_service), score=ScoreTab(evaluation_service),
        correct=CorrectTab(correction_service), chat_service=chat_service,
        evaluation_service=evaluation_service, correction_service=correction_service,
        preferences=preferences, audio=audio, audio_bytes=audio_bytes,
        asr=asr, tts=tts, sdk=sdk,
    )


def build_interface(component):
    with gr.Blocks(analytics_enabled=False) as blocks:
        tab = component.create_interface()
    assert isinstance(tab, gr.Tab)
    return blocks


def rendered_text(result):
    """Successful Gradio Textbox outputs must be displayable strings."""
    assert isinstance(result, str)
    assert "出错" not in result, result
    assert "失败" not in result, result
    return result


def correction_outputs(components, target=TRANSCRIPT):
    result = components.correct._assess_pronunciation(components.audio, target)
    assert isinstance(result, tuple)
    assert len(result) == 5
    assert all(isinstance(value, str) for value in result)
    assert "出错" not in result[0], result[0]
    assert "失败" not in result[0], result[0]
    return result


class TestChatTab:
    def test_initialization(self, components):
        assert components.chat.chat_service is components.chat_service
        assert components.chat.current_session_id is None
        assert components.chat.history_state["current_page"] == 1
        assert components.chat.preferences is components.preferences

    def test_create_interface(self, components):
        blocks = build_interface(components.chat)
        assert any(isinstance(item, gr.Chatbot) for item in blocks.blocks.values())
        assert any(isinstance(item, gr.Textbox) and "文本输入" in item.label for item in blocks.blocks.values())
        assert any(fn.fn == components.chat._handle_chat_messages for fn in blocks.fns.values())

    def test_process_text_message(self, components):
        audio, history = components.chat._handle_chat(None, "Hello, how are you", [], True)
        assert audio[0] == 16000
        assert len(audio[1]) == 16000
        assert history == [["Hello, how are you", REPLY]]
        assert components.sdk.call_count == 1

    def test_process_audio_message(self, components):
        audio, history = components.chat._handle_chat(components.audio, "", [], False)
        assert audio[0] == 16000
        assert len(audio[1]) == 16000
        assert history == [[TRANSCRIPT, REPLY]]
        components.asr.recognize_with_error_handling.assert_called_once()

    def test_clear_conversation(self, components):
        components.chat._handle_chat(None, "Hello", [], True)
        session_id = components.chat.current_session_id
        status, history = components.chat._clear_context()
        assert "已清除" in status
        assert history == []
        assert components.chat.current_session_id is None
        assert components.chat_service.get_conversation_history(session_id) == [
            {"role": "system", "content": components.chat_service.default_system_prompt}
        ]

    def test_get_topic_suggestions(self, components):
        # The current ChatTab has one topic-generation button rather than the
        # removed get_topic_suggestions wrapper.
        suggestions = components.chat_service.get_topic_suggestions(count=5)
        assert isinstance(suggestions, list)
        assert len(suggestions) > 0
        assert all(isinstance(topic, str) and topic.strip() for topic in suggestions)
        topic = components.chat._generate_topic()
        assert isinstance(topic, str)
        assert topic.strip()
        assert "失败" not in topic

    def test_format_conversation_display(self, components):
        _, history = components.chat._handle_chat_messages(None, "Hello", [], True)
        # Verify against the actual messages-mode widget, so tuple/message
        # mismatches cannot pass just because the callback returns a list.
        # Match ChatTab.create_interface's declared format without coupling this
        # check to independent layout-construction failures.
        chatbot = gr.Chatbot(type="messages")
        displayed = chatbot.postprocess(history)
        payload = displayed.model_dump()
        assert "Hello" in str(payload)
        assert REPLY in str(payload)

    def test_error_handling_empty_input(self, components):
        audio, history = components.chat._handle_chat(None, "", [], True)
        assert audio is None
        assert history and history[-1][1]
        components.sdk.assert_not_called()
        components.tts.synthesize_with_error_handling.assert_not_called()

    def test_error_handling_invalid_audio(self, components):
        audio, history = components.chat._handle_chat(None, "", [], False)
        assert audio is None
        assert history and history[-1][1]
        components.asr.recognize_with_error_handling.assert_not_called()
        components.sdk.assert_not_called()


class TestScoreTab:
    def test_initialization(self, components):
        assert components.score.evaluation_service is components.evaluation_service

    def test_create_interface(self, components):
        blocks = build_interface(components.score)
        assert any(isinstance(item, gr.Audio) for item in blocks.blocks.values())
        assert any(isinstance(item, gr.Textbox) and "练习文本" in item.label for item in blocks.blocks.values())
        assert any(fn.fn == components.score._evaluate_pronunciation for fn in blocks.fns.values())

    def test_evaluate_pronunciation_with_target(self, components):
        result = rendered_text(components.score._evaluate_pronunciation(TRANSCRIPT, components.audio))
        assert TRANSCRIPT in result
        components.asr.recognize_gradio_audio.assert_called_once_with(components.audio)

    def test_evaluate_pronunciation_without_target(self, components):
        # ScoreTab now explicitly requires practice text, while CorrectTab
        # exposes the optional-target analysis flow.
        result = components.score._evaluate_pronunciation("", components.audio)
        assert "请输入" in result or "请先输入" in result
        components.asr.recognize_gradio_audio.assert_not_called()
        components.sdk.assert_not_called()

    def test_format_score_display(self, components):
        analysis = {"pronunciation_score": 85, "fluency_score": 80,
                    "accuracy_score": 85, "feedback": "Good pronunciation overall",
                    "suggestions": ["Work on intonation"]}
        components.sdk.return_value.choices[0].message.content = json.dumps(analysis)
        result = rendered_text(components.score._evaluate_pronunciation(TRANSCRIPT, components.audio))
        assert "85" in result
        assert "fluency" in result.lower() or "流畅" in result

    def test_generate_feedback_text(self, components):
        components.sdk.return_value.choices[0].message.content = json.dumps({
            "pronunciation_score": 85, "fluency_score": 80,
            "feedback": "Good pronunciation overall", "suggestions": [],
        })
        result = rendered_text(components.score._evaluate_pronunciation(TRANSCRIPT, components.audio))
        assert "Good pronunciation overall" in result

    def test_error_handling_no_audio(self, components):
        result = components.score._evaluate_pronunciation("test", None)
        assert "请录制" in result
        components.asr.recognize_gradio_audio.assert_not_called()
        components.sdk.assert_not_called()

    def test_error_handling_service_failure(self, components):
        components.asr.recognize_gradio_audio.side_effect = RuntimeError("Service failed")
        result = components.score._evaluate_pronunciation("test", components.audio)
        assert "出错" in result
        assert "Service failed" in result
        components.asr.recognize_gradio_audio.assert_called_once()


class TestCorrectTab:
    def test_initialization(self, components):
        assert components.correct.correction_service is components.correction_service

    def test_create_interface(self, components):
        blocks = build_interface(components.correct)
        assert any(isinstance(item, gr.Audio) for item in blocks.blocks.values())
        assert any(isinstance(item, gr.Textbox) and "目标文本" in item.label for item in blocks.blocks.values())
        assert any(fn.fn == components.correct._assess_pronunciation for fn in blocks.fns.values())

    def test_analyze_pronunciation(self, components):
        result = correction_outputs(components)
        assert TRANSCRIPT in result[0]
        assert result[1]
        assert result[4]

    def test_format_detailed_analysis(self, components):
        result = correction_outputs(components)
        assert "评分" in result[1]
        assert "发音" in result[1]
        assert "流畅" in result[1]

    def test_format_phoneme_analysis(self, components, monkeypatch):
        # Inject formant measurements at the Praat boundary; retain the real
        # analyzer, correction report, and UI callback that must display them.
        from chatterpal.core.assessment import phoneme as phoneme_module

        real_sound = phoneme_module.parselmouth.Sound

        class MeasuredSound:
            def __init__(self, *args, **kwargs):
                self.sound = real_sound(*args, **kwargs)

            def __getattr__(self, name):
                return getattr(self.sound, name)

            def to_formant_burg(self, **kwargs):
                return SimpleNamespace(
                    get_number_of_frames=lambda: 3,
                    get_time_from_frame_number=lambda frame: frame * 0.01,
                    get_value_at_time=lambda number, time: {
                        1: 1200.0, 2: 3500.0, 3: 4200.0,
                    }[number],
                )

        monkeypatch.setattr(phoneme_module.parselmouth, "Sound", MeasuredSound)
        report = components.correction_service.comprehensive_correction(
            components.audio, "hello", analysis_level="expert"
        )
        problems = {problem["type"]: problem for problem in report.phoneme_problems}
        assert "vowel_height_error" in problems
        assert "vowel_backness_error" in problems
        assert "1200" in problems["vowel_height_error"]["description"]
        assert "3500" in problems["vowel_backness_error"]["description"]
        result = correction_outputs(components, "hello")
        for problem_type in ("vowel_height_error", "vowel_backness_error"):
            assert problems[problem_type]["description"] in result[3]

    def test_generate_improvement_suggestions(self, components):
        result = correction_outputs(components)
        assert "建议" in result[4] or "练习" in result[4]

    def test_get_practice_exercises(self, components):
        # Practice recommendations are returned in the fifth wired output.
        result = correction_outputs(components, "think")
        assert result[4].strip()
        assert "练习" in result[4]

    def test_error_handling_no_audio(self, components):
        result = components.correct._assess_pronunciation(None, "test")
        assert len(result) == 5
        assert "请录制" in result[0]
        assert result[0] == result[1]
        assert result[2:] == ("", "", "")
        components.asr.recognize_gradio_audio.assert_not_called()

    def test_error_handling_service_failure(self, components, monkeypatch):
        # Trigger an exception inside the real correction backend, rather than
        # replacing the callback/service with an exception-producing mock.
        def fail_validation(self, audio):
            raise RuntimeError("Analysis failed")

        monkeypatch.setattr(PronunciationCorrector, "validate_audio_data", fail_validation)
        result = components.correct._assess_pronunciation(components.audio, "test")
        assert "出错" in result[0]
        assert "Analysis failed" in result[0]
        assert result[2:] == ("", "", "")


class TestWebComponentIntegration:
    def test_cross_tab_data_flow(self, components):
        audio, history = components.chat._handle_chat(None, "Hello", [], True)
        assert history == [["Hello", REPLY]]
        result = rendered_text(components.score._evaluate_pronunciation("Hello", audio))
        assert result
        correction_outputs(components, "Hello")

    def test_consistent_audio_handling(self, components):
        audio, history = components.chat._handle_chat(components.audio, "", [], False)
        assert audio[0] == components.audio[0]
        assert history == [[TRANSCRIPT, REPLY]]
        assert rendered_text(components.score._evaluate_pronunciation("test", components.audio))
        correction_outputs(components, "test")

    def test_error_propagation(self, components):
        audio, history = components.chat._handle_chat(None, "", [], False)
        assert audio is None and history
        assert "请录制" in components.score._evaluate_pronunciation("test", None)
        correction = components.correct._assess_pronunciation(None, "test")
        assert "请录制" in correction[0]
        assert correction[2:] == ("", "", "")
        components.asr.recognize_gradio_audio.assert_not_called()
        components.sdk.assert_not_called()
