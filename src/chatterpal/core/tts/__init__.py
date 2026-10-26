# -*- coding: utf-8 -*-
"""
语音合成(TTS)模块
提供统一的语音合成接口，支持多种TTS引擎
"""

from .base import (
    AudioOutputInfo,
    TTSBase,
    TTSError,
    get_audio_output_info,
    prepare_audio_output,
)
from .edge import EdgeTTS
from .options import SynthesisOptions

__all__ = [
    "AudioOutputInfo",
    "TTSBase",
    "TTSError",
    "EdgeTTS",
    "SynthesisOptions",
    "get_audio_output_info",
    "prepare_audio_output",
]
