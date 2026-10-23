"""Offline regressions for persistent TTS results and optional playback."""

import json
import pickle
from unittest.mock import Mock

import pytest

from chatterpal.core.tts.base import PlaybackState, TTSBase


class OfflineTTS(TTSBase):
    """Exercise the base pipeline with a deterministic provider boundary."""

    def __init__(self, config=None):
        super().__init__(config)
        self.provider = Mock(return_value=b"\x00\x01" * 16000)

    def synthesize(self, text, **kwargs):
        return self.provider(text, **kwargs)

    def synthesize_to_file(self, text, output_path, **kwargs):
        raise NotImplementedError


def test_disk_cache_survives_new_instances_with_result_metadata(tmp_path, monkeypatch):
    clock = Mock(return_value=1000.0)
    monkeypatch.setattr("chatterpal.core.tts.base.time.time", clock)
    config = {"cache_dir": tmp_path / "cache", "cache_ttl": 10}
    first = OfflineTTS(config)
    fresh = first.synthesize_with_error_handling("Hello @ world", voice="test")

    clock.return_value = 1009.0
    restored = OfflineTTS(config)
    cached = restored.synthesize_with_error_handling("Hello @ world", voice="test")

    assert fresh.cached is False
    assert cached.cached is True
    assert cached.audio_data == fresh.audio_data
    assert cached.text == fresh.text == "Hello world"
    assert cached.metadata == fresh.metadata == {
        "attempt": 1,
        "original_text": "Hello @ world",
        "text_length": 11,
    }
    assert (cached.duration, cached.format, cached.sample_rate) == (
        fresh.duration,
        fresh.format,
        fresh.sample_rate,
    )
    assert cached.synthesis_time == 0.0
    assert restored.get_cache_stats()["oldest_entry"] == 1000.0
    first.provider.assert_called_once_with("Hello world", voice="test")
    restored.provider.assert_not_called()

    # A disk hit must not extend the entry's original lifetime.
    clock.return_value = 1011.0
    expired = OfflineTTS(config)
    refreshed = expired.synthesize_with_error_handling("Hello @ world", voice="test")
    assert refreshed.cached is False
    assert refreshed.audio_data == fresh.audio_data
    expired.provider.assert_called_once_with("Hello world", voice="test")


def test_autoplay_plays_fresh_memory_and_disk_results(tmp_path, monkeypatch):
    config = {"cache_dir": tmp_path / "cache", "auto_play": True}
    first = OfflineTTS(config)
    playback = Mock(return_value=True)
    monkeypatch.setattr(first, "play_audio", playback)

    fresh = first.synthesize_with_error_handling("Play this")
    memory = first.synthesize_with_error_handling("Play this")
    restored = OfflineTTS(config)
    monkeypatch.setattr(restored, "play_audio", playback)
    disk = restored.synthesize_with_error_handling("Play this")

    assert [fresh.cached, memory.cached, disk.cached] == [False, True, True]
    assert playback.call_count == 3
    for call in playback.call_args_list:
        assert call.args == (fresh.audio_data,)
    first.provider.assert_called_once_with("Play this")
    restored.provider.assert_not_called()


@pytest.mark.parametrize("cache_path", ["fresh", "memory", "disk"])
@pytest.mark.parametrize("failure", ["false", "exception"])
def test_playback_failure_keeps_successful_audio_and_cache(
    tmp_path, monkeypatch, cache_path, failure
):
    config = {"cache_dir": tmp_path / "cache"}
    tts = OfflineTTS(config)
    if cache_path != "fresh":
        tts.synthesize_with_error_handling("Keep this")
    if cache_path == "disk":
        tts = OfflineTTS(config)
    tts.auto_play = True
    playback = Mock(return_value=False)
    if failure == "exception":
        playback.side_effect = OSError("No audio device")
    monkeypatch.setattr(tts, "play_audio", playback)

    result = tts.synthesize_with_error_handling("Keep this")

    assert result.audio_data == b"\x00\x01" * 16000
    assert result.cached is (cache_path != "fresh")
    assert tts.get_playback_state() is PlaybackState.ERROR
    playback.assert_called_once_with(result.audio_data)
    assert tts.provider.call_count == (0 if cache_path == "disk" else 1)

    tts.auto_play = False
    memory = tts.synthesize_with_error_handling("Keep this")
    restored = OfflineTTS(config)
    disk = restored.synthesize_with_error_handling("Keep this")
    assert memory.cached is disk.cached is True
    assert memory.audio_data == disk.audio_data == result.audio_data
    assert memory.metadata == disk.metadata == result.metadata
    restored.provider.assert_not_called()
    assert tts.provider.call_count == (0 if cache_path == "disk" else 1)


