"""Conservative VAD audio chunking module.

Locates natural speech pauses using ffmpeg silencedetect to split long audio
files into 10~15 minute segments without cutting mid-sentence.
"""

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class AudioChunk:
    index: int
    file_path: str
    start_sec: float
    end_sec: float
    duration_sec: float


def get_audio_duration(file_path: str | Path) -> float:
    """Returns audio duration in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path),
    ]
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed to read duration:\n{result.stderr}")
    return float(result.stdout.strip())


def detect_silence_intervals(
    file_path: str | Path,
    noise_thresh_db: int = -40,
    min_silence_sec: float = 0.5,
) -> List[Tuple[float, float]]:
    """Detects silence intervals [start_sec, end_sec] using ffmpeg silencedetect."""
    cmd = [
        "ffmpeg",
        "-i", str(file_path),
        "-af", f"silencedetect=noise={noise_thresh_db}dB:d={min_silence_sec}",
        "-f", "null",
        "-",
    ]
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    silences: List[Tuple[float, float]] = []
    current_start: Optional[float] = None

    for line in result.stderr.splitlines():
        # [silencedetect @ ...] silence_start: 123.456
        m_start = re.search(r"silence_start:\s*([0-9.]+)", line)
        if m_start:
            current_start = float(m_start.group(1))

        # [silencedetect @ ...] silence_end: 125.789 | silence_duration: 2.333
        m_end = re.search(r"silence_end:\s*([0-9.]+)", line)
        if m_end and current_start is not None:
            silences.append((current_start, float(m_end.group(1))))
            current_start = None

    return silences


def find_best_cut_point(
    target_sec: float,
    search_window_sec: float,
    silences: List[Tuple[float, float]],
    fallback_point: float,
) -> float:
    """Finds the optimal cut point within target_sec ± search_window_sec.
    
    Prefers the longest silence pause nearest to the target time.
    """
    min_bound = target_sec - search_window_sec
    max_bound = target_sec + search_window_sec

    candidates = [
        (s, e, (e - s), abs(((s + e) / 2.0) - target_sec))
        for (s, e) in silences
        if min_bound <= ((s + e) / 2.0) <= max_bound
    ]

    if not candidates:
        # If no silence found in search window, use fallback point
        return fallback_point

    # Sort by longest silence duration descending, then smallest distance to target
    candidates.sort(key=lambda x: (-x[2], x[3]))
    best = candidates[0]
    return (best[0] + best[1]) / 2.0  # Cut in the middle of the silence pause


def chunk_audio_file(
    input_audio: str | Path,
    output_dir: str | Path,
    target_chunk_sec: float = 600.0,    # 10 minutes default
    search_window_sec: float = 30.0,    # ±30 seconds search
    silence_thresh_db: int = -40,
    min_silence_sec: float = 0.5,
    min_tail_sec: float = 60.0,         # Minimum remaining duration to create separate tail chunk
) -> List[AudioChunk]:
    """Splits a long audio file into conservative chunks at natural silence pauses."""
    inp = Path(input_audio)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    total_duration = get_audio_duration(inp)

    # If the file is already short enough, return as single chunk
    if total_duration <= target_chunk_sec:
        dest = out_dir / f"{inp.stem}_chunk_001.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(inp), "-c", "copy", str(dest)],
            check=True,
            capture_output=True,
        )
        chunk = AudioChunk(
            index=1,
            file_path=str(dest),
            start_sec=0.0,
            end_sec=round(total_duration, 2),
            duration_sec=round(total_duration, 2),
        )
        _save_manifest(out_dir, [chunk])
        return [chunk]

    # Detect all silences in the audio file
    silences = detect_silence_intervals(
        inp,
        noise_thresh_db=silence_thresh_db,
        min_silence_sec=min_silence_sec,
    )

    # Compute cut points
    cut_points = [0.0]
    current_time = 0.0

    while current_time + target_chunk_sec < total_duration:
        target = current_time + target_chunk_sec
        # If remaining duration after cut would be too short, merge into final chunk
        if total_duration - target < min_tail_sec:
            break

        cut_point = find_best_cut_point(
            target_sec=target,
            search_window_sec=search_window_sec,
            silences=silences,
            fallback_point=target,
        )
        cut_points.append(cut_point)
        current_time = cut_point

    cut_points.append(total_duration)

    # Slice chunks with ffmpeg
    chunks: List[AudioChunk] = []
    for i in range(len(cut_points) - 1):
        c_idx = i + 1
        c_start = cut_points[i]
        c_end = cut_points[i + 1]
        c_dur = round(c_end - c_start, 2)

        chunk_path = out_dir / f"chunk_{c_idx:03d}.wav"

        # Precise slicing with re-encode to prevent keyframe offset issues
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{c_start:.3f}",
            "-to", f"{c_end:.3f}",
            "-i", str(inp),
            "-c", "copy",
            str(chunk_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)

        chunks.append(
            AudioChunk(
                index=c_idx,
                file_path=str(chunk_path),
                start_sec=round(c_start, 2),
                end_sec=round(c_end, 2),
                duration_sec=c_dur,
            )
        )

    _save_manifest(out_dir, chunks)
    return chunks


def _save_manifest(output_dir: Path, chunks: List[AudioChunk]) -> None:
    manifest_path = output_dir / "chunks_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump([asdict(c) for c in chunks], f, indent=2, ensure_ascii=False)
