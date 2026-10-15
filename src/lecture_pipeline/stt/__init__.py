"""STT Channels package and factory."""

from typing import Any
from lecture_pipeline.stt.base import (
    BaseSTTChannel,
    ChunkTranscription,
    FullTranscription,
    save_stt_outputs,
)
from lecture_pipeline.stt.c1_gemini_flash import GeminiFlashChannel
from lecture_pipeline.stt.c2_gemini_transcribe import GeminiTranscribeChannel


def get_stt_channel(channel_id: str = "C1", **kwargs: Any) -> BaseSTTChannel:
    """Factory to retrieve an instantiated STT channel.

    Args:
        channel_id: Channel code ('C1', 'C2', etc.)
        **kwargs: Channel-specific parameters (api_key, model, etc.)

    Returns:
        BaseSTTChannel instance.
    """
    cid = channel_id.strip().upper()
    if cid == "C1":
        return GeminiFlashChannel(**kwargs)
    elif cid == "C2":
        return GeminiTranscribeChannel(**kwargs)
    else:
        raise ValueError(
            f"未知的 STT 通道：'{channel_id}'。目前 Phase 2 支援：'C1' (Gemini Flash), 'C2' (Gemini 3.5 Transcribe)。"
        )


__all__ = [
    "BaseSTTChannel",
    "ChunkTranscription",
    "FullTranscription",
    "GeminiFlashChannel",
    "GeminiTranscribeChannel",
    "get_stt_channel",
    "save_stt_outputs",
]
