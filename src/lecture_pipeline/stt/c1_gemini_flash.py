"""Channel C1: Gemini Flash audio transcription."""

import os
import time
from pathlib import Path
from typing import List, Optional

from google import genai
from google.genai import errors
from opencc import OpenCC
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from lecture_pipeline.stt.base import STTChannelBase, STTChunkResult


class GeminiFlashSTTChannel(STTChannelBase):
    """STT Channel using Gemini Flash (3.8 / latest) multimodal audio understanding."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        primary_model: str = "gemini-3.8-flash",
        fallback_model: str = "gemini-flash-latest",
    ):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not set.")
        self.client = genai.Client(api_key=self.api_key)
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.opencc = OpenCC("s2twp")

    @property
    def channel_id(self) -> str:
        return "C1"

    @property
    def display_name(self) -> str:
        return f"Gemini Flash ({self.primary_model})"

    def _build_prompt(
        self,
        course_name: str,
        glossary: Optional[List[str]],
        prompt_style: str,
    ) -> str:
        lines = [
            "你是一位頂尖專業的大學課堂速記員。請仔細聆聽本段錄音，將課堂講話完整轉錄為標準台灣繁體中文。",
            "",
            "【嚴格約束】",
            "1. 極致逐字還原：切勿省略、跳句、改寫或濃縮任何內容，講師講的每一句話都必須完整記錄。",
            "2. 英文專有名詞：遇到英文專業術語、程式語言關鍵字、縮寫或人名，請直接保留標準英文拼寫（如 Python, LLM, Docker），不要音譯為中文。",
            "3. 繁體中文：所有中文內容一律使用台灣繁體中文與台灣慣用詞（如：記憶體、程式碼、伺服器）。",
            "4. 自然標點：請為文句標上適當的標點符號。",
        ]
        if course_name:
            lines.append(f"本堂課程名稱：{course_name}")
        if glossary:
            terms_str = "、".join(glossary[:100])
            lines.append(f"課程專有名詞參考詞庫：{terms_str}")

        lines.extend([
            "",
            "請直接輸出逐字稿內容，切勿添加任何前言（如「好的，以下是...」）或結尾說明。",
        ])
        return "\n".join(lines)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=3, max=30),
        retry=retry_if_exception_type((errors.ServerError, errors.APIError)),
    )
    def _call_gemini_with_fallback(self, audio_file, prompt: str, temperature: float) -> tuple[str, str]:
        """Tries primary model, falls back to fallback_model on 503 unavailable."""
        active_model = self.primary_model
        try:
            response = self.client.models.generate_content(
                model=active_model,
                contents=[audio_file, prompt],
                config={"temperature": temperature},
            )
            return response.text or "", active_model
        except errors.ServerError as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                active_model = self.fallback_model
                response = self.client.models.generate_content(
                    model=active_model,
                    contents=[audio_file, prompt],
                    config={"temperature": temperature},
                )
                return response.text or "", active_model
            raise

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
        # Upload chunk to Gemini Files API
        uploaded_file = self.client.files.upload(file=str(p))

        try:
            prompt = self._build_prompt(course_name, glossary, prompt_style)
            raw_text, used_model = self._call_gemini_with_fallback(
                audio_file=uploaded_file,
                prompt=prompt,
                temperature=temperature,
            )
            latency = round(time.time() - start_time, 2)

            # Ensure strict Traditional Chinese (Taiwan phrasing)
            cleaned_text = self.opencc.convert(raw_text.strip())

            return STTChunkResult(
                chunk_index=chunk_index,
                text=cleaned_text,
                raw_text=raw_text,
                channel_name=self.channel_id,
                model_name=used_model,
                latency_sec=latency,
                start_sec=start_sec,
                end_sec=end_sec,
            )
        finally:
            # Clean up uploaded audio from Google storage
            try:
                self.client.files.delete(name=uploaded_file.name)
            except Exception:
                pass
