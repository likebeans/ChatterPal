"""Scheduling decisions from observed review attempts, without an ability score."""

from datetime import datetime, timedelta, timezone


def utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Review clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def timestamp(value: str) -> datetime:
    return utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def next_due(now: datetime, independent_successes: int) -> str:
    # These are practice intervals, not a claim about retention or mastery.
    days = 1 if independent_successes == 0 else 3 if independent_successes == 1 else 7
    return (utc(now) + timedelta(days=days)).isoformat()


def scheduling_evidence(
    item: dict, evaluation: dict, now: datetime, *, source_repeat: bool = False
) -> dict:
    assistance = [
        event for event in item.get("assistance", []) if event["cycle"] == item["cycle"]
    ]
    revealed = any(event["kind"] == "reveal" for event in assistance)
    used_hint = bool(assistance)
    delayed = utc(now) >= timestamp(item["due_at"])
    fresh_variant = item["cycle"] < len(item["task_snapshot"]["transfer_questions"])
    independent = delayed and not used_hint and not source_repeat and fresh_variant
    advanced = (
        independent
        and evaluation["status"] == "available"
        and evaluation["result"] == "met"
    )
    # An assessed response has used this authored question, even if it was early,
    # assisted or unsuccessful. A technical failure leaves the question available.
    consumed = fresh_variant and evaluation["status"] != "unavailable"
    if not fresh_variant:
        kind = "repeated_prompt"
    elif revealed:
        kind = "shown_answer"
    elif used_hint:
        kind = "hinted"
    elif not delayed:
        kind = "early_practice"
    elif source_repeat:
        kind = "source_repeat"
    else:
        kind = "delayed_transfer"
    return {
        "used_hint": used_hint,
        "revealed_answer": revealed,
        "independent": independent,
        "delayed": delayed,
        "schedule_advanced": advanced,
        "variant_consumed": consumed,
        "evidence_kind": kind,
    }
