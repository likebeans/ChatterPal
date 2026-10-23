"""Whisper runtime regressions through real ASR entry points and WAV decoding."""

import sys
import tempfile
import threading
import wave
from pathlib import Path
import weakref
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from types import SimpleNamespace

import numpy as np
import pytest
import speech_recognition as sr
import torch
import whisper

from chatterpal.core.asr.base import ASRError
from chatterpal.core.asr.whisper import (
    WhisperASR,
    create_whisper_asr,
    recognize_audio_file,
    recognize_microphone,
)


@pytest.fixture
def pcm_wav(tmp_path):
    """A full second whose opening samples identify truncation and PCM scaling."""
    samples = np.zeros(16000, dtype="<i2")
    samples[:8] = [-32768, -16384, -1, 0, 1, 8192, 16384, 32767]
    samples[-2:] = [4096, -4096]
    path = tmp_path / "opening-signal.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(samples.tobytes())
    return path


@pytest.fixture
def microphone_boundary(monkeypatch):
    """Fake PyAudio's device stream while retaining the real recorder/listener."""
    # Calibration consumes 15 chunks; a full second of speech follows it.
    signal = np.tile(np.array([8192, -8192], dtype="<i2"), 8000)
    frames = b"\0" * (15 * 1024 * 2) + signal.tobytes()
    captured = SimpleNamespace(opens=0, closes=0)

    class Stream:
        def __init__(self):
            self.offset = 0

        def read(self, size, exception_on_overflow=False):
            end = self.offset + size * 2
            chunk = frames[self.offset : end]
            self.offset = end
            return chunk.ljust(size * 2, b"\0")

        def is_stopped(self):
            return False

        def stop_stream(self):
            pass

        def close(self):
            captured.closes += 1

    class PyAudio:
        def get_device_count(self):
            return 1

        def get_default_input_device_info(self):
            return {"defaultSampleRate": 16000.0}

        def open(self, **options):
            captured.opens += 1
            return Stream()

        def terminate(self):
            pass

    fake_pyaudio = SimpleNamespace(
        __version__="0.2.14", paInt16=8, PyAudio=PyAudio, get_sample_size=lambda _: 2
    )
    monkeypatch.setitem(sys.modules, "pyaudio", fake_pyaudio)
    return captured


@pytest.fixture
def temporary_wavs(monkeypatch, tmp_path):
    """Observe actual temporary WAV ownership and clean up even a red test."""
    directory = tmp_path / "owned-wavs"
    directory.mkdir()
    captured = SimpleNamespace(paths=[], handles=[])
    create = tempfile.NamedTemporaryFile

    def create_tracked(*args, **options):
        options["dir"] = directory
        handle = create(*args, **options)
        captured.paths.append(Path(handle.name))
        captured.handles.append(handle)
        return handle

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", create_tracked)
    try:
        yield captured
    finally:
        for handle in captured.handles:
            handle.close()
        for path in captured.paths:
            path.unlink(missing_ok=True)


@pytest.fixture
def model_boundary(monkeypatch):
    """Replace only the external Whisper loader/model, never the ASR adapter."""
    boundary = SimpleNamespace(
        loads=[],
        transcriptions=[],
        living_models=weakref.WeakSet(),
        on_load=None,
        on_loaded=None,
        on_transcribe=None,
        active=0,
        peak_active=0,
        peak_living=0,
    )
    guard = threading.Lock()

    def enter():
        with guard:
            boundary.active += 1
            boundary.peak_active = max(boundary.peak_active, boundary.active)

    def leave():
        with guard:
            boundary.active -= 1

    class Model:
        def __init__(self, name, device):
            self.name = name
            self.device = torch.device(device or "cpu")

        def transcribe(self, audio, **options):
            enter()
            try:
                call = SimpleNamespace(
                    name=self.name,
                    device=str(self.device),
                    waveform=audio.copy(),
                    options=options,
                )
                with guard:
                    boundary.transcriptions.append(call)
                if boundary.on_transcribe is not None:
                    return boundary.on_transcribe(call)
                return {"text": "Hello from Whisper"}
            finally:
                leave()

    def load_model(name, device=None, **options):
        enter()
        try:
            with guard:
                boundary.loads.append((name, device))
            if boundary.on_load is not None:
                boundary.on_load(name, device)
            model = Model(name, device)
            with guard:
                boundary.living_models.add(model)
                boundary.peak_living = max(
                    boundary.peak_living, len(boundary.living_models)
                )
            if boundary.on_loaded is not None:
                boundary.on_loaded(model)
            return model
        finally:
            leave()

    monkeypatch.setattr(whisper, "load_model", load_model)
    return boundary


