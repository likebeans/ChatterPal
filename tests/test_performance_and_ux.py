"""Offline latency, concurrency and UI checks with controlled provider boundaries.

Synthetic audio and provider responses exercise orchestration; these tests do not
measure the accuracy of a downloaded speech model.
"""

import concurrent.futures
import gc
import io
import os
import statistics
import subprocess
import threading
import time
import wave
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from chatterpal.core.asr.base import ASRBase, ASRResult, ConfidenceLevel
from chatterpal.core.llm.alibaba import AlibabaBailianLLM
from chatterpal.core.tts.base import TTSBase, TTSResult
from chatterpal.services.chat import ChatService
from chatterpal.utils.preferences import UserPreferences
from chatterpal.web.components.chat_tab import ChatTab


RECOGNIZED_TEXT = "Hello, this is a test recognition result"
RESPONSE_TEXT = "This is a test response from the language model"


def sample_audio(duration=2.0, quality="high"):
    sample_rate = 16000
    t = np.arange(int(sample_rate * duration)) / sample_rate
    amplitude, noise_level = {
        "high": (0.8, 0.0), "medium": (0.6, 0.05), "low": (0.4, 0.15),
    }[quality]
    rng = np.random.default_rng(42)
    audio = amplitude * np.sin(2 * np.pi * 440 * t)
    audio += rng.normal(0, noise_level, len(t))
    return sample_rate, np.clip(audio, -1.0, 1.0).astype(np.float32)


def wav_bytes(audio):
    sample_rate, samples = audio
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes((samples * 32767).astype("<i2").tobytes())
    return output.getvalue()


def asr_result(text=RECOGNIZED_TEXT, confidence=0.95, processing_time=0.0):
    level = ConfidenceLevel.HIGH if confidence >= 0.8 else ConfidenceLevel.MEDIUM
    return ASRResult(text, confidence, level, 0.8, processing_time, {})


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """Use real chat/LLM services and stub the provider/SDK boundaries."""
    delays = {"asr": 0.0, "tts": 0.0, "llm": 0.0}
    audio_bytes = wav_bytes(sample_audio(duration=1.0))
    asr = Mock(spec=ASRBase)
    tts = Mock(spec=TTSBase)
    client = Mock()
    completions = client.chat.completions.create

    def recognize(*args, **kwargs):
        time.sleep(delays["asr"])
        return asr_result(processing_time=delays["asr"])

    def synthesize(text, **kwargs):
        time.sleep(delays["tts"])
        return TTSResult(text, audio_bytes, 1.0, "wav", 16000, False, delays["tts"], {})

    def complete(**kwargs):
        time.sleep(delays["llm"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=RESPONSE_TEXT))],
            usage=None,
        )

    asr.recognize_with_error_handling.side_effect = recognize
    tts.synthesize_with_error_handling.side_effect = synthesize
    completions.side_effect = complete
    monkeypatch.setattr("chatterpal.core.llm.alibaba.OpenAI", lambda **kwargs: client)
    llm = AlibabaBailianLLM({"api_key": "offline-test-key"})
    service = ChatService(asr=asr, tts=tts, llm=llm, config={"max_history_length": 100})
    preferences = UserPreferences(config_dir=str(tmp_path / "preferences"))
    monkeypatch.setattr("chatterpal.web.components.chat_tab.get_preferences_manager", lambda: preferences)
    return SimpleNamespace(
        service=service, tab=ChatTab(service), preferences=preferences,
        asr=asr, tts=tts, completions=completions, delays=delays,
        audio_bytes=audio_bytes, recognize=recognize, synthesize=synthesize,
        complete=complete,
    )


