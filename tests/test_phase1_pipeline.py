"""Phase 1 Full End-to-End Integration Test.

Tests the complete flow from input audio to filename parsing,
FFmpeg preprocessing, VAD silence chunking, and run.json record.
"""

import math
import struct
import wave
from pathlib import Path
from typer.testing import CliRunner

from lecture_pipeline.cli import app
from lecture_pipeline.state import load_run_state

runner = CliRunner()


def generate_test_lecture_audio(filepath: Path, duration_sec: float = 26.0, sample_rate: int = 44100):
    """Generate a test lecture audio file with periodic pauses:

    0s~8s: speech tone
    8s~10s: silence pause (2s)
    10s~18s: speech tone
    18s~20s: silence pause (2s)
    20s~26s: speech tone
    Total: 26s
    """
    n_samples = int(duration_sec * sample_rate)

    with wave.open(str(filepath), "w") as f:
        f.setnchannels(2)  # Stereo raw input
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_samples):
            t = i / sample_rate
            if (8.0 <= t < 10.0) or (18.0 <= t < 20.0):
                val = 0
            else:
                val = int(7000 * math.sin(2 * math.pi * 350 * t))
            frames.extend(struct.pack("<hh", val, val))
        f.writeframes(frames)


def test_phase1_full_pipeline(tmp_path: Path):
    """Test the complete Phase 1 CLI flow end-to-end."""
    inbox_dir = tmp_path / "inbox"
    work_dir = tmp_path / "work"
    inbox_dir.mkdir()
    work_dir.mkdir()

    raw_audio = inbox_dir / "OperatingSystems_20261015.wav"
    generate_test_lecture_audio(raw_audio, duration_sec=26.0)
    assert raw_audio.is_file()


    target_work_folder = work_dir / "OperatingSystems" / "20261015"

    # Run CLI command: prep <raw_audio> --work-dir <target_work_folder> --target-sec 9 --pre hp,loud
    result = runner.invoke(
        app,
        [
            "prep",
            str(raw_audio),
            "--work-dir",
            str(target_work_folder),
            "--target-sec",
            "9",
            "--pre",
            "hp,loud",
        ],
    )

    assert result.exit_code == 0, f"CLI output: {result.stdout}"
    assert "Phase 1 流程執行成功" in result.stdout

    # Verify directory structure
    assert target_work_folder.is_dir()

    # 1. Normalized audio exists and is 16kHz mono
    norm_audio = target_work_folder / "00_normalized.wav"
    assert norm_audio.is_file()
    with wave.open(str(norm_audio), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 16000

    # 2. Chunks exist and cut at silence points (around 8~10s and 18~20s)
    chunks_dir = target_work_folder / "chunks"
    assert chunks_dir.is_dir()
    manifest_file = chunks_dir / "manifest.json"
    assert manifest_file.is_file()

    chunk_files = list(chunks_dir.glob("chunk_*.wav"))
    assert len(chunk_files) == 3

    # 3. run.json is properly populated
    state = load_run_state(target_work_folder)
    assert state is not None
    assert state.course == "OperatingSystems"
    assert state.date == "20261015"
    assert state.pre_options.hp == "100Hz"
    assert state.pre_options.loud == "-19LUFS"
    assert state.stage_status["preprocess"] == "DONE"
    assert state.stage_status["vad_segment"] == "DONE"
    assert len(state.chunks) == 3
    assert state.chunks[0].filename == "chunk_001.wav"
    assert 8.0 <= state.chunks[0].end_sec <= 10.5
