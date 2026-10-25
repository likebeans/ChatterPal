"""Schema upgrades, bounded audio metadata and deletion races use real SQLite."""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from chatterpal.learning.storage import LearningStore, StoreError
from chatterpal.web.routes.learning_records import mount_learning_api


def test_upgrade_version_one_preserves_identity_records_audio_and_recovery(
    tmp_path, audio_bytes
):
    path = tmp_path / "v1.sqlite3"
    store = LearningStore(path)
    identity, token = store.bootstrap()
    learner = identity["learner_id"]
    store.update_profile(learner, {"retain_audio": True})
    record, operation_id = store.create_record(
        learner, "new-record-1", kind="session", data={"turns": ["Hello"]}
    )
    audio, _ = store.add_audio(learner, "new-audio-1", record["record_id"], audio_bytes)
    # Recreate the released candidate's v1 schema, retaining its actual rows.
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE operation_records")
        connection.execute("DROP INDEX records_parent")
        connection.execute("ALTER TABLE records DROP COLUMN parent_record_id")
        connection.execute("ALTER TABLE operations DROP COLUMN expected_revision")
        connection.execute("PRAGMA user_version=1")
    upgraded = LearningStore(path)
    assert upgraded.authenticate(token) == learner
    assert upgraded.get_record(learner, record["record_id"])["data"] == record["data"]
    assert upgraded.get_audio(learner, audio["audio_asset_id"])[1] == audio_bytes
    assert (
        upgraded.get_operation(learner, operation_id=operation_id)["result_id"]
        == record["record_id"]
    )
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    upgraded.delete_record(learner, record["record_id"])
    with pytest.raises(StoreError):
        upgraded.get_operation(learner, operation_id=operation_id)


def test_session_history_and_configured_proxy_origin(tmp_path):
    app = FastAPI()
    store = LearningStore(tmp_path / "history.sqlite3")
    mount_learning_api(
        app, SimpleNamespace(), store=store, allowed_origins={"http://localhost:13204"}
    )
    import asyncio

    async def drive():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as browser:
            bootstrap = await browser.post(
                "/api/learning/bootstrap", headers={"Origin": "http://localhost:13204"}
            )
            assert bootstrap.status_code == 200
            learner = bootstrap.json()["learner_id"]
            stored, _ = store.create_record(
                learner,
                "session-start-1",
                kind="session",
                session_id="business-id",
                data={"session_id": "business-id", "turns": []},
            )
            assert (await browser.get("/api/learning/sessions")).json() == {
                "items": [stored["data"]],
                "next_cursor": None,
            }
            assert (
                await browser.get("/api/learning/sessions/business-id")
            ).json() == stored["data"]
            assert (
                await browser.get("/api/learning/sessions/missing")
            ).status_code == 404
            assert (
                await browser.post(
                    "/api/learning/bootstrap",
                    headers={"Origin": "http://localhost:3010"},
                )
            ).status_code == 403

    asyncio.run(drive())


def test_delete_learner_while_provider_is_running_blocks_all_late_writes(tmp_path):
    store = LearningStore(tmp_path / "delete-race.sqlite3")
    learner = store.bootstrap()[0]["learner_id"]
    record, _ = store.create_record(
        learner, "session-start-1", kind="session", data={"turns": []}
    )
    claim = store.begin_operation(
        learner,
        "slow-turn-start-1",
        "turn",
        {},
        record_id=record["record_id"],
        expected_revision=1,
    )
    entered, release = threading.Event(), threading.Event()

    def slow_provider():
        entered.set()
        assert release.wait(5)
        try:
            store.complete_operation(
                learner,
                claim.operation["operation_id"],
                claim.lease_token,
                updates=[
                    {
                        "record_id": record["record_id"],
                        "expected_revision": 1,
                        "data": {"turns": ["late-user", "late-assistant"]},
                    }
                ],
            )
        except StoreError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(slow_provider)
        try:
            assert entered.wait(3)
            store.delete_learner(learner)
        finally:
            release.set()
        assert pending.result(timeout=5) == "identity_required"
    with sqlite3.connect(store.path) as connection:
        for table in (
            "learners",
            "records",
            "operations",
            "audio_assets",
            "operation_records",
        ):
            assert (
                connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
            )


@pytest.mark.parametrize(
    "content,expected",
    [
        (b"ID3" + b"\0" * 16, "audio/mpeg"),
        (b"\xff\xfb" + b"\0" * 16, "audio/mpeg"),
        (b"OggS" + b"\0" * 16, "audio/ogg"),
        (b"fLaC" + b"\0" * 16, "audio/flac"),
        (b"\x1aE\xdf\xa3" + b"\0" * 16, "audio/webm"),
    ],
)
def test_audio_container_signatures_are_stored_without_filename_paths(
    tmp_path, content, expected
):
    store = LearningStore(tmp_path / "audio.sqlite3")
    learner = store.bootstrap()[0]["learner_id"]
    store.update_profile(learner, {"retain_audio": True})
    record, _ = store.create_record(learner, "record-start-1", kind="chat", data={})
    audio, _ = store.add_audio(learner, "audio-start-1", record["record_id"], content)
    assert audio["media_type"] == expected
    assert store.get_audio(learner, audio["audio_asset_id"])[1] == content
    assert not list(Path(tmp_path).glob("*.mp3"))
    with store.export_data(learner) as stream:
        exported = json.load(stream)
        assert exported["records"][0]["audio_assets"][0]["state"] == "available"
