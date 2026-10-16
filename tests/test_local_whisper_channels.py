"""Unit tests for local faster-whisper channels (C4, C6, C7)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from lecture_pipeline.state import ChunkInfo
from lecture_pipeline.stt import (
    BreezeASRChannel,
    WhisperLargeChannel,
    WhisperTurboChannel,
    get_stt_channel,
)


def test_channel_instantiation():
    """Verify channel IDs and names."""
    c7 = WhisperTurboChannel()
    assert c7.channel_id == "C7"
    assert "Turbo" in c7.name

    c6 = WhisperLargeChannel()
    assert c6.channel_id == "C6"
    assert "Large-v3" in c6.name

    c4 = BreezeASRChannel()
    assert c4.channel_id == "C4"
    assert "Breeze" in c4.name


def test_get_stt_channel_factory():
    """Verify factory returns appropriate local channel instances."""
    assert isinstance(get_stt_channel("c4"), BreezeASRChannel)
    assert isinstance(get_stt_channel("C6"), WhisperLargeChannel)
    assert isinstance(get_stt_channel("C7"), WhisperTurboChannel)


def test_build_initial_prompt_with_glossary():
    """Verify context prompt contains course title and custom terms."""
    channel = WhisperTurboChannel()
    prompt = channel.build_initial_prompt(
        course="離散數學",
        glossary=["圖論", "生成樹", "漢米頓路徑"],
    )
    assert "離散數學" in prompt
    assert "圖論" in prompt
    assert "生成樹" in prompt
    assert "漢米頓路徑" in prompt


def test_transcribe_chunk_mock_subprocess(tmp_path: Path):
    """Verify transcribe_chunk calls subprocess, parses JSON, and converts Simplified to Traditional Chinese."""
    audio_file = tmp_path / "test_chunk.wav"
    audio_file.write_bytes(b"RIFFdummydataWAVEfmt ")

    chunk_info = ChunkInfo(
        index=1,
        filename="test_chunk.wav",
        filepath=str(audio_file),
        start_sec=0.0,
        end_sec=30.0,
        duration_sec=30.0,
    )

    channel = WhisperTurboChannel()

    mock_output_data = {
        "text": "这是离散数学的图论讲解，请参考生成树演算法。",
        "language": "zh",
        "language_probability": 0.98,
        "duration_sec": 3.45,
        "timestamps": [
            {"start": 0.0, "end": 2.5, "text": "这是离散数学"},
            {"start": 2.5, "end": 5.0, "text": "的图论讲解"},
        ],
    }

    def fake_subprocess_run(cmd, **kwargs):
        # Find --output-json argument
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
        result = channel.transcribe_chunk(
            audio_chunk_path=audio_file,
            chunk_info=chunk_info,
            course="離散數學",
            glossary=["圖論"],
        )

    assert result.index == 1
    assert result.model_used == "turbo"
    assert result.latency_sec == 3.45
    # Verified OpenCC s2twp conversion applied: 离散数学 -> 離散數學, 演算法 preserved
    assert "這是離散數學的圖論講解" in result.raw_text
    assert "生成樹" in result.raw_text
    assert len(result.metadata["timestamps"]) == 2
    assert "這是離散數學" in result.metadata["timestamps"][0]["text"]


def test_transcribe_chunk_with_chinese_filename_and_path(tmp_path: Path):
    """Verify that transcribe_chunk correctly handles Chinese filenames with special characters."""
    chinese_dir = tmp_path / "測試【中文目錄】（專題）"
    chinese_dir.mkdir(parents=True, exist_ok=True)
    audio_file = chinese_dir / "課堂 錄音 %20 (第一講).wav"
    audio_file.write_bytes(b"RIFFdummydataWAVEfmt ")

    chunk_info = ChunkInfo(
        index=1,
        filename=audio_file.name,
        filepath=str(audio_file),
        start_sec=0.0,
        end_sec=60.0,
        duration_sec=60.0,
    )

    channel = WhisperTurboChannel()

    mock_output_data = {
        "text": "這是畢業流程說明的審查事項。",
        "language": "zh",
        "language_probability": 0.99,
        "duration_sec": 2.1,
        "timestamps": [{"start": 0.0, "end": 2.0, "text": "這是畢業流程說明的審查事項。"}],
    }

    captured_cmd = []
    captured_env = {}

    def fake_subprocess_run(cmd, env=None, **kwargs):
        nonlocal captured_cmd, captured_env
        captured_cmd = list(cmd)
        captured_env = dict(env) if env else {}

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
        result = channel.transcribe_chunk(
            audio_chunk_path=audio_file,
            chunk_info=chunk_info,
            course="畢業流程說明",
            glossary=["審查系統"],
        )

    assert result.index == 1
    assert "畢業流程說明" in result.raw_text
    # Verify UTF-8 defensive flags are strictly enforced in subprocess command and environment
    assert "-X" in captured_cmd and "utf8" in captured_cmd
    assert captured_env.get("PYTHONUTF8") == "1"
    assert captured_env.get("PYTHONIOENCODING") == "utf-8"
    # Verify that the full unicode Chinese path was passed cleanly
    assert str(audio_file.resolve()) in captured_cmd
