"""Audio Preprocessing Pipeline Orchestrator for Phase 1 / Section 3.

Coordinates:
1. Multi-part concatenation (if lecture was split by recorder)
2. DSP filter chain (hp, loud, dyn, nr) via FFmpeg
3. Neural enhancements (wpe, dfn, se, vadtrim) via backends/enhance
4. Dual format export: 16kHz mono WAV (for local models) and FLAC (for cloud APIs)
5. Preservation of raw source audio files
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Optional, Sequence, Union
from rich.console import Console

from lecture_pipeline.state import PreprocessOptions
from lecture_pipeline.preprocess.dsp import (
    convert_wav_to_flac,
    run_ffmpeg_dsp,
)
from lecture_pipeline.preprocess.wpe_dereverb import apply_wpe_dereverb
from lecture_pipeline.preprocess.dfn_denoise import apply_dfn_denoise
from lecture_pipeline.preprocess.se_enhance import apply_speech_enhancement
from lecture_pipeline.preprocess.vad_trim import apply_vad_trim

console = Console()


def preprocess_audio(
    input_path: Union[str, Path, Sequence[str | Path]],
    output_path: str | Path,
    opts: Optional[PreprocessOptions] = None,
    ffmpeg_bin: str = "ffmpeg",
    export_flac: bool = True,
) -> Path:
    """Preprocess audio using modular DSP and optional neural enhancement switches.

    Standardizes audio to 16,000 Hz, mono channel, 16-bit PCM WAV (and optional FLAC).
    Preserves raw audio files in their original locations.

    Args:
        input_path: Single audio file or list of multi-part recording files.
        output_path: Target preprocessed WAV path (e.g. 00_normalized.wav).
        opts: PreprocessOptions instance defining active switches.
        ffmpeg_bin: Name or path to ffmpeg binary.
        export_flac: Whether to generate a companion .flac file for cloud APIs.

    Returns:
        Path to the primary preprocessed WAV file.
    """
    if isinstance(input_path, (list, tuple)):
        in_files = [Path(p).resolve() for p in input_path]
    else:
        in_files = [Path(input_path).resolve()]

    for f in in_files:
        if not f.is_file():
            raise FileNotFoundError(f"找不到輸入音檔：{f}")

    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if opts is None:
        opts = PreprocessOptions()

    has_neural_enhancements = (
        opts.wpe == "on" or
        opts.dfn == "on" or
        opts.se == "on" or
        (opts.vadtrim == "on" and Path("backends/enhance/.venv").is_dir())
    )

    if not has_neural_enhancements:
        # Standard fast DSP path: Run FFmpeg directly
        run_ffmpeg_dsp(in_files, out_file, opts=opts, ffmpeg_bin=ffmpeg_bin)
    else:
        # Multi-stage enhancement path:
        # 1. FFmpeg decode and DSP (excluding vadtrim which will be handled by Silero)
        opts_dsp = opts.model_copy()
        if opts.vadtrim == "on":
            opts_dsp.vadtrim = "off"

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            stage1_wav = tmp_path / "stage1_dsp.wav"
            run_ffmpeg_dsp(in_files, stage1_wav, opts=opts_dsp, ffmpeg_bin=ffmpeg_bin)

            curr_wav = stage1_wav

            # 2. WPE Dereverberation
            if opts.wpe == "on":
                wpe_out = tmp_path / "stage2_wpe.wav"
                console.print("   [dim]⏳ 套用 NARA-WPE 語音去殘響...[/dim]")
                curr_wav = apply_wpe_dereverb(curr_wav, wpe_out)

            # 3. DeepFilterNet3 Neural Denoising
            if opts.dfn == "on":
                dfn_out = tmp_path / "stage3_dfn.wav"
                console.print("   [dim]⏳ 套用 DeepFilterNet3 深度神經降噪 (GPU)...[/dim]")
                curr_wav = apply_dfn_denoise(curr_wav, dfn_out)

            # 4. Speech Enhancement
            if opts.se == "on":
                se_out = tmp_path / "stage4_se.wav"
                console.print("   [dim]⏳ 套用特徵語音增強 (Speech Enhancement)...[/dim]")
                curr_wav = apply_speech_enhancement(curr_wav, se_out)

            # 5. Silero VAD Trim
            if opts.vadtrim == "on":
                vad_out = tmp_path / "stage5_vadtrim.wav"
                console.print("   [dim]⏳ 套用 Silero VAD 長靜音修剪...[/dim]")
                curr_wav = apply_vad_trim(curr_wav, vad_out)

            shutil.copyfile(str(curr_wav), str(out_file))

    # Base step: Generate companion FLAC for cloud upload if requested
    if export_flac:
        flac_out = out_file.with_suffix(".flac")
        convert_wav_to_flac(out_file, flac_out, ffmpeg_bin=ffmpeg_bin)

    if not out_file.is_file() or out_file.stat().st_size == 0:
        raise RuntimeError(f"音訊前處理完成但輸出檔案不存在或為空: {out_file}")

    return out_file
