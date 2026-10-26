"""Synchronous learning port backed by the application's configured providers."""

from typing import Any, Callable

from .observability import RuntimeMetrics, active_metrics
from .ports import LearningProvider


class ProviderError(RuntimeError):
    """A configured provider is absent, failed, or returned unusable data."""


class ExistingProviders(LearningProvider):
    """Reuse provider instances; execution and persistence belong to the runtime."""

    def __init__(
        self,
        asr: Any = None,
        llm: Any = None,
        tts: Any = None,
        *,
        metrics: RuntimeMetrics | None = None,
    ):
        self.asr = asr
        self.llm = llm
        self.tts = tts
        self.metrics = metrics if metrics is not None else RuntimeMetrics()

    @staticmethod
    def _method(provider: Any, method_name: str, name: str) -> Callable:
        method = getattr(provider, method_name, None)
        if not callable(method):
            raise ProviderError(f"{name} provider is not configured")
        return method

    @staticmethod
    def _call(method: Callable, value: Any, name: str) -> Any:
        try:
            return method(value)
        except Exception as exc:
            raise ProviderError(f"{name} provider failed") from exc

    @staticmethod
    def _text(value: Any, name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ProviderError(f"{name} provider returned empty or invalid text")
        return value

    def recognize(self, audio: bytes) -> str:
        if not isinstance(audio, bytes) or not audio:
            raise ValueError("Recognition requires nonempty audio bytes")
        with active_metrics(self.metrics).measure("asr"):
            method = self._method(self.asr, "recognize", "ASR")
            return self._text(self._call(method, audio, "ASR"), "ASR")

    def reply(self, messages: list[dict[str, str]]) -> str:
        if not isinstance(messages, list) or not messages:
            raise ValueError("A reply requires nonempty messages")
        for message in messages:
            if (
                not isinstance(message, dict)
                or message.get("role") not in ("system", "user", "assistant")
                or not isinstance(message.get("content"), str)
                or not message["content"].strip()
            ):
                raise ValueError("Messages require a valid role and nonempty text")
        with active_metrics(self.metrics).measure("llm"):
            method = self._method(self.llm, "chat", "LLM")
            return self._text(self._call(method, messages, "LLM"), "LLM")

    def synthesize(self, text: str) -> bytes:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Synthesis requires nonempty text")
        with active_metrics(self.metrics).measure("tts"):
            method = self._method(self.tts, "synthesize", "TTS")
            audio = self._call(method, text, "TTS")
            if not isinstance(audio, (bytes, bytearray, memoryview)):
                raise ProviderError("TTS provider returned invalid audio bytes")
            try:
                audio = bytes(audio)
            except (TypeError, ValueError) as exc:
                raise ProviderError(
                    "TTS provider returned invalid audio bytes"
                ) from exc
            if not audio:
                raise ProviderError("TTS provider returned empty audio bytes")
            return audio

    def snapshot(self) -> dict:
        return self.metrics.snapshot()
