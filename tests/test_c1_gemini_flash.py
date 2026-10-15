"""Unit tests for Channel C1 (Gemini Flash) with mock fallbacks."""

from pathlib import Path
from unittest.mock import MagicMock, patch
from lecture_pipeline.state import ChunkInfo
from lecture_pipeline.stt.c1_gemini_flash import GeminiFlashChannel


def test_c1_build_prompt():
    """Verify C1 prompt contains Taiwanese Mandarin rules, course, and glossary."""
    channel = GeminiFlashChannel(api_key="mock_key")
    prompt = channel._build_prompt(
        course="CloudComputing",
        glossary=["Kubernetes", "Microservices", "Docker"],
    )
    assert "台灣繁體中文" in prompt
    assert "CloudComputing" in prompt
    assert "Kubernetes" in prompt


@patch("lecture_pipeline.stt.c1_gemini_flash.genai.Client")
def test_c1_transcribe_chunk_success(mock_client_cls, tmp_path: Path):
    """Verify standard transcription and file cleanup."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    mock_file = MagicMock()
    mock_file.name = "files/test_file_123"
    mock_client.files.upload.return_value = mock_file

    mock_response = MagicMock()
    mock_response.text = "這是由 Gemini Flash 轉錄的繁體中文逐字稿。"
    mock_client.models.generate_content.return_value = mock_response

    test_audio = tmp_path / "chunk_001.wav"
    test_audio.touch()

    chunk_info = ChunkInfo(
        index=1,
        filename="chunk_001.wav",
        filepath=str(test_audio),
        start_sec=0.0,
        end_sec=600.0,
        duration_sec=600.0,
    )

    channel = GeminiFlashChannel(api_key="mock_key", primary_model="gemini-3.8-flash")
    result = channel.transcribe_chunk(test_audio, chunk_info)

    assert result.index == 1
    assert result.model_used == "gemini-3.8-flash"
    assert "Gemini Flash" in result.raw_text
    # Verify file was cleaned up
    mock_client.files.delete.assert_called_once_with(name="files/test_file_123")


@patch("lecture_pipeline.stt.c1_gemini_flash.genai.Client")
def test_c1_transcribe_chunk_fallback_on_503(mock_client_cls, tmp_path: Path):
    """Verify automatic model fallback when primary model hits a 503 error."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    mock_file = MagicMock()
    mock_file.name = "files/test_file_503"
    mock_client.files.upload.return_value = mock_file

    # First call (gemini-3.8-flash) raises 503 UNAVAILABLE
    # Second call (gemini-3.7-flash) succeeds
    mock_success_response = MagicMock()
    mock_success_response.text = "備援模型成功產出文字。"
    mock_client.models.generate_content.side_effect = [
        Exception("503 This model is currently experiencing high demand. UNAVAILABLE"),
        mock_success_response,
    ]

    test_audio = tmp_path / "chunk_001.wav"
    test_audio.touch()

    chunk_info = ChunkInfo(
        index=1,
        filename="chunk_001.wav",
        filepath=str(test_audio),
        start_sec=0.0,
        end_sec=600.0,
        duration_sec=600.0,
    )

    channel = GeminiFlashChannel(api_key="mock_key", primary_model="gemini-3.8-flash")
    result = channel.transcribe_chunk(test_audio, chunk_info)

    assert result.model_used == "gemini-3.5-flash"
    assert result.raw_text == "備援模型成功產出文字。"
    mock_client.files.delete.assert_called_once_with(name="files/test_file_503")
