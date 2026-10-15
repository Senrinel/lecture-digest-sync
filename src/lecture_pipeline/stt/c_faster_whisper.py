"""Local STT Channels using isolated Faster-Whisper backend.

Covers:
- C4: Breeze-ASR-25 (MediaTek Taiwanese Mandarin fine-tune)
- C6: Whisper large-v3
- C7: Whisper large-v3-turbo
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional
from opencc import OpenCC
from lecture_pipeline.stt.base import STTChannelBase, STTChunkResult


class FasterWhisperLocalChannel(STTChannelBase):
    def __init__(
        self,
        channel_id: str,
        display_name: str,
        model_name_or_path: str,
        compute_type: str = "int8_float16",
        beam_size: int = 5,
    ):
        self._channel_id = channel_id
        self._display_name = display_name
        self.model_name_or_path = model_name_or_path
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.opencc = OpenCC("s2twp")

        # Resolve backend python interpreter
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        self.backend_python = project_root / "backends" / "fasterwhisper" / ".venv" / "Scripts" / "python.exe"
        self.runner_script = project_root / "backends" / "fasterwhisper" / "run_whisper.py"

        if not self.backend_python.exists():
            raise FileNotFoundError(
                f"Faster-whisper backend environment not found at: {self.backend_python}.\n"
                "Please run: cd backends/fasterwhisper && uv venv .venv && uv pip install faster-whisper"
            )

    @property
    def channel_id(self) -> str:
        return self._channel_id

    @property
    def display_name(self) -> str:
        return self._display_name

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
        p = Path(audio_path)
        if not p.exists():
            raise FileNotFoundError(f"Audio file not found: {p}")

        initial_prompt = "這是一段台灣大學課堂的中文教學錄音，請使用繁體中文記錄。"
        if course_name:
            initial_prompt += f" 課程：{course_name}。"
        if glossary:
            initial_prompt += f" 相關專有名詞：{'、'.join(glossary[:30])}。"

        cmd = [
            str(self.backend_python),
            str(self.runner_script),
            "--audio", str(p),
            "--model", self.model_name_or_path,
            "--compute-type", self.compute_type,
            "--initial-prompt", initial_prompt,
            "--beam-size", str(self.beam_size),
        ]

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        if result.returncode != 0:
            raise RuntimeError(f"Local {self.display_name} failed:\n{result.stderr}")

        try:
            data = json.loads(result.stdout.strip())
        except Exception as e:
            raise RuntimeError(f"Failed to parse output from {self.display_name}: {result.stdout}\nError: {e}")

        raw_text = data.get("text", "")
        cleaned_text = self.opencc.convert(raw_text.strip())

        return STTChunkResult(
            chunk_index=chunk_index,
            text=cleaned_text,
            raw_text=raw_text,
            channel_name=self.channel_id,
            model_name=self.model_name_or_path,
            latency_sec=data.get("duration_sec", 0.0),
            start_sec=start_sec,
            end_sec=end_sec,
            timestamps=data.get("timestamps", []),
        )


class ChannelC4Breeze(FasterWhisperLocalChannel):
    def __init__(self, **kwargs):
        super().__init__(
            channel_id="C4",
            display_name="Breeze-ASR-25 (MediaTek TW)",
            model_name_or_path="MediaTek-Research/Breeze-ASR-25",
            **kwargs,
        )


class ChannelC6WhisperLargeV3(FasterWhisperLocalChannel):
    def __init__(self, **kwargs):
        super().__init__(
            channel_id="C6",
            display_name="Whisper large-v3",
            model_name_or_path="large-v3",
            **kwargs,
        )


class ChannelC7WhisperTurbo(FasterWhisperLocalChannel):
    def __init__(self, **kwargs):
        super().__init__(
            channel_id="C7",
            display_name="Whisper large-v3-turbo",
            model_name_or_path="turbo",
            **kwargs,
        )
