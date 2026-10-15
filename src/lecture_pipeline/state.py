"""Pipeline execution state tracking and checkpointing."""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class StageStatus:
    status: str = "pending"  # pending | in_progress | completed | failed | skipped
    start_time: float = 0.0
    end_time: float = 0.0
    duration_sec: float = 0.0
    error_message: str = ""
    outputs: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineRunState:
    run_id: str
    audio_file: str
    course_name: str
    date_str: str
    work_dir: str
    created_at: float = field(default_factory=time.time)
    stages: Dict[str, StageStatus] = field(default_factory=dict)
    config_snapshot: Dict[str, Any] = field(default_factory=dict)

    def get_stage(self, name: str) -> StageStatus:
        if name not in self.stages:
            self.stages[name] = StageStatus()
        return self.stages[name]

    def mark_stage_start(self, name: str):
        st = self.get_stage(name)
        st.status = "in_progress"
        st.start_time = time.time()
        self.save()

    def mark_stage_completed(self, name: str, outputs: Optional[Dict[str, Any]] = None):
        st = self.get_stage(name)
        st.status = "completed"
        st.end_time = time.time()
        st.duration_sec = round(st.end_time - st.start_time, 2)
        if outputs:
            st.outputs.update(outputs)
        self.save()

    def mark_stage_failed(self, name: str, error: str):
        st = self.get_stage(name)
        st.status = "failed"
        st.end_time = time.time()
        st.duration_sec = round(st.end_time - st.start_time, 2)
        st.error_message = error
        self.save()

    def is_stage_completed(self, name: str) -> bool:
        return self.stages.get(name, StageStatus()).status == "completed"

    def save(self):
        state_file = Path(self.work_dir) / "state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_or_create(
        cls,
        work_dir: str | Path,
        audio_file: str | Path,
        course_name: str,
        date_str: str,
        config: Optional[dict] = None,
    ) -> "PipelineRunState":
        wdir = Path(work_dir)
        state_file = wdir / "state.json"
        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                stages = {k: StageStatus(**v) for k, v in data.get("stages", {}).items()}
                return cls(
                    run_id=data.get("run_id", f"{course_name}_{date_str}"),
                    audio_file=data.get("audio_file", str(audio_file)),
                    course_name=data.get("course_name", course_name),
                    date_str=data.get("date_str", date_str),
                    work_dir=str(wdir),
                    created_at=data.get("created_at", time.time()),
                    stages=stages,
                    config_snapshot=data.get("config_snapshot", {}),
                )
            except Exception:
                pass

        run_id = f"{course_name}_{date_str}_{int(time.time())}"
        instance = cls(
            run_id=run_id,
            audio_file=str(audio_file),
            course_name=course_name,
            date_str=date_str,
            work_dir=str(wdir),
            config_snapshot=config or {},
        )
        instance.save()
        return instance
