"""Phase 1 Full End-to-End Integration Test.

Tests the entire Phase 1 pipeline from raw input file to preprocessed WAV,
VAD silence segmentation, directory routing, and state persistence.
"""

import json
import subprocess
from pathlib import Path
import pytest
from typer.testing import CliRunner

from lecture_pipeline.cli import app
from lecture_pipeline.state import load_run_state


runner = CliRunner()


def test_phase1_full_pipeline_e2e(tmp_path):
    # 1. Setup mock audio file simulating Sony recorder file with standard course name
    mock_inbox = tmp_path / "inbox"
    mock_work = tmp_path / "work"
    mock_inbox.mkdir(parents=True)
    mock_work.mkdir(parents=True)

    input_file = mock_inbox / "DataMining_20261015_0900.mp3"

    # Create 15-second synthetic audio with two silence gaps at 5s~7s and 10s~12s
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
            "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=2",
            "-f", "lavfi", "-i", "sine=frequency=520:duration=3",
            "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=2",
            "-f", "lavfi", "-i", "sine=frequency=660:duration=3",
            "-filter_complex", "[0:a][1:a][2:a][3:a][4:a]concat=n=5:v=0:a=1[out]",
            "-map", "[out]",
            "-c:a", "libmp3lame",
            str(input_file),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    # 2. Run CLI command: prep <input_file> --pre hp,loud --target-sec 6.0 --work-dir <mock_work>
    result = runner.invoke(
        app,
        [
            "prep",
            str(input_file),
            "--pre", "hp:100Hz,loud:-19LUFS",
            "--target-sec", "6.0",
            "--work-dir", str(mock_work),
        ],
    )

    assert result.exit_code == 0, f"CLI command failed:\n{result.output}"

    # 3. Verify directory routing: work/DataMining/2026-10-15/
    expected_course_dir = mock_work / "DataMining" / "2026-10-15"
    assert expected_course_dir.exists(), f"Directory not found: {expected_course_dir}"

    # 4. Verify preprocessed WAV
    preprocessed_wav = expected_course_dir / "00_preprocessed.wav"
    assert preprocessed_wav.exists()
    assert preprocessed_wav.stat().st_size > 0

    # 5. Verify chunk files and manifest
    chunks_dir = expected_course_dir / "chunks"
    manifest_file = chunks_dir / "manifest.json"
    assert chunks_dir.exists()
    assert manifest_file.exists()

    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert len(manifest_data) >= 2
    for chunk in manifest_data:
        chunk_file = Path(chunk["file_path"])
        assert chunk_file.exists()
        assert chunk["duration_sec"] > 0

    # 6. Verify run.json state file
    state = load_run_state(expected_course_dir)
    assert state is not None
    assert state.status == "SEGMENTED"
    assert state.audio_meta.course_name == "DataMining"
    assert state.audio_meta.date_str == "2026-10-15"
    assert state.audio_meta.time_str == "0900"
    assert state.preprocess_options.hp == "100Hz"
    assert state.preprocess_options.loud == "-19LUFS"
    assert len(state.chunks) == len(manifest_data)
