"""Base class and common data structures for STT channels."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class STTChunkResult:
    chunk_index: int
    text: str                     # Cleaned / converted Traditional Chinese text
    raw_text: str                 # Raw model output text
    channel_name: str             # e.g., "C1", "C2", "C4"
    model_name: str               # e.g., "gemini-3.8-flash", "gemini-3.5-transcribe"
    latency_sec: float
    start_sec: float
    end_sec: float
    timestamps: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class STTChannelBase(ABC):
    """Abstract base class for all STT recognition channels."""

    @property
    @abstractmethod
    def channel_id(self) -> str:
        """Returns channel identifier, e.g. 'C1', 'C2'."""
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable display name of the channel."""
        pass

    @abstractmethod
    def transcribe_chunk(
        self,
        audio_path: str | Path,
        chunk_index: int,
        start_sec: float,
        end_sec: float,
        course_name: str = "",
        glossary: Optional[List[str]] = None,
        prompt_style: str = "verbatim",
        temperature: float = 0.0,
    ) -> STTChunkResult:
        """Transcribes a single audio chunk and returns STTChunkResult."""
        pass
