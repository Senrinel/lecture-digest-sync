"""Unit tests for FireRedASR (C5) channel."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from lecture_pipeline.state import ChunkInfo
from lecture_pipeline.stt import (
    FireRedASRChannel,
    get_stt_channel,
)


def test_firered_channel_instantiation():
    """Verify channel ID and name."""
    c5 = FireRedASRChannel()
    assert c5.channel_id == "C5"
    assert "FireRedASR" in c5.name


def test_get_stt_channel_factory_c5():
    """Verify factory returns FireRedASRChannel instance."""
    assert isinstance(get_stt_channel("C5"), FireRedASRChannel)


def test_firered_transcribe_chunk_mock(tmp_path: Path):
    """Verify FireRedASR chunk transcription calls subprocess, parses JSON, and converts text."""
    audio_file = tmp_path / "firered_chunk.wav"
    audio_file.write_bytes(b"RIFFdummyWAVEfmt ")

    chunk_info = ChunkInfo(
        index=1,
        filename="firered_chunk.wav",
        filepath=str(audio_file),
        start_sec=0.0,
        end_sec=20.0,
        duration_sec=20.0,
    )

    c5 = FireRedASRChannel()

    mock_output_data = {
        "text": "小红书开源工业级语音识别模型，支持中文方言与歌词识别。",
        "model": "FireRedTeam/FireRedASR-AED-L",
        "asr_type": "aed",
        "duration_sec": 1.85,
        "timestamps": [
            {"start": 0.0, "end": 10.0, "text": "小红书开源工业级语音识别模型"},
            {"start": 10.0, "end": 20.0, "text": "支持中文方言与歌词识别"},
        ],
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
        result = c5.transcribe_chunk(
            audio_chunk_path=audio_file,
            chunk_info=chunk_info,
            course="語音處理",
        )

    assert result.index == 1
    assert result.latency_sec == 1.85
    # Simplified -> Taiwanese Traditional (with Taiwanese terminology conversion: 支持 -> 支援)
    assert "小紅書開源工業級語音識別模型" in result.raw_text
    assert "支援中文方言與歌詞識別" in result.raw_text
    assert len(result.metadata["timestamps"]) == 2
    assert "小紅書開源" in result.metadata["timestamps"][0]["text"]
