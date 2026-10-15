"""Channel C5: FireRedASR (Local GPU / Attention-based Encoder-Decoder).

Xiaohongshu's industrial-grade Mandarin ASR model (FireRedASR-AED-L).
Runs in an isolated PyTorch environment with automatic sub-chunking for stability.
Post-processed with OpenCC 's2twp' for authentic Taiwanese Traditional Chinese.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from opencc import OpenCC
from rich.console import Console

from lecture_pipeline.state import ChunkInfo
from lecture_pipeline.stt.base import BaseSTTChannel, ChunkTranscription

console = Console()


def find_firered_backend_python() -> Path:
    """Locate the Python executable for FireRedASR execution."""
    root = Path(__file__).resolve().parents[3]
    # Check dedicated fireredasr venv or shared torch venv (backends/qwen/.venv)
    cand1 = root / "backends" / "fireredasr" / ".venv"
    cand2 = root / "backends" / "qwen" / ".venv"

    for cand in [cand1, cand2]:
        if sys.platform == "win32":
            py_exe = cand / "Scripts" / "python.exe"
        else:
            py_exe = cand / "bin" / "python"
        if py_exe.is_file():
            return py_exe

    raise RuntimeError(
        "找不到可用的 PyTorch 後端虛擬環境 (backends/fireredasr 或 backends/qwen)。\n"
        "請先依照說明建立環境。"
    )


class FireRedASRChannel(BaseSTTChannel):
    """Channel C5 implementation using FireRedASR-AED."""

    def __init__(
        self,
        model_dir: str = "FireRedTeam/FireRedASR-AED-L",
        asr_type: str = "aed",
        use_gpu: int = 1,
        beam_size: int = 1,
        opencc_config: str = "s2twp",
        backend_python: Optional[Path | str] = None,
    ):
        self._model_dir = model_dir
        self.asr_type = asr_type
        self.use_gpu = use_gpu
        self.beam_size = beam_size
        self.opencc_config = opencc_config
        self.converter = OpenCC(opencc_config)

        if backend_python:
            self.backend_python = Path(backend_python)
        else:
            self.backend_python = find_firered_backend_python()

        root = Path(__file__).resolve().parents[3]
        self.runner_script = root / "backends" / "fireredasr" / "run_firered.py"
        if not self.runner_script.is_file():
            raise FileNotFoundError(f"找不到 FireRedASR 執行腳本：{self.runner_script}")

    @property
    def channel_id(self) -> str:
        return "C5"

    @property
    def name(self) -> str:
        return "FireRedASR (C5)"

    def transcribe_chunk(
        self,
        audio_chunk_path: Path,
        chunk_info: ChunkInfo,
        course: str = "",
        glossary: Optional[List[str]] = None,
    ) -> ChunkTranscription:
        """Transcribe chunk via subprocess call to FireRedASR."""
        path = Path(audio_chunk_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"找不到音訊切片檔案：{path}")

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_f:
            temp_json_path = Path(tmp_f.name)

        start_time = time.time()
        cmd = [
            str(self.backend_python),
            str(self.runner_script),
            "--audio",
            str(path),
            "--model-dir",
            self._model_dir,
            "--asr-type",
            self.asr_type,
            "--use-gpu",
            str(self.use_gpu),
            "--beam-size",
            str(self.beam_size),
            "--output-json",
            str(temp_json_path),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if result.returncode != 0:
                err_msg = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(
                    f"本地 {self.name} 辨識失敗 (代碼 {result.returncode}):\n{err_msg}"
                )

            if not temp_json_path.is_file() or temp_json_path.stat().st_size == 0:
                raise RuntimeError(f"{self.name} 未產生有效的 JSON 輸出檔案。")

            with open(temp_json_path, "r", encoding="utf-8") as f:
                output_data: Dict[str, Any] = json.load(f)

        finally:
            if temp_json_path.is_file():
                try:
                    temp_json_path.unlink()
                except OSError:
                    pass

        wall_duration = round(time.time() - start_time, 2)
        raw_text = output_data.get("text", "").strip()

        # Convert to authentic Taiwanese Traditional Chinese
        converted_text = self.converter.convert(raw_text)

        # Convert timestamps
        timestamps = output_data.get("timestamps", [])
        converted_timestamps = []
        for ts in timestamps:
            converted_timestamps.append({
                "start": ts.get("start", 0.0),
                "end": ts.get("end", 0.0),
                "text": self.converter.convert(ts.get("text", "").strip()),
            })

        return ChunkTranscription(
            index=chunk_info.index,
            start_sec=chunk_info.start_sec,
            end_sec=chunk_info.end_sec,
            duration_sec=chunk_info.duration_sec,
            raw_text=converted_text,
            model_used=self._model_dir,
            latency_sec=output_data.get("duration_sec", wall_duration),
            metadata={
                "channel": self.channel_id,
                "asr_type": self.asr_type,
                "use_gpu": bool(self.use_gpu),
                "timestamps": converted_timestamps,
            },
        )