def test_repeated_recognition_lazily_reuses_one_model(pcm_wav, model_boundary):
    asr = WhisperASR()
    assert isinstance(asr.recognizer, sr.Recognizer)
    assert asr.max_cached_models == 1
    assert model_boundary.loads == []

    assert asr.recognize_file(str(pcm_wav)) == "Hello from Whisper"
    assert asr.recognize_file(str(pcm_wav)) == "Hello from Whisper"

    assert len(model_boundary.loads) == 1
    assert len(model_boundary.transcriptions) == 2


def test_decoded_pcm16_keeps_opening_signal_and_all_samples(pcm_wav, model_boundary):
    assert WhisperASR().recognize_file(str(pcm_wav)) == "Hello from Whisper"

    waveform = model_boundary.transcriptions[0].waveform
    assert waveform.dtype == np.float32
    assert waveform.shape == (16000,)
    np.testing.assert_array_equal(
        waveform[:8],
        np.array(
            [-1.0, -0.5, -1 / 32768, 0.0, 1 / 32768, 0.25, 0.5, 32767 / 32768],
            dtype=np.float32,
        ),
    )
    np.testing.assert_array_equal(waveform[-2:], [0.125, -0.125])
    assert np.count_nonzero(waveform[8:-2]) == 0


@pytest.mark.parametrize(
    ("configured", "cuda_available", "resolved"),
    [
        ("auto", False, "cpu"),
        ("auto", True, "cuda:1"),
        ("cpu", True, "cpu"),
        ("cuda", True, "cuda:1"),
        ("cuda:2", False, "cuda:2"),
    ],
)
def test_configured_device_reaches_loader_and_controls_precision(
    pcm_wav, model_boundary, monkeypatch, configured, cuda_available, resolved
):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: cuda_available)
    monkeypatch.setattr(torch.cuda, "current_device", lambda: 1)
    asr = WhisperASR({"device": configured})

    assert asr.recognize_file(str(pcm_wav)) == "Hello from Whisper"

    assert model_boundary.loads == [("base", resolved)]
    assert model_boundary.transcriptions[0].options["fp16"] is resolved.startswith(
        "cuda"
    )


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("en", "english"),
        ("zh", "chinese"),
        ("auto", None),
        ("english", "english"),
        ("chinese", "chinese"),
        ("AUTO", None),
    ],
)
def test_language_override_reaches_transcribe_without_changing_config(
    pcm_wav, model_boundary, language, expected
):
    config = {"language": "en", "device": "cpu", "model_size": "base"}
    asr = WhisperASR(config)

    assert asr.recognize_file(str(pcm_wav), language=language) == "Hello from Whisper"
    assert asr.recognize_file(str(pcm_wav)) == "Hello from Whisper"

    assert model_boundary.transcriptions[0].options["language"] == expected
    assert model_boundary.transcriptions[1].options["language"] == "english"
    assert config == {"language": "en", "device": "cpu", "model_size": "base"}
    assert asr.language == "en"
    assert len(model_boundary.loads) == 1


