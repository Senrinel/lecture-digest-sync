"""Channel C3: Qwen3-ASR (Local GPU / Qwen Multimodal ASR).

State-of-the-art multilingual ASR model by Alibaba Qwen Team.
Runs in an isolated PyTorch environment in backends/qwen/.venv.
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


def find_qwen_backend_python() -> Path:
    """Locate the Python executable of the isolated Qwen backend virtual environment."""
    root = Path(__file__).resolve().parents[3]
    backend_dir = root / "backends" / "qwen" / ".venv"

    if sys.platform == "win32":
        py_exe = backend_dir / "Scripts" / "python.exe"
    else:
        py_exe = backend_dir / "bin" / "python"

    if not py_exe.is_file():
        raise RuntimeError(
            f"找不到 Qwen 後端虛擬環境 Python：'{py_exe}'。\n"
            f"請先在 backends/qwen 目錄建立 .venv 並安裝 torch 與 transformers。"
        )
    return py_exe


class QwenASRChannel(BaseSTTChannel):
    """Channel C3 implementation using Qwen3-ASR."""

    def __init__(
        self,
        model_name: str = "Qwen/Qwen3-ASR-1.7B-hf",
        device: str = "cuda",
        dtype: str = "float16",
        language: str = "zh",
        opencc_config: str = "s2twp",
        backend_python: Optional[Path | str] = None,
    ):
        self._model_name = model_name
        self.device = device
        self.dtype = dtype
        self.language = language
        self.opencc_config = opencc_config
        self.converter = OpenCC(opencc_config)

        if backend_python:
            self.backend_python = Path(backend_python)
        else:
            self.backend_python = find_qwen_backend_python()

        root = Path(__file__).resolve().parents[3]
        self.runner_script = root / "backends" / "qwen" / "run_qwen.py"
        if not self.runner_script.is_file():
            raise FileNotFoundError(f"找不到 Qwen 執行腳本：{self.runner_script}")

    @property
    def channel_id(self) -> str:
        return "C3"

    @property
    def name(self) -> str:
        return f"Qwen3-ASR (C3)"

    def build_prompt(
        self,
        course: str = "",
        glossary: Optional[List[str]] = None,
    ) -> str:
        """Construct hotword/context biasing prompt for Qwen3-ASR."""
        parts = []
        if course and course.lower() != "unsorted":
            parts.append(f"課程：{course}")
        if glossary:
            terms = ", ".join(glossary[:30])
            parts.append(f"專業術語：{terms}")
        return "；".join(parts)

    def transcribe_chunk(
        self,
        audio_chunk_path: Path,
        chunk_info: ChunkInfo,
        course: str = "",
        glossary: Optional[List[str]] = None,
    ) -> ChunkTranscription:
        """Transcribe chunk via subprocess call to isolated Qwen backend."""
        path = Path(audio_chunk_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"找不到音訊切片檔案：{path}")

        prompt = self.build_prompt(course=course, glossary=glossary)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_f:
            temp_json_path = Path(tmp_f.name)

        start_time = time.time()
        cmd = [
            str(self.backend_python),
            str(self.runner_script),
            "--audio",
            str(path),
            "--model",
            self._model_name,
            "--device",
            self.device,
            "--dtype",
            self.dtype,
            "--language",
            self.language,
            "--prompt",
            prompt,
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

        return ChunkTranscription(
            index=chunk_info.index,
            start_sec=chunk_info.start_sec,
            end_sec=chunk_info.end_sec,
            duration_sec=chunk_info.duration_sec,
            raw_text=converted_text,
            model_used=self._model_name,
            latency_sec=output_data.get("duration_sec", wall_duration),
            metadata={
                "channel": self.channel_id,
                "device": self.device,
                "dtype": self.dtype,
                "language": output_data.get("language", self.language),
                "timestamps": output_data.get("timestamps", []),
            },
        )
