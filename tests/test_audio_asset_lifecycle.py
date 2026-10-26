"""Generated audio remains bounded without removing active HTTP downloads."""

import asyncio

import pytest

from chatterpal.web.audio_assets import (
    AudioAssetLimit,
    AudioDownload,
    GeneratedAudioStore,
)


def test_expiry_rejects_new_download_but_preserves_active_reader(tmp_path):
    now = [1000.0]
    store = GeneratedAudioStore(tmp_path, ttl=10, clock=lambda: now[0])
    name = store.save(b"ID3-audio", "mp3")
    with store.download(name) as (path, media_type):
        assert media_type == "audio/mpeg"
        now[0] = 1011.0
        store.cleanup()
        assert path.read_bytes() == b"ID3-audio"
        with pytest.raises(FileNotFoundError):
            with store.download(name):
                pytest.fail("Expired audio opened")
    assert not path.exists()


def test_capacity_evicts_oldest_unleased_asset_and_refuses_when_all_active(tmp_path):
    now = [1000.0]
    store = GeneratedAudioStore(
        tmp_path, max_files=1, max_bytes=10, clock=lambda: now[0]
    )
    first = store.save(b"first", "wav")
    with store.download(first):
        with pytest.raises(AudioAssetLimit):
            store.save(b"second", "mp3")
        assert store.snapshot()["files"] == 1
    now[0] += 1
    second = store.save(b"second", "mp3")
    assert not (tmp_path / first).exists()
    assert (tmp_path / second).read_bytes() == b"second"
    assert store.snapshot()["bytes"] == 6


def test_restart_recovers_assets_and_never_serves_uploads_or_symlinks(tmp_path):
    now = lambda: 1000.0
    store = GeneratedAudioStore(tmp_path, clock=now)
    name = store.save(b"original", "ogg")
    recovered = GeneratedAudioStore(tmp_path, clock=now)
    with recovered.download(name) as (path, media_type):
        assert path.read_bytes() == b"original"
        assert media_type == "audio/ogg"
    upload = tmp_path / "input_private.wav"
    upload.write_bytes(b"learner")
    for invalid in (upload.name, "../private.wav", "absent.mp3"):
        with pytest.raises(FileNotFoundError):
            with recovered.download(invalid):
                pytest.fail("Non-generated asset opened")
    (tmp_path / name).unlink()
    (tmp_path / name).symlink_to(upload)
    with pytest.raises(FileNotFoundError):
        with recovered.download(name):
            pytest.fail("Symlink opened")
    recovered.cleanup()
    assert upload.read_bytes() == b"learner"


def test_oversized_payload_and_unknown_format_do_not_leave_partial_files(tmp_path):
    store = GeneratedAudioStore(tmp_path, max_bytes=4)
    with pytest.raises(AudioAssetLimit):
        store.save(b"12345", "mp3")
    with pytest.raises(ValueError):
        store.save(b"123", "../wav")
    assert list(tmp_path.iterdir()) == []


def test_stream_cancellation_releases_download_lease(tmp_path):
    store = GeneratedAudioStore(tmp_path)
    name = store.save(b"ID3-audio", "mp3")
    lease = store.download(name)
    path, media_type = lease.__enter__()
    response = AudioDownload(lease, path, media_type)

    async def cancel_send(message):
        raise asyncio.CancelledError()

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def run():
        with pytest.raises(asyncio.CancelledError):
            await response(
                {"type": "http", "method": "GET", "headers": []}, receive, cancel_send
            )

    asyncio.run(run())
    assert store.snapshot()["active_downloads"] == 0
