"""Run state and pipeline metadata management.

Ensures every run is fully reproducible, tracked, and can be resumed from intermediate stages.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from pydantic import BaseModel, Field

from lecture_pipeline.naming import AudioMetadata
from lecture_pipeline.preprocess import PreprocessOptions
from lecture_pipeline.vad_segment import ChunkInfo


class RunState(BaseModel):
    """Execution state and metadata for a lecture processing run."""
    run_id: str = Field(default_factory=lambda: f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}")
    status: str = Field(default="INIT", description="INIT | PREPROCESSED | SEGMENTED | TRANSCRIBED | CLEANED | SUMMARIZED | DONE | ERROR")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    audio_meta: AudioMetadata
    preprocess_options: PreprocessOptions
    total_duration_sec: float = 0.0
    preprocessed_wav: str | None = None
    chunks: list[ChunkInfo] = Field(default_factory=list)
    error_message: str | None = None


def save_run_state(work_dir: str | Path, state: RunState) -> Path:
    """Save run state to run.json inside work_dir."""
    p = Path(work_dir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    state.updated_at = datetime.now().isoformat()
    json_path = p / "run.json"
    json_path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    return json_path


def load_run_state(work_dir: str | Path) -> RunState | None:
    """Load run state from run.json inside work_dir, or None if not found."""
    p = Path(work_dir).resolve() / "run.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return RunState.model_validate(data)
    except Exception:
        return None
