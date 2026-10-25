"""Authored tasks must be usable without a model, database, or old AI service."""

import json
from importlib.resources import files

import pytest

from chatterpal.learning.catalog import TaskCatalog


def test_catalog_contains_three_complete_tasks_with_separate_practice_paths():
    catalog = TaskCatalog()
    tasks = catalog.list_tasks()
    assert {task.task_id for task in tasks} == {
        "weekend",
        "restaurant-order",
        "hotel-check-in",
    }
    for task in tasks:
        assert task.content_version == "1"
        assert task.estimated_minutes == 8
        assert task.learner_role and task.coach_role
        assert len(task.examples) == 2
        assert task.objectives and task.required_information
        assert task.target_expressions and task.criteria
        assert len(task.steps) == len(task.criteria)
        assert {step.criterion_id for step in task.steps} == {
            criterion.criterion_id for criterion in task.criteria
        }
        assert all(
            step.question and step.simpler_question and step.sentence_starter
            for step in task.steps
        )
        assert task.read_aloud.target_text
        assert task.free_response.prompt
        assert task.free_response.required_information
        assert len(task.transfer_questions) >= 2
        assert all(question.criterion_ids for question in task.transfer_questions)
        assert catalog.get_task(task.task_id, "1") == task


def test_weekend_reuses_the_existing_intermediate_material():
    task = TaskCatalog().get_task("weekend")
    assert task.opening == "What did you do over the weekend?"
    assert task.examples[0].startswith(
        "I went hiking with some friends. It was really enjoyable."
    )
    assert task.source_reference == (
        "services/ai_correction.py:daily_conversation/intermediate"
    )
    assert {criterion.criterion_id for criterion in task.criteria} == {
        "activity",
        "company",
        "feeling",
    }


@pytest.mark.parametrize("task_id", ["weekend", "restaurant-order", "hotel-check-in"])
def test_content_snapshot_can_round_trip_without_sharing_mutable_content(task_id):
    task = TaskCatalog().get_task(task_id)
    snapshot = json.loads(json.dumps(task.to_snapshot(), ensure_ascii=False))
    snapshot["examples"].clear()
    snapshot["steps"][0]["question"] = "Injected question"
    assert len(task.examples) == 2
    assert task.steps[0].question != "Injected question"
    assert TaskCatalog().get_task(task_id).to_snapshot() == task.to_snapshot()


def test_unknown_or_unavailable_task_version_is_an_explicit_error():
    catalog = TaskCatalog()
    with pytest.raises(KeyError):
        catalog.get_task("not-a-task")
    with pytest.raises(KeyError):
        catalog.get_task("weekend", "999")


def test_content_files_are_available_from_the_installed_package():
    content = files("chatterpal").joinpath("learning", "content")
    for filename in ("weekend.json", "restaurant-order.json", "hotel-check-in.json"):
        task = json.loads(content.joinpath(filename).read_text(encoding="utf-8"))
        assert task["task_id"] == filename.removesuffix(".json")
        assert task["content_version"] == "1"
