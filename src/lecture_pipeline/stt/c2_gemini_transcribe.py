"""Channel C2: Gemini 3.5 Transcribe Dedicated ASR Model.

Utilizes the specialized gemini-3.5-transcribe model with custom vocabulary hints
and OpenCC 's2twp' conversion to ensure flawless Taiwanese Traditional Chinese output.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types
from opencc import OpenCC
from rich.console import Console

from lecture_pipeline.state import ChunkInfo
from lecture_pipeline.stt.base import BaseSTTChannel, ChunkTranscription

console = Console()


class GeminiTranscribeChannel(BaseSTTChannel):
    """Channel C2 implementation using Gemini 3.5 Transcribe."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        opencc_config: str = "s2twp",
    ):
        load_dotenv()
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
        if not self.api_key:
            raise ValueError("未設定 GEMINI_API_KEY，請於 .env 填入有效的 Gemini API 金鑰。")

        # Set 300s timeout (300,000 ms) for long audio
        http_opts = types.HttpOptions(timeout=300000)
        self.client = genai.Client(api_key=self.api_key, http_options=http_opts)
        self.model_id = "gemini-3.5-transcribe"
        self.opencc_config = opencc_config
        self.converter = OpenCC(opencc_config)


    @property
    def channel_id(self) -> str:
        return "C2"

    @property
    def name(self) -> str:
        return "Gemini 3.5 Transcribe (C2)"

    def transcribe_chunk(
        self,
        audio_chunk_path: Path,
        chunk_info: ChunkInfo,
        course: str = "",
        glossary: Optional[List[str]] = None,
    ) -> ChunkTranscription:
        """Upload chunk, invoke gemini-3.5-transcribe, convert to Traditional Chinese, and cleanup."""
        path = Path(audio_chunk_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"找不到音訊切片檔案：{path}")

        start_time = time.time()

        # 1. Upload audio to Gemini Files API
        uploaded_file = self.client.files.upload(file=str(path))
        uploaded_file_name = uploaded_file.name

        # Prepare transcription configuration
        generation_config = {}
        transcription_config = {
            "language_codes": [],  # Auto-detect language and dynamic code-switching
        }

        # Add custom vocabulary biasing if glossary is provided (up to 1,000 terms)
        if glossary:
            transcription_config["custom_vocabulary"] = glossary[:1000]

        generation_config["transcription_config"] = transcription_config

        transcribed_text = ""
        try:
            interaction = self.client.interactions.create(
                model=self.model_id,
                input=[
                    {
                        "type": "audio",
                        "uri": uploaded_file.uri,
                        "mime_type": uploaded_file.mime_type or "audio/wav",
                    }
                ],
                generation_config=generation_config,
            )
            raw_output = getattr(interaction, "output_text", "") or str(interaction)
            # 2. Convert to Taiwanese Traditional Chinese via OpenCC
            transcribed_text = self.converter.convert(raw_output.strip())

        except Exception as e:
            # Fallback attempt via generateContent if interactions endpoint experiences issues
            try:
                response = self.client.models.generate_content(
                    model=self.model_id,
                    contents=[uploaded_file],
                )
                raw_output = (response.text or "").strip()
                transcribed_text = self.converter.convert(raw_output)
            except Exception:
                raise RuntimeError(f"C2 (gemini-3.5-transcribe) 轉錄失敗: {e}") from e

        finally:
            # 3. Always clean up uploaded file from Gemini storage
            try:
                self.client.files.delete(name=uploaded_file_name)
            except Exception:
                pass

        latency = round(time.time() - start_time, 2)

        return ChunkTranscription(
            index=chunk_info.index,
            start_sec=chunk_info.start_sec,
            end_sec=chunk_info.end_sec,
            duration_sec=chunk_info.duration_sec,
            raw_text=transcribed_text,
            model_used=self.model_id,
            latency_sec=latency,
            metadata={"opencc_config": self.opencc_config},
        )
