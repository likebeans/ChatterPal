"""Pronunciation HTTP contracts use real services and controlled provider boundaries."""

import asyncio
import io
import json
import threading
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import numpy as np
import pytest

from chatterpal.core.assessment.base import AssessmentError
from chatterpal.services.correction import CorrectionService
from chatterpal.services.ai_correction import AICorrectionService
from chatterpal.web import api_server


def wav_audio(value=1200):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(np.full(16000, value, dtype="<i2").tobytes())
    return output.getvalue()


@pytest.fixture
def analysis_api(monkeypatch, tmp_path):
    monkeypatch.setattr(
        api_server.APIServer, "_init_core_components", lambda self: None
    )
    monkeypatch.setattr(api_server.APIServer, "_init_services", lambda self: None)
    # Exercise the actual analyzers' unavailable-dependency paths without external tools.
    monkeypatch.setattr("chatterpal.core.assessment.phoneme.parselmouth", None)
    monkeypatch.setattr("chatterpal.core.assessment.prosody.mysp", None)
    api = api_server.APIServer(settings=SimpleNamespace())
    api.temp_dir = tmp_path
    api.asr = SimpleNamespace(recognize_file=Mock(return_value="I said these words"))
    api.llm = api.tts = api.chat_service = api.evaluation_service = None
    api.correction_service = CorrectionService(asr=api.asr)
    api.ai_correction_service = AICorrectionService(
        base_correction_service=api.correction_service
    )
    return api


def test_service_preserves_the_actual_transcript_in_serialized_report(analysis_api):
    path = analysis_api.temp_dir / "caller.wav"
    path.write_bytes(wav_audio())
    report = analysis_api.correction_service.comprehensive_correction(
        str(path), "Different reference words", analysis_level="basic"
    )
    assert report.recognized_text == "I said these words"
    assert report.to_dict()["recognized_text"] == "I said these words"


@pytest.mark.parametrize("route", ["analyze", "ai-analyze"])
def test_analysis_http_returns_transcript_and_keeps_existing_fields(
    analysis_api, route
):
    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=analysis_api.app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/pronunciation/{route}",
                data={"reference_text": "Different reference words"},
                files={"audio": ("input.wav", wav_audio(), "audio/wav")},
            )
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["recognized_text"] == "I said these words"
            assert result["overall_score"] == 75.0
            if route == "analyze":
                assert set(result) == {
                    "recognized_text",
                    "overall_score",
                    "corrections",
                    "suggestions",
                }
                assert any(item["corrected"] for item in result["corrections"])
            else:
                assert result["difficulty_level"] == "intermediate"
                assert "AI" in result["detailed_analysis"]
                assert "基础分析" in result["detailed_analysis"]
                assert result["confidence_score"] == 0.0
                if result["next_scenario"]:
                    scenario, difficulty = result["next_scenario"].rsplit("-", 1)
                    context = await client.get(
                        f"/api/scenarios/{scenario}/{difficulty}"
                    )
                    assert context.status_code == 200

    asyncio.run(request())
    analysis_api.asr.recognize_file.assert_called_once()
    assert not list(analysis_api.temp_dir.iterdir())


def test_every_listed_scenario_has_a_usable_context(analysis_api):
    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=analysis_api.app), base_url="http://test"
        ) as client:
            response = await client.get("/api/scenarios")
            assert response.status_code == 200
            scenarios = response.json()
            expected = {
                (kind.value, level.value)
                for kind, levels in analysis_api.ai_correction_service.scenarios.items()
                for level in levels
            }
            assert {
                (item["scenario_type"], item["difficulty_level"]) for item in scenarios
            } == expected
            for item in scenarios:
                context = await client.get(
                    f"/api/scenarios/{item['scenario_type']}/{item['difficulty_level']}"
                )
                assert context.status_code == 200, context.text
                assert context.json()["context_description"]

    asyncio.run(request())


@pytest.mark.parametrize(
    ("scenario", "difficulty", "status"),
    [
        ("travel_situation", "beginner", 404),
        ("daily_conversation", "advanced", 404),
        ("invalid", "beginner", 422),
        ("daily_conversation", "invalid", 422),
    ],
)
def test_unavailable_scenario_has_an_explanatory_response(
    analysis_api, scenario, difficulty, status
):
    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=analysis_api.app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/scenarios/{scenario}/{difficulty}")
            assert response.status_code == status, response.text
            assert response.json()["detail"]

    asyncio.run(request())


