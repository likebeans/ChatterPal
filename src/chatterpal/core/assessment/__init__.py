# -*- coding: utf-8 -*-
"""
发音评估模块
提供全面的发音质量评估功能，包括韵律分析、音素分析和发音纠错
"""

from .base import AssessmentBase, AssessmentError, AssessmentResult


def __getattr__(name):
    """Load optional acoustic implementations only when requested."""
    from importlib import import_module

    modules = {"ProsodyAnalyzer": ".prosody", "PhonemeAnalyzer": ".phoneme", "PronunciationCorrector": ".corrector"}
    if name not in modules:
        raise AttributeError(name)
    value = getattr(import_module(modules[name], __name__), name)
    globals()[name] = value
    return value

__all__ = [
    "AssessmentBase",
    "AssessmentError",
    "AssessmentResult",
    "ProsodyAnalyzer",
    "PhonemeAnalyzer",
    "PronunciationCorrector",
]
