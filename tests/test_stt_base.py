"""Unit tests for STT base interface and outputs."""

from pathlib import Path
from lecture_pipeline.stt.base import (
    ChunkTranscription,
    FullTranscription,
    format_timestamp,
    save_stt_outputs,
)


def test_format_timestamp():
    """Verify timestamp formatting for various durations."""
    assert format_timestamp(0) == "[00:00]"
    assert format_timestamp(45.2) == "[00:45]"
    assert format_timestamp(615) == "[10:15]"
    assert format_timestamp(3665) == "[01:01:05]"


def test_save_stt_outputs(tmp_path: Path):
    """Verify 01_raw.txt, 01_raw_timestamped.txt, and 01_raw.json generation."""
    chunks = [
        ChunkTranscription(
            index=1,
            start_sec=0.0,
            end_sec=600.0,
            duration_sec=600.0,
            raw_text="大家好，今天我們來討論作業系統的核心架構。",
            model_used="gemini-3.8-flash",
            latency_sec=3.5,
        ),
        ChunkTranscription(
            index=2,
            start_sec=600.0,
            end_sec=1200.0,
            duration_sec=600.0,
            raw_text="首先看記憶體管理（Memory Management）。",
            model_used="gemini-3.8-flash",
            latency_sec=4.1,
        ),
    ]

    full = FullTranscription(
        channel_name="C1",
        course="OperatingSystems",
        date="20261015",
        chunks=chunks,
        full_text="大家好，今天我們來討論作業系統的核心架構。\n\n首先看記憶體管理（Memory Management）。",
        timestamped_text="[00:00] 大家好，今天我們來討論作業系統的核心架構。\n\n[10:00] 首先看記憶體管理（Memory Management）。",
        total_latency_sec=7.6,
    )

    outputs = save_stt_outputs(full, tmp_path)

    raw_file = outputs["raw"]
    ts_file = outputs["timestamped"]
    json_file = outputs["json"]

    assert raw_file.is_file()
    assert ts_file.is_file()
    assert json_file.is_file()

    raw_content = raw_file.read_text(encoding="utf-8")
    assert "作業系統的核心架構" in raw_content
    assert "記憶體管理" in raw_content

    ts_content = ts_file.read_text(encoding="utf-8")
    assert "[00:00]" in ts_content
    assert "[10:00]" in ts_content