def test_successful_ai_http_maps_feedback_and_a_usable_next_scenario(analysis_api):
    prompts = []

    def generate(prompt):
        prompts.append(prompt)
        if "grammar_errors" in prompt:
            payload = {
                "grammar_errors": [
                    {
                        "error_type": "tense",
                        "original": "said",
                        "corrected": "say",
                        "explanation": "Use the present tense",
                    }
                ]
            }
        else:
            payload = {
                "pronunciation_issues": [
                    {
                        "phoneme": "/s/",
                        "issue_description": "Check the sound",
                        "correction_technique": "Practice slowly",
                    }
                ]
            }
        return SimpleNamespace(text=json.dumps(payload))

    analysis_api.ai_correction_service.model = SimpleNamespace(
        generate_content=generate
    )

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=analysis_api.app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/pronunciation/ai-analyze",
                data={
                    "reference_text": "Reference words",
                    "current_scenario": "restaurant_ordering-beginner",
                },
                files={"audio": ("input.wav", wav_audio(), "audio/wav")},
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["recognized_text"] == "I said these words"
            assert data["grammar_corrections"][0]["type"] == "tense"
            assert data["pronunciation_feedback"][0]["explanation"] == "Check the sound"
            assert data["next_scenario"] == "daily_conversation-intermediate"
            scenario, difficulty = data["next_scenario"].rsplit("-", 1)
            assert (
                await client.get(f"/api/scenarios/{scenario}/{difficulty}")
            ).status_code == 200

    asyncio.run(request())
    assert len(prompts) == 2
    assert not list(analysis_api.temp_dir.iterdir())


@pytest.mark.parametrize("scenario", ["invalid-beginner", "daily_conversation-invalid"])
def test_invalid_scenario_is_rejected_before_creating_upload(analysis_api, scenario):
    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=analysis_api.app), base_url="http://test"
        ) as client:
            return await client.post(
                "/api/pronunciation/ai-analyze",
                data={"reference_text": "Reference", "current_scenario": scenario},
                files={"audio": ("input.wav", wav_audio(), "audio/wav")},
            )

    assert asyncio.run(request()).status_code == 422
    analysis_api.asr.recognize_file.assert_not_called()
    assert not list(analysis_api.temp_dir.iterdir())


@pytest.mark.parametrize("route", ["analyze", "ai-analyze"])
@pytest.mark.parametrize("failure", ["asr", "assessment"])
def test_failed_analysis_is_explanatory_and_cleans_its_upload(
    analysis_api, monkeypatch, route, failure
):
    sentinel = analysis_api.temp_dir / "existing.wav"
    sentinel.write_bytes(wav_audio())
    if failure == "asr":
        analysis_api.asr.recognize_file.side_effect = RuntimeError("ASR unavailable")
    else:
        monkeypatch.setattr(
            analysis_api.correction_service.corrector,
            "assess",
            Mock(side_effect=AssessmentError("Assessment unavailable")),
        )

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=analysis_api.app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/pronunciation/{route}",
                data={"reference_text": "Never use me as the transcript"},
                files={"audio": ("input.wav", wav_audio(), "audio/wav")},
            )
            assert response.status_code == 500
            assert "unavailable" in response.json()["detail"]
            assert "base_report" not in response.json()["detail"]

    asyncio.run(request())
    assert list(analysis_api.temp_dir.iterdir()) == [sentinel]


@pytest.mark.parametrize("route", ["analyze", "ai-analyze"])
def test_upload_read_failure_cleans_a_partial_file(analysis_api, monkeypatch, route):
    async def fail_read(self, size=-1):
        raise OSError("Upload read unavailable")

    monkeypatch.setattr("starlette.datastructures.UploadFile.read", fail_read)

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=analysis_api.app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/pronunciation/{route}",
                data={"reference_text": "Reference"},
                files={"audio": ("input.wav", wav_audio(), "audio/wav")},
            )
            assert response.status_code == 500
            assert "Upload read unavailable" in response.json()["detail"]

    asyncio.run(request())
    assert not list(analysis_api.temp_dir.iterdir())
    analysis_api.asr.recognize_file.assert_not_called()


