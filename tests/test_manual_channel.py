"""Unit tests for Manual Baseline (C8) channel."""

from pathlib import Path
import pytest
from lecture_pipeline.state import ChunkInfo
from lecture_pipeline.stt import (
    ManualBaselineChannel,
    get_stt_channel,
)


def test_channel_instantiations():
    """Verify channel IDs and names."""
    c8 = ManualBaselineChannel()
    assert c8.channel_id == "C8"
    assert "Manual" in c8.name


def test_get_stt_channel_factory_c8():
    """Verify factory returns appropriate instance."""
    assert isinstance(get_stt_channel("C8"), ManualBaselineChannel)


def test_manual_baseline_transcribe_all(tmp_path: Path):
    """Verify ManualBaselineChannel loads ground truth file directly."""
    ref_file = tmp_path / "reference_ground_truth.txt"
    ref_file.write_text("這是人工校對的黃金標準逐字稿內容，包含了专业术语。", encoding="utf-8")

    c8 = ManualBaselineChannel(source_text_path=ref_file)

    chunks = [
        ChunkInfo(
            index=1,
            filename="chunk_001.wav",
            filepath=str(tmp_path / "chunk_001.wav"),
            start_sec=0.0,
            end_sec=60.0,
            duration_sec=60.0,
        )
    ]

    full = c8.transcribe_all(chunks=chunks, course="測試課程", date="20261015")
    assert full.channel_name == "C8"
    assert "這是人工校對的黃金標準逐字稿內容" in full.full_text
    assert "專業術語" in full.full_text  # OpenCC converted
    assert len(full.chunks) == 1
