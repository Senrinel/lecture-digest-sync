"""STT Channels package and factory supporting all 8 parallel channels (C1-C8)."""

from typing import Any
from lecture_pipeline.stt.base import (
    BaseSTTChannel,
    ChunkTranscription,
    FullTranscription,
    save_stt_outputs,
)
from lecture_pipeline.stt.c1_gemini_flash import GeminiFlashChannel
from lecture_pipeline.stt.c2_gemini_transcribe import GeminiTranscribeChannel
from lecture_pipeline.stt.c3_qwen_asr import QwenASRChannel
from lecture_pipeline.stt.c4_breeze_asr import BreezeASRChannel
from lecture_pipeline.stt.c5_firered_asr import FireRedASRChannel
from lecture_pipeline.stt.c6_whisper_large import WhisperLargeChannel
from lecture_pipeline.stt.c7_whisper_turbo import WhisperTurboChannel
from lecture_pipeline.stt.c8_manual_baseline import ManualBaselineChannel


def get_stt_channel(channel_id: str = "C1", **kwargs: Any) -> BaseSTTChannel:
    """Factory to retrieve an instantiated STT channel.

    Args:
        channel_id: Channel code ('C1' ~ 'C8')
        **kwargs: Channel-specific parameters (api_key, model, device, etc.)

    Returns:
        BaseSTTChannel instance.
    """
    cid = channel_id.strip().upper()
    if cid == "C1":
        return GeminiFlashChannel(**kwargs)
    elif cid == "C2":
        return GeminiTranscribeChannel(**kwargs)
    elif cid == "C3":
        return QwenASRChannel(**kwargs)
    elif cid == "C4":
        return BreezeASRChannel(**kwargs)
    elif cid == "C5":
        return FireRedASRChannel(**kwargs)
    elif cid == "C6":
        return WhisperLargeChannel(**kwargs)
    elif cid == "C7":
        return WhisperTurboChannel(**kwargs)
    elif cid == "C8":
        return ManualBaselineChannel(**kwargs)
    else:
        raise ValueError(
            f"未知的 STT 通道：'{channel_id}'。目前支援全數 8 通道：\n"
            f"  - 雲端: 'C1' (Gemini Flash), 'C2' (Gemini 3.5 Transcribe)\n"
            f"  - 本地: 'C3' (Qwen3-ASR), 'C4' (Breeze-ASR-25), 'C5' (FireRedASR), 'C6' (Whisper Large-v3), 'C7' (Whisper Turbo)\n"
            f"  - 基準: 'C8' (Manual Human Baseline)"
        )


__all__ = [
    "BaseSTTChannel",
    "BreezeASRChannel",
    "ChunkTranscription",
    "FireRedASRChannel",
    "FullTranscription",
    "GeminiFlashChannel",
    "GeminiTranscribeChannel",
    "ManualBaselineChannel",
    "QwenASRChannel",
    "WhisperLargeChannel",
    "WhisperTurboChannel",
    "get_stt_channel",
    "save_stt_outputs",
]