@pytest.mark.parametrize(
    "entrypoint", [WhisperASR, create_whisper_asr, recognize_audio_file]
)
def test_default_entrypoints_transcribe_in_english(pcm_wav, model_boundary, entrypoint):
    if entrypoint is recognize_audio_file:
        assert entrypoint(str(pcm_wav)) == "Hello from Whisper"
    else:
        asr = entrypoint()
        assert asr.language == "en"
        assert asr.recognize_file(str(pcm_wav)) == "Hello from Whisper"

    assert model_boundary.transcriptions[0].options["language"] == "english"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("max_cached_models", 0),
        ("max_cached_models", 5),
        ("max_cached_models", -1),
        ("max_cached_models", True),
        ("max_cached_models", False),
        ("max_cached_models", 1.0),
        ("max_cached_models", 1.5),
        ("max_cached_models", "2"),
        ("max_cached_models", None),
        ("language", "fr"),
        ("language", ""),
        ("language", None),
        ("language", 1),
        ("device", "gpu"),
        ("device", "mps"),
        ("device", "cuda:-1"),
        ("device", "cuda:01"),
        ("device", "cuda:1x"),
        ("device", "cuda:1\n"),
        ("device", "cpu:0"),
        ("device", None),
        ("device", 1),
    ],
)
def test_invalid_direct_configuration_is_rejected_without_loading(
    model_boundary, key, value
):
    with pytest.raises(ValueError, match=key):
        WhisperASR({key: value})

    assert model_boundary.loads == []


@pytest.mark.parametrize("capacity", [1, 2, 3, 4])
def test_cache_reuses_recent_models_and_evicts_before_loading(
    pcm_wav, model_boundary, capacity
):
    asr = WhisperASR({"device": "cpu", "max_cached_models": capacity})
    sizes = ["tiny", "base", "small", "medium"][:capacity]

    for size in sizes:
        assert asr.recognize_file(str(pcm_wav), model_size=size) == "Hello from Whisper"
    assert model_boundary.loads == [(size, "cpu") for size in sizes]
    assert len(model_boundary.living_models) == capacity

    # Touch the oldest entry so the next eviction must use recency, not insertion.
    assert asr.recognize_file(str(pcm_wav), model_size="tiny") == "Hello from Whisper"
    assert len(model_boundary.loads) == capacity
    assert asr.recognize_file(str(pcm_wav), model_size="large") == "Hello from Whisper"
    survivor = "tiny" if capacity > 1 else "large"
    assert asr.recognize_file(str(pcm_wav), model_size=survivor) == "Hello from Whisper"
    assert len(model_boundary.loads) == capacity + 1

    evicted = sizes[1] if capacity > 1 else "tiny"
    assert asr.recognize_file(str(pcm_wav), model_size=evicted) == "Hello from Whisper"
    assert model_boundary.loads[-2:] == [("large", "cpu"), (evicted, "cpu")]
    assert len(model_boundary.living_models) == capacity
    assert model_boundary.peak_living == capacity


@pytest.mark.parametrize(
    ("requested", "available", "resolved"),
    [
        ("auto", False, "cpu"),
        ("auto", True, "cuda:1"),
        ("cpu", True, "cpu"),
        ("cuda", True, "cuda:1"),
        ("cuda:2", False, "cuda:2"),
    ],
)
def test_request_device_and_model_overrides_leave_config_unchanged(
    pcm_wav, model_boundary, monkeypatch, requested, available, resolved
):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: available)
    monkeypatch.setattr(torch.cuda, "current_device", lambda: 1)
    config = {
        "device": "cpu",
        "model_size": "base",
        "language": "zh",
        "max_cached_models": 3,
    }
    original = config.copy()
    asr = WhisperASR(config)

    assert asr.recognize_file(str(pcm_wav)) == "Hello from Whisper"
    for _ in range(2):
        assert (
            asr.recognize_file(
                str(pcm_wav), model_size="tiny", device=requested, language="auto"
            )
            == "Hello from Whisper"
        )
    assert asr.recognize_file(str(pcm_wav)) == "Hello from Whisper"

    assert model_boundary.loads == [("base", "cpu"), ("tiny", resolved)]
    assert [call.device for call in model_boundary.transcriptions] == [
        "cpu",
        resolved,
        resolved,
        "cpu",
    ]
    assert model_boundary.transcriptions[1].options == {
        "language": None,
        "fp16": resolved.startswith("cuda"),
    }
    assert model_boundary.transcriptions[-1].options == {
        "language": "chinese",
        "fp16": False,
    }
    assert config == original
    assert asr.device == "cpu"
    assert asr.model_size == "base"
    assert asr.language == "zh"


