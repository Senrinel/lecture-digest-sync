"""Channel C8: Manual Baseline Channel.

Allows importing an existing human-transcribed or reference transcript text.
Used as ground truth for objective Character Error Rate (CER) benchmarking,
as well as running Phase 3-5 directly on human-verified lecture transcripts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional
from opencc import OpenCC
from rich.console import Console

from lecture_pipeline.state import ChunkInfo
from lecture_pipeline.stt.base import BaseSTTChannel, ChunkTranscription, FullTranscription

console = Console()


class ManualBaselineChannel(BaseSTTChannel):
    """Channel C8 implementation for manual / ground truth transcripts."""

    def __init__(
        self,
        source_text_path: Optional[Path | str] = None,
        opencc_config: str = "s2twp",
    ):
        self.source_text_path = Path(source_text_path) if source_text_path else None
        self.opencc_config = opencc_config
        self.converter = OpenCC(opencc_config)

    @property
    def channel_id(self) -> str:
        return "C8"

    @property
    def name(self) -> str:
        return "Manual Baseline (C8)"

    def transcribe_chunk(
        self,
        audio_chunk_path: Path,
        chunk_info: ChunkInfo,
        course: str = "",
        glossary: Optional[List[str]] = None,
    ) -> ChunkTranscription:
        """Manual channel processes full text or per-chunk text if available."""
        # If a specific text file exists alongside chunk (e.g. chunk_001.txt)
        chunk_txt = audio_chunk_path.with_suffix(".txt")
        if chunk_txt.is_file():
            text = chunk_txt.read_text(encoding="utf-8").strip()
        else:
            text = ""

        converted = self.converter.convert(text)
        return ChunkTranscription(
            index=chunk_info.index,
            start_sec=chunk_info.start_sec,
            end_sec=chunk_info.end_sec,
            duration_sec=chunk_info.duration_sec,
            raw_text=converted,
            model_used="human_manual_baseline",
            latency_sec=0.0,
            metadata={"source": "manual"},
        )

    def transcribe_all(
        self,
        chunks: List[ChunkInfo],
        course: str = "",
        date: str = "",
        glossary: Optional[List[str]] = None,
    ) -> FullTranscription:
        """Load complete manual text if provided, otherwise check per-chunk texts."""
        if self.source_text_path and self.source_text_path.is_file():
            full_raw = self.source_text_path.read_text(encoding="utf-8").strip()
            converted_full = self.converter.convert(full_raw)
            # Create a synthetic single or mapped chunk
            chunk_results: List[ChunkTranscription] = []
            total_duration = sum(c.duration_sec for c in chunks) if chunks else 0.0

            if chunks:
                # Distribute or assign
                chunk_results.append(
                    ChunkTranscription(
                        index=1,
                        start_sec=chunks[0].start_sec,
                        end_sec=chunks[-1].end_sec,
                        duration_sec=total_duration,
                        raw_text=converted_full,
                        model_used="human_manual_baseline",
                        latency_sec=0.0,
                        metadata={"source_file": str(self.source_text_path)},
                    )
                )
            return FullTranscription(
                channel_name=self.channel_id,
                course=course,
                date=date,
                chunks=chunk_results,
                full_text=converted_full,
                timestamped_text=f"[00:00] {converted_full}",
                total_latency_sec=0.0,
                metadata={"source_file": str(self.source_text_path)},
            )

        # Fallback to standard chunk-by-chunk loader
        return super().transcribe_all(
            chunks=chunks,
            course=course,
            date=date,
            glossary=glossary,
        )
