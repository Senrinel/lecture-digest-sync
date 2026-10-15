"""Channel C4: Breeze-ASR-25 (Local GPU / faster-whisper).

MediaTek Research fine-tuned Whisper model specifically optimized for Taiwanese Mandarin,
local colloquial speech patterns, and Taiwanese accent nuances.
Pre-converted CTranslate2 model: 'phate334/Breeze-ASR-25-ct2'.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from lecture_pipeline.stt.faster_whisper_base import FasterWhisperChannelBase


class BreezeASRChannel(FasterWhisperChannelBase):
    """Channel C4 implementation using MediaTek Breeze-ASR-25 via faster-whisper."""

    def __init__(
        self,
        model_name: str = "phate334/Breeze-ASR-25-ct2",
        device: str = "cuda",
        compute_type: str = "int8_float16",
        beam_size: int = 5,
        language: str = "zh",
        opencc_config: str = "s2twp",
        backend_python: Optional[Path | str] = None,
    ):
        super().__init__(
            model_name=model_name,
            channel_id="C4",
            display_name="Breeze-ASR-25 (C4)",
            device=device,
            compute_type=compute_type,
            beam_size=beam_size,
            language=language,
            opencc_config=opencc_config,
            backend_python=backend_python,
        )
