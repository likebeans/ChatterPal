"""Strict provider JSON with optional complete Markdown code-fence wrapping."""

import json
import re

_CODE_FENCE = re.compile(
    r"```(?:json)?[ \t]*\r?\n(.*)\r?\n```", re.DOTALL | re.IGNORECASE
)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"Nonfinite JSON number: {value}")


def load_provider_json(raw: str) -> object:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 32000:
        raise ValueError("The provider response is missing or too large")
    # Strip only a single complete wrapper, never extract JSON from prose.
    wrapped = _CODE_FENCE.fullmatch(raw.strip())
    content = wrapped.group(1) if wrapped else raw
    return json.loads(
        content, object_pairs_hook=_unique_object, parse_constant=_invalid_constant
    )
