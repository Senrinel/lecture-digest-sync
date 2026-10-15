"""Phase 2 Full End-to-End Integration Test.

Tests running the CLI 'stt' command against a prepared lecture work directory,
verifying 01_raw.txt, 01_raw_timestamped.txt, 01_raw.json, and run.json updates.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
from typer.testing import CliRunner

from lecture_pipeline.cli import app
from lecture_pipeline.state import (
    ChunkInfo,
    PreprocessOptions,
    RunState,
    SegmentationOptions,
    load_run_state,
    save_run_state,
)
from lecture_pipeline.stt.base import ChunkTranscription, FullTranscription

runner = CliRunner()


@patch("lecture_pipeline.cli.get_stt_channel")
def test_phase2_full_pipeline(mock_get_channel, tmp_path: Path):
    """Test CLI 'stt' command execution and output artifact generation."""
    work_dir = tmp_path / "work" / "DatabaseSystems" / "20261015"
    chunks_dir = work_dir / "chunks"
    chunks_dir.mkdir(parents=True)

    # 1. Setup mock chunks on disk
    chunk1_file = chunks_dir / "chunk_001.wav"
    chunk2_file = chunks_dir / "chunk_002.wav"
    chunk1_file.touch()
    chunk2_file.touch()

    chunks = [
        ChunkInfo(
            index=1,
            filename="chunk_001.wav",
            filepath=str(chunk1_file),
            start_sec=0.0,
            end_sec=600.0,
            duration_sec=600.0,
        ),
        ChunkInfo(
            index=2,
            filename="chunk_002.wav",
            filepath=str(chunk2_file),
            start_sec=600.0,
            end_sec=1200.0,
            duration_sec=600.0,
        ),
    ]

    initial_state = RunState(
        source_filename="DatabaseSystems_20261015.mp3",
        source_path="/path/to/DatabaseSystems_20261015.mp3",
        course="DatabaseSystems",
        date="20261015",
        pre_options=PreprocessOptions(),
        seg_options=SegmentationOptions(),
        chunks=chunks,
        stage_status={"preprocess": "DONE", "vad_segment": "DONE", "stt": "PENDING"},
    )
    save_run_state(initial_state, work_dir)

    # 2. Mock STT Channel worker
    mock_channel = MagicMock()
    mock_channel.name = "Mocked STT Channel"
    mock_channel.channel_id = "C1"

    mock_full_transcription = FullTranscription(
        channel_name="C1",
        course="DatabaseSystems",
        date="20261015",
        chunks=[
            ChunkTranscription(
                index=1,
                start_sec=0.0,
                end_sec=600.0,
                duration_sec=600.0,
                raw_text="今天我們介紹關聯式資料庫（Relational Database）正規化。",
                model_used="gemini-3.8-flash",
                latency_sec=2.1,
            ),
            ChunkTranscription(
                index=2,
                start_sec=600.0,
                end_sec=1200.0,
                duration_sec=600.0,
                raw_text="正規化（Normalization）是為了消除資料冗餘。",
                model_used="gemini-3.8-flash",
                latency_sec=2.4,
            ),
        ],
        full_text="今天我們介紹關聯式資料庫（Relational Database）正規化。\n\n正規化（Normalization）是為了消除資料冗餘。",
        timestamped_text="[00:00] 今天我們介紹關聯式資料庫（Relational Database）正規化。\n\n[10:00] 正規化（Normalization）是為了消除資料冗餘。",
        total_latency_sec=4.5,
    )
    mock_channel.transcribe_all.return_value = mock_full_transcription
    mock_get_channel.return_value = mock_channel

    # 3. Invoke CLI command: stt <work_dir> --channel C1
    result = runner.invoke(app, ["stt", str(work_dir), "--channel", "C1"])

    assert result.exit_code == 0, f"CLI output: {result.stdout}"
    assert "辨識完成" in result.stdout

    # 4. Verify output files
    raw_txt = work_dir / "01_raw.txt"
    ts_txt = work_dir / "01_raw_timestamped.txt"
    raw_json = work_dir / "01_raw.json"

    assert raw_txt.is_file()
    assert ts_txt.is_file()
    assert raw_json.is_file()

    raw_content = raw_txt.read_text(encoding="utf-8")
    assert "關聯式資料庫" in raw_content
    assert "消除資料冗餘" in raw_content

    ts_content = ts_txt.read_text(encoding="utf-8")
    assert "[00:00]" in ts_content
    assert "[10:00]" in ts_content

    # 5. Verify updated run.json
    updated_state = load_run_state(work_dir)
    assert updated_state is not None
    assert updated_state.stage_status["stt"] == "DONE"
    assert updated_state.metadata["stt_channel"] == "C1"
    assert updated_state.metadata["stt_total_chars"] == len(raw_content)
