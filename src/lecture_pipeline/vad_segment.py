"""Smart VAD silence detection and audio segmentation module.

Segments long lectures (2~3 hours) at natural speech pauses (≥0.5s silence)
near target intervals (e.g. 10~15 minutes) without cutting mid-word or mid-sentence.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple
from rich.console import Console

from lecture_pipeline.state import ChunkInfo, SegmentationOptions

console = Console()

# Regex patterns for FFmpeg silencedetect output
SILENCE_START_RE = re.compile(r"silence_start:\s*(?P<start>[-+]?\d*\.?\d+)")
SILENCE_END_RE = re.compile(
    r"silence_end:\s*(?P<end>[-+]?\d*\.?\d+)\s*\|\s*silence_duration:\s*(?P<dur>[-+]?\d*\.?\d+)"
)


def get_audio_duration(audio_path: Path | str, ffprobe_bin: str = "ffprobe") -> float:
    """Retrieve precise audio duration in seconds using ffprobe.

    Args:
        audio_path: Path to audio file.
        ffprobe_bin: Name or path of ffprobe binary.

    Returns:
        Duration in seconds as a float.
    """
    path = Path(audio_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"找不到音訊檔案：{path}")

    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]

    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        return float(res.stdout.strip())
    except Exception as e:
        raise RuntimeError(f"無法讀取音訊長度 ({path.name}): {e}") from e


def detect_silence_intervals(
    audio_path: Path | str,
    silence_thresh_db: int = -40,
    min_silence_sec: float = 0.5,
    ffmpeg_bin: str = "ffmpeg",
) -> List[Tuple[float, float]]:
    """Detect intervals of silence in an audio file using FFmpeg's silencedetect filter.

    Args:
        audio_path: Path to audio file.
        silence_thresh_db: Noise threshold in dB (e.g. -40dB).
        min_silence_sec: Minimum duration of silence to detect (seconds).
        ffmpeg_bin: Name or path of ffmpeg binary.

    Returns:
        List of (silence_start, silence_end) in seconds.
    """
    path = Path(audio_path).resolve()
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-i",
        str(path),
        "-af",
        f"silencedetect=noise={silence_thresh_db}dB:d={min_silence_sec}",
        "-f",
        "null",
        "-",
    ]

    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except Exception as e:
        raise RuntimeError(f"FFmpeg 靜音偵測失敗: {e}") from e

    intervals: List[Tuple[float, float]] = []
    current_start: Optional[float] = None

    stderr_text = res.stderr or ""
    for line in stderr_text.splitlines():
        start_m = SILENCE_START_RE.search(line)
        if start_m:
            current_start = float(start_m.group("start"))
            continue

        end_m = SILENCE_END_RE.search(line)
        if end_m and current_start is not None:
            end_sec = float(end_m.group("end"))
            intervals.append((current_start, end_sec))
            current_start = None

    return intervals


def calculate_cut_points(
    total_duration: float,
    silence_intervals: List[Tuple[float, float]],
    target_chunk_sec: int = 600,
    search_window_sec: int = 30,
) -> List[float]:
    """Calculate optimal cut points at natural speech pauses.

    Args:
        total_duration: Total audio length in seconds.
        silence_intervals: List of (silence_start, silence_end) pairs.
        target_chunk_sec: Desired chunk length (default: 600s = 10m).
        search_window_sec: Window radius around target time to search for silence.

    Returns:
        Sorted list of cut boundaries in seconds, starting with 0.0 and ending with total_duration.
    """
    min_tail_sec = min(30.0, target_chunk_sec * 0.3)

    # If audio is shorter than target plus minimum tail, no cut needed
    if total_duration <= (target_chunk_sec + min_tail_sec):
        return [0.0, round(total_duration, 3)]

    cut_points: List[float] = [0.0]
    current_time = 0.0

    while current_time + target_chunk_sec + min_tail_sec <= total_duration:
        target_cut = current_time + target_chunk_sec
        window_start = max(0.0, target_cut - search_window_sec)
        window_end = min(total_duration, target_cut + search_window_sec)

        # Find candidates intersecting the search window
        candidates = [
            (s, e, e - s)
            for s, e in silence_intervals
            if (s <= window_end and e >= window_start)
        ]

        if candidates:
            # Score candidates: prioritize longer silence duration and proximity to target_cut
            best_candidate = max(
                candidates,
                key=lambda c: (c[2], -abs(((c[0] + c[1]) / 2.0) - target_cut)),
            )
            # Cut at the midpoint of the best silence interval
            cut_point = round((best_candidate[0] + best_candidate[1]) / 2.0, 3)
        else:
            # If no silence in immediate window, search broader window or fallback to target_cut
            broader_candidates = [
                (s, e, e - s)
                for s, e in silence_intervals
                if (s <= window_end + search_window_sec and e >= window_start - search_window_sec)
            ]
            if broader_candidates:
                best_candidate = min(
                    broader_candidates,
                    key=lambda c: abs(((c[0] + c[1]) / 2.0) - target_cut),
                )
                cut_point = round((best_candidate[0] + best_candidate[1]) / 2.0, 3)
            else:
                cut_point = round(target_cut, 3)

        # Guard: ensure progress is made
        if cut_point <= cut_points[-1] + 1.0:
            cut_point = round(cut_points[-1] + target_chunk_sec, 3)

        if cut_point >= total_duration - min_tail_sec:
            break

        cut_points.append(cut_point)
        current_time = cut_point

    cut_points.append(round(total_duration, 3))
    return cut_points



def segment_audio(
    input_path: Path | str,
    output_dir: Path | str,
    opts: Optional[SegmentationOptions] = None,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
) -> List[ChunkInfo]:
    """Segment an audio file into manageable chunks at natural silence points.

    Args:
        input_path: Preprocessed WAV file.
        output_dir: Output folder for audio chunks (e.g. work/<course>/<date>/chunks).
        opts: Segmentation options.
        ffmpeg_bin: Path to ffmpeg binary.
        ffprobe_bin: Path to ffprobe binary.

    Returns:
        List of ChunkInfo objects representing generated chunk files.
    """
    in_file = Path(input_path).resolve()
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if opts is None:
        opts = SegmentationOptions()

    total_duration = get_audio_duration(in_file, ffprobe_bin=ffprobe_bin)
    silence_intervals = detect_silence_intervals(
        in_file,
        silence_thresh_db=opts.silence_thresh_db,
        min_silence_sec=opts.min_silence_sec,
        ffmpeg_bin=ffmpeg_bin,
    )

    cut_points = calculate_cut_points(
        total_duration,
        silence_intervals,
        target_chunk_sec=opts.target_chunk_sec,
        search_window_sec=opts.search_window_sec,
    )

    chunks: List[ChunkInfo] = []

    for idx in range(len(cut_points) - 1):
        start_sec = cut_points[idx]
        end_sec = cut_points[idx + 1]
        chunk_duration = round(end_sec - start_sec, 3)

        chunk_filename = f"chunk_{idx + 1:03d}.wav"
        chunk_path = out_dir / chunk_filename

        # Extract chunk using stream copy (lossless, instant)
        cmd = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start_sec:.3f}",
            "-to",
            f"{end_sec:.3f}",
            "-i",
            str(in_file),
            "-c",
            "copy",
            str(chunk_path),
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode("utf-8", errors="ignore") if e.stderr else str(e)
            raise RuntimeError(f"切割片段 {chunk_filename} 失敗: {err}") from e

        chunks.append(
            ChunkInfo(
                index=idx + 1,
                filename=chunk_filename,
                filepath=str(chunk_path),
                start_sec=start_sec,
                end_sec=end_sec,
                duration_sec=chunk_duration,
            )
        )

    # Save manifest.json in chunks folder
    manifest_file = out_dir / "manifest.json"
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump([c.model_dump() for c in chunks], f, ensure_ascii=False, indent=2)

    return chunks
