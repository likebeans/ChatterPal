"""Offline regressions at the synthesis, SDK, and audio-output boundaries."""

import io
import sys
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, create_autospec

import pytest
import soundfile as sf

from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

from chatterpal.core.tts import alibaba
from chatterpal.core.tts.base import (
    AudioOutputInfo,
    TTSBase,
    TTSError,
    get_audio_output_info,
    prepare_audio_output,
)
from chatterpal.core.tts.edge import EdgeTTS
from chatterpal.core.tts.options import SynthesisOptions


def wav_bytes(sample_rate=16000, channels=1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x00\x01" * sample_rate * channels)
    return buffer.getvalue()


def test_cosyvoice_passes_request_options_using_installed_sdk_signature(monkeypatch):
    """All configured controls must reach the installed SDK unchanged."""
    sdk = create_autospec(SpeechSynthesizer)
    sdk.return_value.call.return_value = wav_bytes(24000)
    monkeypatch.setattr(alibaba, "SpeechSynthesizer", sdk)
    monkeypatch.setattr(alibaba, "dashscope", Mock())
    tts = alibaba.AlibabaTTS({"api_key": "offline-key"})

    audio = tts.synthesize(
        "Hello",
        voice="cosyvoice-v2",
        voice_name="longfei",
        format="wav",
        sample_rate=24000,
        volume=75,
        speech_rate=1.25,
        pitch_rate=0.75,
    )

    with wave.open(io.BytesIO(audio), "rb") as decoded:
        assert decoded.getframerate() == 24000
        assert decoded.getnframes() == 24000
    sdk.assert_called_once_with(
        model="cosyvoice-v2",
        voice="longfei",
        format=AudioFormat.WAV_24000HZ_MONO_16BIT,
        volume=75,
        speech_rate=1.25,
        pitch_rate=0.75,
    )
    sdk.return_value.call.assert_called_once_with("Hello")


def test_edge_resolves_immutable_options_and_reports_its_actual_defaults():
    tts = EdgeTTS({"audio_format": "wav", "sample_rate": 16000})

    options = tts.resolve_synthesis_options(voice="en-GB-SoniaNeural", rate="+10%")

    assert options.provider == "edge"
    assert options.voice == "en-GB-SoniaNeural"
    assert options.rate == "+10%"
    assert options.volume == "+0%"
    assert options.pitch == "+0Hz"
    assert options.format == "mp3"
    assert options.sample_rate == 24000
    with pytest.raises(FrozenInstanceError):
        options.voice = "zh-CN-YunxiNeural"
    assert tts.voice == "en-US-AriaNeural"
    assert tts.get_supported_formats() == ["mp3"]


def test_disk_cache_separates_effective_edge_defaults_and_reuses_explicit_defaults(
    tmp_path, monkeypatch
):
    async def save(output_path):
        Path(output_path).write_bytes(b"offline encoded audio")

    sdk = Mock(return_value=SimpleNamespace(save=save))
    monkeypatch.setattr("edge_tts.Communicate", sdk)
    cache_dir = tmp_path / "shared-cache"
    first = EdgeTTS({"cache_dir": cache_dir, "voice": "en-GB-SoniaNeural"})
    second = EdgeTTS({"cache_dir": cache_dir, "voice": "zh-CN-YunxiNeural"})

    one = first.synthesize_with_error_handling("Hello")
    two = second.synthesize_with_error_handling("Hello")
    restored = EdgeTTS({"cache_dir": cache_dir, "voice": "en-GB-SoniaNeural"})
    cached = restored.synthesize_with_error_handling(
        "Hello", voice="en-GB-SoniaNeural", rate="+0%", volume="+0%", pitch="+0Hz"
    )

    assert [one.cached, two.cached, cached.cached] == [False, False, True]
    assert sdk.call_count == 2
    assert {entry.args[1] for entry in sdk.call_args_list} == {
        "en-GB-SoniaNeural",
        "zh-CN-YunxiNeural",
    }


def test_edge_options_are_immutable_and_disk_cache_includes_resolved_defaults(
    monkeypatch,
    tmp_path,
):
    async def save(output_path):
        Path(output_path).write_bytes(b"ID3" + b"\x00" * 10)

    sdk = Mock(return_value=SimpleNamespace(save=save))
    monkeypatch.setattr("edge_tts.Communicate", sdk)
    base_config = {"cache_dir": tmp_path / "cache"}
    original = EdgeTTS(base_config)
    options = original.resolve_synthesis_options(voice="en-GB-SoniaNeural", rate="+10%")
    assert options.voice == "en-GB-SoniaNeural"
    assert options.rate == "+10%"
    assert (options.format, options.sample_rate) == ("mp3", 24000)
    with pytest.raises(FrozenInstanceError):
        options.voice = "en-US-GuyNeural"
    assert original.voice == "en-US-AriaNeural"

    fresh = original.synthesize_with_error_handling("Same text")
    assert fresh.cached is False
    # Explicit effective defaults identify the same synthesis across processes.
    equivalent = EdgeTTS(base_config).synthesize_with_error_handling(
        "Same text",
        voice="en-US-AriaNeural",
        rate="+0%",
        volume="+0%",
        pitch="+0Hz",
        format="mp3",
        sample_rate=24000,
    )
    assert equivalent.cached is True
    for override in (
        {"voice": "en-US-GuyNeural"},
        {"rate": "+10%"},
        {"volume": "-5%"},
        {"pitch": "+2Hz"},
    ):
        changed = EdgeTTS({**base_config, **override})
        assert changed.synthesize_with_error_handling("Same text").cached is False
    assert sdk.call_count == 5


def mp3_bytes(sample_rate=24000):
    buffer = io.BytesIO()
    sf.write(buffer, [0.1] * (sample_rate // 10), sample_rate, format="MP3")
    return buffer.getvalue()


@pytest.mark.parametrize("audio_format", ["wav", "mp3", "flac", "ogg"])
def test_output_info_uses_encoded_container_over_conflicting_hint(audio_format):
    if audio_format == "wav":
        payload = wav_bytes(24000, channels=2)
    elif audio_format == "mp3":
        payload = mp3_bytes()
    else:
        buffer = io.BytesIO()
        sf.write(buffer, [0.1] * 2400, 24000, format=audio_format.upper())
        payload = buffer.getvalue()

    prepared, info = prepare_audio_output(payload, format="pcm", sample_rate=16000)
    decoded, actual_rate = sf.read(io.BytesIO(prepared))

    assert prepared is payload
    assert info.format == audio_format
    assert info.extension == f".{audio_format}"
    assert (
        info.media_type
        == {
            "wav": "audio/wav",
            "mp3": "audio/mpeg",
            "flac": "audio/flac",
            "ogg": "audio/ogg",
        }[audio_format]
    )
    assert actual_rate == info.sample_rate == 24000
    assert info.channels == (2 if audio_format == "wav" else 1)
    assert info.duration == pytest.approx(len(decoded) / actual_rate)
    with pytest.raises(FrozenInstanceError):
        info.format = "pcm"


@pytest.mark.parametrize("hint", [None, "wav", "mp3"])
def test_unknown_bytes_keep_binary_mime_even_with_encoded_hint(hint):
    payload = b"unrecognized provider response"

    prepared, info = prepare_audio_output(payload, format=hint, sample_rate=24000)

    assert prepared is payload
    assert info == AudioOutputInfo()
    assert info.extension == ".bin"
    assert info.media_type == "application/octet-stream"


def test_explicit_pcm_becomes_decodable_wav_with_unchanged_samples():
    pcm = b"\x00\x01" * 2400
    raw_info = get_audio_output_info(pcm, format="pcm", sample_rate=24000)
    prepared, info = prepare_audio_output(pcm, format="pcm", sample_rate=24000)

    assert raw_info.format == "pcm"
    assert raw_info.extension == ".pcm"
    assert raw_info.media_type == "application/octet-stream"
    assert raw_info.channels == 1
    assert raw_info.duration == pytest.approx(0.1)
    with wave.open(io.BytesIO(prepared), "rb") as source:
        assert source.getframerate() == 24000
        assert source.getnchannels() == 1
        assert source.getsampwidth() == 2
        assert source.readframes(source.getnframes()) == pcm
    assert info.format == "wav"
    assert info.extension == ".wav"
    assert info.media_type == "audio/wav"
    assert info.sample_rate == 24000
    assert info.duration == pytest.approx(0.1)
    assert prepare_audio_output(prepared, format="pcm", sample_rate=8000)[0] is prepared


@pytest.mark.parametrize(
    ("payload", "sample_rate"),
    [
        (b"\x00\x00", 0),
        (b"\x00\x00", -1),
        (b"\x00\x00", True),
        (b"\x00\x00", 16000.5),
        (b"\x00", 16000),
        (b"", 16000),
    ],
)
def test_incomplete_pcm_description_does_not_invent_a_wav(payload, sample_rate):
    prepared, info = prepare_audio_output(
        payload, format="pcm", sample_rate=sample_rate
    )
    assert prepared is payload
    assert info == AudioOutputInfo()


def test_mp3_frame_sniff_does_not_require_optional_decoder(monkeypatch):
    monkeypatch.setattr(
        sf, "info", Mock(side_effect=RuntimeError("Decoder unavailable"))
    )
    payload = mp3_bytes()

    info = get_audio_output_info(payload, format="wav", sample_rate=16000)

    assert info.format == "mp3"
    assert info.extension == ".mp3"
    assert info.media_type == "audio/mpeg"
    assert info.sample_rate == 24000
    assert info.channels == 1
    assert info.duration == 0.0


@pytest.mark.parametrize("changed", ["voice", "rate", "volume", "pitch"])
def test_edge_disk_cache_isolates_each_request_control(tmp_path, monkeypatch, changed):
    payload = mp3_bytes()

    async def save(path):
        Path(path).write_bytes(payload)

    sdk = Mock(return_value=SimpleNamespace(save=save))
    monkeypatch.setattr("edge_tts.Communicate", sdk)
    config = {"cache_dir": tmp_path / "cache"}
    original = EdgeTTS(config).synthesize_with_error_handling("Same words")
    override = {
        "voice": "en-GB-SoniaNeural",
        "rate": "+20%",
        "volume": "-10%",
        "pitch": "+5Hz",
    }
    request = {changed: override[changed]}
    other = EdgeTTS(config).synthesize_with_error_handling("Same words", **request)
    restored = EdgeTTS(config).synthesize_with_error_handling("Same words", **request)

    assert [original.cached, other.cached, restored.cached] == [False, False, True]
    assert original.format == restored.format == "mp3"
    assert original.sample_rate == restored.sample_rate == 24000
    assert original.duration == restored.duration == pytest.approx(0.1)
    assert sdk.call_count == 2


def test_two_edge_voices_synthesize_concurrently_without_changing_defaults(monkeypatch):
    concurrent = threading.Barrier(2)
    calls = []

    def communicate(text, voice, **kwargs):
        calls.append((text, voice, kwargs))

        async def save(path):
            concurrent.wait(timeout=5)
            Path(path).write_bytes(voice.encode())

        return SimpleNamespace(save=save)

    monkeypatch.setattr("edge_tts.Communicate", communicate)
    tts = EdgeTTS()
    voices = ("en-GB-SoniaNeural", "zh-CN-YunxiNeural")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda voice: tts.synthesize_with_error_handling(
                    "Same text", voice=voice
                ),
                voices,
            )
        )

    assert [result.audio_data for result in results] == [
        voice.encode() for voice in voices
    ]
    assert {call[1] for call in calls} == set(voices)
    assert tts.voice == "en-US-AriaNeural"
    assert tts.get_cache_stats()["entries"] == 2
    assert all(
        tts.synthesize_with_error_handling("Same text", voice=voice).cached
        for voice in voices
    )


@pytest.mark.parametrize("audio_format", ["wav", "mp3", "pcm"])
def test_cosyvoice_result_cache_keeps_actual_encoding_and_sample_rate(
    tmp_path, monkeypatch, audio_format
):
    payload = {
        "wav": wav_bytes(24000),
        "mp3": mp3_bytes(),
        "pcm": b"\x00\x01" * 2400,
    }[audio_format]
    sdk = create_autospec(SpeechSynthesizer)
    sdk.return_value.call.return_value = payload
    monkeypatch.setattr(alibaba, "SpeechSynthesizer", sdk)
    monkeypatch.setattr(alibaba, "dashscope", Mock())
    config = {"api_key": "offline-key", "cache_dir": tmp_path / "cache"}
    request = {
        "voice": "cosyvoice-v2",
        "voice_name": "longfei",
        "format": audio_format,
        "sample_rate": 24000,
        "volume": 0,
        "speech_rate": 1.25,
        "pitch_rate": 0.75,
    }
    tts = alibaba.AlibabaTTS(config)
    fresh = tts.synthesize_with_error_handling("Hello", **request)
    memory = tts.synthesize_with_error_handling("Hello", **request)
    disk = alibaba.AlibabaTTS(config).synthesize_with_error_handling("Hello", **request)

    assert [fresh.cached, memory.cached, disk.cached] == [False, True, True]
    assert fresh.audio_data == memory.audio_data == disk.audio_data == payload
    assert fresh.format == memory.format == disk.format == audio_format
    assert fresh.sample_rate == memory.sample_rate == disk.sample_rate == 24000
    assert (
        fresh.duration
        == disk.duration
        == pytest.approx(1.0 if audio_format == "wav" else 0.1)
    )
    assert fresh.metadata == memory.metadata == disk.metadata
    assert fresh.metadata["model"] == "cosyvoice-v2"
    assert fresh.metadata["voice_name"] == "longfei"
    assert fresh.metadata["volume"] == 0
    assert tts.tts_config.voice == "cosyvoice-v1"
    assert tts.tts_config.voice_name == "longxiaochun"
    sdk.assert_called_once_with(
        model="cosyvoice-v2",
        voice="longfei",
        format=alibaba.AlibabaTTS._sdk_audio_format(audio_format, 24000),
        volume=0,
        speech_rate=1.25,
        pitch_rate=0.75,
    )


@pytest.mark.parametrize(
    "control",
    [
        {"voice": "cosyvoice-v2"},
        {"voice_name": "longfei"},
        {"speech_rate": 1.25},
        {"volume": 0},
        {"pitch_rate": 0.75},
        {"format": "mp3"},
        {"sample_rate": 24000},
    ],
)
def test_cosyvoice_disk_cache_separates_every_sdk_control(
    tmp_path, monkeypatch, control
):
    sdk = create_autospec(SpeechSynthesizer)
    sdk.return_value.call.return_value = b"offline encoded bytes"
    monkeypatch.setattr(alibaba, "SpeechSynthesizer", sdk)
    monkeypatch.setattr(alibaba, "dashscope", Mock())
    config = {"api_key": "offline-key", "cache_dir": tmp_path / "cache"}
    original = alibaba.AlibabaTTS(config).synthesize_with_error_handling("Same text")
    changed = alibaba.AlibabaTTS(config).synthesize_with_error_handling(
        "Same text", **control
    )
    restored = alibaba.AlibabaTTS(config).synthesize_with_error_handling(
        "Same text", **control
    )

    assert [original.cached, changed.cached, restored.cached] == [False, False, True]
    assert sdk.call_count == 2


def test_cosyvoice_effective_defaults_match_explicit_options_across_instances(
    tmp_path, monkeypatch
):
    sdk = create_autospec(SpeechSynthesizer)
    sdk.return_value.call.return_value = wav_bytes(22050)
    monkeypatch.setattr(alibaba, "SpeechSynthesizer", sdk)
    monkeypatch.setattr(alibaba, "dashscope", Mock())
    config = {"api_key": "offline-key", "cache_dir": tmp_path / "cache"}
    tts = alibaba.AlibabaTTS(config)
    original = tts.synthesize_with_error_handling("Hello")
    options = alibaba.AlibabaTTS(config).resolve_synthesis_options(
        voice="cosyvoice-v1",
        voice_name="longxiaochun",
        format="wav",
        sample_rate=22050,
        volume=50,
        speech_rate=1.0,
        pitch_rate=1.0,
    )
    restored = alibaba.AlibabaTTS(config).synthesize_with_error_handling(
        "Hello", options=options
    )

    assert original.cached is False
    assert restored.cached is True
    assert restored.format == "wav"
    assert restored.sample_rate == 22050
    assert restored.duration == pytest.approx(1.0)
    assert sdk.call_count == 1
    with pytest.raises(FrozenInstanceError):
        options.volume = 75


def test_two_cosyvoice_voices_use_independent_options_concurrently(monkeypatch):
    concurrent = threading.Barrier(2)
    calls = []

    def synthesizer(**kwargs):
        calls.append(kwargs)

        def call(text):
            concurrent.wait(timeout=5)
            return kwargs["voice"].encode()

        return SimpleNamespace(call=call)

    monkeypatch.setattr(alibaba, "SpeechSynthesizer", synthesizer)
    monkeypatch.setattr(alibaba, "dashscope", Mock())
    tts = alibaba.AlibabaTTS({"api_key": "offline-key"})
    voices = ("longfei", "longxiaochun")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda voice: tts.synthesize_with_error_handling(
                    "Same text", voice_name=voice
                ),
                voices,
            )
        )

    assert [result.audio_data for result in results] == [
        voice.encode() for voice in voices
    ]
    assert {call["voice"] for call in calls} == set(voices)
    assert tts.tts_config.voice_name == "longxiaochun"
    assert tts.get_cache_stats()["entries"] == 2


