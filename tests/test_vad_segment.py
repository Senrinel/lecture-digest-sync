"""Unit tests for the VAD and silence-based audio segmentation module."""

import math
import struct
import wave
from pathlib import Path
from lecture_pipeline.state import SegmentationOptions
from lecture_pipeline.vad_segment import (
    calculate_cut_points,
    detect_silence_intervals,
    get_audio_duration,
    segment_audio,
)


def create_speech_with_silence_wav(filepath: Path, sample_rate: int = 16000):
    """Generate audio with alternating tone (speech proxy) and absolute silence:

    0s ~ 4s: Tone
    4s ~ 6s: Silence (2s)
    6s ~ 10s: Tone
    10s ~ 12s: Silence (2s)
    12s ~ 16s: Tone
    Total = 16s
    """
    total_sec = 16.0
    n_samples = int(total_sec * sample_rate)

    with wave.open(str(filepath), "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        frames = bytearray()

        for i in range(n_samples):
            t = i / sample_rate
            # Silence intervals: 4.0 <= t < 6.0 and 10.0 <= t < 12.0
            if (4.0 <= t < 6.0) or (10.0 <= t < 12.0):
                val = 0
            else:
                val = int(8000 * math.sin(2 * math.pi * 300 * t))
            frames.extend(struct.pack("<h", val))

        wav_file.writeframes(frames)


def test_calculate_cut_points_basic():
    """Verify cut point calculation places boundaries inside silence intervals."""
    silence = [(4.0, 6.0), (10.0, 12.0)]
    # Target cut at 5s (search window +-2s)
    cuts = calculate_cut_points(
        total_duration=16.0,
        silence_intervals=silence,
        target_chunk_sec=5,
        search_window_sec=2,
    )
    # 0.0, midpoint of (4,6) = 5.0, midpoint of (10,12) = 11.0, and 16.0
    assert cuts[0] == 0.0
    assert 4.0 <= cuts[1] <= 6.0
    assert 10.0 <= cuts[2] <= 12.0
    assert cuts[-1] == 16.0


def test_detect_silence_intervals(tmp_path: Path):
    """Verify FFmpeg silencedetect detects the simulated silence intervals."""
    test_wav = tmp_path / "tone_silence.wav"
    create_speech_with_silence_wav(test_wav)

    dur = get_audio_duration(test_wav)
    assert abs(dur - 16.0) < 0.2

    intervals = detect_silence_intervals(
        test_wav,
        silence_thresh_db=-30,
        min_silence_sec=0.5,
    )
    # Should detect at least the two 2-second silence intervals
    assert len(intervals) >= 2
    # Verify first silence around 4~6s
    assert any(3.8 <= start <= 4.2 for start, end in intervals)


def test_segment_audio_execution(tmp_path: Path):
    """Verify complete segmentation process generating chunk files and manifest."""
    test_wav = tmp_path / "speech_to_chunk.wav"
    chunks_dir = tmp_path / "chunks"
    create_speech_with_silence_wav(test_wav)

    # Set target chunk to 5s with 2s window so it cuts at the two pauses
    opts = SegmentationOptions(
        target_chunk_sec=5,
        search_window_sec=2,
        silence_thresh_db=-30,
        min_silence_sec=0.5,
    )

    chunks = segment_audio(test_wav, chunks_dir, opts=opts)
    assert len(chunks) == 3

    assert chunks[0].filename == "chunk_001.wav"
    assert chunks[1].filename == "chunk_002.wav"
    assert chunks[2].filename == "chunk_003.wav"

    # Verify each chunk exists on disk and is a valid WAV
    for c in chunks:
        p = Path(c.filepath)
        assert p.is_file()
        assert p.stat().st_size > 0
        chunk_dur = get_audio_duration(p)
        assert abs(chunk_dur - c.duration_sec) < 0.1

    # Verify manifest.json exists
    manifest_file = chunks_dir / "manifest.json"
    assert manifest_file.is_file()
