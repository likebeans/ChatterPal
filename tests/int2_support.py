"""Deterministic provider boundaries for real INT2 HTTP and browser acceptance."""

import json
import threading


class FixtureASR:
    def __init__(self):
        self.calls = []
        self.text = "I was with my friends."

    def recognize(self, content):
        self.calls.append(content)
        return self.text


class FixtureTTS:
    def __init__(self, content):
        self.content = content
        self.calls = []
        self.fail = False

    def synthesize(self, text):
        self.calls.append(text)
        if self.fail:
            raise RuntimeError("Controlled synthesis outage")
        return self.content


class FixtureLLM:
    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.block_coach = False
        self.fail_feedback = False
        self.invalid_feedback = False

    def chat(self, messages):
        self.calls.append(messages)
        instruction = messages[0]["content"]
        if instruction.startswith("INT2_FEEDBACK"):
            if self.fail_feedback:
                raise RuntimeError("Controlled feedback outage")
            if self.invalid_feedback:
                return '{"strength": null, "focuses": [], "criteria": "invalid"}'
            turns = json.loads(messages[-1]["content"])["learner_turns"]
            first, last = turns[0], turns[-1]
            return json.dumps(
                {
                    "strength": {
                        "source_turn_id": last["source_turn_id"],
                        "quote": last["text"],
                        "explanation": "这句提供了自己的周末经历。",
                    },
                    "focuses": [
                        {
                            "source_turn_id": first["source_turn_id"],
                            "original": first["text"],
                            "suggested": "I went hiking on Saturday.",
                            "explanation": "过去的周末活动可以用 went 表达。",
                            "category": "grammar",
                        }
                    ],
                    "criteria": [
                        {
                            "criterion_id": criterion,
                            "result": "met",
                            "evidence": turns[min(index, len(turns) - 1)]["text"],
                        }
                        for index, criterion in enumerate(
                            ("activity", "company", "feeling")
                        )
                    ],
                },
                ensure_ascii=False,
            )
        if instruction.startswith("INT2_EVALUATE_FREE_RESPONSE"):
            attempt = json.loads(messages[-1]["content"])
            return json.dumps(
                {
                    "result": "met",
                    "reason": "这次表达提供了可核对的周末信息。",
                    "evidence": attempt["transcript"],
                },
                ensure_ascii=False,
            )
        if self.block_coach:
            self.started.set()
            if not self.release.wait(5):
                raise TimeoutError("Controlled coach was not released")
        return json.dumps(
            {
                "reply": "Who were you with, and how did you feel?",
                "suggestions": [],
            }
        )
