"""FFmpeg DSP Filter and Audio Decoding Engine.

Implements modular DSP audio filters tailored for SONY ICD-PX470 desk recordings:
- hp: Highpass filter (80Hz, 100Hz, 120Hz)
- loud: Loudness normalization (EBU R128: -16LUFS, -19LUFS, -23LUFS)
- dyn: Dynamic range leveling (dynaudnorm: light, standard)
- nr: Spectral noise reduction (afftdn: 6dB, 12dB, 18dB)
- vadtrim: Leading/long silence removal via silenceremove
- multi_concat: FFmpeg filter_complex concat for multi-part recordings
- Dual format export: 16kHz mono 16-bit PCM WAV (local) and FLAC (cloud)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional, Sequence
from rich.console import Console

from lecture_pipeline.state import PreprocessOptions

console = Console()


def build_ffmpeg_audio_filters(opts: PreprocessOptions) -> List[str]:
    """Construct a list of FFmpeg audio filters based on enabled DSP options.

    Filters are ordered in an optimal DSP sequence:
    1. Highpass filter (remove sub-bass / desk rumble)
    2. Dynamic range leveling (compensates distance variation)
    3. Spectral noise reduction (steady background hiss)
    4. Loudness normalization (EBU R128)
    5. VAD trim (leading silence removal, if enabled via FFmpeg)
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

    # 5. Trim leading silence (vadtrim via FFmpeg if neural vad is not used)
    if opts.vadtrim == "on":
        filters.append("silenceremove=start_periods=1:start_duration=1:start_threshold=-45dB")

    return filters


def run_ffmpeg_dsp(
    input_files: Sequence[Path],
    output_wav: Path,
    opts: PreprocessOptions,
    ffmpeg_bin: str = "ffmpeg",
) -> Path:
    """Execute FFmpeg audio decoding, multi-part concat, and DSP filter chain."""
    output_wav = Path(output_wav).resolve()
    output_wav.parent.mkdir(parents=True, exist_ok=True)

    filters = build_ffmpeg_audio_filters(opts)
    filter_str = ",".join(filters) if filters else None

    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    for f in input_files:
        cmd.extend(["-i", str(f)])

    if len(input_files) > 1:
        concat_inputs = "".join(f"[{i}:a]" for i in range(len(input_files)))
        if filter_str:
            complex_filter = f"{concat_inputs}concat=n={len(input_files)}:v=0:a=1[cat];[cat]{filter_str}[out]"
        else:
            complex_filter = f"{concat_inputs}concat=n={len(input_files)}:v=0:a=1[out]"
        cmd.extend(["-filter_complex", complex_filter, "-map", "[out]"])
    else:
        if filter_str:
            cmd.extend(["-af", filter_str])

    cmd.extend([
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(output_wav),
    ])

    try:
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.strip() if e.stderr else str(e)
        raise RuntimeError(f"FFmpeg 前處理執行失敗: {err_msg}") from e
    except FileNotFoundError as e:
        raise FileNotFoundError(f"找不到 FFmpeg 執行檔 '{ffmpeg_bin}'，請確認是否安裝於系統 PATH 中。") from e

    if not output_wav.is_file() or output_wav.stat().st_size == 0:
        raise RuntimeError(f"FFmpeg 輸出檔案為空或未生成: {output_wav}")

    return output_wav


def convert_wav_to_flac(
    wav_path: Path | str,
    flac_path: Optional[Path | str] = None,
    ffmpeg_bin: str = "ffmpeg",
) -> Path:
    """Convert normalized 16kHz WAV to FLAC format for bandwidth-saving cloud API upload."""
    wav_p = Path(wav_path).resolve()
    if flac_path is None:
        flac_p = wav_p.with_suffix(".flac")
    else:
        flac_p = Path(flac_path).resolve()

    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i", str(wav_p),
        "-c:a", "flac",
        str(flac_p),
    ]

    try:
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except Exception as e:
        console.print(f"[yellow]⚠️ FLAC 轉換略過或失敗 ({e})，雲端將沿用 WAV。[/yellow]")
        return wav_p

    return flac_p