@pytest.mark.parametrize(
    "corruption", ["truncated", "legacy_pickle", "shape", "audio", "timestamp"]
)
def test_corrupt_disk_cache_is_a_miss_and_is_replaced(tmp_path, corruption):
    config = {"cache_dir": tmp_path / "cache"}
    first = OfflineTTS(config)
    original = first.synthesize_with_error_handling("Recover this")
    cache_file, = (tmp_path / "cache").glob("*.cache")
    if corruption == "truncated":
        cache_file.write_bytes(b'{"version":')
    elif corruption == "legacy_pickle":
        cache_file.write_bytes(b"\x80\x04N.")
    elif corruption == "shape":
        cache_file.write_text("[]", encoding="utf-8")
    else:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        payload["audio_data" if corruption == "audio" else "created_at"] = (
            "bm90IHRoZSBvcmlnaW5hbCBhdWRpbw==" if corruption == "audio" else "invalid"
        )
        cache_file.write_text(json.dumps(payload), encoding="utf-8")
    restored = OfflineTTS(config)

    recovered = restored.synthesize_with_error_handling("Recover this")
    memory = restored.synthesize_with_error_handling("Recover this")
    next_instance = OfflineTTS(config)
    disk = next_instance.synthesize_with_error_handling("Recover this")

    assert recovered.cached is False
    assert memory.cached is disk.cached is True
    assert recovered.audio_data == original.audio_data
    assert memory.audio_data == disk.audio_data == recovered.audio_data
    restored.provider.assert_called_once_with("Recover this")
    next_instance.provider.assert_not_called()


@pytest.mark.parametrize("limit", [1, 2])
def test_evicted_entries_do_not_reappear_from_disk(tmp_path, limit):
    config = {"cache_dir": tmp_path / "cache", "cache_size_limit": limit}
    tts = OfflineTTS(config)
    for text in ("First", "Second", "Third"):
        tts.synthesize_with_error_handling(text)

    assert tts.get_cache_stats()["entries"] <= limit
    assert len(list((tmp_path / "cache").glob("*.cache"))) <= limit
    restored = OfflineTTS(config)
    last = restored.synthesize_with_error_handling("Third")
    evicted = restored.synthesize_with_error_handling("First")
    assert last.cached is True
    assert evicted.cached is False
    restored.provider.assert_called_once_with("First")


def test_clear_cache_removes_memory_and_persistent_results(tmp_path):
    config = {"cache_dir": tmp_path / "cache"}
    first = OfflineTTS(config)
    first.synthesize_with_error_handling("Forget this")
    unrelated = tmp_path / "cache" / "keep.txt"
    unrelated.write_text("unrelated", encoding="utf-8")

    # Clear must also remove entries that this instance has not loaded yet.
    restored = OfflineTTS(config)
    restored.clear_cache()
    assert restored.get_cache_stats()["entries"] == 0
    assert not list((tmp_path / "cache").glob("*.cache"))
    assert unrelated.read_text(encoding="utf-8") == "unrelated"
    result = restored.synthesize_with_error_handling("Forget this")
    assert result.cached is False
    restored.provider.assert_called_once_with("Forget this")


def test_unavailable_disk_cache_keeps_memory_cache(tmp_path):
    cache_dir = tmp_path / "not-a-directory"
    cache_dir.write_text("occupied", encoding="utf-8")
    tts = OfflineTTS({"cache_dir": cache_dir})

    fresh = tts.synthesize_with_error_handling("Still works")
    cached = tts.synthesize_with_error_handling("Still works")

    assert fresh.cached is False
    assert cached.cached is True
    assert cached.audio_data == fresh.audio_data
    tts.provider.assert_called_once_with("Still works")
    assert cache_dir.read_text(encoding="utf-8") == "occupied"


@pytest.mark.parametrize("config", [{"enable_cache": False}, {"cache_size_limit": 0}])
def test_disabled_cache_does_not_persist_results(tmp_path, config):
    cache_dir = tmp_path / "cache"
    tts = OfflineTTS({**config, "cache_dir": cache_dir})
    first = tts.synthesize_with_error_handling("Uncached")
    second = tts.synthesize_with_error_handling("Uncached")
    assert first.cached is second.cached is False
    assert tts.provider.call_count == 2
    assert tts.get_cache_stats()["entries"] == 0
    assert not list(cache_dir.glob("*.cache"))


