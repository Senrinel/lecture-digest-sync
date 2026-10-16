"""Base classes and data models for Phase 3: AI Text Cleanup."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CleanedChunk(BaseModel):
    """Metadata and content for a single cleaned text segment."""

    index: int
    raw_text: str
    cleaned_text: str
    raw_char_count: int
    cleaned_char_count: int
    retention_ratio: float
    model_used: str
    latency_sec: float
    retried: bool = False
    validation_passed: bool = True


class CleanupResult(BaseModel):
    """Overall result and audit metadata for Phase 3 Text Cleanup."""

    full_cleaned_text: str
    raw_char_count: int
    cleaned_char_count: int
    retention_ratio: float
    model_used: str
    prompt_template: str
    min_ratio: float = 0.80
    max_ratio: float = 1.20
    validation_passed: bool = True
    total_latency_sec: float
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    chunks: List[CleanedChunk] = Field(default_factory=list)
    audit_metadata: Dict[str, Any] = Field(default_factory=dict)


class BaseCleanupEngine(ABC):
    """Abstract base class for lecture transcript cleanup engines."""

    @property
    @abstractmethod
    def engine_id(self) -> str:
        """Engine unique identifier (e.g., 'gemini_text', 'gemini_audio_grounded')."""
        pass

    @abstractmethod
    def clean(
        self,
        raw_text: str,
        course: Optional[str] = None,
        date: Optional[str] = None,
        glossary: Optional[List[str]] = None,
    ) -> CleanupResult:
        """Perform text cleanup, punctuation restoration, and paragraphing."""
        pass


def save_cleanup_outputs(
    result: CleanupResult,
    work_dir: Path | str,
) -> Dict[str, Path]:
    """Persist cleaned text and structured audit metadata into work directory.

    Emits:
      - 02_cleaned.txt: Cleaned lecture text ready for reading and summarization.
      - 02_cleaned.json: Audit trail containing retention ratios, latency, and validations.
    """
    target_dir = Path(work_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Plain text / Markdown output
    txt_path = target_dir / "02_cleaned.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(result.full_cleaned_text)

    # 2. Complete audit JSON
    json_path = target_dir / "02_cleaned.json"
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(result.model_dump_json(indent=2))

    return {
        "cleaned_txt": txt_path,
        "cleaned_json": json_path,
    }
