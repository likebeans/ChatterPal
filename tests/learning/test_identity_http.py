"""Exercise A2's real HTTP boundary with an isolated SQLite file."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from chatterpal.learning.storage import LearningStore
from chatterpal.web.routes.learning_records import COOKIE_NAME, mount_learning_api


@pytest.fixture
def api(tmp_path):
    database = LearningStore(tmp_path / "learning.sqlite3")
    app = FastAPI()
    mount_learning_api(app, SimpleNamespace(), store=database)
    return app, database


def client(app, base_url="http://test"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)


@pytest.mark.asyncio
async def test_bootstrap_sets_private_cookie_and_recovers_profile_after_app_restart(
    api,
):
    app, database = api
    async with client(app) as browser:
        assert (await browser.get("/api/learning/profile")).status_code == 401
        first = await browser.post("/api/learning/bootstrap")
        assert first.status_code == 200
        assert first.headers["cache-control"] == "no-store"
        cookie = first.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie
        assert "Path=/api/learning" in cookie and "Max-Age=31536000" in cookie
        assert "Secure" not in cookie
        patch = await browser.patch(
            "/api/learning/profile", json={"goal": "travel", "daily_minutes": 12}
        )
        assert patch.status_code == 200
        token = browser.cookies.get(COOKIE_NAME)
        second = await browser.post("/api/learning/bootstrap")
        assert second.json()["learner_id"] == first.json()["learner_id"]
    restarted = FastAPI()
    mount_learning_api(restarted, SimpleNamespace(), store=LearningStore(database.path))
    async with client(restarted) as browser:
        browser.cookies.set(COOKIE_NAME, token, path="/api/learning")
        restored = await browser.post("/api/learning/bootstrap")
        assert restored.json()["learner_id"] == first.json()["learner_id"]
        assert restored.json()["profile"]["goal"] == "travel"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,configured", [("https://test", False), ("http://test", True)]
)
async def test_https_or_configuration_enforces_secure_cookie(tmp_path, url, configured):
    app = FastAPI()
    mount_learning_api(
        app,
        SimpleNamespace(learning_cookie_secure=configured),
        store=LearningStore(tmp_path / "secure.sqlite3"),
    )
    async with client(app, url) as browser:
        response = await browser.post("/api/learning/bootstrap")
        assert "Secure" in response.headers["set-cookie"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://untrusted.example"},
        {"Origin": "null"},
        {"Origin": "http://test:broken"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": "http://test", "Sec-Fetch-Site": "cross-site"},
    ],
)
async def test_untrusted_write_origins_are_rejected_without_creating_identity(
    api, headers
):
    app, _ = api
    async with client(app) as browser:
        response = await browser.post("/api/learning/bootstrap", headers=headers)
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "origin_not_allowed"
        assert "set-cookie" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin", ["http://test", "http://test:80", "http://localhost:3010"]
)
async def test_same_origin_and_declared_development_origin_can_bootstrap(api, origin):
    app, _ = api
    async with client(app) as browser:
        assert (
            await browser.post("/api/learning/bootstrap", headers={"Origin": origin})
        ).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch",
    [
        {"goal": "bad"},
        {"daily_minutes": True},
        {"daily_minutes": "8"},
        {"daily_minutes": None},
        {"retain_audio": None},
        {"retain_audio": "true"},
        {"learner_id": "other"},
        {"goal": None},
    ],
)
async def test_http_profile_validates_types_and_rejects_unknown_ownership_fields(
    api, patch
):
    app, _ = api
    async with client(app) as browser:
        first = await browser.post("/api/learning/bootstrap")
        response = await browser.patch("/api/learning/profile", json=patch)
        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store"
        assert (await browser.get("/api/learning/profile")).json() == first.json()[
            "profile"
        ]


@pytest.mark.asyncio
async def test_two_browsers_cannot_read_modify_export_or_delete_each_others_data(
    api, audio_bytes
):
    app, database = api
    async with client(app) as a, client(app) as b:
        first, second = await asyncio.gather(
            a.post("/api/learning/bootstrap"), b.post("/api/learning/bootstrap")
        )
        assert first.json()["learner_id"] != second.json()["learner_id"]
        body = {
            "request_id": "new-session-001",
            "kind": "session",
            "session_id": "session-a",
            "data": {"turns": [{"text": "Only A"}]},
        }
        created = await a.post("/api/learning/records", json=body)
        assert created.status_code == 200
        record, operation_id = created.json(), created.headers["x-operation-id"]
        assert record["record_id"] != "session-a"
        await a.patch("/api/learning/profile", json={"retain_audio": True})
        saved = await a.post(
            f"/api/learning/records/{record['record_id']}/audio",
            data={"request_id": "audio-session-1"},
            files={"audio": ("../../fake.html", audio_bytes, "text/html")},
        )
        assert saved.status_code == 200 and saved.json()["media_type"] == "audio/wav"
        assert (await a.get(saved.json()["url"])).content == audio_bytes
        for response in (
            await b.get(f"/api/learning/records/{record['record_id']}"),
            await b.put(
                f"/api/learning/records/{record['record_id']}",
                json={
                    "request_id": "update-session-1",
                    "expected_revision": 1,
                    "data": {},
                },
            ),
            await b.delete(f"/api/learning/records/{record['record_id']}"),
            await b.get(f"/api/learning/operations/{operation_id}"),
            await b.get(saved.json()["url"]),
        ):
            assert response.status_code == 404
        assert (await b.get("/api/learning/records")).json()["items"] == []
        export_b = await b.get("/api/learning/export")
        assert export_b.status_code == 200 and export_b.json()["records"] == []
        assert "Only A" not in export_b.text
        export_a = await a.get("/api/learning/export")
        assert export_a.headers["content-type"].startswith("application/json")
        assert (
            'filename="chatterpal-learning.json"'
            in export_a.headers["content-disposition"]
        )
        assert export_a.json()["records"][0]["record_id"] == record["record_id"]
        assert "token_hash" not in export_a.text and "lease_token" not in export_a.text
        deleted = await a.request(
            "DELETE", "/api/learning/data", json={"request_id": "delete-all-001"}
        )
        assert deleted.status_code == 200 and deleted.json() == {"deleted": True}
        assert "Max-Age=0" in deleted.headers["set-cookie"]
        assert (await a.get(saved.json()["url"])).status_code == 401
        assert (await b.get("/api/learning/profile")).status_code == 200
        assert (await a.post("/api/learning/bootstrap")).json()[
            "learner_id"
        ] != first.json()["learner_id"]
        assert database.list_records(second.json()["learner_id"])["items"] == []


@pytest.mark.asyncio
async def test_http_dedup_revision_conflicts_and_recovery_lookup(api):
    app, _ = api
    async with client(app) as browser:
        await browser.post("/api/learning/bootstrap")
        body = {
            "request_id": "new-session-001",
            "kind": "session",
            "data": {"turns": []},
        }
        responses = await asyncio.gather(
            *(browser.post("/api/learning/records", json=body) for _ in range(4))
        )
        assert all(response.status_code == 200 for response in responses)
        assert all(response.json() == responses[0].json() for response in responses)
        assert len({response.headers["x-operation-id"] for response in responses}) == 1
        conflict = await browser.post(
            "/api/learning/records", json={**body, "data": {"changed": True}}
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "request_conflict"
        restored = await browser.get("/api/learning/requests/new-session-001")
        assert restored.json()["result"] == responses[0].json()
        record_id = responses[0].json()["record_id"]
        update = {
            "request_id": "update-session-1",
            "expected_revision": 1,
            "data": {"turns": [{"text": "Hello"}]},
        }
        updated = await browser.put(f"/api/learning/records/{record_id}", json=update)
        assert updated.json()["revision"] == 2
        assert (
            await browser.put(f"/api/learning/records/{record_id}", json=update)
        ).json() == updated.json()
        stale = await browser.put(
            f"/api/learning/records/{record_id}",
            json={**update, "request_id": "update-session-2"},
        )
        assert stale.status_code == 409
        assert (await browser.get("/api/learning/records?limit=0")).status_code == 422
        assert (
            await browser.get("/api/learning/records?cursor=bad")
        ).status_code == 422
        assert (
            await browser.delete(f"/api/learning/records/{record_id}")
        ).status_code == 200
        assert (
            await browser.get("/api/learning/requests/new-session-001")
        ).status_code == 404


@pytest.mark.asyncio
async def test_http_invalid_uploads_leave_no_assets_and_respect_size_limit(api):
    app, database = api
    database.max_audio_bytes = 32
    async with client(app) as browser:
        await browser.post("/api/learning/bootstrap")
        created = await browser.post(
            "/api/learning/records",
            json={"request_id": "new-record-1", "kind": "analysis", "data": {}},
        )
        record_id = created.json()["record_id"]
        for content, expected in (
            (b"", 422),
            (b"<html>no audio</html>", 415),
            (b"x" * 33, 413),
        ):
            response = await browser.post(
                f"/api/learning/records/{record_id}/audio",
                data={"request_id": "save-audio-001"},
                files={"audio": ("fake.wav", content, "audio/wav")},
            )
            assert response.status_code == expected
        assert (await browser.get(f"/api/learning/records/{record_id}")).json()[
            "audio_assets"
        ] == []


@pytest.mark.asyncio
async def test_default_factory_is_lazy_and_non_learning_routes_are_unchanged(tmp_path):
    path = tmp_path / "configured.sqlite3"
    app = FastAPI()

    @app.get("/legacy")
    def legacy():
        return {"legacy": True}

    mount_learning_api(app, SimpleNamespace(learning_db_path=path))
    assert not path.exists()
    async with client(app) as browser:
        response = await browser.get("/legacy")
        assert response.json() == {"legacy": True}
        assert "cache-control" not in response.headers
        assert not path.exists()
        assert (await browser.post("/api/learning/bootstrap")).status_code == 200
        assert path.is_file()