@pytest.mark.parametrize("route", ["analyze", "ai-analyze"])
def test_cancelled_request_keeps_upload_until_worker_finishes(analysis_api, route):
    started, release = threading.Event(), threading.Event()
    paths, survived = [], []

    def recognize(path):
        paths.append(Path(path))
        started.set()
        if not release.wait(3):
            raise TimeoutError("ASR release timed out")
        survived.append(Path(path).read_bytes() == wav_audio())
        return "I said these words"

    analysis_api.asr.recognize_file.side_effect = recognize

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=analysis_api.app), base_url="http://test"
        ) as client:
            pending = asyncio.create_task(
                client.post(
                    f"/api/pronunciation/{route}",
                    data={"reference_text": "Reference"},
                    files={"audio": ("input.wav", wav_audio(), "audio/wav")},
                )
            )
            try:
                assert await asyncio.to_thread(started.wait, 2)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                assert paths[0].exists(), "Cancellation removed the active worker input"
            finally:
                release.set()
                # Workers continue after cancellation. Let the owned analysis task finish.
                for _ in range(200):
                    if paths and not paths[0].exists():
                        break
                    await asyncio.sleep(0.01)

    asyncio.run(request())
    assert survived == [True]
    assert not list(analysis_api.temp_dir.iterdir())


@pytest.mark.parametrize("text", ["", "   ", None])
def test_empty_recognition_does_not_return_a_successful_score(analysis_api, text):
    analysis_api.asr.recognize_file.return_value = text
    path = analysis_api.temp_dir / "caller.wav"
    path.write_bytes(wav_audio())
    with pytest.raises(AssessmentError, match="识别"):
        analysis_api.correction_service.comprehensive_correction(str(path), "Reference")


@pytest.mark.parametrize("route", ["analyze", "ai-analyze"])
def test_slow_analysis_does_not_block_health(analysis_api, route):
    started, release, expired = threading.Event(), threading.Event(), threading.Event()
    threads = []
    loop_thread = threading.get_ident()

    def recognize(path):
        threads.append(threading.get_ident())
        started.set()
        if not release.wait(1):
            expired.set()
            raise TimeoutError("Health request could not release ASR")
        assert Path(path).read_bytes() == wav_audio()
        return "I said these words"

    analysis_api.asr.recognize_file.side_effect = recognize

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=analysis_api.app), base_url="http://test"
        ) as client:
            pending = asyncio.create_task(
                client.post(
                    f"/api/pronunciation/{route}",
                    data={"reference_text": "Reference"},
                    files={"audio": ("input.wav", wav_audio(), "audio/wav")},
                )
            )
            try:
                assert await asyncio.to_thread(started.wait, 2)
                assert not expired.is_set(), "Analysis blocked the API loop"
                assert (
                    await asyncio.wait_for(client.get("/health"), 0.5)
                ).status_code == 200
                assert not pending.done()
                release.set()
                response = await asyncio.wait_for(pending, 2)
                assert response.status_code == 200, response.text
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(request())
    assert threads and all(thread != loop_thread for thread in threads)
    assert not list(analysis_api.temp_dir.iterdir())


@pytest.mark.parametrize("route", ["analyze", "ai-analyze"])
def test_concurrent_uploads_are_unique_and_cleaned_independently(analysis_api, route):
    first_started, second_finished, release = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    seen = []

    def recognize(path):
        content = Path(path).read_bytes()
        seen.append(path)
        if content == wav_audio(1200):
            first_started.set()
            if not release.wait(2):
                raise TimeoutError("Second upload could not complete")
            assert Path(path).read_bytes() == content
            raise RuntimeError("First ASR unavailable")
        assert content == wav_audio(2400)
        second_finished.set()
        return "Second audio words"

    analysis_api.asr.recognize_file.side_effect = recognize

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=analysis_api.app), base_url="http://test"
        ) as client:

            async def post(value):
                return await client.post(
                    f"/api/pronunciation/{route}",
                    data={"reference_text": "Reference"},
                    files={"audio": ("input.wav", wav_audio(value), "audio/wav")},
                )

            first = asyncio.create_task(post(1200))
            try:
                assert await asyncio.to_thread(first_started.wait, 3)
                second = await asyncio.wait_for(post(2400), 1)
                assert second_finished.is_set()
                assert second.status_code == 200, second.text
                assert second.json()["recognized_text"] == "Second audio words"
                assert Path(seen[0]).exists()
                assert not Path(seen[1]).exists()
                release.set()
                assert (await first).status_code == 500
            finally:
                release.set()
                await asyncio.gather(first, return_exceptions=True)

    asyncio.run(request())
    assert len(seen) == len(set(seen)) == 2
    assert not list(analysis_api.temp_dir.iterdir())
