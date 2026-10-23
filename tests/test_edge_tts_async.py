"""Offline regressions for Edge TTS calls made from a running event loop."""

import asyncio
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from chatterpal.core.tts.base import TTSError
from chatterpal.core.tts.edge import EdgeTTS


@pytest.fixture
def voice_catalogue(monkeypatch):
    """Use the full names returned by Edge, which differ from synthesis IDs."""
    voices = [
        {
            "Name": "Microsoft Server Speech Text to Speech Voice (en-GB, SoniaNeural)",
            "ShortName": "en-GB-SoniaNeural",
            "FriendlyName": "Microsoft Sonia Online (Natural) - English (United Kingdom)",
            "Gender": "Female",
            "Locale": "en-GB",
            "SampleRateHertz": "24000",
            "VoiceType": "Neural",
        },
        {
            "Name": "Microsoft Server Speech Text to Speech Voice (zh-CN, XiaoyiNeural)",
            "ShortName": "zh-CN-XiaoyiNeural",
            "FriendlyName": "Microsoft Xiaoyi Online (Natural) - Chinese (Mainland)",
            "Gender": "Female",
            "Locale": "zh-CN",
            "SampleRateHertz": "24000",
            "VoiceType": "Neural",
        },
    ]
    list_voices = AsyncMock(return_value=voices)
    monkeypatch.setattr("edge_tts.list_voices", list_voices)
    return voices, list_voices


def test_sync_voice_lookup_inside_running_event_loop(voice_catalogue, caplog):
    """The API's original sync call pattern must return real service data."""
    voices, list_voices = voice_catalogue
    tts = EdgeTTS()

    async def lookup():
        supported = tts.get_supported_voices()
        metadata = [tts.get_voice_info(voice) for voice in supported]
        return supported, metadata

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        supported, metadata = asyncio.run(lookup())

    assert not caught, [str(warning.message) for warning in caught]
    assert "asyncio.run() cannot be called" not in caplog.text
    assert supported == [voice["ShortName"] for voice in voices]
    assert metadata[0]["gender"] == "Female"
    assert metadata[0]["locale"] == "en-GB"
    assert metadata[1]["locale"] == "zh-CN"
    assert list_voices.await_count == 1


def test_sync_catalogue_cache_and_full_name_alias(voice_catalogue):
    voices, list_voices = voice_catalogue
    tts = EdgeTTS({"voice": "en-GB-SoniaNeural"})

    assert tts.get_supported_voices() == [voice["ShortName"] for voice in voices]
    info = tts.get_voice_info()
    assert info["name"] == "en-GB-SoniaNeural"
    assert info["display_name"] == voices[0]["FriendlyName"]
    assert info["language"] == "en-GB"
    assert tts.get_voice_info(voices[0]["Name"]) == info
    assert tts.get_voice_info(voices[1]["ShortName"])["locale"] == "zh-CN"
    assert list_voices.await_count == 1


def test_legacy_voice_records_without_short_name(monkeypatch):
    list_voices = AsyncMock(
        return_value=[
            {
                "Name": "en-US-AriaNeural",
                "DisplayName": "Aria",
                "LocalName": "Aria local name",
                "Gender": "Female",
                "Locale": "en-US",
                "Language": "English",
            }
        ]
    )
    monkeypatch.setattr("edge_tts.list_voices", list_voices)
    tts = EdgeTTS()

    assert tts.get_supported_voices() == ["en-US-AriaNeural"]
    assert tts.get_voice_info()["display_name"] == "Aria"
    assert tts.get_voice_info()["local_name"] == "Aria local name"
    assert tts.get_voice_info()["language"] == "English"
    assert list_voices.await_count == 1