@pytest.mark.parametrize("sample_rate", [0, -1, 12345])
def test_unsupported_cosyvoice_format_fails_before_sdk_call(monkeypatch, sample_rate):
    sdk = create_autospec(SpeechSynthesizer)
    monkeypatch.setattr(alibaba, "SpeechSynthesizer", sdk)
    monkeypatch.setattr(alibaba, "dashscope", Mock())
    tts = alibaba.AlibabaTTS({"api_key": "offline-key"})

    with pytest.raises(TTSError, match="不支持的音频格式或采样率"):
        tts.synthesize_with_error_handling(
            "Hello", format="wav", sample_rate=sample_rate
        )
    sdk.assert_not_called()


def test_retry_keeps_snapshot_even_if_provider_defaults_change(monkeypatch):
    sdk = create_autospec(SpeechSynthesizer)
    monkeypatch.setattr(alibaba, "SpeechSynthesizer", sdk)
    monkeypatch.setattr(alibaba, "dashscope", Mock())
    monkeypatch.setattr("chatterpal.core.tts.base.time.sleep", lambda seconds: None)
    tts = alibaba.AlibabaTTS({"api_key": "offline-key"})

    def change_defaults(text):
        tts.tts_config.voice_name = "longfei"
        tts.tts_config.speech_rate = 1.5
        raise OSError("Transient service failure")

    attempts = [change_defaults, lambda text: wav_bytes(22050)]
    sdk.return_value.call.side_effect = lambda text: attempts.pop(0)(text)
    result = tts.synthesize_with_error_handling("Hello", max_retries=2)

    assert result.cached is False
    assert result.metadata["voice_name"] == "longxiaochun"
    assert result.metadata["speech_rate"] == 1.0
    assert sdk.call_count == 2
    assert all(call.kwargs["voice"] == "longxiaochun" for call in sdk.call_args_list)
    assert all(call.kwargs["speech_rate"] == 1.0 for call in sdk.call_args_list)


