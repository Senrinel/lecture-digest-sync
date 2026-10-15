"""Integration test for Phase 4 pipeline execution via CLI."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from lecture_pipeline.cli import app
from lecture_pipeline.state import (
    PreprocessOptions,
    RunState,
    SegmentationOptions,
    load_run_state,
    save_run_state,
)
from typer.testing import CliRunner

runner = CliRunner()


@patch("lecture_pipeline.summary.summarizer.genai.Client")
def test_phase4_full_pipeline_cli(mock_client_cls, tmp_path: Path):
    """Test full Phase 4 summary execution via CLI command 'summary'."""
    work_dir = tmp_path / "work" / "Discrete_Math" / "20261015"
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create simulated 02_cleaned.txt
    cleaned_text = (
        "今天我們來講解離散數學的快速排序演算法，它的平均也是 Divide and Conquer 嘛。\n"
        "經常排序的速度平均來講大概是世界最快的排序法。\n"
        "當然以 Worst Case 來講，它並不是最快。"
    )
    cleaned_file = work_dir / "02_cleaned.txt"
    cleaned_file.write_text(cleaned_text, encoding="utf-8")

    # 2. Create simulated run.json
    state = RunState(
        source_filename="test_lecture.mp3",
        source_path=str(tmp_path / "test_lecture.mp3"),
        course="Discrete_Math",
        date="20261015",
        pre_options=PreprocessOptions(),
        seg_options=SegmentationOptions(),
        stage_status={
            "preprocess": "DONE",
            "vad_segment": "DONE",
            "stt": "DONE",
            "cleanup": "DONE",
        },
        metadata={"cleanup_cleaned_chars": len(cleaned_text)},
    )
    save_run_state(state, work_dir)

    # 3. Mock Gemini API response
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    summary_mock_text = (
        "# 【課程主旨】\n"
        "本堂課深入介紹 Quick Sort 的核心原理與三大複雜度分析。\n\n"
        "# 【核心概念與定義】\n"
        "- **Divide and Conquer**：分治演算法架構。\n"
        "- **Worst Case**：最壞時間複雜度 $O(n^2)$。\n\n"
        "# 【關鍵詞彙對照表】\n"
        "| 中文 | 英文 |\n"
        "| 快速排序 | Quick Sort |"
    )
    mock_resp = MagicMock()
    mock_resp.text = summary_mock_text
    mock_client.models.generate_content.return_value = mock_resp

    # 4. Invoke CLI command: summary
    res = runner.invoke(app, ["summary", str(work_dir)])
    assert res.exit_code == 0, f"CLI command failed: {res.stdout}"

    # 5. Verify outputs
    summary_md = work_dir / "03_summary.md"
    summary_json = work_dir / "03_summary.json"
    assert summary_md.is_file()
    assert summary_json.is_file()

    saved_md = summary_md.read_text(encoding="utf-8")
    assert "# 【課程主旨】" in saved_md
    assert "Divide and Conquer" in saved_md

    # 6. Verify run.json state updated
    updated_state = load_run_state(work_dir)
    assert updated_state is not None
    assert updated_state.stage_status["summarize"] == "DONE"
    assert updated_state.metadata["summary_chars"] == len(summary_mock_text)
