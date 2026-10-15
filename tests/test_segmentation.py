import subprocess
from pathlib import Path
from lecture_pipeline.segmentation import (
    chunk_audio_file,
    detect_silence_intervals,
    get_audio_duration,
)


def generate_audio_with_silence(path: Path):
    """Generates 12s audio: 5s tone + 2s silence + 5s tone."""
    # sine=1000:d=5, aevalsrc=0:d=2, sine=1000:d=5
    filter_complex = "sine=frequency=1000:duration=5[a1];aevalsrc=0:d=2[s1];sine=frequency=1000:duration=5[a2];[a1][s1][a2]concat=n=3:v=0:a=1[out]"
    cmd = [
        "ffmpeg", "-y",
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-ar", "16000",
        "-ac", "1",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def test_get_audio_duration(tmp_path):
    audio_path = tmp_path / "test_dur.wav"
    generate_audio_with_silence(audio_path)
    dur = get_audio_duration(audio_path)
    assert 11.5 <= dur <= 12.5


def test_detect_silence_intervals(tmp_path):
    audio_path = tmp_path / "test_silence.wav"
    generate_audio_with_silence(audio_path)
    silences = detect_silence_intervals(audio_path, noise_thresh_db=-40, min_silence_sec=0.5)
    assert len(silences) >= 1
    # Silence should be around 5.0 to 7.0
    start, end = silences[0]
    assert 4.8 <= start <= 5.2
    assert 6.8 <= end <= 7.2


def test_chunk_audio_file(tmp_path):
    audio_path = tmp_path / "test_chunk.wav"
    chunks_dir = tmp_path / "chunks"
    generate_audio_with_silence(audio_path)

    # Set target chunk to 6 seconds so it splits at the 5-7s pause
    chunks = chunk_audio_file(
        input_audio=audio_path,
        output_dir=chunks_dir,
        target_chunk_sec=6.0,
        search_window_sec=2.0,
        min_tail_sec=1.0,
    )
    assert len(chunks) == 2
    assert chunks[0].index == 1
    assert chunks[1].index == 2
    assert (chunks_dir / "chunks_manifest.json").exists()
