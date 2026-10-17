"""Unit tests for Phase 5: NotebookLM export bundle generator."""

from pathlib import Path
import pytest

from lecture_pipeline.export.notebook_bundle import prepare_notebook_bundle
from lecture_pipeline.state import (
    PreprocessOptions,
    RunState,
    SegmentationOptions,
    save_run_state,
)


def test_prepare_notebook_bundle_files_and_headers(tmp_path: Path):
    """Verify generated bundle contains proper headers and metadata."""
    work_dir = tmp_path / "work" / "Algorithms" / "20261015"
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Setup mock run.json
    state = RunState(
        source_filename="test.mp3",
        source_path=str(tmp_path / "test.mp3"),
        course="Algorithms",
        date="20261015",
        pre_options=PreprocessOptions(),
        seg_options=SegmentationOptions(),
        stage_status={
            "preprocess": "DONE",
            "vad_segment": "DONE",
            "stt": "DONE",
            "cleanup": "DONE",
            "summarize": "DONE",
        },
        metadata={"stt_total_chars": 2500},
    )
    save_run_state(state, work_dir)

    # 2. Setup mock files
    (work_dir / "03_summary.md").write_text("# 【課程主旨】\nQuick Sort 演算法分析。", encoding="utf-8")
    (work_dir / "02_cleaned.txt").write_text("這是精修後的課堂文稿全文。", encoding="utf-8")
    (work_dir / "01_raw_timestamped.txt").write_text("[00:00 - 05:00] 這是原始文字。", encoding="utf-8")

    export_mirror = tmp_path / "my_drive" / "notes"

    # 3. Generate bundle
    result = prepare_notebook_bundle(work_dir, export_dir=export_mirror)

    assert result.course == "Algorithms"
    assert result.date == "20261015"

    bundle_dir = Path(result.bundle_dir)
    assert bundle_dir.is_dir()

    summary_file = Path(result.summary_file)
    cleaned_file = Path(result.cleaned_file)
    ts_file = Path(result.raw_timestamped_file)

    assert summary_file.is_file()
    assert cleaned_file.is_file()
    assert ts_file.is_file()

    # Verify content and header formatting
    summary_text = summary_file.read_text(encoding="utf-8")
    assert "> **課程名稱 (Course)**：Algorithms" in summary_text
    assert "> **課堂日期 (Date)**：2026-10-15" in summary_text
    assert "Quick Sort 演算法分析" in summary_text

    cleaned_text = cleaned_file.read_text(encoding="utf-8")
    assert "這是精修後的課堂文稿全文" in cleaned_text

    # Verify mirrored copy in export_dir
    assert result.export_dir is not None
    mirrored_dir = Path(result.export_dir)
    assert (mirrored_dir / summary_file.name).is_file()
    assert (mirrored_dir / cleaned_file.name).is_file()
    # Specification check: Neither bundle_dir nor export_dir may contain HTML
    assert not any(bundle_dir.glob("*.html"))
    assert not any(mirrored_dir.glob("*.html"))