def test_network_failure_uses_correct_cached_fallback_and_recovers(monkeypatch, caplog):
    list_voices = AsyncMock(side_effect=OSError("offline"))
    monkeypatch.setattr("edge_tts.list_voices", list_voices)
    clock = Mock(return_value=100.0)
    monkeypatch.setattr("chatterpal.core.tts.edge.time.monotonic", clock)
    tts = EdgeTTS()

    async def lookup():
        voices = tts.get_supported_voices()
        return voices, [tts.get_voice_info(voice) for voice in voices]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        voices, metadata = asyncio.run(lookup())

    assert not caught, [str(warning.message) for warning in caught]
    assert "asyncio.run() cannot be called" not in caplog.text
    assert "en-US-AriaNeural" in voices
    assert "zh-CN-YunxiNeural" in voices
    by_name = {info["name"]: info for info in metadata}
    assert by_name["zh-CN-YunxiNeural"]["locale"] == "zh-CN"
    assert by_name["zh-CN-YunxiNeural"]["language"] == "zh-CN"
    assert by_name["zh-CN-YunxiNeural"]["gender"] == "Male"
    assert list_voices.await_count == 1

    # A short failure cooldown avoids a network request for every fallback voice.
    list_voices.side_effect = None
    list_voices.return_value = [
        {"ShortName": "en-GB-SoniaNeural", "Locale": "en-GB", "Gender": "Female"}
    ]
    clock.return_value = 161.0
    assert tts.get_supported_voices() == ["en-GB-SoniaNeural"]
    assert list_voices.await_count == 2


def test_expired_successful_catalogue_survives_refresh_failure(
    voice_catalogue, monkeypatch
):
    voices, list_voices = voice_catalogue
    clock = Mock(return_value=100.0)
    monkeypatch.setattr("chatterpal.core.tts.edge.time.monotonic", clock)
    tts = EdgeTTS()
    assert tts.get_supported_voices() == [voice["ShortName"] for voice in voices]

    clock.return_value = 3701.0
    list_voices.side_effect = OSError("offline")
    assert tts.get_supported_voices() == [voice["ShortName"] for voice in voices]
    assert tts.get_voice_info("en-GB-SoniaNeural")["locale"] == "en-GB"
    assert list_voices.await_count == 2


def test_concurrent_catalogue_requests_share_one_fetch(voice_catalogue):
    voices, list_voices = voice_catalogue
    tts = EdgeTTS()

    async def requests():
        return await asyncio.gather(
            *(asyncio.to_thread(tts.get_supported_voices) for _ in range(4))
        )

    expected = [voice["ShortName"] for voice in voices]
    assert asyncio.run(requests()) == [expected] * 4
    assert list_voices.await_count == 1


@pytest.mark.parametrize("running_loop", [False, True])
@pytest.mark.parametrize("to_file", [False, True])
def test_synthesis_sync_interfaces_work_in_both_contexts(
    monkeypatch, tmp_path, running_loop, to_file
):
    paths = []

    async def save(output_path):
        paths.append(Path(output_path))
        Path(output_path).write_bytes(b"mock audio")

    communicate = Mock(return_value=SimpleNamespace(save=save))
    monkeypatch.setattr("edge_tts.Communicate", communicate)
    tts = EdgeTTS()
    output_path = tmp_path / "output.wav"

    def synthesize():
        kwargs = dict(
            voice="en-GB-SoniaNeural", rate="+10%", pitch="+1Hz", volume="-5%"
        )
        if to_file:
            return tts.synthesize_to_file("Hello @ world", str(output_path), **kwargs)
        return tts.synthesize("Hello @ world", **kwargs)

    async def call_in_loop():
        return synthesize()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = asyncio.run(call_in_loop()) if running_loop else synthesize()

    assert not caught, [str(warning.message) for warning in caught]
    communicate.assert_called_once_with(
        "Hello world", "en-GB-SoniaNeural", rate="+10%", pitch="+1Hz", volume="-5%"
    )
    if to_file:
        assert result is True
        assert output_path.read_bytes() == b"mock audio"
    else:
        assert result == b"mock audio"
        assert len(paths) == 1 and not paths[0].exists()


@pytest.mark.parametrize("failure", ["network", "empty"])
def test_synthesis_failure_raises_tts_error_and_cleans_temp_file(monkeypatch, failure):
    paths = []

    async def save(output_path):
        paths.append(Path(output_path))
        if failure == "network":
            raise OSError("offline")

    monkeypatch.setattr(
        "edge_tts.Communicate", Mock(return_value=SimpleNamespace(save=save))
    )
    tts = EdgeTTS()

    async def call_in_loop():
        return tts.synthesize("Hello world")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(TTSError, match="语音合成失败"):
            asyncio.run(call_in_loop())

    assert not caught, [str(warning.message) for warning in caught]
    assert len(paths) == 1 and not paths[0].exists()
