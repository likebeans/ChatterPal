"""LLM interfaces and providers without eager SDK imports."""

from importlib import import_module

_EXPORTS = {
    "LLMBase": ".base",
    "LLMError": ".base",
    "AlibabaDashScopeLLM": ".alibaba",
    "OpenAILLM": ".openai",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(_EXPORTS[name], __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
