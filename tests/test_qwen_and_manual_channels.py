"""Unit tests for Qwen3-ASR (C3) and Manual Baseline (C8) channels."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from lecture_pipeline.state import ChunkInfo
from lecture_pipeline.stt import (
    ManualBaselineChannel,
    QwenASRChannel,
    get_stt_channel,
)


def test_channel_instantiations():
    """Verify channel IDs and names."""
    c3 = QwenASRChannel()
    assert c3.channel_id == "C3"
    assert "Qwen" in c3.name

    c8 = ManualBaselineChannel()
    assert c8.channel_id == "C8"
    assert "Manual" in c8.name


def test_get_stt_channel_factory_c3_c8():
    """Verify factory returns appropriate instances."""
    assert isinstance(get_stt_channel("C3"), QwenASRChannel)
    assert isinstance(get_stt_channel("C8"), ManualBaselineChannel)


def test_qwen_build_prompt():
    """Verify prompt formatting with course and glossary."""
    c3 = QwenASRChannel()
    prompt = c3.build_prompt(
        course="資料結構",
        glossary=["紅黑樹", "二元搜尋", "雜湊表"],
    )
    assert "資料結構" in prompt
    assert "紅黑樹" in prompt
    assert "雜湊表" in prompt


def test_qwen_transcribe_chunk_mock(tmp_path: Path):
    """Verify Qwen chunk transcription calls subprocess and applies OpenCC."""
    audio_file = tmp_path / "qwen_chunk.wav"
    audio_file.write_bytes(b"RIFFdummyWAVEfmt ")

    chunk_info = ChunkInfo(
        index=1,
        filename="qwen_chunk.wav",
        filepath=str(audio_file),
        start_sec=0.0,
        end_sec=15.0,
        duration_sec=15.0,
    )

    c3 = QwenASRChannel()

    mock_output_data = {
        "text": "今天我们要讨论图论算法与最小生成树。",
        "model": "Qwen/Qwen3-ASR-1.7B-hf",
        "language": "zh",
        "duration_sec": 2.15,
        "timestamps": [],
    }

    def fake_subprocess_run(cmd, **kwargs):
        if "--output-json" in cmd:
            idx = cmd.index("--output-json")
            json_file = Path(cmd[idx + 1])
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(mock_output_data, f, ensure_ascii=False)
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = json.dumps(mock_output_data, ensure_ascii=False)
        mock_res.stderr = ""
        return mock_res

    with patch("subprocess.run", side_effect=fake_subprocess_run):
        result = c3.transcribe_chunk(
            audio_chunk_path=audio_file,
            chunk_info=chunk_info,
            course="演算法",
            glossary=["圖論"],
        )

    assert result.index == 1
    assert result.latency_sec == 2.15
    # Simplified -> Taiwanese Traditional
    assert "今天我們要討論圖論演算法與最小生成樹" in result.raw_text


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
