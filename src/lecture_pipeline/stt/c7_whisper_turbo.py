"""Channel C7: Whisper Large-v3-Turbo (Local GPU / faster-whisper).

High-speed, memory-efficient Whisper Turbo model running on CUDA int8_float16.
Typically completes a 10-minute lecture chunk in ~18 seconds on an RTX 4060 (RTF ~0.03).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from lecture_pipeline.stt.faster_whisper_base import FasterWhisperChannelBase


class WhisperTurboChannel(FasterWhisperChannelBase):
    """Channel C7 implementation using Whisper Large-v3-Turbo via faster-whisper."""

    def __init__(
        self,
        model_name: str = "turbo",
        device: str = "cuda",
        compute_type: str = "int8_float16",
        beam_size: int = 5,
        language: str = "zh",
        opencc_config: str = "s2twp",
        backend_python: Optional[Path | str] = None,
    ):
        super().__init__(
            model_name=model_name,
            channel_id="C7",
            display_name="Whisper Large-v3-Turbo (C7)",
            device=device,
            compute_type=compute_type,
            beam_size=beam_size,
            language=language,
            opencc_config=opencc_config,
            backend_python=backend_python,
        )
