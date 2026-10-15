"""Channel C6: Whisper Large-v3 (Local GPU / faster-whisper).

OpenAI's benchmark Whisper Large-v3 model running on CUDA int8_float16 via faster-whisper.
High accuracy, standard transcription baseline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from lecture_pipeline.stt.faster_whisper_base import FasterWhisperChannelBase


class WhisperLargeChannel(FasterWhisperChannelBase):
    """Channel C6 implementation using Whisper Large-v3 via faster-whisper."""

    def __init__(
        self,
        model_name: str = "large-v3",
        device: str = "cuda",
        compute_type: str = "int8_float16",
        beam_size: int = 5,
        language: str = "zh",
        opencc_config: str = "s2twp",
        backend_python: Optional[Path | str] = None,
    ):
        super().__init__(
            model_name=model_name,
            channel_id="C6",
            display_name="Whisper Large-v3 (C6)",
            device=device,
            compute_type=compute_type,
            beam_size=beam_size,
            language=language,
            opencc_config=opencc_config,
            backend_python=backend_python,
        )
