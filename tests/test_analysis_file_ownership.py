"""Analyzer calls retain caller files and release only their own audio files."""

import io
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from chatterpal.core.assessment import phoneme, prosody
from chatterpal.core.assessment.base import AssessmentError


@pytest.fixture(
    params=[phoneme.PhonemeAnalyzer, prosody.ProsodyAnalyzer],
    ids=["phoneme", "prosody"],
)
def analyzer(request, tmp_path):
    instance = request.param()
    instance.temp_dir = str(tmp_path)
    return instance


@pytest.fixture
def audio():
    sample_rate = 16000
    samples = (0.2 * np.sin(2 * np.pi * 220 * np.arange(1600) / sample_rate)).astype(
        np.float32
    )
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes((samples * 32767).astype(np.int16).tobytes())
    return output.getvalue(), (sample_rate, samples)


@pytest.fixture
def acoustics(monkeypatch):
    boundary = SimpleNamespace(paths=[], failure=None, on_open=None)

    def read_audio(path):
        path = Path(path)
        with wave.open(str(path), "rb") as wav:
            assert wav.getnchannels() == 1
            assert wav.getsampwidth() == 2
            assert wav.getframerate() == 16000
            assert wav.getnframes() == 1600
            assert len(wav.readframes(1600)) == 3200
        boundary.paths.append(path)
        if boundary.on_open is not None:
            boundary.on_open(path)
        if boundary.failure is not None:
            raise boundary.failure("acoustic boundary failed")

    def sound(path):
        read_audio(path)
        return SimpleNamespace(
            get_total_duration=lambda: 0.1,
            sampling_frequency=16000,
            to_pitch=lambda **kwargs: SimpleNamespace(
                selected_array={"frequency": np.array([100.0, 0.0, 150.0, 200.0])}
            ),
            to_formant_burg=lambda **kwargs: SimpleNamespace(
                get_number_of_frames=lambda: 2,
                get_time_from_frame_number=lambda frame: frame * 0.01,
                get_value_at_time=lambda number, time: {1: 500.0, 2: 1500.0, 3: 2500.0}[
                    number
                ],
            ),
            to_intensity=lambda **kwargs: SimpleNamespace(
                values=np.array([[60.0, 65.0]])
            ),
            to_spectrum=lambda: SimpleNamespace(
                xs=lambda: np.array([100.0, 200.0, 300.0]),
                values=np.array([[1.0, 2.0, 1.0]]),
            ),
        )

    def syllables(name, directory):
        read_audio(Path(directory) / f"{name}.wav")
        return 4

    monkeypatch.setattr(phoneme, "parselmouth", SimpleNamespace(Sound=sound))
    monkeypatch.setattr(
        prosody,
        "mysp",
        SimpleNamespace(
            myspsyl=syllables,
            mysppaus=lambda name, directory: 1,
            myspsr=lambda name, directory: 120.0,
            myspatc=lambda name, directory: 150.0,
        ),
    )
    return boundary


def assess_with_boundary(analyzer, audio_data, acoustics):
    if acoustics.failure is RuntimeError and isinstance(
        analyzer, phoneme.PhonemeAnalyzer
    ):
        with pytest.raises(AssessmentError, match="acoustic boundary failed"):
            analyzer.assess(audio_data)
    else:
        result = analyzer.assess(audio_data)
        assert result["analysis_success"] is True
        if isinstance(analyzer, prosody.ProsodyAnalyzer):
            assert result["prosody_features"].syllable_count == (
                None if acoustics.failure else 4
            )
    assert acoustics.paths


@pytest.mark.parametrize("analysis_error", [False, True], ids=["success", "error"])
def test_assess_preserves_caller_path_in_temp_directory(
    analyzer, audio, acoustics, analysis_error
):
    wav_bytes, _ = audio
    caller_path = Path(analyzer.temp_dir) / "caller.wav"
    caller_path.write_bytes(wav_bytes)
    if analysis_error:
        acoustics.failure = RuntimeError

    assess_with_boundary(analyzer, str(caller_path), acoustics)

    assert caller_path.is_file()
    assert caller_path.read_bytes() == wav_bytes
    assert list(Path(analyzer.temp_dir).iterdir()) == [caller_path]


@pytest.mark.parametrize("input_kind", ["bytes", "tuple"])
@pytest.mark.parametrize("analysis_error", [False, True], ids=["success", "error"])
def test_assess_cleans_owned_audio_files(
    analyzer, audio, acoustics, input_kind, analysis_error
):
    wav_bytes, gradio_audio = audio
    audio_data = wav_bytes if input_kind == "bytes" else gradio_audio
    caller_path = Path(analyzer.temp_dir) / "caller.wav"
    caller_path.write_bytes(wav_bytes)
    if analysis_error:
        acoustics.failure = RuntimeError

    assess_with_boundary(analyzer, audio_data, acoustics)

    assert len(acoustics.paths) == 1
    assert acoustics.paths[0] != caller_path
    assert not acoustics.paths[0].exists()
    assert caller_path.read_bytes() == wav_bytes
    assert list(Path(analyzer.temp_dir).iterdir()) == [caller_path]


