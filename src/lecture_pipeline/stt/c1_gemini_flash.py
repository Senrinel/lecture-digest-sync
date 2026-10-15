"""Channel C1: Gemini Flash Audio Understanding Verbatim Transcription.

Listens directly to the audio chunk using Gemini multimodal audio capabilities.
Includes automatic fallback across Flash generations (3.8 -> 3.7 -> 3.6 -> 3.5)
to safeguard against temporary 503 high-demand spikes.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv
from google import genai
from rich.console import Console

from lecture_pipeline.state import ChunkInfo
from lecture_pipeline.stt.base import BaseSTTChannel, ChunkTranscription

console = Console()

FLASH_MODEL_CANDIDATES = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]


class GeminiFlashChannel(BaseSTTChannel):
    """Channel C1 implementation using Gemini Flash audio understanding."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        primary_model: str = "gemini-3.8-flash",
        temperature: float = 0.0,
    ):
        load_dotenv()
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
        if not self.api_key:
            raise ValueError("未設定 GEMINI_API_KEY，請於 .env 填入有效的 Gemini API 金鑰。")

        self.client = genai.Client(api_key=self.api_key)
        self.primary_model = primary_model
        self.temperature = temperature

    @property
    def channel_id(self) -> str:
        return "C1"

    @property
    def name(self) -> str:
        return "Gemini Flash Audio Verbatim (C1)"

    def _build_prompt(self, course: str = "", glossary: Optional[List[str]] = None) -> str:
        """Construct prompt enforcing verbatim Taiwan Traditional Chinese transcription."""
        prompt_lines = [
            "你是一位頂尖的大學課堂速記員與語音聽寫專家。",
            "請完整、精確地將此音訊片段逐字轉錄為文字：",
            "1. 語言以台灣華語為主，請一律使用標準台灣繁體中文輸出（例如：程式碼、記憶體、演算法、伺服器、變數）。",
            "2. 英文專有名詞、科技縮寫、函數與術語請務必保留原始英文大小寫，嚴禁進行中文音譯。",
            "3. 極致逐字還原：絕對不可摘要、不可刪減、不可漏掉講師講的任何一句話，保留所有說明的完整性。",
            "4. 僅輸出逐字稿內容，不要包含任何開場白、問候語或中繼說明。",
        ]

        if course:
            prompt_lines.append(f"\n【本堂課程主題】：{course}")

        if glossary:
            terms_str = "、".join(glossary[:200])
            prompt_lines.append(f"【課程專有名詞提示】：{terms_str}")

        return "\n".join(prompt_lines)

    def transcribe_chunk(
        self,
        audio_chunk_path: Path,
        chunk_info: ChunkInfo,
        course: str = "",
        glossary: Optional[List[str]] = None,
    ) -> ChunkTranscription:
        """Upload chunk, transcribe with Gemini Flash, handle fallback and cleanup."""
        path = Path(audio_chunk_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"找不到音訊切片檔案：{path}")

        prompt = self._build_prompt(course=course, glossary=glossary)
        start_time = time.time()

        # 1. Upload audio to Gemini Files API
        uploaded_file = self.client.files.upload(file=str(path))
        uploaded_file_name = uploaded_file.name

        transcribed_text = ""
        model_used = self.primary_model
        last_error = None

        # Build candidate hierarchy starting from preferred primary model
        models_to_try = [self.primary_model] + [
            m for m in FLASH_MODEL_CANDIDATES if m != self.primary_model
        ]

        try:
            for model_id in models_to_try:
                try:
                    response = self.client.models.generate_content(
                        model=model_id,
                        contents=[uploaded_file, prompt],
                    )
                    transcribed_text = (response.text or "").strip()
                    model_used = model_id
                    break
                except Exception as e:
                    last_error = e
                    err_msg = str(e)
                    # If 503 (high demand) or 429, try next model candidate
                    if "503" in err_msg or "UNAVAILABLE" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                        console.print(
                            f"[yellow]⚠️ {model_id} 遇尖峰負載 ({err_msg[:40]}...)，正在自動備援至下一代模型...[/yellow]"
                        )
                        time.sleep(1.0)
                        continue
                    else:
                        raise e

            if not transcribed_text and last_error is not None:
                raise RuntimeError(f"C1 所有 Flash 候選模型皆呼叫失敗: {last_error}")

        finally:
            # 2. Always clean up uploaded file from Gemini storage
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
            model_used=model_used,
            latency_sec=latency,
        )
