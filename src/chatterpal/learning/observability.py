"""Small local timing aggregates with fixed labels and bounded recent samples.

Only durations, known statuses and allowlisted exception classes are retained.
No caller-provided labels, text, provider responses or exception messages enter
these snapshots. Percentiles describe the recent local sample, not a provider SLA.
"""

from collections import Counter, deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import math
from threading import RLock
from time import perf_counter
from typing import Iterator


_STAGES = frozenset(
    {
        "asr",
        "llm",
        "tts",
        "chat_text",
        "chat_audio",
        "chat_queue",
        "operation_queue",
        "operation_total",
        "worker_total",
    }
)
_STATUSES = frozenset({"succeeded", "failed", "interrupted", "unavailable"})
_ERRORS = frozenset(
    {
        "ProviderError",
        "StoreError",
        "ASRError",
        "LLMError",
        "TTSError",
        "ChatModuleError",
        "SpeechRecognitionError",
        "SpeechSynthesisError",
        "AudioInputError",
        "TimeoutError",
        "CancelledError",
        "RuntimeError",
        "OSError",
        "ValueError",
        "TypeError",
        "Exception",
    }
)
_COUNTERS = frozenset(
    {
        "admitted",
        "recovered",
        "rejected_busy",
        "rejected_stopping",
        "sessions_created",
        "sessions_expired",
        "sessions_evicted",
        "sessions_rejected",
    }
)
_CURRENT: ContextVar["RuntimeMetrics | None"] = ContextVar(
    "runtime_metrics", default=None
)


def safe_error_type(error: BaseException | None) -> str | None:
    """Never turn an exception message or a dynamic class name into a label."""
    if error is None:
        return None
    name = type(error).__name__
    return name if name in _ERRORS else "OtherError"


@dataclass
class TimingOutcome:
    status: str = "succeeded"
    error: BaseException | None = None


class RuntimeMetrics:
    """Thread-safe lifetime counters and a capped window per fixed stage."""

    def __init__(self, *, max_samples: int = 256):
        if (
            isinstance(max_samples, bool)
            or not isinstance(max_samples, int)
            or not 1 <= max_samples <= 4096
        ):
            raise ValueError("Timing sample limit must be between 1 and 4096")
        self.max_samples = max_samples
        self._lock = RLock()
        self._stages: dict[str, dict] = {}
        self._counters: Counter = Counter()

    def increment(self, name: str) -> None:
        if name in _COUNTERS:
            with self._lock:
                self._counters[name] += 1

    def observe(
        self,
        stage: str,
        seconds: float,
        *,
        status: str = "succeeded",
        error: BaseException | None = None,
    ) -> None:
        if stage not in _STAGES:
            return
        if (
            not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or seconds < 0
        ):
            return
        status = status if status in _STATUSES else "failed"
        error_type = safe_error_type(error)
        with self._lock:
            bucket = self._stages.setdefault(
                stage,
                {
                    "count": 0,
                    "total_seconds": 0.0,
                    "max_seconds": 0.0,
                    "samples": deque(maxlen=self.max_samples),
                    "statuses": Counter(),
                    "errors": Counter(),
                },
            )
            bucket["count"] += 1
            bucket["total_seconds"] += seconds
            bucket["max_seconds"] = max(bucket["max_seconds"], seconds)
            bucket["samples"].append(seconds)
            bucket["statuses"][status] += 1
            if error_type:
                bucket["errors"][error_type] += 1

    @contextmanager
    def measure(self, stage: str) -> Iterator[TimingOutcome]:
        started = perf_counter()
        outcome = TimingOutcome()
        try:
            yield outcome
        except BaseException as error:
            outcome.status = "failed" if isinstance(error, Exception) else "interrupted"
            outcome.error = error
            raise
        finally:
            self.observe(
                stage,
                perf_counter() - started,
                status=outcome.status,
                error=outcome.error,
            )

    def snapshot(self) -> dict:
        with self._lock:
            stages = {}
            for stage, bucket in self._stages.items():
                samples = sorted(bucket["samples"])
                size = len(samples)
                stages[stage] = {
                    "count": bucket["count"],
                    "total_seconds": bucket["total_seconds"],
                    "max_seconds": bucket["max_seconds"],
                    "sample_count": size,
                    "p50_seconds": samples[max(0, math.ceil(size * 0.50) - 1)],
                    "p95_seconds": samples[max(0, math.ceil(size * 0.95) - 1)],
                    "statuses": dict(bucket["statuses"]),
                    "errors": dict(bucket["errors"]),
                }
            return {
                "max_samples": self.max_samples,
                "counters": dict(self._counters),
                "stages": stages,
            }


@contextmanager
def metrics_context(metrics: RuntimeMetrics) -> Iterator[None]:
    token = _CURRENT.set(metrics)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def active_metrics(fallback: RuntimeMetrics) -> RuntimeMetrics:
    """Adapters inside runtime workers contribute directly to runtime snapshots."""
    return _CURRENT.get() or fallback
