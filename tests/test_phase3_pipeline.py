"""Integration test for Phase 3 pipeline execution."""

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


@patch("lecture_pipeline.cleanup.text_cleanup.genai.Client")
def test_phase3_full_pipeline_cli(mock_client_cls, tmp_path: Path):
    """Test full Phase 3 execution via CLI command 'clean'."""
    work_dir = tmp_path / "work" / "Discrete_Math" / "20261015"
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create simulated 01_raw.txt
    raw_text = (
        "今天我們來講解離散數學的快速排序演算法它的平均也是Divide and Conquer嘛。"
        "然後呢經常排序的速度平均來講是大概是世界最快的排序法平均啦。"
        "當然以Worst Case來講它並不是最快。"
    )
    raw_file = work_dir / "01_raw.txt"
    raw_file.write_text(raw_text, encoding="utf-8")

    # 2. Create simulated run.json
    state = RunState(
        source_filename="test_lecture.mp3",
        source_path=str(tmp_path / "test_lecture.mp3"),
        course="Discrete_Math",
        date="20261015",
        pre_options=PreprocessOptions(),
        seg_options=SegmentationOptions(),
        stage_status={"preprocess": "DONE", "vad_segment": "DONE", "stt": "DONE"},
        metadata={"stt_total_chars": len(raw_text)},
    )
    save_run_state(state, work_dir)

    # 3. Mock Gemini API response
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    cleaned_mock_text = (
        "今天我們來講解離散數學的快速排序演算法，它的平均也是 Divide and Conquer 嘛。\n"
        "然後呢，經常排序的速度平均來講大概是世界最快的排序法，平均啦。\n"
        "當然以 Worst Case 來講，它並不是最快。"
    )
    mock_resp = MagicMock()
    mock_resp.text = cleaned_mock_text
    mock_client.models.generate_content.return_value = mock_resp

    # 4. Invoke CLI command: clean
    res = runner.invoke(app, ["clean", str(work_dir)])
    assert res.exit_code == 0, f"CLI command failed: {res.stdout}"

    # 5. Verify outputs
    cleaned_txt = work_dir / "02_cleaned.txt"
    cleaned_json = work_dir / "02_cleaned.json"
    assert cleaned_txt.is_file()
    assert cleaned_json.is_file()

    saved_content = cleaned_txt.read_text(encoding="utf-8")
    assert "Divide and Conquer" in saved_content
    assert "Worst Case" in saved_content

    # 6. Verify run.json state updated
    updated_state = load_run_state(work_dir)
    assert updated_state is not None
    assert updated_state.stage_status["cleanup"] == "DONE"
    assert updated_state.metadata["cleanup_raw_chars"] == len(raw_text)
    assert updated_state.metadata["cleanup_cleaned_chars"] == len(cleaned_mock_text)
    assert updated_state.metadata["cleanup_validation_passed"] is True
