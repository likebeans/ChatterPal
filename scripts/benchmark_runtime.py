"""Repeatable offline lifecycle load with controlled ASR/LLM/TTS boundaries.

Run from any directory with the checkout's src on PYTHONPATH, for example:
  PYTHONPATH=src python scripts/benchmark_runtime.py --iterations 120 --concurrency 4

This measures local orchestration, not real provider or model performance. The
benchmark owns a temporary directory and removes it on exit. Durable SQLite size
is reported separately from transient uploads, generated audio and synthesis cache.
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import io
import json
import logging
import math
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
from threading import Event, Lock, Thread
from time import perf_counter
from types import SimpleNamespace
import wave

from chatterpal.core.tts.base import TTSBase
from chatterpal.learning.adapters import ExistingProviders
from chatterpal.learning.observability import safe_error_type
from chatterpal.learning.runtime import LearningRuntime
from chatterpal.learning.storage import LearningStore, StoreError
from chatterpal.services.chat import ChatService
from chatterpal.web.audio_assets import GeneratedAudioStore


def disk_usage(directory):
    sizes = []
    for path in directory.rglob("*"):
        try:
            if path.is_file():
                sizes.append(path.stat().st_size)
        except FileNotFoundError:
            pass
    return {"files": len(sizes), "bytes": sum(sizes)}


def rss():
    """Current and process high-water RSS, in bytes (macOS and Linux)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak = int(peak if sys.platform == "darwin" else peak * 1024)
    try:
        import os

        current = (
            int(
                subprocess.check_output(
                    ["ps", "-o", "rss=", "-p", str(os.getpid())], text=True
                ).strip()
            )
            * 1024
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        current = None
    return {"current_bytes": current, "process_peak_bytes": peak}


def latency_summary(samples, errors, elapsed):
    ordered = sorted(samples)

    def percentile(fraction):
        return (
            ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else None
        )

    failures = sum(errors.values())
    return {
        "requests": len(ordered),
        "elapsed_seconds": elapsed,
        "p50_seconds": percentile(0.50),
        "p95_seconds": percentile(0.95),
        "throughput_per_second": len(ordered) / elapsed,
        "successful_per_second": (len(ordered) - failures) / elapsed,
        "errors": dict(errors),
        "error_rate": failures / len(ordered) if ordered else 0,
    }


class ControlledASR:
    def __init__(self, delay):
        self.delay = delay

    def recognize(self, audio):
        Event().wait(self.delay)
        return audio.decode("utf-8")

    def recognize_with_error_handling(self, audio, **kwargs):
        data = Path(audio).read_bytes() if isinstance(audio, str) else audio
        return SimpleNamespace(text=self.recognize(data), confidence=1.0)


class ControlledLLM:
    def __init__(self, delay):
        self.delay = delay

    def chat(self, messages, **kwargs):
        Event().wait(self.delay)
        return f"Controlled reply to {messages[-1]['content']}"


class ControlledTTS(TTSBase):
    def __init__(self, delay, cache_dir):
        super().__init__(
            {"cache_dir": str(cache_dir), "cache_size_limit": 8, "cache_ttl": 60}
        )
        self.delay = delay
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x01" * 512)
        self.audio = output.getvalue()

    def synthesize(self, text, **kwargs):
        Event().wait(self.delay)
        return self.audio

    def synthesize_to_file(self, text, output_path, **kwargs):
        Path(output_path).write_bytes(self.synthesize(text, **kwargs))
        return True


class Sampler:
    def __init__(self, transient, chat):
        self.transient, self.chat = transient, chat
        self.stop = Event()
        self.peak = {"files": 0, "bytes": 0, "sessions": 0}
        self.thread = Thread(
            target=self._run, name="runtime-benchmark-sampler", daemon=True
        )

    def sample(self):
        usage = disk_usage(self.transient)
        snapshot = self.chat.runtime_snapshot()
        for key in ("files", "bytes"):
            self.peak[key] = max(self.peak[key], usage[key])
        self.peak["sessions"] = max(self.peak["sessions"], snapshot["sessions"])

    def _run(self):
        while not self.stop.wait(0.01):
            self.sample()

    def __enter__(self):
        self.sample()
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join()
        self.sample()


def parallel_load(iterations, concurrency, invoke):
    samples, errors = [], Counter()
    lock = Lock()

    def request(index):
        started = perf_counter()
        error_kind = None
        try:
            error_kind = invoke(index)
        except Exception as error:
            error_kind = (
                error.code if isinstance(error, StoreError) else safe_error_type(error)
            )
        finally:
            with lock:
                samples.append(perf_counter() - started)
                if error_kind:
                    errors[error_kind] += 1

    started = perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        list(executor.map(request, range(iterations)))
    return latency_summary(samples, errors, perf_counter() - started)


def benchmark(args):
    with tempfile.TemporaryDirectory(
        prefix="chatterpal-runtime-benchmark-"
    ) as directory:
        root = Path(directory)
        transient = root / "transient"
        uploads = transient / "uploads"
        uploads.mkdir(parents=True)
        providers = ExistingProviders(
            asr=ControlledASR(args.delay_ms / 1000),
            llm=ControlledLLM(args.delay_ms / 1000),
            tts=ControlledTTS(args.delay_ms / 1000, transient / "cache"),
        )
        chat = ChatService(
            asr=providers.asr,
            llm=providers.llm,
            tts=providers.tts,
            config={"max_sessions": 8, "max_history_length": 20, "session_timeout": 60},
        )
        now = [1000.0]
        audio = GeneratedAudioStore(
            transient / "generated",
            max_files=16,
            max_bytes=64 * 1024,
            ttl=60,
            clock=lambda: now[0],
        )
        before_rss, before_disk = rss(), disk_usage(transient)

        def legacy(index):
            # Every round creates churn beyond the eight-session retention limit.
            # Unknown IDs preserve the existing API behavior of assigning a new ID.
            path = uploads / f"input_{index}.wav"
            try:
                path.write_bytes(f"controlled input {index}".encode())
                result = chat.chat_with_audio_result(str(path), f"new-{index}")
                if result.response_audio is None:
                    return "tts_failed"
                audio.save(result.response_audio, "wav")
            finally:
                path.unlink(missing_ok=True)

        with Sampler(transient, chat) as sampler:
            legacy_result = parallel_load(args.iterations, args.concurrency, legacy)
            history_session = chat.create_session()
            for turn in range(40):
                chat.chat_with_text(f"history probe {turn}", history_session)
        legacy_snapshot = chat.runtime_snapshot()
        retained_lengths = [
            len(session.conversation.messages) for session in chat.sessions.values()
        ]
        after_legacy = disk_usage(transient)
        assets = audio.snapshot()
        cache = providers.tts.get_cache_stats()

        store = LearningStore(root / "durable" / "benchmark.sqlite3")
        owner = store.bootstrap()[0]["learner_id"]
        runtime = LearningRuntime(
            store,
            max_workers=args.workers,
            max_pending=args.pending,
            operation_timeout=30,
            shutdown_timeout=30,
        )

        def learning(index):
            def work(claim):
                text = providers.recognize(b"controlled learning input")
                reply = providers.reply([{"role": "user", "content": text}])
                providers.synthesize(reply)
                return {"result": {"completed": True}}

            operation = runtime.submit(owner, f"benchmark-{index}", "turn", {}, work)
            deadline = perf_counter() + 35
            while operation["status"] == "pending":
                if perf_counter() >= deadline:
                    return "poll_timeout"
                Event().wait(0.001)
                operation = store.get_operation(
                    owner, operation_id=operation["operation_id"]
                )
            return None if operation["status"] == "succeeded" else operation["status"]

        try:
            learning_result = parallel_load(args.iterations, args.concurrency, learning)
        finally:
            runtime.shutdown()
        learning_snapshot = runtime.snapshot()

        # Advance controlled idle time; no sleep or active worker is needed to
        # demonstrate that expiry removes generated files and idle sessions.
        now[0] += 61
        audio.cleanup()
        for session in chat.sessions.values():
            session.last_activity = datetime.now() - timedelta(seconds=61)
        expired = chat.cleanup_expired_sessions()
        providers.tts.clear_cache()
        after_cleanup = disk_usage(transient)
        return {
            "mode": "controlled offline providers; not real model/cloud performance",
            "configuration": vars(args),
            "legacy_chat": {
                **legacy_result,
                "snapshot": legacy_snapshot,
                "history_probe_turns": 40,
                "max_retained_messages": max(retained_lengths, default=0),
                "generated_audio": assets,
                "tts_cache": {
                    "entries": cache["entries"],
                    "bytes": cache["total_size_bytes"],
                },
            },
            "learning_runtime": {**learning_result, "snapshot": learning_snapshot},
            "resources": {
                "rss_before": before_rss,
                "rss_after": rss(),
                "transient_before": before_disk,
                "transient_peak_sampled": sampler.peak,
                "transient_after_load": after_legacy,
                "transient_after_expiry_and_cache_clear": after_cleanup,
                "expired_sessions": expired,
                "sessions_after_expiry": len(chat.sessions),
                "uploads_remaining": len(list(uploads.iterdir())),
                "durable_sqlite": disk_usage(root / "durable"),
            },
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=120)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--pending", type=int, default=8)
    parser.add_argument("--delay-ms", type=float, default=5.0)
    args = parser.parse_args()
    if not (1 <= args.iterations <= 100000 and 1 <= args.concurrency <= 64):
        parser.error("iterations must be 1..100000 and concurrency 1..64")
    if not (1 <= args.workers <= args.pending <= 256):
        parser.error("workers must be 1..pending, with pending at most 256")
    if not math.isfinite(args.delay_ms) or not 0 <= args.delay_ms <= 1000:
        parser.error("delay-ms must be finite and between 0 and 1000")
    logging.basicConfig(level=logging.CRITICAL)
    print(json.dumps(benchmark(args), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
