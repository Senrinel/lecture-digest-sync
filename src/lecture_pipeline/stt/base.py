"""Base interface and data models for Speech-to-Text channels."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from lecture_pipeline.state import ChunkInfo


def format_timestamp(seconds: float) -> str:
    """Format seconds into [HH:MM:SS] or [MM:SS]."""
    total_sec = int(round(seconds))
    hrs = total_sec // 3600
    mins = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hrs > 0:
        return f"[{hrs:02d}:{mins:02d}:{secs:02d}]"
    return f"[{mins:02d}:{secs:02d}]"


class ChunkTranscription(BaseModel):
    """Transcription result for a single audio chunk."""

    index: int
    start_sec: float
    end_sec: float
    duration_sec: float
    raw_text: str
    model_used: str
    latency_sec: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FullTranscription(BaseModel):
    """Aggregated transcription result for a complete lecture."""

    channel_name: str
    course: str
    date: str
    chunks: List[ChunkTranscription]
    full_text: str
    timestamped_text: str
    total_latency_sec: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BaseSTTChannel(ABC):
    """Abstract base class for all STT channels (Cloud & Local)."""

    @property
    @abstractmethod
    def channel_id(self) -> str:
        """Channel identifier (e.g. 'C1', 'C2', 'C3')."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Display name of the channel."""
        pass

    @abstractmethod
    def transcribe_chunk(
        self,
        audio_chunk_path: Path,
        chunk_info: ChunkInfo,
        course: str = "",
        glossary: Optional[List[str]] = None,
    ) -> ChunkTranscription:
        """Transcribe an individual audio chunk file."""
        pass

    def transcribe_all(
        self,
        chunks: List[ChunkInfo],
        course: str = "",
        date: str = "",
        glossary: Optional[List[str]] = None,
        abort_event: Optional[Any] = None,
    ) -> FullTranscription:
        """Transcribe all chunks sequentially and aggregate full output."""
        transcriptions: List[ChunkTranscription] = []
        total_latency = 0.0

        for idx, chunk in enumerate(chunks):
            if abort_event and abort_event.is_set():
                raise InterruptedError("語音辨識已被使用者強制中止。")

            if idx > 0:
                # Conservative 4.0s pause between chunk API calls to prevent 429 rate limits
                wait_steps = 40
                for _ in range(wait_steps):
                    if abort_event and abort_event.is_set():
                        raise InterruptedError("語音辨識已被使用者強制中止。")
                    time.sleep(0.1)

            chunk_path = Path(chunk.filepath)
            result = self.transcribe_chunk(
                audio_chunk_path=chunk_path,
                chunk_info=chunk,
                course=course,
                glossary=glossary,
            )
            transcriptions.append(result)
            total_latency += result.latency_sec

        # Construct full texts
        full_text_parts: List[str] = []
        timestamped_parts: List[str] = []

        for c in transcriptions:
            text = c.raw_text.strip()
            if text:
                full_text_parts.append(text)
                ts = format_timestamp(c.start_sec)
                timestamped_parts.append(f"{ts} {text}")

        full_text = "\n\n".join(full_text_parts)
        timestamped_text = "\n\n".join(timestamped_parts)

        return FullTranscription(
            channel_name=self.channel_id,
            course=course,
            date=date,
            chunks=transcriptions,
            full_text=full_text,
            timestamped_text=timestamped_text,
            total_latency_sec=round(total_latency, 2),
        )


def save_stt_outputs(
    transcription: FullTranscription,
    work_dir: Path | str,
) -> Dict[str, Path]:
    """Save 01_raw.txt, 01_raw_timestamped.txt, and 01_raw.json in work_dir.

    Returns:
        Dict mapping key to Path ('raw', 'timestamped', 'json').
    """
    target = Path(work_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)

    raw_file = target / "01_raw.txt"
    ts_file = target / "01_raw_timestamped.txt"
    json_file = target / "01_raw.json"

    with open(raw_file, "w", encoding="utf-8") as f:
        f.write(transcription.full_text)

    with open(ts_file, "w", encoding="utf-8") as f:
        f.write(transcription.timestamped_text)

    with open(json_file, "w", encoding="utf-8") as f:
        f.write(transcription.model_dump_json(indent=2))

    return {
        "raw": raw_file,
        "timestamped": ts_file,
        "json": json_file,
    }
