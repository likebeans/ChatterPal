"""Immutable synthesis parameters shared by providers and cache identity."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Tuple, Union


def freeze_parameters(value: Any) -> Any:
    """Keep nested request parameters deterministic and independent of callers."""
    if isinstance(value, Enum):
        return freeze_parameters(value.value)
    if isinstance(value, Mapping):
        return tuple(
            sorted((key, freeze_parameters(item)) for key, item in value.items())
        )
    if isinstance(value, (tuple, list)):
        return tuple(freeze_parameters(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((freeze_parameters(item) for item in value), key=repr))
    if value is None or isinstance(value, (str, bytes, int, float, bool)):
        return value
    raise TypeError(f"Unsupported synthesis parameter type: {type(value).__name__}")


@dataclass(frozen=True)
class SynthesisOptions:
    """Effective parameters for a single request; never mutate provider defaults."""

    provider: str
    model: str = ""
    voice: str = ""
    rate: Union[str, float] = 1.0
    volume: Union[str, int] = 50
    pitch: Union[str, float] = 1.0
    format: str = "wav"
    sample_rate: int = 16000
    extra: Tuple[Tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "extra", freeze_parameters(self.extra))