class GenericTTS(TTSBase):
    def synthesize(self, text, **kwargs):
        return b"offline bytes"

    def synthesize_to_file(self, text, output_path, **kwargs):
        return True


def test_cache_identity_includes_provider_and_canonical_nested_extras():
    tts = GenericTTS()
    left = {"custom": {"language": "en", "tokens": ["a", "b"]}}
    right = {"custom": {"tokens": ["a", "b"], "language": "en"}}
    assert tts._generate_cache_key("Hello", left) == tts._generate_cache_key(
        "Hello", right
    )
    options = tts.resolve_synthesis_options(**left)
    changed = replace(options, provider="another-provider")
    assert tts._generate_cache_key("Hello", options) != tts._generate_cache_key(
        "Hello", changed
    )
    left["custom"]["tokens"].append("changed")
    assert options.extra == (("custom", (("language", "en"), ("tokens", ("a", "b")))),)


def test_options_freeze_nested_parameters_supplied_directly():
    extra = {"custom": {"tokens": ["first"]}}
    options = SynthesisOptions(provider="generic", extra=extra)
    extra["custom"]["tokens"].append("second")
    assert asdict(options)["extra"] == (("custom", (("tokens", ("first",)),)),)


def test_result_uses_encoded_metadata_over_resolved_settings(monkeypatch):
    tts = GenericTTS({"audio_format": "mp3", "sample_rate": 16000})
    monkeypatch.setattr(
        tts, "synthesize", Mock(return_value=wav_bytes(24000, channels=2))
    )

    fresh = tts.synthesize_with_error_handling("Hello")
    cached = tts.synthesize_with_error_handling("Hello")

    assert fresh.format == cached.format == "wav"
    assert fresh.sample_rate == cached.sample_rate == 24000
    assert fresh.duration == cached.duration == pytest.approx(1.0)
    tts.synthesize.assert_called_once_with("Hello")


