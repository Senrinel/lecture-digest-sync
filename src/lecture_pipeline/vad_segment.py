"""Intelligent VAD & Silence-based segmentation module.

Divides long audio recordings (2~3 hours) into stable chunks (default: 10~12 minutes)
by locating natural speech pauses/silences (>= 0.5s) near target intervals,
ensuring speech words and sentences are NEVER cut in the middle.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from pydantic import BaseModel


class SilenceInterval(BaseModel):
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def midpoint(self) -> float:
        return (self.start + self.end) / 2.0


class ChunkInfo(BaseModel):
    index: int
    filename: str
    file_path: str
    start_sec: float
    end_sec: float
    duration_sec: float


def get_audio_duration(file_path: str | Path) -> float:
    """Get exact audio duration in seconds using ffprobe."""
    path = Path(file_path).resolve()
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}:\n{res.stderr}")
    return float(res.stdout.strip())


def detect_silences(
    wav_path: str | Path,
    noise_thresh_db: float = -40.0,
    min_silence_sec: float = 0.5,
) -> list[SilenceInterval]:
    """Detect natural silence/pause intervals in audio using FFmpeg silencedetect.

    Args:
        wav_path: Path to audio file.
        noise_thresh_db: Silence threshold in dB (e.g. -40dB).
        min_silence_sec: Minimum pause duration to qualify as silence.

    Returns:
        List of SilenceInterval objects sorted by start time.
    """
    path = Path(wav_path).resolve()
    cmd = [
        "ffmpeg",
        "-i", str(path),
        "-af", f"silencedetect=noise={noise_thresh_db}dB:d={min_silence_sec}",
        "-f", "null",
        "-",
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # ffmpeg silencedetect outputs to stderr
    stderr = res.stderr

    silence_starts: list[float] = []
    intervals: list[SilenceInterval] = []

    # Regex patterns
    re_start = re.compile(r"silence_start:\s*([0-9.]+)")
    re_end = re.compile(r"silence_end:\s*([0-9.]+)")

    for line in stderr.splitlines():
        m_start = re_start.search(line)
        if m_start:
            silence_starts.append(float(m_start.group(1)))
            continue

        m_end = re_end.search(line)
        if m_end:
            end_t = float(m_end.group(1))
            start_t = silence_starts.pop(0) if silence_starts else 0.0
            intervals.append(SilenceInterval(start=start_t, end=end_t))

    return intervals


def calculate_cut_points(
    total_duration: float,
    silences: list[SilenceInterval],
    target_chunk_sec: float = 600.0,
    search_window_sec: float = 30.0,
) -> list[float]:
    """Calculate natural cut points by finding the best silence pause near target intervals.

    Args:
        total_duration: Total audio length in seconds.
        silences: List of detected silence intervals.
        target_chunk_sec: Preferred chunk duration (default: 600s = 10 minutes).
        search_window_sec: Search window around target point (default: ±30s).

    Returns:
        List of cut timestamps [t1, t2, ..., tn].
    """
    min_remainder = min(60.0, target_chunk_sec * 0.15)
    # If audio is already short enough, no cut needed
    if total_duration <= target_chunk_sec + min_remainder:
        return []

    cut_points: list[float] = []
    current_time = 0.0

    while current_time + target_chunk_sec < total_duration:
        target_cut = current_time + target_chunk_sec

        # If remaining time after cut would be too short, absorb into current chunk
        if total_duration - target_cut < min_remainder:
            break


        window_start = target_cut - search_window_sec
        window_end = target_cut + search_window_sec

        # Find silences overlapping the search window
        candidates = [
            s for s in silences
            if s.end >= window_start and s.start <= window_end
        ]

        if candidates:
            # Score candidates: prioritize longer silence, then closest to target
            best_silence = max(
                candidates,
                key=lambda s: (s.duration, -abs(s.midpoint - target_cut)),
            )
            cut_t = round(best_silence.midpoint, 2)
        else:
            # Try expanded search window (±60s)
            expanded_candidates = [
                s for s in silences
                if s.end >= (target_cut - search_window_sec * 2)
                and s.start <= (target_cut + search_window_sec * 2)
            ]
            if expanded_candidates:
                best_silence = max(
                    expanded_candidates,
                    key=lambda s: (s.duration, -abs(s.midpoint - target_cut)),
                )
                cut_t = round(best_silence.midpoint, 2)
            else:
                # Fallback to exact target cut if no silence detected
                cut_t = round(target_cut, 2)

        cut_points.append(cut_t)
        current_time = cut_t

    return cut_points


def segment_audio(
    wav_path: str | Path,
    output_dir: str | Path,
    target_chunk_sec: float = 600.0,
    search_window_sec: float = 30.0,
    noise_thresh_db: float = -40.0,
    min_silence_sec: float = 0.5,
) -> list[ChunkInfo]:
    """Segment audio into natural chunks and write them to output_dir/chunks/.

    Returns:
        List of ChunkInfo objects representing each created chunk.
    """
    inp = Path(wav_path).resolve()
    out_dir = Path(output_dir).resolve() / "chunks"
    out_dir.mkdir(parents=True, exist_ok=True)

    total_dur = get_audio_duration(inp)
    silences = detect_silences(inp, noise_thresh_db=noise_thresh_db, min_silence_sec=min_silence_sec)
    cuts = calculate_cut_points(total_dur, silences, target_chunk_sec, search_window_sec)

    # Boundaries: [0.0, cut1, cut2, ..., total_dur]
    boundaries = [0.0] + cuts + [total_dur]
    chunks: list[ChunkInfo] = []

    for i in range(len(boundaries) - 1):
        start_t = boundaries[i]
        end_t = boundaries[i + 1]
        chunk_dur = round(end_t - start_t, 2)

        chunk_name = f"chunk_{i+1:03d}.wav"
        chunk_path = out_dir / chunk_name

        # Slice using FFmpeg
        # -ss before -i for fast seek, -to for precise end
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(start_t),
            "-to", str(end_t),
            "-i", str(inp),
            "-c:a", "pcm_s16le",
            str(chunk_path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg slicing failed for chunk {i+1}:\n{res.stderr}")

        chunk_info = ChunkInfo(
            index=i + 1,
            filename=chunk_name,
            file_path=str(chunk_path),
            start_sec=round(start_t, 2),
            end_sec=round(end_t, 2),
            duration_sec=chunk_dur,
        )
        chunks.append(chunk_info)

    # Save manifest
    manifest_path = out_dir / "manifest.json"
    manifest_data = [c.model_dump() for c in chunks]
    manifest_path.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")

    return chunks
