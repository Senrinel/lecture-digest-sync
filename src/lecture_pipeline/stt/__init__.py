"""STT Channel registry and factory."""

from typing import Dict, Type
from lecture_pipeline.stt.base import STTChannelBase
from lecture_pipeline.stt.c1_gemini_flash import GeminiFlashSTTChannel
from lecture_pipeline.stt.c2_gemini_transcribe import GeminiTranscribeSTTChannel
from lecture_pipeline.stt.c_faster_whisper import (
    ChannelC4Breeze,
    ChannelC6WhisperLargeV3,
    ChannelC7WhisperTurbo,
)

_REGISTRY: Dict[str, Type[STTChannelBase]] = {
    "C1": GeminiFlashSTTChannel,
    "C2": GeminiTranscribeSTTChannel,
    "C4": ChannelC4Breeze,
    "C6": ChannelC6WhisperLargeV3,
    "C7": ChannelC7WhisperTurbo,
}


def get_stt_channel(channel_id: str, **kwargs) -> STTChannelBase:
    """Instantiates an STT channel by ID."""
    ch_id = channel_id.upper()
    if ch_id not in _REGISTRY:
        raise ValueError(f"Unknown STT channel: '{channel_id}'. Available: {list(_REGISTRY.keys())}")
    return _REGISTRY[ch_id](**kwargs)


def register_channel(channel_id: str, channel_cls: Type[STTChannelBase]):
    """Registers a new STT channel (e.g. local backends)."""
    _REGISTRY[channel_id.upper()] = channel_cls
