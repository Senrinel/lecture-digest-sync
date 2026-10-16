"""Unit tests for Phase 4: GeminiLectureSummarizer."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from lecture_pipeline.summary.base import SummaryResult, save_summary_outputs
from lecture_pipeline.summary.summarizer import GeminiLectureSummarizer


def test_summarizer_prompt_building_with_course_and_glossary(tmp_path: Path):
    """Verify prompt formatting correctly embeds course name, date, and glossary."""
    prompt_file = tmp_path / "custom_summary_prompt.md"
    prompt_file.write_text("這是自訂重點總結範本。", encoding="utf-8")

    summarizer = GeminiLectureSummarizer(
        api_key="mock_key",
        template_path=prompt_file,
    )

    prompt = summarizer.build_prompt(
        cleaned_text="今天介紹 Quick Sort 演算法與時間複雜度分析。",
        course="Algorithm",
        date="20261015",
        glossary=["Quick Sort", "Divide and Conquer", "Worst Case"],
    )

    assert "這是自訂重點總結範本。" in prompt
    assert "【課程主題】：Algorithm" in prompt
    assert "【課堂日期】：20261015" in prompt
    assert "Quick Sort, Divide and Conquer, Worst Case" in prompt
    assert "今天介紹 Quick Sort 演算法與時間複雜度分析。" in prompt


@patch("lecture_pipeline.summary.summarizer.genai.Client")
def test_summarizer_success(mock_client_cls):
    """Verify successful generation of lecture summary."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    mock_resp = MagicMock()
    mock_resp.text = (
        "# 【課程主旨】\n"
        "深入解析 Quick Sort 的三大時間複雜度，特別著重 Average Case 推導。\n\n"
        "# 【核心概念與定義】\n"
        "- **Quick Sort**：基於 Divide and Conquer 的原地排序演算法。"
    )
    mock_client.models.generate_content.return_value = mock_resp

    summarizer = GeminiLectureSummarizer(
        api_key="mock_key",
        primary_model="gemini-3.5-flash",
        thinking_budget=1024,
    )

    cleaned_text = "今天介紹 Quick Sort 演算法與時間複雜度分析。"
    result = summarizer.summarize(
        cleaned_text=cleaned_text,
        course="Algorithm",
        date="20261015",
    )

    assert result.model_used == "gemini-3.5-flash"
    assert result.input_char_count == len(cleaned_text)
    assert result.summary_char_count == len(mock_resp.text)
    assert "# 【課程主旨】" in result.full_summary_md
    assert "Quick Sort" in result.full_summary_md


@patch("time.sleep", return_value=None)
@patch("lecture_pipeline.summary.summarizer.genai.Client")
def test_summarizer_model_fallback_on_error(mock_client_cls, mock_sleep):
    """Verify fallback to secondary model on 503 error."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    mock_success = MagicMock()
    mock_success.text = "# 【課程主旨】\n成功由備援模型產出重點筆記。"

    # First model raises 503 across 3 attempts, then falls back to secondary model which succeeds
    mock_client.models.generate_content.side_effect = [
        Exception("503 UNAVAILABLE: High demand"),
        Exception("503 UNAVAILABLE: High demand"),
        Exception("503 UNAVAILABLE: High demand"),
        mock_success,
    ]

    summarizer = GeminiLectureSummarizer(
        api_key="mock_key",
        primary_model="gemini-3.5-flash",
    )

    result = summarizer.summarize(cleaned_text="課堂測試文字。")
    assert result.model_used == "gemini-3.5-flash-lite"
    assert "備援模型" in result.full_summary_md


def test_save_summary_outputs(tmp_path: Path):
    """Verify saving 03_summary.md and 03_summary.json."""
    res = SummaryResult(
        full_summary_md="# 課堂總結\n\n- 重點一：Divide and Conquer",
        input_char_count=50,
        summary_char_count=35,
        model_used="gemini-3.5-flash",
        prompt_template="fabric_summarize_lecture.md",
        thinking_budget=1024,
        total_latency_sec=3.45,
    )

    files = save_summary_outputs(res, tmp_path)
    assert files["summary_md"].is_file()
    assert files["summary_json"].is_file()

    saved_md = files["summary_md"].read_text(encoding="utf-8")
    assert "# 課堂總結" in saved_md
    assert "Divide and Conquer" in saved_md

    import json
    with open(files["summary_json"], "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["model_used"] == "gemini-3.5-flash"
    assert data["thinking_budget"] == 1024


def test_summarizer_meeting_auto_detection():
    """Verify that when course/topic is a meeting, summarizer auto-selects meeting template."""
    summarizer = GeminiLectureSummarizer(
        api_key="mock_key",
        template_path="prompts/library/fabric_summarize_lecture.md",
    )

    # 1. Academic course uses lecture template
    lec_prompt = summarizer.build_prompt("課堂內文", course="電磁學")
    assert "大學課程助教" in lec_prompt or "課程主旨" in lec_prompt

    # 2. Meeting uses meeting template
    meeting_prompt = summarizer.build_prompt("討論事項全文", course="實驗室組會")
    assert "會議主旨與目標" in meeting_prompt or "待辦事項與行動清單" in meeting_prompt