@pytest.mark.parametrize("device", ["auto", "cuda"])
@pytest.mark.parametrize("override", [False, True])
@pytest.mark.parametrize("capacity", [1, 2])
def test_unindexed_cuda_tracks_current_device_before_cache_lookup(
    pcm_wav, model_boundary, monkeypatch, device, override, capacity
):
    current = 0
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "current_device", lambda: current)
    asr = WhisperASR(
        {"device": "cpu" if override else device, "max_cached_models": capacity}
    )
    options = {"device": device} if override else {}

    for current in [0, 1, 0, 1]:
        assert asr.recognize_file(str(pcm_wav), **options) == "Hello from Whisper"

    expected_devices = ["cuda:0", "cuda:1", "cuda:0", "cuda:1"]
    assert [call.device for call in model_boundary.transcriptions] == expected_devices
    assert model_boundary.loads == [
        ("base", resolved)
        for resolved in (expected_devices if capacity == 1 else expected_devices[:2])
    ]
    assert model_boundary.peak_living == capacity
    assert asr.device == ("cpu" if override else device)


@pytest.mark.parametrize("device", ["auto", "cuda"])
def test_shared_adapter_resolves_each_callers_thread_local_cuda_device(
    pcm_wav, model_boundary, monkeypatch, device
):
    caller = threading.local()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "current_device", lambda: caller.index)
    asr = WhisperASR({"device": device, "max_cached_models": 2})
    start = threading.Barrier(2)

    def transcribe(call):
        assert call.device == f"cuda:{caller.index}"
        return {"text": "Hello from Whisper"}

    def recognize(index):
        caller.index = index
        start.wait(timeout=5)
        return [asr.recognize_file(str(pcm_wav)) for _ in range(2)]

    model_boundary.on_transcribe = transcribe
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(recognize, index) for index in [0, 1]]
        assert [future.result(timeout=5) for future in futures] == [
            ["Hello from Whisper"] * 2
        ] * 2

    assert sorted(model_boundary.loads) == [("base", "cuda:0"), ("base", "cuda:1")]
    assert model_boundary.peak_active == 1
    assert model_boundary.peak_living == 2


def test_unsupported_mps_is_rejected_before_loading_or_evicting(
    pcm_wav, model_boundary
):
    with pytest.raises(ValueError, match=r"device[\s\S]*mps[\s\S]*cpu.*auto"):
        WhisperASR({"device": "mps"})
    assert model_boundary.loads == []

    asr = WhisperASR({"device": "cpu"})
    assert asr.recognize_file(str(pcm_wav)) == "Hello from Whisper"
    with pytest.raises(ASRError, match=r"mps[\s\S]*cpu.*auto"):
        asr.recognize_file(str(pcm_wav), model_size="tiny", device="mps")
    assert asr.recognize_file(str(pcm_wav)) == "Hello from Whisper"
    assert model_boundary.loads == [("base", "cpu")]


def test_same_model_on_distinct_devices_has_distinct_cache_entries(
    pcm_wav, model_boundary
):
    asr = WhisperASR({"device": "cpu", "max_cached_models": 2})
    for device in ["cpu", "cuda:1", "cpu", "cuda:1"]:
        assert asr.recognize_file(str(pcm_wav), device=device) == "Hello from Whisper"

    assert model_boundary.loads == [("base", "cpu"), ("base", "cuda:1")]
    assert model_boundary.peak_living == 2


