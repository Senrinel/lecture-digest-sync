"""NotebookLM & Google Drive export bundle generator for Phase 5.

Packages structured summaries, cleaned lecture transcripts, and timestamped raw text
into clean, citation-friendly Markdown files ready for 1-click import into Google NotebookLM.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from lecture_pipeline.state import RunState, load_run_state, save_run_state


class NotebookBundleResult(BaseModel):
    """Output paths and metadata for staged NotebookLM files."""

    course: str
    date: str
    bundle_dir: str
    export_dir: Optional[str] = None
    summary_file: str
    cleaned_file: str
    raw_timestamped_file: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict[str, Any] = Field(default_factory=dict)


def prepare_notebook_bundle(
    work_dir: Path | str,
    export_dir: Optional[Path | str] = None,
) -> NotebookBundleResult:
    """Create formatted, citation-grounded Markdown bundle for Google NotebookLM.

    Staging outputs in `work_dir/notebook_bundle/`:
      - 01_{Course}_{Date}_重點總結筆記.md
      - 02_{Course}_{Date}_完整課堂整理文稿.md
      - 03_{Course}_{Date}_原始時間戳對照.md
    Optionally mirrors to `export_dir` (e.g. Google Drive sync directory).
    """
    target_work_dir = Path(work_dir).resolve()
    state = load_run_state(target_work_dir)

    course = state.course if state else "Lecture"
    date_str = state.date if state else datetime.now().strftime("%Y%m%d")

    # Format human-readable date if YYYYMMDD
    formatted_date = date_str
    if len(date_str) == 8 and date_str.isdigit():
        formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    bundle_dir = target_work_dir / "notebook_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    # 1. Prepare Header Metadata for NotebookLM groundings
    header_meta = [
        f"> **課程名稱 (Course)**：{course}",
        f"> **課堂日期 (Date)**：{formatted_date}",
        f"> **生成時間 (Generated)**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if state and state.metadata.get("stt_total_chars"):
        header_meta.append(f"> **課堂總字數**：約 {state.metadata['stt_total_chars']} 字")
    header_block = "\n".join(header_meta) + "\n\n---\n\n"

    # 2. Package 01: Structured Summary Note
    summary_src = target_work_dir / "03_summary.md"
    summary_out = bundle_dir / f"01_{course}_{date_str}_重點總結筆記.md"
    summary_content = ""
    if summary_src.is_file():
        summary_content = summary_src.read_text(encoding="utf-8")
    else:
        summary_content = "# 【課堂考前重點筆記】\n\n（尚未執行總結階段）"

    with open(summary_out, "w", encoding="utf-8") as f:
        f.write(f"# 【課堂考前重點總結筆記】{course} ({formatted_date})\n\n{header_block}{summary_content}\n")

    # 3. Package 02: Full Cleaned Transcript
    cleaned_src = target_work_dir / "02_cleaned.txt"
    cleaned_out = bundle_dir / f"02_{course}_{date_str}_完整課堂整理文稿.md"
    cleaned_content = ""
    if cleaned_src.is_file():
        cleaned_content = cleaned_src.read_text(encoding="utf-8")
    else:
        # Fallback to 01_raw.txt if cleaned not available
        raw_src = target_work_dir / "01_raw.txt"
        cleaned_content = raw_src.read_text(encoding="utf-8") if raw_src.is_file() else ""

    with open(cleaned_out, "w", encoding="utf-8") as f:
        f.write(f"# 【完整課堂精修文稿】{course} ({formatted_date})\n\n{header_block}{cleaned_content}\n")

    # 4. Package 03: Timestamped Transcript (Optional audio locator)
    raw_ts_src = target_work_dir / "01_raw_timestamped.txt"
    ts_out = bundle_dir / f"03_{course}_{date_str}_原始時間戳對照.md"
    ts_file_path: Optional[str] = None
    if raw_ts_src.is_file():
        ts_content = raw_ts_src.read_text(encoding="utf-8")
        with open(ts_out, "w", encoding="utf-8") as f:
            f.write(f"# 【原始課堂音訊時間戳對照】{course} ({formatted_date})\n\n{header_block}{ts_content}\n")
        ts_file_path = str(ts_out)

    # 5. Mirror to custom export_dir if specified
    export_dir_str: Optional[str] = None
    if export_dir:
        target_exp = Path(export_dir).resolve() / course / date_str
        target_exp.mkdir(parents=True, exist_ok=True)
        shutil.copy2(summary_out, target_exp / summary_out.name)
        shutil.copy2(cleaned_out, target_exp / cleaned_out.name)
        if ts_file_path and Path(ts_file_path).is_file():
            shutil.copy2(ts_out, target_exp / ts_out.name)
        export_dir_str = str(target_exp)

    # 6. Update run.json state if present
    if state:
        state.stage_status["export"] = "DONE"
        state.metadata["export_bundle_dir"] = str(bundle_dir)
        if export_dir_str:
            state.metadata["export_mirrored_dir"] = export_dir_str
        save_run_state(state, target_work_dir)

    return NotebookBundleResult(
        course=course,
        date=date_str,
        bundle_dir=str(bundle_dir),
        export_dir=export_dir_str,
        summary_file=str(summary_out),
        cleaned_file=str(cleaned_out),
        raw_timestamped_file=ts_file_path,
        metadata={
            "summary_chars": len(summary_content),
            "cleaned_chars": len(cleaned_content),
        },
    )


def open_notebooklm_in_browser(url: str = "https://notebooklm.google.com/") -> None:
    """Open Google NotebookLM web application in user's default browser."""
    try:
        webbrowser.open(url)
    except Exception as e:
        pass


def reveal_in_explorer(file_or_dir: Path | str) -> None:
    """Reveal directory or select file in Windows Explorer."""
    path = Path(file_or_dir).resolve()
    if sys.platform == "win32":
        try:
            if path.is_file():
                subprocess.run(["explorer", f"/select,{str(path)}"], check=False)
            elif path.is_dir():
                subprocess.run(["explorer", str(path)], check=False)
        except Exception:
            pass