class TestResponseTimePerformance:
    @pytest.fixture(autouse=True)
    def configure(self, runtime):
        self.runtime = runtime
        self.asr_delay, self.tts_delay, self.llm_delay = 0.5, 0.8, 1.0
        runtime.delays.update(asr=self.asr_delay, tts=self.tts_delay, llm=self.llm_delay)

    def test_asr_response_time_performance(self):
        runtime = self.runtime
        audio = sample_audio()
        started = time.perf_counter()
        response, response_audio, session_id = runtime.service.chat_with_audio(audio, return_audio=False)
        elapsed = time.perf_counter() - started
        assert self.asr_delay + self.llm_delay <= elapsed <= self.asr_delay + self.llm_delay + 0.5
        assert response == RESPONSE_TEXT
        assert response_audio is None
        assert runtime.service.get_session(session_id) is not None
        runtime.asr.recognize_with_error_handling.assert_called_once_with(audio, max_retries=3)
        runtime.tts.synthesize_with_error_handling.assert_not_called()

    def test_tts_response_time_performance(self):
        texts = ["Hello", "Hello, how are you today? I hope you're doing well.",
                 "This is a longer text that should take more time to synthesize. "
                 "It contains multiple sentences and should test the TTS performance under realistic conditions."]
        for text in texts:
            started = time.perf_counter()
            result = self.runtime.service._synthesize_with_error_handling(text)
            elapsed = time.perf_counter() - started
            assert result == self.runtime.audio_bytes
            assert self.tts_delay <= elapsed <= self.tts_delay + 0.3
        assert self.runtime.tts.synthesize_with_error_handling.call_count == len(texts)

    def test_end_to_end_response_time(self):
        started = time.perf_counter()
        response, audio, session_id = self.runtime.service.chat_with_audio(sample_audio(3.0))
        elapsed = time.perf_counter() - started
        expected = self.asr_delay + self.llm_delay + self.tts_delay
        assert expected <= elapsed <= expected + 1.0
        assert response == RESPONSE_TEXT
        assert audio == self.runtime.audio_bytes
        assert session_id
        assert self.runtime.completions.call_count == 1
        assert self.runtime.asr.recognize_with_error_handling.call_count == 1

    def test_batch_processing_performance(self):
        session_id = self.runtime.service.create_session()
        times = []
        for i in range(5):
            started = time.perf_counter()
            response, returned_id = self.runtime.service.chat_with_text(f"Test message {i + 1}", session_id)
            times.append(time.perf_counter() - started)
            assert response == RESPONSE_TEXT
            assert returned_id == session_id
        assert max(times) <= self.llm_delay + 0.5
        assert statistics.mean(times) <= self.llm_delay + 0.5
        history = self.runtime.service.get_conversation_history(session_id)
        assert [item["content"] for item in history if item["role"] == "user"] == [f"Test message {i + 1}" for i in range(5)]

    def test_memory_usage_stability(self):
        # The memory workload retains all turns; artificial provider sleeps add
        # no allocation coverage and are measured by the latency tests above.
        self.runtime.delays.update(asr=0.0, tts=0.0, llm=0.0)
        # Measure current resident memory without an additional dependency.
        def resident_megabytes():
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes

                class ProcessMemoryCounters(ctypes.Structure):
                    _fields_ = [
                        ("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                    ]

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                psapi = ctypes.WinDLL("psapi", use_last_error=True)
                kernel32.GetCurrentProcess.restype = wintypes.HANDLE
                psapi.GetProcessMemoryInfo.argtypes = [
                    wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD,
                ]
                psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
                counters = ProcessMemoryCounters()
                counters.cb = ctypes.sizeof(counters)
                if not psapi.GetProcessMemoryInfo(
                    kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                return counters.WorkingSetSize / 1024**2
            rss = subprocess.check_output(
                ["ps", "-o", "rss=", "-p", str(os.getpid())], text=True
            )
            return int(rss.strip()) / 1024

        gc.collect()
        initial = resident_megabytes()
        session_id = self.runtime.service.create_session()
        for i in range(10):
            self.runtime.service.chat_with_text(f"Memory test message {i}", session_id)
            self.runtime.service.chat_with_audio(sample_audio(1.0), session_id)
        growth = resident_megabytes() - initial
        assert growth <= 50
        history = self.runtime.service.get_conversation_history(session_id)
        assert len([msg for msg in history if msg["role"] != "system"]) == 40
        assert [msg["content"] for msg in history if msg["role"] == "system"] == [self.runtime.service.default_system_prompt]


class TestAudioQualityAccuracy:
    @pytest.fixture(autouse=True)
    def configure(self, runtime):
        self.runtime = runtime
        self.results = []

        def recognize(audio, **kwargs):
            # This controlled ASR boundary models provider confidence. Assert its
            # result is propagated into real conversation history at each quality.
            rms = np.sqrt(np.mean(audio[1] ** 2))
            if rms > 0.5:
                text, confidence = "Perfect recognition result with high accuracy", 0.95
            elif rms > 0.4:
                text, confidence = "Good recognition result with some minor errors", 0.80
            else:
                text, confidence = "Poor recognition result with multiple errors", 0.60
            result = asr_result(text, confidence)
            self.results.append(result)
            return result

        runtime.asr.recognize_with_error_handling.side_effect = recognize

    def recognize(self, quality, duration=2.0):
        response, audio, session_id = self.runtime.service.chat_with_audio(sample_audio(duration, quality), return_audio=False)
        assert response == RESPONSE_TEXT
        assert audio is None
        result = self.results[-1]
        history = self.runtime.service.get_conversation_history(session_id)
        turns = [msg for msg in history if msg["role"] != "system"]
        assert turns == [
            {"role": "user", "content": result.text},
            {"role": "assistant", "content": RESPONSE_TEXT},
        ]
        return result

    def test_high_quality_audio_recognition(self):
        result = self.recognize("high")
        assert result.confidence >= 0.9
        assert "Perfect" in result.text

    def test_medium_quality_audio_recognition(self):
        result = self.recognize("medium")
        assert 0.7 <= result.confidence < 0.9
        assert "Good" in result.text

    def test_low_quality_audio_recognition(self):
        result = self.recognize("low")
        assert result.confidence < 0.7
        assert "Poor" in result.text

    def test_audio_quality_degradation_handling(self):
        results = [self.recognize(quality) for quality in ["high", "medium", "low"]]
        assert results[0].confidence > results[1].confidence > results[2].confidence
        assert "Poor" in results[2].text
        assert self.runtime.asr.recognize_with_error_handling.call_count == 3

    def test_different_audio_durations(self):
        for duration in [0.5, 1.0, 2.0, 5.0]:
            started = time.perf_counter()
            self.recognize("high", duration)
            assert time.perf_counter() - started <= 3.0


class TestConcurrentUserScenarios:
    @pytest.fixture(autouse=True)
    def configure(self, runtime):
        self.runtime = runtime
        runtime.delays.update(asr=0.1, tts=0.2, llm=0.3)
        self.barrier = None

    def run_users(self, text_users, voice_users, messages=1):
        count = text_users + voice_users
        barrier = threading.Barrier(count)

        def user(user_id):
            session_id = self.runtime.service.create_session()
            barrier.wait(timeout=10)
            results = []
            for i in range(messages):
                started = time.perf_counter()
                if user_id < text_users:
                    text = f"User {user_id} unique message {i + 1}"
                    response, returned_id = self.runtime.service.chat_with_text(text, session_id)
                    assert returned_id == session_id
                    audio = None
                else:
                    response, audio, returned_id = self.runtime.service.chat_with_audio(sample_audio(1.0), session_id)
                    assert returned_id == session_id
                    assert audio == self.runtime.audio_bytes
                results.append((time.perf_counter() - started, response, audio))
            return user_id, session_id, results

        with concurrent.futures.ThreadPoolExecutor(max_workers=count) as executor:
            futures = [executor.submit(user, i) for i in range(count)]
            results = [future.result(timeout=30) for future in futures]
        assert len(results) == count
        assert len({result[1] for result in results}) == count
        for _, _, turns in results:
            assert len(turns) == messages
            assert all(turn[1] == RESPONSE_TEXT for turn in turns)
        return results

    def test_concurrent_text_conversations(self):
        results = self.run_users(5, 0, messages=3)
        times = [turn[0] for _, _, turns in results for turn in turns]
        assert len(times) == 15
        assert max(times) <= 2.0
        assert statistics.mean(times) <= 1.0

    def test_concurrent_voice_conversations(self):
        results = self.run_users(0, 3)
        times = [turn[0] for _, _, turns in results for turn in turns]
        assert max(times) <= 5.0
        assert statistics.mean(times) <= 3.0
        assert all(turn[2] for _, _, turns in results for turn in turns)

    def test_mixed_concurrent_operations(self):
        results = self.run_users(3, 2)
        text_times = [turn[0] for user, _, turns in results if user < 3 for turn in turns]
        voice_times = [turn[0] for user, _, turns in results if user >= 3 for turn in turns]
        assert len(text_times) == 3
        assert len(voice_times) == 2
        assert statistics.mean(text_times) <= 1.0
        assert statistics.mean(voice_times) <= 3.0

    def test_session_isolation_under_load(self):
        results = self.run_users(5, 0, messages=3)
        for user_id, session_id, _ in results:
            history = self.runtime.service.get_conversation_history(session_id)
            assert len([msg for msg in history if msg["role"] != "system"]) == 6
            assert [msg["content"] for msg in history if msg["role"] == "system"] == [self.runtime.service.default_system_prompt]
            assert [msg["content"] for msg in history if msg["role"] == "user"] == [
                f"User {user_id} unique message {i + 1}" for i in range(3)
            ]
            assert all(msg["content"] == RESPONSE_TEXT for msg in history if msg["role"] == "assistant")
        # Inspect actual SDK inputs to catch cross-session context leakage.
        for call in self.runtime.completions.call_args_list:
            user_messages = [msg["content"] for msg in call.kwargs["messages"] if msg["role"] == "user"]
            assert len({message.split()[1] for message in user_messages}) == 1


class TestUIResponsivenessAndFluidity:
    def test_input_mode_switching_responsiveness(self, runtime):
        times = []
        current_mode = True
        for _ in range(10):
            started = time.perf_counter()
            new_mode, audio, text, _, _ = runtime.tab._toggle_input_mode(current_mode)
            times.append(time.perf_counter() - started)
            assert new_mode is not current_mode
            assert audio["visible"] is not new_mode
            assert text["visible"] is new_mode
            current_mode = new_mode
        assert max(times) <= 0.1
        assert statistics.mean(times) <= 0.05

    def test_chat_interaction_responsiveness(self, runtime):
        text_times, voice_times = [], []
        for use_text, count, times in [(True, 5, text_times), (False, 3, voice_times)]:
            for i in range(count):
                started = time.perf_counter()
                audio, history = runtime.tab._handle_chat(
                    None if use_text else sample_audio(1.0), f"Test message {i}" if use_text else "", [], use_text,
                )
                times.append(time.perf_counter() - started)
                assert isinstance(audio, tuple)
                assert audio[0] == 16000
                assert len(audio[1]) == 16000
                assert isinstance(history, list)
                assert history[-1][1] == RESPONSE_TEXT
        assert statistics.mean(text_times) <= 1.0
        assert statistics.mean(voice_times) <= 2.0

    def test_preference_persistence_performance(self, runtime):
        save_times = []
        for method, values in [
            (runtime.preferences.set_input_mode, ["text", "voice"]),
            (runtime.preferences.set_show_history, [True, False]),
            (runtime.preferences.set_auto_play_response, [True, False]),
        ]:
            for value in values:
                started = time.perf_counter()
                assert method(value)
                save_times.append(time.perf_counter() - started)
        assert max(save_times) <= 0.1
        assert statistics.mean(save_times) <= 0.05
        reloaded = UserPreferences(config_dir=str(runtime.preferences.config_dir))
        assert reloaded.get_input_mode() == "voice"
        assert reloaded.get_show_history() is False
        assert reloaded.get_auto_play_response() is False
        load_times = []
        for _ in range(10):
            started = time.perf_counter()
            assert runtime.preferences.get_input_mode() == "voice"
            assert runtime.preferences.get_show_history() is False
            assert runtime.preferences.get_auto_play_response() is False
            load_times.append(time.perf_counter() - started)
        assert max(load_times) <= 0.05
        assert statistics.mean(load_times) <= 0.02

    def test_error_handling_ui_responsiveness(self, runtime):
        times = []
        boundaries = [runtime.asr.recognize_with_error_handling,
                      runtime.tts.synthesize_with_error_handling, runtime.completions]
        normal = [runtime.recognize, runtime.synthesize, runtime.complete]
        for i, boundary in enumerate(boundaries):
            boundary.side_effect = RuntimeError("provider unavailable")
            started = time.perf_counter()
            audio, history = runtime.tab._handle_chat(
                sample_audio(1.0) if i == 0 else None,
                "" if i == 0 else "Test error handling", [], i != 0,
            )
            times.append(time.perf_counter() - started)
            assert audio is None
            assert isinstance(history, list) and history
            if i == 1:
                assert history[-1][1] == RESPONSE_TEXT
            else:
                assert history[-1][1] != RESPONSE_TEXT
                assert history[-1][1]
            boundary.side_effect = normal[i]
        assert all(elapsed <= 2.0 for elapsed in times)
        assert statistics.mean(times) <= 1.0

    def test_ui_state_consistency_under_load(self, runtime):
        operations = []
        started = time.perf_counter()
        for i in range(20):
            runtime.tab._toggle_input_mode(i % 2 == 0)
            chatbot, panel = runtime.tab._toggle_history_display(i % 3 == 0)
            assert chatbot["visible"] == panel["visible"] == (i % 3 == 0)
            if i % 2 == 0:
                audio, history = runtime.tab._handle_chat(None, f"Rapid message {i}", [], True)
                assert isinstance(audio, tuple)
                assert history[-1][1] == RESPONSE_TEXT
            operations.append(i)
        elapsed = time.perf_counter() - started
        assert len(operations) == 20
        assert runtime.preferences.get_input_mode() == "text"
        assert runtime.preferences.get_show_history() is False
        assert elapsed / len(operations) <= 0.1