def test_simultaneous_cold_calls_share_one_load_and_serialize_inference(
    pcm_wav, model_boundary
):
    asr = WhisperASR({"device": "cpu"})
    start = threading.Barrier(5)
    loading = threading.Event()
    release = threading.Event()

    def load(name, device):
        loading.set()
        assert release.wait(5), "test did not release the model loader"

    def recognize():
        start.wait(timeout=5)
        return asr.recognize_file(str(pcm_wav))

    model_boundary.on_load = load
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(recognize) for _ in range(4)]
        try:
            start.wait(timeout=5)
            assert loading.wait(5)
            assert model_boundary.loads == [("base", "cpu")]
            assert model_boundary.transcriptions == []
        finally:
            release.set()
        assert [future.result(timeout=5) for future in futures] == [
            "Hello from Whisper"
        ] * 4

    assert model_boundary.loads == [("base", "cpu")]
    assert len(model_boundary.transcriptions) == 4
    assert model_boundary.peak_active == 1
    assert model_boundary.peak_living == 1


@pytest.mark.parametrize("capacity", [1, 2])
def test_inference_blocks_other_keys_including_cache_hits_and_eviction(
    pcm_wav, model_boundary, capacity
):
    asr = WhisperASR({"device": "cpu", "max_cached_models": capacity})
    assert asr.recognize_file(str(pcm_wav), model_size="tiny") == "Hello from Whisper"
    assert asr.recognize_file(str(pcm_wav)) == "Hello from Whisper"
    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    third_started = threading.Event()

    def transcribe(call):
        if call.name == "base" and call.device == "cpu":
            entered.set()
            assert release.wait(5), "test did not release inference"
        return {"text": "Hello from Whisper"}

    def recognize(started, **options):
        started.set()
        return asr.recognize_file(str(pcm_wav), **options)

    model_boundary.on_transcribe = transcribe
    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(asr.recognize_file, str(pcm_wav))
        try:
            assert entered.wait(5)
            second = executor.submit(recognize, second_started, model_size="tiny")
            third = executor.submit(recognize, third_started, device="cuda:1")
            assert second_started.wait(5)
            assert third_started.wait(5)
            for future in (second, third):
                with pytest.raises(FutureTimeout):
                    future.result(timeout=0.1)
            assert model_boundary.peak_active == 1
            assert model_boundary.loads == [("tiny", "cpu"), ("base", "cpu")]
        finally:
            release.set()
        assert [future.result(timeout=5) for future in (first, second, third)] == [
            "Hello from Whisper"
        ] * 3

    assert model_boundary.peak_active == 1
    assert model_boundary.peak_living <= capacity


def test_failed_cold_load_is_retryable_by_waiting_calls(pcm_wav, model_boundary):
    asr = WhisperASR({"device": "cpu"})
    loading = threading.Event()
    release = threading.Event()
    waiters_started = [threading.Event() for _ in range(3)]

    def load(name, device):
        if len(model_boundary.loads) == 1:
            loading.set()
            assert release.wait(5), "test did not release failed initialization"
            raise RuntimeError("initialization interrupted")

    def recognize(started):
        started.set()
        return asr.recognize_file(str(pcm_wav))

    model_boundary.on_load = load
    with ThreadPoolExecutor(max_workers=4) as executor:
        first = executor.submit(asr.recognize_file, str(pcm_wav))
        try:
            assert loading.wait(5)
            waiters = [
                executor.submit(recognize, started) for started in waiters_started
            ]
            assert all(started.wait(5) for started in waiters_started)
            for future in waiters:
                with pytest.raises(FutureTimeout):
                    future.result(timeout=0.1)
        finally:
            release.set()
        with pytest.raises(ASRError, match="initialization interrupted"):
            first.result(timeout=5)
        assert [future.result(timeout=5) for future in waiters] == [
            "Hello from Whisper"
        ] * 3

    assert asr.recognize_file(str(pcm_wav)) == "Hello from Whisper"
    assert model_boundary.loads == [("base", "cpu"), ("base", "cpu")]
    assert len(model_boundary.transcriptions) == 4
    assert model_boundary.peak_active == 1
    assert model_boundary.peak_living == 1


