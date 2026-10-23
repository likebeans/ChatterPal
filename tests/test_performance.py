"""Offline performance checks for caches and audio processing."""

import concurrent.futures
import threading
import time
from unittest.mock import Mock, patch

import pytest

from chatterpal.utils import audio_optimizer, cache as cache_module
from chatterpal.utils.audio_optimizer import (
    AudioBuffer,
    AudioProcessingTask,
    AudioProcessor,
    OptimizedTTSService,
    PerformanceMonitor,
    get_performance_monitor,
    time_operation,
)
from chatterpal.utils.cache import (
    CacheManager,
    LRUCache,
    SessionCache,
    TTSCache,
    get_cache_manager,
)


@pytest.fixture(autouse=True)
def isolated_caches(tmp_path, monkeypatch):
    """Keep disk caches and singleton metrics independent of the checkout."""
    settings = Mock()
    settings.get_cache_path.return_value = tmp_path
    monkeypatch.setattr(cache_module, "get_settings", lambda: settings)
    monkeypatch.setattr(cache_module, "_cache_manager", None)
    monkeypatch.setattr(audio_optimizer, "_performance_monitor", None)
    # The perpetual maintenance thread is unrelated to these request timings.
    monkeypatch.setattr(CacheManager, "_start_cleanup_task", lambda self: None)


@pytest.fixture
def processor():
    instance = AudioProcessor(max_workers=4)
    yield instance
    instance.shutdown()


@pytest.fixture
def optimized_tts():
    instances = []

    def create(tts_service, **kwargs):
        instance = OptimizedTTSService(tts_service, **kwargs)
        instances.append(instance)
        return instance

    yield create
    for instance in instances:
        if instance.enable_preprocessing:
            instance.preprocessing_queue.put(None)
            instance.audio_processor.shutdown()


class TestLRUCache:
    def test_cache_performance(self):
        cache = LRUCache(max_size=1000)
        started = time.perf_counter()
        for i in range(1000):
            cache.put(f"key_{i}", f"value_{i}")
        write_time = time.perf_counter() - started

        started = time.perf_counter()
        for i in range(1000):
            assert cache.get(f"key_{i}") == f"value_{i}"
        read_time = time.perf_counter() - started
        assert write_time < 1.0
        assert read_time < 0.5

    def test_concurrent_access(self):
        cache = LRUCache(max_size=1000)

        def write(start):
            for i in range(start, start + 250):
                cache.put(f"key_{i}", f"value_{i}")

        def read(start):
            for i in range(start, start + 250):
                assert cache.get(f"key_{i}") == f"value_{i}"

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            started = time.perf_counter()
            list(executor.map(write, range(0, 1000, 250)))
            write_time = time.perf_counter() - started
            started = time.perf_counter()
            list(executor.map(read, range(0, 1000, 250)))
            read_time = time.perf_counter() - started
        assert write_time < 2.0
        assert read_time < 1.0
        assert cache.get_stats()["size"] == 1000

    def test_memory_usage(self):
        cache = LRUCache(max_size=10000)
        for i in range(1000):
            cache.put(f"key_{i}", "x" * 1000)
        stats = cache.get_stats()
        assert stats["size"] == 1000
        assert stats["total_size_bytes"] == 1_000_000


class TestTTSCache:
    def test_tts_cache_performance(self, tmp_path):
        cache = TTSCache(cache_dir=str(tmp_path), max_memory_size=100)
        audio_data = b"fake_audio_data" * 1000
        started = time.perf_counter()
        for i in range(100):
            cache.put(f"text_{i}", "voice", audio_data)
        store_time = time.perf_counter() - started

        started = time.perf_counter()
        for i in range(100):
            assert cache.get(f"text_{i}", "voice") == audio_data
        retrieve_time = time.perf_counter() - started
        assert store_time < 2.0
        assert retrieve_time < 0.5
        stats = cache.get_stats()
        assert stats["stats"]["memory_hits"] == 100
        assert stats["hit_rate"] > 0.8

    def test_disk_cache_performance(self, tmp_path):
        cache = TTSCache(cache_dir=str(tmp_path), max_memory_size=10)
        audio_data = b"fake_audio_data" * 1000
        for i in range(50):
            cache.put(f"text_{i}", "voice", audio_data)
        cache._memory_cache.clear()

        started = time.perf_counter()
        for i in range(20):
            assert cache.get(f"text_{i}", "voice") == audio_data
        assert time.perf_counter() - started < 1.0
        assert cache.get_stats()["stats"]["disk_hits"] == 20


