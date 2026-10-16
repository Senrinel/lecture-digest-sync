"""Unit tests for Phase 3: GeminiTextCleanupEngine."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from lecture_pipeline.cleanup.base import (
    CleanedChunk,
    CleanupResult,
    save_cleanup_outputs,
)
from lecture_pipeline.cleanup.text_cleanup import GeminiTextCleanupEngine


def test_cleanup_prompt_building_with_course_and_glossary(tmp_path: Path):
    """Verify prompt formatting correctly embeds course name and glossary."""
    prompt_file = tmp_path / "custom_prompt.md"
    prompt_file.write_text("這是自訂提示詞範本。", encoding="utf-8")

    engine = GeminiTextCleanupEngine(
        api_key="mock_key",
        prompt_template_path=prompt_file,
    )

    prompt = engine.build_prompt(
        raw_text_chunk="今天我們來講解離散數學的快速排序演算法。",
        course="Discrete_Mathematics",
        glossary=["Quick Sort", "Divide and Conquer", "Worst Case"],
    )

    assert "這是自訂提示詞範本。" in prompt
    assert "【本堂課程主題】：Discrete_Mathematics" in prompt
    assert "Quick Sort, Divide and Conquer, Worst Case" in prompt
    assert "今天我們來講解離散數學的快速排序演算法。" in prompt


def test_cleanup_sentence_boundary_split():
    """Verify that long texts are chunked cleanly at sentence delimiters."""
    engine = GeminiTextCleanupEngine(
        api_key="mock_key",
        chunk_chars=50,
    )

    # 120 characters with clear sentence periods
    text = (
        "這是第一句非常長的課堂解說內容。"  # 17 chars
        "這是第二句接著說明的資料結構概念。"  # 17 chars
        "這是第三句進一步探討的時間複雜度。"  # 17 chars
        "這是第四句關於遞迴關係式的證明過程。"  # 18 chars
    )

    chunks = engine.split_into_chunks(text)
    assert len(chunks) > 1
    # Reassembling chunks must reconstruct the entire text
    assert "".join(chunks) == text
    # Each chunk should not exceed chunk_chars significantly
    for c in chunks:
        assert len(c) <= 50 or c.endswith("。")


@patch("lecture_pipeline.cleanup.text_cleanup.genai.Client")
def test_cleanup_single_chunk_success(mock_client_cls):
    """Verify successful cleanup of a single chunk."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    mock_resp = MagicMock()
    mock_resp.text = "今天我們來講解離散數學的演算法。它的核心是 Divide and Conquer。"
    mock_client.models.generate_content.return_value = mock_resp

    engine = GeminiTextCleanupEngine(api_key="mock_key", primary_model="gemini-3.5-flash")
    raw = "今天我們來講解離散數學的演算法它的核心是Divide and Conquer"

    result = engine.clean_single_chunk(
        chunk_index=1,
        raw_chunk=raw,
        course="Algorithm",
    )

    assert result.index == 1
    assert result.raw_char_count == len(raw)
    assert result.cleaned_char_count == len(mock_resp.text)
    assert result.validation_passed is True
    assert result.retried is False
    assert result.model_used == "gemini-3.5-flash"


@patch("lecture_pipeline.cleanup.text_cleanup.genai.Client")
def test_cleanup_guardrail_retry_on_summarization(mock_client_cls):
    """Verify that output with length < min_ratio triggers automated retry."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    # First response is over-summarized (length ratio ~ 0.25 < 0.80)
    mock_resp_1 = MagicMock()
    mock_resp_1.text = "這堂課講了演算法。"  # 9 chars

    # Second response (after retry warning) restores full length
    mock_resp_2 = MagicMock()
    mock_resp_2.text = "今天我們上課主要探討的是演算法的時間複雜度，包含 Best Case 與 Worst Case 的各項詳細分析推導過程。"  # 54 chars

    mock_client.models.generate_content.side_effect = [mock_resp_1, mock_resp_2]

    engine = GeminiTextCleanupEngine(
        api_key="mock_key",
        primary_model="gemini-3.5-flash",
        min_ratio=0.80,
    )
    raw = "今天我們上課主要探討的是演算法的時間複雜度包含Best Case與Worst Case的各項詳細分析推導過程"  # 50 chars

    chunk_result = engine.clean_single_chunk(
        chunk_index=1,
        raw_chunk=raw,
    )

    assert chunk_result.retried is True
    assert chunk_result.validation_passed is True
    assert chunk_result.cleaned_text == mock_resp_2.text
    assert chunk_result.retention_ratio >= 0.80


@patch("lecture_pipeline.cleanup.text_cleanup.genai.Client")
def test_cleanup_model_fallback_on_transient_error(mock_client_cls):
    """Verify fallback to secondary model on 503/timeout error."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    mock_success = MagicMock()
    mock_success.text = "成功使用備用模型完成課堂文字整理。"

    # First model hits RemoteProtocolError, second model succeeds
    mock_client.models.generate_content.side_effect = [
        Exception("RemoteProtocolError: Server disconnected without sending a response"),
        mock_success,
    ]

    engine = GeminiTextCleanupEngine(
        api_key="mock_key",
        primary_model="gemini-3.5-flash",
    )

    raw = "成功使用備用模型完成課堂文字整理"
    result = engine.clean_single_chunk(chunk_index=1, raw_chunk=raw)

    assert result.model_used == "gemini-3.5-flash-lite"
    assert result.cleaned_text == mock_success.text


def test_save_cleanup_outputs(tmp_path: Path):
    """Verify persisting 02_cleaned.txt and 02_cleaned.json with audit metadata."""
    res = CleanupResult(
        full_cleaned_text="### 離散數學第一講\n\n今天介紹 Divide and Conquer 架構。",
        raw_char_count=35,
        cleaned_char_count=36,
        retention_ratio=1.0286,
        model_used="gemini-3.5-flash",
        prompt_template="fabric_clean_text.md",
        validation_passed=True,
        total_latency_sec=2.15,
        chunks=[
            CleanedChunk(
                index=1,
                raw_text="今天介紹Divide and Conquer架構",
                cleaned_text="今天介紹 Divide and Conquer 架構。",
                raw_char_count=28,
                cleaned_char_count=30,
                retention_ratio=1.0714,
                model_used="gemini-3.5-flash",
                latency_sec=2.15,
            )
        ],
    )

    files = save_cleanup_outputs(res, tmp_path)
    assert files["cleaned_txt"].is_file()
    assert files["cleaned_json"].is_file()

    saved_txt = files["cleaned_txt"].read_text(encoding="utf-8")
    assert "### 離散數學第一講" in saved_txt

    import json

    with open(files["cleaned_json"], "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["model_used"] == "gemini-3.5-flash"
    assert data["retention_ratio"] == 1.0286
    assert data["validation_passed"] is True