@pytest.mark.parametrize("player", ["internal", "playsound", "system"])
def test_library_playback_uses_real_encoded_file_extension(monkeypatch, player):
    payload = mp3_bytes()
    paths = []

    def play(path):
        path = Path(path)
        paths.append(path)
        assert path.suffix == ".mp3"
        assert path.read_bytes() == payload
        assert sf.info(path).samplerate == 24000

    monkeypatch.setattr("platform.system", lambda: "darwin")
    monkeypatch.setattr("os.name", "posix")
    monkeypatch.setattr("subprocess.run", lambda command, **kwargs: play(command[1]))
    monkeypatch.setitem(sys.modules, "playsound", SimpleNamespace(playsound=play))
    tts = GenericTTS()
    method = {
        "internal": tts._play_audio_internal,
        "playsound": tts._play_with_playsound,
        "system": tts._play_with_system,
    }[player]

    assert method(payload) is True
    assert len(paths) == 1
    assert not paths[0].exists()


def test_cosyvoice_retry_error_keeps_final_reason_without_api_key(monkeypatch):
    sdk = create_autospec(SpeechSynthesizer)
    sdk.return_value.call.side_effect = RuntimeError(
        "offline-key: provider unavailable"
    )
    monkeypatch.setattr(alibaba, "SpeechSynthesizer", sdk)
    monkeypatch.setattr(alibaba, "dashscope", Mock())
    monkeypatch.setattr("chatterpal.core.tts.base.time.sleep", lambda seconds: None)
    tts = alibaba.AlibabaTTS({"api_key": "offline-key"})

    with pytest.raises(TTSError, match="已重试 2 次") as caught:
        tts.synthesize_with_error_handling("Hello", max_retries=2)

    assert sdk.return_value.call.call_count == 2
    assert "offline-key" not in str(caught.value)
    assert "offline-key" not in repr(caught.value.metadata)
    assert "provider unavailable" in caught.value.metadata["error_message"]
    assert caught.value.metadata["attempt"] == 2
    assert tts.get_cache_stats()["entries"] == 0
