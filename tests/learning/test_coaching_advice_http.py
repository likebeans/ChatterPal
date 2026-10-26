"""Immediate advice degradation preserves real HTTP turns and saved history."""

import json

import pytest

from tests.learning.test_int2_weekend_flow import (
    PREFIX,
    application as application_fixture,
    browser,
    succeeded,
    turn,
)

application = application_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,claim",
    [
        ("explanation", "你的发音错误，准确率95"),
        ("corrected", "Your pronunciation score is 95%."),
    ],
)
async def test_invalid_immediate_advice_is_not_saved_or_replayed(
    application, monkeypatch, field, claim
):
    text = "I go hiking last weekend."
    reply = "Who did you go with?"
    suggestion = {
        "original": text,
        "corrected": "I went hiking last weekend.",
        "explanation": "Use the past tense for last weekend.",
        "category": "grammar",
    }
    suggestion[field] = claim
    calls = []

    def respond(messages):
        calls.append(messages)
        return json.dumps({"reply": reply, "suggestions": [suggestion]})

    monkeypatch.setattr(application.llm, "chat", respond)
    server = application.create()
    async with browser(server) as client:
        assert (await client.post(f"{PREFIX}/bootstrap")).status_code == 200
        session = await succeeded(
            client,
            await client.post(
                f"{PREFIX}/sessions",
                json={
                    "request_id": "advice-session-start",
                    "task_id": "weekend",
                    "content_version": "1",
                    "correction_mode": "immediate",
                },
            ),
        )
        updated = await turn(client, session, text, "invalid-advice-turn")
        assert updated["revision"] == session["revision"] + 1
        assert updated["turns"][-2]["text"] == text
        assert updated["turns"][-1]["text"] == reply
        assert updated["coaching"][-1]["status"] == "unavailable"
        assert updated["coaching"][-1]["suggestions"] == []
        assert claim not in json.dumps(updated, ensure_ascii=False)
        assert len(calls) == 1
        assert application.asr.calls == []
        assert application.tts.calls == [reply]

        saved = await client.get(f"{PREFIX}/sessions/{session['session_id']}")
        assert saved.status_code == 200
        assert saved.json() == updated
        replay = await succeeded(
            client,
            await client.post(
                f"{PREFIX}/sessions/{session['session_id']}/turns",
                json={
                    "request_id": "invalid-advice-turn",
                    "expected_revision": session["revision"],
                    "text": text,
                },
            ),
        )
        assert replay == updated
        assert len(calls) == 1
        assert application.tts.calls == [reply]
        token = client.cookies.get("chatterpal_learner")

    async with browser(application.create(), token=token) as client:
        restored = await client.get(f"{PREFIX}/sessions/{session['session_id']}")
        assert restored.status_code == 200
        assert restored.json() == updated
        assert claim not in json.dumps(restored.json(), ensure_ascii=False)
        assert len(calls) == 1