def test_failed_atomic_write_keeps_complete_record_and_successful_audio(
    tmp_path, monkeypatch
):
    config = {"cache_dir": tmp_path / "cache"}
    first = OfflineTTS(config)
    original = first.synthesize_with_error_handling("Already cached")
    cache_file, = (tmp_path / "cache").glob("*.cache")
    original_record = cache_file.read_bytes()
    assert json.loads(original_record)["version"] == 1
    replace = Mock(side_effect=OSError("Disk unavailable"))
    monkeypatch.setattr("chatterpal.core.tts.base.os.replace", replace)
    restored = OfflineTTS(config)

    cached = restored.synthesize_with_error_handling("Already cached")
    fresh = restored.synthesize_with_error_handling("New audio")
    memory = restored.synthesize_with_error_handling("New audio")

    assert cached.cached is memory.cached is True
    assert fresh.cached is False
    assert cached.audio_data == original.audio_data
    assert fresh.audio_data == memory.audio_data
    restored.provider.assert_called_once_with("New audio")
    assert cache_file.read_bytes() == original_record
    assert not list((tmp_path / "cache").glob("*.tmp"))
    assert replace.call_count == 3


def test_disk_eviction_applies_when_each_instance_starts_with_empty_memory(tmp_path):
    config = {"cache_dir": tmp_path / "cache", "cache_size_limit": 2}
    for text in ("First", "Second", "Third"):
        OfflineTTS(config).synthesize_with_error_handling(text)

    restored = OfflineTTS(config)
    assert restored.synthesize_with_error_handling("Third").cached is True
    assert restored.synthesize_with_error_handling("Second").cached is True
    assert restored.synthesize_with_error_handling("First").cached is False
    restored.provider.assert_called_once_with("First")
    assert len(list((tmp_path / "cache").glob("*.cache"))) == 2


@pytest.mark.parametrize("failure", ["serialization", "write"])
def test_cache_write_failure_does_not_repeat_successful_synthesis(
    tmp_path, monkeypatch, failure
):
    config = {"cache_dir": tmp_path / "cache", "auto_play": True}
    tts = OfflineTTS(config)
    playback = Mock(return_value=True)
    monkeypatch.setattr(tts, "play_audio", playback)
    boundary = (
        "chatterpal.core.tts.base.json.dumps"
        if failure == "serialization"
        else "chatterpal.core.tts.base.tempfile.NamedTemporaryFile"
    )
    monkeypatch.setattr(boundary, Mock(side_effect=OSError("Cache unavailable")))

    fresh = tts.synthesize_with_error_handling("Keep the audio")
    memory = tts.synthesize_with_error_handling("Keep the audio")

    assert fresh.cached is False
    assert memory.cached is True
    assert fresh.audio_data == memory.audio_data == b"\x00\x01" * 16000
    tts.provider.assert_called_once_with("Keep the audio")
    assert playback.call_count == 2
    assert not list((tmp_path / "cache").glob("*.tmp"))


def test_expired_memory_entry_cannot_be_restored_from_old_disk_record(
    tmp_path, monkeypatch
):
    clock = Mock(return_value=1000.0)
    monkeypatch.setattr("chatterpal.core.tts.base.time.time", clock)
    config = {"cache_dir": tmp_path / "cache", "cache_ttl": 10}
    first = OfflineTTS(config)
    first.synthesize_with_error_handling("Expire this")
    clock.return_value = 1011.0
    refreshed = first.synthesize_with_error_handling("Expire this")
    assert refreshed.cached is False
    assert first.provider.call_count == 2

    restored = OfflineTTS(config)
    cached = restored.synthesize_with_error_handling("Expire this")
    assert cached.cached is True
    assert restored.get_cache_stats()["oldest_entry"] == 1011.0
    restored.provider.assert_not_called()


_legacy_pickle_executions = []


def _record_legacy_pickle_execution():
    _legacy_pickle_executions.append(True)


def test_legacy_pickle_is_never_executed(tmp_path):
    config = {"cache_dir": tmp_path / "cache"}
    first = OfflineTTS(config)
    first.synthesize_with_error_handling("Untrusted cache")
    cache_file, = (tmp_path / "cache").glob("*.cache")

    class LegacyRecord:
        def __reduce__(self):
            return _record_legacy_pickle_execution, ()

    _legacy_pickle_executions.clear()
    cache_file.write_bytes(pickle.dumps(LegacyRecord()))
    restored = OfflineTTS(config)
    result = restored.synthesize_with_error_handling("Untrusted cache")

    assert result.cached is False
    restored.provider.assert_called_once_with("Untrusted cache")
    assert _legacy_pickle_executions == []