def test_microphone_uses_the_same_cache_and_request_options_as_files(
    pcm_wav, model_boundary, microphone_boundary
):
    asr = WhisperASR({"device": "cpu", "max_cached_models": 2})
    assert asr.recognize_file(str(pcm_wav)) == "Hello from Whisper"
    assert asr.recognize_from_microphone(duration=2) == "Hello from Whisper"
    assert asr.recognize_from_microphone(duration=2) == "Hello from Whisper"
    assert model_boundary.loads == [("base", "cpu")]
    for _ in range(2):
        assert (
            asr.recognize_from_microphone(
                duration=2, model_size="tiny", device="cuda:2", language="auto"
            )
            == "Hello from Whisper"
        )
    assert asr.recognize_file(str(pcm_wav)) == "Hello from Whisper"

    assert model_boundary.loads == [("base", "cpu"), ("tiny", "cuda:2")]
    assert model_boundary.transcriptions[1].options == {
        "language": "english",
        "fp16": False,
    }
    assert model_boundary.transcriptions[3].options == {"language": None, "fp16": True}
    assert model_boundary.transcriptions[1].waveform.dtype == np.float32
    assert np.max(np.abs(model_boundary.transcriptions[1].waveform)) == 0.25
    assert microphone_boundary.opens == microphone_boundary.closes == 4


def test_microphone_convenience_function_defaults_to_english(
    model_boundary, microphone_boundary
):
    assert recognize_microphone(duration=2) == "Hello from Whisper"
    assert model_boundary.transcriptions[0].options["language"] == "english"
    assert microphone_boundary.opens == microphone_boundary.closes == 1


@pytest.mark.parametrize(
    "stage", ["success", "unknown", "load", "transcribe", "invalid", "write"]
)
def test_bytes_recognition_removes_owned_wavs_on_every_exit(
    pcm_wav, model_boundary, temporary_wavs, stage
):
    def fail(*args):
        if stage == "unknown":
            raise sr.UnknownValueError()
        raise sr.RequestError(f"{stage} failed")

    asr = WhisperASR({"device": "cpu"})
    payload = pcm_wav.read_bytes()
    if stage == "load":
        model_boundary.on_load = fail
    elif stage in {"transcribe", "unknown"}:
        model_boundary.on_transcribe = fail
    elif stage == "invalid":
        payload = b"this is not a WAV file"
    elif stage == "write":
        payload = object()

    if stage in {"load", "transcribe", "invalid", "write"}:
        with pytest.raises(ASRError):
            asr.recognize(payload)
    else:
        expected = None if stage == "unknown" else "Hello from Whisper"
        assert asr.recognize(payload) == expected

    assert len(temporary_wavs.paths) == 1
    assert all(handle.closed for handle in temporary_wavs.handles)
    assert all(not path.exists() for path in temporary_wavs.paths)
    assert pcm_wav.exists()
    if stage == "invalid":
        assert model_boundary.loads == []


@pytest.mark.parametrize("stage", ["success", "unknown", "load", "transcribe"])
def test_connection_check_closes_and_removes_its_wav_on_every_exit(
    model_boundary, temporary_wavs, stage
):
    def fail(*args):
        if stage == "unknown":
            raise sr.UnknownValueError()
        raise sr.RequestError(f"{stage} failed")

    def check_closed(*args):
        assert temporary_wavs.handles[0].closed, "connection WAV is still open"

    asr = WhisperASR({"device": "cpu"})
    if stage == "load":
        model_boundary.on_load = fail
    else:
        model_boundary.on_load = check_closed
        if stage in {"unknown", "transcribe"}:
            model_boundary.on_transcribe = fail

    assert asr.test_connection() is (stage in {"success", "unknown"})
    assert len(temporary_wavs.paths) == 1
    assert all(handle.closed for handle in temporary_wavs.handles)
    assert all(not path.exists() for path in temporary_wavs.paths)


