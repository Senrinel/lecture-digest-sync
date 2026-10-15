"""Integration test for Phase 5 CLI commands."""

from pathlib import Path
import pytest
from typer.testing import CliRunner

from lecture_pipeline.cli import app
from lecture_pipeline.state import (
    PreprocessOptions,
    RunState,
    SegmentationOptions,
    load_run_state,
    save_run_state,
)

runner = CliRunner()


def test_cli_notebook_command(tmp_path: Path):
    """Verify CLI 'notebook' command produces bundle and updates run.json."""
    work_dir = tmp_path / "work" / "Discrete_Math" / "20261015"
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Setup mock files
    (work_dir / "03_summary.md").write_text("# 考前總結\n- Quick Sort", encoding="utf-8")
    (work_dir / "02_cleaned.txt").write_text("這是整理後的全文。", encoding="utf-8")

    state = RunState(
        source_filename="test.mp3",
        source_path=str(tmp_path / "test.mp3"),
        course="Discrete_Math",
        date="20261015",
        pre_options=PreprocessOptions(),
        seg_options=SegmentationOptions(),
        stage_status={"preprocess": "DONE", "vad_segment": "DONE", "stt": "DONE", "cleanup": "DONE", "summarize": "DONE"},
        metadata={},
    )
    save_run_state(state, work_dir)

    # 2. Invoke CLI
    res = runner.invoke(app, ["notebook", str(work_dir), "--no-reveal"])
    assert res.exit_code == 0, f"CLI error: {res.stdout}"

    # 3. Verify bundle outputs
    bundle_dir = work_dir / "notebook_bundle"
    assert bundle_dir.is_dir()
    files = list(bundle_dir.glob("*.md"))
    assert len(files) >= 2

    # 4. Verify run.json state updated
    updated_state = load_run_state(work_dir)
    assert updated_state is not None
    assert updated_state.stage_status["export"] == "DONE"
    assert "notebook_bundle" in updated_state.metadata["export_bundle_dir"]
