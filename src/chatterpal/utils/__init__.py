"""Utilities with lazy exports so logging and text do not import audio runtimes."""

from importlib import import_module

_EXPORTS = {
    **dict.fromkeys(
        (
            "AudioProcessor",
            "create_temp_audio_file",
            "cleanup_temp_file",
            "get_audio_duration",
            "convert_audio_format",
        ),
        ".audio",
    ),
    **dict.fromkeys(
        (
            "TextProcessor",
            "remove_filler_words",
            "format_transcript_with_timestamps",
            "extract_pronunciation_targets",
            "generate_pronunciation_feedback",
        ),
        ".text",
    ),
    **dict.fromkeys(
        (
            "ChatterPalLogger",
            "get_logger",
            "setup_logging",
            "LoggerMixin",
            "log_function_call",
            "log_performance",
            "debug",
            "info",
            "warning",
            "error",
            "critical",
            "exception",
        ),
        ".logger",
    ),
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
