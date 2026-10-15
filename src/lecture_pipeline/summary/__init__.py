"""Phase 4 AI Lecture Summary package."""

from lecture_pipeline.summary.base import (
    BaseSummarizer,
    SummaryResult,
    save_summary_outputs,
)
from lecture_pipeline.summary.summarizer import GeminiLectureSummarizer

__all__ = [
    "BaseSummarizer",
    "SummaryResult",
    "save_summary_outputs",
    "GeminiLectureSummarizer",
]