@pytest.mark.parametrize("capacity", [1, 2, 4])
def test_instances_keep_independent_bounded_model_caches(
    pcm_wav, model_boundary, capacity
):
    first = WhisperASR({"device": "cpu", "max_cached_models": capacity})
    second = WhisperASR({"device": "cpu", "max_cached_models": capacity})
    sizes = ["tiny", "base", "small", "medium"][:capacity]
    for asr in (first, second):
        for size in sizes:
            assert (
                asr.recognize_file(str(pcm_wav), model_size=size)
                == "Hello from Whisper"
            )
    assert model_boundary.loads == [(size, "cpu") for size in sizes] * 2
    assert len(model_boundary.living_models) == 2 * capacity

    for size in ["large", "large-v2", "large-v3"]:
        assert (
            first.recognize_file(str(pcm_wav), model_size=size) == "Hello from Whisper"
        )
    loads_before_second = len(model_boundary.loads)
    for size in sizes:
        assert (
            second.recognize_file(str(pcm_wav), model_size=size) == "Hello from Whisper"
        )
    assert len(model_boundary.loads) == loads_before_second
    assert len(model_boundary.living_models) == 2 * capacity
    assert model_boundary.peak_living == 2 * capacity


def test_one_instance_does_not_block_another_instance(pcm_wav, model_boundary):
    first = WhisperASR({"device": "cpu"})
    second = WhisperASR({"device": "cpu"})
    entered = threading.Event()
    release = threading.Event()

    def transcribe(call):
        if call.name == "tiny":
            entered.set()
            assert release.wait(5), "test did not release first instance"
        return {"text": "Hello from Whisper"}

    model_boundary.on_transcribe = transcribe
    with ThreadPoolExecutor(max_workers=2) as executor:
        blocked = executor.submit(first.recognize_file, str(pcm_wav), model_size="tiny")
        try:
            assert entered.wait(5)
            independent = executor.submit(second.recognize_file, str(pcm_wav))
            assert independent.result(timeout=3) == "Hello from Whisper"
        finally:
            release.set()
        assert blocked.result(timeout=5) == "Hello from Whisper"

    assert model_boundary.loads == [("tiny", "cpu"), ("base", "cpu")]
    assert model_boundary.peak_active == 2


@pytest.mark.parametrize("capacity", [1, 2, 4])
def test_failed_replacement_can_retry_and_preserves_other_cached_models(
    pcm_wav, model_boundary, capacity
):
    asr = WhisperASR({"device": "cpu", "max_cached_models": capacity})
    sizes = ["tiny", "base", "small", "medium"][:capacity]
    for size in sizes:
        assert asr.recognize_file(str(pcm_wav), model_size=size) == "Hello from Whisper"

    def load(name, device):
        if name == "large" and model_boundary.loads.count(("large", "cpu")) == 1:
            raise RuntimeError("replacement interrupted")

    model_boundary.on_load = load
    with pytest.raises(ASRError, match="replacement interrupted"):
        asr.recognize_file(str(pcm_wav), model_size="large")
    assert len(model_boundary.living_models) == capacity - 1
    for size in sizes[1:]:
        assert asr.recognize_file(str(pcm_wav), model_size=size) == "Hello from Whisper"
    assert len(model_boundary.loads) == capacity + 1
    assert asr.recognize_file(str(pcm_wav), model_size="large") == "Hello from Whisper"
    assert asr.recognize_file(str(pcm_wav), model_size="large") == "Hello from Whisper"
    assert model_boundary.loads[-2:] == [("large", "cpu"), ("large", "cpu")]
    assert len(model_boundary.living_models) == capacity
    assert model_boundary.peak_living == capacity


