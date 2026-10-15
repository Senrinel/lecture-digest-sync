"""Unit tests for P5 Advanced Cleanup modes (audio, fusion)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from lecture_pipeline.cleanup.text_cleanup import GeminiTextCleanupEngine


def test_fusion_mode_prompt_building():
    """Verify fusion mode formats both Channel A and Channel B texts in prompt."""
    engine = GeminiTextCleanupEngine(
        api_key="mock_key",
        mode="fusion",
    )
    prompt = engine.build_prompt(
        raw_text_chunk="通道 A 逐字稿內容：這是一堂離散數學。",
        secondary_text_chunk="通道 B 逐字稿內容：這是一堂離散數學課程。",
        course="離散數學",
    )

    assert "【通道 A 逐字稿】" in prompt
    assert "通道 A 逐字稿內容" in prompt
    assert "【通道 B 逐字稿】" in prompt
    assert "通道 B 逐字稿內容" in prompt


def test_fusion_mode_clean_execution():
    """Verify fusion mode executes clean and returns fused result."""
    engine = GeminiTextCleanupEngine(
        api_key="mock_key",
        mode="fusion",
    )

    mock_fused_text = "這是一堂關於離散數學的高品質融合課堂講義文稿內容。"
    raw_text = "這是一堂關於離散數學的高品質融合課堂講義文稿內容。"

    with patch.object(
        engine,
        "_call_gemini_api",
        return_value=(mock_fused_text, "gemini-3.5-flash"),
    ):
        result = engine.clean(
            raw_text=raw_text,
            secondary_text="這是一堂關於離散數學的課程講義內容。",
            course="離散數學",
        )

    assert "高品質融合" in result.full_cleaned_text
    assert result.validation_passed is True


def test_audio_mode_file_upload_and_cleanup(tmp_path: Path):
    """Verify audio mode uploads audio chunk and safely deletes it after inference."""
    audio_file = tmp_path / "chunk_001.wav"
    audio_file.write_bytes(b"RIFFdummydataWAVEfmt ")

    engine = GeminiTextCleanupEngine(
        api_key="mock_key",
        mode="audio",
    )

    mock_upload = MagicMock()
    mock_upload.name = "files/test_audio_id"

    engine.client = MagicMock()
    engine.client.files.upload.return_value = mock_upload
    mock_resp = MagicMock()
    mock_resp.text = "聽音修正後的標準課堂繁體文字。"
    engine.client.models.generate_content.return_value = mock_resp

    cleaned_text, model = engine._call_gemini_api(
        prompt="請聽音修正逐字稿",
        audio_path=audio_file,
    )

    assert "聽音修正後" in cleaned_text
    # Verify file was uploaded
    engine.client.files.upload.assert_called_once_with(file=str(audio_file))
    # Verify file was cleaned up
    engine.client.files.delete.assert_called_once_with(name="files/test_audio_id")
