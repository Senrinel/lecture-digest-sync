"""Pipeline configuration and run state management module.

Maintains reproducible run records (run.json) and typed configuration models.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
import yaml


class PreprocessOptions(BaseModel):
    """Controllable audio preprocessing toggles."""

    hp: Literal["off", "80Hz", "100Hz", "120Hz"] = "100Hz"
    loud: Literal["off", "-16LUFS", "-19LUFS", "-23LUFS"] = "-19LUFS"
    dyn: Literal["off", "light", "standard"] = "off"
    nr: Literal["off", "6dB", "12dB", "18dB"] = "off"
    dfn: Literal["off", "on"] = "off"
    se: Literal["off", "on"] = "off"
    wpe: Literal["off", "on"] = "off"
    vadtrim: Literal["off", "on"] = "off"

    @classmethod
    def from_cli_flags(cls, flags_str: Optional[str] = None) -> "PreprocessOptions":
        """Parse CLI string like 'hp,loud' or 'hp=120Hz,loud=-16LUFS,nr=12dB'.

        Bare names default to recommended active values:
          hp -> 100Hz
          loud -> -19LUFS
          dyn -> standard
          nr -> 12dB
          dfn -> on
          se -> on
          wpe -> on
          vadtrim -> on
        """
        defaults: Dict[str, Any] = {
            "hp": "off",
            "loud": "off",
            "dyn": "off",
            "nr": "off",
            "dfn": "off",
            "se": "off",
            "wpe": "off",
            "vadtrim": "off",
        }
        if not flags_str:
            return cls(**defaults)

        active_defaults: Dict[str, str] = {
            "hp": "100Hz",
            "loud": "-19LUFS",
            "dyn": "standard",
            "nr": "12dB",
            "dfn": "on",
            "se": "on",
            "wpe": "on",
            "vadtrim": "on",
        }

        tokens = [t.strip() for t in flags_str.split(",") if t.strip()]
        for token in tokens:
            if "=" in token:
                k, v = token.split("=", 1)
                k = k.strip().lower()
                v = v.strip()
                if k in defaults:
                    defaults[k] = v
            else:
                k = token.lower()
                if k in active_defaults:
                    defaults[k] = active_defaults[k]

        return cls(**defaults)


class SegmentationOptions(BaseModel):
    """VAD and chunking configuration."""

    target_chunk_sec: int = Field(default=600, description="Target chunk duration in seconds (10 min)")
    search_window_sec: int = Field(default=30, description="Silence search window radius (±30s)")
    silence_thresh_db: int = Field(default=-40, description="Silence detection threshold in dB")
    min_silence_sec: float = Field(default=0.5, description="Minimum silence duration for a cut point")
    overlap_sec: int = Field(default=0, description="Chunk overlap duration in seconds")


class ChunkInfo(BaseModel):
    """Metadata for an individual audio chunk."""

    index: int
    filename: str
    filepath: str
    start_sec: float
    end_sec: float
    duration_sec: float


class RunState(BaseModel):
    """Execution state tracking every pipeline run in run.json."""

    run_id: str = Field(default_factory=lambda: f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    source_filename: str
    source_path: str
    course: str
    date: str
    pre_options: PreprocessOptions
    seg_options: SegmentationOptions
    chunks: List[ChunkInfo] = Field(default_factory=list)
    stage_status: Dict[str, str] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


def load_config(config_path: Path | str = "config.yaml") -> Dict[str, Any]:
    """Load configuration from YAML file."""
    path = Path(config_path)
    if not path.is_file():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_run_state(state: RunState, work_dir: Path | str) -> Path:
    """Save RunState to run.json in work_dir."""
    target_dir = Path(work_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    out_file = target_dir / "run.json"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(state.model_dump_json(indent=2))
    return out_file


def load_run_state(work_dir: Path | str) -> Optional[RunState]:
    """Load RunState from work_dir/run.json if it exists."""
    run_file = Path(work_dir) / "run.json"
    if not run_file.is_file():
        return None
    with open(run_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return RunState(**data)