@pytest.mark.parametrize("entrypoint", ["file", "bytes", "microphone"])
@pytest.mark.parametrize("error", ["unknown", "request", "runtime"])
def test_errors_release_inference_lock_and_preserve_caller_files(
    pcm_wav, model_boundary, microphone_boundary, entrypoint, error
):
    asr = WhisperASR({"device": "cpu"})
    original = pcm_wav.read_bytes()

    def recognize():
        if entrypoint == "bytes":
            return asr.recognize(original)
        if entrypoint == "microphone":
            return asr.recognize_from_microphone(duration=2)
        return asr.recognize_file(str(pcm_wav))

    def fail(call):
        if error == "unknown":
            raise sr.UnknownValueError()
        if error == "request":
            raise sr.RequestError("backend unavailable")
        raise RuntimeError("inference interrupted")

    model_boundary.on_transcribe = fail
    if error == "unknown":
        assert recognize() is None
    else:
        message = (
            "backend unavailable" if error == "request" else "inference interrupted"
        )
        with pytest.raises(ASRError, match=message):
            recognize()

    model_boundary.on_transcribe = None
    assert recognize() == "Hello from Whisper"
    assert model_boundary.loads == [("base", "cpu")]
    assert pcm_wav.read_bytes() == original
    assert model_boundary.active == 0
    assert model_boundary.peak_active == 1
    if entrypoint == "microphone":
        assert microphone_boundary.opens == microphone_boundary.closes == 2


@pytest.mark.parametrize(
    "options",
    [{"device": "gpu"}, {"device": "mps"}, {"device": None}, {"language": "fr"}],
)
def test_invalid_request_overrides_do_not_load_or_evict_models(
    pcm_wav, model_boundary, options
):
    asr = WhisperASR({"device": "cpu"})
    assert asr.recognize_file(str(pcm_wav)) == "Hello from Whisper"
    with pytest.raises(ASRError):
        asr.recognize_file(str(pcm_wav), model_size="tiny", **options)
    assert asr.recognize_file(str(pcm_wav)) == "Hello from Whisper"
    assert model_boundary.loads == [("base", "cpu")]


def test_auto_device_cache_keys_use_the_resolved_device(
    pcm_wav, model_boundary, monkeypatch
):
    asr = WhisperASR({"device": "auto", "max_cached_models": 2})
    available = False
    monkeypatch.setattr(torch.cuda, "is_available", lambda: available)
    monkeypatch.setattr(torch.cuda, "current_device", lambda: 1)
    for available in [False, True, False, True]:
        assert asr.recognize_file(str(pcm_wav)) == "Hello from Whisper"
    assert model_boundary.loads == [("base", "cpu"), ("base", "cuda:1")]
    assert [call.options["fp16"] for call in model_boundary.transcriptions] == [
        False,
        True,
        False,
        True,
    ]


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("en", "english"),
        ("zh", "chinese"),
        ("auto", None),
        ("english", "english"),
        ("chinese", "chinese"),
    ],
)
def test_configured_language_policy_reaches_inference(
    pcm_wav, model_boundary, language, expected
):
    asr = WhisperASR({"language": language, "device": "cpu"})
    assert asr.recognize_file(str(pcm_wav)) == "Hello from Whisper"
    assert model_boundary.transcriptions[0].options["language"] == expected


def test_retained_transcription_error_does_not_keep_evicted_models_alive(
    pcm_wav, model_boundary
):
    asr = WhisperASR({"device": "cpu"})

    def fail(call):
        raise sr.RequestError("transcription interrupted")

    model_boundary.on_transcribe = fail
    with pytest.raises(ASRError, match="transcription interrupted") as retained_error:
        asr.recognize_file(str(pcm_wav))
    model_boundary.on_transcribe = None
    assert asr.recognize_file(str(pcm_wav), model_size="tiny") == "Hello from Whisper"
    assert retained_error.value is not None
    assert len(model_boundary.living_models) == 1
    assert model_boundary.peak_living == 1


def test_failed_load_releases_partial_model_even_when_error_is_retained(
    pcm_wav, model_boundary
):
    asr = WhisperASR({"device": "cpu"})

    def fail_after_allocation(model):
        raise RuntimeError("device initialization interrupted")

    model_boundary.on_loaded = fail_after_allocation
    with pytest.raises(ASRError, match="device initialization interrupted") as retained:
        asr.recognize_file(str(pcm_wav))
    assert retained.value is not None
    assert len(model_boundary.living_models) == 0
    model_boundary.on_loaded = None
    assert asr.recognize_file(str(pcm_wav)) == "Hello from Whisper"
    assert model_boundary.loads == [("base", "cpu"), ("base", "cpu")]
    assert model_boundary.peak_living == 1
