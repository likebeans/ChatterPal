"""Offline smoke of current source in an existing Python runtime image.

Mount src/, tests/ and this script read-only; no pytest, network, models or secrets
are required. This checks the mounted source, not a fresh Dockerfile build.
"""

import io
import json
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

from fastapi.testclient import TestClient

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))

from chatterpal.config.settings import Settings
from chatterpal.web import api_server
from tests.int2_support import FixtureASR, FixtureLLM, FixtureTTS

buffer = io.BytesIO()
with wave.open(buffer, "wb") as audio:
    audio.setnchannels(1)
    audio.setsampwidth(2)
    audio.setframerate(16000)
    audio.writeframes(b"\0\0" * 1600)
audio_bytes = buffer.getvalue()
asr, llm, tts = FixtureASR(), FixtureLLM(), FixtureTTS(audio_bytes)
api_server.create_asr = lambda settings: asr
api_server.create_llm = lambda settings: llm
api_server.create_tts = lambda settings: tts
prefix = "/api/learning"


def saved(client, response):
    assert response.status_code == 202, response.text
    operation = response.json()
    deadline = time.monotonic() + 5
    while operation["status"] == "pending" and time.monotonic() < deadline:
        threading.Event().wait(0.005)
        response = client.get(f"{prefix}/operations/{operation['operation_id']}")
        assert response.status_code == 200, response.text
        operation = response.json()
    assert operation["status"] == "succeeded", operation
    result = operation["result"]
    return result["data"] if operation["result_type"] == "record" else result


def main():
    with tempfile.TemporaryDirectory(prefix="chatterpal-int2-") as directory:
        settings = Settings(
            _env_file=None,
            learning_db_path=str(Path(directory) / "learning.sqlite3"),
            learning_shutdown_timeout=1,
        )
        server = api_server.APIServer(settings)
        with TestClient(server.app) as client:
            assert client.get("/health").status_code == 200
            identity = client.post(f"{prefix}/bootstrap").json()
            session = saved(
                client,
                client.post(
                    f"{prefix}/sessions",
                    json={
                        "request_id": "container-start-001",
                        "task_id": "weekend",
                        "content_version": "1",
                    },
                ),
            )
            session = saved(
                client,
                client.post(
                    f"{prefix}/sessions/{session['session_id']}/turns",
                    json={
                        "request_id": "container-turn-001",
                        "expected_revision": session["revision"],
                        "text": "I go hiking last Saturday.",
                    },
                ),
            )
            session = saved(
                client,
                client.post(
                    f"{prefix}/sessions/{session['session_id']}/turns",
                    data={
                        "request_id": "container-turn-002",
                        "expected_revision": session["revision"],
                    },
                    files={"audio": ("answer.wav", audio_bytes, "audio/wav")},
                ),
            )
            assert asr.calls == [audio_bytes]
            asset = session["turns"][-2]["audio"]
            assert client.get(asset["url"]).content == audio_bytes
            session = saved(
                client,
                client.post(
                    f"{prefix}/sessions/{session['session_id']}/finish",
                    json={
                        "request_id": "container-finish-001",
                        "expected_revision": session["revision"],
                    },
                ),
            )
            assert session["feedback"]["status"] == "available"
            original = session["attempts"][0]
            attempt = saved(
                client,
                client.post(
                    f"{prefix}/attempts",
                    json={
                        "request_id": "container-retry-001",
                        "session_id": session["session_id"],
                        "source_revision": session["revision"],
                        "source_turn_id": original["source_turn_id"],
                        "focus_id": original["focus_id"],
                        "parent_attempt_id": original["attempt_id"],
                        "mode": "free_response",
                        "text": "I spent Saturday hiking with friends and felt relaxed.",
                    },
                ),
            )
            assert attempt["parent_attempt_id"] == original["attempt_id"]
            session = client.get(f"{prefix}/sessions/{session['session_id']}").json()
            cookie = client.cookies.get("chatterpal_learner")
        with TestClient(api_server.APIServer(settings).app) as restarted:
            restarted.cookies.set("chatterpal_learner", cookie, path=prefix)
            assert (
                restarted.post(f"{prefix}/bootstrap").json()["learner_id"]
                == identity["learner_id"]
            )
            assert (
                restarted.get(f"{prefix}/sessions/{session['session_id']}").json()
                == session
            )
            assert (
                restarted.get(f"{prefix}/attempts/{attempt['attempt_id']}").json()
                == attempt
            )
            assert restarted.get(asset["url"]).content == audio_bytes
            assert restarted.get(f"{prefix}/reviews/due").status_code == 503
        print(
            json.dumps(
                {
                    "passed": True,
                    "sqlite_restart": True,
                    "asr_calls": len(asr.calls),
                    "attempts": len(session["attempts"]),
                    "audio_recovered": True,
                    "source": str(Path(api_server.__file__).resolve()),
                }
            )
        )


if __name__ == "__main__":
    main()
