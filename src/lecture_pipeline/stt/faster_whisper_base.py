"""Base implementation for local faster-whisper family STT channels.

Runs isolated faster-whisper inference via subprocess in backends/fasterwhisper/.venv,
protecting the main pipeline environment from torch/CUDA dependency bloat.
Applies OpenCC 's2twp' conversion to guarantee authentic Taiwanese Traditional Chinese.
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


def find_backend_python(backend_name: str = "fasterwhisper") -> Path:
    """Locate the Python executable of an isolated backend virtual environment."""
    root = Path(__file__).resolve().parents[3]
    backend_dir = root / "backends" / backend_name / ".venv"

    if sys.platform == "win32":
        py_exe = backend_dir / "Scripts" / "python.exe"
    else:
        py_exe = backend_dir / "bin" / "python"

    if not py_exe.is_file():
        raise RuntimeError(
            f"找不到後端虛擬環境 Python：'{py_exe}'。\n"
            f"請先依照說明在 backends/{backend_name} 目錄建立 .venv 並安裝必要套件。"
        )
    return py_exe


class FasterWhisperChannelBase(BaseSTTChannel):
    """Generic base class for models powered by faster-whisper / CTranslate2."""

    def __init__(
        self,
        model_name: str,
        channel_id: str,
        display_name: str,
        device: str = "cuda",
        compute_type: str = "int8_float16",
        beam_size: int = 5,
        language: str = "zh",
        opencc_config: str = "s2twp",
        backend_python: Optional[Path | str] = None,
    ):
        self._model_name = model_name
        self._channel_id = channel_id
        self._display_name = display_name
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.language = language
        self.opencc_config = opencc_config
        self.converter = OpenCC(opencc_config)

        if backend_python:
            self.backend_python = Path(backend_python)
        else:
            self.backend_python = find_backend_python("fasterwhisper")

        root = Path(__file__).resolve().parents[3]
        self.runner_script = root / "backends" / "fasterwhisper" / "run_whisper.py"
        if not self.runner_script.is_file():
            raise FileNotFoundError(f"找不到 faster-whisper 執行腳本：{self.runner_script}")

    @property
    def channel_id(self) -> str:
        return self._channel_id

    @property
    def name(self) -> str:
        return self._display_name

    def build_initial_prompt(
        self,
        course: str = "",
        glossary: Optional[List[str]] = None,
    ) -> str:
        """Construct Taiwan-tuned prompt and vocabulary biasing for Whisper."""
        prompt_parts = ["這是一段台灣大學課堂教學錄音，請使用繁體中文記錄。"]
        if course and course.lower() != "unsorted":
            prompt_parts.append(f"課程：{course}。")
        if glossary:
            terms = ", ".join(glossary[:50])
            prompt_parts.append(f"專業術語包含：{terms}。")
        return " ".join(prompt_parts)

    def transcribe_chunk(
        self,
        audio_chunk_path: Path,
        chunk_info: ChunkInfo,
        course: str = "",
        glossary: Optional[List[str]] = None,
    ) -> ChunkTranscription:
        """Transcribe chunk via subprocess call to isolated faster-whisper environment."""
        path = Path(audio_chunk_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"找不到音訊切片檔案：{path}")

        initial_prompt = self.build_initial_prompt(course=course, glossary=glossary)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp_f:
            temp_json_path = Path(tmp_f.name)

        start_time = time.time()
        cmd = [
            str(self.backend_python),
            "-X",
            "utf8",
            str(self.runner_script),
            "--audio",
            str(path),
            "--model",
            self._model_name,
            "--device",
            self.device,
            "--compute-type",
            self.compute_type,
            "--language",
            self.language,
            "--initial-prompt",
            initial_prompt,
            "--beam-size",
            str(self.beam_size),
            "--output-json",
            str(temp_json_path),
        ]

        # Enforce UTF-8 in child Python environment on Windows (bypasses default CP950/ANSI limit)
        sub_env = os.environ.copy()
        sub_env["PYTHONUTF8"] = "1"
        sub_env["PYTHONIOENCODING"] = "utf-8"

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=sub_env,
                check=False,
            )
            if result.returncode != 0:
                err_msg = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(
                    f"本地 {self.name} 辨識失敗 (代碼 {result.returncode}):\n{err_msg}"
                )

            # Load structured output
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

        # Safeguard: Remove occasional Whisper hallucination/echo of the initial_prompt
        known_prefixes = [
            "這是一段台灣大學課堂教學錄音，請使用繁體中文記錄。",
            "請使用繁體中文記錄：Unsorted。",
            "請使用繁體中文記錄：",
            "請使用繁體中文記錄",
        ]
        for pfx in known_prefixes:
            if converted_text.startswith(pfx):
                converted_text = converted_text[len(pfx):].lstrip("。，、： ")

        # Convert timestamps text as well
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
            model_used=self._model_name,
            latency_sec=output_data.get("duration_sec", wall_duration),
            metadata={
                "channel": self.channel_id,
                "device": self.device,
                "compute_type": self.compute_type,
                "language": output_data.get("language", self.language),
                "language_probability": output_data.get("language_probability", 1.0),
                "timestamps": converted_timestamps,
            },
        )
