"""Base classes and data models for Phase 4: AI Lecture Summary."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SummaryResult(BaseModel):
    """Execution results and audit metadata for Phase 4 structured lecture summary."""

    full_summary_md: str
    input_char_count: int
    summary_char_count: int
    model_used: str
    prompt_template: str
    thinking_budget: int = 1024
    total_latency_sec: float
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    audit_metadata: Dict[str, Any] = Field(default_factory=dict)


class BaseSummarizer(ABC):
    """Abstract base class for lecture summarizers."""

    @property
    @abstractmethod
    def summarizer_id(self) -> str:
        """Summarizer identifier (e.g. 'gemini_lecture_summarizer')."""
        pass

    @abstractmethod
    def summarize(
        self,
        cleaned_text: str,
        course: Optional[str] = None,
        date: Optional[str] = None,
        glossary: Optional[List[str]] = None,
    ) -> SummaryResult:
        """Generate structured lecture summary notes from cleaned lecture text."""
        pass


def save_summary_outputs(
    result: SummaryResult,
    work_dir: Path | str,
) -> Dict[str, Path]:
    """Persist structured summary markdown and audit metadata into work directory.

    Emits:
      - 03_summary.md: Comprehensive markdown lecture review note ready for NotebookLM.
      - 03_summary.json: Structured audit trail tracking generation metadata.
    """
    target_dir = Path(work_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Structured Markdown file
    md_path = target_dir / "03_summary.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(result.full_summary_md)

    # 2. Complete audit JSON
    json_path = target_dir / "03_summary.json"
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(result.model_dump_json(indent=2))

    return {
        "summary_md": md_path,
        "summary_json": json_path,
    }
