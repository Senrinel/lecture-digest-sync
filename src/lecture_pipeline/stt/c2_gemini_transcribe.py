"""Channel C2: Gemini 3.5 Transcribe ASR model."""

import os
import time
from pathlib import Path
from typing import List, Optional

from google import genai
from google.genai import errors
from opencc import OpenCC
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from lecture_pipeline.stt.base import STTChannelBase, STTChunkResult


class GeminiTranscribeSTTChannel(STTChannelBase):
    """STT Channel using specialized Gemini 3.5 Transcribe model."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-3.5-transcribe",
    ):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not set.")
        self.client = genai.Client(api_key=self.api_key)
        self.model_name = model_name
        self.opencc = OpenCC("s2twp")

    @property
    def channel_id(self) -> str:
        return "C2"

    @property
    def display_name(self) -> str:
        return f"Gemini Transcribe ({self.model_name})"

    @retry(
        reraise=True,
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=3, max=45),
        retry=retry_if_exception_type((errors.ServerError, errors.APIError, Exception)),
    )
    def _call_transcribe_api(self, uploaded_file, glossary: Optional[List[str]]) -> str:
        """Calls gemini-3.5-transcribe using interactions API with generate_content fallback."""
        config_kwargs = {}
        if glossary:
            config_kwargs["custom_vocabulary"] = glossary[:100]

        # Method A: client.interactions.create
        if hasattr(self.client, "interactions"):
            try:
                gen_config = {}
                if config_kwargs:
                    gen_config["transcription_config"] = config_kwargs

                res = self.client.interactions.create(
                    model=self.model_name,
                    input=[
                        {
                            "type": "audio",
                            "uri": uploaded_file.uri,
                            "mime_type": uploaded_file.mime_type or "audio/wav",
                        }
                    ],
                    generation_config=gen_config or None,
                )
                output_text = getattr(res, "output_text", None)
                if output_text is not None:
                    return output_text
            except errors.ClientError:
                # If interactions API reports client parameter mismatch, try generate_content
                pass

        # Method B: client.models.generate_content
        res2 = self.client.models.generate_content(
            model=self.model_name,
            contents=[uploaded_file],
        )
        return res2.text or ""

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

        start_time = time.time()
        uploaded_file = self.client.files.upload(file=str(p))

        try:
            raw_text = self._call_transcribe_api(uploaded_file, glossary)
            latency = round(time.time() - start_time, 2)

            # gemini-3.5-transcribe outputs Simplified Chinese by default; convert to Taiwan Traditional
            tw_text = self.opencc.convert(raw_text.strip())

            return STTChunkResult(
                chunk_index=chunk_index,
                text=tw_text,
                raw_text=raw_text,
                channel_name=self.channel_id,
                model_name=self.model_name,
                latency_sec=latency,
                start_sec=start_sec,
                end_sec=end_sec,
            )
        finally:
            try:
                self.client.files.delete(name=uploaded_file.name)
            except Exception:
                pass
