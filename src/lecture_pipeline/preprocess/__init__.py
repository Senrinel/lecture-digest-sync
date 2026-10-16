"""Modular Audio Preprocessing Package for Lecture Pipeline (Section 3).

Provides:
- FFmpeg DSP filter chains (hp, loud, dyn, nr)
- Deep learning neural denoiser (DeepFilterNet3)
- Spatial dereverberation (NARA-WPE)
- Speech enhancement (SE)
- Silero VAD silence trimming (vadtrim)
- Dual format outputs (16kHz mono WAV + FLAC)
- Multi-part recording concatenation
"""

from __future__ import annotations

from lecture_pipeline.state import PreprocessOptions
from lecture_pipeline.preprocess.dsp import (
    build_ffmpeg_audio_filters,
    convert_wav_to_flac,
    run_ffmpeg_dsp,
)
from lecture_pipeline.preprocess.wpe_dereverb import apply_wpe_dereverb
from lecture_pipeline.preprocess.dfn_denoise import apply_dfn_denoise
from lecture_pipeline.preprocess.se_enhance import apply_speech_enhancement
from lecture_pipeline.preprocess.vad_trim import apply_vad_trim
from lecture_pipeline.preprocess.pipeline import preprocess_audio

__all__ = [
    "PreprocessOptions",
    "preprocess_audio",
    "build_ffmpeg_audio_filters",
    "convert_wav_to_flac",
    "run_ffmpeg_dsp",
    "apply_wpe_dereverb",
    "apply_dfn_denoise",
    "apply_speech_enhancement",
    "apply_vad_trim",
]