class TestAudioProcessor:
    def test_audio_processing_performance(self, processor):
        audio_data = b"\x00\x01" * 16000
        started = time.perf_counter()
        for _ in range(10):
            converted = processor.convert_sample_rate(audio_data, 16000, 22050)
            assert len(converted) > len(audio_data)
        convert_time = time.perf_counter() - started

        started = time.perf_counter()
        for _ in range(10):
            normalized = processor.normalize_volume(audio_data, 0.8)
            assert len(normalized) == len(audio_data)
        normalize_time = time.perf_counter() - started
        assert convert_time < 2.0
        assert normalize_time < 1.0
        stats = processor.get_stats()
        assert stats["tasks_processed"] == 20
        assert stats["tasks_failed"] == 0
        assert stats["average_processing_time"] > 0

    def test_concurrent_audio_processing(self, processor):
        audio_data = b"\x00\x01" * 8000
        tasks = [
            AudioProcessingTask(
                task_id=f"task_{i}",
                audio_data=audio_data,
                operation="normalize_volume",
                params={"target_volume": 0.8},
            )
            for i in range(20)
        ]
        started = time.perf_counter()
        futures = [processor.process_async(task) for task in tasks]
        for future in futures:
            assert len(future.result(timeout=5)) == len(audio_data)
        assert time.perf_counter() - started < 5.0
        assert processor.get_stats()["tasks_processed"] == 20


class TestAudioBuffer:
    def test_buffer_throughput(self):
        buffer = AudioBuffer(max_size=100)
        audio_data = b"audio_chunk" * 100
        started = time.perf_counter()
        for _ in range(100):
            assert buffer.put(audio_data, timeout=1.0)
        write_time = time.perf_counter() - started
        assert buffer.is_full()

        started = time.perf_counter()
        for _ in range(100):
            assert buffer.get(timeout=1.0) == audio_data
        read_time = time.perf_counter() - started
        assert write_time < 1.0
        assert read_time < 1.0
        assert buffer.is_empty()


class TestOptimizedTTSService:
    def test_tts_optimization_performance(self, optimized_tts):
        # Model provider latency, so cache timing measures saved work.
        def synthesize(*args):
            time.sleep(0.02)
            return b"fake_audio_data" * 1000

        provider = Mock()
        provider.synthesize.side_effect = synthesize
        service = optimized_tts(provider, enable_cache=True, enable_preprocessing=False)
        started = time.perf_counter()
        result1 = service.synthesize("Hello world", "voice")
        first_time = time.perf_counter() - started
        started = time.perf_counter()
        result2 = service.synthesize("Hello world", "voice")
        cached_time = time.perf_counter() - started
        assert result1 == result2 == b"fake_audio_data" * 1000
        assert cached_time <= first_time * 1.5
        provider.synthesize.assert_called_once_with("Hello world", "voice", "+0%", "+0%")
        assert service.get_cache_stats()["stats"]["memory_hits"] == 1

    def test_preloading_performance(self, optimized_tts):
        provider = Mock()
        provider.synthesize.return_value = b"fake_audio_data" * 500
        service = optimized_tts(provider, enable_preprocessing=True)
        phrases = [
            "Hello", "How are you", "Thank you", "Goodbye",
            "Please", "Sorry", "Excuse me", "You're welcome",
        ]
        service.preload_common_phrases(phrases)
        # Wait for observable cache completion instead of a fixed sleep.
        deadline = time.monotonic() + 2.0
        while service.get_cache_stats()["stats"]["stores"] < len(phrases):
            assert time.monotonic() < deadline, "Preloading did not finish within 2 seconds"
            threading.Event().wait(0.005)
        assert provider.synthesize.call_count == len(phrases)
        started = time.perf_counter()
        for phrase in phrases:
            assert service.synthesize(phrase) == provider.synthesize.return_value
        assert time.perf_counter() - started < 0.1
        assert provider.synthesize.call_count == len(phrases)


