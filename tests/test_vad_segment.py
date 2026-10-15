"""Unit tests for vad_segment.py."""

import subprocess
import pytest
from pathlib import Path
from lecture_pipeline.vad_segment import (
    SilenceInterval,
    calculate_cut_points,
    detect_silences,
    segment_audio,
    get_audio_duration,
)


def test_calculate_cut_points_picks_best_silence():
    # Audio of 1200 seconds (20 mins), target 600s
    silences = [
        SilenceInterval(start=100.0, end=102.0),
        # Good silence near target 600s (at 598s~602s)
        SilenceInterval(start=598.0, end=602.0),
        SilenceInterval(start=900.0, end=901.0),
    ]
    cuts = calculate_cut_points(
        total_duration=1200.0,
        silences=silences,
        target_chunk_sec=600.0,
        search_window_sec=30.0,
    )
    assert len(cuts) == 1
    # Should cut at the midpoint of the silence at 600.0s
    assert cuts[0] == 600.0


def test_segment_short_audio_no_cuts():
    # 300s audio with target 600s: should produce 0 cuts
    cuts = calculate_cut_points(
        total_duration=300.0,
        silences=[],
        target_chunk_sec=600.0,
    )
    assert cuts == []


def test_actual_vad_segmentation_with_silence_gap(tmp_path):
    # Generate 12-second audio: 5s tone + 2s silence + 5s tone
    synth_wav = tmp_path / "tone_with_silence.wav"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "sine=frequency=440:duration=5",
            "-f", "lavfi",
            "-i", "anullsrc=r=16000:cl=mono:d=2",
            "-f", "lavfi",
            "-i", "sine=frequency=440:duration=5",
            "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]",
            "-map", "[out]",
            "-c:a", "pcm_s16le",
            str(synth_wav),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    total_dur = get_audio_duration(synth_wav)
    assert 11.5 <= total_dur <= 12.5

    # Detect silences: should find the silence around 5.0s ~ 7.0s
    silences = detect_silences(synth_wav, noise_thresh_db=-40.0, min_silence_sec=0.5)
    assert len(silences) >= 1
    assert any(4.5 <= s.start <= 5.5 and 6.5 <= s.end <= 7.5 for s in silences)

    # Segment with target_chunk_sec = 6.0s (target lands right in the 2s silence gap)
    chunks = segment_audio(
        synth_wav,
        tmp_path,
        target_chunk_sec=6.0,
        search_window_sec=2.0,
        noise_thresh_db=-40.0,
        min_silence_sec=0.5,
    )

    assert len(chunks) == 2
    assert chunks[0].filename == "chunk_001.wav"
    assert chunks[1].filename == "chunk_002.wav"
    assert (tmp_path / "chunks" / "manifest.json").exists()
