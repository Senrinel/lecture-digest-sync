"""Audio preprocessing module with modular toggle switches.

Executes FFmpeg filter chains tailored for SONY ICD-PX470 desk recordings.
Converts any incoming audio format to standard 16kHz mono 16-bit PCM WAV.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional
from rich.console import Console

from lecture_pipeline.state import PreprocessOptions

console = Console()


def build_ffmpeg_audio_filters(opts: PreprocessOptions) -> List[str]:
    """Construct a list of FFmpeg audio filters based on enabled options.

    Filters are ordered in an optimal DSP sequence:
    1. Highpass filter (remove sub-bass / desk rumble)
    2. Dynamic range leveling (if enabled)
    3. Spectral noise reduction (if enabled)
    4. Loudness normalization (EBU R128)
    5. VAD trim (leading silence removal, if enabled)
    """
    filters: List[str] = []

    # 1. High-pass filter (Desk rumble, AC hum)
    if opts.hp == "80Hz":
        filters.append("highpass=f=80")
    elif opts.hp == "100Hz":
        filters.append("highpass=f=100")
    elif opts.hp == "120Hz":
        filters.append("highpass=f=120")

    # 2. Dynamic Audio Normalizer (Lecturer distance compensation)
    if opts.dyn == "light":
        filters.append("dynaudnorm=f=500:g=15:p=0.95")
    elif opts.dyn == "standard":
        filters.append("dynaudnorm=f=1000:g=31:p=0.95")

    # 3. Spectral Noise Reduction (FFmpeg afftdn)
    if opts.nr == "6dB":
        filters.append("afftdn=nr=6:nf=-25")
    elif opts.nr == "12dB":
        filters.append("afftdn=nr=12:nf=-25")
    elif opts.nr == "18dB":
        filters.append("afftdn=nr=18:nf=-25")

    # 4. Loudness Normalization (EBU R128)
    if opts.loud == "-16LUFS":
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    elif opts.loud == "-19LUFS":
        filters.append("loudnorm=I=-19:TP=-1.5:LRA=11")
    elif opts.loud == "-23LUFS":
        filters.append("loudnorm=I=-23:TP=-1.5:LRA=11")

    # 5. Trim leading silence (vadtrim)
    if opts.vadtrim == "on":
        filters.append("silenceremove=start_periods=1:start_duration=1:start_threshold=-45dB")

    return filters


def preprocess_audio(
    input_path: Path | str,
    output_path: Path | str,
    opts: Optional[PreprocessOptions] = None,
    ffmpeg_bin: str = "ffmpeg",
) -> Path:
    """Preprocess audio using FFmpeg with the requested filter chain.

    Standardizes audio to 16,000 Hz, mono channel, 16-bit PCM WAV.

    Args:
        input_path: Input audio file.
        output_path: Target preprocessed WAV file.
        opts: Preprocessing options. If None, default settings are used.
        ffmpeg_bin: Name or path of the ffmpeg binary.

    Returns:
        Path to output file.

    Raises:
        FileNotFoundError: If input file or ffmpeg binary not found.
        RuntimeError: If FFmpeg execution fails.
    """
    in_file = Path(input_path).resolve()
    out_file = Path(output_path).resolve()

    if not in_file.is_file():
        raise FileNotFoundError(f"找不到輸入音檔：{in_file}")

    out_file.parent.mkdir(parents=True, exist_ok=True)

    if opts is None:
        opts = PreprocessOptions()

    filters = build_ffmpeg_audio_filters(opts)
    cmd = [
        ffmpeg_bin,
        "-y",  # Overwrite output
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(in_file),
        "-ar",
        "16000",  # Standard 16kHz
        "-ac",
        "1",  # Mono
        "-c:a",
        "pcm_s16le",  # 16-bit PCM
    ]

    if filters:
        filter_str = ",".join(filters)
        cmd.extend(["-af", filter_str])

    cmd.append(str(out_file))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.strip() if e.stderr else str(e)
        raise RuntimeError(f"FFmpeg 前處理執行失敗: {err_msg}") from e
    except FileNotFoundError as e:
        raise FileNotFoundError(f"找不到 FFmpeg 執行檔 '{ffmpeg_bin}'，請確認是否安裝於系統 PATH 中。") from e

    if not out_file.is_file() or out_file.stat().st_size == 0:
        raise RuntimeError(f"FFmpeg 輸出檔案為空或未生成: {out_file}")

    return out_file