class TestPerformanceMonitor:
    def test_timing_accuracy(self):
        monitor = PerformanceMonitor()
        # A controlled clock verifies exact arithmetic without scheduler jitter.
        with patch.object(audio_optimizer.time, "time", side_effect=[10, 10, 10.1, 20, 20, 20.5]):
            short = monitor.end_timing(monitor.start_timing("short_operation"))
            long = monitor.end_timing(monitor.start_timing("long_operation"))
        assert 0.09 <= short <= 0.15
        assert 0.45 <= long <= 0.55
        assert short == pytest.approx(0.1)
        assert long == pytest.approx(0.5)

    def test_operation_statistics(self):
        monitor = PerformanceMonitor()
        for i in range(10):
            started = float(i + 1)
            with patch.object(audio_optimizer.time, "time", side_effect=[started, started, started + 0.01 * (i + 1)]):
                monitor.end_timing(monitor.start_timing("test_operation"))
        stats = monitor.get_operation_stats("test_operation")
        assert stats["count"] == 10
        assert stats["total_time"] == pytest.approx(0.55)
        assert stats["average_time"] == pytest.approx(0.055)
        assert stats["min_time"] > 0
        assert stats["max_time"] > stats["min_time"]

    def test_timing_decorator(self):
        @time_operation("decorated_function")
        def measured_function():
            return "result"

        with patch.object(audio_optimizer.time, "time", side_effect=[10, 10, 10.1]):
            assert measured_function() == "result"
        stats = get_performance_monitor().get_operation_stats("decorated_function")
        assert stats["count"] == 1
        assert stats["average_time"] >= 0.09


class TestSessionCache:
    def test_session_cache_performance(self):
        cache = SessionCache(max_sessions=1000, session_ttl_seconds=3600)
        started = time.perf_counter()
        for i in range(1000):
            cache.put_session(f"session_{i}", {
                "user_id": f"user_{i}",
                "messages": [f"message_{j}" for j in range(10)],
                "created_at": time.time(),
            })
        store_time = time.perf_counter() - started
        started = time.perf_counter()
        for i in range(1000):
            session = cache.get_session(f"session_{i}")
            assert session["user_id"] == f"user_{i}"
            assert len(session["messages"]) == 10
        retrieve_time = time.perf_counter() - started
        assert store_time < 1.0
        assert retrieve_time < 0.5
        assert cache.get_stats()["size"] == 1000

    def test_concurrent_session_access(self):
        cache = SessionCache(max_sessions=1000)

        def worker(start):
            for i in range(start, start + 100):
                data = {"data": f"session_{i}"}
                cache.put_session(f"session_{i}", data)
                assert cache.get_session(f"session_{i}") == data

        started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(worker, range(0, 400, 100)))
        assert time.perf_counter() - started < 2.0
        assert cache.get_stats()["size"] == 400


class TestCacheManager:
    def test_integrated_cache_performance(self):
        manager = get_cache_manager()
        assert get_cache_manager() is manager
        audio_data = b"test_audio" * 1000
        started = time.perf_counter()
        for i in range(100):
            manager.get_tts_cache().put(f"text_{i}", "voice", audio_data)
        tts_store_time = time.perf_counter() - started
        started = time.perf_counter()
        for i in range(100):
            manager.get_session_cache().put_session(f"session_{i}", {
                "messages": [f"msg_{j}" for j in range(5)],
            })
        session_store_time = time.perf_counter() - started
        overall_stats = manager.get_overall_stats()
        assert overall_stats["tts"]["stats"]["stores"] == 100
        assert overall_stats["session"]["size"] == 100
        assert tts_store_time < 2.0
        assert session_store_time < 1.0
