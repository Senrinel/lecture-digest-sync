"""Unit tests for Channel C2 (Gemini 3.5 Transcribe) with OpenCC conversion."""

from pathlib import Path
from unittest.mock import MagicMock, patch
from lecture_pipeline.state import ChunkInfo
from lecture_pipeline.stt.c2_gemini_transcribe import GeminiTranscribeChannel


@patch("lecture_pipeline.stt.c2_gemini_transcribe.genai.Client")
def test_c2_transcribe_chunk_with_opencc_conversion(mock_client_cls, tmp_path: Path):
    """Verify simplified output from gemini-3.5-transcribe is converted to Taiwanese phrasing."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    mock_file = MagicMock()
    mock_file.name = "files/test_file_c2"
    mock_file.uri = "https://generativelanguage.googleapis.com/v1beta/files/test_file_c2"
    mock_file.mime_type = "audio/wav"
    mock_client.files.upload.return_value = mock_file

    # Simulate raw simplified Chinese from ASR
    mock_interaction = MagicMock()
    mock_interaction.output_text = "今天的课程讨论服务器内存管理与网络代码优化。"
    mock_client.interactions.create.return_value = mock_interaction

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

    channel = GeminiTranscribeChannel(api_key="mock_key", opencc_config="s2twp")
    result = channel.transcribe_chunk(test_audio, chunk_info, glossary=["Kubernetes"])

    assert result.index == 1
    assert result.model_used == "gemini-3.5-transcribe"

    # Verify OpenCC s2twp transformed mainland terms to Taiwanese terms:
    # 服务器 -> 伺服器
    # 内存 -> 記憶體
    # 网络 -> 網路
    # 代码 -> 程式碼
    assert "伺服器" in result.raw_text
    assert "記憶體" in result.raw_text
    assert "網路" in result.raw_text
    assert "程式碼" in result.raw_text

    # Verify file deletion cleanup
    mock_client.files.delete.assert_called_once_with(name="files/test_file_c2")