@pytest.mark.parametrize("seam", ["assess", "prepare"])
def test_preparation_failure_cleans_partial_audio_file(analyzer, audio, seam):
    wav_bytes, (_, samples) = audio
    caller_path = Path(analyzer.temp_dir) / "caller.wav"
    caller_path.write_bytes(wav_bytes)
    # Both WAV writers reject this rate after opening their output file.
    invalid_audio = (0, samples)

    expected_error = (
        AssessmentError
        if seam == "assess" or isinstance(analyzer, phoneme.PhonemeAnalyzer)
        else wave.Error
    )
    with pytest.raises(expected_error):
        if seam == "assess":
            analyzer.assess(invalid_audio)
        else:
            analyzer._prepare_audio_file(invalid_audio)

    assert caller_path.read_bytes() == wav_bytes
    assert list(Path(analyzer.temp_dir).iterdir()) == [caller_path]


@pytest.mark.parametrize("first_input", ["bytes", "tuple"])
@pytest.mark.parametrize("first_error", [False, True], ids=["success", "error"])
def test_overlapping_calls_own_distinct_files(
    analyzer, audio, acoustics, first_input, first_error
):
    wav_bytes, gradio_audio = audio
    inputs = [wav_bytes, gradio_audio]
    if first_input == "tuple":
        inputs.reverse()
    first_entered, second_entered = threading.Event(), threading.Event()
    release_first, release_second = threading.Event(), threading.Event()
    timed_out = threading.Event()
    opened = []
    lock = threading.Lock()

    def hold_acoustic_call(path):
        with lock:
            index = len(opened)
            opened.append(path)
        entered, release = (
            (first_entered, release_first)
            if index == 0
            else (second_entered, release_second)
        )
        entered.set()
        if not release.wait(5):
            timed_out.set()
            raise RuntimeError("overlapping acoustic call timed out")
        if index == 0 and first_error:
            raise RuntimeError("first acoustic call failed")

    def assess(audio_data):
        try:
            return analyzer.assess(audio_data)
        except AssessmentError as error:
            return error

    acoustics.on_open = hold_acoustic_call
    random_state = np.random.get_state()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            try:
                first = executor.submit(assess, inputs[0])
                assert first_entered.wait(5)
                # Replaying a random draw must never reuse an active file.
                np.random.set_state(random_state)
                second = executor.submit(assess, inputs[1])
                assert second_entered.wait(5)
                paths = list(opened)
                release_first.set()
                first_result = first.result(timeout=5)
                second_file_survived = paths[1].is_file()
                release_second.set()
                second_result = second.result(timeout=5)
            finally:
                release_first.set()
                release_second.set()
    finally:
        np.random.set_state(random_state)

    assert not timed_out.is_set()
    assert len(paths) == 2
    assert paths[0] != paths[1]
    assert second_file_survived
    assert second_result["analysis_success"] is True
    if first_error and isinstance(analyzer, phoneme.PhonemeAnalyzer):
        assert isinstance(first_result, AssessmentError)
    else:
        assert first_result["analysis_success"] is True
    assert list(Path(analyzer.temp_dir).iterdir()) == []


@pytest.mark.parametrize("input_kind", ["path", "bytes", "tuple"])
def test_interrupted_analysis_releases_only_owned_files(
    analyzer, audio, acoustics, input_kind
):
    class AcousticInterrupted(BaseException):
        pass

    wav_bytes, gradio_audio = audio
    caller_path = Path(analyzer.temp_dir) / "caller.wav"
    caller_path.write_bytes(wav_bytes)
    audio_data = {"path": str(caller_path), "bytes": wav_bytes, "tuple": gradio_audio}[
        input_kind
    ]
    acoustics.failure = AcousticInterrupted

    with pytest.raises(AcousticInterrupted, match="acoustic boundary failed"):
        analyzer.assess(audio_data)

    assert acoustics.paths
    assert caller_path.read_bytes() == wav_bytes
    assert list(Path(analyzer.temp_dir).iterdir()) == [caller_path]


@pytest.mark.parametrize("input_kind", ["path", "bytes", "tuple"])
def test_missing_acoustic_dependency_releases_only_owned_files(
    analyzer, audio, monkeypatch, input_kind
):
    wav_bytes, gradio_audio = audio
    caller_path = Path(analyzer.temp_dir) / "caller.wav"
    caller_path.write_bytes(wav_bytes)
    audio_data = {"path": str(caller_path), "bytes": wav_bytes, "tuple": gradio_audio}[
        input_kind
    ]
    monkeypatch.setattr(phoneme, "parselmouth", None)
    monkeypatch.setattr(prosody, "mysp", None)

    result = analyzer.assess(audio_data)

    assert result["analysis_success"] is isinstance(analyzer, prosody.ProsodyAnalyzer)
    assert caller_path.read_bytes() == wav_bytes
    assert list(Path(analyzer.temp_dir).iterdir()) == [caller_path]


@pytest.mark.parametrize("input_kind", ["bytes", "tuple"])
def test_owned_file_cleanup_does_not_depend_on_current_temp_directory(
    analyzer, audio, acoustics, input_kind, tmp_path_factory
):
    original_directory = Path(analyzer.temp_dir)
    next_directory = tmp_path_factory.mktemp("next_analysis")
    wav_bytes, gradio_audio = audio
    audio_data = wav_bytes if input_kind == "bytes" else gradio_audio

    def change_temp_directory(path):
        analyzer.temp_dir = str(next_directory)

    acoustics.on_open = change_temp_directory

    assess_with_boundary(analyzer, audio_data, acoustics)

    assert list(original_directory.iterdir()) == []
    assert list(next_directory.iterdir()) == []
